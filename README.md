# 诗词分类与接口（chinese-poetry 扩展）

把 `chinese-poetry` 的全量语料做成**可分类调用的 API**：11 大类 / 69 小类 / 21 题材，
全部平铺成一级路径（`/shanshui`、`/lushan`、`/meihua`），全文转为简体。

**当前规模：571,189 首**，69 个小类无一为空。分部如下：

| 分部 | 首数 | 说明 |
|---|---:|---|
| 宋诗 | 250,826 | 全宋诗 |
| 明诗 | 224,235 | 明诗 |
| 唐诗 | 55,261 | 全唐诗 + 唐诗三百首 |
| 宋词 | 20,742 | 全宋词 + 宋词三百首 |
| 元曲 | 10,872 | 散曲、小令、套数 |
| 魏晋南北朝 | 7,008 | 建安以后至陈、北朝 |
| 隋 | 1,046 | 隋代诗 |
| 秦汉 | 331 | 汉诗，含曹操 |
| 诗经 | 305 | 风雅颂全篇 |
| 清词 | 256 | 纳兰性德 |
| 五代 | 242 | 花间集、南唐二主词（与全唐诗重出的已去重） |
| 楚辞 | 65 | 屈原及后学 |

宋诗、明诗、汉魏六朝与隋诗来自 [wujinpai/chinese-classics](https://github.com/wujinpai/chinese-classics)（MIT），
其余分部来自 `ibycat/chinese-poetry-api` 仓库。重复篇目已在构建时按首句去重。

---

## 一、快速开始

```bash
python -m tagger.build          # 构建语料（约 35 秒）
cd cloudflare && npx wrangler dev   # 本地跑接口（与线上同一份代码）
```

生产部署见下方「部署到 Cloudflare」。`server.py` 是早期的本地参考实现，
只覆盖意象分类、不含 21 题材，已不再维护——本地调试请用 `wrangler dev`。

```bash
curl https://yiyan.952762.xyz/lushan.json
curl https://yiyan.952762.xyz/aiqing.txt
curl https://yiyan.952762.xyz/meihua.json?n=3
curl https://yiyan.952762.xyz/shanshui,lushan.json
```

浏览器打开 `web/index.html` 可按分类浏览（本页为分层抽样，全库走接口）。

### 部署到 Cloudflare

接口层已适配 Cloudflare Workers，自带调用文档页：语料走 Static Assets（220 MB / 4086 文件），
Worker 只做路由、限流与随机抽取（约 27 KiB，gzip 9 KiB），因此不受 1 秒冷启动限制。

```bash
cd cloudflare
npm install
python ../tools/build_cf.py     # 生成分片与文档页
npx wrangler login
npx wrangler deploy
```

### 无头运行（NAS / 服务器）

整条流水线不依赖编辑器，装好依赖、配好 `.env` 即可夜间跑：

```bash
git clone https://github.com/ibycat/chinese-poetry-api.git && cd chinese-poetry-api
pip install -r requirements.txt        # OpenCC；没有也能跑，会退回内置字表
cp .env.example .env                   # 填 LLM_BASE_URL / LLM_API_KEY / CLOUDFLARE_API_TOKEN
bash scripts/nightly.sh --fetch        # 抓语料 -> 构建 -> 生成产物 -> 部署 -> 自检
```

- 只构建不部署：`bash scripts/nightly.sh --no-deploy`（NAS 上没有 Node 时用，产物留在 `cloudflare/public`）
- 日志写在 `logs/nightly-<时间>.log`，退出码非 0 表示有步骤失败
- 部署只需 `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`，不需要交互式 `wrangler login`
- 模型标注默认读 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，没有才回退到 `~/.workbuddy/models.json`
- 定时示例：`30 2 * * * cd /vol1/chinese-poetry-api && bash scripts/nightly.sh --fetch >> logs/cron.log 2>&1`

服务器需要能访问 `api.github.com`、`cdn.jsdelivr.net`、`api.cloudflare.com` 和模型网关；
需要代理时在 `.env` 里设 `HTTPS_PROXY`。

部署后：`/` 是调用文档，`/api` 是分类总表，`/lushan.json`、`/shanshui,lushan.json` 等一级路径可直接调用。
详见 [cloudflare/README.md](cloudflare/README.md)。

---

## 二、接口

### 路由（扁平，无层级）

| 路由 | 说明 |
|---|---|
| `/` `/doc` | 调用文档页（纯静态，不经过 Worker，不计调用额度） |
| `/api` | 分类总表，含每个分类的调用地址 |
| `/all` | 全部随机一首 |
| `/shanshui` | 大类：山水随机一首 |
| `/lushan` | 小类：庐山，不必带父级 |
| `/meihua` | 小类 slug，全局唯一 |
| `/shanshui,lushan` | 多选，逗号分隔，等概率各占一份 |
| `/line` | 全库随机返回连续两句 |
| `/line/songbie,jizeng` | 在指定分类内摘句 |

后缀 `.json`（默认）/ `.txt` / `.svg`。唯一参数 `?n=5` 一次取多条：
全库最多 50，分类与多选最多 20。动态响应使用 `Cache-Control: no-store`。
接口按客户端 IP 限流：同一 IP 每 60 秒最多 60 次，平均约每秒 1 次，允许短时突发；
使用 Cloudflare 原生近似限流，按机房分别计数。超限返回 `429 / 1004` 和 `Retry-After: 60`。

摘句：`/line` 或 `/line/{分类}`，`content` 改为随机连续两句，分类和其他字段不变。
长诗、长词会按自然句序切成多个句对，因此每一句都有机会出现，不再只显示开头两句。

```text
/shanshui,lushan.json?n=3
/songbie,shanshui,youxian.json
/line/songbie,jizeng.json
```

多选先等概率选一个分类，再在该分类内选作品与句对，语料多的分类不会挤占语料少的分类。
全部 101 个 slug 全局唯一，路径只用 ASCII；题材为避开重名取了近义词，见 3.1。

摘句额外返回 `title`、`lines`、`line_index`、`line_total` 和 `poem` 基本信息块。
摘句模式统一返回 JSON，即使 URL 写成 `.txt` 也返回 JSON；普通分类的 `.txt` 仍只返回正文。

### 返回体

返回体前四个字段是 `content` / `origin` / `author` / `category`，其后为扩展字段：

```json
{
  "content": "庐山小隐。",
  "origin": "尾犯",
  "author": "晁补之",
  "category": "古诗文-山水-庐山",
  "id": "cp1a2b3c4d5e6f",
  "dynasty": "宋",
  "section": "宋词",
  "form": "词",
  "paragraphs": ["庐山小隐。", "..."],
  "tags": { "theme": "山水田园", "style": "冲淡", "mood": "恬淡", "scene": ["山"] },
  "categories": ["山水/庐山", "生活/田园"]
}
```

`content` 默认为首句，全文在 `paragraphs`。
摘句模式另返回 `mode: "line"`、`lines`（组成该联的两句）、`line_index`、`line_total`。

---

## 三、分类体系

两套并存，回答不同问题：**意象分类**（诗里写到什么）与**题材分类**（这首诗在写什么事）。

### 3.1 题材（21 类，与意象分类同级）

按语料实际构成重定的主旨分类。相对原 19 类做了三处调整：
送别赠答拆成「送别」与「寄赠酬答」（前者场景是离别，后者是往还唱和）、
哲理悟道拆出「佛道禅颂」（全唐诗佛偈量大且判法不同）、应制酬和更名「应制干谒」（收进贺官与干谒求荐）。

| 题材 | 路径 | 条数 | 题材 | 路径 | 条数 |
|---|---|---|---|---|---|
| 山水田园 | `/tianyuan` | 109,143 | 节令风俗 | `/jieling` | 16,655 |
| 送别 | `/songbie` | 70,872 | 题画咏艺 | `/tihua` | 16,237 |
| 寄赠酬答 | `/jizeng` | 53,249 | 佛道禅颂 | `/fodao` | 16,143 |
| 咏怀抒感 | `/yonghuai` | 47,774 | 闺情宫怨 | `/guiqing` | 13,597 |
| 羁旅思乡 | `/jilv` | 40,695 | 游仙梦幻 | `/youxian` | 12,238 |
| 咏物 | `/yongwu` | 29,732 | 应制干谒 | `/yingzhi` | 10,055 |
| 闲适隐逸 | `/xianshi` | 26,638 | 民生疾苦 | `/minsheng` | 7,878 |
| 咏史怀古 | `/yongshi` | 24,995 | 亲情 | `/qinqing` | 5,191 |
| 边塞征战 | `/bianzai` | 23,888 | 爱情相思 | `/xiangsi` | 4,925 |
| 悼亡挽歌 | `/wange` | 19,964 | 讽喻刺世 | `/fengyu` | 2,292 |
| 哲理悟道 | `/wudao` | 19,028 | | | |

这四个题材改过 slug，是为了与意象分类不重复、路径全 ASCII：
山水田园→`tianyuan`、爱情相思→`xiangsi`（相思）、悼亡挽歌→`wange`（挽歌）、哲理悟道→`wudao`（悟道）。
荔枝的 slug 也调整为 `lychee`，避免与「励志」的 `lizhi` 重名。

### 3.2 意象分类（11 大类 / 69 小类）

slug 全部 ASCII 且彼此唯一，路径在根下：

| 大类 | 小类 |
|---|---|
| 抒情 | 爱情 友情 离别 思念 思乡 伤感 孤独 闺怨 悼亡 怀古 爱国 感恩 |
| 四季 | 春天 夏天 秋天 冬天 |
| 山水 | 庐山 泰山 江河 长江 黄河 西湖 瀑布 |
| 天气 | 写风 写云 写雨 写雪 彩虹 太阳 月亮 星星 |
| 人物 | 女子 父亲 母亲 老师 儿童 |
| 人生 | 励志 哲理 青春 时光 梦想 读书 战争 |
| 生活 | 乡村 田野 边塞 写桥 |
| 节日 | 春节 元宵节 寒食节 清明节 端午节 七夕节 中秋节 重阳节 |
| 动物 | 写鸟 写马 写猫 |
| 植物 | 梅花 梨花 桃花 荷花 菊花 柳树 叶子 竹子 |
| 食物 | 写酒 写茶 荔枝 |

一首诗可以同时属于多个小类（如《庐山得元侍御书》同时属 `山水/庐山`、`抒情/友情`），接口的 `category` 字段在命中的小类中随机取一个，全部命中的在 `categories`。

---

## 四、两层标签的分工

分类体系之外，另有一套按内容属性划分的五维标签（21 题材 / 10 风格 / 8 情绪 / 24 场景 / 13 体裁），两者用途不同：

| | 分类体系 | 五维标签 |
|---|---|---|
| 回答的问题 | 用户**想找什么**（写雨、庐山、荔枝） | 这首诗**是什么**（边塞、豪放、慷慨） |
| 颗粒度 | 检索维度，可重叠 | 题材/风格/情绪单归属，一主多副 |
| 用途 | 随机推荐、按类浏览 | 结构化分析、个性化分组 |

接口返回同时带两者，页面侧栏以分类为主导航，详情里展示五维归属。

---

## 五、判定口径

意象分类采用规则 + 阈值的可解释判定，全部代码在 `tagger/categories.py`；21 题材则采用
「高置信规则 + DeepSeek 弱类重判 + 低置信终审」的混合策略。意象规则形如：

```python
"庐山": {"kw": {"庐山": 1.5, "匡庐": 1.5, "香炉峰": 1.5}, "min": 1.0}
```

- **kw** 关键词权重，标题命中 ×2；**theme/scene/mood** 可复用五维标签；**min** 触发阈值
- 越宽泛的词权重越低（风 0.5、月 0.7），必须组合才够分，否则「写风」会命中六成以上的诗
- **title_required**：植物/动物/食物共 13 条规则要求关键词出现在标题。因为「梅」「柳」「桃」在唐诗里绝大多数是随手取景，正文命中会把咏物类淹掉（梅花从 4,349 条降到 384 条，才是真正的咏梅诗）

21 题材在 195 首人工基准上：关键词规则 83.6%，DeepSeek 单模 76.4%，混合策略 **87.2%**。
混合策略只对经抽查验证的弱类使用模型结果，其余保留高置信规则，避免全量替换造成回退。

### 已修掉的典型误判

| 现象 | 根因 | 处理 |
|---|---|---|
| 《芍药》被判「荔枝」 | 「薜**荔**」含单字荔 | 删单字「荔」 |
| 《日东病僧》被判「端午节」 | 「**艾**」既是艾草也触发端午 | 改为「艾草」，场景同步改 |
| 《题王母庙》被判「母亲」 | 「王**母**」是神祇 | 删单字「母」 |
| 《题**禅师**兰若》被判「老师」 | 「禅**师**」不是教师 | 删单字「师」 |
| 《汴河》被判「端午节」 | 隋炀帝「**龙舟**」非竞渡 | 龙舟降权至 1.0 |
| 友情与离别返回同一批诗 | 都取「送别赠答」整个题材 | 题材降为弱先验（0.3），靠各自专属词区分 |
| 田园占全库 28% | 等同「山水田园」题材 | 题材降为 0.35，必须有农事词 |

---

## 六、繁转简

全文（标题、作者、正文）转为简体，使用 opencc 词级转换，能正确处理「髮/發」「乾/干」这类语境歧义：

- 转换发生在**构建期**，产物已固化简体，运行时不依赖 opencc
- 无 opencc 时自动退回内置 3,751 字单字映射表
- 原始繁体保留在 `raw/` 中，未被修改

---

## 七、目录

```
cp-tags/
├─ server.py            接口服务（零第三方依赖，Python 3.8+）
├─ tagger/
│  ├─ categories.py     69 个意象小类的规则表 + slug 对照
│  ├─ themes21.py       21 题材定义与 19→21 映射
│  ├─ classify.py       分类匹配（关键词分桶扫描 + 阈值判定）
│  ├─ taxonomy.py       五维受控词表
│  ├─ lexicon.py        714 条意象词典
│  ├─ legacy.py         旧标签迁移表
│  ├─ engine.py         五维打标引擎
│  ├─ simp.py           繁转简
│  ├─ build.py          构建入口
│  └─ t2s.json          内置繁简单字映射
├─ cloudflare/          Workers 部署（生产环境）
│  ├─ src/index.js      路由与随机抽取
│  ├─ template/doc.html 调用文档页模板
│  └─ public/           生成的静态资源（31 MB / 626 文件）
├─ tools/
│  ├─ fetch_full.py     全量语料下载（走 jsDelivr 镜像）
│  ├─ build_cf.py       生成 Cloudflare 分片与文档页
│  ├─ llm_tag.py        题材语义标注与分歧终审（支持断点续跑）
│  ├─ eval_theme.py     题材精度基准
│  └─ eval_llm.py       LLM 与引擎的对比评估
├─ data/
│  ├─ poems.jsonl       全量简体语料，一行一首（接口唯一数据源）
│  └─ index.json        分类计数 + 词表 + 统计
├─ tags/                三百首选集的标签层，与源数组同下标一一对应
├─ web/                 浏览页
└─ raw/                 原始数据（繁体）
```

---

## 八、构建与扩展

```bash
python tools/fetch_full.py all      # 下载全量（约 31 MB，80 个分片）
python -m tagger.build              # 构建，实测 34 秒
python -m tagger.build --only tang300   # 只构建某一部
python -m tagger.build --sample 100     # 调整浏览页每类抽样条数
```

接入新数据集只需在 `tagger/build.py` 的 `DATASETS` 登记：

```python
{"key": "songshi", "kind": "dir", "path": "raw/full/songshi",
 "name": "全宋诗", "section": "宋诗", "dynasty": "宋"}
```

全宋诗（约 26 万首）尚未纳入，因其体量大且与唐诗高度重复，按需开启即可——引擎吞吐约 8,500 首/秒，26 万首约 1 分钟。

---

## 九、已知局限

1. **关键词法必然有噪声**。除已修的同形歧义外，「芙蓉」（水芙蓉/木芙蓉）、「雪」（可指白发、洁白）等仍有误判。每首诗都在 `cats` 里给出命中来源，便于核查。
2. **部分分类天然偏宽**。「春天」占 20%、「抒情」占 55%，因为唐诗本身「春」「愁」高频，不是规则错误；若要更严格，"写春天"需改为标题驱动。
3. **分类是检索维度，不是文学断代**。一首诗可同时属于「山水/庐山」和「抒情/友情」，这不矛盾，因为回答的是"能不能被这样找到"。
4. **全宋诗未纳入**，宋诗中的写猫、写茶、荔枝类样本会偏少（写猫仅 5 首）。
5. **词牌不是标题**：宋词的"标题"是曲调名，早期版本让《水调歌头》《临江仙》因「水」「仙」
   被判成山水、游仙，影响 37.6% 的宋词（7,789 首）。现已对词一律停用标题规则，词牌只经
   `RHYTHMIC_PRIOR` 影响声情。
6. 五维标签中「排律」判定不精确：五古十六句与五排十六句字数句数相同，差别在对仗，需平仄数据（仓库 `strains/` 有）。

---

## 十、许可

分类规则、标签层与接口代码为新增内容，沿用原仓库 MIT 许可。诗词文本版权与来源遵循原仓库说明。
