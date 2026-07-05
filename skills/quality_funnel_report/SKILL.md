# quality_funnel_report — 采集质量漏斗诊断

## 功能
诊断 Pipeline V3 为什么采集量大但高质量信息少。统计从 raw → cleaned/reference/rejected 的漏斗转化，按多个维度拆解，找出瓶颈和可疑遗漏。

## 定位
Pipeline V3 的 filter 后诊断步骤，帮助调优采集源和过滤规则。

## 在 Pipeline 中的位置
建议放在 `filter` 步骤之后（或 `filter_merged` 之后），与 `source_health_report` 并列。

## 输入
| 文件 | 必需 | 说明 |
|------|------|------|
| `data/raw/{date}/raw_articles_all.json` | ✅ | 合并后的原始文章 |
| `data/cleaned/{date}/cleaned_articles.json` | ✅ | cleaned池 |
| `data/cleaned/{date}/reference_articles.json` | ✅ | reference池 |
| `data/rejected/{date}/rejected_articles.json` | ✅ | rejected池 |

## 输出
| 文件 | 说明 |
|------|------|
| `data/logs/{date}/quality_funnel_report.json` | 完整报告JSON |
| `data/logs/{date}/quality_funnel_report.md` | 人类可读MD报告 |

## 19 项统计维度

### 简单维度 (1-10)
| # | 维度 | 说明 |
|---|------|------|
| 1 | by_collector | 按采集器（rsshub/xcrawl/web_monitor/broad_search/...） |
| 2 | by_source_name | 按源名（xcrawl_01, rsshub_36kr_newsflashes, ...） |
| 3 | by_source_type | 按源类型（rsshub/xcrawl/tavily/web_monitor） |
| 4 | by_freshness_status | 按时效标记（rsshub_collect/newly_discovered/day_primary/...） |
| 5 | by_time_status | 按时间状态（in_window/near_window/old/unknown_time） |
| 6 | by_page_type | 按页面类型（news/article/report/unknown/...） |
| 7 | by_region_tag | 按地区标签（mainland/hk/tw/overseas/unknown） |
| 8 | by_content_status | 按内容丰富度（rich/moderate/thin/summary_only/empty） |
| 9 | by_rule_score_bucket | 按规则评分段（≥10/8-9/6-7/3-5/<3/未评分） |
| 10 | by_final_pool | 按最终池（cleaned/reference/rejected/unprocessed） |

### 交叉维度 (11-13)
| # | 维度 | 说明 |
|---|------|------|
| 11 | collector × final_pool | 采集器到最终池的转化 |
| 12 | collector × page_type | 采集器的页面类型分布 |
| 13 | collector × freshness_status | 采集器的时效标记分布 |

### 源名分析 (14-15)
| # | 维度 | 说明 |
|---|------|------|
| 14 | source_name × cleaned_count | 各源进了多少cleaned |
| 15 | source_name × reject_count | 各源进了多少rejected |

### 排行 (16-17)
| # | 维度 | 说明 |
|---|------|------|
| 16 | top 50 reference by rule_score | reference池里最高的50篇 |
| 17 | top 50 rejected by rule_score | rejected池里最高的50篇 |

### 明细 (18-19)
| # | 维度 | 说明 |
|---|------|------|
| 18 | cleaned 全量清单 | 所有cleaned文章详情 |
| 19 | suspected_missed_candidates | 高分但未进cleaned的可疑遗漏 |

## suspected_missed_candidates 条件
- `rule_score >= 6` 但不在 cleaned 池
- `freshness_status` 属于有效时效（newly_discovered/day_primary/tavily_day/search_day/published_in_window/broad_discovery/rsshub_collect/in_window）
- 或 `time_status` 属于 in_window/near_window
- `page_type` 属于 news/article/report/official_notice/unknown

## 自动诊断
报告自动生成诊断建议：
- cleaned率极低(<5%)或偏低(<15%)
- old文章占比过大
- unknown_time文章过多
- 噪音page_type占比过大
- 正文缺失比例过高
- suspected_missed_candidates分析
- 零cleaned的collector警告

## 用法
```bash
python skills/quality_funnel_report/quality_funnel_report.py \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-05-02 \
    --verbose
```

## Python调用
```python
from skills.quality_funnel_report.quality_funnel_report import quality_funnel_report
result = quality_funnel_report(
    project_root="/app/working/projects/watsons-retail-intel",
    date="2026-05-02",
    verbose=True,
)
```