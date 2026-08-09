# temporal_fact_validation — 时效判断机制

## 用途

解决"今天发布、但正文重新汇总去年旧闻"的 AI 转载/汇总文章识别问题。
核心是把 **文章发布时间** 与 **事实发生时间** 分离判断。

## Pipeline 位置

```
extract → temporal_fact_validation → event_novelty_check → score → analyze
```

在 extract 之后立即执行，避免旧闻进入昂贵的经营分析（analyze）步骤。

## 调用方式

```bash
python3 -m skills.temporal_fact_validation.temporal_fact_validation \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-08-09
```

或通过 run_daily_pipeline 的 `temporal_fact_validation` 步骤调用。

## 输出

- `data/events/{date}/events_temporal.json` — 事件（含全部时间字段）
- `data/logs/{date}/temporal_fact_validation.log`
- `data/logs/{date}/temporal_stats.json` — 分类计数 + Top20 republished_old_fact

## 字段说明

### 文章层（保留原有）
- `article_published_at` / `article_first_seen_at` / `article_time_status`
- `article_time_status` 取值: valid / invalid_future_date / unknown

### 事件层（新增）
- `event_occurred_at` — 事件实际发生时间
- `event_announced_at` — 事件宣布/公布时间
- `event_effective_at` — 事件生效/执行时间
- `event_time_precision` — day/week/month/quarter/year/unknown
- `event_time_confidence` — high/medium/low
- `temporal_evidence` — 时间证据
- `content_dates` — 正文日期提取
- `historical_date_ratio` — 历史日期占比 (0~1)
- `is_retrospective` — 是否回顾文

### 事实新鲜度
- `fact_freshness_status` — 见下方分类
- `temporal_eligibility` — core / tracking / reference / reject / review
- `temporal_reasons` — 判定原因

## 分类规则

| fact_freshness_status | 准入 | 说明 |
|---|---|---|
| new_fact | core | 今天发布、窗口内发生 |
| updated_fact | core | 事实历史存在，今天有新增 delta |
| upcoming_fact | core | 今天宣布、未来执行（不误杀） |
| ongoing_fact | tracking | 持续进行中 |
| historical_background | reference | 旧背景事实 |
| retrospective_summary | reference | 复盘历史多事件 |
| republished_old_fact | reject | 历史已存在且无新增 |
| date_conflict | reject | 时间矛盾 |
| unknown_fact_time | review/reference | 高价值→review，普通→reference |

## 日期源置信度

json_ld / html_time / og_metadata = high；url_date = medium；tavily / search_snippet = low。
Tavily published_date 不再作为最终权威时间。

## 页面类型

homepage / listing_page / tag_page / category_page / search_page / topic_page
不得直接成为新闻事件，仅用于发现 article URL。
