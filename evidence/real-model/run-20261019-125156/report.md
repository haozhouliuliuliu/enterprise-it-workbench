# 真实模型端到端评估

- 运行：`run-20261019-125156`，调用 48/60
- 用例：8，满足预设断言：8

| 用例 | 状态 | 审核 | 提案 | 调用 |
|---|---|---|---|---|
| R1 | completed | pass | executed | 7 |
| R2 | partial | needs_evidence | - | 6 |
| R3 | partial | needs_evidence | - | 6 |
| R4 | partial | needs_evidence | - | 6 |
| R5 | partial | fail | - | 6 |
| R6 | partial | needs_evidence | - | 7 |
| R7 | partial | None | - | 4 |
| R8 | completed | pass | - | 6 |

说明：R8 由程序检测到员工已持有 ASSET-1002，拒绝生成提案，工单/预留增量为 0。
