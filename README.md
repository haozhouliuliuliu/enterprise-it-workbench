# Enterprise IT Onboarding & Compliance Workbench

> 企业 IT 入职与设备合规工作台：自建 RAG、文档版本治理、四个 Agent 协作、受控审批与事务执行。
> 全部业务数据与制度均为虚构；只操作本项目 SQLite，不连接真实企业系统。

[![CI](https://github.com/haozhouliuliuliu/enterprise-it-workbench/actions/workflows/ci.yml/badge.svg)](https://github.com/haozhouliuliuliu/enterprise-it-workbench/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 这个项目做了什么

一条完整的受控链路：

```text
提交请求 → RAG 制度检索 + 业务数据查询（并行）
        → Planner / Business / Policy / Auditor 联合研判
        → 证据不足则 partial/needs_evidence；可靠则生成待审批提案
        → 审批人批准/拒绝（请求人不能自批）
        → 批准后确定性事务执行：预留资产 + 创建工单
        → 全程审计可查
```

四个独立 Agent：

| Agent | 职责 | 工具 | 模型 |
|---|---|---|---|
| Planner | 拆解任务、决定并行与审核 | 无 | deepseek-flash |
| Business | 库存/员工/设备/工单 | 5 个只读业务工具 | qwen3.8-flash |
| Policy | 制度检索、版本/状态/冲突 | `search_knowledge`（RAG） | qwen3.8-flash |
| Auditor | 独立核查证据、冲突、不确定性 | 无 | kimi-k2.7-code |

工具授权在**模型工具列表**和**执行层**双重强制；文档/工单里的“忽略规则”不能改变权限或跳过审批。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 服务 API key（角色 + 部门）与模型密钥只放环境变量
set SERVICE_API_KEYS=[{"key":"exec-key-change-me","user":"alice","role":"executor","department":"研发中心"},{"key":"approver-key-change-me","user":"carol","role":"approver","department":"研发中心"},{"key":"admin-key-change-me","user":"root","role":"admin","department":""}]
set DEEPSEEK_API_KEY=...
set QWEN_API_KEY=...
set KIMI_API_KEY=...

workbench serve            # http://127.0.0.1:8000
```

首次启动会自动初始化业务数据、导入 `seed_data/policies` 与 `sample_docs`（PDF/DOCX/TXT/MD）并建立索引。

## 角色与示例

| 角色 | 能做什么 |
|---|---|
| executor | 提交任务、查看自己的任务 |
| approver | 查看本部门待审批、批准/拒绝（不能批准自己的申请） |
| admin | 全部任务、删除任务、知识库导入/删除/重建索引 |

```bash
# 提交
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "Authorization: Bearer exec-key-change-me" -H "Content-Type: application/json" \
  -d '{"request":"研发中心新员工 EMP-1012 申请预留一台 ThinkPad T14 Gen5 笔记本电脑，请核查资产预留规范和制度。"}'

# 审批队列（approver）
curl http://127.0.0.1:8000/v1/approvals -H "Authorization: Bearer approver-key-change-me"

# 批准并执行
curl -X POST http://127.0.0.1:8000/v1/approvals/<proposal_id>/decision \
  -H "Authorization: Bearer approver-key-change-me" -H "Content-Type: application/json" \
  -d '{"decision":"approve","params_hash":"<hash>"}'
```

工作台：浏览器打开 `http://127.0.0.1:8000/`，输入 API key（只保存在页面内存，刷新后需重输）。

## RAG 与文档生命周期

- 支持 PDF / DOCX / TXT / Markdown 导入；扩展名、大小（2MB）、UTF-8、解析失败都有明确错误；
- 元数据：文档 ID、标题、SHA-256、版本、状态（current/superseded/draft）、生效/失效日期、
  类型、来源、章节/页码、索引状态；
- 同一文件拒绝重复导入；同 doc_id+version 拒绝重复；新 current 版本自动把旧 current 标为 superseded；
- 删除文档级联删除 chunk；重建索引重算向量；SQLite 持久化，重启后可查；
- 混合检索：hashing embedding（256 维）+ BM25 风格关键词 + 章节/标题/现行版本加权；
- 证据 ID `EV-KB-*` 可追溯到文档、版本、章节、页码与片段；
- 无相关证据返回空，允许“现有资料无法确认”；
- 已废止/草稿只作为冲突背景，提案生成要求制度规则为现行。

固定 RAG 评估（30 题：20 可答 + 10 无答案）：

| 指标 | 结果 | 门槛 |
|---|---|---|
| Top-5 召回 | **100%** | ≥85% |
| 引用正确率 | **90%** | ≥90% |
| 无答案拒答 | **10/10** | ≥9/10 |
| 版本/状态 | **100%** | 不把废止/草稿当现行 |

报告：`evidence/rag/`；题目：`eval/rag_cases.json`。

## 多 Agent 与审核

- Planner 输出结构化计划；Business/Policy 并行，结果以结构化 handoff 交给 Auditor；
- Auditor 看到计划、双方结论、证据目录与程序发现的问题；
- 程序校验业务 claim 与制度 rule 的 evidence ID；伪造 ID 会降级审核；
- Planner/Auditor 无工具；Business/Policy 只能调用各自白名单工具；
- Policy 模型故障时不生成制度结论，任务 `partial`，`policy_unavailable=true`。

## 工作流、审批与执行

状态：`planning → running → needs_approval → approved → executing → completed`，
以及 `needs_evidence/partial`、`rejected`、`failed`。

- 只有 `audit=pass` + 员工存在 + 部门标准机型 + 库存可用 + 制度现行，才由确定性代码生成提案；
- 提案绑定 action 参数与 SHA-256 `params_hash`；参数变化原审批失效；
- 请求人不能自批；approver 只能处理本部门；admin 可跨部门；
- 未批准/已拒绝不能执行；重复批准返回原结果，不重复写入；
- 执行使用 SQLite `BEGIN IMMEDIATE`：预留资产 + 创建工单同一事务，失败整体回滚；
- `wb_reservations.asset_id` 活跃唯一索引防止并发重复预留。

## 安全

- Bearer API key 认证；角色授权；任务 owner/department 隔离；非本人统一 404；
- 两层工具权限；无 SQL/命令/文件访问工具；上传只接受 multipart，不使用客户端路径；
- API key/模型密钥只从环境变量读取；日志自动脱敏 `sk-*`、`Bearer *`；客户端只收到稳定错误码；
- 输入长度、单用户限流、全局并发、全局/单任务模型预算、超时与有限重试；
- 重启把 running 标 `INTERRUPTED`；历史、审批、执行记录保留；
- Docker 非 root + Compose + Caddy HTTPS + 数据卷；SQLite 在线备份/恢复。

威胁模型：[docs/threat-model.md](docs/threat-model.md)

## 测试与评估

```bash
pytest -q
pytest --cov=workbench --cov-report=term   # 门槛 80%

python -m eval.rag_eval --output evidence/rag
python -m eval.harness --output evidence/real-model   # 需要三个模型密钥，≤60 次调用
```

- **127 个自动化测试，覆盖率 84%**；
- RAG 固定集 30 题，指标见上；
- 真实模型 8 个端到端场景（含审批执行、重复审批、Policy 故障）：见 [docs/testing.md](docs/testing.md)。

### 真实模型运行（8 场景）

- 运行：`evidence/real-model/run-20261019-125156/`
- 模型：deepseek-flash + qwen3.8-flash + kimi-k2.7-code
- 调用：**48/60**
- 结果：**8/8 符合预设断言**
  - R1 标准申请 → 提案批准 → 工单 +1、资产预留 +1
  - R2 库存不足 / R3 非标准机型 / R5 注入 / R6 员工不存在 → 均无提案
  - R7 Policy 模型 404 → `policy_unavailable=true`，无制度结论
  - R8 同一员工重复申请 → 程序检测到已有 `ASSET-1002`，拒绝生成提案，零写入
- 详细表格与失败轮次说明：[docs/testing.md](docs/testing.md)

## Docker 与 HTTPS

```bash
export SERVICE_API_KEYS='[...]'
export DEEPSEEK_API_KEY=... QWEN_API_KEY=... KIMI_API_KEY=...
docker compose up --build -d
curl -k https://localhost:8443/health/live
```

Agent 以 uid 10001 运行，数据库/知识库在 `workbench-data` 卷；Caddy 使用本地内部 CA
（测试用途，生产替换受信任证书）。部署、备份、恢复见 [docs/deployment.md](docs/deployment.md)。

## 文档

- [架构与数据流](docs/architecture.md)
- [威胁模型](docs/threat-model.md)
- [验收对照](docs/acceptance.md)
- [API](docs/api.md)
- [测试与评估](docs/testing.md)
- [部署/备份](docs/deployment.md)
- [已知限制](docs/known-limitations.md)
- [上游来源](UPSTREAM.md)

## 许可

MIT；上游版权与来源见 [UPSTREAM.md](UPSTREAM.md)。
