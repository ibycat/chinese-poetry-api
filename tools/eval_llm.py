# -*- coding: utf-8 -*-
"""对比 LLM 标注与人工金标准，并与关键词引擎同口径比较。

旧标签（19 类）到新类目（21 类）存在一对多，例如"送别赠答"在新体系里拆成
"送别"与"寄赠酬答"，因此按"可接受集合"判对错，不强行单选。
"""

import json
import os
import sys
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from tagger import engine, legacy, simp  # noqa: E402
import eval_theme as ev  # noqa: E402

# 旧体系标签 -> 新体系可接受集合（按具体标签细分，而非按旧主题名）
OLD2NEW = {
    "山水": {"山水田园"}, "田园": {"山水田园"},
    "边塞": {"边塞征战"}, "战争": {"边塞征战"}, "将士": {"边塞征战"},
    "戍边": {"边塞征战"}, "征人": {"边塞征战"}, "出塞": {"边塞征战"},
    "怀古": {"咏史怀古"}, "咏史": {"咏史怀古"}, "咏史怀古": {"咏史怀古"},
    "吊古伤今": {"咏史怀古"}, "凭吊古迹": {"咏史怀古"},
    "咏物": {"咏物"}, "咏物诗": {"咏物"}, "托物寄情": {"咏物"},
    "送别": {"送别"}, "离别": {"送别"}, "赠别": {"送别"}, "惜别": {"送别"},
    "友情": {"寄赠酬答"}, "友人": {"寄赠酬答"}, "酬赠": {"寄赠酬答"},
    "酬答": {"寄赠酬答"},
    "闺怨": {"闺情宫怨"}, "宫怨": {"闺情宫怨"}, "怨情": {"闺情宫怨"},
    "悼亡": {"悼亡挽歌"},
    "爱情": {"爱情相思"}, "新婚": {"爱情相思"},
    "思乡": {"羁旅思乡"}, "思归": {"羁旅思乡"}, "羁旅": {"羁旅思乡"},
    "归隐": {"闲适隐逸"}, "隐逸": {"闲适隐逸"},
    "哲理": {"哲理悟道", "佛道禅颂"},
    "讽刺": {"讽喻刺世"}, "讽喻": {"讽喻刺世"},
    "同情": {"民生疾苦"}, "忧国忧民": {"民生疾苦"},
    "音乐": {"题画咏艺"}, "题画": {"题画咏艺"},
    "游仙": {"游仙梦幻", "佛道禅颂"},
}


def gold_sets(raw):
    out = {}
    for r in raw:
        s = set()
        for t in r.get("tags") or []:
            s |= OLD2NEW.get(t, set())
        if s:
            out[(simp.to_simp(r.get("author") or ""),
                 simp.to_simp(r.get("title") or r.get("rhythmic") or ""))] = s
    return out


def main():
    raw = []
    for k in ("tang300", "song300"):
        raw += json.load(open(os.path.join(ROOT, "raw", f"{k}.json"), encoding="utf-8"))
    gold = gold_sets(raw)

    llm = {}
    for line in open(os.path.join(ROOT, "data", "llm_theme.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        if r.get("theme"):
            llm[r["uid"]] = r["theme"]

    poems = [json.loads(x) for x in open(os.path.join(ROOT, "data", "poems.jsonl"),
                                        encoding="utf-8")]
    # 关键词引擎的旧主题 -> 新体系可接受集合
    ENGINE_MAP = {
        "山水田园": {"山水田园"}, "边塞征战": {"边塞征战"}, "咏史怀古": {"咏史怀古"},
        "咏物寄兴": {"咏物"}, "送别赠答": {"送别", "寄赠酬答"},
        "闺怨宫怨": {"闺情宫怨"}, "爱情相思": {"爱情相思"}, "悼亡伤逝": {"悼亡挽歌"},
        "羁旅思乡": {"羁旅思乡"}, "闲适隐逸": {"闲适隐逸"},
        "哲理悟道": {"哲理悟道", "佛道禅颂"}, "讽喻刺世": {"讽喻刺世"},
        "民生疾苦": {"民生疾苦"}, "题画咏艺": {"题画咏艺"},
        "游仙梦幻": {"游仙梦幻", "佛道禅颂"}, "亲情": {"亲情"},
        "节令风俗": {"节令风俗"}, "咏怀抒感": {"咏怀抒感"}, "应制酬和": {"应制干谒"},
    }

    n = ok_llm = ok_eng = 0
    conf = collections.Counter()
    for p in poems:
        g = gold.get((p["author"], p["title"]))
        if not g:
            continue
        lab = llm.get(p["uid"])
        if not lab:            # 没被标注过的诗不计入分母
            continue
        n += 1
        if lab and lab in g:
            ok_llm += 1
        elif lab:
            conf[(tuple(sorted(g)), lab)] += 1
        eng = ENGINE_MAP.get(p["theme"], set())
        if eng & g:
            ok_eng += 1

    print(f"样本 {n} 首（人工标签可映射且已获 LLM 标注）")
    print(f"  LLM       {ok_llm}/{n} = {ok_llm / n:.1%}")
    print(f"  关键词引擎 {ok_eng}/{n} = {ok_eng / n:.1%}")
    print("\nLLM 主要误判：")
    for (g, lab), c in conf.most_common(10):
        print(f"  应为 {'/'.join(g)} → 判为 {lab}: {c} 次")


if __name__ == "__main__":
    main()
