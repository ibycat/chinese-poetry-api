# -*- coding: utf-8 -*-
"""把诗词匹配到 jinrishici v1 的 69 个二级分类。

性能：把全部分类关键词按词长分桶，正文/标题各扫一遍，再对 69 个分类求分，
避免为每个分类单独搜索。全量 7.8 万首实测约 12 秒。
"""

import math

from .categories import CATS, TITLE_X

_bucket = {}
for _top, _cfg in CATS.items():
    for _sub, _rule in _cfg["subs"].items():
        for _kw in _rule.get("kw", {}):
            _bucket.setdefault(len(_kw), {})[_kw] = True
_LENS = sorted(_bucket)


def _scan(text):
    hits = {}
    n = len(text)
    for L in _LENS:
        table = _bucket[L]
        for i in range(n - L + 1):
            seg = text[i:i + L]
            if seg in table:
                hits[seg] = hits.get(seg, 0) + 1
    return hits


def _score(rule, bhits, thits, tags):
    score = 0.0
    tr = rule.get("title_required", False)
    for kw, w in rule.get("kw", {}).items():
        if kw in thits:
            score += w * TITLE_X
        if not tr and kw in bhits:
            score += w * (1.0 + 0.15 * math.log(bhits[kw]))
    w = rule.get("theme_w", 1.0)
    if any(t in tags["theme"] for t in rule.get("theme", [])):
        score += w
    if any(t in tags["mood"] for t in rule.get("mood", [])):
        score += w
    if any(t in tags["scene"] for t in rule.get("scene", [])):
        score += w
    return score


def classify(body, title, tags):
    """返回命中集合 {一级: [二级...]}，只含有命中的分类。"""
    bh, th = _scan(body), _scan(title)
    out = {}
    for top, cfg in CATS.items():
        hit = []
        for sub, rule in cfg["subs"].items():
            if _score(rule, bh, th, tags) >= rule.get("min", 1.0):
                hit.append(sub)
        if hit:
            out[top] = hit
    return out
