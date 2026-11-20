# -*- coding: utf-8 -*-
"""线上自检：部署后跑一遍，确认总数、分类、摘句都正常。

用法：python tools/verify_online.py [--base https://yiyan.952762.xyz]
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get(base, path, timeout=30):
    req = urllib.request.Request(base + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("CP_BASE", "https://yiyan.952762.xyz"))
    ap.add_argument("--sample", type=int, default=6)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    local = sum(1 for _ in open(os.path.join(ROOT, "data", "poems.jsonl"), encoding="utf-8"))
    fails = []

    code, body = get(base, "/api")
    api = json.loads(body)
    print(f"/api        {code}  count={api['count']}  本地={local}")
    if api["count"] != local:
        fails.append(f"总数不一致：线上 {api['count']} / 本地 {local}")

    paths = []
    for item in api["list"]:
        name = next(iter(item))
        paths.append((name, item[name].split(base, 1)[-1]))
    picks = random.Random(7).sample(paths, min(args.sample, len(paths)))
    for name, path in picks:
        time.sleep(0.4)
        try:
            code, body = get(base, path + ".json")
            d = json.loads(body)
            ok = code == 200 and d.get("paragraphs") and d.get("origin")
            print(f"  {name:14} {code} {'ok' if ok else 'BAD'}")
            if not ok:
                fails.append(f"{name} 返回异常")
        except Exception as e:
            print(f"  {name:14} ERR {type(e).__name__}")
            fails.append(f"{name} 请求失败")

    time.sleep(0.4)
    code, body = get(base, "/line")
    d = json.loads(body)
    print(f"/line       {code}  mode={d.get('mode')}  lines={len(d.get('lines') or [])}")
    if d.get("mode") != "line" or len(d.get("lines") or []) != 2:
        fails.append("摘句接口异常")

    if fails:
        print("\n自检未通过：")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("\n自检通过")


if __name__ == "__main__":
    main()
