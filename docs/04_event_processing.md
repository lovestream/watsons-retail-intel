# 04. 事件加工子系统审计 — extract / temporal / novelty / score / analyze

> 只读审计 · 以真实代码为准（未运行 git / 未发邮件 / 未改动仓库）
> 审计对象：Watson's Retail Intel — 即时零售 × 个护美妆经营情报系统
> 日期：2026-10-24

## 0. 子系统边界与管线位置

事件加工子系统把「已清洗文章」加工成「带优先级与经营影响分析的事件」，供日报/周期报告使用。
管线步骤顺序（`skills/run_daily_pipeline/run_daily_pipeline.py` 第 86-92 行的步骤表，`STEP_ORDER`）：

```
extract → temporal_fact_validation → novelty_check → score → analyze → evergreen_candidates
```

（`extract`=事件抽取、`temporal_fact_validation`=时效判断、`novelty_check`=新颖性、
`score`=评分、`analyze`=经营分析）

涉及目录（以真实代码为准）：

| 技能目录 | 主文件 | 产出 |
|---|---|---|
| `skills/extract_events/` | `extract_events.py` | `data/events/{date}/events_raw.json` + `events_rejected_articles.json` + `events_failed_articles.json` + `data/logs/{date}/extract_events.log` |
| `skills/temporal_fact_validation/` | `temporal_fact_validation.py` | `data/events/{date}/events_temporal.json` + `data/logs/{date}/temporal_fact_validation.log` + `temporal_stats.json` |
| `skills/event_novelty_check/` | `event_novelty_check.py` | `data/events/{date}/events_scored_novelty.json` + 更新 `data/event_ledger/events_seen.jsonl` |
| `skills/score_events/` | `score_events.py` | `data/events/{date}/events_scored.json` + `data/logs/{date}/score_events.log` |
| `skills/analyze_business_impact/` | `analyze_business_impact.py` | `data/events/{date}/events_analyzed.json` + `data/logs/{date}/analyze_business_impact.log` |
| `skills/utils/` | `parallel_runner.py`（`parallel_map`/`batch_parallel_map`/checkpoint） | 跨技能并行与断点续跑工具 |
| 配置 | `config/scoring.yaml`、`config/parallel.yaml` | 权重/阈值/来源映射；并行策略 |

事件数据在阶段间**单向串流**（后一阶段读前一阶段输出，逐层叠加字段）：

```
events_raw.json ──temporal──▶ events_temporal.json ──novelty──▶ events_scored_novelty.json ──score──▶ events_scored.json ──analyze──▶ events_analyzed.json
```

> 注意：各 `SKILL.md` 的「输入文件」描述有些滞旧（如 event_novelty_check 的 SKILL.md 仍写
> 「读取 events_scored.json」），**以代码为准**。实际读取优先级见 `event_novelty_check.py`
> 主函数与 `score_events.py` 主函数中的 `resolve_path` 候选列表。

---

## 1. 事件数据结构

### 1.1 event_id 生成规则

`extract_events.py` 顶部：

```python
def generate_event_id(date: str, index: int) -> str:
    date_compact = date.replace("-", "")
    return f"E{date_compact}_{index:04d}"
```

即 `E {YYYYMMDD} _ {4位零填充序号}`，例：`E20260809_0001`。`event_index` 从 `1` 起，
在抽取主流程中对每条**未被 reject 的 LLM 事件** `event_index += 1` 后分配（`extract_events.py`；
规则兜底 `rule_fallback_extract`/`rule_extract_events` 也用它生成并回传下一个序号）。
因此 event_id 顺序号是**当日全事件池的全局递增序号**，不因批处理而重复。

### 1.2 字段全集（事件对象）

抽取阶段由 `_extract_from_parsed` 用 `setdefault` 补齐必要字段，随后主流程再补齐一批字段：

- **抽取核心字段**：`event_id`、`event_title`、`event_type`、`fact`、`evidence_text`
- **分类/枚举字段**：`business_variables`（列表，来自 `BUSINESS_VARIABLES`）、
  `time_sensitivity`（`today|recent|background`）、`novelty`（`new|follow_up|old_background|unclear`）、
  `confidence`（`high|medium|low`）
- **实体字段** `entities`：`{platforms, companies, competitors, brands, channels, categories}`（均为列表）
- **时间字段**：`event_occurred_at` / `event_announced_at` / `event_effective_at`（YYYY-MM-DD 或空）、
  `event_time_precision`（`day|week|month|quarter|year|unknown`）、`event_time_confidence`（`high|medium|low`）、
  `temporal_evidence`
- **溯源字段**：`extraction_method`（`llm` / `rule_fallback` / `rule_extraction`）、
  `source_article_id`、`source_title`、`source_name`、`source_url`、`published_at`、`is_reference`
- **拒绝字段**：`reject`（bool）、`reject_reason`
- **时效新增**（temporal 阶段叠加）：`cluster_key`、`fact_hash`、`article_time_status`、
  `published_at_source`、`published_at_confidence`、`page_type`、`event_occurred_at` 等时间、
  `content_dates`、`historical_date_ratio`、`is_retrospective`、`fact_freshness_status`、`temporal_eligibility`、`temporal_reasons`
- **新颖性新增**（novelty 阶段叠加）：`cluster_key`、`fact_hash`、`novelty_status`、
  `first_seen_at`、`last_seen_at`、`last_reported_at`、`report_count`、`report_eligibility`、
  `fact_first_seen_at`、`fact_seen_count`、`fact_has_new_delta`、`new_delta_fields`
- **评分新增**（score 阶段叠加）：`scores`（八维）、`weighted_score`、`priority`、`score_reasons`、`downgrade_reasons`
- **经营分析新增**（analyze 阶段叠加）：`business_analysis`（见 §6）

### 1.3 事件类型与业务变量枚举

`extract_events.py` 顶部常量：

```python
EVENT_TYPES = ["platform_move","platform_rule","competitor_move","brand_move",
    "category_trend","channel_shift","consumer_scene","data_signal",
    "policy_signal","background_only","unclear"]
BUSINESS_VARIABLES = ["流量","转化率","客单价","复购","价格","补贴","毛利",
    "货盘","SKU","履约","会员","私域","活动","投流","门店覆盖",
    "平台资源位","组织执行","竞争格局","品类机会","风险"]
```

（注意：LLM 提示词里的 `policy_change / consumer_trend / new_product / supply_chain / marketing` 等
事件类型在 `score_events.py`、`analyze_business_impact.py` 的映射表中出现，但**不在** `EVENT_TYPES`
抽取枚举里——抽取层只认上面 11 种，评分/分析层对未知事件类型会回退到 `1`/`noise`。）

### 1.4 输出文件结构

`events_raw.json`（metadata version `3.0`）：

```json
{
  "metadata": {
    "version": "3.0", "date": "...", "source_file": "...",
    "created_at": "...",
    "total_articles": N, "total_events": N,
    "rejected_articles": N, "failed_articles": N,
    "llm_success_count": N, "llm_failed_count": N,
    "retry_success_count": N, "retry_failed_count": N, "rule_fallback_count": N,
    "by_event_type": {...}, "by_confidence": {...}, "by_source": {...},
    "by_failure_type": {...}, "top_business_variables": {...}
  },
  "events": [ ... ]
}
```

`events_rejected_articles.json`：`metadata`（含 `reject_reason_stats`）+ `articles`（业务拒绝文章，附 `reject_reason`）。
`events_failed_articles.json`：`metadata`（含 `failure_type_stats`）+ `articles`（技术失败文章，
字段含 `failure_type`/`failure_reason`/`retry_required`/`raw_llm_preview`/`attempt_count`）。

`events_temporal.json`（metadata version `2.0`）：`metadata.freshness_stats`、
`republished_old_fact_count`、`future_date_article_count` + `events`。

`events_scored_novelty.json`（metadata version `1.0`）：`metadata.novelty_stats` + `events`（含新颖性字段）。

`events_scored.json`（metadata version `2.0`）：`metadata` 含 `p0_count/p1_count/p2_count/archive_count`、
`by_priority`、`by_event_type`、`by_confidence`、`avg_weighted_score`、`top_10_events`、
`downgrade_stats`、`weights`、`thresholds` + `events`。

`events_analyzed.json`（metadata version `1.0`）：`metadata` 含 `event_count`、`analyzed_count`、
`llm_success_count`、`llm_failed_count`、`by_priority`、`by_impact_type`、`by_action_level`、
`by_owner_hint`、`immediate_count/test_count/watch_count/archive_count`、`downgrade_stats`、
`top_action_events` + `events`。

---

## 2. 抽取流程（extract_events.py）

### 2.1 输入与两池文章

读取 `data/cleaned/{date}/cleaned_articles.json` 的 `articles`（main 池）；若存在
`data/cleaned/{date}/reference_articles.json` 则并入 reference 池，**只纳**
`time_status ∈ {in_window, near_window}` 或 `uncertain_date`，或 `unknown_time/old` 且
`campaign_temporality ∈ {current_campaign, upcoming_campaign}` 的 reference 文章，
其余跳过（防去年双11旧闻污染）。source 文章打 `is_reference` 标记（参考池为 `True`、main 池为 `False`）。
`max_articles` 限流：优先保留 main 池，其余按 `filter.rule_score` 降序补参考文章。

失败路径：若 `cleaned_articles.json` 无法加载，直接返回 `{ok:False}` 且不产出事件文件。

### 2.2 LLM 抽取提示词

- 系统提示词 `LLM_SYSTEM_PROMPT`（extract_events.py）：约束「只抽明确发生的事实事件、
  禁止日报/过度推断、只输出一个 JSON 对象」，内含 `confidence` 评定标准
  （high=事实明确+数据/官方来源+时新；medium=较明确但缺数据/近期非当天；low=模糊/趋势观点，
  **明确提醒「不要默认打 low」**），并拼接 `EVENT_TYPES` 与 `BUSINESS_VARIABLES` 枚举。
- 用户提示词 `LLM_USER_PROMPT_TEMPLATE`：注入 `article_id/title/source_name/published_at/summary/content`
  （正文截断到 2000 字符），给出 JSON 骨架与**时间字段填写要求**（`event_occurred_at`/
  `event_announced_at`/`event_effective_at`/`event_time_precision`/`event_time_confidence`/
  `temporal_evidence`，并提示「回顾/复盘/去年=历史；今天/昨日/发布=近期」）。
- 极简重试提示词 `SIMPLIFIED_SYSTEM_PROMPT` / `SIMPLIFIED_USER_TEMPLATE`
  （正文截到 800 字符，最多 3 个事件，无复杂字段）——用于二次重试、切换 fallback 模型。

`llm_extract_events()` 单篇流程：
1. 首次尝试：完整 prompt，`temperature=0.15, max_tokens=4096, response_format="json"`，
   使用默认模型。
2. `_extract_from_parsed` 校验/规范化（setdefault 补齐字段；非法枚举回退：
   `event_type→unclear`、`time_sensitivity→background`、`novelty→unclear`、`confidence→low`；
   `fact` 为空则 `reject=true` + `reject_reason="fact为空"`）。
   **关键：空事件但 `article_reject` 未置位**时，若标题命中疑似事件关键词
   `["屈臣氏","GMV","突破","增长","发布","推出","上线","合作","签约","开店","关闭","退出"]`
   → 标记 `_empty_events_suspect_failure=True`（疑为 thinking 模型把答案放 reasoning 导致），
   否则保守归业务拒绝。
3. 失败分类 `_classify_failure`：`timeout/超时→timeout`；`connection/连接→llm_api_error`；
   `json_parse_failed`/`truncated_json`/`invalid_schema`/`empty_content`（截断判定看内容是否以
   `, " [ {` 结尾）。
4. 二次重试：极简 prompt + fallback 模型（`get_model_for_skill("extract_events")` 返回的
   fallback，`parallel.yaml` 也可覆盖），`temperature=0.1, max_tokens=2048`。`attempt_count=2`。
5. 返回 dict 携带 `failure_type/failure_reason/raw_llm_preview/attempt_count`。

### 2.3 并行调用与 checkpoint（Phase 1）

`extract_events()` 中从 `parallel_runner` 引用 `parallel_map/load_checkpoint/save_checkpoint/clear_checkpoint/load_parallel_config`。
读取 `parallel.yaml` 的 `extract_events.article_parallel`：

```
enabled:true, max_workers:4, article_timeout:180, retry_on_failure:true,
checkpoint_file: extract_events_checkpoint.jsonl,
model_strategy: {default: deepseek-v4-flash-free, fallback: deepseek-v4-flash-free, skip_thinking:true}
```

每次运行先 `clear_checkpoint`；加载 checkpoint 恢复已完成文章（跳过），剩余 `_pending_indices`
`parallel_map` 并行。单篇 `_extract_one_article`：默认模型(Lite)抽取→失败且 `retry_on_failure`
→切换 fallback 模型重试→（成功则标 `retry_used/retry_model`）→`save_checkpoint`。
不使用 Thinking 模型做 JSON 抽取（`skip_thinking:true`）。

### 2.4 规则兜底（rule_fallback）

**触发条件**：LLM 技术失败（`failure_type` 非空）或 `_empty_events_suspect_failure`，
且 `_should_rule_fallback(article)`=True（标题含 `RULE_FALLBACK_TITLE_KEYWORDS`：
`屈臣氏 / 美团闪购 / 京东秒送 / 京东到家 / 淘宝闪购 / 抖音小时达 / GMV / 增长 / 突破 / 合作 / 上线 / 入驻 / 加码 / 布局`）。

`rule_fallback_extract()` 生成**一条低置信事件**：
- `fact = f"[规则兜底] {source_name}报道：{title}"`；`event_type` 由 `_KEYWORD_EVENT_MAP`
  （约 40 个关键词→事件类型）命中决定，无命中→`unclear`；
- `time_sensitivity`：`time_status==in_window→today`，`near_window→recent`，其余 `background`；
- `confidence="low"`、`extraction_method="rule_fallback"`、`needs_verification=True`；
- `business_variables` 由 `_KEYWORD_BV_MAP` 命中合并（列表去重）。

**非 LLM 全文兜底** `rule_extract_events()`（LLM 完全不可用时的降级模式，或并行失败后的
`else` 分支逐篇调用）：对全文做关键词匹配，无任何类型命中且含 `屈臣氏/即时零售/小时达/同城零售`→
`background_only`（`has_watsons`），否则 `reject`（"规则无法匹配明确事件"）。
注意 `rule_extract_events` 产出的事件 `extraction_method="rule_extraction"`（区别于 `rule_fallback`）。

### 2.5 失败统计与三分流

`llm_success_count`（LLM 成功；`retry_used or attempt_count>1` 计入 `retry_success_count`）、
`llm_failed_count`（计 `by_failure_type`：`llm_api_error / timeout / json_parse_failed /
truncated_json / invalid_schema / suspect_empty_events / llm_exception`…）。

三分流：LLM 成功且 `article_reject`→ `events_rejected_articles.json`；技术失败→
`events_failed_articles.json`（`retry_required:true`）或规则兜底生成事件；成功且未 reject→事件池。

### 2.6 事件去重

`deduplicate_events(events, similarity_threshold=0.50)（主调用处用 0.50）`：
基于标题中文字符集 Jaccard 相似度；`sim>threshold` 且主体实体（companies/brands/platforms）有交集
→视为重复；`_event_richness_score` 决定保留更丰富版本并合并 `source_articles`（按 url 去重）。
返回 `(deduped, merge_log)`，主流程记录 `dedup_count`（`pre_dedup_count - len(all_events)`）。

---

## 3. 时效/事实校验（temporal_fact_validation.py）

### 3.1 要解决的问题

系统原用 `article.published_at` 判断新旧，无法识别「今天发布、但正文重新汇总去年旧闻」的
AI 转载/汇总文章。本技能把**文章发布时间**与**事实发生时间**分离，两层机制：
`article_date_sanity_check`（文章层）+ `temporal_fact_validation`（事实层）。

### 3.2 常量与工具

- 时区 `CST = timezone(timedelta(hours=8))`。
- `FUTURE_TOLERANCE_HOURS = 6`：`published_at > now + 6h` → `invalid_future_date`。
- `FRESHNESS_STATUSES`（9 态）：见 §3.5。
- `PAGE_TYPES`：`homepage/listing_page/tag_page/category_page/search_page/topic_page/article_page/unknown`。
- `DATE_SOURCE_CONFIDENCE`：`json_ld/html_time/og_metadata→high`；`url_date→medium`；`tavily/search_snippet→low`；空/未知→`unknown`。
- `parse_flexible_date`：支持带时区 ISO、`%Y-%m-%d`/`%Y/%m/%d`/`%Y.%m.%d`/`%Y年%m月%d日`/带时间、
  中文 `(\d{4})年(\d{1,2})月(\d{1,2})日`，结果归一到 CST。

### 3.3 对时间戳/回溯文的处理

- **未来日期拦截**：`article_date_sanity_check` 判定 `invalid_future_date`（不得进入事件池），
  附带 `published_at_source/published_at_confidence`。
- **页面类型识别** `classify_page_type`（第八节）：URL 正则（tags/topic/category/search/list/page）
  + 标题特征；列出 `listing/tag/category/search/page/homepage` 的**不直接成为新闻事件**，
  仅用于发现 URL。
- **正文日期提取** `extract_content_dates`（第五节）：抽绝对日期（年月日/纯数字/年月，去重挖除避免
  年月重复匹配）、相对时间（今天/昨天/前天/N天前/小时前/分钟前/周前）、营销节点
  （`_CAMPAIGN_PATTERNS`：今年 618/双11/双12/年货节…映射到今年；`unspecified_year` 仅记录不映射
  ——避免去年 618 旧文误判）。产出 `absolute_dates/relative_dates/campaign_refs/total_mentions/
  historical_mentions/recent_mentions/historical_date_ratio/oldest_date/newest_date`。
  `historical`=旧年份（`d.year < now.year`）；`recent`=近 7 天窗口。
- **事件时间字段填充** `validate_event_times`：优先用抽取层 LLM 给的 occurred/announced/effective；
  occurred 缺失则用正文最新日期近似（并默认 `precision=day, confidence=medium`）；
  announced 缺失则落 `published_at`；`temporal_evidence` 由各时间片段拼装。

### 3.4 历史账本（复用 event_novelty_check 的账本）

`load_ledger(data/event_ledger/events_seen.jsonl)` → `{cluster_key: entry}`。
`generate_cluster_key(event)` = `"{event_type}:{normalized_title_prefix}:{entity_hash}"`
（`normalize_for_cluster` 去日期/数字/标点，`title_prefix` 取前 40 字符，实体名排序取前 5 拼装；
超 200 字符则 hash）。
`generate_fact_hash(event)` = `md5(normalize(fact))[:12]`。
账本 entry 含 `first_seen_at/last_seen_at/fact_hash/fact_first_seen_at/fact_seen_count` 等。

### 3.5 事实新鲜度分类 `classify_fact_freshness`

判定优先级（与 `temporal_fact_validation.py` 注释一致）：
1. 文章日期未来 → `date_conflict`（eligibility=`reject`）
2. `event_effective_at` 未来 → `upcoming_fact`（`core`，**不误杀新政策**）
3-4. 账本同一 `cluster_key` 比对：
   - `_detect_new_delta` 命中（标题变化 >4 字 / fact 含动作词
     `新增 首次 上线 发布 宣布 出台 调整 升级 推出 试点 落地 开放`）→ `updated_fact`（core）
   - `fact_hash` 一致：首次出现 <3 天 → `ongoing_fact`（tracking）；≥3 天 → `republished_old_fact`（reject）
   - 同 cluster 异 fact → `updated_fact`
5. 回顾文 `hdr>=0.6 且 recent_mentions==0`：`title/fact` 含 `_RETROSPECTIVE_HINT`
   （回顾|复盘|时间线|盘点|总结|对比|大事记|本文由AI生成）→ `retrospective_summary`（reference），
   否则 `historical_background`（reference）
6. 事件发生时间：近 7 天 → `new_fact`（core）；旧年份 → `historical_background`；距今>30 天 →
   `historical_background`；今年 7-30 天 → `ongoing_fact`
7. 无法确定 → `unknown_fact_time`：标题/fact 含 `_HIGH_VALUE_SIGNALS`（即时零售/美团/京东/阿里/…
   屈臣氏/政策/补贴/上市/发布/收购…）→ eligibility=`review`（高价值走 temporal review），否则 `reference`。

### 3.6 准入映射

`FRESHNESS_ELIGIBILITY`：

```
new_fact:core  updated_fact:core  upcoming_fact:core  ongoing_fact:tracking
historical_background:reference  retrospective_summary:reference
republished_old_fact:reject  date_conflict:reject  unknown_fact_time:review（高价值）|reference（普通）
```

### 3.7 主函数行为与输出

逐事件：文章层 sanity → 页面类型（列表/标签/搜索页直接跳过并记录 `page_type` reject）→
正文日期提取 → 时间字段 → 账本比对 → 新鲜度分类 → 合并字段到事件（`is_retrospective` 当且仅当
`retrospective_summary|historical_background`）。
统计 `freshness_stats`（Counter），收集 Top20 `republished_old_fact` 与未来日期文章到
`data/logs/{date}/temporal_stats.json`。
输出 `events_temporal.json`（含全部时间字段）、`temporal_fact_validation.log`、`temporal_stats.json`。

### 3.8 9/9 测试

`tests/test_temporal_fact_validation.py`（第十二节 9 个案例，基准时间 `NOW=2026-08-09 10:00 CST`）：
1. `test_case1_new_fact` — 今天发生→`new_fact/core`
2. `test_case2_republished_old_fact` — 去年发生+账本已有同 fact_hash→`republished_old_fact/reject`
3. `test_case3_retrospective_summary` — 复盘去年多个事件（hdr≥0.6）→`retrospective_summary/reference`
4. `test_case4_split_background_and_update` — 旧背景拆为 `historical_background/reference` +
   今天新数据为 `new_fact/core`（**拆事件后各自独立判定，不整体 reject**）
5. `test_case5_upcoming_fact` — 宣布下周生效→`upcoming_fact/core`
6. `test_case6_future_published_date` — 文章日期未来 7 天→`date_conflict/reject`
7. `test_case7_listing_page` — tag/category/search/listing 页不成为事件
8. `test_case8_this_year_618` — 今年 618 不误杀（hdr=0）；未生效新规→`upcoming_fact`
9. `test_case9_ai_rewrite_old` — AI 重写去年双11（hdr≥0.6+账本）→`republished_old_fact/reject`
+ 端到端 `test_end_to_end`（临时目录跑主函数，校验 `events_temporal.json` 生成）。

另有 `tests/test_v6_retro_future.py`（V6 整改单测）：文章层 `detect_retrospective` 强/弱信号
（回顾/复盘/时间线/本文由AI生成→strong；盘点/对比/大事记/文内回顾→weak；正常新闻→none）以及
未来日期兜底 `decide_final_pool`（未来日期高分文章→reject、回顾文→reject/reference）。

---

## 4. 新颖性/去重（event_novelty_check.py）

### 4.1 输入优先顺序

主函数 events_file 候选：`events_temporal.json`（优先，含 `fact_freshness_status`）→
`events_raw.json` → `events_analyzed.json` → `events_scored.json`（均存在才用）。
（SKILL.md 写 "读取 events_scored.json" 为旧描述，实际优先 temporal。）账本
`data/event_ledger/events_seen.jsonl`，输出 `events_scored_novelty.json`。

### 4.2 cluster_key / fact_hash

与 temporal 复用同一定义：`normalize_for_cluster`（去日期/数字/标点/空格/小写），
`generate_cluster_key`=`"{event_type}:{title_prefix}:{entity_str}"`；`generate_fact_hash`＝
`md5(normalize(fact))[:12]`。

### 4.3 新颖性判定 `determine_novelty`

读 `ledger = load_ledger()`，分四类：

- **首次出现**（`existing is None`）→ `new_today`，`report_eligibility=core`，
  `report_count=1`，`first/last_reported_at=date`。
- **同日重跑保护**：`first_seen == date`（当天 cron 重试/补跑写入）→
  事实 hash 变→`updated_today/core`；不变→`new_today/core`。**避免重跑把当天真实新闻误判为
  ongoing/tracking 而被踢出正文**（代码注释明确此设计原因）。
- **账本已存在**：结合时效结果（`fact_freshness_status`/`temporal_eligibility`）：
  - fact hash 变（有实质更新）→ `updated_today/core`，`report_count+1`
  - `freshness=="republished_old_fact"` 或 `temporal_eligibility=="reject"` → `repeated/reference`（不进正文）
  - 近 3 天已报告且事实未变 → `repeated/reference`
  - 近 3 天见过但未报告 → `ongoing/tracking`
  - 3-7 天 → `ongoing/tracking`
  - 7 天以上 → `background/archive`
- 事实级字段：`fact_seen_count`（同 fact hash 累加）、`fact_has_new_delta/new_delta_fields`
  （fact 内容变化标记 `fact_content`）。

`novelty_status ∈ {new_today, updated_today, ongoing, repeated, background}`；
`report_eligibility ∈ {core, tracking, reference, archive}`。

### 4.4 事件账本更新

对每条事件（无论是否新）都 `save_ledger_entry` 追加 JSONL；`ledger` 内存 dict 同步更新
（已存在则更新 `last_seen_at/fact_hash/last_reported_at/report_count/fact_*`，不更新 `first_seen_at`；
不存在则插入新 entry）。entry 字段：
`cluster_key/event_id/event_title/event_type/fact_hash/first_seen_at/last_seen_at/
last_reported_at/report_count/fact_first_seen_at/fact_seen_count/fact_has_new_delta/
new_delta_fields/fact_freshness_status/date/priority/source_url`。

输出 `events_scored_novelty.json` metadata version `1.0`：`novelty_stats`（含 `total`）+ `events`。

---

## 5. 评分与优先级（score_events.py）

### 5.1 配置来源

`config/scoring.yaml`（`score_events.py` 读取；内置 `DEFAULT_*` 兜底）：
- `weights`（8 维合计 1.0，异常时归一化）：
  `strategic_importance .18 / watsons_relevance .24 / impact_scope .12 /
  source_credibility .12 / data_richness .10 / actionability .14 /
  time_sensitivity .06 / novelty .04`
- `thresholds`：`P0:4.3 / P1:3.6 / P2:2.8 / ARCHIVE:0.0`（程序强制 `ARCHIVE=0.0`）。
- `source_tier_map`（source_name→可信度）：Tier5 官方/监管（meituan_official、jd_daojia_official、
  eleoda_official、watsons_official、nmpa、sec_filing、company_earnings、company_announcement）；
  Tier4 头部媒体（latepost/36kr/caixin/wallstreetcn/jiemian/huxiu）；Tier3 行业垂类
  （cbndata/cosmetic_observation/beauty_evolution/retail_observation/instant_retail_watch）；
  Tier2 聚合自媒体（sohu/sina/toutiao/baijiahao/wechat_article）；Tier1 `_unknown`。
- `event_type_importance`（基线分）：`platform_rule 5 / platform_move 4 / competitor_move 4 /
  data_signal 4 / policy_change 5 / category_trend 3 / … / background_only 1 / unclear 1`。
- `actionable_variables`、`data_keywords`（含 GMV/增长率/订单量/门店数/渗透率/%/营收/利润…）。

### 5.2 评分入口与顺序

主函数 events_file 候选：默认 `events_raw.json`，若存在 `events_scored_novelty.json`（优先）或
`events_temporal.json` 则用之——**因此 score 能读到 novelty_status + fact_freshness_status +
temporal_eligibility 参与硬降级**。逐条八维打分（各返回 score 0-5 + reasons）：
`score_strategic_importance / score_watsons_relevance / score_impact_scope /
score_source_credibility / score_data_richness / score_actionability /
score_time_sensitivity / score_novelty`。`compute_weighted_score` 加权；`classify_priority`
按阈值预分级；`apply_hard_downgrade` 硬降级；排序 `(priority 次序, -weighted_score, -sc,
-dr, -ts)`。

屈臣氏相关关键词组：`WATSONS_DIRECT_KEYWORDS`（屈臣氏/Watsons）、`WATSONS_CHANNEL_KEYWORDS`
（美团闪购/京东到家/秒送/淘宝闪购/抖音小时达/饿了么/即时零售）、`WATSONS_CATEGORY_KEYWORDS`、
`WATSONS_COMPETITOR_KEYWORDS`（丝芙兰/万宁/调色师/话梅/妍丽/WOW COLOUR/名创优品/KK集团）。
`score_watsons_relevance`：直接提屈臣氏=5；核心即时零售渠道=4；竞对/品类即时零售=3；泛零售=2；
仅个护美妆=1；无关=0。

### 5.3 硬降级规则（apply_hard_downgrade）

`PRIORITY_ORDER={P0:0,P1:1,P2:2,ARCHIVE:3}`，只降不升（除非规则内显式放行）。

新增拦截函数（规则 0 / 0b / 0c / 0d / 0e，`score_events.py`）：
- **规则0 / 0b（fc340b1 一类）**：
  - `_is_stock_market_event` 纯资本市场/证券行情（涨停/跌停/涨超/个股/股价/市值蒸发/收盘价/
    二级市场/主力资金…；强信号：目标价/评级/估值/EPS/市盈率/研报/归母净利润等——命中≥1 即拦截。
    `"研报"`+证券/券商/投行/评级…组合、`证券/券商`+指出/点评/看好…组合各 +2。经营动作词
    `即时零售/闪购/门店/上新/促销/GMV…` 只在非强信号路径用于「`stock_hits> biz_hits` 才判纯股市」）→ ARCHIVE。
  - `_is_corporate_finance_event` 竞对/第三方财报·IPO·招股·募资（上市/IPO/招股书/挂牌/港交所/敲钟/
    募资/融资/A/B/C轮/市值/…数据丰富但**对屈臣氏电商负责人无可执行性**；`FINAL`≥1 且 ≥ ACTION 命中
    → 判纯财经）→ ARCHIVE。
- **规则0d** `_is_non_mainland_subject_event` 港·台·海外（香港/港交所/港元/港币/HK$/HKD/联交所/恒生/
  台湾/台北/新台币/台股/澳门/新加坡/马来西亚/日本/韩国/美国市场；`non_hits≥2 且 > mainland_hits`
  判为非大陆主体，阈值 2 避免大陆文章顺带一句"香港"被误杀）→ ARCHIVE。
- **规则0c** `watsons_relevance ≤ 1`（几乎无关）→ ARCHIVE。
- **规则0e** 时效准入：`fact_freshness_status ∈ {republished_old_fact, date_conflict}` 或
  `temporal_eligibility == reject` → ARCHIVE；
  `{historical_background, retrospective_summary}` 或 `temporal_eligibility == reference` →
  除非是**行业大盘/规模类参考数据例外**（文本含 大盘/行业规模/市场规模/GMV/增速/渗透率/总额/超万亿…
  且含 美妆/即时零售/个护/化妆品/零售，且 `data_richness≥4`）→ 最高 P2；否则 ARCHIVE。
  其余 `new_fact/updated_fact/upcoming_fact` → 正常。

原始 9 条规则：
- 规则1 `confidence=low`→最高 P2（例外：`weighted_score≥3.5 且 source_cred≥3` 可留 P1）
- 规则2 `extraction_method=rule_fallback`→最高 P2
- 规则3 `source_credibility<2`→最高 P2
- 规则4 `watsons_relevance<3`→最高 P2
- 规则5 `event_type=background_only`→最高 P2
- 规则6 `event_type=unclear`→最高 P2
- 规则7 `source_url` 缺失→ARCHIVE
- 规则8 `fact` 为空→ARCHIVE
- 规则9 `evidence_text` 为空→最高 P2

（`scoring.yaml` 的 `hard_downgrade`/`push_limits: daily_P0_max:5,daily_P1_max:20` 同样记录这些语义。）

评分后事件新增：`scores`（八维）、`weighted_score`、`priority`、`score_reasons`、`downgrade_reasons`。

---

## 6. 经营影响分析（analyze_business_impact.py）

### 6.1 输入与业务分析结构

读取 `events_scored.json`，对每条生成 `business_analysis`，输出 `events_analyzed.json`。
`business_analysis` 结构（LLM 提示词 `LLM_SYSTEM_PROMPT` 定义的严格 JSON）：

```json
{
  "impact_type": "opportunity|risk|watch|noise",
  "affected_channels": [],
  "affected_business_variables": [],
  "watsons_impact": "",
  "recommended_action": "",
  "action_level": "immediate|test|watch|archive",
  "owner_hint": "",
  "tracking_metrics": [],
  "follow_up_questions": [],
  "confidence": "high|medium|low",
  "downgrade_reasons": []
}
```

- 合法枚举：`VALID_IMPACT_TYPES`、`VALID_ACTION_LEVELS`、`VALID_OWNER_HINTS`
  （即时零售运营/天猫运营/京东运营/商品货盘团队/价格活动团队/数据分析/平台BD/会员私域/管理层关注/暂不分派）、`VALID_ANALYSIS_CONFIDENCE`。
- 渠道映射 `CHANNEL_KEYWORDS`（美团闪购/京东秒送/京东到家/淘宝闪购/抖音小时达/饿了么/大众点评/
  抖音本地生活/本地生活/天猫旗舰店/京东旗舰店/天猫超市/京东自营/经销商分销/便利店/即时零售(泛)/B2C(泛)）；
  泛渠道兜底 `INSTANT_RETAIL_DEFAULT_CHANNELS=["美团闪购","京东到家"]`。
- 经营变量映射 `BUSINESS_VARIABLE_MAP`（GMV/订单量/客单价/转化率/复购率/流量/履约时效/缺货率/
  门店覆盖/SKU/搜索排名/平台券/活动坑位/会员复购/价格竞争力/竞争格局…）。
- `EVENT_TYPE_IMPACT`：platform_rule→risk、platform_move→opportunity、competitor_move→risk、
  data_signal→opportunity、category_trend→watch、policy_change→risk、consumer_trend→watch、
  new_product→opportunity、supply_chain→risk、marketing→opportunity、background_only/unclear→noise。
- `EVENT_TYPE_OWNER`：platform_rule→平台BD、platform_move/competitor_move→即时零售运营、
  data_signal→数据分析、category_trend/…→商品/货盘团队…

### 6.2 规则分析（rule_analyze_event）

`rule_infer_affected_channels/variables/impact_type/owner_hint/tracking_metrics/
follow_up_questions/watsons_impact/recommended_action/action_level/analysis_confidence`
各推断器组装 `business_analysis`。`watsons_impact` 用 `wr≥4→"直接影响屈臣氏"、≥3→"间接影响"、
否则→"对屈臣氏影响较弱"` + 渠道 + 类型描述。`recommended_action` 按 event_type 模板填充渠道
（ARCHIVE→"归档备查，无需立即行动"），并按业务变量追加补贴 ROI/SKU/客单价对比等。

### 6.3 LLM 分析与校验/降级

`llm_analyze_event`：`LLM_SYSTEM_PROMPT + LLM_USER_TEMPLATE`（注入 event 全字段 + 八维 scores），
`temperature=0.15, max_tokens=2048, response_format="json"`；解析失败用 `robust_json_extract`
（含 `reasoning_content` 提取）；仍无则返回 `None`（触发规则兜底）。
`_validate_analysis` 规范化/回填（缺失字段用规则推断补齐）。
`apply_analysis_downgrade(ba,event)` 7 条硬规则：
1. `priority=ARCHIVE → action_level=archive`
2. `confidence=low`→不得 immediate（降为 test）
3. `extraction_method=rule_fallback`→不得 immediate
4. `event_type=unclear`→不得 immediate
5. `source_credibility<2`→不得 immediate
6. `recommended_action` 空→watch
7. `tracking_metrics` 空/`["待定"]`→补 follow_up，且 immediate/test→watch
另含**空话检测**：`加强关注/持续优化/密切关注/保持关注…` 且 <20 字→watch。

### 6.4 批次分流与并发（orig_idx 索引关键，§7.1 事故）

读取 `parallel.yaml` 的 `analyze_business_impact.event_parallel`：
`ARCHIVE 跳过(use_llm:false, workers:0) / P2 Lite 高并发(6, 60s) / P1 Chat 中并发(4,120s) /
P0 Chat 低并发(2,150s) / fallback_to_rule:true / checkpoint_file: analyze_business_checkpoint.jsonl`。
并且模型路由 `model_router.yaml` 按优先级选模型（P0→Thinking、P1→Chat、P2→Lite，见 MEMORY.md）。

事件分流：
```python
archive_indices=[(i,e)]  # ARCHIVE 或 LLM 不可用
p2_indices=[(i,e)]  p1_indices=[(i,e)]  p0_indices=[(i,e)]   # (original_index, event)
other_indices=[(i,e)]  # 未知优先级，用规则
```
ARCHIVE/other 串行 `rule_analyze_event`；P0/P1/P2 走 `batch_parallel_map`（`items_by_batch`，
每批独立 workers/timeout）。`_analyze_one_event(event, orig_idx, batch)`：调 `llm_analyze_event`，
成功 `save_checkpoint`；返回 None/异常 → `fallback_to_rule` 时 `rule_analyze_event`（checkpoint 标
`fallback_rule`/`fallback_rule_exception`），否则 `failed`。
回读 `_batch_results` 时**必须按 `orig_idx` 下标取**（注释原文）：
```python
_r = _batch_results[orig_idx] if orig_idx < len(_batch_results) else None
```
`orig_idx` 是**完整事件列表**的索引（可能 > total_llm_items），绝不能顺序自增。
`_batch_results` 为 `None`（超时/异常）则 `llm_failed_count+=1` 并用规则兜底分析。
最终组装 `analyzed_events`：遍历 `raw_events`，`i in llm_results` 取之，否则补 `rule_analyze_event`，
统一 `apply_analysis_downgrade`，写入 `analyzed_event["business_analysis"]=ba`。

### 6.5 输出

`events_analyzed.json` metadata：`llm_success_count/llm_failed_count`，
`by_priority/by_impact_type/by_action_level/by_owner_hint`，
`immediate_count/test_count/watch_count/archive_count`，`downgrade_stats`，
`top_action_events`（`action_level∈{immediate,test}` 的事件精简列表）。

---

## 7. 已知问题与已修复事故

### 7.1 588b95f — business_analysis 分析错位（orig_idx 索引根因，"智障"问题）

**根因**：`batch_parallel_map` 的 `results` 列表**按 orig_idx 下标定位**
（`results[orig_idx] = result`，见 `skills/utils/parallel_runner.py`），而 orig_idx 是
「完整事件列表中的索引」，只有部分事件走 LLM 时可能远大于 `total_items`。若调用方
（旧版 `analyze_business_impact.py`）用**顺序自增的下标**遍历 `_batch_results` 去取结果，
就会导致 `business_analysis` 与事件**错位/张冠李戴**——A 事件的 `watsons_impact`/`recommended_action`
被挂到 B 事件上，报告里出现完全对不上的分析（即用户反馈的"智障"输出）。

**已修复**（当前代码已固化为两处防御）：
1. `parallel_runner.batch_parallel_map` 按 `max(orig_idx)+1` 分配 `results` 长度
   （`_alloc = max(total_items, _max_idx+1)`），并注释说明这是为避免 IndexError/静默丢结果；
2. `analyze_business_impact.py`（1057-1095 行）回读时显式 `_batch_results[orig_idx]`，
   并有两段醒目注释提醒"**必须用 orig_idx 直接索引，绝不能用顺序自增的 _result_idx，
   否则 business_analysis 会与事件错位**"。

审计结论：该事故的代码级教训已写入 `batch_parallel_map` 与 `analyze_business_impact` 的注释，
是"**批处理结果按原始索引定位、不得依赖展开顺序**"的典型反例。任何新增的 batch 并行调用
（含 `extract_events` 的 `parallel_map` 组装循环）都应采用同样的 orig_idx 回填方式。

### 7.2 fc340b1 — 竞对 IPO / 港台海外 / 低相关拦截遗漏

**问题**：早期 `score_events.py` 只有 9 条硬降级，缺少对三类"数据丰富但无经营价值"内容的拦截：
- 竞对/第三方公司**财报·IPO·招股·募资**（如"龙丰集团香港 IPO 财报"）——数据丰富（data_richness 高）、
  看似战略重要，但对屈臣氏电商负责人**无可执行性**，会挤占日报正文；
- **非中国大陆主体/市场**（港股、台湾、海外公司营收/门店）；
- **与屈臣氏几乎无关**（`watsons_relevance≤1`）但分数较高的内容。

**已修复**（`score_events.py` 的硬降级新增规则）：
- `_is_stock_market_event`（规则0）：纯资本市场/证券行情 → ARCHIVE（含券商研报/估值强信号判定）
- `_is_corporate_finance_event`（规则0b）：竞对/第三方财报·IPO·招股·募资 → ARCHIVE
- `_is_non_mainland_subject_event`（规则0d）：港·台·海外主体/市场 → ARCHIVE（阈值 2 防误杀）
- `_is_corporate_finance_event` / `_is_stock_market_event` 含**经营动作词抵消**逻辑，
  避免把"国补首次覆盖 Apple 新品"这类真经营新闻误判（`首次覆盖`不在券商强信号列表）
- `watsons_relevance <= 1 → ARCHIVE`（规则0c，低相关硬拦截）
- `fact_freshness_status` 旧闻/回顾文 → ARCHIVE 或降 P2（规则0e，行业大盘数据例外）

这些与 `config/scoring.yaml` 的 `hard_downgrade` 语义一致，属于同一次整改（fc340b1）的产物。

### 7.3 ARCHIVE 低相关 / 其他已知注意点

- **ARCHIVE 低相关去向**：`score_events` 把低分/拦截事件标 `ARCHIVE`，`analyze_business_impact`
  对 ARCHIVE **跳过 LLM、纯规则分析**（`batch_workers["ARCHIVE"]=0`）并 `action_level=archive`，
  日报生成阶段排除 ARCHIVE（参考 `run_daily_pipeline` 与报告技能）。用户口径"ARCHIVE 低相关"
  由 `watsons_relevance<=1→ARCHIVE` 与分数阈值共同保证。
- **SKILL.md 与代码不一致**：`event_novelty_check/SKILL.md` 写"读取 events_scored.json"，
  但代码优先读 `events_temporal.json`；`score_events` 优先读 `events_scored_novelty.json`。
  文档审计时以代码为准。
- **事件类型枚举不一致**：`EVENT_TYPES`（抽取层 11 种）与评分/分析层的
  `policy_change/consumer_trend/new_product/supply_chain/marketing` 不重合，未知类型会回退到
  `unclear`/`noise`/`1` 分——抽取层不会产出这些类型（仅规则层可能映射出 `background_only`）。
- **时效层复用与双层账本**：`temporal_fact_validation` 与 `event_novelty_check` 各自实现
  `load_ledger/generate_cluster_key/generate_fact_hash` 且**逻辑重复但一致**；`events_seen.jsonl`
  是两者共同读取/写入的账本。改动需同步两处以避免漂移。

---

## 8. 相关配置与常量的快速索引

| 常量/配置 | 值 | 位置 |
|---|---|---|
| event_id | `E{YYYYMMDD}_{index:04d}` | `extract_events.py::generate_event_id` |
| LLM 时间字段规则 | occurred/announced/effective + precision/confidence/evidence | `LLM_USER_PROMPT_TEMPLATE` |
| rule_fallback 触发关键词 | 屈臣氏/美团闪购/京东秒送/京东到家/淘宝闪购/抖音小时达/GMV/增长/突破… | `RULE_FALLBACK_TITLE_KEYWORDS` |
| FUTURE_TOLERANCE_HOURS | 6 | `temporal_fact_validation.py` |
| historical_date_ratio 阈值 | 0.6（回顾文） | `classify_fact_freshness` |
| 近 7 天窗口（new） | `occurred_dt >= now-7d` | `classify_fact_freshness` |
| FRESHNESS_ELIGIBILITY | 见 §3.6 | `temporal_fact_validation.py` |
| novelty 同日重跑保护 | `first_seen==date` → new/updated_today | `determine_novelty` |
| 账本路径 | `data/event_ledger/events_seen.jsonl` | 两技能共用 |
| 权重 | 0.18/0.24/0.12/0.12/0.10/0.14/0.06/0.04 | `config/scoring.yaml` |
| 阈值 | P0 4.3 / P1 3.6 / P2 2.8 / ARCHIVE 0.0 | `config/scoring.yaml` |
| watsons_relevance 拦截 | ≤1 → ARCHIVE；<3 → 最高 P2 | `score_events.py` |
| 竞对IPO/港台/股市拦截 | 见 §5.3 | `_is_corporate_finance_event` `_is_non_mainland_subject_event` `_is_stock_market_event` |
| business_analysis | 见 §6.1 | `analyze_business_impact.py` `LLM_SYSTEM_PROMPT` |
| analyze 批次并发 | ARCHIVE 0 / P2 6 / P1 4 / P0 2 | `config/parallel.yaml` |
| batch_parallel_map 索引 | `results[orig_idx]`，分配 `max(total, max_idx+1)` | `parallel_runner.py` |

---

---
