# generate_no_signal_report — 无新增信号日报

## 功能

当所有采集方式（RSSHub/Tavily/Web/RSS 直连）都无法获得高质量信号时，
输出坦诚的"无新增信号"日报。

**这不是失败，而是诚实输出。**

## 触发条件

Pipeline 在 `filter` 步骤后发现 `cleaned_count == 0`（且 Tavily gap search
后仍为 0），自动触发本模块，跳过后续 extract/score/analyze/report 步骤。

## 用法

```bash
python skills/generate_no_signal_report/generate_no_signal_report.py \
    --project-root . --date 2026-04-26
```

## 输入

自动读取以下文件（无需手动指定）：

| 文件 | 路径 | 用途 |
|------|------|------|
| 原始文章 | `data/raw/YYYY-MM-DD/raw_articles.json` | 统计原始数量 |
| 合并文章 | `data/raw/YYYY-MM-DD/raw_articles_merged.json` | 统计合并后数量 |
| 参考文章 | `data/cleaned/YYYY-MM-DD/reference_articles.json` | 统计参考数量 |
| 拒绝文章 | `data/rejected/YYYY-MM-DD/rejected_articles.json` | 统计拒绝数量 |
| Tavily补搜 | `data/raw/YYYY-MM-DD/tavily_gap_articles.json` | 统计补搜数量 |
| 源健康报告 | `data/logs/YYYY-MM-DD/source_health_report.json` | 源健康度 |

## 输出

| 文件 | 路径 |
|------|------|
| 无信号日报 | `data/reports/YYYY-MM-DD/daily_report_{date}_no_signal.md` |
| 最终日报 | `data/reports/YYYY-MM-DD/final_report_{date}.md` |
| 日志 | `data/logs/YYYY-MM-DD/generate_no_signal_report.log` |
| JSON结果 | `data/logs/YYYY-MM-DD/generate_no_signal_report.json` |

## 返回值

```json
{
  "ok": true,
  "date": "2026-04-26",
  "report_type": "no_signal",
  "output_file": "data/reports/2026-04-26/daily_report_2026-04-26_no_signal.md",
  "final_file": "data/reports/2026-04-26/final_report_2026-04-26.md",
  "sendable": true,
  "raw_count": 53,
  "gap_count": 52,
  "gap_query_count": 18,
  "reference_count": 0,
  "rejected_count": 53
}
```

## 日报内容模板

7 个章节：
1. **今日一句话判断** — 坦诚无新增信号
2. **今日信息质量说明** — 采集量/补搜量/拒绝原因/关键词/时间窗口/源健康度
3. **平台变化解读** — 无新增信号
4. **竞对与品牌动作** — 无新增竞对动作
5. **对屈臣氏的经营提示** — 转向内部数据复盘
6. **今日唯一建议动作** — 复核昨日追踪项
7. **明日追踪清单** — 常规追踪项