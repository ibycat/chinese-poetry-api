# -*- coding: utf-8 -*-
"""旧标签迁移表：把 239 个自由词映射到受控词表。

旧标签的问题（实测）
  · 维度混淆：体裁(五言律诗)、题材(边塞)、情感(思乡)、学段(一年级下册)混在同一数组
  · 同义重复：咏物/咏物诗、写山/描写山、春天/春、离别/惜别/送别
  · 教材标签占三分之一：一年级上册……九年级下册、小学古诗、初中古诗、高中古诗
  · 粒度失控：黄河/洞庭湖/黄鹤楼 这类单一地名与"边塞"平级

迁移策略
  · 直接类名给 STRONG，近义给 MED，泛化词给 WEAK（因为"写景"几乎无区分度）
  · 学段、地名不进入任何维度，另存 meta.textbook / meta.place，保留原信息不丢失
  · 体裁由结构判定，旧标签只作为"乐府"这类无法结构判定的补充
"""

STRONG, MED, WEAK = 1.0, 0.7, 0.45

_MAP = {
    # ---------------- 体裁（仅保留结构无法判定的部分）
    "form_keep": {
        "乐府": "乐府", "新乐府辞": "乐府", "清商曲辞": "乐府",
        "近代曲辞": "乐府", "杂曲歌辞": "乐府", "相和歌辞": "乐府",
        "鼓吹曲辞": "乐府", "古体": "五言古诗", "长诗": "七言古诗",
    },

    # ---------------- 题材
    "山水田园": {
        "山水": STRONG, "田园": STRONG, "写景": WEAK, "描写水": MED, "写水": MED,
        "描写山": MED, "写山": MED, "写花": MED, "花": MED, "荷花": MED,
        "梅花": MED, "写鸟": MED, "鸟": MED, "写雨": MED, "雨": MED,
        "写雪": MED, "雪": MED, "写风": MED, "风": MED, "写草": MED,
        "写树": MED, "植物": MED, "水": MED, "山": MED, "写塔": WEAK,
        "记游": MED, "纪游": MED, "游历": MED, "景点": WEAK,
        "即景抒情": MED, "名楼、庙宇": WEAK, "写马": WEAK, "马": WEAK,
    },
    "边塞征战": {
        "边塞": STRONG, "战争": STRONG, "将士": MED, "将军": MED, "征人": MED,
        "戍边": STRONG, "狩猎": MED, "游侠": MED, "豪侠": MED, "爱国": WEAK,
        "忧国忧民": WEAK,
    },
    "咏史怀古": {
        "怀古": STRONG, "咏史": STRONG, "咏史怀古": STRONG,
        "吊古伤今": STRONG, "凭吊古迹": STRONG, "托古讽今": MED, "典故": WEAK,
    },
    "咏物寄兴": {
        "咏物": STRONG, "咏物诗": STRONG, "托物寄情": STRONG, "题咏": MED,
    },
    "送别赠答": {
        "送别": STRONG, "离别": STRONG, "惜别": MED, "赠别": STRONG,
        "酬赠": MED, "酬答": MED, "酬和": MED, "唱和": MED, "和诗": MED,
        "友情": MED, "友人": MED, "慰勉": MED, "规劝": MED, "待客": MED,
        "邀请": MED, "访友": MED, "寻访": MED, "相聚": MED, "依恋": WEAK,
        "怀念": WEAK, "怀人": MED,
    },
    "羁旅思乡": {
        "思乡": STRONG, "思归": STRONG, "羁旅": STRONG, "家乡": MED,
        "思亲": MED, "旅途": MED, "天气": WEAK,
    },
    "闺怨宫怨": {
        "闺怨": STRONG, "宫怨": STRONG, "怨情": MED, "哀怨": MED,
        "女子": MED, "妇女": MED, "美人": MED,
    },
    "爱情相思": {
        "爱情": STRONG, "相思": STRONG, "新婚": STRONG, "母爱": MED,
    },
    "悼亡伤逝": {"悼亡": STRONG},
    "讽喻刺世": {
        "讽刺": STRONG, "讽喻": STRONG, "愤懑": MED, "怀才不遇": MED,
        "贬谪": MED, "迁谪": MED, "仕途": WEAK, "失意": WEAK, "寓言": MED,
    },
    "民生疾苦": {"同情": MED, "忧国忧民": MED},
    "哲理悟道": {
        "哲理": STRONG, "人生": MED, "议论": WEAK, "态度": WEAK,
        "人格": WEAK, "坚贞": WEAK, "寓言": WEAK,
    },
    "闲适隐逸": {
        "归隐": STRONG, "隐逸": STRONG, "隐士": MED, "饮酒": MED,
        "宴饮": MED, "宴会": MED, "生活": WEAK, "岁月": WEAK, "惜时": MED,
    },
    "节令风俗": {
        "中秋": STRONG, "中秋节": STRONG, "重阳节": STRONG,
        "寒食节": STRONG, "春节": STRONG, "清明": STRONG,
    },
    "题画咏艺": {"音乐": STRONG, "题画": STRONG},
    "游仙梦幻": {"游仙": STRONG, "神话": MED, "记梦": STRONG},
}

# ---------------- 风格
_STYLE = {
    "豪放": {"豪放": STRONG, "豪迈": STRONG, "豪侠": MED},
    "婉约": {"婉约": STRONG},
    "悲慨": {"忧国忧民": MED, "吊古伤今": MED, "爱国": MED, "愤懑": WEAK},
    "旷达": {"旷达": STRONG},  # 预留
}

# ---------------- 情绪
_MOOD = {
    "欢愉": {"喜悦": STRONG, "赞美": WEAK, "赞颂": WEAK, "赞扬": WEAK, "热爱": MED},
    "恬淡": {"生活": WEAK},
    "孤寂": {"孤独": STRONG},
    "愁怨": {"伤怀": MED, "感伤": MED, "愁思": MED, "忧思": MED, "怨情": WEAK},
    "悲怆": {"悼亡": MED, "伤老": MED, "年老": WEAK},
    "慷慨": {"励志": MED, "抱负": MED, "抒志": MED},
    "惆怅": {"感慨": MED, "感叹": MED, "咏叹": MED, "叹息": MED,
             "怅惘": STRONG, "追忆": MED, "感时": MED, "失意": MED},
    "相思": {"思念": MED, "相思": MED, "怀人": MED, "怀念": MED, "思亲": MED},
}

# ---------------- 时令场景
_SCENE = {
    "春": {"春天": STRONG, "春": STRONG, "惜春": MED},
    "夏": {"夏天": STRONG, "夏": STRONG},
    "秋": {"秋天": STRONG, "秋": STRONG, "秋雨": MED},
    "冬": {"冬天": STRONG, "冬": STRONG},
    "元宵": {"元宵": STRONG}, "清明": {"寒食节": STRONG, "清明": STRONG},
    "端午": {"端午": STRONG}, "七夕": {"七夕": STRONG},
    "中秋": {"中秋": STRONG, "中秋节": STRONG},
    "重阳": {"重阳节": STRONG, "登高": MED},
    "除夕": {"春节": STRONG},
    "夜": {"月夜": STRONG, "月下": STRONG}, "月": {"月亮": STRONG, "月夜": MED, "月下": MED},
    "雨": {"写雨": MED, "雨": MED, "风雨": MED, "秋雨": MED},
    "雪": {"写雪": MED, "雪": MED},
    "江": {"黄河": MED, "长江": MED, "洞庭湖": MED, "渡江": MED, "写水": WEAK},
    "山": {"山": MED, "写山": WEAK, "终南山": MED, "黄山": MED, "峨眉山": MED,
           "泰山": MED, "庐山": MED},
    "楼": {"黄鹤楼": MED, "岳阳楼": MED, "登楼": MED, "名楼、庙宇": MED},
    "酒": {"饮酒": MED, "宴饮": MED},
}

# ---------------- 无维度价值 / 另存 meta
TEXTBOOK_PREFIX = ("一年级", "二年级", "三年级", "四年级", "五年级", "六年级",
                   "七年级", "八年级", "九年级", "小学古诗", "初中古诗", "高中古诗")
PLACE_TAGS = {"带有地名", "地名", "黄河", "长江", "洞庭湖", "终南山", "黄山",
              "峨眉山", "泰山", "庐山", "黄鹤楼", "岳阳楼"}
NOISE = {"唐诗三百首", "宋词三百首", "抒情", "写人", "叙事", "赞美", "赞颂",
         "赞扬", "咏叹", "感慨", "感叹", "叹息", "组诗", "民歌", "拟古",
         "援引", "考试", "态度", "景中情", "抒怀", "抒志", "寓人", "修辞",
         "描写手法", "表现手法", "地点"}


def _build():
    table = {}
    for theme, items in _MAP.items():
        if theme == "form_keep":
            continue
        for tag, w in items.items():
            table.setdefault(tag, []).append(("theme", theme, w))
    for style, items in _STYLE.items():
        for tag, w in items.items():
            table.setdefault(tag, []).append(("style", style, w))
    for mood, items in _MOOD.items():
        for tag, w in items.items():
            table.setdefault(tag, []).append(("mood", mood, w))
    for scene, items in _SCENE.items():
        for tag, w in items.items():
            table.setdefault(tag, []).append(("scene", scene, w))
    return table


LEGACY = _build()
FORM_KEEP = _MAP["form_keep"]


def prior(old_tags):
    """旧标签数组 -> 维度先验列表（去重取最大权重）。"""
    best = {}
    for t in old_tags or []:
        for dim, tag, w in LEGACY.get(t, []):
            k = (dim, tag)
            best[k] = max(best.get(k, 0.0), w * 1.9)  # 人工标注是权威信号，权重高于关键词猜测
    return [(d, t, w) for (d, t), w in best.items()]


def meta(old_tags):
    old_tags = old_tags or []
    tb = [t for t in old_tags if t.startswith(TEXTBOOK_PREFIX)]
    place = [t for t in old_tags if t in PLACE_TAGS]
    return {"textbook": tb, "place": place}


def form_override(old_tags):
    for t in old_tags or []:
        if t in FORM_KEEP:
            return FORM_KEEP[t]
    return None
