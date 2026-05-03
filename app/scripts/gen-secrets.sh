#!/usr/bin/env bash
# 生成 .env 最终版本(D-R 修正):从 .env.example seed,把所有 __GENERATE_ME__ /
# __64_HEX_CHARS__ 占位符替换为真随机密钥。已经填好真值的字段不动。
# 参考 IMPLEMENTATION_SPEC §6.2 / R10
#
# 用法:
#   ./scripts/gen-secrets.sh                  # 默认 ./.env
#   ./scripts/gen-secrets.sh foo.env
#   EXAMPLE_FILE=./.env.example ./scripts/gen-secrets.sh
#
# 行为:
#   - 读 EXAMPLE_FILE(默认 .env.example)
#   - cp 到 OUT(默认 .env);若 OUT 已存在,拒绝覆盖(防误操作)
#   - 用 Python secrets 生成:
#     · BID_APP_MASTER_KEY = token_hex(32)  -> 64 hex chars(R10 强制长度)
#     · JWT_SECRET         = token_hex(32)
#     · POSTGRES_PASSWORD  = token_urlsafe(24)
#   - sed in-place 替换占位符
#   - chmod 600 OUT(只允许 owner 读写)
#   - 校验:确保 OUT 已无残留占位符,否则退出 2
set -euo pipefail

EXAMPLE="${EXAMPLE_FILE:-.env.example}"
OUT="${1:-.env}"

if [[ ! -f "$EXAMPLE" ]]; then
  echo "❌ $EXAMPLE 不存在,先 cd 到 app/ 再跑" >&2
  exit 1
fi
if [[ -f "$OUT" ]]; then
  echo "❌ $OUT 已存在,拒绝覆盖" >&2
  echo "   要重置:mv $OUT $OUT.bak && $0 $OUT" >&2
  exit 1
fi

# 校验 python3 可用(用 Python 而不是 openssl,避免不同发行版差异)
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 未安装" >&2
  exit 1
fi

umask 077
cp "$EXAMPLE" "$OUT"

gen_hex() { python3 -c 'import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))' "$1"; }
gen_url() { python3 -c 'import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))' "$1"; }

MASTER_KEY="$(gen_hex 32)"
JWT_SECRET="$(gen_hex 32)"
PG_PASSWORD="$(gen_url 24)"

# R10 防御:再次确认 master_key 是 64 hex(token_hex(32) 总是给 64 chars,
# 但若被篡改则提早暴露)
if [[ ! "$MASTER_KEY" =~ ^[0-9a-f]{64}$ ]]; then
  echo "❌ MASTER_KEY 长度异常,生成失败" >&2
  exit 1
fi
if [[ ! "$JWT_SECRET" =~ ^[0-9a-f]{64}$ ]]; then
  echo "❌ JWT_SECRET 长度异常,生成失败" >&2
  exit 1
fi

# sed 分隔符用 |(避开 base64 / hex 字符)
sed -i.bak \
  -e "s|^BID_APP_MASTER_KEY=.*|BID_APP_MASTER_KEY=${MASTER_KEY}|" \
  -e "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASSWORD}|" \
  "$OUT"
rm -f "${OUT}.bak"
chmod 600 "$OUT"

# 自检:不能仍残留占位符。
# 注意:.env.example 头部注释里也写了占位符字面量(给读者解释),不能裸 grep
# 否则误命中。anchor `^[A-Z_]+=`,只看 KEY=VALUE 形式(忽略 # 注释行)。
if grep -E "^[A-Z_]+=.*(__GENERATE_ME__|__64_HEX_CHARS__)" "$OUT" >/dev/null; then
  echo "❌ $OUT 仍有未替换占位符,请检查 .env.example 字段名" >&2
  grep -nE "^[A-Z_]+=.*(__GENERATE_ME__|__64_HEX_CHARS__)" "$OUT" >&2
  exit 2
fi

echo "✅ 已生成 $OUT (mode 600)"
echo "   BID_APP_MASTER_KEY: ${MASTER_KEY:0:8}...(64 hex chars)"
echo "   JWT_SECRET:         ${JWT_SECRET:0:8}...(64 hex chars)"
echo "   POSTGRES_PASSWORD:  ${#PG_PASSWORD} chars (random url-safe)"
echo ""
echo "⚠️  R10 警告:BID_APP_MASTER_KEY 一旦丢失,所有 ApiKey.encrypted_key 永久不可解密"
echo "    强烈建议在密码管理器(1Password / Bitwarden)留备份"
