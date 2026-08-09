# periodic_synthesis — 周期级聚合层

将每日事件池转换为周期级经营情报（趋势、聚类、对比、分章证据），
输出 `data/periodic/{report_type}/{period_id}/periodic_synthesis.json`，
作为周报/月报/年报生成的唯一标准化输入。

## 数据流

```text
data/events/{date}/events_analyzed.json  (每日事件池)
    ↓
period_coverage_check   (周期覆盖度检查)
    ↓
period_time_filter      (按 effective_event_time 过滤周期边界)
    ↓
period_event_cluster    (cluster_key/fact_hash/实体/标题 四级聚类)
    ↓
trend_detection         (规则化趋势方向: emerging/accelerating/stable/weakening/reversing/unclear)
    ↓
previous_period_compare (与上一周期基线比较)
    ↓
channel/theme balancing (分层配额: P0/P1 全保留 + 维度最低配额)
    ↓
chapter_evidence_pack   (每章只传本章证据)
    ↓
periodic_synthesis.json / .md / coverage_report.json
```

## 输出 schema

见 `data/periodic/{type}/{period_id}/periodic_synthesis.json`：

```json
{
  "report_type": "monthly",
  "period": {"start": "...", "end": "...", "label": "2026年7月"},
  "coverage": {"expected_days": 31, "available_days": 31, "healthy_days": 30,
               "coverage_ratio": 0.968, "missing_dates": [], "degraded_dates": []},
  "event_stats": {"raw_events": 423, "reportable_events": 86, "clusters": 24},
  "trend_clusters": [],
  "previous_period_comparison": {},
  "channel_summary": {},
  "platform_summary": {},
  "competitor_summary": {},
  "category_summary": {},
  "risk_summary": {},
  "opportunity_summary": {},
  "chapter_evidence": {"01": [], "02": []}
}
```

## 关键原则

- 内部 event_id 保留（`representative_event_ids`），展示层才清除
- 事实（facts/source_quotes/source_urls）与 AI 上游分析（analyst_hints）分离
- 趋势必须有 cluster/compare 证据；无真实 GMV/市占率数据不得写"市场份额上升"
- 所有新字段必须有默认值，脏数据不崩溃
