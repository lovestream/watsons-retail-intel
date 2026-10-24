# 02 采集管线子系统审计

> 范围：**采集管线**（信息收集入口）。覆盖 collect_daily_articles 主采集、source_url_monitor、
> broad_search_discovery、cloakbrowser 四级接入、tophub、xcrawl_enrich、
> tavily_gap_search、merge_raw_articles 合并去重与新鲜度日期门。
> 本文为**只读审计**，逐层引用真实文件路径与实际运行产物。数据基准：`data/raw/` 近期实际产物（2026-06-22、2026-09-06、2026-10-05 等）。
> 只读，未改动任何仓库文件。

---

## 1. 采集器总览

采集管线在日报管线 `skills/run_daily_pipeline/run_daily_pipeline.py` 中的顺序（STEP_ORDER，L62-91）为：

```
health_check → collect → source_url_monitor → broad_search_discovery
→ cloakbrowser_collect [L1] → cloakbrowser_content_fetch [L1.5] → tophub_collect
→ xcrawl_enrich_articles → cloakbrowser_enrich [L2] → merge_raw_articles
→ cloakbrowser_date_verify [L3] → filter ...
```

其中**非关键步骤**（失败不阻塞主链路，SOURCE: `NON_CRITICAL_STEPS` L96-119）：health_check、source_url_monitor、
cloakbrowser_collect / content_fetch / enrich / date_verify、tophub_collect、tavily_gap_search、
enrich_cleaned_fulltext 等。核心关键链路：`collect → merge_raw_articles → filter`。

CLOAKBROWSER_STEPS（L278）= `{cloakbrowser_collect, cloakbrowser_content_fetch, cloakbrowser_enrich, cloakbrowser_date_verify, tophub_collect}`，
这些步骤由 `_check_cloakbrowser_available()` 探测补丁版 Chromium 是否可用再决定执行。

### 1.1 采集器清单

| 采集器 | 入口函数 | 抓什么源 | 强弱 | 当前状态 | 已知阻塞 |
|---|---|---|---|---|---|
| **collect 主采集（rsshub / rss / web / xcrawl / tavily / baidu_news 六法合一）** | `skills/collect_daily_articles/collect_daily_articles.py::collect_daily_articles()` | `config/sources.yaml` 全部 `enabled=true` 源 | 覆盖面广、日期门完善；但采集词宽则噪音大 | **启用**（管道第2步） | XCrawl 网络 ReadTimeout；Tavily 偶发 428/432；RSSHub 依赖内网 192.168.2.100:1200 |
| **source_url_monitor（web_monitor）** | `skills/source_url_monitor/source_url_monitor.py::monitor_source()` | `web_monitor_sources` 中 `monitor.enabled=true` 的站点列表页 | 捕捉搜索未索引的即时新文章；不猜发布日期 | **启用**（第3步）；但产出全部 unknown_time，下游多被判 reference | 虎嗅/亿邦 JS+WAF 无法直连已移除；36kr 偶发反爬验证码 |
| **broad_search_discovery** | `skills/broad_search_discovery/broad_search_discovery.py::broad_search_discovery()` | Tavily/XCrawl 搜索矩阵（平台×品类×动作×竞对），A/B/C 三级 query + site: 搜索 | 只发现 URL，不直接进 cleaned | **启用**（第4步） | 产出 0 事件（08-11 实测每4分钟 0 事件）；C-tier 已完全禁用 |
| **cloakbrowser_collect [L1]** | `skills/cloakbrowser_collect/cloakbrowser_collect.py::run_cloakbrowser_collect()` | `TARGET_SITES` 硬编码约 18 个主力站点变体（36kr×5、华尔街见闻×2、品玩、钛媒体、财联社×2、每经、界面×2、品观、新浪×2、虎嗅×4） | **主力通道**（08-11 事件贡献 59%）| **启用**（第5步） | 大量垂直媒体 AJAX 墙/网络阻塞，见 §2.4 |
| **cloakbrowser_content_fetch [L1.5]** | `skills/cloakbrowser_collect/cloakbrowser_content_fetch.py::run_cloakbrowser_content_fetch()` | 对 cloakbrowser_articles.json 的文章补全文 | 补全文提升质量 | 启用（第6步） | 依赖上一站成功产出 |
| **tophub_collect** | `skills/tophub_collect/tophub_collect.py::run_tophub_collect()` | tophub.today 聚合搜索，覆盖数百源 | 覆盖广、精确时间戳、免维护选择器 | 启用但在 pipeline 中**已名存实亡** | **验证码墙**（"安全验证"页），cookies 注入后仍不稳定；08-10/11 及 10-05 均 0 产出——作者建议停用（enabled=false） |
| **xcrawl_enrich_articles** | `skills/xcrawl_enrich_articles/xcrawl_enrich_articles.py::xcrawl_enrich_articles()` | 对 source_url_monitor/broad_search 发现的 URL 抓正文（XCrawl scrape API） | 7 key 轮换，1 credit/URL，把 URL stub 变富文章 | **启用**（第8步，非关键） | XCrawl 网络不稳；credits 有限（每 key 约1000/月）；content 截断 20000 字符 |
| **cloakbrowser_enrich [L2]** | `skills/cloakbrowser_collect/cloakbrowser_enrich.py::run_cloakbrowser_enrich()` | 对 XCrawl scrape 失败或 content<200 的 URL 用 CloakBrowser 重抓 | XCrawl 失败补救 | 启用（第9步） | 依赖 cloakbrowser 可用 |
| **cloakbrowser_date_verify [L3]** | `skills/cloakbrowser_collect/cloakbrowser_date_verify.py::run_date_verify()` | 复核可疑日期（质量守门） | 提高日期可信度 | 启用（第11步） | — |
| **tavily_gap_search** | `skills/tavily_gap_search/tavily_gap_search.py::tavily_gap_search()` | 补漏：当 cleaned_count<5 或核心平台缺文章时触发 | 精准补漏，非主渠道 | 启用（管道第15步，非关键/条件触发） | 曾 P0 崩溃（keywords 711 无 str() 防御）已修复；Tavily 额度有限 |
| **merge_raw_articles** | `skills/merge_raw_articles/merge_raw_articles.py::merge_raw_articles()` | 合并全部采集产物 + 硬日期门 | 去重/优先级/日期门核心 | **核心**（第10步） | — |

### 1.2 已知阻塞明细（来自 `AUDIT_20260811_collection.md` + 代码实测）

1. **DNS 超时**：CBO化妆品财经、联商网等 CloakBrowser 站点 timeout/DNS 不可达（cloakbrowser_articles.py TARGET_SITES 注释"已禁用（网络限制）"）。
2. **AJAX 墙**：DoNews、第一财经、经济观察报、亿欧网、创业邦、投资界、极客公园、网经社、亿邦动力——可访问但搜索结果 JS 动态渲染，需 API 逆向（cloakbrowser_collect.py L269-289 "待激活"注释）。
3. **JS 反爬/WAF**：虎嗅（严重 JS+阿里云 WAF）、亿邦动力（空壳重定向）已从 monitor 源列表移除（source_url_monitor SKILL.md "Known Limitations"）。
4. **Google News 被墙**：tophub 结果里 `news.google.com/rss/articles` 超长重定向链被判噪音直接跳过（tophub_collect.py `if "news.google.com/rss/articles" in url and len(url) > 200: continue`）。
5. **Tavily 432**：Tavily API 偶发 432/限流错误，代码在 HTTP 4xx 时 `mark_exhausted`，401/403 记 Key 耗尽切换（collect_daily_articles.py collect_tavily HTTPError 分支）。
6. **XCrawl 超时**：run.xcrawl.com 频繁 ReadTimeout，单源曾空转 374s（08-11 实测 7 源各 374s）。**已修复**：`collect_xcrawl` timeout=min(timeout,15) + max_retries=1（collect_daily_articles.py L1387 附近）。
7. **tophub 验证码墙**：返回"安全验证"页 → `_CAPTCHA_BLOCKED` 快速失败放弃全部查询（tophub_collect.py _search_tophub）。
8. **tavily_gap 711 int 崩溃**（P0，已修复）：`config/keywords.yaml` L259 `- 711` 被 YAML 解析为 int，tavily_gap `kw.lower()` 崩。已改为 `"711"` 引号 + `_load_all_keywords` 加 `str(w)` 防御（tavily_gap_search.py L284-307 现已有 `str(w)`/`str(text)`）。
9. **web_monitor 12 直连源全采全滤（0 事件贡献）**：直连源标题短、无正文 → 规则分低 → reference/reject。已在 filter 侧让垂直源跳过标题级 CORE 过滤，由全文判断。
10. **XCrawl 全站文章路由 collection_keywords**：RSSHub huxiu route 若 collection_keywords 命中 0 则整篇跳过（V2.2 特性，不是 bug）。

---

## 2. 来源清单梳理（config/sources.yaml）

### 2.1 主源（web_monitor_sources，直连监控）

共 12 个 web_monitor_sources 定义，**启用 9 个 / 禁用 2 个**（来源 `monitor.enabled=true`）：

| id | 名称 | list_urls | source_tier | 启用 |
|---|---|---|---|---|
| web_36kr_newsflashes | 36kr快讯(直连) | 36kr.com/newsflashes + /information/technology | 1 (tier1_core) | ✅ |
| web_36kr_more | 36kr金融(直连) | 36kr.com/information/finance/ | 2 | ✅ |
| web_latepost | 晚点LatePost(直连) | latepost.com + /news/index | 2 | ✅ |
| web_jiemian_business | 界面新闻-商业(直连) | jiemian.com | 2 | ✅ |
| web_ebrun_retail | 亿邦动力-零售 | ebrun.com/retail/ + /instant/ | 1 | ✅（但 JS 反爬受限）|
| web_ebrun_beauty | 亿邦动力-美妆 | ebrun.com/beauty/ | 1 | ✅（JS 受限）|
| web_linkshop | 联商网 | linkshop.com/news/ + /retail/ | 1 | ✅（DNS 受限）|
| web_winshang | 赢商网 | news.winshang.com | 2 | ✅ |
| web_pinguan | 品观APP | pinguan.com/article/ | 1 | ✅（CloakBrowser 主力，品观 08-11 贡献 23 条/天）|
| web_jumeili | 聚美丽 | jumeili.cn/news/ | 1 | ✅ |
| web_qingyan | 青眼 | qingyan001.com/article/ | 2 | ❌ 禁用 |
| web_c2cc | C2CC传媒 | c2cc.cn | 2 | ❌ 禁用 |

> 注：主源在 config 中列于 `web_monitor_sources`，由 source_url_monitor 解析；同时部分（如 36kr）经 rsshub route 单独采集。

**启用量大**：36kr 快讯（newsflashes + technology）、晚点、界面、品观、亿邦零售/美妆。
**降级/禁用**：青眼、C2CC 已禁；亿邦/联商/赢商受 JS/WAF/DNS 反爬限制实际产出受限。
**片段限制**：RSSHub / 网页采集器 content 截断 50000 字符（collect_daily_articles.py collect_rsshub/collect_rss `content[:50000]`）；
web 采集器 `_extract_article_content` 同样 50000 截断；summary 取 content[:500]。
xcrawl_enrich 正文截断 20000 字符（xcrawl_enrich_articles.py rule 9 "truncate at 20000 chars"）。
RSSHub 某些路由本身只给 100-227 字 summary（feeds 的 `<description>` 片段），故 36kr/快讯类常只有 snippet 级内容，需 enrich 补全文。

### 2.2 RSSHub 搜索路由（rsshub_sources）

`rsshub_base: http://192.168.2.100:1200`（内网）。两类：
- **RSSHub 频道**：`/36kr/newsflashes`、`/latepost`
- **RSSHub 搜索路由**：`/huxiu/search/{词}` 与 `/36kr/search/articles/{词}`

启用的搜索路由（route / 关键词 / 用途）：
- `虎嗅搜索`：即时零售、美团闪购、京东秒送、淘宝闪购、抖音小时达、小红书、美妆、个护、防晒、屈臣氏、名创优品、本地生活、门店、新零售（tier2-4）
- `36kr搜索`：即时零售、美妆、屈臣氏、美团闪购、淘宝闪购、京东秒送、抖音小时达（tier4_clue）

**已禁用的冗余/低效 RSSHub 搜索源**（enabled=false）：虎嗅渠道/列表（huxiu_article、huxiu_consumer_channel、huxiu_business_channel）、huxiu search 闪购/前置仓/天猫/京东/抖音电商/丝芙兰/欧莱雅/珀莱雅/薇诺娜/WOW COLOUR/大众点评/抖音本地/妍丽/便利店/天猫(二)/京东(二)、36kr skincare。
作者已在 AUDIT 指出：**rsshub 48 源里 ~20 disabled，enabled 的 huxiu 搜索多数 0-1 条/天**（"门店"、"新零售"、"本地生活"每天 0-1 条），而 36kr 搜索稳定 2-3 条。
**严格/客户端日期过滤**：RSSHub 对 huxiu/search 路由 `filterout_time` 不生效 → V2.3 客户端二次校验，超过 filterout_days 的丢弃；**搜索路由无日期条目直接跳过**（collect_daily_articles.py collect_rsshub V2.3 注释）。

### 2.3 固定/Tavily/XCrawl 搜索源（other_sources）

- **XCrawl 搜索源**（method: xcrawl，22 个定义，12 启用 / 10 禁用）：xcrawl_01..28。启用：即时零售最新/动态、美团即时零售、京东到家即时零售、屈臣氏×4（最新/门店/扩张/到家）、美妆×3、竞品丝芙兰/名创优品/话梅调色师、品牌×2、抖音渠道。禁用：万亿、淘宝闪购、闪电仓、美团京东阿里、小象、外卖大战、小红书、私域、行业报告×3。
- **Tavily 搜索源**（method: tavily，4 个启用 tavily_01..04 + 2 个禁用的 tavily_gap_fill/beauty_gap）：Tavily 已从 80 预算削减到 **daily_budget=40 + reserve=10**（search_policy.yaml，CloakBrowser 接管后）。

### 2.4 CloakBrowser TARGET_SITES（硬编码，非 sources.yaml）

来源 `skills/cloakbrowser_collect/cloakbrowser_collect.py::TARGET_SITES`：
- **可采 18 变体**：36kr×5（即时零售/美妆/新零售/消费/供应链）+ 华尔街见闻×2 + 品玩 + 钛媒体 + 财联社×2 + 每日经济新闻 + 界面×2 + 品观 + 新浪×2（消费/美妆）。
- **AJAX 阻塞 9 站**（待激活注释）：DoNews、第一财经、经济观察报、亿欧网、创业邦、投资界、极客公园、网经社、亿邦动力。
- **网络阻塞 10+**：DNS/SSL/连接重置（如 CBO、联商网）。
- **虎嗅 4 站**（2026-05-18 新增）：消费频道、零售频道、搜索-即时零售、搜索-美妆个护。

### 2.5 source_packs.yaml 站内搜索域名（broad_search 用 site: 查询）

- **platform_official**：meituan.com、jddj.com、ele.me、doudian.com
- **vertical_media**：ebrun、linkshop、winshang、pinguan、jumeili、qingyan001、c2cc、36kr、huxiu、jiemian、latepost、thepaper、yicai、cbnweek、cyzone
- **general_portal**：163、sina.com.cn、qq、sohu、ifeng
- **beauty_vertical**：cosmetic-back-stage、beautyinfrance、chbd.org.cn
- **competitor_brand**：watsons.com.cn、watsons.com.tw、sephora.cn、mint.cn
- **general_news**：news.cn、people.com.cn、stcn.com

预算（source_packs.yaml V4）：max_queries_per_run=60、tavily_daily_limit=50、xcrawl_daily_limit=10、
tier_budget A(10-15) / B(5-10) / **C 完全禁用**（CloakBrowser 替代）。

---

## 3. 搜索/查询词策略

来源：`config/search_policy.yaml`、`config/source_packs.yaml`、`config/keywords.yaml`。

### 3.1 中文短查询 + news-friendly

- search_policy `fixed_queries` 按 platform/competitors/categories/channels 四组，全部**中文短查询**且 news-friendly：
  - platform：`"即时零售 美团闪购 京东到家 最新动态"`、`"即时零售 淘宝闪购 抖音小时达 最新政策"`、`"即时零售 屈臣氏 美妆个护 最新合作"`、`"前置仓 即时零售 美妆 最新消息"`
  - competitors：`"丝芙兰 即时零售 最新动态"`、`"万宁 即时零售 最新布局"`、`"调色师 美团闪购 最新投入"`、`"话梅 妍丽 即时零售"`
  - categories：`"美妆个护 即时零售 GMV 增长 数据"`、`"防晒 即时零售 销量 趋势"`、`"护肤 即时零售 增长 最新"`、`"面膜 即时零售 爆款"`
  - channels：`"屈臣氏 电商 渠道变化 最新"`、`"美妆个护 前置仓 渠道 最新"`、`"即时零售 美妆 品牌入驻 新品"`
- **XCrawl 短查询原则**：`collect_xcrawl` 注释明确"不再追加日期后缀"，短查询+不带日期更能命中当天热点（collect_daily_articles.py collect_xcrawl）。

### 3.2 搜索矩阵（source_packs.yaml）

- platforms：美团闪购、京东到家、京东秒送、淘宝闪购、饿了么、抖音小时达
- categories：美妆、个护、护肤、彩妆、洗护、防晒、女性护理、日化
- actions：最新、上线、加码、合作、补贴、招商、规则、小时达、即时零售
- competitors：屈臣氏、丝芙兰、万宁、妍丽、WOW COLOUR、调色师、话梅、名创优品

生成两类 query：泛搜索（平台×品类×动作）与站内 site: 搜索。A 类必跑（竞对×平台 + 平台×品类核心词），B 类按预算，C 类禁用。

### 3.3 只大陆中国 / 中文查询

- 所有查询均为中文（Tavily/XCrawl `language=zh`，source_packs xcrawl_search.language=zh）。
- Tavily 全局 `exclude_domains` 去掉 zhihu/baidu/weibo/douban/淘宝/京东/天猫/拼多多/社媒/小红书等低信噪比域，`include_domains` 默认空=不限。
- **大陆过滤在 filter 阶段**（不在采集器）：`filter_relevant_articles.py` L556 "大陆强指标：命中任意一个，几乎可确定是大陆内容，繁体字判定一律让位"，L574 检测港台媒体名/地区词 → 非大陆降级/拒绝。

### 3.4 关键词命中规则（无命中即拒）

- 采集器层面：`collect_rsshub` V2.2 若源配置 `collection_keywords`，标题+摘要**不含任一关键词则整篇丢弃**（RSSHub 全站路由用）。
- source_url_monitor：用 `strict_keywords` 优先精确过滤标题（web_36kr_newsflashes 配了 30+ strict_keywords，见 sources.yaml）。
- **下游 filter 准入**（`filter_relevant_articles.py::has_core_hit`）：核心命中词（屈臣氏/核心平台/竞对）用于 cleaned 快通道；美妆词需强信号词单独命中或弱词+上下文双验证，**单一弱信号词+无上下文不算核心命中 → 不享受快通道**。`matched_keywords` 为空列表的文章仍可能过 rule_score 进 reference，但难进 cleaned——实质"无命中即拒"落在核心命中判定层。
- 关键词来源：`config/keywords.yaml` 经 `load_all_keywords`/`_try_load_keywords` 递归展平去重，含 `"711"`（已加引号防 int）。

---

## 4. 新鲜度日期门

### 4.1 时间窗口（collect_daily_articles.compute_time_window / classify_time_status）

- 窗口：date-1 天 07:00 ~ date 07:00（Asia/Shanghai），`extend_to_now` 可扩展同日，硬上限 date 23:59:59 绝不跨天。
- `classify_time_status(published_at, window_start, window_end)`：以"距 window_end 的天数"为统一基准——0-2 天 → `in_window`；3-7 天 → `near_window`；>7 天 → `old`；无法解析/无日期 → `unknown_time`。**发布于窗口之后（diff<0）也判 in_window**（未来/极近）。

### 4.2 硬日期门（merge_raw_articles.py，核心实现）

`merge_raw_articles` L520 起"硬日期门"为唯一权威日期门，三层 cutoff（按时效分层 `classify_date_tier`）：

| 分层 (date_tier) | 判定 | 保留范围 | cutoff |
|---|---|---|---|
| **channel**（RSSHub 频道，严格 1 天） | 非 search 非 broad | 昨天~今天 | `window_start - 1天` |
| **search**（RSSHub 搜索，放宽 3 天） | source_name/route/collector 含 "search" | 3 天 | `window_start - 3天` |
| **broad**（XCrawl/CloakBrowser/tophub/broad_search，放宽 7 天） | 命中 BROAD_SOURCES 集合 | 7 天 | `window_start - 7天` |

优先级/例外（L611-onwards）：
1. `time_status == "uncertain_date"` 且 collector 含 cloakbrowser / source_type 是 tophub / 属于 BROAD_SOURCES → **直接保留**（采集端已做时效筛选，不用 content 猜的日期拒绝）。
2. `unknown_time` 且来源属 MONITOR_SOURCES（source_url_monitor/web_monitor）→ **保留**（web_monitor 不 fake 日期，只记 discovered_at）。
3. URL 日期二次校验：published_at 与 URL 日期差异 >7 天 → 以 URL 日期为准；若 URL 日期过 cutoff 则拒绝。
4. **未来日期拦截（V6）**：published_at 晚于 window_end+1 天 → 假日期/时区错位 → **拒绝**（不再当 in_window）。
5. 无日期文章：broad 源保留；RSSHub 搜索源无日期 → 拒绝（collect 已过滤，漏网说明路由坏）；其他 → 拒绝。

**unknown_time/uncertain_date 处理汇总**：
- unknown_time 且 monitor 源 → 保留并进 reference；
- uncertain_date 且 cloakbrowser/tophub/broad → 保留；
- 其他无日期 → 拒绝（rejected_no_date）。

### 4.3 采集端的 filterout_days

- RSSHub：`filterout_days` 源级/全局默认（defaults.filterout_days=14，全局 filterout_days 兜底 90 天），转换成 `filterout_time` 秒参数追加到 feed URL；因 huxiu/search 不生效故再客户端二次校验。
- **RSSHub 搜索路由日期不可解析 → 直接跳过**。
- tophub：`max_days_back=2` 只保留 2 天内。
- 校验点在 `data/raw/{date}/raw_articles_all.json` 的 `metadata.date_gate`（实测 2026-10-05：total_before=115 → kept=41，rejected_old=63、rejected_no_date=11、near_cutoff_kept=17）。

---

## 5. merge_raw_articles：合并去重

入口 `skills/merge_raw_articles/merge_raw_articles.py::merge_raw_articles()`，输出 `raw_articles_all.json`。

### 5.1 合并输入源（MERGE_SOURCES，按优先级先到先得）

**有全文的 enriched 版本必须排在无全文前面**——这样同一 URL 同时出现时保留有全文版：

| 优先级 | 文件 | name | 备注 |
|---|---|---|---|
| 1(最高) | `cloakbrowser_enriched_articles.json` | cloakbrowser_content_fetch | 有全文 |
| 2 | `xcrawl_enriched_articles.json` | xcrawl_enrich | 有全文 |
| 3 | `xcrawl_cloakbrowser_fallback.json` | cloakbrowser_enrich | XCrawl 失败救回 |
| 4 | `raw_articles.json` | main_collection | RSSHub+XCrawl snippet |
| 5 | `tophub_articles.json` | tophub_collect | 精确时间戳 |
| 6 | `newly_discovered_urls.json` | source_url_monitor | Web Monitor |
| 7 | `cloakbrowser_articles.json` | cloakbrowser_collect | 兜底（仅标题）|
| 8 | `tavily_gap_articles.json` | tavily_gap_search | 补搜 |
| 9(最低) | `broad_search_urls.json` | broad_search_discovery | 会被 enriched 覆盖去重 |

### 5.2 去重机制（seen_urls ledger）

- **去重主键 = URL（normalized）**，`seen_urls: Set[str]` 全程累积，先到先得：后续相同 URL 直接丢弃（`file_deduped++`）。注意：**合并器的 URL 去重不按 tier 保留高分**——先出现的保留（与 collect 内部 dedup 不同，collect 内 dedup 是保留低 tier 数值）。
- `normalize_url`：小写 host、去尾斜杠、去 `utm_*`/`from`/`share` 追踪参数。
- 文件缺失则跳过；JSON 解析失败记 error 跳过。
- 分类：`classify_article` 按 collector/source_type/role/file_source 五级映射到 rsshub/rss/web/source_url_monitor/broad_search/xcrawl_enriched/tavily_gap/cloakbrowser/tophub 计数。
- **日期补充**：无 published_at/time_status 的文章，`_extract_date_from_article` 依次从 `raw.published_date` → 摘要 → 中文日期正则 → 相对日期（"5天前"含"前"才匹配）→ 昨天/今天/前天 → URL 日期，补 published_at 与 time_status。
- **published_at_source 置信度标记**（第七节）：tavily→low、pub_date/pub_time→html_time(high)、URL 日期→url_date(medium)、否则 unknown。

### 5.3 优先级总结：enriched 优先于 snippet

实测（2026-10-05 raw_articles_all metadata）：source_counts = {cloakbrowser_count:13, source_url_monitor_count:2, tavily_gap_count:19, rsshub_count:72, xcrawl_enriched_count:9}，
file_counts = {cloakbrowser_enriched:13, xcrawl_enriched:21, xcrawl_cloakbrowser_fallback:0, raw_articles:100, tophub:0, newly_discovered:2, cloakbrowser_articles:13, broad_search:0}。
→ 有全文的 enriched 版本排前，snippet（raw_articles）中被覆盖的自动去重。

---

## 6. 各采集器真实输出文件路径与字段

统一输出目录 `data/raw/{date}/`，日志 `data/logs/{date}/`。实测确认（2026-10-05、2026-09-06、2026-06-22）：

| 采集器 | 输出文件 | 文章字段 |
|---|---|---|
| **collect 主采集** | `data/raw/{date}/raw_articles.json` | metadata{window_start/end, by_collector, time_summary, tavily_key_status, collection_limits} + source_stats + articles[ {article_id,title,url,source_name,source_type,source_tier,collector,published_at,collected_at,time_status,summary,content,matched_keywords,raw} ]。实测文章 keys: article_id,title,url,content,summary,published_at,source_name,source_type,time_status,collected_at,keywords_matched,tier,role,category,raw_collector,matched_keywords |
| **source_url_monitor** | `newly_discovered_urls.json` + `source_url_monitor_summary.json`（ledger：`data/source_ledger/seen_urls.jsonl`） | article stub: {article_id:"N{date}_{idx}",title,url,source_name,source_type:web_monitor,source_tier,collector:source_url_monitor,published_at:null,discovered_at,first_seen_at,time_status:unknown_time,freshness_status:newly_discovered|bootstrap_seen,summary:"",content:"",matched_keywords,role,category,raw{list_page_url,link_text,content_hash}}。summary: per_source 各源 total/new/seen/failed |
| **broad_search_discovery** | `broad_search_urls.json` | {metadata, articles[]}，只发现 URL 线索不进 cleaned。实测 10-05 为 0 条 |
| **cloakbrowser_collect [L1]** | `cloakbrowser_articles.json` | {metadata, articles[兼容 raw_articles 格式]}，实测 10-05 n=13 |
| **cloakbrowser_content_fetch [L1.5]** | `cloakbrowser_enriched_articles.json`（同时回写 cloakbrowser_articles.json）| 富文章全文 |
| **xcrawl_enrich_articles** | `xcrawl_enriched_articles.json` + `xcrawl_enrich_stats.json` | {metadata, articles[ {article_id,title,url,source_name,source_type,source_tier,role,category,collector:xcrawl_enrich,published_at,collected_at,discovered_at,freshness_status:newly_discovered,time_status,summary,content,matched_keywords,enrichment{credits_used,status,error?},raw} ]}，含 search_query。实测 10-05 n=21 |
| **cloakbrowser_enrich [L2]** | `xcrawl_cloakbrowser_fallback.json` | XCrawl 失败/短文补救，有全文 |
| **tophub_collect** | `tophub_articles.json` | {title,url,source,source_type:tophub,domain,pub_date,pub_time,summary,collected_at}（**非 raw_articles 兼容格式**，合并时会 standardize pub_date→published_at）。实测 10-05 为 0 条（`[]`）|
| **tavily_gap_search** | `tavily_gap_articles.json` + `raw_articles_merged.json`（+ 重跑 filter 产 cleaned/reference/rejected）| {metadata, articles[兼容 raw_articles：article_id,title,url,source_name,source_type:tavily,source_tier,collector:tavily_gap,published_at,collected_at,time_status,summary,content,matched_keywords,raw{query,time_range,search_query,...}} ]}。实测 10-05 n=78 |
| **merge_raw_articles** | `raw_articles_all.json` | metadata{version,date,total_count,source_counts,file_counts,dedup_counts,date_extraction,date_gate{total_before,kept,rejected_old,rejected_future,rejected_no_date,near_cutoff_kept},merge_sources,window_start,window_end} + articles[] |
| （下游 filter 另有 raw_articles_merged.json 由 tavily_gap 产出）| `raw_articles_merged.json` | tavily_gap 补搜合并后的文件（1.02MB 实测含更多字段）|

---

## 7. 结论与要点

1. **主力通道是 cloakbrowser**（08-11 事件贡献 59%），rsshub 次之（17%），tavily 15%，xcrawl 8%，tophub 已 0，web_monitor 直连 0。
2. **tophub 已实质废弃**（验证码墙），作者建议停用 enabled=false；目前 pipeline 仍调度它但每次 0 产出空转 ~20s。
3. **日期门统一在 merge_raw_articles**，channel 1d / search 3d / broad 7d，unknown_time/uncertain_date 对 monitor/cloakbrowser/broad/tophub 源放宽保留。
4. **合并优先级：有全文的 enriched 优先于 snippet**，先到先得，URL 去重。
5. **已知阻塞已部分修复**：tavily 711 int 崩溃（P0）、tophub 验证码墙快速失败（P0）、xcrawl 超时降级（P1-3）、web_monitor 过滤策略（P1-4）均已修，但 xcrawl 慢/空转、web_monitor 0 贡献、tavily 限流仍未根治。

