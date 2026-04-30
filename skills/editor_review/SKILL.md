# editor_review — 日报审稿技能

## 功能
读取日报初稿(V1)和重构稿(V2)及事件分析数据，执行审稿流程，输出审稿报告和可发送终稿。

支持单稿和双稿两种模式：
- **双稿模式**：V1存在→V1检查→V2检查→两稿比较→LLM审稿→终稿校验
- **单稿模式**（V2不存在时自动回退）：规则校验→LLM审稿→终稿校验

## 输入
| 文件 | 路径 | 说明 |
|------|------|------|
| daily_report_draft_v1.md | `data/drafts/{date}/daily_report_draft_v1.md` | V1完整初稿（偏覆盖） |
| daily_report_draft_v2.md | `data/drafts/{date}/daily_report_draft_v2.md` | V2重构稿（偏取舍），可选 |
| daily_report_draft.md | `data/drafts/{date}/daily_report_draft.md` | 兼容稿（=V1） |
| events_analyzed.json | `data/events/{date}/events_analyzed.json` | 事件分析数据 |

## 输出
| 文件 | 路径 | 说明 |
|------|------|------|
| editor_review.md | `data/reviews/{date}/editor_review.md` | 审稿报告（含V1/V2/比较/终稿四部分） |
| 终稿 | `reports/daily/YYYY/MM/YYYY-MM-DD.md` | 可发送终稿 |
| 日志 | `data/logs/{date}/editor_review.log` | 运行日志 |

## 审稿流程
### 双稿模式（5步）
1. **V1规则校验** — 11条自动检查（完整性）
2. **V2规则校验** — 克制性检查（最多3信号、1600-2400字、标记合规等）
3. **两稿比较** — P1遗漏检查、事件池外事实、唯一动作合规
4. **LLM审稿** — Chat模型审稿+修订，不得创造新事实
5. **终稿校验** — 再一次规则检查，不合格标记【待人工复核】

### 单稿模式（3步，V2不存在时自动回退）
1. **规则校验** — 11条自动检查
2. **LLM审稿** — Chat模型审稿+修订
3. **终稿校验** — 再一次规则检查

## 模型路由
- 默认: LongCat-Flash-Chat
- Fallback: LongCat-Flash-Lite
- Thinking 仅作为 optional_deep_review

## CLI
```bash
python editor_review.py --project-root ... --date 2026-04-26 --use-llm true
# 可选参数
python editor_review.py --project-root ... --date 2026-04-26 --draft-v1-file ... --draft-v2-file ... --use-llm true
```
