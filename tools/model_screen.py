# -*- coding: utf-8 -*-
"""横向评测：哪个模型判题材更准。

在同一批人工金标准上跑同一个提示词，只换模型，比准确率。
用法：python tools/model_screen.py [--n 40] [--model id]...
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from tagger import simp  # noqa: E402
import eval_theme as ev  # noqa: E402

CANDIDATES = [
    "cn:deepseek-v4.1-flash",   # 当前使用
    "cn:deepseek-v4-pro",
    "cn:glm-5.3",
    "cn:kimi-k3.1-pro",
    "cn:deep-model",
    "cn:hy4std",
]

TAX = {
    "山水田园": "以自然山水、田园农事为主体的观照与栖居",
    "边塞征战": "边关风物、军旅戍役、战争与征人",
    "咏史怀古": "凭吊古迹、品评历史人物、兴亡之叹",
    "咏物": "以具体物象为题，托物言志",
    "送别": "饯别、留别、送行，场景是离别本身",
    "寄赠酬答": "寄赠、酬和、唱和、题赠，重在往还而非离别",
    "羁旅思乡": "行旅途次、客居异乡、望乡怀人",
    "闺情宫怨": "女性视角的幽怨、宫掖寂寞、弃妇之叹",
    "爱情相思": "男女情爱、相思、幽会、盟誓",
    "悼亡挽歌": "悼念亡者、挽词、哀祭",
    "讽喻刺世": "讥刺时政、揭露权贵、世态炎凉",
    "民生疾苦": "百姓生计、赋役灾荒、织妇征夫之苦",
    "哲理悟道": "老庄玄理、人生感悟、世事无常",
    "佛道禅颂": "佛偈、禅颂、赞佛、道教丹道等宗教性文本",
    "闲适隐逸": "闲居自适、饮酒抚琴、避世幽栖",
    "节令风俗": "节日、节气、民俗风物",
    "题画咏艺": "题画、听乐、观舞、咏书论艺",
    "游仙梦幻": "神仙境界、梦游、幻境",
    "应制干谒": "应诏奉和、朝会侍宴、贺官授职、干谒求荐",
    "咏怀抒感": "直抒胸臆、无具体事由的感怀",
    "亲情": "亲子、手足、夫妻人伦之情",
}
TAGSTR = "\n".join(f"- {k}：{v}" for k, v in TAX.items())

FEW = """示例：
《送元二使安西》王维：渭城朝雨浥轻尘／客舍青青柳色新／劝君更尽一杯酒／西出阳关无故人 → 送别
《寄令狐郎中》李商隐：嵩云秦树久离居／双鲤迢迢一纸书 → 寄赠酬答
《无题》李商隐：相见时难别亦难／东风无力百花残／春蚕到死丝方尽 → 爱情相思
《长信秋词》王昌龄：奉帚平明金殿开／且将团扇共徘徊 → 闺情宫怨
《题破山寺后禅院》常建：清晨入古寺／初日照高林／曲径通幽处 → 山水田园
"""

PROMPT = """给这首诗判一个题材，只能从下列 {n} 个标签中选一个：
{tags}

{few}
要点：
1. 以标题事由与全诗主旨为准，不被个别字句带偏。
2. 送别＝场景是离别；寄赠酬答＝往还唱和。
3. 应制、奉和、扈从、贺官、干谒求荐 → 应制干谒。
4. 偈、颂、赞佛、禅理、丹道 → 佛道禅颂；老庄与人生感悟 → 哲理悟道。
5. 咏怀抒感只用于确实无具体事由的纯抒怀。

只输出 JSON：{{"theme":"标签"}}

《{title}》{author}：{body}"""


def body_of(p):
    ps = p["paragraphs"]
    if sum(len(x) for x in ps) <= 110:
        return "／".join(ps)
    return "／".join(ps[:8])


def call(base, key, model, prompt):
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps({"model": model, "temperature": 0,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    r = json.load(urllib.request.urlopen(req, timeout=35))
    t = r["choices"][0]["message"]["content"]
    i, j = t.find("{"), t.rfind("}")
    try:
        v = json.loads(t[i:j + 1]).get("theme", "")
        return v if v in TAX else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--model", action="append")
    args = ap.parse_args()

    base = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not (base and key):
        cfg = json.load(open(os.path.join(os.path.expanduser("~"), ".workbuddy",
                                          "models.json"), encoding="utf-8"))[0]
        base, key = cfg["url"], cfg["apiKey"]
    base = base.rstrip("/")

    raw = []
    for k in ("tang300", "song300"):
        raw += json.load(open(os.path.join(ROOT, "raw", f"{k}.json"), encoding="utf-8"))
    gold = {}
    for r in raw:
        g = ev.gold_of(r.get("tags") or [])
        if g:
            gold[(simp.to_simp(r.get("author") or ""),
                  simp.to_simp(r.get("title") or r.get("rhythmic") or ""))] = g

    poems = [json.loads(x) for x in open(os.path.join(ROOT, "data", "poems.jsonl"),
                                        encoding="utf-8")]
    sel = [p for p in poems if (p["author"], p["title"]) in gold][: args.n]
    print(f"评测集 {len(sel)} 首（人工标签可映射）\n")

    models = args.model or CANDIDATES
    rows = []
    for m in models:
        t0 = time.time()
        ok = fail = 0

        def one(p):
            pr = PROMPT.format(n=len(TAX), tags=TAGSTR, few=FEW,
                               title=p["title"], author=p["author"], body=body_of(p))
            try:
                return p, call(base, key, m, pr)
            except Exception:
                return p, None

        with ThreadPoolExecutor(max_workers=12) as ex:
            res = list(ex.map(one, sel))
        for p, lab in res:
            if lab is None:
                fail += 1
                continue
            if lab in gold[(p["author"], p["title"])]:
                ok += 1
        n = len(sel)
        rows.append((m, ok, n - ok - fail, fail, time.time() - t0))
        print(f"{m:<24s} 命中 {ok:>3}/{n} = {ok/n:>5.1%}  无效 {fail}  {time.time()-t0:.0f}s")

    rows.sort(key=lambda x: -x[1])
    print("\n排序：")
    for m, ok, bad, fail, sec in rows:
        print(f"  {ok/len(sel):>5.1%}  {m}")


if __name__ == "__main__":
    main()
