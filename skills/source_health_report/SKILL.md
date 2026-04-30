# source_health_report — 信息源健康度评估

## 功能

在 collect + filter 完成后运行，统计每个信息源的健康指标，按 A/B/C/D 四级分类，
给出 keep/limit/reference/disable 建议。

## 定位

诊断工具，位于 pipeline 的 collect → filter 之后、extract 之前（或之后均可）。
**暂未集成到 run_daily_pipeline 主流程。**

## 用法

```bash
python skills/source_health_report/source_health_report.py \
    --project-root . --date 2026-04-26
```

## 输入

| 文件 | 路径 | 必需 |
|------|------|------|
| 原始文章 | `data/raw/YYYY-MM-DD/raw_articles.json` | ✅ |
| 清洗文章 | `data/cleaned/YYYY-MM-DD/cleaned_articles.json` | ✅ |
| 参考文章 | `data/cleaned/YYYY-MM-DD/reference_articles.json` | ✅ |
| 拒绝文章 | `data/rejected/YYYY-MM-DD/rejected_articles.json` | ❌ |

## 输出

| 文件 | 路径 |
|------|------|
| JSON 报告 | `data/logs/YYYY-MM-DD/source_health_report.json` |
| Markdown 报告 | `data/logs/YYYY-MM-DD/source_health_report.md` |

## 分级规则

| 级别 | 条件 | 建议 |
|------|------|------|
| 🟢 A | recent_ratio ≥ 0.3 且 cleaned_yield_rate ≥ 0.1 | keep_primary |
| 🟡 B | recent_ratio ≥ 0.3 且 cleaned_yield_rate < 0.1 | keep_secondary_limit |
| 🟠 C | old_ratio ≥ 0.8 但 matched_keyword_count > 0 | reference_only |
| 🔴 D | old_ratio ≥ 0.8 且 matched_keyword_count = 0 | disable_candidate |

## 统计字段（per source）

- `total_count` — 总采集数
- `in_window_count` — 时间窗口内
- `near_window_count` — 近窗口
- `old_count` — 旧文章
- `unknown_time_count` — 无时间
- `matched_keyword_count` — 有匹配关键词数
- `cleaned_count` — 进入清洗集
- `reference_count` — 进入参考集
- `rejected_count` — 被拒绝数
- `old_ratio` — old_count / total_count
- `recent_ratio` — (in_window + near_window) / total_count
- `cleaned_yield_rate` — cleaned_count / total_count
- `top_titles` — 最相关前5篇标题
- `grade` — A/B/C/D
- `recommendation` — keep_primary/keep_secondary_limit/reference_only/disable_candidate

## Markdown 报告章节

1. A类源
2. B类源
3. C类源
4. D类源
5. old_ratio 最高的前10个源
6. cleaned_yield 最高的前10个源
7. 建议降级为 reference_only 的源
8. 建议禁用的源
9. 附录：全源明细表