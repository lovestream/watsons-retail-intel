# tavily_gap_search — Tavily 缺口补搜

## 功能

当 RSSHub/RSS/Web 采集后 `cleaned_count` 过低或核心平台缺失时，
调用 Tavily 搜索做精准补搜，补充文章并重新过滤。

不替代 RSSHub/RSS/Web 采集，只做补漏。

## 触发条件

满足任一即触发：

1. `cleaned_count < 5`
2. 核心平台（美团闪购/京东到家/京东秒送/淘宝闪购/抖音小时达）无有效文章
3. reference 中存在高价值线索但 cleaned 不足

## 用法

```bash
# 标准模式：补搜 + 合并 + 重过滤
python skills/tavily_gap_search/tavily_gap_search.py \
    --project-root . --date 2026-04-26

# 仅补搜，不合并和重过滤
python skills/tavily_gap_search/tavily_gap_search.py \
    --project-root . --date 2026-04-26 --skip-merge

# 自定义阈值
python skills/tavily_gap_search/tavily_gap_search.py \
    --project-root . --date 2026-04-26 --cleaned-threshold 3
```

## 输入

| 文件 | 路径 | 用途 |
|------|------|------|
| 原始文章 | `data/raw/YYYY-MM-DD/raw_articles.json` | 去重基准 |
| 清洗文章 | `data/cleaned/YYYY-MM-DD/cleaned_articles.json` | 判断触发条件 |
| 参考文章 | `data/cleaned/YYYY-MM-DD/reference_articles.json` | 判断触发条件 |
| 搜索策略 | `config/search_policy.yaml` | 预算、参数 |
| 关键词 | `config/keywords.yaml` | 关键词匹配 |

## 输出

| 文件 | 路径 |
|------|------|
| 补搜文章 | `data/raw/YYYY-MM-DD/tavily_gap_articles.json` |
| 合并文件 | `data/raw/YYYY-MM-DD/raw_articles_merged.json` |
| 日志 | `data/logs/YYYY-MM-DD/tavily_gap_search.log` |

合并后会自动运行 `filter_relevant_articles` 生成新的：
- `cleaned_articles.json`
- `reference_articles.json`
- `rejected_articles.json`

## 返回值

```json
{
  "ok": true,
  "triggered": true,
  "trigger_reasons": ["cleaned_count=0 < 5"],
  "date": "2026-04-26",
  "queries": 23,
  "gap_count": 86,
  "unique_count": 45,
  "raw_count_before": 300,
  "raw_count_after": 345,
  "cleaned_count_before": 0,
  "cleaned_count_after": 12,
  "reference_count_after": 30,
  "rejected_count_after": 303
}
```

## Tavily Key

从环境变量读取（按优先级）：
1. `TAVILY_API_KEYS` — 逗号分隔的多 Key
2. `TAVILY_API_KEY` — 单 Key
3. `TAVILY_KEY` — 备选单 Key
4. `tavily_key` / `tavily_key1` / `tavily_key2` — collect 模块兼容

支持多 Key 轮换，每个 Key 月度上限 1000 次。

## 每日预算

从 `config/search_policy.yaml` 读取：
- `daily_budget`: 80（上限）
- `max_results_per_query`: 5
- `gap_search.max_queries_per_platform`: 3
- 单次运行最多 30 个 query