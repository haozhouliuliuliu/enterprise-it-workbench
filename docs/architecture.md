# 架构与数据流

## 1. 组件

```text
浏览器工作台 (HTML/JS，API key 仅内存)
        │ HTTPS
        ▼
Caddy ──► FastAPI（Bearer 认证 / 角色 / 任务隔离）
            ├─ TaskService（并发、限流、预算、恢复）
            ├─ Orchestrator
            │    ├─ Planner（deepseek-flash，无工具）
            │    ├─ Business（qwen3.8-flash，5 个只读业务工具）
            │    ├─ Policy（qwen3.8-flash，search_knowledge）
            │    └─ Auditor（kimi-k2.7-code，无工具）
            ├─ KnowledgeStore（文档/分块/向量/关键词）
            ├─ Workflow（提案、审批、事务执行）
            └─ SQLite（业务 + 任务 + 知识库 + 审批/审计）
```

## 2. RAG 文档管线

1. 管理员通过 `POST /v1/knowledge/documents` 上传 PDF/DOCX/TXT/MD（multipart，最大 2MB）；
2. `ingest.extract_sections` 解析：
   - PDF 按页提取（pypdf）；
   - DOCX 按 Heading 分段（python-docx）；
   - Markdown 按 `##` 章节；
   - TXT 作为正文；
3. 校验扩展名、大小、UTF-8、解析错误 → 明确错误码；
4. `KnowledgeStore.add_document`：
   - 计算 SHA-256，拒绝重复文件；
   - 同 `doc_id+version` 拒绝重复；
   - 新 current 版本会把同 `doc_group` 的旧 current 标为 superseded；
   - 分块（约 700 字，80 重叠），生成 `EV-KB-*` 证据 ID；
   - 每块保存 hashing embedding（256 维）与 term counts；
5. 检索：BM25 风格关键词分数 + 向量余弦 + 章节/标题加权 + 当前版本加权；
6. 无证据时返回空（阈值 + 内容词覆盖过滤），允许"现有资料无法确认"；
7. 删除文档级联 chunk；重建索引重算向量；数据在 SQLite 中持久化。

## 3. 状态与业务工作流

任务状态：

```text
planning → running
   ├→ completed（仅分析，无提案）
   ├→ needs_approval → approved → executing → completed
   │                        └→ rejected
   ├→ partial（审核 needs_evidence / 制度不可用）
   ├→ failed（规划/业务失败、执行失败）
   └→ interrupted（重启时 running → failed/INTERRUPTED）
```

提案：

- 只有 `audit=pass`、请求可执行、员工存在、型号符合部门标准、库存有可用资产、制度为现行时，
  才由确定性代码生成提案；
- 提案包含 action 参数与 `params_hash`；
- 提案状态：`pending → approved → executing → executed` 或 `rejected/failed`；
- 未批准不能执行；拒绝不能执行；参数变化导致 hash 不匹配 → 409。

执行事务（SQLite `BEGIN IMMEDIATE`）：

1. `UPDATE wb_proposals SET status='executing' WHERE status='approved'`（行数必须 1）；
2. 选择 `assets.status='in_stock'` 的资产，`UPDATE ... WHERE status='in_stock'`；
3. 插入 `wb_reservations`（`asset_id` 活跃唯一索引）；
4. 生成 `INC-2026-xxxx` 工单并插入；
5. 更新提案 `executed`、任务 `completed`、写入审计事件；
6. 任一步失败整体回滚；重复批准返回原执行结果。

## 4. 证据模型

| 证据 | 生成者 | 存储 |
|---|---|---|
| `EV-KB-<hash>` | 知识库 chunk | `kb_chunks.evidence_id` |
| `EV-<hash>` | 业务/知识工具调用 | `Evidence` + final report |
| 审核 findings | Auditor + 程序规则 | `collab_audits` |

- 业务 claim 必须引用真实 evidence ID；不存在的 ID 会被程序标记 `invalid_evidence`；
- 制度规则可引用 `EV-KB-*`，程序用 `KnowledgeStore.find_evidence` 校验；
- 审核 Agent 看到的证据目录包含工具结果与知识片段。

## 5. 权限矩阵

| 角色 | 工具 | API 能力 |
|---|---|---|
| Planner | 无 | - |
| Business | `query_inventory`、`get_employee`、`get_asset`、`get_ticket`、`list_tickets` | - |
| Policy | `search_knowledge` | - |
| Auditor | 无 | - |
| executor | - | 提交任务、查看自己的任务 |
| approver | - | 查看本部门待审批、批准/拒绝 |
| admin | - | 全部任务、知识库管理、删除 |

两层强制：`schemas_for(agent)` 过滤模型工具列表 + `execute(agent,...)` 执行前校验。

## 6. 数据表

| 表 | 内容 |
|---|---|
| `collab_tasks` | 任务、owner、department、状态、最终结果、预算用量 |
| `collab_subtasks` / `collab_runs` | 子任务、每次模型调用（模型、阶段、token、错误） |
| `collab_handoffs` | Agent 间结构化交接 |
| `collab_audits` / `collab_events` | 审核、全链路事件 |
| `kb_documents` / `kb_chunks` | 文档元数据、版本状态、分块、向量 |
| `wb_proposals` | 提案、参数哈希、审批人/时间、执行结果 |
| `wb_reservations` | 资产预留（活跃唯一） |
| `tickets` / `assets` / `employees` | 虚构业务数据 |

## 7. 并发与恢复

- `BudgetManager`：全局 60 / 单任务 12，锁内预留，重试与失败计数；
- 单用户滑动窗口 30 次/分钟；全局并发 4，超出 503；
- 审批执行：进程内锁 + SQLite `BEGIN IMMEDIATE` + 唯一索引；
- 重启：`planning/running` 标记 `INTERRUPTED`，历史、审批、执行记录保留；
- 备份：SQLite 在线 backup API（`python -m workbench.backup_cli`）。
