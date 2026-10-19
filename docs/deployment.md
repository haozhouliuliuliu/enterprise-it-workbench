# 部署、HTTPS 与备份恢复

## 1. 本地 Compose（HTTPS）

```bash
export SERVICE_API_KEYS='[{"key":"exec-key-change-me","user":"alice","role":"executor","department":"研发中心"},{"key":"approver-key-change-me","user":"carol","role":"approver","department":"研发中心"},{"key":"admin-key-change-me","user":"root","role":"admin"}]'
export DEEPSEEK_API_KEY=... QWEN_API_KEY=... KIMI_API_KEY=...
docker compose up --build -d
curl -k https://localhost:8443/health/live
curl -k -o /dev/null -w "%{http_code}\n" https://localhost:8443/v1/tasks   # 401
```

- Agent 以 uid 10001 运行，SQLite/知识库在 `workbench-data` 卷；
- Caddy `tls internal` 生成本地 CA 证书，仅测试；
- 首次启动自动初始化业务数据与知识库。

## 2. 生产 HTTPS

1. 使用受信任证书（ACME/企业 CA），替换 `deploy/Caddyfile` 的 `tls internal`；
2. 域名示例：

```caddyfile
workbench.example.com {
    reverse_proxy agent:8000
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
    }
}
```

3. 只暴露 443；8000 仅容器网络；
4. 反向代理层限制请求体大小、超时与速率；
5. 密钥使用 Vault/KMS 或容器 secret，而非 `.env` 文件。

## 3. 健康检查与优雅关闭

- `/health/live`：进程存活；
- `/health/ready`：SQLite 可读、API key 与四个模型配置存在；
- Compose healthcheck 使用 live；
- SIGTERM 后 uvicorn 停止接收请求，lifespan 关闭线程池；
- 重启时 `planning/running` 任务标 `failed/INTERRUPTED`，历史与审批记录保留。

## 4. 备份

```bash
docker compose exec agent python -m workbench.backup_cli backup \
  --db /app/data/collab.db --out /app/data/backup-20261019.db
docker compose cp agent:/app/data/backup-20261019.db ./backup-20261019.db
python -m workbench.backup_cli verify --db ./backup-20261019.db
```

知识库在 `/app/data/knowledge.db`，与业务库一起备份；使用 SQLite 在线 backup API，
不要直接复制正在写入的 WAL 文件。

## 5. 恢复

```bash
docker compose stop agent
docker compose cp ./backup-20261019.db agent:/app/data/collab.db
docker compose start agent
curl -k https://localhost:8443/health/ready
```

恢复前停止 Agent；恢复后检查 ready、任务列表、待审批与知识库文档。

## 6. 保留与删除

- 任务默认保留 30 天（`SERVICE_TASK_RETENTION_DAYS`）；
- 管理员 `DELETE /v1/tasks/{id}` 删除任务及轨迹；
- 批量清理：`python -m workbench.backup_cli purge --db data/collab.db --days 30`；
- 文档删除：`DELETE /v1/knowledge/documents/{doc_pk}`；
- 备份文件单独设置访问控制与保留期。

## 7. 恢复演练

自动化测试 `test_backup_restore_roundtrip`：生成任务 → 备份 → 删除 → 校验计数 → 恢复 → 任务可读。
CI 的 `compose-https` job 真实启动 Caddy + Agent，验证 HTTPS、401 与工作台页面。
