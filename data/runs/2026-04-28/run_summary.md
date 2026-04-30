# Daily Pipeline Run Summary｜2026-04-28

## 运行结论

- **状态**: ✅ 成功
- **可发送**: ⚠️ 否/未审稿
- **播客**: ⚠️ 失败/部分成功
- **邮件**: ❌ 失败
- **日报类型**: normal
- **开始时间**: 2026-04-28T23:16:53.026747+08:00
- **结束时间**: 2026-04-28T23:16:53.127885+08:00
- **总耗时**: 0.1s

## 步骤状态

| 步骤 | 状态 | 耗时 | 核心统计 | 警告 | 错误 |
|------|------|------|----------|------|------|
| collect | 🔍 dry_run | 0.0s | 54 | 0 | 0 |
| filter | 🔍 dry_run | 0.0s | 34 | 0 | 0 |
| source_health_report | 🔍 dry_run | 0.0s | 5 | 0 | 0 |
| tavily_gap_search | 🔍 dry_run | 0.0s |  | 1 | 0 |
| filter_merged | 🔍 dry_run | 0.0s | 34 | 0 | 0 |
| extract | 🔍 dry_run | 0.0s | 100 | 0 | 0 |
| score | 🔍 dry_run | 0.0s | 102 | 1 | 0 |
| analyze | 🔍 dry_run | 0.0s | 102 | 0 | 0 |
| generate_report | 🔍 dry_run | 0.0s |  | 0 | 3 |
| editor_review | 🔍 dry_run | 0.0s | False | 0 | 0 |
| generate_podcast | 🔍 dry_run | 0.0s | 206 | 1 | 0 |
| send_daily_report_email | 🔍 dry_run | 0.0s | False | 0 | 0 |

## 核心统计

- **采集文章数**: 54
- **清洗文章数**: 34
- **参考文章数**: 15
- **抽取事件数**: 100
- **评分事件数**: 102
- **分析事件数**: 102
- **日报事件ID数**: 0
- **P0事件**: 0
- **P1事件**: 0
- **P2事件**: 59
- **口播稿字数**: 206
- **播客音频**: ✅ 有
- **邮件发送**: 🔍 Dry Run

## Warning

- ⚠️ [tavily_gap_search] Tavily gap search 未触发（无需补搜或条件不满足）
- ⚠️ [score] 评分事件数 102 ≠ 原始事件数 100
- ⚠️ [generate_podcast] 口播稿偏短（206 < 1800 中文字符）

## Error

- ❌ [generate_report] 日报文件不存在: /app/working/projects/watsons-retail-intel/data/drafts/2026-04-28/daily_report_draft_v1.md
- ❌ [generate_report] 日报文件不存在: /app/working/projects/watsons-retail-intel/data/drafts/2026-04-28/daily_report_draft_v2.md
- ❌ [generate_report] 日报文件不存在: /app/working/projects/watsons-retail-intel/data/drafts/2026-04-28/daily_report_draft.md

## 是否可发送

**⚠️ 不可发送/未审稿**
