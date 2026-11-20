#!/usr/bin/env bash
# 给新增语料（汉魏六朝、元曲、隋、秦汉、诗经、楚辞、五代、清词）补题材标注。
# 只处理规则弱类和低置信作品，与之前 76k 语料的策略一致。
#
# 用法：bash scripts/label_new_corpora.sh [--no-deploy]
# 可反复执行：已标注的 uid 会被跳过。

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEPLOY=1
[ "${1:-}" = "--no-deploy" ] && DEPLOY=0

mkdir -p logs
LOG="logs/label-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

SEC=(
  --section 魏晋南北朝 --section 元曲 --section 隋 --section 秦汉
  --section 诗经 --section 楚辞 --section 五代 --section 清词
)
WEAK=(
  --theme 寄赠酬答 --theme 节令风俗 --theme 闲适隐逸 --theme 应制干谒
  --theme 佛道禅颂 --theme 悼亡挽歌 --theme 咏怀抒感
)

echo "== 1/4 弱类初判"
python tools/llm_tag.py "${SEC[@]}" "${WEAK[@]}" -j 12 --out data/theme_labels.jsonl

echo "== 2/4 低置信初判"
python tools/llm_tag.py "${SEC[@]}" --max-rule-conf 0.35 -j 12 --out data/theme_labels.jsonl

echo "== 3/4 分歧终审"
python tools/llm_tag.py "${SEC[@]}" --review-with data/theme_labels.jsonl \
  --max-rule-conf 0.35 -j 8 --out data/theme_labels_review.jsonl

echo "== 4/4 重建"
PYTHONPATH="$ROOT" python -m tagger.build
python tools/build_cf.py

if [ "$DEPLOY" = "1" ]; then
  (cd cloudflare && npx wrangler deploy --config wrangler.jsonc)
  python tools/verify_online.py
fi

echo "== 完成，日志 $LOG"
