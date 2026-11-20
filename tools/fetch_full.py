# -*- coding: utf-8 -*-
"""全量语料下载：走 jsDelivr 的 GitHub 镜像（raw.githubusercontent 与 codeload 在此环境不可用）。

源仓库是上游 chinese-poetry/chinese-poetry：本项目的 fork 已清掉这些语料目录，
只保留构建产物所需的 raw/full。宋诗、明诗不在这里，见 tools/fetch_classics.py。

用法：python tools/fetch_full.py [tang|ci|shijing|chuci|wudai|nalan|yuanqu|all]
输出：raw/full/<dest>/*.json（已存在的文件会跳过，可用于断点续传）
"""

import os
import sys
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = os.environ.get("CP_UPSTREAM_REPO", "chinese-poetry/chinese-poetry")
BASE = f"https://cdn.jsdelivr.net/gh/{UPSTREAM}@master/"
OUT = os.path.join(ROOT, "raw", "full")

TASKS = {
    "tang": {
        "dir": "全唐诗",
        "files": [f"poet.tang.{i}.json" for i in range(0, 58000, 1000)],
        "dest": "tang",
    },
    "ci": {
        "dir": "宋词",
        "files": [f"ci.song.{i}.json" for i in range(0, 22000, 1000)],
        "dest": "ci",
    },
    "shijing": {"dir": "诗经", "files": ["shijing.json"], "dest": "shijing"},
    "chuci": {"dir": "楚辞", "files": ["chuci.json"], "dest": "chuci"},
    "wudai": {
        "dir": "五代诗词",
        "files": [("nantang/poetrys.json", "poetrys.json")] +
                 [(f"huajianji/huajianji-{i}-juan.json", f"hj-{i}-juan.json")
                  for i in list(range(1, 10)) + ["x"]],
        "dest": "wudai",
    },
    "nalan": {"dir": "纳兰性德",
              "files": [("纳兰性德诗集.json", "nalan.json")], "dest": "nalan"},
    "yuanqu": {"dir": "元曲", "files": ["yuanqu.json"], "dest": "yuanqu"},
}


def url_of(path):
    return BASE + urllib.parse.quote(path)


def fetch_one(args):
    sub, remote, name, dest = args
    dst = os.path.join(OUT, dest, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 1000:
        return name, "skip", os.path.getsize(dst)
    url = url_of(f"{sub}/{remote}")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            json.loads(data.decode("utf-8"))  # 校验不是错误页
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
            return name, "ok", len(data)
        except Exception as e:
            if attempt == 2:
                return name, f"fail:{type(e).__name__}", 0
    return name, "fail", 0


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = []
    for key, cfg in TASKS.items():
        if want not in ("all", key):
            continue
        for f in cfg["files"]:
            remote, local = f if isinstance(f, tuple) else (f, f)
            jobs.append((cfg["dir"], remote, local, cfg["dest"]))
    print(f"待下载 {len(jobs)} 个文件 -> {OUT}")
    ok = skip = 0
    fails = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (name, st, size) in enumerate(ex.map(fetch_one, jobs), 1):
            if st == "ok":
                ok += 1
            elif st == "skip":
                skip += 1
            else:
                fails.append((name, st))
            if i % 10 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  成功 {ok} 跳过 {skip} 失败 {len(fails)}", flush=True)
    if fails:
        print("失败:", fails[:10])
    print("完成")


if __name__ == "__main__":
    main()
