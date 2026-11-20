/**
 * 诗词分类接口 · Cloudflare Workers
 *
 * 数据不打包进 Worker：7.6 万首诗的 JSON 会让启动时间突破 1 秒上限。
 * 语料放在 Static Assets，Worker 只做路由 + 随机抽取，一次请求最多两次子请求：
 *   data/i/{slug}.{k}.json  某分类的 1000 个诗库下标（约 7 KB）
 *   data/p/{c}.json         诗库分片（约 120 KB）
 */

import { CATS, THEMES, POEM_CHUNKS, TOTAL, IDX_SHARD, BUILT_AT } from "./manifest.js";

const ASSET_ORIGIN = "https://assets.local";

/** 原生 Rate Limiting：60 次 / 60 秒 / IP，按 Cloudflare 机房近似计数并允许突发。 */
async function allowRequest(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  try {
    const { success } = await env.IP_RATE_LIMITER.limit({ key: ip });
    return success;
  } catch (e) {
    // 限流设施异常时放行，避免整个 API 因限流组件不可用而中断。
    console.error("rate limiter failed:", e && e.stack || e);
    return true;
  }
}

/* ---------------- 扁平命名空间 ----------------
   每个分类一个 ASCII slug，大类、小类、题材平级：
     /shanshui  /lushan  /meihua  /songbie
   slug 全局唯一（题材名取不与意象冲突的近义词），因此不需要任何前缀或中文别名。 */
const FLAT = new Map();

const nodeKey = (n) => `${n.type}|${n.top || ""}|${n.sub || ""}|${n.slug}`;

for (const [top, cfg] of Object.entries(CATS)) {
  FLAT.set(cfg.slug, { type: "top", top, sub: null, slug: cfg.slug, count: cfg.count });
}
for (const [top, cfg] of Object.entries(CATS)) {
  for (const [sub, sc] of Object.entries(cfg.subs)) {
    FLAT.set(sc.slug, { type: "sub", top, sub, slug: sc.slug, count: sc.count });
  }
}
for (const [slug, t] of Object.entries(THEMES)) {
  FLAT.set(slug, { type: "theme", top: null, sub: null, slug,
                   count: t.count, themeName: t.name });
}

/* ---------------- 工具 ---------------- */
function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      // 动态随机响应不能被浏览器复用，否则连续调用会拿到同一条。
      "Cache-Control": "no-store",
      ...extra,
    },
  });
}

function text(s, status = 200) {
  return new Response(s, {
    status,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/* ---------------- 摘句模式：按自然句序切成连续两句 ---------------- */
const NOTE_RE = /（[^）]{0,300}）/g;
const CLAUSE_RE = /[^，。！？；：,.!?;:]+[，。！？；：,.!?;:]*/g;

/** 每首按标点切成小句，再顺序两两成组；奇数末句与上一句组成最后一组，
 *  因此任何一句都不会被丢掉，也不会只显示开头两句。 */
function twoLineUnits(paragraphs) {
  const clauses = [];
  for (const para of paragraphs || []) {
    const s = String(para).replace(NOTE_RE, "").trim();
    if (!s) continue;
    for (const part of s.match(CLAUSE_RE) || []) {
      const c = part.trim();
      if (c) clauses.push(c);
    }
  }
  if (!clauses.length) return [];
  if (clauses.length === 1) return [[clauses[0]]];
  const units = [];
  for (let i = 0; i + 1 < clauses.length; i += 2) {
    units.push([clauses[i], clauses[i + 1]]);
  }
  if (clauses.length % 2) {
    units.push([clauses[clauses.length - 2], clauses[clauses.length - 1]]);
  }
  return units;
}

/** 随机取一个两句片段；分类、全文和其他标签均保持不变。 */
function applyLine(item, r) {
  const units = twoLineUnits(r[6]);
  if (!units.length) return false;
  const i = Math.floor(Math.random() * units.length);
  item.content = units[i].join("");
  item.lines = units[i];
  item.line_index = i + 1;
  item.line_total = units.length;
  item.mode = "line";
  // origin 是 v1 字段名；title 与 poem 提供更直观的摘句出处。
  item.title = item.origin;
  item.poem = {
    id: item.id,
    title: item.origin,
    author: item.author,
    dynasty: item.dynasty,
    section: item.section,
    form: item.form,
    category: item.category,
    categories: item.categories,
    theme: item.tags.theme,
    style: item.tags.style,
    mood: item.tags.mood,
    scene: item.tags.scene,
    theme_conf: item.tags.theme_conf,
    theme_alt: item.tags.theme_alt,
    tags: item.tags,
  };
  return true;
}


function svgOf(content, author, title) {
  const lines = [];
  for (let i = 0; i < content.length; i += 14) lines.push(content.slice(i, i + 14));
  if (!lines.length) lines.push("");
  const h = 60 + 30 * lines.length;
  const body = lines.map((l, i) =>
    `<text x="24" y="${56 + 30 * i}" font-size="20" fill="#26221e" ` +
    `font-family="Songti SC,SimSun,serif">${esc(l)}</text>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="420" height="${h}" ` +
    `viewBox="0 0 420 ${h}"><rect width="420" height="${h}" fill="#f7f4ee"/>${body}` +
    `<text x="24" y="${h - 16}" font-size="12" fill="#8c8378">` +
    `${esc(author)}《${esc(title)}》</text></svg>`;
}

/* ---------------- 取数 ---------------- */
/** 分类下标所在的目录：大类用 _all，小类用自身 slug，均按大类分目录避免 slug 重名 */
function idxBase(node) {
  if (node.type === "theme") return `t/${node.slug}`;
  const topSlug = CATS[node.top].slug;
  return node.type === "top" ? `${topSlug}/_all` : `${topSlug}/${node.slug}`;
}

function chunkOf(g) {
  let lo = 0, hi = POEM_CHUNKS.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (POEM_CHUNKS[mid] <= g) lo = mid; else hi = mid - 1;
  }
  return lo;
}

async function assetJSON(env, path, cache) {
  if (cache.has(path)) return cache.get(path);
  const res = await env.ASSETS.fetch(new Request(ASSET_ORIGIN + path));
  if (!res.ok) throw new Error(`asset ${path} -> ${res.status}`);
  const v = await res.json();
  cache.set(path, v);
  return v;
}

async function pickOne(env, node, cache) {
  const count = node.count;
  const pos = Math.floor(Math.random() * count);
  const k = Math.floor(pos / IDX_SHARD);
  const off = pos % IDX_SHARD;
  const idx = await assetJSON(env, `/data/i/${idxBase(node)}.${k}.json`, cache);
  const g = idx[off];
  const c = chunkOf(g);
  const arr = await assetJSON(env, `/data/p/${c}.json`, cache);
  return arr[g - POEM_CHUNKS[c]];
}

/** 紧凑数组 -> 对象。字段顺序与 tools/build_cf.py 的 compact() 一致。 */
function expand(r, preferTop, preferSub) {
  const cats = {};
  if (r[11]) {
    for (const part of r[11].split(";")) {
      const [t, subs] = part.split(":");
      if (t) cats[t] = subs ? subs.split(",").filter(Boolean) : [];
    }
  }
  const tops = Object.keys(cats);
  const top = preferTop && cats[preferTop] ? preferTop : (tops[0] || "其他");
  const subs = cats[top] || [];
  // 指定了小类就用它；只有按大类请求时才在命中项中随机选一个用于展示
  const sub = preferSub && subs.includes(preferSub)
    ? preferSub
    : (subs.length ? subs[Math.floor(Math.random() * subs.length)] : null);
  return {
    content: (r[6] && r[6][0]) || "",
    origin: r[1],
    author: r[2],
    category: sub ? `古诗文-${top}-${sub}` : `古诗文-${top}`,
    // ---- 扩展字段 ----
    id: r[0],
    dynasty: r[3],
    section: r[4],
    form: r[5],
    paragraphs: r[6],
    tags: {
      theme: r[7],
      style: r[8],
      mood: r[9],
      scene: r[10] ? r[10].split("/").filter(Boolean) : [],
      theme_conf: typeof r[12] === "number" ? r[12] : 0,
      theme_alt: r[13] ? r[13].split(",").filter(Boolean) : [],
    },
    categories: tops.flatMap((t) => (cats[t] || []).map((s) => `${t}/${s}`)),
  };
}

/* ---------------- 分类总表 ---------------- */
function catalog(url) {
  const base = `${url.protocol}//${url.host}`;
  const list = [{ 全部: `${base}/all` }];
  for (const [top, cfg] of Object.entries(CATS)) {
    list.push({ [top]: `${base}/${cfg.slug}` });
  }
  for (const [top, cfg] of Object.entries(CATS)) {
    for (const [sub, sc] of Object.entries(cfg.subs)) {
      list.push({ [`${top}-${sub}`]: `${base}/${sc.slug}` });
    }
  }
  for (const [slug, t] of Object.entries(THEMES)) {
    list.push({ [`题材-${t.name}`]: `${base}/${slug}` });
  }
  return {
    welcome: "欢迎使用诗词分类接口",
    "api-document": `${base}/doc　在链接后加 .json / .txt / .svg 得到不同格式；可加 ?n=5 取多条`,
    "line-mode": `${base}/line　随机连续两句，可接分类：/line/shanshui,lushan`,
    "multi-category": `${base}/shanshui,lushan,meihua　一个 slug 就是一个分类，逗号分隔即多选`,
    help: "数据源 ibycat/chinese-poetry，全文简体；80 个意象分类 + 21 个题材，全部一级路径",
    count: TOTAL,
    built_at: BUILT_AT,
    list,
  };
}

/* ---------------- 主入口 ---------------- */
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
          "Access-Control-Allow-Headers": "Content-Type",
          "Access-Control-Max-Age": "86400",
        },
      });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ status: "error", errCode: 1001,
        errMessage: "Only GET and HEAD are supported" }, 405,
        { Allow: "GET, HEAD, OPTIONS" });
    }
    if (!(await allowRequest(request, env))) {
      return json({ status: "error", errCode: 1004,
        errMessage: "Too many requests; max 60 requests per 60 seconds per IP" }, 429,
        { "Retry-After": "60", "X-RateLimit-Limit": "60/60s" });
    }

    let path;
    try {
      path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
    } catch {
      return json({ status: "error", errCode: 1002, errMessage: "Bad path" }, 400);
    }

    // 文档页由静态资源直接命中（public/index.html、public/doc.html），
    // 请求不会进入 Worker，也就不占免费版每日请求额度。
    // 下面这支分支只作为静态层异常时的兜底，返回时打标记便于排查。
    if (path === "" || path === "doc" || path === "index.html") {
      const file = path === "doc" ? "/doc.html" : "/index.html";
      const r = await env.ASSETS.fetch(new Request(ASSET_ORIGIN + file));
      const h = new Headers(r.headers);
      h.set("x-doc-fallback", "worker");
      return new Response(r.body, { status: r.status, headers: h });
    }
    if (path === "favicon.ico") return new Response(null, { status: 204 });

    // 分类总表
    if (path === "api") return json(catalog(url));

    // 扩展名
    let fmt = "json";
    for (const f of ["json", "txt", "svg"]) {
      if (path.endsWith("." + f)) { fmt = f; path = path.slice(0, -f.length - 1); break; }
    }

    // /line 或 /line/{分类}：摘句动作词
    let lineMode = false;
    if (path.toLowerCase() === "line" || path.toLowerCase().startsWith("line/")) {
      lineMode = true;
      path = path.slice(4).replace(/^\/+/, "");
    }

    const q = url.searchParams;
    let n = parseInt(q.get("n") || "1", 10);
    if (!Number.isFinite(n)) n = 1;
    n = Math.max(1, Math.min(n, 50));
    // 摘句需要携带结构化出处信息，因此 .txt 在摘句模式下也返回 JSON。
    // 普通分类的 .txt 仍是纯文本；.svg 仍返回图片。

    /* 路径关键词：逗号分隔的分类；all 表示全库
         /all            全库
         /a              单分类
         /a,b            多分类                                                        */
    let tokens = path.split(",").map((s) => s.trim()).filter(Boolean);
    if (tokens.length && tokens[0].toLowerCase() === "all") {
      if (tokens.length > 1) {
        return json({ status: "error", errCode: 2002,
          errMessage: "all 不能和分类一起写；多选直接用 /shanshui,lushan" }, 400);
      }
      tokens = [];
    }
    if (lineMode && fmt === "txt") fmt = "json";
    // 解析关键词：查不到就是写错了，不做模糊猜测
    let node;
    if (!tokens.length) {
      node = { type: "all", top: null, sub: null, slug: null, count: TOTAL };
    } else {
      const picked = new Map();
      for (const token of tokens) {
        const hit = FLAT.get(token.toLowerCase());
        if (!hit) {
          return json({ status: "error", errCode: 2002,
            errMessage: `未知分类：${token}；可用名字见 /api` }, 400);
        }
        picked.set(nodeKey(hit), hit);
      }
      if (picked.size > 20) {
        return json({ status: "error", errCode: 2002,
          errMessage: "一次最多 20 个分类" }, 400);
      }
      const list = [...picked.values()];
      node = list.length === 1 ? list[0] : {
        type: "multi", top: null, sub: null, slug: null,
        count: list.reduce((sum, x) => sum + x.count, 0), nodes: list,
      };
    }
    if (!node.count) {
      return json({ status: "error", errCode: 2001, errMessage: "Category is empty" }, 404);
    }
    // 分类请求每次抽样要读“下标 + 诗库”两个子请求；n=50 会超过 Cloudflare
    // 单次调用 50 个子请求的上限。全库路径只需读诗库，因此仍支持 50。
    const maxN = node.type === "all" ? 50 : 20;
    if (n > maxN) {
      return json({ status: "error", errCode: 1003,
        errMessage: `n is too large for categorized requests; max ${maxN}` }, 400);
    }

    try {
      const cache = new Map();
      if (node.type === "multi") {
        const picks = Math.min(n, node.count);
        const seen = new Set();
        const items = [];
        for (let attempt = 0; attempt < picks * 5 && items.length < picks; attempt++) {
          const chosen = node.nodes[Math.floor(Math.random() * node.nodes.length)];
          const r = await pickOne(env, chosen, cache);
          if (seen.has(r[0])) continue;
          const one = expand(r,
            chosen.type === "theme" ? null : chosen.top,
            chosen.type === "sub" ? chosen.sub : null);
          if (chosen.type === "theme") one.category = `古诗文-${chosen.themeName}`;
          if (lineMode && !applyLine(one, r)) continue;
          seen.add(r[0]);
          items.push(one);
        }
        if (!items.length) {
          return json({ status: "error", errCode: 1001,
            errMessage: "No readable lines in selected categories" }, 500);
        }
        if (fmt === "txt") return text(items.map((x) => x.content).join("\n"));
        if (fmt === "svg") return svg(items[0]);
        return json(n > 1 ? items : items[0]);
      }

      if (node.type === "all") {
        // 全库随机：直接用诗库下标，无需分类索引。
        // 摘句模式先随机整首，再在整首的句对中等概率取一联，
        // 避免长诗因为句数多而挤占其他作品的曝光。
        const picks = Math.min(n, TOTAL);
        const seen = new Set();
        const items = [];
        for (let attempt = 0; attempt < picks * 5 && items.length < picks; attempt++) {
          const g = Math.floor(Math.random() * TOTAL);
          const c = chunkOf(g);
          const arr = await assetJSON(env, `/data/p/${c}.json`, cache);
          const r = arr[g - POEM_CHUNKS[c]];
          if (seen.has(r[0])) continue;
          const one = expand(r, null, null);
          if (lineMode && !applyLine(one, r)) continue;
          seen.add(r[0]);
          items.push(one);
        }
        if (!items.length) {
          return json({ status: "error", errCode: 1001,
            errMessage: "No readable lines in corpus" }, 500);
        }
        if (fmt === "txt") return text(items.map((x) => x.content).join("\n"));
        if (fmt === "svg") return svg(items[0]);
        return json(n > 1 ? items : items[0]);
      }

      const picks = Math.min(n, node.count);
      const seen = new Set();
      const items = [];
      for (let i = 0; i < picks * 3 && items.length < picks; i++) {
        const r = await pickOne(env, node, cache);
        if (seen.has(r[0])) continue;
        // 按大类请求时也让 category 优先回该大类，避免出现「点了山水却标抒情」
        const it = expand(r, node.top, node.type === "sub" ? node.sub : null);
        // 按题材请求时，category 直接回题材名，不再用意象分类
        if (node.type === "theme") it.category = `古诗文-${node.themeName}`;
        if (lineMode && !applyLine(it, r)) continue;
        seen.add(r[0]);
        items.push(it);
      }

      if (fmt === "txt") return text(items.map((x) => x.content).join("\n"));
      if (fmt === "svg") return svg(items[0]);
      return json(n > 1 ? items : items[0]);
    } catch (e) {
      console.error("corpus read failed:", e && e.stack || e);
      return json({ status: "error", errCode: 1001,
        errMessage: "Internal error while reading corpus" }, 500);
    }
  },
};

export { twoLineUnits };

function svg(one) {
  return new Response(svgOf(one.content, one.author, one.origin), {
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
    },
  });
}
