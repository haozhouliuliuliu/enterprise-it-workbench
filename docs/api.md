# API 文档

所有 `/v1/*` 接口（除 health）要求：

```http
Authorization: Bearer <api-key>
```

角色：`executor`（提交/查看自己的任务）、`approver`（本部门审批）、`admin`（全部任务与知识库）。

## 身份

### GET /v1/me
返回 `{user, role, department}`。

## 任务

### POST /v1/tasks
```json
{"request": "研发中心新员工 EMP-1012 申请预留一台 ThinkPad T14 Gen5 笔记本电脑。"}
```
202：`{"task_id","status":"accepted","owner"}`。

### GET /v1/tasks?limit=20
仅返回当前用户任务。

### GET /v1/tasks/{task_id}
返回：
```json
{
  "task_id": "...", "status": "needs_approval", "owner": "alice",
  "model_calls": 6, "total_tokens": 12000,
  "result": {"conclusion": "...", "audit_verdict": "pass", "evidence": [], "proposal": {}},
  "proposal": {"proposal_id": "PR-...", "actions_json": "...", "params_hash": "...", "status": "pending"},
  "error_code": null
}
```
非本人/不存在统一 404。

## 审批

### GET /v1/approvals
approver/admin；approver 仅本部门 pending 提案。

### POST /v1/approvals/{proposal_id}/decision
```json
{"decision": "approve", "params_hash": "..."}
```
- approve：事务执行（预留资产 + 创建工单），返回 `{proposal_id,status:"executed",result}`；
- reject：`{"decision":"reject","reason":"..."}`，任务变 `rejected`，无写入；
- 请求人自批 403；跨部门 403；hash 不匹配/状态不对 409；
- 重复 approve 返回原执行结果，不重复写入。

## 知识库（admin）

### GET /v1/knowledge/documents
列出文档元数据（版本、状态、hash、索引状态、chunk 数）。

### POST /v1/knowledge/documents
`multipart/form-data`：
- `file`：PDF/DOCX/TXT/MD，最大 2MB；
- `title`、`doc_id`、`version`、`status`（current/superseded/draft）、`doc_type`、`source`、
  `doc_group`、`effective_from`、`effective_to`。

201 返回 `{doc_pk, doc_id, version, status, file_hash, doc_type, chunks}`。
重复文件 409；格式/解析错误 400；过大 413。

### DELETE /v1/knowledge/documents/{doc_pk}
204；删除文档与 chunks。

### POST /v1/knowledge/reindex
重算所有 chunk 向量，返回数量。

## 健康检查

- `GET /health/live` → 200 `{"status":"ok"}`
- `GET /health/ready` → 200/503（SQLite、API key、模型配置）

## 工作台

`GET /` 返回单页工作台（登录、提交、任务、审批、知识库）。API key 只保存在页面内存变量，
不使用 localStorage/sessionStorage；页面不包含任何服务端密钥。

## 错误码

`UNAUTHENTICATED/INVALID_CREDENTIALS 401`、`FORBIDDEN 403`、`TASK_NOT_FOUND 404`、
`INPUT_TOO_LARGE/FILE_TOO_LARGE 413`、`RATE_LIMITED 429`、`OVERLOADED 503`、
`SELF_APPROVAL_FORBIDDEN/PARAMS_CHANGED/PROPOSAL_NOT_PENDING 403/409`。
供应商原始错误不会返回给客户端。
