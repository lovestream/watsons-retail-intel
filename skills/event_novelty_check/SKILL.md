# event_novelty_check

检查事件新颖性，防止日报每天重复写同一事件。

## 用法

```bash
python3 -m skills.event_novelty_check.event_novelty_check \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-05-03
```

## 输入

- `data/events/YYYY-MM-DD/events_scored.json` — 当日评分后的事件
- `data/event_ledger/events_seen.jsonl` — 历史事件账本 (自动创建)

## 输出

- `data/events/YYYY-MM-DD/events_scored_novelty.json` — 带新颖性字段的事件
- `data/event_ledger/events_seen.jsonl` — 更新后的事件账本

## 新增字段

每条事件新增:

| 字段 | 含义 |
|------|------|
| `cluster_key` | 事件聚类键 (基于标题+实体) |
| `novelty_status` | new_today / updated_today / ongoing / repeated / background |
| `first_seen_at` | 首次出现日期 |
| `last_seen_at` | 最后出现日期 |
| `last_reported_at` | 最后写入报告日期 |
| `report_count` | 累计报告次数 |
| `report_eligibility` | core / tracking / reference / archive |

## 新颖性规则

1. cluster_key 从未出现 → `new_today`
2. cluster_key 出现过但 fact_hash 不同 → `updated_today`
3. cluster_key 近3天已 reported 且 fact_hash 相同 → `repeated`
4. cluster_key 24-72小时内但未写过 → `ongoing`
5. cluster_key 7天以上 → `background`

## report_eligibility

| novelty_status | report_eligibility |
|----------------|-------------------|
| new_today | core |
| updated_today | core |
| ongoing | tracking |
| repeated | reference |
| background | archive |