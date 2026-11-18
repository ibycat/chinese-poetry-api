# -*- coding: utf-8 -*-
"""打标引擎：零第三方依赖，可解释、可复跑。

流程
  1. 归一化：繁->简（内置单字映射表）、去标点、只留汉字
  2. 证据匹配：按词长分桶的词表逐位扫描，累计 维度/标签 得分
  3. 长度归一：长诗命中词多，按字数做对数衰减，避免"排律通吃"
  4. 先验融合：旧标签迁移先验 + 词牌风格先验，与文本证据相加
  5. 决策：每维度取最高分为主标签，>=55% 主分的入副标签
  6. 体裁：由句长/句数确定性判定，不参与打分
"""

import json
import math
import os
import hashlib
import unicodedata

from . import taxonomy
from .lexicon import LEX, TITLE_BOOST

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "t2s.json"), encoding="utf-8") as _f:
    T2S = json.load(_f)

# 按词长分桶，扫描时逐长度查集合
_BUCKET = {}
for _kw in LEX:
    _BUCKET.setdefault(len(_kw), {})[_kw] = LEX[_kw]
_LENS = sorted(_BUCKET)

# 词牌风格先验：词牌本身的声情倾向（0.5 弱，1.2 强）
RHYTHMIC_PRIOR = {
    "满江红": [("style", "豪放", 1.2), ("mood", "慷慨", 0.9)],
    "念奴娇": [("style", "豪放", 1.0), ("mood", "慷慨", 0.7)],
    "水调歌头": [("style", "豪放", 0.7), ("mood", "旷达", 0.8)],
    "沁园春": [("style", "豪放", 1.0)],
    "破阵子": [("style", "豪放", 0.9), ("theme", "边塞征战", 0.6)],
    "渔家傲": [("style", "豪放", 0.7), ("theme", "边塞征战", 0.6)],
    "江城子": [("style", "豪放", 0.6)],
    "六州歌头": [("style", "悲慨", 1.0)],
    "贺新郎": [("style", "悲慨", 0.9)],
    "蝶恋花": [("style", "婉约", 0.9)],
    "临江仙": [("style", "婉约", 0.6)],
    "浣溪沙": [("style", "婉约", 0.7), ("style", "清新", 0.5)],
    "一剪梅": [("style", "婉约", 1.0), ("mood", "相思", 0.8)],
    "雨霖铃": [("style", "婉约", 1.1), ("mood", "愁怨", 0.9)],
    "鹧鸪天": [("style", "婉约", 0.6)],
    "青玉案": [("style", "婉约", 0.6)],
    "鹊桥仙": [("scene", "七夕", 0.9), ("style", "婉约", 0.6)],
    "踏莎行": [("style", "婉约", 0.6)],
    "点绛唇": [("style", "婉约", 0.7)],
    "生查子": [("style", "婉约", 0.6)],
    "卜算子": [("style", "婉约", 0.6)],
    "如梦令": [("style", "清新", 0.7)],
    "醉花阴": [("style", "婉约", 0.8)],
    "声声慢": [("style", "沉郁", 1.1), ("mood", "愁怨", 1.0)],
    "扬州慢": [("style", "悲慨", 1.0), ("theme", "咏史怀古", 0.8)],
    "兰陵王": [("style", "沉郁", 0.9)],
    "摸鱼儿": [("style", "悲慨", 0.9)],
    "水龙吟": [("style", "豪放", 0.7)],
    "永遇乐": [("style", "悲慨", 0.9)],
    "齐天乐": [("style", "沉郁", 0.7)],
    "桂枝香": [("theme", "咏史怀古", 1.0), ("style", "悲慨", 0.8)],
}

DIM_ORDER = ["theme", "style", "mood", "scene"]
MIN_SCORE = {"theme": 1.0, "style": 0.9, "mood": 0.8, "scene": 0.8}
SEC_RATIO = 0.55
MAX_SEC = {"theme": 3, "style": 2, "mood": 2, "scene": 5}

# 风格/情绪的兜底推导：题材本身携带稳定的体性倾向，比留空更有用。
# source 会标为 derived，便于区分"文本证据"与"体性推导"。
DERIVE_STYLE = {
    "边塞征战": ("豪放", 0.34), "咏史怀古": ("悲慨", 0.30),
    "山水田园": ("冲淡", 0.30), "闺怨宫怨": ("婉约", 0.34),
    "爱情相思": ("婉约", 0.32), "民生疾苦": ("沉郁", 0.30),
    "闲适隐逸": ("冲淡", 0.30), "咏物寄兴": ("清新", 0.26),
    "送别赠答": ("婉约", 0.26), "游仙梦幻": ("绮丽", 0.30),
    "题画咏艺": ("绮丽", 0.26), "悼亡伤逝": ("沉郁", 0.34),
    "节令风俗": ("清新", 0.26), "羁旅思乡": ("沉郁", 0.30),
    "哲理悟道": ("冲淡", 0.30), "讽喻刺世": ("悲慨", 0.30),
    "咏怀抒感": ("沉郁", 0.28), "亲情": ("冲淡", 0.28), "应制酬和": ("绮丽", 0.30),
}
DERIVE_MOOD = {
    "悼亡伤逝": "悲怆", "送别赠答": "惆怅", "闺怨宫怨": "愁怨",
    "羁旅思乡": "相思", "边塞征战": "慷慨", "山水田园": "恬淡",
    "闲适隐逸": "恬淡", "咏史怀古": "惆怅", "民生疾苦": "悲怆",
    "爱情相思": "相思", "游仙梦幻": "欢愉", "节令风俗": "欢愉",
    "哲理悟道": "恬淡", "咏物寄兴": "孤寂", "讽喻刺世": "愁怨",
    "题画咏艺": "欢愉", "咏怀抒感": "惆怅", "亲情": "相思", "应制酬和": "欢愉",
}


def is_cjk(ch):
    return "\u3400" <= ch <= "\u9fff"


def norm(text):
    """繁->简、去标点、只留汉字。"""
    out = []
    for ch in text:
        ch = T2S.get(ch, ch)
        if is_cjk(ch):
            out.append(ch)
    return "".join(out)


def cjk_len(text):
    return sum(1 for ch in text if is_cjk(ch))


def scan(text):
    """返回 {kw: count}，按词长分桶后逐位匹配。"""
    hits = {}
    n = len(text)
    for L in _LENS:
        table = _BUCKET[L]
        for i in range(n - L + 1):
            seg = text[i:i + L]
            if seg in table:
                hits[seg] = hits.get(seg, 0) + 1
    return hits


def _accumulate(scores, evidence, hits, boost):
    for kw, cnt in hits.items():
        grow = 1.0 + 0.30 * math.log(cnt)  # 重复出现收益递减
        for dim, tag, w in LEX[kw]:
            key = (dim, tag)
            val = w * grow * boost
            scores[key] = scores.get(key, 0.0) + val
            if dim == "theme":
                evidence.setdefault(tag, []).append(kw)


def tag_poem(poem, legacy_prior=None, form_hint=None):
    """poem: dict(paragraphs/title/author/rhythmic)；返回结构化标签记录。"""
    raw_text = "".join(poem.get("paragraphs") or [])
    title = poem.get("title") or poem.get("rhythmic") or ""
    body = norm(raw_text)
    head = norm(title)

    scores, evidence = {}, {}
    _accumulate(scores, evidence, scan(body), 1.0)
    _accumulate(scores, evidence, scan(head), TITLE_BOOST)

    # 词的"标题"是词牌，只是曲调名。《水调歌头》会命中"水""山"、《临江仙》命中"仙"，
    # 若照常跑标题规则，2 万首宋词里近四成会被曲调名带偏。词牌对声情的影响由
    # RHYTHMIC_PRIOR 单独承担，因此词一律跳过标题规则。
    is_tune = bool(poem.get("rhythmic"))

    # 标题规则：题材指向远强于正文
    from .lexicon import (TITLE_RULES, TITLE_XIYONG_EXCLUDE, TITLE_VERB_PREFIX,
                         OBJECT_NOUNS, TITLE_WEIGHT)
    from .lexicon import TITLE_SONGBIE_EXCLUDE
    sb_blocked = any(w in head for w in TITLE_SONGBIE_EXCLUDE)
    for tag, words in ([] if is_tune else TITLE_RULES):
        if tag == "咏物寄兴":
            continue
        if tag == "送别赠答" and sb_blocked:
            continue
        for item in words:
            kw, w = item if isinstance(item, tuple) else (item, TITLE_WEIGHT.get(tag, 0.8))
            if kw in head:
                scores[("theme", tag)] = scores.get(("theme", tag), 0.0) + \
                    w * TITLE_WEIGHT.get(tag, 0.8)
                evidence.setdefault(tag, []).append("题:" + kw)
                break
    # 咏物需标题形制支持，且排除寺观楼壁一类"题壁诗"
    is_wuti = ("无题" in head) or ("無題" in head)
    if (not is_tune) and (not is_wuti) and any(w in head for w in ("咏", "赋得", "题")):
        if not any(w in head for w in TITLE_XIYONG_EXCLUDE) \
                and not any(head.startswith(v) for v in TITLE_VERB_PREFIX):
            scores[("theme", "咏物寄兴")] = scores.get(("theme", "咏物寄兴"), 0.0) + 1.1
            evidence.setdefault("咏物寄兴", []).append("题:咏物形制")
    elif (not is_tune) and len(head) <= 4 and any(head.endswith(o) for o in OBJECT_NOUNS):
        scores[("theme", "咏物寄兴")] = scores.get(("theme", "咏物寄兴"), 0.0) + 1.25
        evidence.setdefault("咏物寄兴", []).append("题:物名")

    # "无题"在标题形制上明确表示"不著事由"，事由类题材不应取胜
    if is_wuti:
        for t in ("送别赠答", "咏物寄兴"):
            if ("theme", t) in scores:
                scores[("theme", t)] *= 0.5
        scores[("theme", "爱情相思")] = scores.get(("theme", "爱情相思"), 0.0) + 0.9

    # 词牌先验
    rhy = poem.get("rhythmic")
    if rhy:
        for dim, tag, w in RHYTHMIC_PRIOR.get(rhy, []):
            scores[(dim, tag)] = scores.get((dim, tag), 0.0) + w

    # 旧标签先验
    for dim, tag, w in (legacy_prior or []):
        scores[(dim, tag)] = scores.get((dim, tag), 0.0) + w

    # 长度归一
    L = max(cjk_len(raw_text), 1)
    factor = 1.0 if L <= 60 else 1.0 + 0.45 * math.log10(L / 60.0)
    for k in scores:
        scores[k] /= factor

    rec = {
        "form": resolve_form(poem, form_hint),
        "tags": {},
        "scores": {},
        "evidence": {},
    }
    for dim in DIM_ORDER:
        items = sorted(
            [(t, s) for (d, t), s in scores.items() if d == dim and s >= MIN_SCORE[dim]],
            key=lambda x: -x[1],
        )
        if not items:
            rec["tags"][dim] = {"primary": None, "secondary": []}
            continue
        top_t, top_s = items[0]
        sec = [t for t, s in items[1:] if s >= top_s * SEC_RATIO][: MAX_SEC[dim]]
        total = sum(s for _, s in items)
        rec["tags"][dim] = {
            "primary": top_t,
            "secondary": sec,
            "conf": round(top_s / total, 3) if total else 0.0,
            "source": "text",
        }
        rec["scores"][dim] = {t: round(s, 2) for t, s in items[:6]}
        if dim == "theme":
            rec["evidence"][dim] = list(dict.fromkeys(evidence.get(top_t, [])))[:6]

    # 题材兜底：弱证据（>=0.5）先用，实在没有才归入"咏怀抒感"，不留空值
    if not rec["tags"].get("theme", {}).get("primary"):
        cands = sorted([(t, sc) for (d, t), sc in scores.items() if d == "theme"],
                       key=lambda x: -x[1])
        if cands and cands[0][1] >= 0.65:
            rec["tags"]["theme"] = {"primary": cands[0][0], "secondary": [],
                                    "conf": round(min(cands[0][1], 0.65), 3),
                                    "source": "weak"}
        else:
            rec["tags"]["theme"] = {"primary": "咏怀抒感", "secondary": [],
                                    "conf": 0.2, "source": "fallback"}

    # 题材确定后，风格/情绪缺位则按体性推导补位
    theme = rec["tags"].get("theme", {}).get("primary")
    if theme and not rec["tags"].get("style", {}).get("primary"):
        d = DERIVE_STYLE.get(theme)
        if d:
            rec["tags"]["style"] = {"primary": d[0], "secondary": [], "conf": d[1], "source": "derived"}
    if theme and not rec["tags"].get("mood", {}).get("primary"):
        d = DERIVE_MOOD.get(theme)
        if d:
            rec["tags"]["mood"] = {"primary": d, "secondary": [], "conf": 0.3, "source": "derived"}
    return rec


_SEPS = set("，。、；：？！,.;:?! 　\u3000（）()《》〈〉“”‘’…—·")


def split_sentences(paragraphs):
    """按句读切分。原始数据的一条 paragraph 往往是一整联（两句），
    直接按 paragraph 计长会把五律误判成杂言。"""
    out = []
    for p in paragraphs or []:
        buf = []
        for ch in p:
            if ch in _SEPS:
                if buf:
                    out.append("".join(buf))
                    buf = []
            else:
                buf.append(ch)
        if buf:
            out.append("".join(buf))
    return out


def line_lens(poem):
    return [cjk_len(s) for s in split_sentences(poem.get("paragraphs")) if cjk_len(s)]


def detect_form(poem):
    if poem.get("rhythmic"):
        return "词"
    lens = line_lens(poem)
    if not lens:
        return "未知"
    s = set(lens)
    n = len(lens)
    if s == {4}:
        return "四言"
    for size, unit in ((5, "五言"), (7, "七言")):
        if s == {size}:
            if n == 4:
                return unit + "绝句"
            if n == 8:
                return unit + "律诗"
            # 排律与古诗的界限在"通篇对仗"，仅凭句数无法判定（如五古十六句
            # 与五排十六句字数句数完全一致），故默认归古体，待旧标签或平仄数据升级。
            return unit + "古诗"
    return "杂言古诗"


def resolve_form(poem, form_hint=None):
    """结构判定优先；旧标签只在判为古体时补"乐府"这类无法由字数判定的分类。"""
    form = detect_form(poem)
    if form_hint and (form.endswith("古诗") or form == "杂言古诗"):
        return form_hint
    return form


def make_uid(poem, section):
    if poem.get("id"):
        return poem["id"]
    key = "|".join([
        section,
        poem.get("author", ""),
        poem.get("title") or poem.get("rhythmic") or "",
        (poem.get("paragraphs") or [""])[0],
    ])
    return "cp" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:14]
