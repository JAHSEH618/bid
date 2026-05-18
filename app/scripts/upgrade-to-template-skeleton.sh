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

# ── 0) 预检 ──────────────────────────────────────────────────────────
[[ -f "$APP_DIR/.env" ]] || { echo "❌ $APP_DIR/.env 不存在,先恢复"; exit 1; }

# docker daemon 可能需要 sudo。**关键**:用 ``docker version`` 而不是
# ``docker compose version`` 检测 —— 后者只验插件二进制存在,不连 daemon;
# 用户(非 docker 组)能跑通这条,但所有真 docker 命令都会被 socket 权限拒。
docker_cmd=(docker)
if ! docker version >/dev/null 2>&1; then
  if sudo -n docker version >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  elif sudo docker version >/dev/null 2>&1; then
    # 上一行可能弹了密码,弹完进缓存,后续都走 sudo
    docker_cmd=(sudo docker)
  else
    echo "❌ docker daemon 不可访问且 sudo 也用不了" >&2
    echo "   把当前用户加 docker 组,或用 ``sudo ./app/scripts/upgrade-to-template-skeleton.sh`` 重试" >&2
    exit 1
  fi
fi
COMPOSE=("${docker_cmd[@]}" compose)

# docker compose 从 docker-compose.yml 所在目录推断 project,直接 cd 到
# APP_DIR 整轮跑;git 操作用 ``git -C "$REPO_DIR"`` 在 REPO_DIR 上操作。
cd "$APP_DIR"

# 容器运行检测:用 ``docker inspect`` 按容器名直查(对齐
# restart-after-update.sh 的 healthcheck 逻辑),绕开 compose project name
# 解析的歧义(否则不同 cwd / COMPOSE_PROJECT_NAME / override.yml 都可能让
# ``compose ps -q`` 返空)。
status="$("${docker_cmd[@]}" inspect --format '{{.State.Status}}' bid-app 2>&1 || true)"
if [[ "$status" != "running" ]]; then
  echo "❌ bid-app 容器未运行" >&2
  echo "   docker inspect 输出: $status" >&2
  echo "   建议先跑 ./restart.sh" >&2
  exit 1
fi

echo "=== [1/5] 拉取新代码 ==="
# 选 git remote:优先 ``$UPGRADE_REMOTE``;否则有 ``gitee`` 用 gitee
# (国内服务器走 GitHub 会超时);没 gitee 回落 ``origin``。
remote="${UPGRADE_REMOTE:-}"
if [[ -z "$remote" ]]; then
  if git -C "$REPO_DIR" remote get-url gitee >/dev/null 2>&1; then
    remote=gitee
  else
    remote=origin
  fi
fi
echo "git remote: $remote"
git -C "$REPO_DIR" fetch "$remote" main
echo "本地: $(git -C "$REPO_DIR" rev-parse --short HEAD) / 远端: $(git -C "$REPO_DIR" rev-parse --short "$remote/main")"
git -C "$REPO_DIR" pull --ff-only "$remote" main

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
# 本次升级必须 rebuild 镜像 — flush_running_workflows.py / template_validator
# / 新 prompts 等都需要烤进镜像。即使外层 shell 设了 ``SKIP_BUILD=1``,
# 也必须强制 build。用 ``env -u SKIP_BUILD`` 清掉环境变量。
env -u SKIP_BUILD "$REPO_DIR/restart.sh"

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
