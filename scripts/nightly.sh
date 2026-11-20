#!/usr/bin/env bash
# 夜间批处理：抓语料 -> 构建 -> 生成静态产物 -> 部署 -> 自检
#
# 用法：
#   bash scripts/nightly.sh            # 不重新下载语料，构建后部署
#   bash scripts/nightly.sh --fetch    # 先补抓语料（汉魏六朝、唐、宋等）
#   bash scripts/nightly.sh --no-deploy  # 只构建，不部署（NAS 无 node 时用）
#
# 凭据从项目根目录的 .env 读取，参见 .env.example。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FETCH=0
DEPLOY=1
for arg in "$@"; do
  case "$arg" in
    --fetch) FETCH=1 ;;
    --no-deploy) DEPLOY=0 ;;
    *) echo "未知参数：$arg"; exit 2 ;;
  esac
done

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

mkdir -p logs
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="logs/nightly-$STAMP.log"
exec > >(tee -a "$LOG") 2>&1
echo "== $STAMP 开始（fetch=$FETCH deploy=$DEPLOY）"

python -c "import sys; print('python', sys.version.split()[0])"

if [ "$FETCH" = "1" ]; then
  echo "-- 抓取语料"
  python tools/fetch_full.py all
  python tools/fetch_classics.py all
fi

echo "-- 构建语料"
PYTHONPATH="$ROOT" python -m tagger.build

echo "-- 生成 Cloudflare 产物"
python tools/build_cf.py

if [ "$DEPLOY" = "1" ]; then
  if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    echo "!! 未设置 CLOUDFLARE_API_TOKEN，跳过部署"
  elif ! command -v npx >/dev/null 2>&1; then
    echo "!! 未找到 npx（需要 Node.js），跳过部署；产物已生成在 cloudflare/public"
  else
    echo "-- 部署"
    (cd cloudflare && npx wrangler deploy --config wrangler.jsonc)
    echo "-- 自检"
    python tools/verify_online.py || echo "!! 自检未通过，查看上面输出"
  fi
fi

echo "== $STAMP 完成，日志：$LOG"
