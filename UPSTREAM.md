# 上游来源与技术关系

项目⑧是独立仓库，不修改项目②、④、⑤、⑦的已发布版本。

| 上游 | 复用/参考 | 本项目处理 |
|---|---|---|
| 项目② RAG 文档处理 | 分块、来源引用、固定评估思路 | 重新实现持久化混合检索（SQLite + hashing embedding + BM25），加入版本/状态元数据与文档生命周期 |
| 项目④ Agent 工具与审批 | 工具注册、参数校验、审批/幂等思想 | 重构为按角色强制授权的工具注册表 + 提案/审批/事务执行 |
| 项目⑤ 多 Agent 协作 | Planner/Business/Policy/Auditor、并行、交接、审核 | 复用编排思路；Policy 改为调用 RAG；新增工作流状态与提案 |
| 项目⑦ 安全服务 | Bearer 认证、角色、任务隔离、预算、日志脱敏、Docker/HTTPS | 扩展审批人角色、知识库管理员、审批接口保护；继续沿用两层工具授权 |

## 基线

- 项目⑤：https://github.com/haozhouliuliuliu/it-agent-collab `v1.0.0` / `fb17bfc`
- 项目⑦：https://github.com/haozhouliuliuliu/secure-agent-runtime `v1.0.0` / `5d0af09`
- 本项目从项目⑦代码结构出发，重命名包为 `workbench`，新增知识库、RAG、工作流、界面与相应测试。

许可证：MIT，保留上游版权声明（见 `LICENSE`）。
