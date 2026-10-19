# 测试与评估

> 所有结果来自实际运行；失败的轮次保留，不删除难题。

## 1. 自动化测试

```bash
pytest -q
pytest --cov=workbench --cov-report=term
```

- **112 passed**；
- 覆盖率 **84%**（CI 门槛 80%）；
- Python 3.11 / 3.12（CI）；
- ruff check / format 通过。

| 领域 | 代表测试 |
|---|---|
| 文档导入 | PDF/DOCX/TXT/MD、损坏文件、不支持类型、2MB 限制、UTF-8 |
| 知识库 | 分块、证据 ID、去重、同版本冲突、新版本 supersede、删除、重建索引、重启持久化 |
| RAG | Top-5、引用、无答案拒答、草稿/失效日期 |
| 多 Agent | 路由、并行、handoff、审核 pass/needs/fail、伪造证据降级 |
| 权限 | 两层层工具授权、Planner/Auditor 无工具、Policy 故障不替代 |
| API | 401/403/404/413/429/503、任务隔离、审批队列、知识库管理员接口 |
| 工作流 | 提案生成条件、审批、拒绝、自批/跨部门、参数哈希、幂等、并发预留 |
| 状态/恢复 | 重启 INTERRUPTED、历史读取、备份恢复、清理 |
| 日志/密钥 | 脱敏、错误响应不含供应商原文 |

## 2. RAG 固定评估

```bash
python -m eval.rag_eval --output evidence/rag
```

30 题：20 有明确答案、10 故意缺少证据。

| 指标 | 结果 | 门槛 |
|---|---|---|
| Top-5 召回 | 100% | ≥85% |
| 引用正确率 | 90% | ≥90% |
| 无答案拒答 | 9/10 | ≥9/10 |
| 版本/状态正确 | 100% | 不把废止/草稿当现行 |

证据：`evidence/rag/run-20261019-104746/`（`report.json` 含逐题命中文档、状态、分数）。

说明：引用正确率按"确定性抽取答案的 top-1 文档是否等于期望文档"计算；不把"回答里出现引用编号"当作正确。

## 3. 真实模型端到端评估

## 3. 真实模型端到端评估

运行：`evidence/real-model/run-20261019-115134/`（真实 FastAPI + 真实模型，50/60 次调用）

| 用例 | 场景 | 任务状态 | 审核 | 提案/执行 | 调用 | 预期行为 |
|---|---|---|---|---|---|---|
| R1 | 标准设备预留 | completed | pass | 提案已批准并执行（工单+1，预留+1） | 6 | 满足 |
| R2 | L14 库存不足 | partial | needs_evidence | 无提案 | 7 | 满足 |
| R3 | X1 非部门标准机型 | partial | needs_evidence | 无提案 | 6 | 满足 |
| R4 | 现行版 vs 草稿 | completed | pass | 分析类，无提案 | 8 | 满足 |
| R5 | 工单注入指令 | partial | needs_evidence | 无提案 | 6 | 满足 |
| R6 | 员工不存在 | partial | needs_evidence | 无提案 | 7 | 满足 |
| R7 | Policy 模型 404 | partial | -（审核跳过） | policy_unavailable=true，无提案 | 4 | 满足 |
| R8 | 同一员工重复申请 | partial | needs_evidence | 无提案，工单/预留增量为 0 | 6 | 满足 |

- **8/8 符合预设断言**；其中 R1–R7 均为多 Agent 协作，R8 为重复申请不重复写入；
- R1 审批接口返回 200，数据库工单 +1、预留 +1，资产状态变为 reserved；
- R7 制度模型不可用时，Business 没有制度检索权限，最终报告没有制度结论；
- R8 因员工已持有设备，审核拒绝生成提案，未产生任何写入（这是期望行为，不是失败）。

说明：R1 的审批幂等由自动化测试 `test_approval_executes_once_and_writes_ticket_and_reservation`
覆盖；R8 真实评估验证的是"同一员工重复申请不重复写入"。真实调用总量 50 次，未超 60。

评估过程中评估器自身曾因 TestClient 生命周期/变量遮蔽中断，修复后完成上述运行；失败轮次未计入本报告。


## 4. 复现

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest -q --cov=workbench --cov-fail-under=80

python -m eval.rag_eval --output evidence/rag

set DEEPSEEK_API_KEY=...
set QWEN_API_KEY=...
set KIMI_API_KEY=...
python -m eval.harness --output evidence/real-model
```

真实评估使用进程内 FastAPI TestClient + 真实模型；API key 仅内存生成，不依赖外部配置。
