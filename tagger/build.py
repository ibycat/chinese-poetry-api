# -*- coding: utf-8 -*-
"""构建脚本：原始诗词 -> 简体语料 + 五维标签 + v1 分类索引 + 前端数据包。

用法
    python -m tagger.build                 # 构建全部已登记数据集
    python -m tagger.build --only tang300  # 只构建某一个
    python -m tagger.build --sample 80     # 每个二级分类进入浏览页的条数上限

输出
    data/poems.jsonl    全量简体语料，每行一首（API 的唯一数据源）
    data/index.json     分类 / 词表 / 统计
    tags/<key>.json     三百首选集的标签层，与源数组严格一一对应（同下标同诗）
    web/data.js         浏览页数据包（按分类分层抽样，控制体积）
"""

import json
import os
import argparse
import collections
import hashlib
import random

from . import engine, legacy, taxonomy, simp, classify
from .themes21 import to21, DIRECT, TO19, THEMES
from .categories import CATS, SLUG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATASETS = [
    {"key": "tang300", "kind": "file", "path": "raw/tang300.json",
     "name": "唐诗三百首", "section": "唐诗", "dynasty": "唐", "tags_layer": True},
    {"key": "song300", "kind": "file", "path": "raw/song300.json",
     "name": "宋词三百首", "section": "宋词", "dynasty": "宋", "tags_layer": True},
    {"key": "tang", "kind": "dir", "path": "raw/full/tang",
     "name": "全唐诗", "section": "唐诗", "dynasty": "唐"},
    {"key": "ci", "kind": "dir", "path": "raw/full/ci",
     "name": "全宋词", "section": "宋词", "dynasty": "宋"},
]

DIMS = ["theme", "style", "mood", "scene"]

# 经人工基准与全量高风险类抽查验证，这些规则弱类使用模型语义标签。
LLM_OVERRIDE_THEMES = {
    "寄赠酬答", "节令风俗", "闲适隐逸", "应制干谒",
    "佛道禅颂", "悼亡挽歌", "咏怀抒感",
}
LOW_CONF_REVIEW = 0.35


def load_labels(filename):
    path = os.path.join(ROOT, "data", filename)
    labels = {}
    if not os.path.exists(path):
        return labels
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
            if row.get("theme") in THEMES:
                labels[row["uid"]] = row
        except Exception:
            pass
    return labels


def choose_theme(uid, rule_theme, rule_conf, initial, reviews):
    first = initial.get(uid)
    if first and rule_theme in LLM_OVERRIDE_THEMES:
        return first["theme"], first.get("conf") or 0, "llm"
    reviewed = reviews.get(uid)
    if (rule_conf < LOW_CONF_REVIEW and first and reviewed
            and first["theme"] != rule_theme):
        return reviewed["theme"], reviewed.get("conf") or 0, "llm_review"
    return rule_theme, rule_conf, "rules"


def load_dataset(ds):
    p = os.path.join(ROOT, ds["path"])
    if ds["kind"] == "file":
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    out = []
    for name in sorted(os.listdir(p)):
        if name.endswith(".json"):
            with open(os.path.join(p, name), encoding="utf-8") as f:
                out += json.load(f)
    return out


def dump(obj, path, indent=1):
    p = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent,
                  separators=(",", ": ") if indent else (",", ":"))
    return p


def make_uid(poem, section):
    if poem.get("id"):
        return poem["id"]
    key = "|".join([section, poem.get("author", ""),
                    poem.get("title") or poem.get("rhythmic") or "",
                    (poem.get("paragraphs") or [""])[0]])
    return "cp" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:14]


def process(ds, raw, seen, sample_pool, initial=None, reviews=None):
    """逐首：转简 -> 五维打标 -> 语义复核 -> v1 分类匹配 -> 首句去重。"""
    initial = initial or {}
    reviews = reviews or {}
    recs, layer = [], []
    kept = dup = 0
    for poem in raw:
        title = simp.to_simp(poem.get("title") or poem.get("rhythmic") or "")
        author = simp.to_simp(poem.get("author") or "")
        paras = [simp.to_simp(x) for x in (poem.get("paragraphs") or [])]
        if not paras:
            continue
        dk = engine.norm(paras[0])[:24]
        # 需要与源数组一一对应的数据集（tags/）不做去重，否则下标错位；
        # 源数据本身含重复（如《清平调》既在乐府又在近体），重复项在全量层再去。
        if not ds.get("tags_layer"):
            if dk and dk in seen:
                dup += 1
                continue
        if dk:
            seen.add(dk)

        sp = {"title": title, "author": author, "paragraphs": paras,
              "rhythmic": poem.get("rhythmic")}
        old = poem.get("tags") or []
        rec = engine.tag_poem(sp, legacy.prior(old), legacy.form_override(old))
        tg = rec["tags"]

        scene = [t for t in [tg["scene"]["primary"]] + tg["scene"]["secondary"] if t]
        uid = make_uid(poem, ds["section"])
        rule_theme = to21(tg["theme"]["primary"], engine.norm(title),
                          engine.norm("".join(paras)))
        rule_conf = tg["theme"].get("conf") or 0.0
        theme21, theme_conf, theme_source = choose_theme(
            uid, rule_theme, rule_conf, initial, reviews)
        cats = classify.classify(
            engine.norm("".join(paras)), engine.norm(title),
            {"theme": [TO19.get(theme21, theme21)] + tg["theme"]["secondary"],
             "mood": [tg["mood"]["primary"]] + tg["mood"]["secondary"],
             "scene": scene})

        # 对外统一用 21 题材；只有验证过的弱类和低置信分歧使用模型标签。
        item = {
            "uid": uid, "title": title, "author": author,
            "dynasty": ds["dynasty"], "section": ds["section"], "form": rec["form"],
            "paragraphs": paras,
            "theme": theme21,
            "theme2": [DIRECT.get(x, x) for x in tg["theme"]["secondary"]],
            "style": tg["style"]["primary"], "style2": tg["style"]["secondary"],
            "mood": tg["mood"]["primary"], "mood2": tg["mood"]["secondary"],
            "scene": scene, "cats": cats,
            "theme_conf": round(theme_conf, 3),
        }
        recs.append(item)

        if ds.get("tags_layer"):
            note = legacy.form_override(old)
            sources = {d: tg[d].get("source") for d in DIMS if tg.get(d)}
            sources["theme"] = theme_source
            layer.append({
                "uid": item["uid"], "title": title, "author": author,
                "form": rec["form"],
                "form_note": note if note and note != rec["form"] else None,
                "theme": item["theme"], "theme_2": item["theme2"],
                "style": item["style"], "style_2": item["style2"],
                "mood": item["mood"], "mood_2": item["mood2"],
                "scene": scene, "cats": cats,
                "source": sources,
                "conf": {d: tg[d].get("conf") for d in DIMS if tg.get(d)},
                "evidence": rec["evidence"].get("theme", []),
                "legacy": old, "dataset": ds["name"],
            })

        cap = sample_pool[None]
        for subs in cats.values():
            for s in subs:
                bucket = sample_pool[s]
                if len(bucket) < cap:
                    bucket.append(item)
        kept += 1
    return recs, layer, kept, dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--sample", type=int, default=60,
                    help="每个二级分类进入浏览页的条数上限")
    ap.add_argument("--no-llm", action="store_true", help="只使用规则，便于回归对照")
    args = ap.parse_args()

    initial = {} if args.no_llm else load_labels("theme_labels.jsonl")
    reviews = {} if args.no_llm else load_labels("theme_labels_review.jsonl")
    if initial:
        print(f"载入模型初判 {len(initial)} 条、分歧终审 {len(reviews)} 条", flush=True)

    datasets = [d for d in DATASETS if not args.only or d["key"] == args.only]
    seen = set()
    sample_pool = collections.defaultdict(list, {None: args.sample})
    all_recs, report = [], []

    for ds in datasets:
        try:
            raw = load_dataset(ds)
        except FileNotFoundError:
            print(f"[{ds['key']}] 找不到 {ds['path']}，跳过")
            continue
        recs, layer, kept, dup = process(ds, raw, seen, sample_pool, initial, reviews)
        all_recs += recs
        report.append((ds["key"], ds["name"], len(raw), kept, dup))
        if layer:
            dump(layer, f"tags/{ds['key']}.json")
        print(f"[{ds['key']}] 读入 {len(raw)}，保留 {kept}，去重 {dup}", flush=True)

    # tags/ 必须与源数组严格同下标，因此重复诗仍保存在标签层；API 语料则按 UID
    # 去重，避免同一 id 出现两次导致客户端缓存、随机去重和引用出错。
    unique = {}
    for rec in all_recs:
        unique.setdefault(rec["uid"], rec)
    api_dups = len(all_recs) - len(unique)
    all_recs = list(unique.values())
    if api_dups:
        print(f"API 语料按 UID 去重 {api_dups} 首", flush=True)

    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "poems.jsonl"), "w", encoding="utf-8") as f:
        for r in all_recs:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    top_counts = collections.Counter()
    sub_counts = collections.Counter()
    for r in all_recs:
        for top, subs in r["cats"].items():
            top_counts[top] += 1
            for s in subs:
                sub_counts[f"{top}/{s}"] += 1

    cat_index = {}
    for top, cfg in CATS.items():
        cat_index[top] = {"slug": SLUG[top], "desc": cfg["desc"],
                          "count": top_counts.get(top, 0), "subs": {}}
        for sub in cfg["subs"]:
            cat_index[top]["subs"][sub] = {
                "slug": SLUG[sub], "count": sub_counts.get(f"{top}/{sub}", 0)}

    stats = {
        "datasets": [{"key": k, "name": n, "read": rd, "kept": kp, "dup": dp}
                     for k, n, rd, kp, dp in report],
        "total": len(all_recs),
        "api_dedup": api_dups,
        "simplified_by": simp.BACKEND,
    }
    dump({"source": "ibycat/chinese-poetry",
          "taxonomy": {d: {"name": taxonomy.DIMENSIONS[d]["name"],
                           "desc": taxonomy.DIMENSIONS[d]["desc"],
                           "tags": taxonomy.DIMENSIONS[d]["tags"]}
                       for d in ["theme", "style", "mood", "scene", "form"]},
          "categories": cat_index, "stats": stats}, "data/index.json")

    pick = {}
    for sub, bucket in sample_pool.items():
        if sub is None:
            continue
        for it in bucket:
            pick[it["uid"]] = it
    poems = list(pick.values())
    random.Random(7).shuffle(poems)
    payload = {"taxonomy": {d: {"name": taxonomy.DIMENSIONS[d]["name"],
                                "desc": taxonomy.DIMENSIONS[d]["desc"],
                                "tags": taxonomy.DIMENSIONS[d]["tags"]}
                            for d in ["theme", "style", "mood", "scene", "form"]},
               "categories": cat_index, "stats": stats, "poems": poems}
    p = os.path.join(ROOT, "web", "data.js")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("window.CP_DATA = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    zero = [f"{k}/{s}" for k, v in cat_index.items()
            for s, sv in v["subs"].items() if sv["count"] == 0]
    print(f"全量 {len(all_recs)} 首 -> data/poems.jsonl；浏览页抽样 {len(poems)} 首")
    print(f"分类 {len(CATS)} 一级 / {sum(len(v['subs']) for v in CATS.values())} 二级；"
          f"空分类 {len(zero)} 个 {zero[:6]}")


if __name__ == "__main__":
    main()
