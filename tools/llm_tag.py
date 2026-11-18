# -*- coding: utf-8 -*-
"""用大模型给全量语料做题材标注（离线跑，产物固化进静态分片）。

为什么走这条路
    关键词法在人工标注集上到 85.6%，但在全唐诗盲测只有 63%——瓶颈不是词表，
    而是"应制贺官""佛偈禅颂"这类仪式性文本用字面特征判不出来。交给模型判语义才有空间。

设计
    · 类目重定为 21 类，按语料实际构成（不迁就 v1）：送别与寄赠酬答分开、
      哲理与佛道禅颂分开、补应制干谒。
    · 每批 18 首，只给标题 + 作者 + 前四句（够判题旨，且控制 token）。
    · 断点续跑：结果追加进 JSONL，按 uid 跳过已完成项。
    · 并发 8，失败重试 2 次。

用法
    python tools/llm_tag.py --set bench        # 只跑 195 首人工基准，用于评估
    python tools/llm_tag.py --limit 400        # 跑一个抽样
    python tools/llm_tag.py --set all -j 8     # 全量（约 20 分钟）
"""

import argparse
import json
import os
import sys
import time
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "data", "llm_theme.jsonl")

TAXONOMY = {
    "山水田园": "以自然山水、田园农事为主体的观照与栖居",
    "边塞征战": "边关风物、军旅戍役、战争与征人",
    "咏史怀古": "凭吊古迹、品评历史人物、兴亡之叹",
    "咏物": "以具体物象为题，托物言志（咏蝉、咏梅、咏马等）",
    "送别": "饯别、留别、送行，场景是离别本身",
    "寄赠酬答": "寄赠、酬和、唱和、题赠友人，重在往还而非离别",
    "羁旅思乡": "行旅途次、客居异乡、望乡怀人",
    "闺情宫怨": "女性视角的幽怨、宫掖寂寞、弃妇之叹",
    "爱情相思": "男女情爱、相思、幽会、盟誓",
    "悼亡挽歌": "悼念亡者、挽词、哀祭",
    "讽喻刺世": "讥刺时政、揭露权贵、世态炎凉",
    "民生疾苦": "百姓生计、赋役灾荒、织妇征夫之苦",
    "哲理悟道": "老庄玄理、人生感悟、世事无常（非宗教仪式）",
    "佛道禅颂": "佛偈、禅颂、赞佛、道教丹道等宗教性文本",
    "闲适隐逸": "闲居自适、饮酒抚琴、避世幽栖",
    "节令风俗": "节日、节气、民俗风物",
    "题画咏艺": "题画、听乐、观舞、咏书论艺",
    "游仙梦幻": "神仙境界、梦游、幻境",
    "应制干谒": "应诏奉和、朝会侍宴、贺官授职、干谒求荐",
    "咏怀抒感": "直抒胸臆、无具体事由的感怀（遣怀、杂兴、偶成）",
    "亲情": "亲子、手足、夫妻人伦之情",
}
LABELS = list(TAXONOMY)

SYSTEM = "你是古典诗词分类专家，判断精准，只输出要求的 JSON。"

TMPL = """给下列每首诗判一个题材，只能从这 {n} 个标签中选一个：
{tags}

判定要点：
1. 以标题所示的事由和全诗主旨为准，不要被个别字句带偏。
2. 送别（场景是离别）与寄赠酬答（往还唱和）要区分；分不清时优先按标题的事由。
3. 应制、奉和、扈从、贺官、干谒求荐 → 应制干谒。
4. 偈、颂、赞佛、禅理、丹道 → 佛道禅颂；老庄玄理与人生感悟 → 哲理悟道。
5. 只有确实没有具体事由、纯抒感慨时才用咏怀抒感，不要拿它当兜底。
6. 乐府旧题按其实际内容判（如《少年行》多为边塞或咏侠）。

严格输出 JSON 对象，键为序号字符串，值为标签原文，不要任何解释或代码块：
{{"1":"山水田园","2":"送别"}}

诗：
{poems}"""


def load_cfg():
    p = os.path.join(os.path.expanduser("~"), ".workbuddy", "models.json")
    cfg = json.load(open(p, encoding="utf-8"))[0]
    return cfg["url"].rstrip("/") + "/chat/completions", cfg["apiKey"], cfg["id"]


def call(url, key, model, prompt, retries=2):
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}]}
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            r = json.load(urllib.request.urlopen(req, timeout=120))
            return r["choices"][0]["message"]["content"]
        except Exception as e:
            if a == retries:
                raise
            time.sleep(1.5 * (a + 1))


def parse(text, n):
    s = text.strip()
    if "```" in s:
        s = s.split("```")[1]
        s = s[4:] if s.lstrip().startswith("json") else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return {}
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return {}
    out = {}
    for k, v in obj.items():
        try:
            idx = int(str(k).strip())
        except ValueError:
            continue
        if 1 <= idx <= n and isinstance(v, str) and v.strip() in TAXONOMY:
            out[idx] = v.strip()
    return out


def batch(poems, size=18):
    for i in range(0, len(poems), size):
        yield poems[i:i + size]


def render(items):
    lines = []
    for k, p in enumerate(items, 1):
        head = "／".join(p["paragraphs"][:4])[:160]
        lines.append(f'{k}.《{p["title"]}》{p["author"]}：{head}')
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all", choices=["all", "bench", "sample"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--size", type=int, default=18)
    ap.add_argument("-j", type=int, default=8)
    args = ap.parse_args()

    poems = [json.loads(x) for x in open(os.path.join(ROOT, "data", "poems.jsonl"),
                                        encoding="utf-8")]
    if args.set == "bench":
        import importlib
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        ev = importlib.import_module("eval_theme")
        keep = []
        for p in poems:
            if p["section"] in ("唐诗", "宋词"):
                keep.append(p)
        # 基准集是三百首，用标题+作者匹配
        raw = []
        for k in ("tang300", "song300"):
            raw += json.load(open(os.path.join(ROOT, "raw", f"{k}.json"), encoding="utf-8"))
        from tagger import simp
        keys = set()
        for r in raw:
            if ev.gold_of(r.get("tags") or []):
                keys.add((simp.to_simp(r.get("author") or ""),
                          simp.to_simp(r.get("title") or r.get("rhythmic") or "")))
        poems = [p for p in poems if (p["author"], p["title"]) in keys]
        print(f"基准集 {len(poems)} 首")

    if args.limit:
        poems = poems[: args.limit]

    done = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                done.add(json.loads(line)["uid"])
            except Exception:
                pass
    todo = [p for p in poems if p["uid"] not in done]
    print(f"待标注 {len(todo)} 首（已完成 {len(done)}），批大小 {args.size}，并发 {args.j}")

    url, key, model = load_cfg()
    tag_str = "\n".join(f"- {k}：{v}" for k, v in TAXONOMY.items())
    lock = threading.Lock()
    stat = {"ok": 0, "miss": 0, "fail": 0}
    t0 = time.time()

    def work(chunk):
        prompt = TMPL.format(n=len(TAXONOMY), tags=tag_str, poems=render(chunk))
        try:
            out = parse(call(url, key, model, prompt), len(chunk))
        except Exception:
            with lock:
                stat["fail"] += 1
            return
        rows = []
        for k, p in enumerate(chunk, 1):
            if k in out:
                rows.append({"uid": p["uid"], "theme": out[k], "src": "llm"})
            else:
                rows.append({"uid": p["uid"], "theme": None, "src": "llm"})
        with lock:
            with open(OUT, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            stat["ok"] += sum(1 for r in rows if r["theme"])
            stat["miss"] += sum(1 for r in rows if not r["theme"])
            n = stat["ok"] + stat["miss"]
            if n and n % 180 < args.size:
                rate = n / max(time.time() - t0, 1e-6)
                print(f"  {n}/{len(todo)}  {rate:.1f} 首/秒  "
                      f"预计剩余 {(len(todo) - n) / max(rate, .01) / 60:.1f} 分钟", flush=True)

    with ThreadPoolExecutor(max_workers=args.j) as ex:
        list(ex.map(work, batch(todo, args.size)))

    print(f"完成：命中 {stat['ok']}，缺标签 {stat['miss']}，失败批次 {stat['fail']}，"
          f"耗时 {(time.time() - t0) / 60:.1f} 分钟 -> {OUT}")


if __name__ == "__main__":
    main()
