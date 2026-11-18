# -*- coding: utf-8 -*-
"""题材精度基准：用唐诗三百首 / 宋词三百首的人工标签作金标准，量 top-1 准确率。

为什么用这个集子
    两集子里的 `tags` 是人工标注，含"边塞""送别""怀古"这类明确的题材词，
    可以无歧义地映射到我们 19 个题材。646 首里能选出约 200 首作为金标准，
    样本量虽小但标签可信，足以判断一次改动是提升还是回退。

用法
    python tools/eval_theme.py            # 只看总体
    python tools/eval_theme.py -v         # 列出每条错例及驱动词
"""

import json
import os
import sys
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagger import engine, legacy, simp  # noqa: E402

# 人工标签 -> 金标准题材。只收无歧义的映射，含糊的一律丢弃。
GOLD = {
    "边塞征战": ["边塞", "战争", "将士", "戍边", "征人", "出塞"],
    "送别赠答": ["送别", "离别", "赠别", "惜别", "友情", "友人", "酬赠", "酬答"],
    "咏史怀古": ["怀古", "咏史", "咏史怀古", "吊古伤今", "凭吊古迹"],
    "咏物寄兴": ["咏物", "咏物诗", "托物寄情"],
    "闺怨宫怨": ["闺怨", "宫怨", "怨情"],
    "悼亡伤逝": ["悼亡"],
    "爱情相思": ["爱情", "新婚"],
    "羁旅思乡": ["思乡", "思归", "羁旅"],
    "山水田园": ["山水", "田园"],
    "闲适隐逸": ["归隐", "隐逸"],
    "哲理悟道": ["哲理"],
    "讽喻刺世": ["讽刺", "讽喻"],
    "民生疾苦": ["同情", "忧国忧民"],
    "题画咏艺": ["音乐", "题画"],
    "游仙梦幻": ["游仙"],
    # 节令标签噪声大（《月下独酌》被旧标签标成"中秋"），不计入金标准
    "节令风俗": [],
}
TAG2THEME = {t: th for th, ts in GOLD.items() for t in ts}


def gold_of(old_tags):
    hit = {TAG2THEME[t] for t in old_tags if t in TAG2THEME}
    return hit.pop() if len(hit) == 1 else None


def load(keys=("tang300", "song300")):
    out = []
    for k in keys:
        p = os.path.join(ROOT, "raw", f"{k}.json")
        if os.path.exists(p):
            for poem in json.load(open(p, encoding="utf-8")):
                out.append(poem)
    return out


def run(verbose=False):
    rows = []
    for poem in load():
        g = gold_of(poem.get("tags") or [])
        if not g:
            continue
        title = simp.to_simp(poem.get("title") or poem.get("rhythmic") or "")
        paras = [simp.to_simp(x) for x in (poem.get("paragraphs") or [])]
        sp = {"title": title, "author": poem.get("author"),
              "paragraphs": paras, "rhythmic": poem.get("rhythmic")}
        old = poem.get("tags") or []
        rec = engine.tag_poem(sp, legacy.prior(old), legacy.form_override(old))
        pred = rec["tags"]["theme"]["primary"]
        rows.append((g, pred, title, poem.get("author"), old))

    ok = sum(1 for g, p, *_ in rows if g == p)
    n = len(rows)
    print(f"金标准样本 {n} 首，top-1 命中 {ok}，准确率 {ok / n:.1%}")

    per = collections.defaultdict(lambda: [0, 0])
    for g, p, *_ in rows:
        per[g][1] += 1
        if g == p:
            per[g][0] += 1
    print("\n分题材：")
    for th, (c, t) in sorted(per.items(), key=lambda x: -x[1][1]):
        bar = "█" * int(c / t * 10) if t else ""
        print(f"  {th:<6s} {c:>3}/{t:<3} {c / t:>5.0%} {bar}")

    wrong = [(g, p, ti, a, o) for g, p, ti, a, o in rows if g != p]
    print(f"\n错例 {len(wrong)} 条" + ("：" if verbose else "（-v 查看明细）"))
    if verbose:
        for g, p, ti, a, o in wrong:
            print(f"  {a}《{ti}》 金标={g} 预测={p}  旧标签={','.join(o)}")
    conf = collections.Counter((g, p) for g, p, *_ in wrong)
    print("\n主要混淆对：")
    for (g, p), c in conf.most_common(8):
        print(f"  应为 {g} → 误判 {p}: {c} 次")
    return ok, n


if __name__ == "__main__":
    run(verbose="-v" in sys.argv)
