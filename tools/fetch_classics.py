# -*- coding: utf-8 -*-
"""补抓汉魏六朝语料：来自 wujinpai/chinese-classics（MIT）。

该仓库按作者一文件，形如 {"dynasty": ..., "author": ..., "works": [...]}；
这里只取秦漢、魏晋南北朝、隋三段，拍平成数组，交给 tagger.build 直接读。

用法：python tools/fetch_classics.py [qinhan|weijin|sui|all]
输出：raw/full/{qinhan,weijin,sui}/*.json（每段一个文件）
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://cdn.jsdelivr.net/gh/wujinpai/chinese-classics@main/"
OUT = os.path.join(ROOT, "raw", "full")

SECTIONS = {
    "qinhan": {"dir": "秦汉诗", "dynasty": "汉", "section": "秦汉"},
    "weijin": {"dir": "魏晋南北朝诗", "dynasty": "魏晋南北朝", "section": "魏晋南北朝"},
    "sui": {"dir": "隋诗", "dynasty": "隋", "section": "隋"},
}

# 宋诗、明诗体量太大（13,141 个作者文件），走整仓 tarball 一次性解压，快得多。
TARBALL = "https://codeload.github.com/wujinpai/chinese-classics/tar.gz/refs/heads/main"
BULK = {
    "songshi": {"dir": "宋诗", "dynasty": "宋", "section": "宋诗"},
    "mingshi": {"dir": "明诗", "dynasty": "明", "section": "明诗"},
}

# 作者目录需要先知道文件名，用仓库的文件树换取
TREE_API = ("https://api.github.com/repos/wujinpai/chinese-classics/git/trees/main"
            "?recursive=1")


def _token():
    """GitHub API 要鉴权才稳定，用本机已保存的 git 凭据。"""
    try:
        import subprocess
        p = subprocess.run(["git", "credential", "fill"], text=True, capture_output=True,
                           input="protocol=https\nhost=github.com\n\n", timeout=30)
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except Exception:
        pass
    return ""


def _headers():
    h = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}
    tok = _token()
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def list_files():
    """逐个目录列文件：contents API 一次最多返回 1000 条，足够用。"""
    out = {}
    for key, cfg in SECTIONS.items():
        url = ("https://api.github.com/repos/wujinpai/chinese-classics/contents/"
               + urllib.parse.quote(cfg["dir"]) + "?ref=main")
        names = []
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers=_headers())
                with urllib.request.urlopen(req, timeout=90) as r:
                    items = json.loads(r.read())
                names = [it["path"] for it in items
                         if it.get("type") == "file" and it["path"].endswith(".json")]
                break
            except Exception as e:
                print(f"  [{key}] 列表重试 {attempt + 1} {type(e).__name__}")
                time.sleep(2)
        out[key] = names
    return out


def fetch_one(path):
    """优先 jsDelivr（快），失败退回 GitHub contents API（慢但稳）。"""
    url = BASE + urllib.parse.quote(path)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return path, json.loads(r.read())
        except Exception:
            time.sleep(1.5)
    tok = _token()
    if tok:
        import base64
        api = ("https://api.github.com/repos/wujinpai/chinese-classics/contents/"
               + urllib.parse.quote(path))
        try:
            req = urllib.request.Request(api, headers={
                "User-Agent": "Mozilla/5.0", "Authorization": "Bearer " + tok,
                "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            return path, json.loads(base64.b64decode(d["content"]))
        except Exception as e:
            return path, {"__error__": f"{type(e).__name__}"}
    return path, {"__error__": "no-token"}


def _flatten(data, cfg, poems):
    author = (data.get("author") or "").strip()
    for w in data.get("works") or []:
        paras = [str(x).strip() for x in (w.get("paragraphs") or []) if str(x).strip()]
        title = (w.get("title") or "").strip()
        if not paras or not title:
            continue
        poems.append({"title": title, "author": author or "佚名",
                      "paragraphs": paras,
                      "dynasty": data.get("dynasty") or cfg["dynasty"]})


SHARD = 20000  # 每个分片的首数；6 MB 左右，远低于 GitHub 的 50 MB 警告线


def _has_shards(key):
    d = os.path.join(OUT, key)
    return os.path.isdir(d) and any(
        f.endswith(".json") and not f.startswith("_") for f in os.listdir(d))


def _write_shards(key, poems):
    """写成 {key}.0.json / {key}.1.json ...，先清空目录避免旧文件被重复累加。"""
    d = os.path.join(OUT, key)
    os.makedirs(d, exist_ok=True)
    for old in os.listdir(d):
        if old.endswith(".json"):
            os.remove(os.path.join(d, old))
    n = 0
    for i in range(0, len(poems), SHARD):
        p = os.path.join(d, f"{key}.{n}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(poems[i:i + SHARD], f, ensure_ascii=False)
        n += 1
    return n


def fetch_bulk(keys):
    """宋诗 / 明诗：整仓 tarball 解压后拍平，比逐个作者抓快一个数量级。"""
    import shutil
    import tarfile

    keys = [k for k in keys if not _has_shards(k)]
    if not keys:
        return
    tgz = os.path.join(OUT, "_classics.tar.gz")
    if not os.path.exists(tgz):
        print("下载整仓 tarball（约 110 MB）...")
        req = urllib.request.Request(TARBALL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=900) as r, open(tgz, "wb") as f:
            shutil.copyfileobj(r, f)
    tmp = os.path.join(OUT, "_tmp_tar")
    for key in keys:
        cfg = BULK[key]
        print(f"[{key}] 解压 {cfg['dir']} ...")
        with tarfile.open(tgz) as tf:
            root = tf.getmembers()[0].name.split("/")[0]
            prefix = f"{root}/{cfg['dir']}/"
            members = [m for m in tf.getmembers()
                       if m.isfile() and m.name.startswith(prefix)]
            tf.extractall(tmp, members=members, filter="data")
        poems = []
        src = os.path.join(tmp, root, cfg["dir"])
        for name in sorted(os.listdir(src)):
            if not name.endswith(".json"):
                continue
            try:
                data = json.load(open(os.path.join(src, name), encoding="utf-8"))
            except Exception:
                continue
            _flatten(data, cfg, poems)
        n = _write_shards(key, poems)
        print(f"[{key}] {len(poems)} 首 -> {n} 个分片（{os.path.relpath(os.path.join(OUT, key), ROOT)}）")
    shutil.rmtree(tmp, ignore_errors=True)


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "all"
    if want in ("all", "songshi", "mingshi"):
        keys = list(BULK) if want == "all" else [want]
        fetch_bulk(keys)
        if want != "all":
            return
    files = list_files()
    if not files:
        return
    for key, cfg in SECTIONS.items():
        if want not in ("all", key) or not files.get(key):
            continue
        dst = os.path.join(OUT, key, f"{key}.json")
        if os.path.exists(dst) and os.path.getsize(dst) > 10000:
            print(f"[{key}] 已存在，跳过")
            continue
        print(f"[{key}] 下载 {len(files[key])} 个作者文件 ...")
        poems, errors = [], 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for path, data in ex.map(fetch_one, sorted(files[key])):
                if "__error__" in data:
                    errors += 1
                    continue
                author = (data.get("author") or "").strip()
                for w in data.get("works") or []:
                    paras = w.get("paragraphs") or []
                    title = (w.get("title") or "").strip()
                    if not paras or not title:
                        continue
                    poems.append({
                        "title": title,
                        "author": author or "佚名",
                        "paragraphs": [str(x).strip() for x in paras if str(x).strip()],
                        "dynasty": cfg["dynasty"],
                    })
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(poems, f, ensure_ascii=False)
        print(f"[{key}] {len(poems)} 首 -> {os.path.relpath(dst, ROOT)}，失败 {errors} 个文件")


if __name__ == "__main__":
    main()
