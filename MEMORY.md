# Watsons Retail Intel — Project Memory

## Project Overview
- **Name**: 即时零售 × 个护美妆经营情报系统
- **Root**: `/app/working/projects/watsons-retail-intel`
- **Purpose**: Daily intelligence pipeline for Watsons retail — collect → filter → extract events → score → report

## Architecture & Data Flow (V4 Pipeline — 方案C: 播客独立于日报)
```
collect → source_url_monitor → broad_search_discovery → xcrawl_enrich
  → cloakbrowser_enrich → merge_raw_articles → cloakbrowser_date_verify
  → filter → quality_funnel → source_health → tavily_gap_search
  → filter_merged → enrich_cleaned_fulltext → extract → score → analyze → novelty_check
  → evergreen_candidates
  → generate_podcast (从事件池直接生成，不依赖日报)
  → podcast_review
  → generate_report → editor_review
  → send_daily_report_email → pipeline_alert

播客数据源: events_scored_novelty.json (含business_analysis)
日报数据源: events_scored_novelty.json
两者独立生成，互不阻塞。
```

## Config & Secrets
- **LLM**: Longcat API (`https://api.longcat.chat/openai`), 3 models available
- **LLM Keys**: 6 keys (`longcat`–`longcat5`) round-robin, 5M tokens/day each
- **Tavily**: 6 keys (`tavily_key`–`tavily_key5`), 1000 searches/month each, round-robin; **two-stage time_range** (day→week fallback)
- **RSSHub**: `http://192.168.2.100:1200`
- **Daily window**: (date-1) 07:00 ~ max(date 07:00, now) (Asia/Shanghai) — 晚间运行自动扩展到当前时刻
- **Pipeline默认date**: today（非yesterday）
- **No .env files** — secrets only via QwenPaw env vars

## Model Router (新!)
- **Config**: `config/model_router.yaml`
- **Models**: LongCat-Flash-Lite (批量结构化), LongCat-Flash-Chat (中等分析), LongCat-Flash-Thinking (复杂推理)
- **Routing**:
  - filter_relevant_articles: Lite → Chat fallback
  - extract_events: Lite(默认) → Chat(二次重试fallback); 高价值不用Thinking，二次失败走 rule_fallback + needs_verification=True
  - analyze_business_impact: P0→Thinking, P1→Chat, P2→Lite, ARCHIVE→rule_only
  - generate_daily_report: Thinking(默认) → Chat → Lite; max_tokens=8192
  - generate_podcast: Thinking(默认) → Chat → Lite; max_tokens=8192
  - editor_review: Chat(默认) → Lite(fallback)
- **Backward compat**: model_router.yaml 不存在时使用默认模型 (LongCat-Flash-Thinking)
- **llm_client.chat()** 新增 `model` 参数，可覆盖 `self.model`

## Search Policy (新!)
- **Config**: `config/search_policy.yaml`
- **Tavily**: enabled, daily_budget=80, reserve_budget=20
- **Tavily two-stage search**: default `time_range="day"`, fallback to `"week"` if day results < `min_day_results` (default 3)
- **Tavily domain filtering**: `include_domains` / `exclude_domains` per source; global exclude (zhihu, baidu, weibo, etc.)
- **Tavily `domain_scope`**: maps keywords ("retail", "beauty", "platform") to vertical domain lists
- **`freshness_status`**: `day_primary` (Tavily day search) | `week_fallback` (Tavily week fallback) | `newly_discovered` (web_monitor) | `enriched` (xcrawl_enrich)
- **Week fallback articles**: never enter `main` pool; `reference` if score ≥ 3, else `reject`
- **Fixed queries**: 4 categories (platform/competitors/categories/channels)
- **Gap search**: 当某平台当天无有效事件时自动补搜
- **Verify search**: 高 watsons_relevance(≥4) + low/medium confidence 事件反查验证

## Three-Draft System (2026-04-27)
- **V1**（完整初稿）: 偏覆盖，保留所有事件详情，low/rule_fallback标注但仍展开
- **V2**（经营总编重构稿）: 偏取舍、压缩和经营判断，最多3核心信号，1600-2400中文字，low/rule_fallback只作线索
- **daily_report_draft.md**: 向后兼容，指向V1
- **Output files**: `daily_report_draft_v1.md`, `daily_report_draft_v2.md`, `daily_report_draft.md`
- **editor_review双稿模式**: V1→V2→比较→LLM审稿→终稿校验; V2不存在时自动回退单稿模式
- **V2规则生成**: `generate_v2_by_rules()` — 压缩信号为最多3条、精炼影响判断、合并平台变化、唯一动作合规
- **V2 LLM重构**: `refine_v2_with_llm()` — 独立LLM prompt，不受V1限制
- **V2检查**: check_v2_restraint() — 信号数上限3、字数1600-2400、标记合规、唯一动作
- **两稿比较**: compare_drafts() — P1遗漏、事件池外事实、唯一动作合规

## LLM Quirks (Critical!)
1. **Thinking model**: `content` often empty; actual output in `reasoning_content` → `llm_client.py` checks both
2. **JSON truncation**: reasoning consumes tokens → truncated JSON → `_repair_truncated_json()` salvages by finding last complete object
3. **Connection instability**: `ConnectionResetError`/`RemoteDisconnected` → retry with next key
4. **Empty events + no reject**: When LLM returns `events=[]` + `article_reject=false`, check title for event keywords → `suspect_empty_events` → rule fallback

## Pipeline (run_daily_pipeline V2)
- **10步骤 + 动态分支**: collect → filter → source_health_report → tavily_gap_search → filter_merged → extract → score → analyze → generate_report → editor_review
- **无信号分支**: filter_merged 后 cleaned_count=0 → generate_no_signal_report → editor_review（跳过 extract/score/analyze/generate_report）
- **无信号日报 ≠ 系统故障**: 是诚实输出
- **filter_merged**: 用 raw_articles_merged.json 重跑 filter(use_llm=False)
- **source_health_report**: best-effort, 非阻塞
- **tavily_gap_search**: 条件触发(cleand<5/平台缺失/参考线索), best-effort, 非阻塞
- **优先import调用**，失败时subprocess CLI
- **每步校验**: 文件存在、JSON合法、数量合理
- **resume跳过**: 已有合格输出可跳过（enable时）
- **force重跑**: 强制覆盖已有输出
- **dry_run**: 只检查不执行
- **start_step/end_step**: 子集运行
- **continue_on_error**: 失败后继续（默认false）
- **产物**: `data/runs/{date}/run_manifest.json` + `step_status.json` + `run_summary.md`
- **manifest新增**: report_type("normal"/"no_signal"), no_signal(bool)

## generate_podcast (2026-04-28)
- **Skill**: `skills/generate_podcast/generate_podcast.py`
- **定位**: 最终日报 → 口播稿 + MP3音频
- **核心原则**: 不直接朗读日报，转成通勤收听口播；不新增日报外事实/数据
- **口播稿**: 1800-2500中文字符
- **LLM**: LongCat-Flash-Chat → LongCat-Flash-Lite → 规则模板(fallback)
- **语音**: edge-tts, 默认 zh-CN-XiaoxiaoNeural
- **输出**: `podcasts/scripts/{date}.md` + `podcasts/audio/{date}.mp3`
- **无信号日报也有口播版**（规则模板）
- **音频失败不阻塞日报发送**
- **状态**: WIP — 无信号模板字数不足(1231/1800min)，需要扩充
- **sendable**: 从editor_review步骤结果或审稿报告中读取
- **send_email**: 参数保留但暂未实现

## Skills Status
| Skill | Status | Key Files |
|-------|--------|-----------|
| collect_daily_articles | ✅ V2 tested | `skills/collect_daily_articles/` |
| source_url_monitor | ✅ V2 tested | `skills/source_url_monitor/` |
| broad_search_discovery | ✅ V1 tested | `skills/broad_search_discovery/` |
| xcrawl_enrich_articles | ✅ V2 multi-input | `skills/xcrawl_enrich_articles/` |
| merge_raw_articles | ✅ V1 tested | `skills/merge_raw_articles/` |
| filter_relevant_articles | ✅ V4 noise tested | `skills/filter_relevant_articles/` |
| quality_funnel_report | ✅ V1 created | `skills/quality_funnel_report/` |
| source_health_report | ✅ tested | `skills/source_health_report/` |
| tavily_gap_search | ✅ tested | `skills/tavily_gap_search/` |
| extract_events | ✅ V2 parallel (Lite→Chat retry+checkpoint) | `skills/extract_events/` |
| score_events | ✅ V1 tested | `skills/score_events/` |
| analyze_business_impact | ✅ parallel (batch ARCHIVE/P2/P1/P0+checkpoint) | `skills/analyze_business_impact/` |
| generate_daily_report | ✅ V2 three-draft tested | `skills/generate_daily_report/` |
| generate_no_signal_report | ✅ V1 tested | `skills/generate_no_signal_report/` |
| generate_podcast | ✅ V3 podcast-first (2026-05-17 rewrite) | `skills/generate_podcast/` |
| podcast_review | ✅ V1 created (2026-05-17) | `skills/podcast_review/` |
| editor_review | ✅ V2 dual-draft tested | `skills/editor_review/` |
| run_daily_pipeline | ✅ V3 19-step (podcast_review integrated) | `skills/run_daily_pipeline/` |

## Key Decisions
- **LLM client**: `requests.post` over OpenAI SDK (compatibility)
- **llm_client.chat(model=)** 新增 model 参数覆盖，支持模型路由
- **model_router.py**: 读取 config/model_router.yaml，按技能/优先级选模型
- **3-way failure routing**: `events_raw.json` / `events_rejected_articles.json` / `events_failed_articles.json`
- **Failure type separation**: Technical failures (LLM/format errors) vs business rejections (no events)
- **suspect_empty_events**: When LLM returns empty events without explicit reject, but title contains event keywords → treated as technical failure → rule fallback
- **8维度评分体系**: strategic_importance/watsons_relevance/impact_scope/source_credibility/data_richness/actionability/time_sensitivity/novelty
- **硬降级9条规则**: confidence=low, rule_fallback, source_cred<2, wr<3, background_only, unclear → P2封顶; source_url缺失/fact空 → ARCHIVE; evidence_text空 → P2
- **经营分析模块**: 规则分析+LLM辅助双模式; 7条硬降级规则; impact_type=opportunity/risk/watch/noise; action_level=immediate/test/watch/archive
- **editor_review 审稿模块**: 三步审稿(Step1规则校验→Step2 LLM审稿→Step3终稿校验); 标记独立性(low=⚠️待验证, rf=🔄规则兜底, 互不替代); sendable只看Step3终稿复检; final_validate返回结构化issues
- **source_url_monitor bootstrap模式**: 第一次运行时ledger为空→所有URL标记`bootstrap_seen`(非`newly_discovered`); bootstrap_seen绝不进cleaned, 最高reference; 第二次及以后→新URL才标`newly_discovered`; `load_seen_urls()`返回`(seen_dict, is_bootstrap)`元组
- **filter `bootstrap_seen`处理**: compute_rule_score -2分; decide_final_pool 绝不进cleaned(最高reference)
- **搜索 Query A/B/C 分层**: A类(必跑,竞对×平台+核心趋势,~28条,time_range=day); B类(按预算,平台×品类×动作,~34条,time_range=day→week); C类(动态,site:domain搜索,~46条,time_range=week); A类不受总预算限制
- **垂类媒体双轨**: web_monitor栏监控 + broad_search C类site:搜索; 综合门户只靠site:搜索(163/sina/qq/sohu/ifeng)
- **evergreen_candidates**: 从reference+rejected提取rule_score≥8且old/unknown_time的高价值旧文; 存入data/evergreen/YYYY-MM-DD/; 非阻塞步骤; 用于知识库/周报/经营分析补充
- **动态模板变量名**: 配置用复数(platforms/categories/competitors/keywords), 模板花括号也用复数({platforms}); domains_from特殊处理转domain

## Test Results (2026-04-26)
- **collect**: 60 raw articles ✅
- **filter**: 6 cleaned articles (3 main + 1 reference + 2 rejects) ✅
- **extract_events**: 11 events (10 LLM + 1 rule fallback), 1 rejected (趋势分析), 0 failed ✅
  - `suspect_empty_events` correctly triggered for "屈臣氏京东到家渠道4月GMV突破2亿" → rule fallback generated event
- **score_events**: 11 events → P1=3, P2=7, ARCHIVE=1, P0=0 ✅
  - 硬降级: 2条confidence=low事件降级到P2 (含1条rule_fallback双重降级)
  - 评分维度全部有理可溯，排序正确
  - avg_weighted_score=3.755
- **analyze_business_impact**: 11 events → LLM 8 success / 3 fallback; action_level: immediate=2, test=6, watch=2, archive=1 ✅
- **generate_daily_report**: Rule mode ✅ + LLM 润色 ✅
  - 10 events used (ARCHIVE excluded), 3 top signals, 1 unique action event (E20260426_0010)
  - 8 sections fully generated, validation passed
  - LLM 润色: 4312字符, 更精炼专业; 1 minor warning (rule_fallback标注被LLM部分去除)
  - Rule fallback: 约5068字, 完整保留所有标注
- **editor_review**:
  - 规则模式: 0 issue, validation_passed=true, sendable=true ✅
  - LLM模式 (Chat): 初稿6问题→终稿0问题→sendable=true ✅
  - 三步审稿→**双稿五步审稿**: V1检查(Step1)→V2检查(Step1b)→两稿比较(Step1c)→LLM审稿(Step2)→终稿校验(Step3)
  - **标记独立性**: low confidence只检查⚠️待验证, rule_fallback只检查🔄规则兜底, 互不替代; 双重条件事件必须两个标记都有
  - **sendable逻辑**: 只看终稿复检(Step3)的final_issues; 初稿high问题被LLM修复后不再阻塞发送
  - **final_validate**: 返回(Passed, List[structured_issue]); high=结构性问题(缺章节/缺ID/多条建议)
  - **V2检查**: 最多3信号、1600-2400字、标记合规、唯一动作合规、8章节
  - **两稿比较**: P1遗漏、事件池外事实、唯一动作合规
  - 合规事件筛选: P0/P1 + confidence≠low + method≠rule_fallback + action_level=immediate|test

## Pipeline V3 (2026-05-02)

### 新流水线架构（17步+分支）
```
collect → source_url_monitor → broad_search_discovery → xcrawl_enrich_articles
  → merge_raw_articles → filter → quality_funnel_report → source_health_report
  → tavily_gap_search → filter_merged
  → branch:
    有信号: extract → score → analyze → generate_report → editor_review → generate_podcast → send_email
    无信号: generate_no_signal_report → editor_review → generate_podcast → send_email
```

### 新增步骤
- **source_url_monitor**: Web Monitor发现新URL → `newly_discovered_urls.json`
- **broad_search_discovery**: 多搜索源×多关键词矩阵发现 → `broad_search_urls.json`
- **xcrawl_enrich_articles**: 为搜索发现的URL抓取正文 → `xcrawl_enriched_articles.json`
- **merge_raw_articles**: 合并所有采集来源到 `raw_articles_all.json`
- **quality_funnel_report**: 漏斗诊断报告, 19维度拆解, 自动诊断建议

### 关键设计
- **filter** 必须针对 `raw_articles_all.json`（合并后的全集）
- **broad_search_discovery** 默认 `skip_merge=True`，合并由 `merge_raw_articles` 统一处理
- **xcrawl_enrich_articles** 支持多个输入源自动去重
- **所有搜索结果必须先抓正文或至少保留摘要，再进入filter**
- 不改变 sendable 安全门

### 来源统计（日志输出）
- rsshub_count, rss_count, web_count
- source_url_monitor_count
- broad_search_count
- xcrawl_enriched_count
- tavily_gap_count

### merge_raw_articles 合并优先级
1. `raw_articles.json` — 主采集
2. `newly_discovered_urls.json` — Web Monitor
3. `broad_search_urls.json` — 广泛搜索
4. `xcrawl_enriched_articles.json` — XCrawl抓取
5. `tavily_gap_articles.json` — Tavily补搜

### broad_search_discovery 模块
- **路径**: `skills/broad_search_discovery/broad_search_discovery.py`
- **配置**: `config/source_packs.yaml`
- **搜索矩阵**: platforms(6) × categories(8) × actions(9) = 多组合
- **搜索源**: Tavily(6 key round-robin) + XCrawl(7 key)
- **两级搜索**: Tavily day→week fallback
- **输出**: `broad_search_urls.json` (每条含source_type, discovery_type, matched_keywords)
- **Bug修复**: Tavily key 从 `tavily_key`/`tavily_key1-6` 读取; budget配置从顶层继承了
- **测试结果**: 4 query → 39 articles (20 Tavily + 19 XCrawl + 1 dedup); HK/TW噪音6篇被V4过滤拦下

### xcrawl_enrich_articles V2 升级
- **多输入源**: newly_discovered_urls.json + broad_search_urls.json + tavily_gap_articles.json
- **URL级去重**: 对 raw_articles.json/raw_articles_all.json 已有URL去重
- **URL级并行**: 逐URL抓取, 1-2s延迟
- **统计**: by_key, by_domain, by_source
- **保留collector来源**: 传入文章的collector/source_type字段原样保留
- **兼容raw_articles.json格式**: 输出结构包含metadata和articles

### 测试结果 (2026-05-02 合并)
- raw_articles: 279 → merged total: 449 articles
- 来源分布: rsshub=107, source_url_monitor=131, broad_search=39, xcrawl_enriched=154, tavily_gap=18
- xcrawl_enriched 10条全部与已有数据去重

## Pipeline V2 (2026-04-28) [旧版，参考]

### 无信号日报分支
- 触发条件: filter_merged 后 cleaned_count == 0
- 跳过: extract, score, analyze, generate_report
- 执行: generate_no_signal_report → editor_review
- 无信号日报 ≠ 系统故障，是诚实输出

### 关键设计决策
- `filter_merged` 用 `raw_articles_merged.json`（tavily gap search 合并后）重跑 `filter_relevant_articles(use_llm=False)`
- tavily_gap_search 内部已调用 filter（内置重过滤），但 pipeline 也显式跑一次 filter_merged 保证一致性
- source_health_report 和 tavily_gap_search 都是 best-effort（非阻塞）
- generate_no_signal_report 输出到 `data/reports/{date}/daily_report_{date}_no_signal.md`

## Tavily Gap Search (2026-04-27)
- **Skill**: `skills/tavily_gap_search/tavily_gap_search.py`
- **定位**: RSS采集后的补漏机制，不替代RSSHub
- **触发条件**: cleaned_count<5 / 核心平台缺失 / reference有线索但cleaned不足
- **核心平台**: 美团闪购、京东到家/秒送、淘宝闪购/饿了么、抖音小时达
- **补搜query**: 18条（平台8+竞对5+品类5），从GAP_SEARCH_QUERIES常量定义
- **输出**: `tavily_gap_articles.json` + `raw_articles_merged.json` + 重过滤后的cleaned/reference/rejected
- **去重**: 与raw_articles.json做URL去重
- **重过滤**: 合并后自动运行filter_relevant_articles(use_llm=False)
- **Tavily Key**: 从环境变量读取(支持逗号分隔多key) + collect模块兼容(tavily_key/tavily_key1/tavily_key2)
- **测试结果(04-27)**: 触发(cleaned=0,4平台缺失) → 18 query → 60返回 → 52去重 → 0→33 cleaned → 4平台全覆盖
- **432 rate limit**: 部分query遇到432错误(key3额度不足)，key轮换自动跳过

## Collect Optimization (2026-04-27)
- **filterout_time**: RSSHub请求自动添加 `filterout_time=N*86400` 参数，排除N天外的旧文章
  - 全局默认: 90天 (`DEFAULT_FILTEROUT_DAYS = 90`, sources.yaml defaults.filterout_days = 90)
  - 源级配置可覆盖: `filterout_days: 0` 表示不加此参数
  - 搜索源(rsshub_search)全部90天, 文章/36kr 60天, 频道源不加(内容天然按时间排列)
- **allow_old过滤**: collect去重后根据源的 allow_old 配置丢弃 old 文章
  - allow_old=false (默认): old文章在collect阶段就丢弃, 不进入filter
  - allow_old=true (如屈臣氏搜索、晚点LatePost): 保留old文章进入filter, 由filter处理
- **窗口扩展**: compute_time_window 新增 extend_to_now=True
  - 晚间运行时 window_end 从 date 07:00 扩展到当前时刻
  - 晨间运行时 window_end 仍是 date 07:00 (无需扩展)
- **pipeline默认date**: 从 yesterday_date() 改为 today_date()
- **效果对比** (date=04-27 vs date=04-26):
  - 修改前: 692采集→300截断→0清洗(全部old)→D类源占6%
  - 修改后: 553采集→247 old被allow_old过滤→53保存(24 in_window)→B类源4个
- **score_novelty bug修复**: 添加 `fact = event.get("fact", "") or ""` 修复未定义变量引用
- **Skill**: `skills/source_health_report/source_health_report.py`
- **位置**: collect + filter 之后运行（暂未集成到 pipeline 主流程）
- **输入**: raw_articles.json, cleaned_articles.json, reference_articles.json, rejected_articles.json
- **输出**: `data/logs/YYYY-MM-DD/source_health_report.json` + `.md`
- **分级**: A(keep_primary) / B(keep_secondary_limit) / C(reference_only) / D(disable_candidate)
- **2026-04-26 实际结果**: 18个源，0个A类，1个B类(虎嗅消费频道)，16个C类(reference_only)，1个D类(虎嗅文章专栏)
- **核心发现**: 虎嗅搜索类源100%是old文章（因为虎嗅RSS返回的是搜索结果而非最新文章），但有高关键词匹配率→C类(reference_only)
- **唯一B类源**: rsshub_huxiu_consumer_channel (33% recent, 但cleaned_yield=0)
- **唯一D类源**: rsshub_huxiu_article (100% old, 0% keyword匹配)

## Phase 2 LLM Parallelization (2026-04-28)

### extract_events 并行化
- **文章级并行**: `parallel_map` 替代原始 `ThreadPoolExecutor`
- **并发**: 默认 4 workers (parallel.yaml 可配到 6)
- **模型策略**: Lite 为默认, Chat 为失败重试 (不使用 Thinking)
- **Checkpoint**: `extract_events_checkpoint.jsonl` (JSONL, per-article)
- **重试**: 首次 Lite 失败 → Chat 重试一次 → 仍失败则 rule_fallback
- **顺序稳定**: 输出按 article index 排序
- **resume支持**: 下次运行可跳过已 checkpoint 的文章 (当前清除旧checkpoint后运行)

### analyze_business_impact 并行化
- **事件级分批并行**: `batch_parallel_map` 替代混合 ThreadPoolExecutor
- **ARCHIVE**: 跳过 LLM, 纯规则 (串行, workers=0)
- **P2**: LongCat-Flash-Lite, 6 并发, 60s timeout
- **P1**: LongCat-Flash-Chat, 4 并发, 120s timeout
- **P0**: LongCat-Flash-Chat, 2 并发 (低并发), 150s timeout
- **Checkpoint**: `analyze_business_checkpoint.jsonl` (JSONL, per-event)
- **fallback_to_rule**: LLM 失败回退规则分析 (默认 true)
- **顺序稳定**: 输出 events_analyzed.json 与 events_scored.json 顺序一致
- **Bug 修复**: 返回 dict 中补充 `llm_success_count` 字段

### parallel_runner.py 增强
- 新增 `batch_parallel_map`: 按批次分组并行, 每批独立并发度
- 新增 `save_checkpoint` / `load_checkpoint` / `clear_checkpoint`: JSONL 格式断点续传

### parallel.yaml 配置
```yaml
extract_events:
  article_parallel:
    enabled: true, max_workers: 4, article_timeout: 180
    model_strategy: {default: LongCat-Flash-Lite, fallback: LongCat-Flash-Chat, skip_thinking: true}
    retry_on_failure: true, retry_model: LongCat-Flash-Chat
    checkpoint_file: extract_events_checkpoint.jsonl

analyze_business_impact:
  event_parallel:
    enabled: true, fallback_to_rule: true
    checkpoint_file: analyze_business_checkpoint.jsonl
    priority_batches:
      ARCHIVE: {use_llm: false, max_workers: 0}
      P2: {use_llm: true, model: LongCat-Flash-Lite, max_workers: 6, timeout: 60}
      P1: {use_llm: true, model: LongCat-Flash-Chat, max_workers: 4, timeout: 120}
      P0: {use_llm: true, model: LongCat-Flash-Chat, max_workers: 2, timeout: 150}
```

### 测试结果 (2026-04-26)
- extract_events: 81.8s, 38/38 文章成功, 0 失败, 0 规则兜底
- score_events: 0.1s (纯规则)
- analyze_business_impact: 59.2s, 56 P2 LLM成功, 0 失败, 53 ARCHIVE 跳过
- 端到端完整性: ✅ 事件顺序一致, 0 缺失字段

## Resource Optimization (2026-04-26)
- **model_router.yaml**: 5 skill 路由 + 3 模型定义 + 全局 fallback
- **search_policy.yaml**: Tavily budget + fixed/gap/verify 搜索策略
- **llm_client.py**: chat() 新增 model 参数
- **model_router.py**: 新工具模块
- **4 modules modified**: filter, extract_events, analyze_business_impact, generate_daily_report
- **Backward compatible**: model_router.yaml 不存在 ⟹ 使用默认模型

## V4 采集筛选优化 (2026-05-02)

### 已修复
1. `is_search_source` 改为 `source_type` 精确匹配 → `real_search_types = {"xcrawl", "tavily", "search", "gap"}`
2. XCrawl/Tavily 日期提取: 去掉 `default_dt=window_end` 兜底，加了 `_extract_date_from_url()` 从URL提取日期
3. `unknown_time` 处理: 搜索源+高关键词→reference, 其余→reject
4. `old` 搜索源文章: 不进main，最高进reference（背景资料）
5. 采集阶段 `allow_old` 过滤: 移除，所有文章传给filter阶段处理
6. XCrawl queries: 从28个死板行业术语→28个热点追踪型查询
7. 去掉了query后面追加日期后缀（之前追加"2026年5月2日"缩窄结果）
8. `old` 搜索源评分: -3（之前-1）; `old` 非搜索源: -4（之前-2）
9. Main池门槛: `review` + `in_window` + `score>=5` → main（之前review→reference）
10. RSSHub超时: 20s→30s; XCrawl max_results: 10→20

### 当前状况 (V4, 2026-05-02)
- 采集: 505篇 (XCrawl 251, RSSHub 246, Tavily 8)
- XCrawl日期提取率: 70% (之前7%)
- Main: 4篇 | Reference: 176篇 | Rejected: 325篇

### 仍需优化
- RSSHub 36kr_newsflashes 返回68篇无关通用新闻（需要精简或替换）
- RSSHub 虎嗅/晚点全部超时失败
- Tavily 结果被XCrawl大量重叠去重
- XCrawl 30%仍为unknown_time（静态页面/百科无日期）
- 36kr 反爬: 多次请求触发验证码，考虑仅首次运行用web_monitor

### Tavily 两级时间范围升级 (2026-05-02)
- **改动文件**: `collect_daily_articles.py`, `filter_relevant_articles.py`, `config/search_policy.yaml`
- **两级搜索**: 默认 `time_range="day"` → 不足 `min_day_results`(默认3) → 降级 `time_range="week"`
- **freshness_status 新字段**: `day_primary` / `week_fallback` (Tavily); `newly_discovered` (web_monitor)
- **week_fallback 结果去重**: day 轮已存在的URL不会在week轮重复出现
- **域名过滤**: `include_domains` / `exclude_domains` 支持; `domain_scope` 映射垂直站点列表
- **include_raw_content="text"**: 替代旧布尔值 True，返回纯文本正文

### Cleaned 准入规则 V4 — 噪音过滤 (2026-05-02)
- **改动文件**: `filter_relevant_articles.py`
- **新增3个分类器**:
  - `classify_page_type()`: news/article/report/official_notice/product_page/homepage/social_post/promotion/job/unknown
  - `classify_region_tag()`: mainland/hk/tw/overseas/unknown
  - `compute_noise_flags()`: 噪音标记列表
- **噪音硬拦截规则**:
  - product_page, homepage, social_post, promotion, job, unknown 不得进入 cleaned（降级为 reference 或 reject）
  - social_post 和 homepage 直接 reject（降级也 reject）
  - hk/tw 地区屈臣氏促销页直接 reject
  - hk/tw + promotion/homepage/product_page 直接 reject
- **噪音降级机制 `_noise_downgrade()`**:
  - main → reference（保留但不在报告中使用）
  - reference → 可能 reject（social_post/homepage/job 直接 reject）
  - 所有 `decide_final_pool()` 返回点都经过 `_noise_downgrade()`
- **输出新增字段**: `filter.page_type`, `filter.region_tag`, `filter.noise_flags`
- **元数据新增**: `by_page_type`, `by_region`, `by_noise`
- **分类依据**:
  - page_type: URL模式(/product/,/item/等)、社媒域名、品牌官网首页、促销关键词
  - region_tag: 域名(.hk/.tw/.com.hk/.com.tw)、繁体字、HK/TW关键词
  - noise_flags: page_type阻断、region标记、official_site_title、promo_title、social_content、thin_content、hk_tw_watsons_promo
- **V4 测试结果** (2026-05-02 五一数据):
  - 279篇输入, cleaned=0, reference=147, reject=132
  - 所有噪音（香港屈臣氏促销、Instagram帖子、官网首页、产品页）均被正确拦截
  - page_type统计: news=155, article=60, report=3, unknown=28, product_page=16, social_post=11, promotion=1, homepage=1, job=4
  - region统计: mainland=265, hk=9, tw=5
  - 香港屈臣氏促销4篇被hk_tw_watsons_promo标签直接reject

### 之前的准入规则 (V3 保留用于参考)
- **核心命中快通道 `has_core_hit()`**: 4类核心关键词
- **freshness_status路径**: newly_discovered/day_primary/week_fallback 各有不同准入条件

### 重点监控源精简 (2026-05-02)
- **精简到约30个源** (9 RSS + 17 XCrawl + 4 web_monitor)
- **禁用的噪音源**: 虎嗅消费频道、36kr搜索系列、小红书搜索等7个RSS源
- **禁用的XCrawl查询**: 11个低质量/重叠查询（万亿、淘宝闪购、外卖大战等）
- **4类源结构**:
  1. 平台官方/规则: 6个 (XCrawl 01/02/04/05 + web_36kr*2)
  2. 即时零售/零售媒体: 5个 (RSS晚点/虎嗅*2/屈臣氏搜索 + web界面)
  3. 美妆个护垂类: 6个 (XCrawl 15-17/21-23)
  4. 竞对/品牌官方: 3+4个 (XCrawl 18-20竞对 + 11-14屈臣氏)

## Source URL Monitor + XCrawl Enrich (2026-05-02)

### 架构
```
source_url_monitor.py → newly_discovered_urls.json → xcrawl_enrich_articles.py → xcrawl_enriched_articles.json
```

### source_url_monitor
- **路径**: `skills/source_url_monitor/source_url_monitor.py`
- **功能**: 对 config/sources.yaml 中 monitor.enabled=true 的来源，抓取列表页提取文章URL
- **输出**: `data/raw/{date}/newly_discovered_urls.json`, `data/source_ledger/seen_urls.jsonl`
- **站点适配器**: Kr36Parser(SSR), LatepostParser, JiemianParser, EbrunParser, GenericParser
- **测试结果**: 125 newly_discovered (36kr 50 + 界面 61 + 晚点 14)
- **去重**: 第二次运行 seen=78, new=0 ✅
- **已知问题**: 36kr触发反爬(返回验证码页面)；虎嗅/亿邦有WAF无法抓取
- **反检测**: 随机延迟2-5s(来源间)、1.5-3s(列表页间)、验证码检测、最多2次重试

### xcrawl_enrich_articles
- **路径**: `skills/xcrawl_enrich_articles/xcrawl_enrich_articles.py`
- **功能**: 读取 newly_discovered_urls.json，调用 XCrawl scrape API 获取正文
- **输出**: `data/raw/{date}/xcrawl_enriched_articles.json`, `xcrawl_enrich_stats.json`
- **关键**: 必须指定 `OutputConfig(formats=["markdown","html"])`，否则只返回metadata
- **XCrawl SDK返回dict而非对象**: 用 `resp["data"]["markdown"]` 而非 `resp.data.markdown`
- **7 key轮换**: round-robin，每key ~1000 credits/month
- **测试结果**: 10/10 成功，内容6K-20K字符/篇
- **信用消耗**: 1 credit/URL

### config/sources.yaml web_monitor_sources (4个)
1. **web_36kr_newsflashes**: 快讯+科技 (Kr36Parser, SSR数据)
2. **web_latepost**: 晚点LatePost (LatepostParser, /news/dj_detail)
3. **web_jiemian_business**: 界面新闻首页 (JiemianParser, /article/)
4. **web_36kr_more**: 36kr金融板 (Kr36Parser, 可能限流)

## Persistent Package Management (2026-05-10)

### 问题
`/app/venv/` 是 QwenPaw 系统级虚拟环境，`pyvenv.cfg` 中 `include-system-site-packages = false`，
且每次服务重启/会话重建时 `venv` 会从零重建（参考 `.lock` 文件时间戳），
导致 `pip install xcrawl edge-tts` 等在下次会话丢失。

### 方案：项目本地持久安装
- **安装目录**: `${PROJECT_ROOT}/.venv_packages/`（不受 venv 重建影响）
- **安装命令**: `pip install --target .venv_packages xcrawl edge-tts`
- **路径注入**: 所有入口脚本在最顶部 `sys.path.insert(0, ".venv_packages")`

### 自愈机制（三层防护）
1. **cron 启动前**: `pip_ensure.py --project-root . --quiet` — 检查+补装
2. **health_check 运行时**: `python_packages` 类别设为 `auto_fix: true`，缺包自动调用 pip_ensure
3. **pipeline 入口**: `run_daily_pipeline.py` 顶部注入 `.venv_packages`

### pip_ensure.py
- **位置**: `skills/health_check/pip_ensure.py`
- **用法**: `python3 skills/health_check/pip_ensure.py --project-root .`
- **检查 8 个关键包**: xcrawl, edge-tts, pyyaml, markdown-it-py, beautifulsoup4, jinja2, aiohttp, requests
- **缺包时**: `pip install --target .venv_packages <missing>` → 复检

### XCrawl Key 状态 (2026-05-12)
- **`xcrawl_key`**: 当前额度耗尽 (401 auth_failed)，**轮换自动跳过，下月恢复**
- **`xcrawl_key1`~`xcrawl_key6`**: 6 个正常
- **轮换机制**: `XCrawlSearchEngine._dead_keys` + `XCrawlKeyRotator.mark_dead()` — 首次失败后自动标记跳过
- **健康检查**: `auth_failed` / `quota_exceeded` / `rate_limited` 归类为"软失败"
  - 软失败 + 至少一个 key 正常 → `healthy`（不报警）
  - 全部软失败 → `degraded`（不报 down）
  - 硬失败（timeout/connection/sdk）→ 按严重级别正常报警
- **设计意图**: 任何 key 额度用完都会自动跳过，下月恢复，无需人工干预。硬故障才报警。

### Cron Job
- **ID**: `873a2bd1-691f-4ae2-b8ad-6ef9d3745467`
- **名称**: watsons-daily-0700
- **调度**: 每日 07:00 Asia/Shanghai, timeout 7200s
- **命令**: Step 0 → pip_ensure.py --project-root . --quiet; Step 1 → run_daily_pipeline.py

## Session 7 优化 (2026-05-21)

### 完成的优化
1. **podcast_review 适配方案C** — action_items 正则支持"第一，"格式；outline 不存在时不阻塞
2. **Pipeline 非关键步骤容错** — NON_CRITICAL_STEPS (14个) 失败时自动跳过
3. **日报禁用播客转化路径** — 方案C下日报独立从事件池生成
4. **日报 select_top_signals 实体多样性** — 同一主实体不重复出现在 top 3
5. **日报+播客都做同日事件去重** — deduplicate_events(threshold=0.50)
6. **日报 tracking 事件传入 LLM** — 用于第11节"近期延续观察"
7. **日报模型升级 Chat→Thinking** — max_tokens 8192，解决截断
8. **enrich_cleaned_fulltext 增加 requests+BS4** — CloakBrowser → requests → XCrawl
9. **禁用虎嗅3个全频道源** — 0%通过率纯噪音
10. **启用36kr快讯+3个平台搜索源** — strict_keywords + filterout_days
11. **broad_search 排除港台域名** — 减少21篇/天非大陆内容

### 实测结果
- 日报: 10135字节/10章节/3信号/40标签（完整无截断）
- 播客: 2796中文字符/TTS成功/sendable=True
- 事件去重: 77→61（-21%）
- top 3 信号多样性: 阿里/苏宁/杭州低空

### Git commits
- 9edeb5b: 播客方案C重构 + 事件去重 + 跨日去重修复
- 6d68218: pipeline 鲁棒性 + 去重扩展 + 源优化 + 全文补抓增强
- 9b9c497: 日报生成质量提升 + 模型升级