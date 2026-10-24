# 03 过滤与质量子系统审计

> 范围：**过滤引擎 + 质量诊断**。覆盖 `filter_relevant_articles`（规则过滤 + LLM 语义复核）、
> 上游 `merge_raw_articles` 硬日期门（filter 的准入门）、以及质量诊断双报告
> `quality_funnel_report`（漏斗）与 `source_health_report`（来源健康）、下游 `enrich_cleaned_fulltext`
>（cleaned 全文补抓）与 `evergreen_candidates`（高价值旧文转知识库）。
> 本文为**只读审计**，逐层引用真实文件路径与实际运行产物。数据基准：`data/raw|cleaned|rejected|logs` 近期实际产物
>（2026-10-05 ~ 2026-10-11、2026-08-08~08-11），并与姊妹审计 `02_collection_pipeline.md`、`AUDIT_20260811_collection.md` 交叉印证。
> 只读，未改动任何仓库文件、未跑 git、未发邮件。

---

## 0. 定位与阶段

过滤引擎主脚本 `skills/filter_relevant_articles/filter_relevant_articles.py`（2192 行，版本迭代到 V6）。

在日报管线 `skills/run_daily_pipeline/run_daily_pipeline.py::STEP_ORDER`（L62-91）中的位置：

```
... → cloakbrowser_date_verify [L3] → filter → quality_funnel_report
→ source_health_report → tavily_gap_search → filter_merged
→ enrich_cleaned_fulltext → extract → temporal_fact_validation
→ novelty_check → score → analyze → evergreen_candidates → ...
```

- **filter**：核心相关性过滤（第 12 步，关键路径）。
- **filter_merged**：`tavily_gap_search` 补搜后对新增文章重过滤（第 16 步）。AUDIT 指出它会对全部文章**再跑一遍规则评分**（不重复 LLM，因 filter 结果已缓存），日志会出现两次"开始过滤"。
- **quality_funnel_report / source_health_report**：诊断步骤，非关键（`NON_CRITICAL_STEPS`）。
- **enrich_cleaned_fulltext / evergreen_candidates**：对 cleaned/reference/rejected 的下游增强，均非关键。

输入输出数据流：

| 阶段 | 读取 | 产出 |
|---|---|---|
| filter | `data/raw/{date}/raw_articles_all.json`（合并后）或 `raw_articles.json`（回退） | `data/cleaned/{date}/cleaned_articles.json`、`reference_articles.json`、`data/rejected/{date}/rejected_articles.json`、`data/logs/{date}/filter_relevant_articles.log` |
| funnel | raw_all + cleaned + reference + rejected | `data/logs/{date}/quality_funnel_report.{json,md}` |
| health | `raw_articles.json` + cleaned + reference + rejected | `data/logs/{date}/source_health_report.{json,md}` |
| enrich | cleaned_articles.json | 回写 cleaned 的 content 全文 |
| evergreen | reference + rejected | `data/evergreen/{date}/evergreen_candidates.json` |

---

## 1. 过滤目标与设计原则

设计目标体现在 `filter_relevant_articles.py` 的 docstring、SKILL.md 与各阶段硬规则中，可归纳为五条：

1. **只大陆大陆内容**：`classify_region_tag()`（L~500-580）判 `mainland`/`hk`/`tw`/`overseas`/`unknown`，地区非大陆在 `decide_final_pool()` Phase 0 直接 `reject`。"大陆强指标"词表让繁体字判定让位，避免大陆美妆稿引用港台品牌 slogan 被误杀。
2. **只即时零售 × 个护美妆**：关键词命中是准入门（`has_core_hit()` + 六类加分关键词，见 §2/§3）。排除词（`NEGATIVE_GENERAL/TOPIC/JUNK_KEYWORDS`）把资本市场、汽车/房产/游戏/chip/AI、招聘/公益/导航页等拒掉。
3. **无关键词命中即拒**：`decide_final_pool()` review 分支 `if not core_hit and not matched_keywords: return "reject"`——防 `source_tier + time` 分值虚高把无关文章送进去。
4. **少而精**：AUDIT 反复强调"采集 100 → cleaned 8-11 → 20 事件 → 8 可报告"的核心矛盾，过滤就是要把 cleaned 池压到个位数高质量。
5. **强信号规则**：核心命中快通道（`has_core_hit`）仅对**屈臣氏 / 核心平台 / 竞对核心**开放；美妆弱词需上下文佐证；review 分 3-5 需"当日 + core hit"才升 main；LLM 仅复核边界（`borderline_only` 默认）而非常量。

> ⚠️ **版本差异说明（重要）**：任务描述中的"A/B/C 三级 + `keep_primary`/`keep_secondary_limit`/`reference_only`/`reject` 四决策"是 **source_health_report** 的语言（那里是 A/B/C/D 四级 + 四类建议），**并不是 filter 的分池语言**。filter 当前实际输出三类池：
> `main`（→cleaned 文件）、`reference`（→reference 文件）、`reject`（→rejected 文件）。A/B/C 分级不存在于 `filter_relevant_articles.py`，它属于来源健康评级表（见 §5）。下文如实按代码实现记录。

---

## 2. 过滤主流程各阶段

### 2.0 输入规范化（Phase 1 开头）

`filter_relevant_articles` 主函数先对每篇做字段类型防御（L~1788）：
`title/summary/description/content` 若为 list 转成空格连接的 str；缺失/非 str 转 str。
然后统计 `by_time_status`/`by_freshness`/`by_source` 及 `all_matched_keywords`。

### 2.1 阶段 0：噪音硬拦截（`decide_final_pool` Phase 0，V4）

按顺序判定（L~1460-1490）：

- **page_type 阻断**：`classify_page_type()` 分为 `news|article|report|official_notice|product_page|homepage|social_post|promotion|job|unknown`。其中
  `CLEANED_BLOCKED_PAGE_TYPES = {product_page, homepage, social_post, promotion, job, unknown}`（L~288-298）不得进 cleaned。
  `page_type` 判定依据：社媒域名（`SOCIAL_MEDIA_DOMAINS`，L~301）、产品/促销 URL 模式（`PRODUCT_URL_PATTERNS`，L~310，含 `smzdm` 比价聚合页）、品牌官网域名根路径（`HOMEPAGE_DOMAINS`，L~327，屈臣氏/丝芙兰/万宁各站）、促销标题关键词（`PROMOTION_KEYWORDS` + promo_title_patterns）、招聘词、官方公告/报告标题正则。
- **地区闸门**：`region_tag` ∈ {hk, tw, overseas} → 直接 `reject`（`"噪音拦截: 非大陆地区 region=..."`）。`REGION_HK/TW/OVERSEAS/UNKNOWN` 常量在 L~299-300。判定逻辑 `classify_region_tag()`：
  - `.hk/.com.hk/hongkong/mingpao/hk01...` 域名 → HK；`.tw/.com.tw/taiwan/台灣/台湾` → TW；`.sg/.my/.th/...` TLD → overseas。
  - 内容繁体字 ≥5 且无大陆强指标 → HK；`hk_tw_strong` 词（購物優惠、換購、檔期、銅板價、優惠碼...）命中 → HK。
  - **大陆强指标让位**：`_MAINLAND_STRONG`（"中国大陆/内地/即时零售/美团闪购/京东到家/淘宝闪购/饿了么/抖音小时达/前置仓/小时达/分钟达/天猫/拼多多/唯品会/得物/小红书"）命中任一个即视为大陆，繁体字判定一律让位。阈值取 5（避免误杀大陆美妆稿）。
  - 兜底：含中文 → mainland；否则 unknown。
- **hk_tw_watsons_promo** 噪音标记 → 直接 reject（港台屈臣氏促销页）。
- **url/title 为空** → reject。
- **未来日期兜底**：`published_at` 晚于今天+1 天 → reject（merge 硬日期门是主拦截，此处 filter 兜底）。
- **内容农场黑名单**：`CONTENT_FARM_DOMAINS = {"kgblm.com"}`（sohu 已移除，因搜狐有原创），`CONTENT_FARM_SUFFIXES = []`。
- **季节错位**：`check_seasonal_mismatch()`——双11 仅允许 10/11/12/1 月、618 仅 5/6/7 月、双12 仅 11/12/1、年货节仅 1/2 月，违规 → reject（"旧闻改日期重发"）。
- **垃圾硬拒**：`NEGATIVE_JUNK_KEYWORDS` 命中且 `rule_score<=1` → reject。
- **大促时效**：`classify_campaign_temporality()`（V5 新增）按 `CAMPAIGN_CALENDAR`（38节/520/618/七夕/双11/双12/年货节各窗口）判 `current/upcoming/retrospective/historical/unknown_campaign_time`：
  - `historical_campaign` → reject（旧大促与当日情报无关）。
  - `unknown_campaign_time` + (unknown_time|old) → reject；+ (in_window|near_window) → reference。

### 2.2 关键词命中（`compute_rule_score` 加分，合并 title+summary+content 前 2000 字判断）

六类加分关键词，逐类**只加一次**（break）：

| 类别 | 词例（内置词典，`filter_relevant_articles.py` L~50-95） | 加分 |
|---|---|---|
| 屈臣氏直接命中 | 屈臣氏、Watsons、watsons | **+5** |
| 即时零售平台 | 美团闪购、京东秒送、京东到家、淘宝闪购、饿了么、抖音小时达、即时零售、小时达、同城零售、本地生活、前置仓、闪电仓、门店履约、大众点评、抖音本地生活、抖音本服、抖音团购、到店 | **+4** |
| 美妆个护品类 | 美妆、个护、护肤、彩妆、洗护、防晒、…、旅行装、小规格、凑品类、凑单品 | **+3**（列出前3个） |
| 竞对 | 丝芙兰、万宁、妍丽、WOW COLOUR、调色师、话梅、名创优品、KK集团、便利蜂、全家、711、罗森、美宜佳、盒马、永辉、大润发、山姆 | **+3** |
| B2C/To B 渠道 | 天猫官旗、天猫官方旗舰店、天猫超市、京东旗舰店、京东POP、京东自营 | **+2** |
| 经营变量 | 流量、转化率、客单价、复购、价格、补贴、毛利、货盘、SKU、履约、会员、私域、活动、投流、资源位 | **+1** |

额外：`matched_keywords >= 3` → +1；`source_tier=1` → +2、`tier=2` → +1（`_get_source_tier` 兼容 tier/source_tier 字符串映射）；`time_status`
`in_window` → +2、`near_window` → +1、`old` → 搜索源/allow_old 减 1、否则减 2；`freshness_status` `week_fallback` → -1、`bootstrap_seen` → -2、`newly_discovered` → +0（注释）。

> 注意：`config/keywords.yaml` 是**采集与搜索**侧的全量词典（platforms/competitors/categories/business_variables/scenarios，含"711"已加引号），
> 而 `filter` 的内置六类词典是运行时可独立于 keywords.yaml 的硬编码集（含 `"711"` 字符串）。两者是**两套词表**：keywords.yaml 用于 match_keywords/搜索，内置词典用于 compute_rule_score 打分。`keywords.yaml` 未直接作为 filter 的加减分源（score 用内置字典 + matched_keywords 计数加成）。

### 2.3 排除词与资本市场/财报/IPO 拦截（减分）

`NEGATIVE_GENERAL_KEYWORDS`（**-3**，L~1060-1075）：泛科技/泛财经/泛宏观 + 资本市场行情：
`宏观经济、GDP、CPI、联储、加息、降息、A股、港股、美股、IPO、独角兽、融资轮、涨停、跌停、涨超、跌超、个股、股价、股票、续创历史新高、板块异动、盘中、收盘、开盘、市值蒸发、市值突破、二级市场、券商研报`。
> 这组词覆盖任务描述的"涨停/个股/股价/研报/目标价/估值/盈利预测"等资本市场内容——**即使顺带提到即时零售/美妆也会被 -3**；配合 rule_score 门槛，这类"财经研报 + 顺带提即时零售"的文章难以进 cleaned（参考 10-11 funnel 中`【风口研报·公司】`被 reject/reference 的实例）。

`NEGATIVE_TOPIC_KEYWORDS`（-3）：汽车、新能源车、电动车、房产、楼市、房价、游戏、手游、芯片、半导体、大模型、AI大模型、农业、国际政治、医药审批、临床试验。
`NEGATIVE_JUNK_KEYWORDS`（-5）：招聘、求职、简历、公益、慈善、投诉电话、免责声明、导航页、网站地图、版权所有、备案号。

另：`title 或 url 为空` → -5；`content+summary 极短且无关键词`（content<50 且 summary<50 且无 matched_keywords）→ -2。
所有减分项均 break 只扣一次（取该组命中的第一个）。

### 2.4 公司财报/IPO 语义（无独立硬规则，靠关键词负分 + 页面类型 + LLM 语义）

代码中**没有**独立的"公司财报/IPO"硬拒绝分支；它通过三条路径被挡：
1. `NEGATIVE_GENERAL_KEYWORDS` 中 `IPO/融资轮/券商研报/涨停/股价/市值/财报相关词` 的 **-3** 负分，把低相关度的财报公告打到 review 以下。
2. `classify_page_type()` 对 `report`（`(?:行业|市场|发展|产业).*(?:报告|白皮书|蓝皮书|研究)`）等的分类 → 若同时命中噪音标签/非大陆 → 降级。
3. LLM 语义复核（`business_relevance_type=irrelevant/background` → reject/reference）。AUDIT §四也确认 score 阶段有"资本市场 → ARCHIVE"硬降级（`scoring.yaml` hard_downgrade），那是事件侧而非 filter 侧。

### 2.5 旧闻降级（回顾/复盘/时间线特征，V6 新增）

`detect_retrospective()`（L~200-260）返回 `strong/weak/none`：

- **strong**（标题强信号，`RETRO_STRONG_TITLE_PATTERNS`）：`回顾`、`复盘`、`时间线`、`本文由AI生成`、`(?:年终|年度|半年|季度|月度).{0,8}(?:盘点|总结|回顾|榜单)`、`盘点.{0,8}(?:年|季|月)`、`(?:一年|这一年|202\d年).{0,12}(?:回顾|总结|复盘)`。→ `core_hit 且 score>=5` 降级 reference（历史背景）；否则 reject。
- **weak**（标题弱信号 `RETRO_WEAK_TITLE_PATTERNS`：盘点/总结/对比/时间轴/大事记/回看/重温/梳理；或正文 `RETRO_BODY_PATTERNS`：本文由AI生成/AI生成/回顾/复盘/时间线/大事记/回看/盘点，取"摘要+正文前500字"）→ **一律降级 reference，绝不让进 main/cleaned**。

测试覆盖：`tests/test_v6_retro_future.py`（1a 强信号案例、1b 弱信号、1c 正文信号、1d 正常新闻不误杀）。funnel 10-11 实例：`从冲规模到保利润，美妆双11要打四场硬仗` 被 `噪音降级(page_type=product_page)`；`【九点特供】阿里财报...` 被 `回顾文(弱): 正文命中回顾特征[回顾]` 降到 reference。

### 2.6 时间未知处理（unknown_time / uncertain_date）

`decide_final_pool` 对 `time_status == "unknown_time"`（非 newly_discovered）：
- 有 core_hit → reference（无日期也保留）。
- 搜索源（`source_type ∈ {xcrawl, tavily, search, gap}`）且 `matched_keywords>=3 且 score>=3` → reference。
- 搜索源且 `score>=5` → reference。
- 其余 → **reject**（"time_status=unknown_time（无日期，无法确认时效性）"）。

`time_status == "uncertain_date"`（CloakBrowser 日期提取失败但采集端已滤 >3 天旧文）：
- 是 cloakbrowser（`collector=="cloakbrowser"` 或 `_source_file` 含"cloakbrowser"）且 core_hit 且 score>=3 → **main**（V5：CloakBrowser 是最可靠新鲜来源，核心命中直接进 main）。
- core_hit 且 score>=5 → main；core_hit（其余）→ reference；score>=4 → reference；否则 reject。

merge 端（`merge_raw_articles.py` L611-onwards）对 unknown_time/uncertain_date 的**放宽**保留：uncertain_date 属 cloakbrowser/tophub/BROAD_SOURCES 直接保留；unknown_time 属 MONITOR_SOURCES（source_url_monitor/web_monitor）保留。但 filter 端对 unknown_time 非搜索源仍会 reject——所以 web_monitor 产出"全 unknown_time → 参考/拒绝"，与 AUDIT P1-4 实测吻合。

### 2.7 内容为空/过薄处理

- `compute_rule_score`：content<50 且 summary<50 且无关键词 → -2。
- `compute_noise_flags`：`summary<20 且 title<15` → `thin_content` 标记。
- 但 `thin_content` **不等于**硬拒绝：10-11 cleaned 全量清单里就有噪音标记 `["thin_content"]` 的 cleaned 文章（`国货不是输了，是不玩了`），因为 CloakBrowser 源核心命中 `uncertain_date+core_beauty score>=3` 直接进 main。**过薄主要影响 rule_score 与 LLM 复核质量，不单独拦截**。
- 上游 merge 的硬日期门只卡日期不卡内容。

---

## 3. 优先级与分级（实际实现）

### 3.1 决策函数流

`make_rule_decision(rule_score)`（V3，L~1155）：`>=6 → keep`；`3-5 → review`；`<3 → reject`（V3 把 review 阈值从 2 提到 3，避免单关键词命中触发大量 LLM 调用）。

`decide_final_pool()` 按 `freshness_status` / `time_status` / `core_hit` / `rule_score` / `llm_result` 分层判定，返回 `main|reference|reject`。核心阈值（L~1490-1680）：

| freshness / 分支 | 条件 | 结果 |
|---|---|---|
| bootstrap_seen | core_hit & score≥5 / score≥6 / core_hit… | reference（历史背景） |
| bootstrap_seen | score 3-5 | reference |
| bootstrap_seen | score<3 | reject |
| newly_discovered (web_monitor) | core_hit & score≥4 | **main** |
| newly_discovered | score≥5 | reference（LLM 候选，需 LLM 确认可升 main） |
| newly_discovered + unknown_time | core_hit / (score≥2 & kw≥2) | reference |
| newly_discovered + unknown_time | 其余 | reject |
| newly_discovered | score≥3 | reference；否则 reject |
| week_fallback (Tavily 周补搜) | core_hit & score≥5 | **main** |
| week_fallback | score≥3 & (core_hit or kw) | reference；否则 reject |
| day_primary (Tavily 日搜) | score≥6 | **main** |
| day_primary | core_hit & score≥4 | **main**；否则落回通用规则 |
| uncertain_date | cloakbrowser & core & score≥3 / core & score≥5 | **main** |
| uncertain_date | core（其余）/ score≥4 | reference；否则 reject |
| unknown_time | core | reference；搜索源+kw≥3+score≥3 / score≥5 → reference；否则 reject |
| LLM 结果 | recommended main & score≥3 | **main**（_noise_downgrade 包裹） |
| LLM 结果 | recommended reference | reference |
| LLM 结果 | recommended reject & score≥6 | 降级 reference；否则 reject |
| 通用 keep | rule_score≥6 | **main** |
| 通用 review | core & score≥4 & in_window | **main** |
| 通用 review | 无 core 无 matched_keywords | **reject**（防虚高） |
| 通用 review | score≥5 & in_window | **main** |
| 通用 review | score≥4 / in_window / near_window | reference |
| 通用 review | score≥2 | reference(降级保留) |
| 通用 reject | in_window/near_window & score≥0 & (core or kw) | reference(降级) |
| 通用 reject | 默认 | reject |

所有返回 main/reference 的路径都经 `_noise_downgrade()` 包裹：若噪音命中（page_type 阻断）则 `main → reference`、`reference → 可能 reject`（social_post/homepage/job 直接 reject）。

### 3.2 核心命中 vs 一般信号（`has_core_hit`，L~265-300）

核心命中决定快通道，比普通加减分更"硬"：

- `CORE_WATSONS_KEYWORDS = [屈臣氏, Watsons, watsons]` → core_hit=watsons。
- `CORE_PLATFORM_KEYWORDS = [美团闪购, 京东秒送, 京东到家, 淘宝闪购, 饿了么, 抖音小时达, 即时零售, 前置仓, 闪电仓]` → core_hit=platform（**平台级强信号**）。
- 美妆词需**双重验证**：`CORE_BEAUTY_KEYWORDS`（大幅扩展版，含精华/面霜/口红/香水/成分词/玻尿酸/视黄醇/烟酰胺/纯净美妆/功效护肤/英文 skincare 等，L~115-142）。
  - 命中 `_BEAUTY_STRONG`（美妆/个护/护肤/彩妆/防晒/面膜/化妆品/skincare/cosmetics/makeup/beauty/丝芙兰/屈臣氏/万宁/妍丽）→ 直接通过；
  - 否则 ≥2 个美妆词 → 通过；
  - 单个弱信号词（如"抗衰""流量"）需要 `_BEAUTY_CONTEXT`（品牌/零售/电商/渠道/消费/市场/产品/门店/销售/增长/下滑/美容/化妆/个人护理）佐证才通过。
- `CORE_COMPETITOR_KEYWORDS = [丝芙兰, 万宁, 调色师, 话梅, 妍丽, 便利蜂, 全家, 711, 罗森]` → core_hit=competitor。

对比：**加分词典（§2.2）里的"即时零售平台/美妆品类词"范围更宽**（含"本地生活/大众点评/到店/旅行装"），而**核心命中词更窄**（只有 9 个平台词 + 屈臣氏/核心竞对）。core_hit 只用于快通道，普通加分仍能靠 `rule_score≥6` 进 main（如 `美团骑手进不了宿舍楼...` 的 rsshub_36kr_search_taobao_flash score=6 但被噪音降级）。

### 3.3 LLM 语义复核（Phase 2，`llm_review_article`）

- 触发：`llm_mode=="borderline_only"`（默认）时 `rule_decision=="review"` 的文章，或 `source_tier<=2 且 rule_score<=1` 的高 tier 低分文章；`llm_mode=="all"` 则全量。
- 上限：默认 `_FILTER_MAX_LLM=100`、`_FILTER_BATCH_SIZE=5`，从 `config/parallel.yaml::filter_relevant_articles.llm_review_parallel` 读取。超限时按 (tier 小优先, score 低优先) 截断优先级。
- Prompt 要求返回 JSON：`llm_relevance(high|medium|low|none)`、`business_relevance_type(direct|indirect|background|irrelevant)`、`recommended_pool(main|reference|reject)`、`confidence(high|medium|low)` 等。
- 模型：`config/model_router.yaml L36-39` `filter_relevant_articles: default: deepseek-v4-flash-free`；`temperature 0.2 / max_tokens 2048`（SKILL.md 记"deepseek-v4-flash-free（OpenCode）、6 个 Longcat Key round-robin"，与当前 model_router 一致于 deepseek-v4-flash-free）。
- 降级：LLM 不可用/JSON 解析失败/超时 → 该条 `llm_reviewed=False`，不回填，**单条失败不中断全局**，整体降级为纯规则。

---

## 4. quality_funnel_report 的计数语义

入口 `skills/quality_funnel_report/quality_funnel_report.py::quality_funnel_report()`（804 行）。

### 4.1 数据装载与 URL 归一化

读取四个文件：`raw_articles_all.json`、`cleaned_articles.json`、`reference_articles.json`、`rejected_articles.json`。
**池归属按 URL 归一化匹配**（`_normalize_url` = netloc+path，去尾斜杠/去 host 大小写），cleaned 优先级最高（同一 URL 同时在多池时记 cleaned）。raw 里找不到的在 processed 池 → `unprocessed`。
构建统一文章列表：raw_articles + 不在 raw 中的 processed 文章（后者的池读 `filter.final_pool`）。

### 4.2 `funnel_summary` 各字段（L~258-277，分母是 `total_in_funnel` = 统一列表长度）

| 字段 | 含义 |
|---|---|
| `raw_total` | raw_articles_all.json 里的原始数 |
| `processed_total` | cleaned + reference + rejected 三文件文章数之和 |
| `cleaned_total` | 统一列表中 `_final_pool == "cleaned"` 的个数（funnel_cleaned） |
| `reference_total` / `rejected_total` | 对应池个数 |
| `unprocessed_total` | raw 有但任何池都没收录的 URL 数 |
| `total_in_funnel` | `funnel_total` = 统一列表总长 |
| `cleaned_rate` | `cleaned / total_in_funnel * 100`（**百分比数**，如 9.59） |
| `reference_rate` / `reject_rate` / `unprocessed_rate` | 同理 |

> 注意：`cleaned_rate` 分母是 **unified list 长度**（含 unprocessed），不是 raw_total。若 unprocessed>0，`cleaned_rate` 会低于 cleaned/raw_total。

### 4.3 19 项统计维度（SKILL.md 详列）

1-10 简单维度：`by_collector`、`by_source_name`、`by_source_type`、`by_freshness_status`、`by_time_status`、`by_page_type`、`by_region_tag`、`by_content_status`、`by_rule_score_bucket`（≥10/8-9/6-7/3-5/<3/未评分）、`by_final_pool`。
11-13 交叉：collector×pool、collector×page_type、collector×freshness。
14-15 source_name×cleaned/rejected。16-17 top50 reference/rejected by rule_score。18 cleaned 全量清单。19 `suspected_missed_candidates`。

`_get_collector()`（L~85-108）：优先 `_source_category`（merge 分类，映射 rsshub → web_monitor → broad_search → xcrawl_enriched → tavily_gap → newly_discovered/web_monitor），否则 `raw_collector`/`source_type`。
`_get_content_status()`（L~126-140）：content>500 → rich；>100 → moderate；>0 → thin；summary>50 → summary_only；否则 empty。
`_get_freshness()`：缺失时按 collector 推断（web_monitor→newly_discovered、broad_search→broad_discovery、tavily_gap→tavily_gap、其他→rsshub_collect）。

### 4.4 自动诊断阈值（L~470-530）

- cleaned_rate < 5 → "漏斗严重堵塞"；<15 → "cleaned 率偏低"。
- `by_time_status["old"]/raw_total > 0.4` → "时效性是最大瓶颈，建议增加 filterout_days"。
- `unknown_time > raw_total*0.15` → "增强日期提取或降低 unknown_time 准入门槛"。
- 噪音 page_type（product_page/homepage/social_post/promotion/job）> raw_total*0.15 → "几乎不可能成为高质量内容"。
- 缺正文（empty+summary_only+thin）> raw_total*0.3 → "XCrawl 富化覆盖率待提升"。
- `suspected_missed_candidates`>0：`rule_score>=6` 且不在 cleaned、有效 freshness、page_type∈{news/article/report/official_notice/unknown}。
- collector×pool：某 collector 采集≥5 且 cleaned=0 → "该采集渠道可能需要优化关键词或过滤源配置"。

### 4.5 各采集器产出分布（实测 2026-10-11 funnel 报告）

- 漏斗：raw=73，processed=73，cleaned=7(9.59%)，reference=24(32.88%)，rejected=42(57.53%)，unprocessed=0。
- by_collector：rsshub=39、cloakbrowser_count=24、web_monitor=8、tavily_gap=1、broad_search=1。
- by_source_type：rsshub=39、cloakbrowser=24、web_monitor=8、tavily=1、xcrawl_broad=1。
- by_freshness_status：rsshub_collect=51、enriched=12、newly_discovered=8、day_primary=1、broad_discovery=1。
- by_time_status：in_window=28、uncertain_date=24、unknown_time=21（**unknown_time 占 28.8%**，命中诊断①）。
- by_page_type：news=40、product_page=21、article=11、unknown=1（**噪音 product_page 占 28.8%**，命中诊断②）。
- by_region_tag：mainland=67、unknown=6（**无 hk/tw/overseas → 地区闸门有效**）。
- by_content_status：rich=35、thin=15、moderate=14、empty=9（缺正文 24/73=32.9%，命中诊断③）。
- by_rule_score_bucket：6-7=1、3-5=11、<3=61（**绝大多数 <3**）。
- Collector×Final Pool：**cloakbrowser_count 产出全部 7 篇 cleaned 中的 6 篇**（6 cleaned/10 ref/8 rej），rsshub 1 cleaned/12 ref/26 rej，web_monitor 0/1/7（零 cleaned → 触发诊断④），tavily_gap 0/1/0，broad_search 0/0/1。

AUDIT（08-08~11）补充各通道事件贡献占比：cloakbrowser 59%、rsshub 17%、tavily 15%、xcrawl 8%、tophub 0%（验证码墙废）、web_monitor 0%、broad_search 0（只做线索发现）。

---

## 5. source_health_report 的评级与建议

入口 `skills/source_health_report/source_health_report.py`（490 行），**SKILL.md 明确"暂未集成到 run_daily_pipeline 主流程"**，是诊断工具。

### 5.1 统计字段（`compute_source_stats` L~108-177）

按 `source_name` 聚合 raw_articles.json，用 `article_id` 与 cleaned/reference/rejected 的索引交叉计数：
`total_count`、`in_window_count`、`near_window_count`、`old_count`、`unknown_time_count`、`matched_keyword_count`（有 matched_keywords 的文章数）、`cleaned_count`、`reference_count`、`rejected_count`。

三个比率（L~141-143）：
```
recent_ratio = (in_window + near_window) / total
old_ratio    = old / total
cleaned_yield_rate = cleaned_count / total
```
`top_titles`：in_window(2)/near_window(1)/其余(0) 优先、再按 rule_score 排序取前 5 标题。

### 5.2 分级算法（`classify_source` L~45-80）

| 级别 | 条件 | recommendation |
|---|---|---|
| 🟢 A | `recent_ratio>=0.3 且 cleaned_yield_rate>=0.1` | `keep_primary` |
| 🟡 B | `recent_ratio>=0.3 且 cleaned_yield_rate<0.1` | `keep_secondary_limit` |
| 🟠 C | `old_ratio>=0.8 且 matched_keyword_count>0` | `reference_only` |
| 🔴 D | `old_ratio>=0.8 且 matched_keyword_count==0` | `disable_candidate` |
| （兜底） | `old_ratio>=0.5` 的中间态 | C / reference_only |
| （兜底） | 其余（0.3>recent & old<0.5） | B / keep_secondary_limit |

> 注意实际兜底逻辑与 SKILL.md 略有出入：代码对 `total==0` 直接返回 D/disable_candidate；`old_ratio>=0.5` → C；否则 → B。A/B/C/D 阈值是**硬编码在源码**里（0.3/0.1/0.8/0.5），不读 yaml。

### 5.3 实际评级结果（实测 2026-10-11 source_health_report.md）

13 个源：**A=1，B=9，C=3，D=0**。

- **A 类（keep_primary）**：`rsshub_huxiu_search_store`（total=2，recent=100%，yield=50.0%）。
- **B 类（keep_secondary_limit）**：`rsshub_36kr_newsflashes`(20/0 cleaned)、`rsshub_36kr_search_beauty`(18, 61% recent, 0 cleaned)、`rsshub_36kr_search_instant_retail`(13, 31% recent)、`rsshub_36kr_search_meituan_flash`(4)、`rsshub_36kr_search_taobao_flash`(9)、`rsshub_huxiu_search_taobao_flash`(1)、`tavily_01/03/04`——**时效高但 cleaned_yield 全 0%**，是典型的"采到但滤掉"。
- **C 类（reference_only）**：`rsshub_36kr_search_jd_instant`(old=100%)、`rsshub_huxiu_search_beauty`(old=100%)、`tavily_02`(old=80%)——**全部为旧文但有关键词匹配**。
- **D 类：无**（10-11 无"过时且无关键词"源）。

附录明细确认：B 类源 `matched_kw` 都有值但 `cleaned=0`，说明这些搜索源文章"有关键词命中但 rule_score/噪音/时间被滤"，是过滤质量最值得调的一档。

### 5.4 历史降级/禁用（MEMORY.md + AUDIT 交叉）

- 2026-04-26：18 源 → A=0、B=1（虎嗅消费频道）、C=16、D=1（`rsshub_huxiu_article` 100% old & 0% 关键词，**唯一 D 类源**）。虎嗅搜索类源 100% 是 old（返回搜索结果非最新文章）。
- 2026-05-02 精简：禁用虎嗅消费频道、36kr 搜索系列、小红书搜索等 7 个 RSS 源；禁用 11 个低质量 XCrawl 查询。
- 2026-05-21：**禁用虎嗅 3 个全频道源**（0% 通过率纯噪音）；启用 36kr 快讯 + 3 个平台搜索源；broad_search 排除港台域名。
- 08-11 AUDIT：web_monitor 12 直连源全采全滤 → 建议"垂直源跳过标题级 CORE 过滤交给 filter 全文判断"（已修）；`config/sources.yaml` 中青眼/ C2CC 已禁；亿邦/联商/赢商受 JS/WAF/DNS 反爬实际产出受限。

---

## 6. 下游：enrich_cleaned_fulltext 与 evergreen_candidates

### 6.1 enrich_cleaned_fulltext（正文补抓）

`skills/enrich_cleaned_fulltext/enrich_cleaned_fulltext.py`：
- `MIN_CONTENT_LENGTH = 200`（中文字符），`MAX_ENRICH = 15`。
- 流程：读 cleaned_articles.json → 找 content 中文 <200 字且 URL 非空 → 最多补 15 篇 → 依次尝试 **CloakBrowser(Playwright, JS 渲染) → requests+BS4(静态) → XCrawl scrape API** → 回写 content 且标 `_fulltext_source="enrich_cleaned"`。每篇间 sleep 1.5s。
- 意义：解决下游 §7 的"RSSHub 片段只有 snippet 级内容"问题，把 cleaned 中缺全文的补成可提取事件的完整正文。

### 6.2 evergreen_candidates（高价值旧文转知识库）

`skills/evergreen_candidates/evergreen_candidates.py`：
- 入选条件（SKILL.md + 常量 L~50-63）：`time_status ∈ {old, unknown_time, week_fallback}`（`OLD_TIME_STATUSES`）、`rule_score >= 8`（`EVERGREEN_MIN_SCORE`）、`page_type ∈ {article, report, news, unknown}`（`VALID_PAGE_TYPES`）、非噪音（`NOISE_ROLES = {promo, product_listing, coupon, deal}`、`NOISE_PAGE_TYPES = {product, listing, promo, coupon, store_page}`、标题噪音词 `TITLE_NOISE_KEYWORDS`=[降价/打折/促销/特价/优惠券/秒杀/拼团/领券/满减/红包/抽奖/返现/福利/招聘/求职/简历/面试]、`content_status ∈ {thin, empty, paywall_only}` 排除）。
- `compute_evergreen_score`：base=rule_score；unknown_time -1、week_fallback +0.5；page_type report +1 / article +0.5；watsons_relevance>=4 +0.5；core_hit +1。
- 用途：周报/月报素材、低信号日解释背景、经营分析补充——**"旧文不污染日报，但保留价值"**，是"少而精+旧闻降级"哲学的下游补充。

---

## 7. 已知问题（filter/质量侧实测与代码证据）

1. **RSSHub 混入美股/财报/泛财经**：`rsshub_36kr_newsflashes` 返回大量无关通用新闻（68 篇/日，MEMORY 04-28 记录）与美股财报/券商研报（`【风口研报·公司】`、`【九点特供】阿里财报...` 在 funnel 10-11 被 reference/reject）。过滤靠 `NEGATIVE_GENERAL_KEYWORDS(-3)` + unknown_time/回顾文降级兜底，但 36kr 快讯这类仍大量进入 review 消耗 LLM。10-11 funnel 中 `rsshub_36kr_newsflashes`（20 条）rejected=16、reference=4，**0 cleaned**。
2. **时间 unknown 比例高**：10-11 unknown_time=21/73(28.8%) 且 uncertain_date=24/73(32.9%)，合计超 60% 无可靠日期 → 触发漏斗"unknown_time>15%"诊断。web_monitor 源产出全 unknown_time 被降级（AUDIT P1-4）。
3. **片段只有 100-227 字**：RSSHub 某些路由只给 `<description>` 摘要片段（100-227 字），36kr/快讯类常只有 snippet，需 enrich 补全文（`02_collection_pipeline.md` §2.1"片段限制"）。10-11 cleaned 的 `thin_content` 噪音标记正说明正文不足。
4. **AJAX 墙**：DoNews、第一财经、经济观察报、亿欧网、创业邦、投资界、极客公园、网经社、亿邦动力 9 站可访问但搜索结果 JS 动态渲染，需 API 逆向（`cloakbrowser_collect.py` L269-289 待激活注释）——这些站的正文/列表无法直接抓到，过滤只能拿到标题级 → 规则分低 → reference/reject。
5. **DNS 超时源**：CBO 化妆品财经、联商网等 CloakBrowser 站点 timeout/DNS 不可达（"已禁用（网络限制）"注释）；XCrawl run.xcrawl.com 频繁 ReadTimeout（08-11 7 源各 374s 空转，已修 timeout15s+retry1）；tophub 验证码墙已实质废弃。
6. **噪音 page_type 占比高**：10-11 product_page=21/73(28.8%)，多数来自 rsshub（16）+ cloakbrowser（5），这类几乎不可能进 cleaned。
7. **web_monitor 0 贡献**：10-11 web_monitor 采 8 条 0 cleaned（rejected=7, reference=1）——直连源标题短、无正文。
8. **filter 重复执行**：filter + filter_merged 两次全量规则评分（第二次无 LLM），AUDIT P2-6。
9. **采集词宽导致高分低质**：top_matched_keywords 显示"零售/即时零售/美团"宽泛命中多但内容无关（AUDIT P1-5），"无关键词命中即拒"只挡零命中，挡不住"命中宽泛词"。

---

## 8. 结论与要点

1. **过滤哲学是"强信号门槛 + 少而精"**：非大陆/噪音 page_type 硬拒，核心命中走快通道，review 边界文章才送 LLM（默认 borderline_only），最终 cleaned 收敛到个位数。
2. **地区闸门在 filter，日期闸门在 merge**（硬日期门 channel1d/search3d/broad7d + 未来日期拦截 + URL 交叉验证）——两者是 filter 准入门的上游，缺一不可。
3. **质量诊断双报告是"漏斗（为什么多但少） + 来源健康（哪个源值得留）"**：漏斗看 cleaned_rate/unknown_time/噪音/缺正文，来源健康按 A/B/C/D 打分并给出 keep_primary / keep_secondary_limit / reference_only / disable_candidate。
4. **主力通道是 cloakbrowser（59% 事件贡献）**，rsshub 次之；tophub 已废、web_monitor 有效采集但 0 清洗产出是最大提升空间。
5. **本子系统与任务描述的"A/B/C 分级"在命名上不对齐**：filter 用 main/reference/reject 三池，A/B/C/D + keep_secondary_limit 等是 source_health_report 的评级语言——两套概念需区分理解。

