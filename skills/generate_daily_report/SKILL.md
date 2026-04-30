# generate_daily_report — 日报生成技能

## 功能

读取 `events_analyzed.json`，生成两稿 Markdown 格式的经营日报：
- **V1**（完整初稿，偏覆盖）
- **V2**（经营总编重构稿，偏取舍、压缩和经营判断）

同时输出兼容文件 `daily_report_draft.md` 指向 V1。

仅生成初稿，不发送邮件，不做最终定稿。

## 输入

| 文件 | 路径 | 说明 |
|------|------|------|
| events_analyzed.json | `data/events/{date}/events_analyzed.json` | 分析后的事件 |
| reference_articles.json | `data/cleaned/{date}/reference_articles.json` | 参考文章（可选） |

## 输出

| 文件 | 路径 | 说明 |
|------|------|------|
| daily_report_draft_v1.md | `data/drafts/{date}/daily_report_draft_v1.md` | V1完整初稿 |
| daily_report_draft_v2.md | `data/drafts/{date}/daily_report_draft_v2.md` | V2重构稿 |
| daily_report_draft.md | `data/drafts/{date}/daily_report_draft.md` | 兼容稿（=V1） |
| generate_daily_report.log | `data/logs/{date}/generate_daily_report.log` | 日志 |

## 两稿差异

| 维度 | V1（完整初稿） | V2（重构稿） |
|------|---------------|-------------|
| 定位 | 偏覆盖 | 偏取舍、压缩和经营判断 |
| 核心信号 | 全部P1 | 最多3条 |
| 建议动作 | 1条 | 1条 |
| 中文字数 | 无上限（自然完成） | 1600-2400 |
| low confidence | 标⚠️待验证，仍可展开 | 只作线索，不展开 |
| rule_fallback | 标🔄规则兜底，仍可展开 | 只作线索，不展开 |

## 8 个固定章节

1. 今日一句话判断
2. 今日最值得关注的3个信号
3. 平台变化解读
4. 竞对与品牌动作
5. 品类与场景机会
6. 对屈臣氏的经营提示
7. 今日唯一建议动作
8. 明日追踪清单

## 事件选择规则

- ARCHIVE 不进入正文
- P1 优先于 P2
- low confidence 只能写"待验证线索"
- rule_fallback 不得写强结论
- 今日唯一建议动作只能从 P1 + 高 confidence + 非 rule_fallback 中选

## 模型路由

- 默认: LongCat-Flash-Chat（V1润色、V2重构）
- Fallback: LongCat-Flash-Lite
- Thinking 作为 optional_deep_review

## CLI
```bash
python generate_daily_report.py --project-root . --date 2026-04-26 --use-llm true
```

## 依赖

- `skills/utils/llm_client.py`（LLM 辅助可选）
- `skills/utils/model_router.py`（模型路由）
- `data/events/{date}/events_analyzed.json`