#!/usr/bin/env bash
# 把生产环境升级到 D-EK/EL/EM/EN(WorkflowState schema v4 → v5)。
#
# 用法(在仓库根目录):
#   ./app/scripts/upgrade-to-hybrid-retrieval.sh
#
# 干什么:
#   1. git pull 最新代码
#   2. dry-run 报数在跑项目
#   3. ./restart.sh 重建 + 重启(entrypoint 自动跑 alembic 0011 + 0012)
#   4. flush --confirm 把残留 v4 checkpoint 项目标 aborted_v1
#   5. 校验:projects.blackboard_embeddings 列、chapters.references 列、
#      DashScope embedding API 可用、混合召回开关生效
#
# 回滚:
#   git reset --hard <升级前 commit>
#   ./restart.sh
#   docker compose -f app/docker-compose.yml exec app alembic downgrade 0010

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_DIR="$REPO_DIR/app"

# ── 0) 预检 ──────────────────────────────────────────────────────────
[[ -f "$APP_DIR/.env" ]] || { echo "❌ $APP_DIR/.env 不存在,先恢复"; exit 1; }

docker_cmd=(docker)
if ! docker version >/dev/null 2>&1; then
  if sudo -n docker version >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  elif sudo docker version >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
  else
    echo "❌ docker daemon 不可访问且 sudo 也用不了" >&2
    echo "   把当前用户加 docker 组,或用 ``sudo ./app/scripts/upgrade-to-hybrid-retrieval.sh`` 重试" >&2
    exit 1
  fi
fi
COMPOSE=("${docker_cmd[@]}" compose)

cd "$APP_DIR"

status="$("${docker_cmd[@]}" inspect --format '{{.State.Status}}' bid-app 2>&1 || true)"
if [[ "$status" != "running" ]]; then
  echo "❌ bid-app 容器未运行" >&2
  echo "   docker inspect 输出: $status" >&2
  echo "   建议先跑 ./restart.sh" >&2
  exit 1
fi

echo "=== [1/5] 拉取新代码 ==="
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
"${COMPOSE[@]}" exec -T app \
  python -m bid_app.cli.flush_running_workflows || true

echo
echo "schema v4 → v5 不向后兼容:在跑的项目 resume 会被拒,需要标 aborted_v1"
echo "(新启项目走 v5 即刻获得混合召回。已合并完成的老项目不受影响)"
read -r -p "继续?[y/N] " yn
[[ "$yn" =~ ^[Yy]$ ]] || { echo "已取消"; exit 1; }

echo
echo "=== [3/5] 重启容器(自动跑 alembic 0011 + 0012)==="
env -u SKIP_BUILD "$REPO_DIR/restart.sh"

echo
echo "=== [4/5] 清退 v4 checkpoint 项目 ==="
"${COMPOSE[@]}" exec -T app \
  python -m bid_app.cli.flush_running_workflows --confirm

echo
echo "=== [5/5] 校验 ==="
# 0011 + 0012 列都加上,alembic 头版本 0012
"${COMPOSE[@]}" exec -T app python -c "
import asyncio, sqlalchemy as sa
from bid_app.db import session_factory

async def check():
    async with session_factory() as s:
        c1 = await s.execute(sa.text(
            \"SELECT column_name FROM information_schema.columns \"
            \"WHERE table_name='projects' AND column_name='blackboard_embeddings'\"
        ))
        assert c1.scalar_one_or_none() == 'blackboard_embeddings', '0011 列缺失'
        c2 = await s.execute(sa.text(
            \"SELECT column_name FROM information_schema.columns \"
            \"WHERE table_name='chapters' AND column_name='references'\"
        ))
        assert c2.scalar_one_or_none() == 'references', '0012 列缺失'
        ver = await s.execute(sa.text('SELECT version_num FROM alembic_version'))
        v = ver.scalar_one()
        assert v == '0012', f'alembic 版本不是 0012,实际 {v}'
        print(f'✅ alembic_version={v}, blackboard_embeddings + references 两列存在')

asyncio.run(check())
"

# 混合召回模块可加载
"${COMPOSE[@]}" exec -T app python -c "
from bid_app.services.hybrid_retrieval import rrf_fuse, DEFAULT_RRF_K
from bid_app.services.embeddings import EMBEDDING_DIM
from bid_app.services.blackboard_retrieval import BlackboardIndex
idx = BlackboardIndex({'scoring_rules': [{'content': 'X'}]}, embeddings={'scoring_rules': [[0.0]*EMBEDDING_DIM]})
print(f'✅ 混合召回模块加载 (RRF k={DEFAULT_RRF_K}, dim={EMBEDDING_DIM}); index size={len(idx)}, has_emb={idx.has_embeddings()}')
"

# 配置开关
"${COMPOSE[@]}" exec -T app python -c "
from bid_app.config import settings
print(f'  hybrid_retrieval_enabled = {settings.hybrid_retrieval_enabled}')
print(f'  embedding_model = {settings.embedding_model}')
print(f'  hybrid_rrf_k = {settings.hybrid_rrf_k}')
"

echo
echo "升级完成。"
echo "  - blackboard_embeddings + references 两列已加,alembic 0012"
echo "  - 在跑项目已标 aborted_v1,用户需重建"
echo "  - 新项目自动获得混合召回 + 参考资料展示 + 提前合并按钮"
echo "  - 失败回退路径:DashScope embedding API 故障 → 全零向量 → 退化纯 BM25"
echo "  - 想关掉混合召回,设置 BID_APP_HYBRID_RETRIEVAL_ENABLED=false 后重启"
