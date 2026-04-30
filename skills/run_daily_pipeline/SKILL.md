# run_daily_pipeline — 日报全流程总控技能（V2）

## 功能
一键串联完整日报生产流程，10个步骤 + 动态无信号分支：

**正常流程（有信号）**：
```
1. collect              — 采集原始文章（RSSHub+RSS+Web）
2. filter               — 过滤与清洗
3. source_health_report — 源健康度诊断（best-effort，非阻塞）
4. tavily_gap_search    — Tavily 缺口补搜（条件触发）
5. filter_merged        — 补搜后重过滤（rule-only）
6. extract              — 事件抽取
7. score                — 事件八维评分
8. analyze              — 经营影响分析
9. generate_report      — 日报生成（V1+V2双稿）
10. editor_review       — 审稿定稿
```

**无信号分支（cleaned_count=0 after filter_merged）**：
```
1~5 同上
6. generate_no_signal_report — 无高质量新增信号日报
7. editor_review             — 审稿（无信号日报也需审计）
跳过: extract, score, analyze, generate_report
```

## 核心原则
- **无信号日报不是失败，而是诚实输出**
- 不重写已有模块逻辑，只负责调度
- 每步校验输出合法性
- 任一步失败停止后续（除非 continue_on_error=true）
- source_health_report 和 tavily_gap_search 是 best-effort（失败不阻塞）
- 支持 start_step / end_step 子集运行
- 支持 resume / force / dry_run
- 默认日期=今天（不是昨天）

## 动态分支逻辑
filter_merged 后检查 cleaned_count：
- **cleaned_count > 0**：走正常流程（extract → score → analyze → generate_report → editor_review）
- **cleaned_count == 0**：走无信号分支（generate_no_signal_report → editor_review）

## 输入
| 参数 | 说明 |
|------|------|
| project_root | 项目根目录 |
| date | 日期 YYYY-MM-DD（默认今天） |
| start_step | 从哪一步开始 |
| end_step | 运行到哪一步停止 |
| use_llm | 是否使用LLM |
| resume | 已有合格输出可跳过 |
| force | 强制重跑覆盖已有输出 |
| dry_run | 只检查不执行 |

## 输出
| 文件 | 路径 | 说明 |
|------|------|------|
| run_manifest.json | `data/runs/{date}/run_manifest.json` | 完整运行清单含report_type/no_signal字段 |
| step_status.json | `data/runs/{date}/step_status.json` | 各步骤状态 |
| run_summary.md | `data/runs/{date}/run_summary.md` | 人类可读摘要 |

无信号日报输出：
| 文件 | 路径 |
|------|------|
| 无信号日报 | `data/reports/{date}/daily_report_{date}_no_signal.md` |
| 最终日报 | `data/reports/{date}/final_report_{date}.md` |
| JSON结果 | `data/logs/{date}/generate_no_signal_report.json` |

## CLI
```bash
# 完整运行（默认今天）
python run_daily_pipeline.py --project-root .

# 指定日期
python run_daily_pipeline.py --project-root . --date 2026-04-27

# 从指定步骤开始
python run_daily_pipeline.py --project-root . --start-step filter

# 运行到指定步骤停止
python run_daily_pipeline.py --project-root . --end-step score

# 强制重跑
python run_daily_pipeline.py --project-root . --force true

# 干运行（只检查不改写）
python run_daily_pipeline.py --project-root . --dry-run true

# 不使用LLM（全rule模式）
python run_daily_pipeline.py --project-root . --use-llm false
```

## 步骤校验规则
| 步骤 | 必须存在 | 数量要求 |
|------|----------|----------|
| collect | raw_articles.json | raw_count >= 20 (warning) |
| filter | cleaned/reference/rejected | cleaned_count >= 3 (warning) |
| source_health_report | .json+.md | best-effort, 非阻塞 |
| tavily_gap_search | .json | best-effort, 非阻塞 |
| filter_merged | 同filter | 同filter |
| extract | events_raw.json | event_count >= 1 (fail) |
| score | events_scored.json | 数量 = events_raw |
| analyze | events_analyzed.json | 数量 = events_scored |
| generate_report | v1.md + v2.md | 至少1个event_id |
| generate_no_signal_report | no_signal.md | 7章节 |
| editor_review | review.md + final report | sendable判断 |

## 配置文件
- `config/pipeline.yaml` — 步骤定义、默认参数、阈值、无信号分支配置