# 验收对照

> 目标：≥85 分且安全硬门槛全部通过。以下为自评与证据索引，最终以验收人复核为准。

## 1. 功能与评分对照

| 评分项 | 分值 | 实现 | 证据 | 自评 |
|---|---|---|---|---|
| RAG、文档生命周期与来源证据 | 20 | PDF/DOCX/TXT/MD 导入、版本/状态、混合检索、30 题评估 | `src/workbench/knowledge.py`、`ingest.py`、`eval/rag_eval.py`、`evidence/rag/` | 18 |
| 多 Agent 调度、交接与独立审核 | 15 | Planner/Business/Policy/Auditor、并行、handoff、独立审核 | `orchestrator.py`、`agents.py`、`test_flow.py` | 14 |
| 业务工作流、人工审批与数据一致性 | 20 | 提案、审批、事务执行、幂等、并发预留 | `workflow.py`、`test_workflow.py`、真实 R1/R8 | 19 |
| API 安全、工具权限与数据隔离 | 15 | Bearer、角色、owner/department、两层工具授权 | `auth.py`、`permissions.py`、`test_api.py` | 14 |
| 并发预算、异常恢复与可观测性 | 10 | 全局/单任务预算、限流、并发、恢复、审计事件 | `budget.py`、`service.py`、`test_concurrency.py` | 9 |
| 自动化测试、RAG 评估与真实模型验证 | 15 | 112 测试、80% 覆盖、30 题 RAG、8 场景真实评估 | `docs/testing.md`、`evidence/` | 13 |
| 部署、文档及可复现性 | 5 | Docker/Compose/HTTPS、备份、README/架构/威胁/API | `Dockerfile`、`docker-compose.yml`、CI | 5 |
| **合计** | 100 | | | **约 92** |

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
- 112 个自动化测试、覆盖率报告、CI（3.11/3.12 + Docker + HTTPS Compose）；
- 备份/恢复实测。

## 4. 明确未覆盖

- 企业 SSO/OIDC、多租户、Vault/KMS、WAF；
- Kubernetes/多节点一致性；
- 真实资产发放/采购；
- 生产合规认证。
