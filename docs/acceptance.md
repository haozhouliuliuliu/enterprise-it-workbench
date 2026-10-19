# 验收对照

> 目标：≥85 分且安全硬门槛全部通过。以下为自评与证据索引，最终以验收人复核为准。

## 1. 功能与评分对照

| 评分项 | 分值 | 实现 | 证据 | 自评 |
|---|---|---|---|---|
| RAG、文档生命周期与来源证据 | 20 | PDF/DOCX/TXT/MD 导入、版本/状态、混合检索、30 题评估 | `src/workbench/knowledge.py`、`ingest.py`、`eval/rag_eval.py`、`evidence/rag/` | 18 |
| 多 Agent 调度、交接与独立审核 | 15 | Planner/Business/Policy/Auditor、并行、handoff、独立审核 | `orchestrator.py`、`agents.py`、`test_flow.py` | 14 |
| 业务工作流、人工审批与数据一致性 | 20 | 提案、审批、强制哈希、事务执行、幂等、并发/重复设备防护 | `workflow.py`、`test_workflow.py`、`test_authorization.py`、真实 R1/R8 | 20 |
| API 安全、工具权限与数据隔离 | 15 | Bearer、角色、非空部门、owner/department、两层工具授权 | `auth.py`、`permissions.py`、`test_api.py`、`test_authorization.py` | 15 |
| 并发预算、异常恢复与可观测性 | 10 | 全局/单任务预算、限流、并发、恢复、审计事件 | `budget.py`、`service.py`、`test_concurrency.py` | 9 |
| 自动化测试、RAG 评估与真实模型验证 | 15 | 112 测试、80% 覆盖、30 题 RAG、8 场景真实评估 | `docs/testing.md`、`evidence/` | 13 |
| 部署、文档及可复现性 | 5 | Docker/Compose/HTTPS、备份、README/架构/威胁/API | `Dockerfile`、`docker-compose.yml`、CI | 5 |
| **合计** | 100 | | | **约 94** |

扣分点：RAG 为轻量向量实现、真实评估样本有限、静态 API key、单节点 SQLite、审批单级。

## 2. 安全硬门槛

| 硬门槛 | 结果 | 证据 |
|---|---|---|
| 伪造证据 ID 不被认可 | 通过 | `test_unsupported_claim_is_downgraded`、制度 rule 校验 |
| 废止/草稿不作为现行审批依据 | 通过 | RAG 版本指标 100%、`test_no_proposal_when_policy_is_draft` |
| 未审批/已拒绝不产生写入 | 通过 | `test_reject_prevents_execution`、API 测试 |
| 跨用户任务不可读 | 通过 | `test_cross_user_access_returns_404` |
| 请求人不能自批 / 越权审批 | 通过 | `test_requester_cannot_approve_own_request`、`test_self_approval_forbidden` |
| 并发不重复预留/执行 | 通过 | `test_concurrent_approval_only_one_reserves_single_asset`、R8 |
| 模型/数据库故障不伪成功 | 通过 | 真实 R7、`test_invalid_model_json...`、事务回滚 |
| 工具权限不可被提示词改变 | 通过 | 两层授权 + `test_security.py` |

## 3. 交付物

- 独立公开仓库与版本标签；
- README、架构、威胁模型、API、部署、已知限制；
- 固定 RAG 评估（30 题）与报告；
- 8 场景真实模型评估（任务状态、审核、模型、调用数、证据、失败归因）；
- 127 个自动化测试、覆盖率报告、CI（3.11/3.12 + Docker + HTTPS Compose）；
- 备份/恢复实测。

## 4. 明确未覆盖

- 企业 SSO/OIDC、多租户、Vault/KMS、WAF；
- Kubernetes/多节点一致性；
- 真实资产发放/采购；
- 生产合规认证。

## 5. 复验修复（针对首轮验收意见）

| 问题 | 修复 | 证据 |
|---|---|---|
| 空部门 approver 可跨部门 | 配置层强制 approver 部门非空；服务层对空部门拒绝审批列表/任务读取/决策；workflow 严格比较部门 | `test_approver_without_department...`、`test_empty_department_approver...` |
| approve 可省略 params_hash | API 模型校验 approve 必填；service 再次检查；workflow 强制非空并比对；执行前重算 actions 哈希 | `test_approve_without_hash...`、`test_tampered_actions...`、API 缺 hash 422/错 hash 409 |
| 制度状态依赖模型 | 新增 `KnowledgeStore.document_metadata`；审核与提案生成均用知识库权威 doc_id/version/status/有效期；模型 status 仅作待核对字段 | `test_model_lies_about_draft_status_blocks_proposal`；`_program_issues` 权威校验 |
| 目标员工部门与申请部门不一致 | 提案生成前比较员工部门与任务部门；无明确跨部门代办机制时阻断并记录 `proposal_blocked` | `test_cross_department_target_employee_blocks_proposal` |
| 重复申请未程序级阻断 | 提案前查询员工在用在库/有效预留并记录 `proposal_blocked`；执行事务再次检查；`wb_reservations` 员工活跃唯一索引 | `test_employee_with_existing_asset...`、`test_employee_with_active_reservation...` |
| UI 浏览器测试吞掉断言失败 | 仅浏览器/环境启动失败时 skip；断言失败会让测试失败 | `test_ui.py` 结构审查 |
| UI 动态 HTML 未转义 | 所有动态字段使用 `escapeHtml`；任务/审批按钮改用 data 属性 + 事件监听；真实 Edge 浏览器测试恶意内容只显示为文本 | `tests/test_ui.py`（quickjs + Playwright/Edge） |
| RAG 报告口径 | 统一使用最终运行 `refusal_accuracy=1.0`（10/10） | `evidence/rag/run-20261019-104746/report.json`、`docs/testing.md` |
