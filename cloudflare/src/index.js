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

/* ---------------- 索引：slug / 中文名 -> 节点 ---------------- */
const BY_SLUG = Object.create(null);
const BY_NAME = Object.create(null);
const SUB_NAMES = Object.create(null);

for (const [top, cfg] of Object.entries(CATS)) {
  const node = { type: "top", top, sub: null, slug: cfg.slug, count: cfg.count };
  BY_SLUG[cfg.slug] = node;
  BY_NAME[top] = node;
  for (const [sub, sc] of Object.entries(cfg.subs)) {
    const sn = { type: "sub", top, sub, slug: sc.slug, count: sc.count };
    BY_SLUG[sc.slug] = sn;
    SUB_NAMES[sub] = SUB_NAMES[sub] || [];
    SUB_NAMES[sub].push(sn);
  }
}
const BY_THEME = Object.create(null);
const THEME_BY_NAME = Object.create(null);
for (const [slug, t] of Object.entries(THEMES)) {
  BY_THEME[slug] = t;
  THEME_BY_NAME[t.name] = slug;
}

// 二级中文名唯一时可直接用 /爱情 访问，重名则必须带大类
for (const [sub, list] of Object.entries(SUB_NAMES)) {
  if (list.length === 1) BY_NAME[sub] = list[0];
}

/* ---------------- 工具 ---------------- */
function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=30",
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
      "Cache-Control": "public, max-age=30",
    },
  });
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

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
  const base = `//${url.host}`;
  const list = [{ 全部: `${base}/all` }];
  for (const [top, cfg] of Object.entries(CATS)) list.push({ [top]: `${base}/${cfg.slug}` });
  for (const [top, cfg] of Object.entries(CATS)) {
    for (const [sub, sc] of Object.entries(cfg.subs)) {
      list.push({ [`${top}-${sub}`]: `${base}/${cfg.slug}/${sc.slug}` });
    }
  }
  for (const [slug, t] of Object.entries(THEMES)) {
    list.push({ [`题材-${t.name}`]: `${base}/t/${slug}` });
  }
  return {
    welcome: "欢迎使用诗词分类接口（分类体系兼容今日诗词 v1）",
    "api-document": `${base}/doc　在链接后加 .json / .txt / .svg 得到不同格式；可加 ?n=5 取多条`,
    help: "数据源 ibycat/chinese-poetry，全文简体；分类体系与 v1.jinrishici.com 一致",
    count: TOTAL,
    built_at: BUILT_AT,
    list,
  };
}

/* ---------------- 主入口 ---------------- */
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ status: "error", errCode: 1001, errMessage: "Only GET is supported" }, 405);
    }

    let path;
    try {
      path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, "");
    } catch {
      return json({ status: "error", errCode: 1002, errMessage: "Bad path" }, 400);
    }

    // 文档页 / 浏览页（静态资源兜底）
    if (path === "" || path === "doc" || path === "docs" || path === "index.html") {
      return env.ASSETS.fetch(new Request(ASSET_ORIGIN + "/index.html"));
    }
    if (path === "favicon.ico") return new Response(null, { status: 204 });

    // 分类总表
    if (path === "api") return json(catalog(url));
    if (path.startsWith("api/")) path = path.slice(4);

    // 扩展名
    let fmt = "json";
    for (const f of ["json", "txt", "svg"]) {
      if (path.endsWith("." + f)) { fmt = f; path = path.slice(0, -f.length - 1); break; }
    }

    const q = url.searchParams;
    let n = parseInt(q.get("n") || "1", 10);
    if (!Number.isFinite(n)) n = 1;
    n = Math.max(1, Math.min(n, 50));


    // 解析分类
    let node = null;
    if (path === "" || path === "all") {
      node = { type: "all", top: null, sub: null, slug: null, count: TOTAL };
    } else {
      const segs = path.split("/").filter(Boolean);
      if (segs[0] === "t" && segs[1]) {
        const key = THEME_BY_NAME[segs[1]] || segs[1];
        const th = BY_THEME[key];
        if (!th) {
          return json({ status: "error", errCode: 2001,
            errMessage: `Unknown theme: ${segs[1]}. See /doc` }, 404);
        }
        node = { type: "theme", top: null, sub: null, slug: key,
                 count: th.count, themeName: th.name };
      }
      node = node || BY_SLUG[segs[0]] || BY_NAME[segs[0]] || null;
      // 题材只有一级，不能再走"大类/小类"的解析
      if (node && node.type !== "theme" && segs.length > 1) {
        const cfg = CATS[node.top];
        if (!cfg) return json({ status: "error", errCode: 2001, errMessage: "Unknown category" }, 404);
        const hit = Object.entries(cfg.subs)
          .find(([s, sc]) => sc.slug === segs[1] || s === segs[1]);
        if (!hit) {
          return json({ status: "error", errCode: 2001,
            errMessage: `Unknown subcategory: ${segs[1]}` }, 404);
        }
        node = { type: "sub", top: node.top, sub: hit[0], slug: hit[1].slug, count: hit[1].count };
      }
    }

    if (!node) {
      return json({ status: "error", errCode: 2001,
        errMessage: "No matching category. See /doc for the full list." }, 404);
    }
    if (!node.count) {
      return json({ status: "error", errCode: 2001, errMessage: "Category is empty" }, 404);
    }

    try {
      const cache = new Map();
      if (node.type === "all") {
        // 全库随机：直接用诗库下标，无需分类索引
        const g = Math.floor(Math.random() * TOTAL);
        const c = chunkOf(g);
        const arr = await assetJSON(env, `/data/p/${c}.json`, cache);
        const one = expand(arr[g - POEM_CHUNKS[c]], null, null);
        if (fmt === "txt") return text(one.content);
        if (fmt === "svg") return svg(one);
        return json(one);
      }

      const picks = Math.min(n, node.count);
      const seen = new Set();
      const items = [];
      for (let i = 0; i < picks * 3 && items.length < picks; i++) {
        const r = await pickOne(env, node, cache);
        if (seen.has(r[0])) continue;
        const it = expand(r, node.type === "sub" ? node.top : null,
          node.type === "sub" ? node.sub : null);
        // 按题材请求时，category 直接回题材名，不再用意象分类
        if (node.type === "theme") it.category = `古诗文-${node.themeName}`;
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

function svg(one) {
  return new Response(svgOf(one.content, one.author, one.origin), {
    headers: {
      "Content-Type": "image/svg+xml; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "public, max-age=30",
    },
  });
}
