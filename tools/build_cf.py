# -*- coding: utf-8 -*-
"""生成 Cloudflare Workers 部署产物。

为什么不是"把数据打进 Worker"
    Worker 打包上限 64 MiB，看起来能装下 40 MB 语料，但**启动必须 < 1 秒**，
    顶层 JSON.parse 几十兆必然超时。因此数据走 Static Assets（2 万文件 / 单文件 25 MiB），
    Worker 只负责路由与随机抽取，一次请求最多两次子请求。

布局
    data/p/{c}.json        诗库分片，按累计字节切成约 120 KB 一块
    data/i/{slug}.{k}.json 某分类的诗在诗库中的下标，每块 1000 个
    src/manifest.js        诗库分片的累计边界 + 分类树（打进 Worker，体积只有几 KB）

用法：python tools/build_cf.py
"""

import json
import os
import sys
import shutil
import hashlib
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tagger.themes21 import THEMES as T21  # noqa: E402
CF = os.path.join(ROOT, "cloudflare")
PUB = os.path.join(CF, "public")

POEM_CHUNK_BYTES = 120_000
IDX_SHARD = 1000


def short_uid(uid):
    return hashlib.sha1(uid.encode("utf-8")).hexdigest()[:12]


def compact(p):
    """把对象压成定长数组，字段顺序由 WORKER_FIELDS 约定，省掉键名开销。"""
    return [
        short_uid(p["uid"]),
        p["title"],
        p["author"],
        p["dynasty"],
        p["section"],
        p["form"],
        p["paragraphs"],
        p["theme"] or "",
        p["style"] or "",
        p["mood"] or "",
        "/".join(p["scene"] or []),
        ";".join(f"{t}:{','.join(ss)}" for t, ss in p["cats"].items()),
        p.get("theme_conf") or 0,
        ",".join(p.get("theme2") or []),
    ]


def _clean(dirpath):
    """尽力而为地清空目录：失败只跳过，绝不让构建中断。"""
    if not os.path.isdir(dirpath):
        return 0
    n = 0
    for root, dirs, files in os.walk(dirpath, topdown=False):
        for f in files:
            try:
                os.unlink(os.path.join(root, f))
                n += 1
            except OSError:
                pass
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass
    return n


def write(path, obj, pretty=False):
    full = os.path.join(PUB, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False,
                  separators=(",", ":") if not pretty else None,
                  indent=1 if pretty else None)


def main():
    meta = json.load(open(os.path.join(ROOT, "data", "index.json"), encoding="utf-8"))
    poems = [json.loads(x) for x in open(os.path.join(ROOT, "data", "poems.jsonl"),
                                         encoding="utf-8")]
    n = len(poems)
    print(f"读入 {n} 首")

    # 不做删除：分片命名是确定性的，直接覆盖即可。残留的旧分片不会被
    # manifest 引用，不影响正确性；而某些环境会拦截批量删除，反而会打断构建。
    # ---------- 1. 诗库分片 ----------
    chunks, cur, cur_bytes = [], [], 0
    for p in poems:
        rec = compact(p)
        cur.append(rec)
        cur_bytes += len(json.dumps(rec, ensure_ascii=False).encode("utf-8"))
        if cur_bytes >= POEM_CHUNK_BYTES:
            chunks.append(cur)
            cur, cur_bytes = [], 0
    if cur:
        chunks.append(cur)

    boundaries = [0]
    for c in chunks:
        boundaries.append(boundaries[-1] + len(c))
    for i, c in enumerate(chunks):
        write(f"data/p/{i}.json", c)
    print(f"诗库 {len(chunks)} 个分片，平均 {n // len(chunks)} 首/片")

    # ---------- 2. 分类下标 ----------
    def shard_index(path_base, indices):
        """path_base 形如 shuqing/aiqing。必须按大类分目录：
        「励志」与「荔枝」的 slug 同为 lizhi，扁平命名会互相覆盖。"""
        k = 0
        for s in range(0, len(indices), IDX_SHARD):
            write(f"data/i/{path_base}.{k}.json", indices[s:s + IDX_SHARD])
            k += 1
        return k

    cats = meta["categories"]
    top_slug, sub_slug = {}, {}
    for top, cfg in cats.items():
        top_slug[top] = cfg["slug"]
    for top, cfg in cats.items():
        for s, sc in cfg["subs"].items():
            sub_slug[f"{top}/{s}"] = sc["slug"]

    bucket = {}
    for i, p in enumerate(poems):
        for top, subs in p["cats"].items():
            bucket.setdefault(f"{top_slug[top]}/_all", []).append(i)
            for s in subs:
                key = f"{top}/{s}"
                if key in sub_slug:
                    bucket.setdefault(f"{top_slug[top]}/{sub_slug[key]}", []).append(i)

    files = 0
    for path_base, idx in bucket.items():
        files += shard_index(path_base, idx)
    print(f"意象分类下标 {files} 个文件 / {len(bucket)} 个分类")

    # 21 题材下标（走 /t/ 命名空间）
    tb = {}
    for i, p in enumerate(poems):
        t = p.get("theme")
        if t in T21:
            tb.setdefault(T21[t][0], []).append(i)
    tf = 0
    for slug, idx in tb.items():
        tf += shard_index("t/" + slug, idx)
    print(f"题材下标 {tf} 个文件 / {len(tb)} 个题材")

    # ---------- 3. manifest.js ----------
    tree = {}
    for top, cfg in cats.items():
        tree[top] = {"slug": cfg["slug"], "count": cfg["count"], "desc": cfg.get("desc", ""),
                     "subs": {s: {"slug": sc["slug"], "count": sc["count"]}
                              for s, sc in cfg["subs"].items()}}
    theme_meta = {}
    for name, (slug, desc) in T21.items():
        theme_meta[slug] = {"name": name, "desc": desc, "count": len(tb.get(slug, []))}

    manifest = (
        "// 由 tools/build_cf.py 生成，请勿手改\n"
        f"export const BUILT_AT = {json.dumps(datetime.date.today().isoformat())};\n"
        f"export const TOTAL = {n};\n"
        f"export const IDX_SHARD = {IDX_SHARD};\n"
        f"export const POEM_CHUNKS = {json.dumps(boundaries)};\n"
        f"export const CATS = {json.dumps(tree, ensure_ascii=False)};\n"
        f"export const THEMES = {json.dumps(theme_meta, ensure_ascii=False)};\n"
    )
    os.makedirs(os.path.join(CF, "src"), exist_ok=True)
    open(os.path.join(CF, "src", "manifest.js"), "w", encoding="utf-8").write(manifest)
    print(f"manifest.js {len(manifest) / 1024:.1f} KB")

    # ---------- 4. 文档页 ----------
    tpl = open(os.path.join(CF, "template", "doc.html"), encoding="utf-8").read()
    trows = "".join(
        f'<tr><td>{d["name"]}</td><td><code>/{slug}</code></td>'
        f'<td class="num">{d["count"]:,}</td>'
        f'<td><button class="try" data-p="/{slug}.json">试一下</button></td></tr>'
        for slug, d in sorted(theme_meta.items(), key=lambda x: -x[1]["count"]))
    rows = []
    for top, cfg in cats.items():
        sub_rows = "".join(
            f'<tr><td class="l2">{s}</td><td><code>/{sc["slug"]}</code></td>'
            f'<td class="num">{sc["count"]:,}</td>'
            f'<td><button class="try" data-p="/{sc["slug"]}.json">试一下</button></td></tr>'
            for s, sc in cfg["subs"].items())
        rows.append(
            f'<tr class="grp"><td colspan="4"><span class="gt">{top}</span>'
            f'<span class="gc">{cfg["count"]:,} 首</span>'
            f'<code>/{cfg["slug"]}</code></td></tr>{sub_rows}')
    html = (tpl.replace("{{THEMEROWS}}", trows)
               .replace("{{NT}}", str(len(theme_meta)))
               .replace("{{ROUTES}}", "".join(rows))
               .replace("{{TOTAL}}", f"{n:,}")
               .replace("{{BUILT}}", datetime.date.today().isoformat()))
    open(os.path.join(PUB, "index.html"), "w", encoding="utf-8").write(html)
    # 另存一份 doc.html：/doc 由静态资源直接命中，不经过 Worker，
    # 因此不占用免费版每日 10 万次请求额度（直接命中，无重定向）。
    open(os.path.join(PUB, "doc.html"), "w", encoding="utf-8").write(html)
    stale = os.path.join(PUB, "doc")
    if os.path.isdir(stale):
        shutil.rmtree(stale)
    print(f"文档页 {len(html) / 1024:.1f} KB（/ 与 /doc 均为静态）")

    # ---------- 5. 浏览页（复用 web/） ----------
    bdst = os.path.join(PUB, "browse")
    os.makedirs(bdst, exist_ok=True)
    for f in ("index.html", "data.js"):
        shutil.copy(os.path.join(ROOT, "web", f), os.path.join(bdst, f))
    src_items = os.path.join(ROOT, "web", "items")
    dst_items = os.path.join(bdst, "items")
    os.makedirs(dst_items, exist_ok=True)
    for f in os.listdir(src_items):
        if f.endswith(".js"):
            shutil.copy(os.path.join(src_items, f), os.path.join(dst_items, f))
    print("浏览页 -> /browse/（按大类按需加载）")

    on_disk = len([f for f in os.listdir(os.path.join(PUB, "data", "p"))
                   if f.endswith(".json")])
    if on_disk < len(chunks):
        raise SystemExit(f"自检失败：诗库分片应有 {len(chunks)} 个，实际只有 {on_disk} 个")
    total_bytes = sum(os.path.getsize(os.path.join(dp, f))
                      for dp, _, fs in os.walk(PUB) for f in fs)
    nfiles = sum(len(fs) for _, _, fs in os.walk(PUB))
    print(f"产物合计 {total_bytes / 1048576:.1f} MB / {nfiles} 个文件")


if __name__ == "__main__":
    main()
