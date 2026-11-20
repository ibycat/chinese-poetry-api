# -*- coding: utf-8 -*-
"""用大模型给语料做 21 题材语义标注（离线跑，产物固化进静态分片）。

精度优先设计：
    · 默认使用 models.json 中最后配置的模型，也可用 --model 显式指定。
    · 默认提供完整正文；长文仅在超出 --max-chars 时保留首尾并标记省略。
    · 宋词明确标注词牌，避免把《临江仙》等曲调名误当事由标题。
    · 输出题材、置信度和最短证据；无效结果不写完成记录，可断点补跑。

用法：
    python tools/llm_tag.py --set bench --out data/theme_labels_bench.jsonl
    python tools/llm_tag.py --set all -j 8
    python tools/llm_tag.py --set all --model cn:kimi-k3.1-pro -j 8
"""

import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DEFAULT_OUT = os.path.join(ROOT, "data", "theme_labels.jsonl")
PROMPT_VERSION = "theme-v2-full-2026-11-18"
DEFAULT_MODEL = "cn:deepseek-v4.1-flash"

TAXONOMY = {
    "山水田园": "以自然山水、田园农事为主体的观照与栖居",
    "边塞征战": "边关风物、军旅戍役、战争与征人",
    "咏史怀古": "凭吊古迹、品评历史人物、兴亡之叹",
    "咏物": "以具体物象为题，托物言志（咏蝉、咏梅、咏马等）",
    "送别": "饯别、留别、送行，离别的场景或动作本身就是主旨",
    "寄赠酬答": "寄赠、酬和、唱和、题赠友人，重在书信往还而非送行",
    "羁旅思乡": "行旅途次、客居异乡、望乡怀人",
    "闺情宫怨": "女性视角的幽怨、宫掖寂寞、弃妇之叹",
    "爱情相思": "男女情爱、相思、幽会、盟誓",
    "悼亡挽歌": "悼念亡者、挽词、哀祭",
    "讽喻刺世": "讥刺时政、揭露权贵、世态炎凉",
    "民生疾苦": "百姓生计、赋役灾荒、织妇征夫之苦",
    "哲理悟道": "老庄玄理、人生感悟、世事无常（非宗教仪式）",
    "佛道禅颂": "佛偈、禅颂、赞佛、道教丹道等宗教性文本",
    "闲适隐逸": "闲居自适、饮酒抚琴、避世幽栖",
    "节令风俗": "节日、节气、民俗风物，节令本身须是写作中心",
    "题画咏艺": "题画、听乐、观舞、咏书论艺",
    "游仙梦幻": "神仙境界、梦游、幻境",
    "应制干谒": "应诏奉和、朝会侍宴、贺官授职、干谒求荐",
    "咏怀抒感": "直抒胸臆、无具体事由的感怀（遣怀、杂兴、偶成）",
    "亲情": "亲子、手足、夫妻人伦之情",
}
LABELS = list(TAXONOMY)

SYSTEM = "你是中国古典文学文献分类专家。你依据写作事由和全篇主旨分类，不做机械关键词匹配。"

TMPL = """给下列每首诗判定唯一题材，只能从 {n} 个标签中选择：
{tags}

判定原则：
1. 先判断写作场合和全篇主旨，再看局部意象；个别字词不能决定题材。
2. 咏史怀古优先于羁旅、山水：只要主旨是凭吊史迹或评论历史人物，就归咏史怀古。
3. 应制、奉和、扈从、朝会、贺官、求荐优先按应制干谒；不能把礼仪性景物诗判成山水。
4. 偈、颂、赞佛、禅理、丹道归佛道禅颂；游寺赏景而没有宗教主旨仍按实际事由分类。
5. 送别必须有送行、离别场景；寄、赠、酬、答、和等往还唱和归寄赠酬答。
6. 咏物要求某一物象是全篇中心；梅、柳、雁、月等随手取景不算咏物。
7. 山水田园、羁旅思乡、闲适隐逸按主旨区分：自然观照／行旅怀乡／闲居避世。
8. 闺情宫怨重女性独处幽怨；爱情相思重男女情爱与相互思念；亲情重亲子手足等伦理关系。
9. 节令风俗要求节日民俗是中心，仅出现春秋风雪不算；咏怀抒感只用于无具体事由的纯抒怀。
10. 宋词标注为“词牌”的名称只是曲调，不得把词牌中的仙、山、美人、贺、行等字当作题材依据。

每首给出最短证据和主观置信度。严格输出 JSON 对象，不要解释或代码块：
{{"1":{{"theme":"山水田园","confidence":0.92,"evidence":"最关键依据"}}}}

作品：
{poems}"""

REVIEW_TMPL = """你是题材终审员。下列作品的规则初判与模型初判不一致，请重新通读全文后终审。
只能从这 {n} 个标签中选择：
{tags}

终审要求：
1. 不被两个候选限制；若都不准确，必须选择更合适的其他标签。
2. 先确定标题所示写作事由和全篇主旨，再比较候选，不因某个显眼词语而投票。
3. 咏史怀古优先于山水、羁旅；应制干谒优先于山水、寄赠；宗教主旨归佛道禅颂。
4. 宋词词牌不是事由标题；咏物须有中心物象；节令须以节日民俗为中心。
5. 送别重实际送行，寄赠酬答重往还唱和，咏怀抒感不得作为有明确事由作品的兜底。

每首给出最短终审证据和置信度。严格输出 JSON，不要解释或代码块：
{{"1":{{"theme":"送别","confidence":0.95,"evidence":"最关键依据"}}}}

候选与作品：
{poems}"""


def load_cfg(model=None):
    """优先读环境变量，便于在无头服务器上跑；没有配置才退回本机 models.json。"""
    model = (model or os.environ.get("CP_THEME_MODEL")
             or os.environ.get("LLM_MODEL") or DEFAULT_MODEL)
    base = os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if base and key:
        return base.rstrip("/") + "/chat/completions", key, model

    path = os.path.join(os.path.expanduser("~"), ".workbuddy", "models.json")
    if not os.path.exists(path):
        raise SystemExit("未配置模型：请设置 LLM_BASE_URL / LLM_API_KEY，"
                         "或提供 ~/.workbuddy/models.json")
    configs = json.load(open(path, encoding="utf-8"))
    matches = [c for c in configs if c.get("id") == model]
    if matches:
        cfg = matches[0]
    else:
        # 同一网关暴露的模型未必都写进 models.json；沿用网关地址与凭据。
        # 调用失败会明确报模型错误，不会静默回退到其他模型。
        cfg = dict(configs[0])
        cfg["id"] = model
    return cfg["url"].rstrip("/") + "/chat/completions", cfg["apiKey"], model


def call(url, key, model, prompt, timeout=180, retries=3):
    body = {"model": model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": prompt}]}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            response = json.load(urllib.request.urlopen(req, timeout=timeout))
            return response["choices"][0]["message"]["content"]
        except Exception:
            if attempt == retries:
                raise
            time.sleep(1.5 * (attempt + 1))


def parse(text, n):
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < 0:
        return {}
    try:
        obj = json.loads(text[i:j + 1])
    except Exception:
        return {}
    out = {}
    for key, value in obj.items():
        try:
            idx = int(str(key).strip())
        except ValueError:
            continue
        if not 1 <= idx <= n:
            continue
        if isinstance(value, str):
            value = {"theme": value}
        if not isinstance(value, dict):
            continue
        theme = value.get("theme") or value.get("label")
        if not isinstance(theme, str) or theme.strip() not in TAXONOMY:
            continue
        try:
            conf = float(value.get("confidence", value.get("conf", 0)))
        except (TypeError, ValueError):
            conf = 0
        evidence = value.get("evidence") or value.get("reason") or ""
        out[idx] = {"theme": theme.strip(), "conf": max(0, min(conf, 1)),
                    "evidence": str(evidence)[:80]}
    return out


def batch(poems, size):
    for i in range(0, len(poems), size):
        yield poems[i:i + size]


def poem_text(poem, max_chars):
    text = "／".join(poem["paragraphs"])
    if max_chars and len(text) > max_chars:
        head = max_chars * 2 // 3
        tail = max_chars - head
        text = text[:head] + "／……［中段省略］……／" + text[-tail:]
    return text


def render(items, max_chars):
    lines = []
    for k, poem in enumerate(items, 1):
        if poem.get("section") == "宋词":
            head = f'{k}.［宋词；词牌《{poem["title"]}》，非事由标题］{poem["author"]}'
        else:
            head = f'{k}.［{poem.get("section", "诗词")}］《{poem["title"]}》{poem["author"]}'
        lines.append(head + "：\n" + poem_text(poem, max_chars))
    return "\n\n".join(lines)


def load_labels(path):
    labels = {}
    if not os.path.exists(path):
        return labels
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
            if row.get("theme") in TAXONOMY:
                labels[row["uid"]] = row
        except Exception:
            pass
    return labels


def render_review(items, max_chars, candidates):
    blocks = []
    for k, poem in enumerate(items, 1):
        candidate = candidates[poem["uid"]]
        block = f'{k}. 规则初判={poem["theme"]}；模型初判={candidate["theme"]}；' \
                f'模型初判依据={candidate.get("evidence", "")}\n' + \
                render([poem], max_chars).replace("1.", f"{k}.", 1)
        blocks.append(block)
    return "\n\n".join(blocks)


def load_done(path):
    done = set()
    if not os.path.exists(path):
        return done
    for line in open(path, encoding="utf-8"):
        try:
            row = json.loads(line)
            if row.get("theme") in TAXONOMY:
                done.add(row["uid"])
        except Exception:
            pass
    return done


def select_bench(poems):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import eval_theme as ev
    from tagger.build import make_uid
    uids = set()
    for key, section in (("tang300", "唐诗"), ("song300", "宋词")):
        raw = json.load(open(os.path.join(ROOT, "raw", f"{key}.json"), encoding="utf-8"))
        for row in raw:
            if ev.gold_of(row.get("tags") or []):
                uids.add(make_uid(row, section))
    return [p for p in poems if p["uid"] in uids]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all", choices=["all", "bench"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--section", action="append",
                    help="只标注指定分部，如 --section 魏晋南北朝 --section 元曲")
    ap.add_argument("--theme", action="append", choices=LABELS,
                    help="只处理指定规则题材，可重复传入")
    ap.add_argument("--random-seed", type=int, default=None,
                    help="按固定随机种子抽样，用于覆盖全量语料而非只取开头")
    ap.add_argument("--max-rule-conf", type=float, default=None,
                    help="只处理规则置信度低于该值的作品")
    ap.add_argument("--size", type=int, default=8, help="每批作品数；精度优先默认 8")
    ap.add_argument("-j", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--max-chars", type=int, default=4000,
                    help="单首最大字符数，0 表示不截断")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--force", action="store_true", help="忽略已有完成记录，重新标注")
    ap.add_argument("--review-with", default=None,
                    help="读取初判标签，只复核它与规则结果不一致的作品")
    args = ap.parse_args()

    poems = [json.loads(x) for x in open(os.path.join(ROOT, "data", "poems.jsonl"),
                                        encoding="utf-8")]
    if args.set == "bench":
        poems = select_bench(poems)
        print(f"基准集 {len(poems)} 首")
    if args.section:
        want = set(args.section)
        poems = [p for p in poems if p.get("section") in want]
        print(f"限定分部 {sorted(want)}：{len(poems)} 首")
    if args.theme:
        poems = [p for p in poems if p["theme"] in args.theme]
    if args.max_rule_conf is not None:
        poems = [p for p in poems if p.get("theme_conf", 0) < args.max_rule_conf]
    if args.random_seed is not None:
        random.Random(args.random_seed).shuffle(poems)
    if args.limit:
        poems = poems[:args.limit]

    candidates = {}
    if args.review_with:
        candidates = load_labels(args.review_with)
        poems = [p for p in poems if p["uid"] in candidates
                 and p["theme"] != candidates[p["uid"]]["theme"]]
        print(f"需要终审的规则/模型分歧作品 {len(poems)} 首")

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    done = set() if args.force else load_done(out_path)
    todo = [p for p in poems if p["uid"] not in done]
    url, key, model = load_cfg(args.model)
    print(f"模型 {model}；待标注 {len(todo)} 首（已完成 {len(done)}），"
          f"批大小 {args.size}，并发 {args.j}", flush=True)

    tag_str = "\n".join(f"- {k}：{v}" for k, v in TAXONOMY.items())
    lock = threading.Lock()
    stat = {"ok": 0, "miss": 0, "fail": 0}
    t0 = time.time()

    def work(chunk):
        if args.review_with:
            prompt = REVIEW_TMPL.format(
                n=len(TAXONOMY), tags=tag_str,
                poems=render_review(chunk, args.max_chars, candidates))
        else:
            prompt = TMPL.format(n=len(TAXONOMY), tags=tag_str,
                                 poems=render(chunk, args.max_chars))
        try:
            result = parse(call(url, key, model, prompt, args.timeout), len(chunk))
            missing = [i for i in range(1, len(chunk) + 1) if i not in result]
            if missing and len(chunk) > 1:
                # 批量输出漏项时单独补判，避免错位或为了凑数而接受无效标签。
                for idx in missing:
                    one = chunk[idx - 1]
                    if args.review_with:
                        one_prompt = REVIEW_TMPL.format(
                            n=len(TAXONOMY), tags=tag_str,
                            poems=render_review([one], args.max_chars, candidates))
                    else:
                        one_prompt = TMPL.format(n=len(TAXONOMY), tags=tag_str,
                                                 poems=render([one], args.max_chars))
                    one_result = parse(call(url, key, model, one_prompt, args.timeout), 1)
                    if 1 in one_result:
                        result[idx] = one_result[1]
        except Exception as exc:
            with lock:
                stat["fail"] += 1
                print(f"  批次失败：{type(exc).__name__}: {exc}", flush=True)
            return

        rows = []
        for k, poem in enumerate(chunk, 1):
            if k not in result:
                continue
            value = result[k]
            rows.append({"uid": poem["uid"], "theme": value["theme"],
                         "conf": round(value["conf"], 3), "evidence": value["evidence"],
                         "src": "llm_review" if args.review_with else "llm",
                         "model": model, "prompt": PROMPT_VERSION})
        with lock:
            with open(out_path, "a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            stat["ok"] += len(rows)
            stat["miss"] += len(chunk) - len(rows)
            finished = stat["ok"] + stat["miss"]
            if finished and (finished % 160 < args.size or finished == len(todo)):
                rate = finished / max(time.time() - t0, 1e-6)
                print(f"  {finished}/{len(todo)}  {rate:.2f} 首/秒  "
                      f"预计剩余 {(len(todo) - finished) / max(rate, .01) / 60:.1f} 分钟",
                      flush=True)

    with ThreadPoolExecutor(max_workers=args.j) as executor:
        list(executor.map(work, batch(todo, args.size)))

    print(f"完成：命中 {stat['ok']}，缺标签 {stat['miss']}，失败批次 {stat['fail']}，"
          f"耗时 {(time.time() - t0) / 60:.1f} 分钟 -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
