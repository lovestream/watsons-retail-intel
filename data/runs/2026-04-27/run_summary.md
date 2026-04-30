# Daily Pipeline Run Summary｜2026-04-27

## 运行结论

- **状态**: ✅ 成功
- **可发送**: ⚠️ 否/未审稿
- **日报类型**: normal
- **开始时间**: 2026-04-28T20:04:29.664570+08:00
- **结束时间**: 2026-04-28T20:04:29.731202+08:00
- **总耗时**: 0.1s

## 步骤状态

| 步骤 | 状态 | 耗时 | 核心统计 | 警告 | 错误 |
|------|------|------|----------|------|------|
| collect | 🔍 dry_run | 0.0s | 53 | 0 | 0 |
| filter | 🔍 dry_run | 0.0s | 33 | 0 | 0 |
| source_health_report | 🔍 dry_run | 0.0s | 5 | 0 | 0 |
| tavily_gap_search | 🔍 dry_run | 0.0s |  | 1 | 0 |
| filter_merged | 🔍 dry_run | 0.0s | 33 | 0 | 0 |
| extract | 🔍 dry_run | 0.0s |  | 0 | 1 |
| score | 🔍 dry_run | 0.0s |  | 0 | 1 |
| analyze | 🔍 dry_run | 0.0s |  | 0 | 1 |
| generate_report | 🔍 dry_run | 0.0s |  | 0 | 3 |
| editor_review | 🔍 dry_run | 0.0s |  | 0 | 1 |
| generate_podcast | 🔍 dry_run | 0.0s |  | 2 | 0 |

## 核心统计

- **采集文章数**: 53
- **清洗文章数**: 33
- **参考文章数**: 12

## Warning

- ⚠️ [tavily_gap_search] Tavily gap search 未触发（无需补搜或条件不满足）
- ⚠️ [generate_podcast] 口播稿文件不存在
- ⚠️ [generate_podcast] 音频文件不存在（edge-tts 生成失败不影响日报）

## Error

- ❌ [extract] 事件文件不存在: ./data/events/2026-04-27/events_raw.json
- ❌ [score] 评分文件不存在: ./data/events/2026-04-27/events_scored.json
- ❌ [analyze] 分析文件不存在: ./data/events/2026-04-27/events_analyzed.json
- ❌ [generate_report] 日报文件不存在: ./data/drafts/2026-04-27/daily_report_draft_v1.md
- ❌ [generate_report] 日报文件不存在: ./data/drafts/2026-04-27/daily_report_draft_v2.md
- ❌ [generate_report] 日报文件不存在: ./data/drafts/2026-04-27/daily_report_draft.md
- ❌ [editor_review] 审稿报告不存在: ./data/reviews/2026-04-27/editor_review.md

## 是否可发送

**⚠️ 不可发送/未审稿**
