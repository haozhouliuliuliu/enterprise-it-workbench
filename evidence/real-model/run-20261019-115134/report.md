# 真实模型端到端评估

- 运行：`run-20261019-115134`，调用 50/60
- 用例：8，满足预设断言：8

| 用例 | 状态 | 审核 | 提案 | 调用 |
|---|---|---|---|---|
| R1 | completed | pass | executed | 6 |
| R2 | partial | needs_evidence | - | 7 |
| R3 | partial | needs_evidence | - | 6 |
| R4 | completed | pass | - | 8 |
| R5 | partial | needs_evidence | - | 6 |
| R6 | partial | needs_evidence | - | 7 |
| R7 | partial | None | - | 4 |
| R8 | partial | needs_evidence | - | 6 |

说明：R8 为同一员工重复申请，系统未生成提案、无工单/预留写入。
