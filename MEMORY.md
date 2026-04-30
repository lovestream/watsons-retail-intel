# Watsons Retail Intel — Project Memory

## Project Overview
- **Name**: 即时零售 × 个护美妆经营情报系统
- **Root**: `/app/working/projects/watsons-retail-intel`
- **Purpose**: Daily intelligence pipeline for Watsons retail — collect → filter → extract events → score → report

## Architecture & Data Flow
```
collect → filter → extract_events → score_events → analyze_business_impact → generate_daily_report
data/raw/  data/cleaned/  data/events/  data/events/  data/events/           data/drafts/
```

## Config & Secrets
- **LLM**: Longcat API (`https://api.longcat.chat/openai`), 3 models available
- **LLM Keys**: 6 keys (`longcat`–`longcat5`) round-robin, 5M tokens/day each
- **Tavily**: 3 accounts, 1000 searches/month each, round-robin
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
  - generate_daily_report: Thinking → Chat fallback
  - generate_daily_report: Chat(默认) → Lite(fallback); Thinking仅optional_deep_review
  - editor_review: Chat(默认) → Lite(fallback); Thinking仅optional_deep_review
- **Backward compat**: model_router.yaml 不存在时使用默认模型 (LongCat-Flash-Thinking)
- **llm_client.chat()** 新增 `model` 参数，可覆盖 `self.model`

## Search Policy (新!)
- **Config**: `config/search_policy.yaml`
- **Tavily**: enabled, daily_budget=80, reserve_budget=20
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
| filter_relevant_articles | ✅ V2 tested | `skills/filter_relevant_articles/` |
| source_health_report | ✅ tested | `skills/source_health_report/` |
| tavily_gap_search | ✅ tested | `skills/tavily_gap_search/` |
| extract_events | ✅ V2 parallel (Lite→Chat retry+checkpoint) | `skills/extract_events/` |
| score_events | ✅ V1 tested | `skills/score_events/` |
| analyze_business_impact | ✅ parallel (batch ARCHIVE/P2/P1/P0+checkpoint) | `skills/analyze_business_impact/` |
| generate_daily_report | ✅ V2 three-draft tested | `skills/generate_daily_report/` |
| generate_no_signal_report | ✅ V1 tested | `skills/generate_no_signal_report/` |
| generate_podcast | 🔧 WIP (模板字数不足) | `skills/generate_podcast/` |
| editor_review | ✅ V2 dual-draft tested | `skills/editor_review/` |
| run_daily_pipeline | ✅ V2 tested (10步+no_signal分支) | `skills/run_daily_pipeline/` |

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

## Pipeline V2 (2026-04-28)

### 新流水线架构（10步+动态分支）
```
collect → filter → source_health_report → tavily_gap_search
  → filter_merged → [no_signal 或 normal分支]
    normal:  extract → score → analyze → generate_report → editor_review
    no_signal: generate_no_signal_report → editor_review
```

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
- **extract_events 路由变更**: 移除 Thinking 直接抽取高价值事件逻辑; 改为 Lite→Chat 二次重试→rule_fallback+needs_verification