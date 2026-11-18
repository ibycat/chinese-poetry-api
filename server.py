# -*- coding: utf-8 -*-
"""今日诗词 v1 兼容 API（本地参考实现，已弃用）。

警告：本文件只覆盖 v1 意象分类，不含 21 题材与新数据字段，与 cloudflare/src/index.js 已不同步。
生产与本地调试请统一使用 `cd cloudflare && npx wrangler dev`。

路由与 https://v1.jinrishici.com 完全一致，把域名换掉即可直接调用：

    GET /                            分类总表（同 v1 的 welcome JSON）
    GET /all                         全部分类随机一句
    GET /shanshui                    一级分类随机一句
    GET /shanshui/lushan             二级分类随机一句
    GET /shuqing/aiqing.json         JSON（默认）
    GET /shanshui.txt                纯文本
    GET /shanshui.svg                图片

扩展（v1 没有，不影响兼容）：
    ?n=5                             一次返回 5 条（数组）
    /抒情/爱情                        直接用中文名访问

返回体在 v1 的四个字段之外追加了 id / dynasty / paragraphs / tags / category_path，
v1 的解析器只读前四个字段，因此可直接替换。

零第三方依赖，只需 Python 3.8+。
"""

import argparse
import json
import os
import random
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data", "poems.jsonl")
INDEX = os.path.join(ROOT, "data", "index.json")

POEMS = []
BY_SUB = {}      # (top, sub) -> [idx]
BY_TOP = {}      # top -> [idx]
META = {}

FORMATS = ("json", "txt", "svg")


# ----------------------------------------------------------------- 载入
def load():
    global META
    if not os.path.exists(DATA):
        sys.exit("找不到 data/poems.jsonl，请先运行：python -m tagger.build")
    with open(INDEX, encoding="utf-8") as f:
        META = json.load(f)

    slug2sub, slug2top = {}, {}
    for top, cfg in META["categories"].items():
        slug2top[cfg["slug"]] = top
        for sub, sc in cfg["subs"].items():
            slug2sub[sc["slug"]] = (top, sub)

    with open(DATA, encoding="utf-8") as f:
        for line in f:
            POEMS.append(json.loads(line))

    for i, p in enumerate(POEMS):
        for top, subs in p["cats"].items():
            BY_TOP.setdefault(top, []).append(i)
            for s in subs:
                BY_SUB.setdefault((top, s), []).append(i)

    print(f"已载入 {len(POEMS)} 首；一级 {len(BY_TOP)}，二级 {len(BY_SUB)}")
    return slug2top, slug2sub


SLUG2TOP, SLUG2SUB = {}, {}


# ----------------------------------------------------------------- 响应
def pick_pool(top, sub):
    if sub:
        return BY_SUB.get((top, sub), [])
    return BY_TOP.get(top, [])


def all_pool():
    return range(len(POEMS))


def to_v1(p):
    """v1 的四个字段 + 追加字段。"""
    cats = p["cats"]
    top = next(iter(cats)) if cats else "其他"
    sub = random.choice(cats[top]) if cats.get(top) else None
    path = f"古诗文-{top}-{sub}" if sub else f"古诗文-{top}"
    return {
        "content": (p["paragraphs"] or [""])[0],
        "origin": p["title"],
        "author": p["author"],
        "category": path,
        # ---- 以下为扩展字段，v1 解析器不读 ----
        "id": p["uid"],
        "dynasty": p["dynasty"],
        "section": p["section"],
        "form": p["form"],
        "paragraphs": p["paragraphs"],
        "tags": {"theme": p["theme"], "style": p["style"], "mood": p["mood"],
                 "scene": p["scene"]},
        "categories": [f"{t}/{s}" for t, ss in cats.items() for s in ss],
    }


def to_v1_in(p, top, sub):
    d = to_v1(p)
    d["category"] = f"古诗文-{top}-{sub}" if sub else f"古诗文-{top}"
    return d


def catalogue(host):
    """同 v1 的 / 返回：welcome / api-document / help / list。"""
    items = [{"全部": f"//{host}/all"}]
    for top, cfg in META["categories"].items():
        items.append({top: f"//{host}/{cfg['slug']}"})
    for top, cfg in META["categories"].items():
        for sub, sc in cfg["subs"].items():
            items.append({f"{top}-{sub}": f"//{host}/{cfg['slug']}/{sc['slug']}"})
    return {
        "welcome": "欢迎使用诗词分类接口（兼容今日诗词 v1）",
        "api-document": "在链接后加 .json / .txt / .svg 得到不同格式；可加 ?n=5 取多条",
        "help": "数据源 chinese-poetry，全文已转简体；分类体系与 v1.jinrishici.com 一致",
        "count": len(POEMS),
        "list": items,
    }


def svg_of(text, author, title):
    lines = [text[i:i + 14] for i in range(0, len(text), 14)] or [""]
    h = 60 + 30 * len(lines)
    t = "".join(
        f'<text x="24" y="{56 + 30 * i}" font-size="20" fill="#26221e" '
        f'font-family="Songti SC,SimSun,serif">{_esc(l)}</text>'
        for i, l in enumerate(lines))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{h}" '
            f'viewBox="0 0 420 {h}"><rect width="420" height="{h}" fill="#f7f4ee"/>'
            f'{t}<text x="24" y="{h - 16}" font-size="12" fill="#8c8378">'
            f'{_esc(author)}《{_esc(title)}》</text></svg>')


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Handler(BaseHTTPRequestHandler):
    server_version = "cp-tags/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(u.path).strip("/")
        q = urllib.parse.parse_qs(u.query)

        fmt = "json"
        for f in FORMATS:
            if path.endswith("." + f):
                path, fmt = path[: -len(f) - 1], f
                break

        try:
            n = max(1, min(int(q.get("n", ["1"])[0]), 50))
        except ValueError:
            n = 1

        if path == "" or path == "index":
            return self._json(catalogue(self.headers.get("Host", "localhost")))

        top, sub = self._resolve(path)
        pool = self._pool(top, sub)

        if not pool:
            return self._json({"status": "error", "errCode": 2001,
                               "errMessage": "No matching category or empty pool"},
                              status=404)

        picks = random.sample(pool, min(n, len(pool)))
        items = [to_v1_in(POEMS[i], top or "全部", sub) for i in picks]
        one = items[0]

        if fmt == "txt":
            body = "\n".join(x["content"] for x in items).encode("utf-8")
            return self._send(body, "text/plain; charset=utf-8")
        if fmt == "svg":
            body = svg_of(one["content"], one["author"], one["origin"]).encode("utf-8")
            return self._send(body, "image/svg+xml; charset=utf-8")

        return self._json(items if n > 1 else one)

    # ------------------------------------------------------------ 内部
    def _resolve(self, path):
        if path == "all":
            return None, None
        parts = path.split("/")
        head = parts[0]
        # 支持 slug 与中文名
        top = SLUG2TOP.get(head) or (head if head in META["categories"] else None)
        if top is None:
            return head, None
        if len(parts) == 1:
            return top, None
        sub_raw = parts[1]
        cfg = META["categories"][top]
        for sub, sc in cfg["subs"].items():
            if sc["slug"] == sub_raw or sub == sub_raw:
                return top, sub
        return top, sub_raw  # 未知二级 -> 空池

    def _pool(self, top, sub):
        if top is None:
            return all_pool()
        if top not in BY_TOP:
            return []
        return pick_pool(top, sub)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def _send(self, body, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    global SLUG2TOP, SLUG2SUB
    SLUG2TOP, SLUG2SUB = load()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"API 已启动  http://{args.host}:{args.port}/")
    print(f"  示例      http://{args.host}:{args.port}/shanshui/lushan.json")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
