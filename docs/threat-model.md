# 威胁模型（M0）

> 范围：单节点、内部演示服务、虚构数据；不覆盖真实企业系统、Kubernetes、多租户 SaaS。
> 硬边界：只读业务工具 + 受控提案执行；未审批不得写入任何业务表。

## 1. 资产

- 模型密钥（DeepSeek/Qwen/Kimi）与服务 API key；
- 任务请求、Agent 结论、证据、审批记录、执行结果；
- 知识库文档（含草稿/废止版本）与向量索引；
- SQLite 数据库（业务 + 审计 + 知识）；
- 容器与 TLS 配置。

## 2. 信任边界

```text
浏览器（不可信输入） → Caddy(HTTPS) → API(认证/授权) → TaskService/Workflow
                                              ├→ Agent/LLM（外部，不可信输出）
                                              ├→ 只读工具（白名单）
                                              ├→ 知识库（不可信文档内容）
                                              └→ SQLite（受控事务）
```

- 用户输入、工单描述、制度文本都是**数据**，不是指令；
- 模型输出必须经 JSON 校验、证据校验和程序规则；
- 未审批提案不得触发 SQL 写入。

## 3. 威胁与控制

| # | 威胁 | 影响 | 控制 | 验证 |
|---|---|---|---|---|
| T1 | Business 调 `search_knowledge`；Policy 调业务工具 | 角色越权 | 模型工具列表裁剪 + 执行前 `TOOL_NOT_PERMITTED`；事件记录 | `test_tools.py`、`test_security.py` |
| T2 | Planner/Auditor 执行工具 | 审核污染 | 工具列表为空 + 执行拒绝 | 同上 |
| T3 | 模型要求不存在/越权工具 | 数据泄漏 | 白名单；无 SQL/命令/文件工具 | `test_business_unknown_tool...` |
| T4 | Policy 模型故障，Business 代查制度 | 伪造制度结论 | Business 无知识工具；`policy_unavailable=true`，报告无制度结论 | `test_policy_model_failure...`、真实 R7 |
| T5 | 未认证/错误 key | 未授权访问 | 所有任务/审批/知识接口 Bearer 认证，401 | `test_missing_and_invalid_token...` |
| T6 | 普通用户访问管理员接口 | 权限提升 | 角色校验 403 | `test_admin_can_read...` |
| T7 | 跨用户读取任务 | 数据泄漏 | owner/department 校验，统一 404 | `test_cross_user_access...` |
| T8 | 普通 executor 审批 | 越权执行 | 仅 approver/admin；请求人不能自批；部门匹配 | `test_self_approval_forbidden`、`test_requester_cannot_approve...` |
| T9 | 未审批/已拒绝提案执行 | 未授权业务写入 | 执行事务要求 `approved`；API 只能 decide；拒绝无写入 | `test_reject_prevents_execution` |
| T10 | 审批后篡改参数 | 执行不同操作 | `params_hash` 绑定；不匹配 409 | `test_params_hash_mismatch_rejected` |
| T11 | 重复审批/重复提交 | 重复工单/预留 | 提案状态原子迁移；executed 返回原结果；预留唯一索引 | `test_approval_executes_once...`、`test_concurrent_approval...` |
| T12 | 并发预留同一资产 | 两名员工拿到同一设备 | `BEGIN IMMEDIATE` + `UPDATE ... WHERE status='in_stock'` + 活跃唯一索引 | 并发测试 |
| T13 | 提示词注入（工单/文档） | 改变权限/跳过审批 | 文本仅作数据；程序标记；注入无法改变工具白名单；提案仍需审批 | `test_untrusted_ticket...`、真实 R5 |
| T14 | 知识库路径穿越/任意文件读取 | 读取服务器文件 | API 只接受 multipart，不使用客户端路径；扩展名/大小/UTF-8 限制 | `test_unsupported...`、`test_file_size_limit` |
| T15 | 伪造证据 ID | 虚假引用 | 业务 claim / 制度 rule 的 evidence ID 程序校验；不存在则降级 | `test_unsupported_claim...`、`test_policy_evidence_missing` |
| T16 | 已废止/草稿被当现行 | 错误审批依据 | 文档元数据 status/effective；检索返回状态；提案生成拒绝非 current 规则；审计复核 | RAG 版本测试、`test_no_proposal_when_policy_is_draft` |
| T17 | 模型 401/429/5xx/超时/无效 JSON/不存在模型 | 伪成功 | 错误分类、有限重试、失败落库、状态降级 | `test_llm.py`、真实 R7 |
| T18 | 并发绕过预算 | 成本失控 | 全局锁 + 每任务独立计数 | `test_global_budget...`、`test_per_task...` |
| T19 | 限流/过载/超长输入 | 资源耗尽 | 429/503/413 | API 测试 |
| T20 | 密钥/Authorization 入日志 | 凭证泄漏 | JSON 日志脱敏；不记录请求头；错误只回稳定码 | `test_authorization_header_is_not_logged`、`test_model_error_is_sanitized` |
| T21 | 容器 root / 密钥入镜像 | 主机/密钥失陷 | uid 10001；密钥仅运行时 env；`.dockerignore` 排除 `.env` | CI docker |
| T22 | 重启后伪完成 | 审计不可信 | 启动把 running 标 `INTERRUPTED`；历史保留 | `test_recovery...` |
| T23 | 备份不可用 | 数据丢失 | SQLite 在线 backup + verify/restore 测试 | `test_backup_restore...` |
| T24 | 明文 HTTP | 凭证窃听 | Compose + Caddy HTTPS（本地内部 CA）；生产受信证书 | CI compose-https |

## 4. 不覆盖

- 企业 SSO/OIDC、多租户、Vault/KMS、WAF/DDoS；
- Kubernetes/多节点一致性；
- 模型内容安全审核（仅结构化与来源校验）；
- 真实企业系统写操作（本项目只操作虚构 SQLite）；
- 生产合规认证。

## 5. 残余风险

1. 静态 Bearer key，泄漏后需手动轮换；
2. 模型仍可能生成"有引用但错误"的结论；审核降低概率而非消除；
3. 单个 SQLite 节点是单点；
4. 本地 HTTPS 使用 Caddy 内部 CA，仅测试；
5. 文档解析库（pypdf/python-docx）自身漏洞需持续更新。
