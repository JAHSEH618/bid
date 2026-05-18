#!/usr/bin/env bash
# 把生产环境升级到 D-EF/EG/EH/EI/EJ(WorkflowState schema v3 → v4)。
#
# 用法(在仓库根目录):
#   ./app/scripts/upgrade-to-template-skeleton.sh
#
# 干什么:
#   1. git pull 最新代码
#   2. dry-run 报数在跑项目
#   3. ./restart.sh 重建 + 重启(entrypoint 自动跑 alembic upgrade head)
#   4. flush --confirm 把残留 v3 checkpoint 项目标 aborted_v1
#   5. 校验 template_pack 列 / 模版包 / 校验器
#
# 回滚:
#   git reset --hard <升级前 commit>
#   ./restart.sh
#   docker compose -f app/docker-compose.yml exec app alembic downgrade 0009

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_DIR="$REPO_DIR/app"
COMPOSE=(docker compose -f "$APP_DIR/docker-compose.yml")

cd "$REPO_DIR"

# ── 0) 预检 ──────────────────────────────────────────────────────────
[[ -f "$APP_DIR/.env" ]] || { echo "❌ $APP_DIR/.env 不存在,先恢复"; exit 1; }
"${COMPOSE[@]}" ps app >/dev/null 2>&1 \
  || { echo "❌ bid-app 容器未运行,先 ./restart.sh 起服务再升级"; exit 1; }

echo "=== [1/5] 拉取新代码 ==="
git fetch origin main
echo "本地: $(git rev-parse --short HEAD) / 远端: $(git rev-parse --short origin/main)"
git pull --ff-only origin main

echo
echo "=== [2/5] 预扫:有多少在跑项目会被清退 ==="
# 在重启前的旧容器里 dry-run,只读 DB
"${COMPOSE[@]}" exec -T app \
  python -m bid_app.cli.flush_running_workflows || true

echo
read -r -p "上面这些项目将被标 aborted_v1,用户需要重建。继续?[y/N] " yn
[[ "$yn" =~ ^[Yy]$ ]] || { echo "已取消"; exit 1; }

echo
echo "=== [3/5] 重启容器(自动跑 alembic 0010 加 template_pack 列)==="
"$REPO_DIR/restart.sh"

echo
echo "=== [4/5] 清退 v3 checkpoint 项目 ==="
"${COMPOSE[@]}" exec -T app \
  python -m bid_app.cli.flush_running_workflows --confirm

echo
echo "=== [5/5] 校验 ==="
# template_pack 列存在 + alembic 版本是 0010
"${COMPOSE[@]}" exec -T app python -c "
import asyncio, sqlalchemy as sa
from bid_app.db import session_factory

async def check():
    async with session_factory() as s:
        col = await s.execute(sa.text(
            \"SELECT column_name FROM information_schema.columns \"
            \"WHERE table_name='projects' AND column_name='template_pack'\"
        ))
        assert col.scalar_one_or_none() == 'template_pack', 'template_pack 列未加上'
        ver = await s.execute(sa.text('SELECT version_num FROM alembic_version'))
        v = ver.scalar_one()
        assert v == '0010', f'alembic 版本不是 0010,实际 {v}'
        print(f'✅ alembic_version={v}, projects.template_pack 列存在')

asyncio.run(check())
"

# 模版包能加载
"${COMPOSE[@]}" exec -T app python -c "
from bid_app.workflow.templates import load_pack, DEFAULT_PACK_ID
pack = load_pack(DEFAULT_PACK_ID)
print(f'✅ 模版包 {pack[\"id\"]} 加载成功,{len(pack[\"skeleton\"])} 个 H1')
"

# 校验器能跑
"${COMPOSE[@]}" exec -T app python -c "
from bid_app.services.template_validator import validate_chapter
issues = validate_chapter('## 1 X\n', {'chapter_type': 'module'})
errs = [i for i in issues if i.severity == 'error']
print(f'✅ 校验器工作正常,空 module 章触发 {len(errs)} 个 error(应为 1)')
assert len(errs) == 1
"

echo
echo "🎉 升级完成!"
echo "  • template_pack 列已加,alembic 0010"
echo "  • 在跑项目已标 aborted_v1,用户需重建"
echo "  • 默认骨架 gov_consumer_platform_v1 已加载"
