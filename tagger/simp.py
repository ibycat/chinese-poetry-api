# -*- coding: utf-8 -*-
"""繁 -> 简。

构建期用 opencc（词级，能正确处理"頭髮/發財""乾隆/干"这类语境歧义），
运行时不需要 opencc：简体文本在构建时已固化进产物。

若环境没有 opencc，退回内置的 3751 字单字映射（引擎自带的 t2s.json），
精度略低但仍可用。
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "t2s.json"), encoding="utf-8") as f:
    _T2S = json.load(f)

_TRAD = frozenset(_T2S)

try:
    from opencc import OpenCC
    _cc = OpenCC("t2s")
    BACKEND = "opencc"
except Exception:  # pragma: no cover
    _cc = None
    BACKEND = "charmap"


def _charmap(s):
    return "".join(_T2S.get(ch, ch) for ch in s)


def has_trad(s):
    return any(ch in _TRAD for ch in s)


def to_simp(s):
    if not s or not has_trad(s):
        return s
    return _cc.convert(s) if _cc else _charmap(s)


def to_simp_list(lines):
    return [to_simp(x) for x in lines]
