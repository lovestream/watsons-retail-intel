# Daily Pipeline Run Summary｜2026-04-29

## 运行结论

- **状态**: ✅ 成功
- **可发送**: ✅ 是
- **播客**: ⚠️ 失败/部分成功
- **邮件**: ⏭️ 跳过（send_email=False）
- **日报类型**: normal
- **开始时间**: 2026-05-01T07:09:31.996328+08:00
- **结束时间**: 2026-05-01T07:09:32.419835+08:00
- **总耗时**: 0.4s

## 步骤状态

| 步骤 | 状态 | 耗时 | 核心统计 | 警告 | 错误 |
|------|------|------|----------|------|------|
| collect | ⏭️ skipped | 0.0s | 557 | 0 | 0 |
| filter | ⏭️ skipped | 0.0s | 184 | 0 | 0 |
| source_health_report | ⏭️ skipped | 0.0s |  | 1 | 0 |
| tavily_gap_search | ⏭️ skipped | 0.0s |  | 1 | 0 |
| filter_merged | ⏭️ skipped | 0.0s | 184 | 0 | 0 |
| extract | ⏭️ skipped | 0.0s | 446 | 0 | 0 |
| score | ⏭️ skipped | 0.0s | 446 | 0 | 0 |
| analyze | ⏭️ skipped | 0.0s | 446 | 0 | 0 |
| generate_report | ⏭️ skipped | 0.0s | 29 | 0 | 0 |
| editor_review | ⏭️ skipped | 0.0s | True | 0 | 0 |
| generate_podcast | ⏭️ skipped | 0.0s | 1216 | 1 | 0 |
| send_daily_report_email | ⏭️ skipped | 0.0s |  | 1 | 0 |

## 核心统计

- **采集文章数**: 557
- **清洗文章数**: 184
- **参考文章数**: 211
- **抽取事件数**: 446
- **评分事件数**: 446
- **分析事件数**: 446
- **日报事件ID数**: 21
- **P0事件**: 0
- **P1事件**: 2
- **P2事件**: 168
- **口播稿字数**: 1216
- **播客音频**: ✅ 有
- **邮件发送**: ⏭️ 未启用

## Warning

- ⚠️ [source_health_report] 源健康报告 JSON 不存在（非阻塞）
- ⚠️ [tavily_gap_search] Tavily gap search 未触发（无需补搜或条件不满足）
- ⚠️ [generate_podcast] 口播稿偏短（1216 < 1800 中文字符）
- ⚠️ [send_daily_report_email] 邮件发送结果 JSON 不存在

## Error

无

## 是否可发送

**✅ 可发送**
