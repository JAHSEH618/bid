# bid-app v2 升级 Runbook

> 适用版本：从 v1.x（M0–M5）升级到 v2.x（M6–M9）。
> 一次升级只能从 v1 直接到 v2，**没有自动迁移路径**（D1 断旧续新）。

## 升级原则（D1–D6 复盘）

1. **D1 断旧续新**：v1 LangGraph checkpoint 与 v2 graph schema 不兼容。
   所有 in-flight 项目在升级窗口必须由 `flush_running_workflows --confirm`
   显式标 `aborted_v1`，用户在 UI 上看到提示后**重建**项目。**不**尝试
   自动迁移。
2. **D2 黑板 = 磁盘 + DB 路径**：备份脚本必须同时覆盖 PostgreSQL 与
   `/var/lib/bid-app/projects/`（PR-M7-3 后效）。
3. **D3 脱敏不可逆**：原值在后端不持久化；占位符是文档的最终形态，
   用户须手动核对替换。
4. **D5 上传上限**：单文件 200MB / 项目总和 500MB。
5. **D6 UI = 瑞典编辑风**：升级后 UI 视觉风格切换；旧浏览器缓存可能
   显示旧 CSS，强制刷新（⌘⇧R / Ctrl+F5）即可。

## 升级步骤

> 必须按顺序执行，每步只在前一步成功后才进行。

### 1. 备份

```bash
docker compose exec app /usr/local/bin/pg-backup.sh
# PR-M7-3 之后此脚本会把 /var/lib/bid-app/projects/ 同步打包到
# /var/lib/bid-app/backups/projects_YYYYMMDD_HHMM.tar.gz
```

确认备份产物存在且非空，记录文件名（用于回滚）。

### 2. 公告维护窗

在 UI 顶部横幅广播 24h 维护窗口（提前 1-3 天）。

### 3. 拉取新版本并重启

```bash
git pull origin main
./scripts/restart-after-update.sh
# 该脚本内部:docker compose build → up -d → 等 healthcheck
# alembic upgrade head 由 docker/entrypoint.sh 在 uvicorn 起来之前同步跑
```

观察容器日志直到出现 `uvicorn running` 与 `arq worker started`。

### 4. 清退残留 v1 项目

```bash
docker compose exec app python -m bid_app.cli.flush_running_workflows --confirm
```

输出示例：

```
Found 7 in-flight project(s) to flush.
Marked 7 project(s) as 'aborted_v1'. Users will be prompted to recreate them.
```

dry-run 检查（不加 `--confirm`）：

```bash
docker compose exec app python -m bid_app.cli.flush_running_workflows
# 只报数,不写库
```

### 5. 核对 master key 指纹

```bash
# 启动横幅会打印 BID_APP_MASTER_KEY 的 sha256 前缀
docker compose logs app | grep 'master_key_fingerprint'
# 与密码管理器中的备份比对(必须完全一致,否则历史 ApiKey 行无法解密)
```

### 6. 烟囱测试

新建一个测试项目，验证：

- 文档上传（>50MB 单文件、混合类型）→ 异步抽取完成
- 项目材料理解（PR-M8-1）展示 LLM-0 输出
- 目录编辑（PR-M8-2）：拖拽 / 增删章节生效
- 选择性生成（PR-M9-1）：仅生成勾选章节
- 单章导出（PR-M6-2）：approved 章节弹出 .docx
- 整本导出：proposal.docx 正常打开
- 占位符 banner（PR-M6-1）：章节正文里若含 `__ORG_xxx__` 显示提示

### 7. 通知用户

> v2 已上线。你之前未完成的项目（标记为 aborted_v1）需要重新创建。
> 新功能：材料理解 / 目录编辑 / 选择性生成 / 单章导出 / 占位符脱敏。

## 回滚（仅在升级失败且未通知用户时）

```bash
git checkout <previous_tag>
./scripts/restart-after-update.sh
./scripts/restore-backup.sh \
    /var/lib/bid-app/backups/bid_YYYYMMDD_HHMM.dump \
    --with-files
```

注意：
- 已被 `flush_running_workflows --confirm` 标记的项目不会被恢复，除非备份
  里包含原 status。
- v2 期间用户创建的项目在回滚后会变成 v1 不识别的孤儿；优先在升级窗口
  完成验证后再公布。

## 已知限制

- `flush_running_workflows` 是**单次**操作，等同于「v1 → v2 一刀切」；
  v2 → v2.x 之间的小升级不需要再跑（schema_version 仍是 2）。
- v1 checkpoint 残留在 LangGraph `checkpoint_*` 表里不会被自动清理，
  但因为对应 project 已 `aborted_v1`，worker 不会再调度它们。需要回收
  空间时可手动 `TRUNCATE checkpoint_blobs`（仅在确认无 v2 项目还在 resume
  老 checkpoint 的时候）。
