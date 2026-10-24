# 架构总览与管线编排 — 只读审计报告

> 审计对象：`/app/working/projects/watsons-retail-intel`（Watson's Retail Intel）
> 审计范围：**架构总览与管线编排子系统**
> 本报告以仓库中真实代码为准，仅做只读审计，不改动任何仓库内容。
> 审计基准：`run_daily_pipeline.py` / `run_periodic_pipeline.py` / `scripts/run_daily.sh` / `scripts/run_periodic.sh` / `config/pipeline.yaml` / `config/runtime.yaml` / 各 skill `SKILL.md` / `README.md` / `AGENTS.md` / `project.yaml` / `MEMORY.md` / `docs/acceptance_criteria.md` / cron 配置。

---

## 1. 系统定位与目标

- **赛道与品类**：围绕**即时零售（Instant Retail）**赛道内的**个护美妆（Personal Care & Beauty）**品类（`README.md`）。
- **服务对象**：**屈臣氏（Watsons）电商经营决策**（`project.yaml` description、`AGENTS.md`、`MEMORY.md`）。
- **内容边界**：**只保留中国大陆（mainland）内容**，排除香港/台湾/澳门/海外主体——这是硬约束而非建议：
  - `filter_relevant_articles` 数据带 `region_tag`（mainland/hk/tw/overseas/unknown），`quality_funnel_report` 按 `by_region_tag` 拆解统计；
  - `periodic_editor_review`/`score_events` 通过 `_is_non_mainland_subject_event` 双重拦截（`acceptance_criteria.md` A5）；
  - `broad_search_discovery` 明确"排除港台域名，减少约 21 篇/天非大陆内容"（`MEMORY.md` Session 7）。
- **价值取向：少而精（quality over quantity）**。系统不追求采集量，而是层层过滤，目标产出"高价值、可决策"情报。质量漏斗（`quality_funnel_report`）本身就是对"采集量大但高质量信息少"这一问题的诊断手段。
- **"新鲜 / 相关"多重闸门**：为达成"少而精"，代码里存在多条把关链路，实际构成"新鲜度 + 相关性 + 新颖性"三重门：
  1. **相关门**：`filter` / `filter_merged`（规则评分 + LLM 语义复核，cleaned/reference/rejected 三池）；
  2. **新鲜门**：merge 阶段的 **URL 日期交叉验证 + 硬日期门**（`merge_raw_articles.py` L134/L602），以及 `cloakbrowser_date_verify`（L3 日期复核）、`temporal_fact_validation`（文章发布时间 vs 事实发生时间分离判断，拦截"今日发布、正文重汇总去年旧闻"的 AI 转载）；
  3. **新颖门**：`event_novelty_check`（账本比对，防止日报/周报每天重复写同一事件）。
  （"三重门"为对本系统把关链路的概括，源码中以 filter / hard-date-gate / temporal / novelty 四个 skill 实现。）

- **系统边界（不做的事）**：不做品牌自有销售数据的采集决策；所有情报源自公开数据源，经清洗、评分、摘要后**推送**（钉钉/邮件/播客），不产生经营结论以外的自动化动作（`README.md` 核心能力表）。

---

## 2. 顶层模块划分

系统按职责划分为 10 个子系统。各子系统最终都由两个总控入口驱动：日报（`run_daily_pipeline`）与周期报告（`run_periodic_pipeline`）。

| 子系统 | 入口脚本（真实路径） | 主要产物 |
|---|---|---|
| **采集** | `skills/collect_daily_articles/collect_daily_articles.py`（RSSHub+RSS+Web）；`skills/cloakbrowser_collect/cloakbrowser_collect.py`、`.../cloakbrowser_content_fetch.py`、`.../cloakbrowser_enrich.py`；`skills/tophub_collect/tophub_collect.py`；`skills/broad_search_discovery/broad_search_discovery.py`；`skills/xcrawl_enrich_articles/xcrawl_enrich_articles.py` | `data/raw/{date}/raw_articles.json`、`cloakbrowser_articles.json`、`cloakbrowser_enriched_articles.json`、`tophub_articles.json`、`broad_search_urls.json`、`xcrawl_enriched_articles.json` |
| **来源监控** | `skills/source_url_monitor/source_url_monitor.py` | `data/raw/{date}/newly_discovered_urls.json` + 累计账本 `data/source_ledger/seen_urls.jsonl` |
| **过滤** | `skills/filter_relevant_articles/filter_relevant_articles.py`（filter 与 filter_merged 共用）；诊断类 `skills/quality_funnel_report/`、`skills/source_health_report/`；`skills/tavily_gap_search/`（补搜） | `data/cleaned/{date}/cleaned_articles.json`、`reference_articles.json`；`data/rejected/{date}/rejected_articles.json`；`data/logs/{date}/*.json/*.md` |
| **事件加工** | `skills/merge_raw_articles/merge_raw_articles.py`（合并+URL日期交叉验证+硬日期门）；`skills/cloakbrowser_collect/cloakbrowser_date_verify.py`；`skills/enrich_cleaned_fulltext/enrich_cleaned_fulltext.py`；`skills/extract_events/extract_events.py`；`skills/temporal_fact_validation/`；`skills/event_novelty_check/`；`skills/score_events/score_events.py`；`skills/analyze_business_impact/analyze_business_impact.py`；`skills/evergreen_candidates/` | `data/raw/{date}/raw_articles_all.json`、`raw_articles_merged.json`；`data/events/{date}/events_raw.json`、`events_temporal.json`、`events_scored_novelty.json`、`events_scored.json`、`events_analyzed.json`；`data/event_ledger/events_seen.jsonl`；`data/evergreen/{date}/evergreen_candidates.json` |
| **日报告** | `skills/generate_daily_report/generate_daily_report.py`（V1+V2 双稿）；`skills/generate_no_signal_report/generate_no_signal_report.py`（无信号分支）；`skills/editor_review/editor_review.py`（审稿定稿） | `data/drafts/{date}/daily_report_draft_v1.md`、`daily_report_draft_v2.md`；`data/reviews/{date}/editor_review.md`；`reports/daily/{year}/{month}/{date}.md` 终稿；无信号日报 `data/reports/{date}/daily_report_{date}_no_signal.md` |
| **周期报告** | `skills/generate_periodic_report/generate_periodic_report.py`；`skills/periodic_synthesis/periodic_synthesis.py`（聚合层）；`skills/periodic_editor_review/periodic_editor_review.py`（质量门） | `data/periodic/{report_type}/{period_id}/periodic_synthesis.json`；`reports/{weekly,monthly,yearly}/...`；`data/runs/periodic/{type}/{period_id}/manifest.json` |
| **播客** | `skills/generate_podcast/generate_podcast.py`；`skills/podcast_review/podcast_review.py` | `podcasts/scripts/{date}.md` 口播稿、`podcasts/audio/{date}.mp3`（edge-tts）；`data/logs/{date}/podcast_review.json` |
| **邮件** | 日报：`skills/send_daily_report_email/send_daily_report_email.py`；周期：`generate_periodic_report.py` 内 `send_periodic_report_email`（D1：正文送 HTML，附件只带 MP3）（`acceptance_criteria.md` D1/D2） | `data/logs/{date}/send_daily_report_email.json`；HTML 邮件 |
| **健康检查** | `skills/health_check/health_check.py`（9 大类检查）；`skills/health_check/pip_ensure.py`（Step0 自愈装包）；`skills/health_check/pipeline_alert.py`（告警，管线最后一步） | `data/logs/{date}/health_check.json`、`pipeline_alert.json`；Step0 静默安装缺失依赖到 `.venv_packages/` |
| **调度** | `scripts/run_daily.sh`、`scripts/run_periodic.sh`（cron 启动器）；`skills/run_daily_pipeline/run_daily_pipeline.py`与`run_cloakbrowser_early.py`；`skills/run_periodic_pipeline/run_periodic_pipeline.py` | `data/runs/{date}/run_manifest.json`、`step_status.json`、`run_summary.md`；`data/runs/periodic/{type}/{period_id}/manifest.json`；日志 `data/logs/{date}/_run_daily.out`、`data/logs/{date}/_run_periodic.out` |

**调度编排关系**：
- `scripts/run_daily.sh` 与 `scripts/run_periodic.sh` 是 cron 侧的**最小启动器**，职责 = 自愈装包(Step0) + 调用对应 pipeline 模块(Step1) + 写 `_run_daily.out` / `_run_periodic.out`。
- cron 配置真实存在：`config/cron_daily_0700.json`（`0 7 * * *` Asia/Shanghai，agent 型任务）、`config/cron_backup/periodic_0900_newspec.json`（`0 9 * * *`，agent 型，只"启动一行即返回"）。`config/runtime.yaml` 中亦描述 05:00 CloakBrowser 早班、07:00 主管道、09:00 日报、周一/月初 周期 等 cron。
- `run_cloakbrowser_early.py` 每天 05:00 提前跑 CloakBrowser 的 collect→enrich→date_verify，主管道 07:00 用 `--resume true` 跳过已完成步骤。

---

## 3. 日报管线完整执行流程

### 3.1 权威步骤顺序（来自 `run_daily_pipeline.py` 的 `STEP_ORDER`）

> 注意：`config/pipeline.yaml` 描述的是较老的 **V2（10 步）** 流程；**当前实际运行代码的 `STEP_ORDER`（V4，29 步）才是权威**。`scripts/run_daily.sh` 直接 `-m skills.run_daily_pipeline.run_daily_pipeline`，走 `STEP_ORDER`。

```
health_check → collect → source_url_monitor → broad_search_discovery
→ cloakbrowser_collect → cloakbrowser_content_fetch → tophub_collect
→ xcrawl_enrich_articles → cloakbrowser_enrich → merge_raw_articles
→ cloakbrowser_date_verify → filter → quality_funnel_report
→ source_health_report → tavily_gap_search → filter_merged
→ enrich_cleaned_fulltext → extract → temporal_fact_validation
→ novelty_check → score → analyze → evergreen_candidates
→ generate_podcast → podcast_review → generate_report / generate_no_signal_report
→ editor_review → send_daily_report_email → pipeline_alert
```

与任务书给出的大致序列核对，真实差异点已用代码确认：
- 任务书中的 `cloakbrowser→xcrawl→tophub` 顺序，真实代码为 `cloakbrowser_collect → cloakbrowser_content_fetch → tophub_collect → xcrawl_enrich_articles → cloakbrowser_enrich`（XCrawl 与 topHub 位置交换，且 enrich 分两段）。
- 任务书中的 `filter→quality/source_health`，真实顺序为 `filter → quality_funnel_report → source_health_report → tavily_gap_search → filter_merged`（两个诊断步骤在 filter 之后、补搜之前）。
- 任务书中的 `enrich_fulltext` 真实位于 `filter_merged` 之后、`extract` 之前。
- 任务节流中的 `temporal→novelty→score→analyze` **与代码一致**：`extract → temporal_fact_validation → novelty_check → score → analyze`，即 **novelty 在 score/analyze 之前**（见 `run_daily.sh` 与 commit 2afeb4d 说明）。
- 播客（`generate_podcast → podcast_review`）在代码里位于 `analyze → evergreen_candidates` 之后、`generate_report` **之前**（与 MEMORY.md 方案C：播客独立于日报、从事件池直接生成一致）。

### 3.2 参数语义（`run_daily_pipeline.py` main()）

| 参数（CLI） | 默认 | 语义 |
|---|---|---|
| `--project-root` | 必填 | 项目根目录 |
| `--date` | 今天 | 目标日期 YYYY-MM-DD（注意：默认**今天**，非昨天） |
| `--start-step` | 无(=首步) | 从 `STEP_ORDER` 该步骤开始；未知则报错 `{"ok":false}` |
| `--end-step` | 无(=末步) | 运行到该步骤为止（含）；未知则报错 |
| `--use-llm` | `true` | 是否用 LLM。`false` 时 filter/extract/analyze/generate_report/editor_review/generate_podcast/podcast_review 走纯规则；`filter_merged` 恒为 rule-only |
| `--send-email` | `false` | 是否尝试发日报邮件；默认禁用 |
| `--recipient` | 无 | 覆盖 `config/email.yaml` 收件人 |
| `--resume` | `false`（CLI） | `true` 时跳过已有合格输出的步骤（校验通过 + 输出文件 mtime 在数据窗口 `date-1 07:00` CST 之后，避免回退旧数据） |
| `--force` | `false` | `true` 时忽略 resume 强制重跑 |
| `--dry-run` | `false` | 只做合法性校验不执行，`step_info.status=dry_run` |
| `--continue-on-error` | `false` | `true` 时某关键步骤失败仍继续后续（非关键步骤无论何值都继续） |

调用方式：优先 `import` 步骤函数，import 抛异常时回退 `subprocess` 调 CLI（`call_step()`）。subprocess 超时：broad_search 1200s、xcrawl 与所有 CloakBrowser 步骤 900s、其余 600s。

### 3.3 分步行为、中间产物与关键性

> 表内"中断性"：**阻塞**=失败会停止后续（除非 `--continue-on-error true`）；**非阻塞(warning)**=失败仅记录并在 manifest 置 warning，继续后续。核心判断依据 = `NON_CRITICAL_STEPS` 集合（代码硬编码）+ 各步 `STEP_VALIDATORS` 校验。

| # | 步骤 | 关键行为 | 输入 → 主要输出 | 中断性 |
|---|---|---|---|---|
| 0 | `health_check` | 9 大类健康检查（Longcat/Tavily/XCrawl keys、RSSHub 连通、EdgeTTS、依赖包、配置、目录、模块 import） | → `data/logs/{date}/health_check.json` | 非阻塞 |
| 1 | `collect` | RSSHub+RSS+Web 多源采集，时间窗口 `(date-1)07:00~date07:00`(CST) | → `data/raw/{date}/raw_articles.json`；min_raw_count 20（warning） | **阻塞**（校验 raw_articles.json 缺则 fail） |
| 2 | `source_url_monitor` | 监控栏目页/频道页新 URL，与 `seen_urls.jsonl` 账本比对 | → `data/raw/{date}/newly_discovered_urls.json` | 非阻塞 |
| 3 | `broad_search_discovery` | Tavily+XCrawl 多平台×品类×动作×竞对关键词矩阵广搜，只发现 URL 不进 cleaned 池 | → `data/raw/{date}/broad_search_urls.json` | **阻塞**（校验文件缺则 fail） |
| 4 | `cloakbrowser_collect` | [L1] CloakBrowser 隐身直抓垂直媒体（品观/财联社等），绕过搜索 API 日期不准 | → `data/raw/{date}/cloakbrowser_articles.json`；max_per_source=6 | 非阻塞 |
| 5 | `cloakbrowser_content_fetch` | [L1.5] 正文抓取 | → `data/raw/{date}/cloakbrowser_enriched_articles.json`；max=30 | 非阻塞 |
| 6 | `tophub_collect` | TopHub.today 聚合搜索覆盖数百源，分钟级发布时间 | → `data/raw/{date}/tophub_articles.json`；max_days_back=2 | 非阻塞 |
| 7 | `xcrawl_enrich_articles` | 对 monitor/broad/podcast 发现的 URL 用 XCrawl scrape 补全正文（7 key 轮换，1 credit/URL） | → `data/raw/{date}/xcrawl_enriched_articles.json` | **阻塞**（校验缺则 fail） |
| 8 | `cloakbrowser_enrich` | [L2] 内容增强，补救 XCrawl 失败 | → `data/raw/{date}/xcrawl_cloakbrowser_fallback.json`；max=40 | 非阻塞 |
| 9 | `merge_raw_articles` | 合并五类采集产物，URL 归一化去重（先到先得），**URL 日期交叉验证 + 硬日期门**（频道=1天/搜索=3天/XCrawl=7天截止） | → `data/raw/{date}/raw_articles_all.json`（含各 collector 计数）；min_total 10（warning） | **阻塞** |
| 10 | `cloakbrowser_date_verify` | [L3] CloakBrowser 日期复核，修正错误发布日期 | → `data/raw/{date}/cloakbrowser_date_verify.json`；max=8 | 非阻塞 |
| 11 | `filter` | 规则评分 + LLM 语义复核，cleaned(rule≥6或LLM main)/reference/rejected 三池；读 `raw_articles_all.json` | → `data/cleaned/{date}/cleaned_articles.json`、`reference_articles.json`、`data/rejected/{date}/rejected_articles.json`；min_cleaned 3（warning） | **阻塞** |
| 12 | `quality_funnel_report` | 19 项漏斗诊断，raw→cleaned/reference/rejected 转化拆解，定位采集量高但有效信息少的原因 | → `data/logs/{date}/quality_funnel_report.json|.md` | 非阻塞（诊断） |
| 13 | `source_health_report` | 每源按 A/B/C/D 分级给 keep/limit/reference/disable 建议 | → `data/logs/{date}/source_health_report.json|.md` | 非阻塞（诊断） |
| 14 | `tavily_gap_search` | cleaned<5 或核心平台无有效文章时触发 Tavily 补搜，合并 + rule-only 重过滤 | → `data/raw/{date}/tavily_gap_articles.json`、`raw_articles_merged.json` | 非阻塞 |
| 15 | `filter_merged` | 补搜后 rule-only 重过滤（不启用 LLM） | → 覆盖 cleaned/reference/rejected；**cleaned_count==0 触发无信号分支** | **阻塞**（本身）+ 无信号分流 |
| 16 | `enrich_cleaned_fulltext` | 对 cleaned 中 content<200 字的文章补抓正文（CloakBrowser→requests→XCrawl 三级） | → 回写 cleaned_articles.json | 非阻塞 |
| 17 | `extract` | 结构化事件抽取（platform_move/competitor_move/brand_move 等 10 类）；LLM 失败降级规则抽取 | → `data/events/{date}/events_raw.json`；min_event 1（**fail**） | **阻塞**；**无信号时跳过** |
| 18 | `temporal_fact_validation` | 文章发布时间 vs 事实发生时间分离；识别"今日发布但正文是旧闻"的转载文，标记 new/updated/upcoming/ongoing/historical_background/retrospective | → `data/events/{date}/events_temporal.json`、`data/logs/{date}/temporal_stats.json`；事件为 0 属正常过滤（warning） | **阻塞**（缺文件 fail） |
| 19 | `novelty_check` | 账本比对新颖性：new_today/updated_today/ongoing/repeated/background，写 `events_seen.jsonl`；**提前到评分前执行** | → `data/events/{date}/events_scored_novelty.json` | **阻塞** |
| 20 | `score` | 八维加权评分（strategic_importance 0.18 / watsons_relevance 0.24 / ...）+ 9 条硬降级规则，分 P0/P1/P2/ARCHIVE | → `data/events/{date}/events_scored.json` | **阻塞**；**无信号时跳过** |
| 21 | `analyze` | 每条事件做经营影响分析（opportunity/risk/watch/noise），产出 business_analysis 与建议动作 | → `data/events/{date}/events_analyzed.json` | **阻塞**；**无信号时跳过** |
| 22 | `evergreen_candidates` | 从 reference/rejected 中筛常青知识入库 | → `data/evergreen/{date}/evergreen_candidates.json` | 非阻塞 |
| 23 | `generate_podcast` | 从事件池直生口播稿（1800-2500 中文字，无信号 200-300）+ edge-tts 音频；**失败不阻塞日报**（仅缺附件） | → `podcasts/scripts/{date}.md`、`podcasts/audio/{date}.mp3` | 非阻塞 |
| 24 | `podcast_review` | 5 项审稿（口语化/非照读/行动建议/事实准确/通勤适用），≥3 项通过算可用；**失败不阻塞日报** | → `data/logs/{date}/podcast_review.json` | 非阻塞 |
| 25 | `generate_report` | 读取 events_analyzed 生成 V1（覆盖）+V2（取舍）双稿；无信号时替换为 `generate_no_signal_report` | → `data/drafts/{date}/daily_report_draft_v1.md`、`_v2.md`、`daily_report_draft.md` | **阻塞**（校验 V1/V2/v2 兼容 + ≥1 event_id）；**无信号替换** |
| 26 | `editor_review` | 审稿定稿：规则校验 → LLM 审稿 → 终稿校验，输出 `sendable` 判定与终稿 | → `data/reviews/{date}/editor_review.md`、`reports/daily/{year}/{month}/{date}.md`（无信号：`data/reports/{date}/final_report_{date}.md`） | **阻塞**（报告生成核心，失败即 pipeline 失败） |
| 27 | `send_daily_report_email` | 发送 HTML 日报邮件+附件；**默认 disabled**，需 `--send-email true` 且过 8 项安全门（含 sendable=true） | → `data/logs/{date}/send_daily_report_email.json` | 非阻塞 |
| 28 | `pipeline_alert` | 仅在 critical 健康状态或关键步骤失败才告警（不影响播客/日报的小问题不发） | → `data/logs/{date}/pipeline_alert.json` | 非阻塞 |

### 3.4 失败 / 降级语义

- **关键步骤失败**：置 `pipeline_ok=False`；`--continue-on-error=false`（默认）时**中断后续**；`true` 时记录错误后继续。
- **非关键步骤失败**（`NON_CRITICAL_STEPS` 集合，代码硬编码）恒为 **warning 继续**，不影响核心数据流：
  `health_check, source_url_monitor, cloakbrowser_collect, cloakbrowser_content_fetch, cloakbrowser_enrich, cloakbrowser_date_verify, tophub_collect, quality_funnel_report, source_health_report, tavily_gap_search, enrich_cleaned_fulltext, evergreen_candidates, generate_podcast, podcast_review, pipeline_alert`。
- **CloakBrowser 不可用**（SDK/Chromium 缺失）：相关步骤自动 `_skip_cloakbrowser_step()` 返回 `ok:true, skipped:true`，不阻塞（有其他源兜底）。
- **LLM 降级**：`--use-llm false` → filter/extract/analyze/generate_report 等纯规则；LLM 调用失败时各 skill 内部自动 fallback（filter 降级纯规则、extract 降级 `rule_fallback`、日报模型 Thinking→Chat→Lite）。付费兜底（gpt-5.4-xhigh）仅 `ALLOW_PAID_FALLBACK=1` 才启用。
- **无信号分支**：`filter_merged` 后 cleaned_count==0 → 跳过 `extract/score/analyze/generate_report`，替换为 `generate_no_signal_report`，仍跑 `editor_review`（无信号日报也需审计），输出诚实的"无新增信号"日报。**这是诚实输出而非失败**。
- **生成播客失败不阻塞日报**；但 `send_daily_report_email` 会在日志记录"播客缺失"，附件不含 MP3。
- **邮件**：默认禁发；即使 `--send-email true`，仍须通过 **8 项安全门**（manifest 存在 / sendable=true / 终稿存在 / 非【待人工复核】开头 / editor_review 判"✅ 可发送" / 8 章节齐 / ≥1 event_id / 收件人存在）(`send_daily_report_email/SKILL.md`)。`--dry-run false` 才真实发送。
- **resume 时间门**：已合格输出但 mtime 早于数据窗口开始（`date-1 07:00` CST）则**不跳过**，避免回退使用旧数据。

### 3.5 产物落盘
`data/runs/{date}/run_manifest.json`（全步骤状态+sendable+report_type）、`data/runs/{date}/step_status.json`、`data/runs/{date}/run_summary.md`；全程日志入 `data/logs/{date}/`，启动器整体写 `data/logs/{date}/_run_daily.out`。

---

## 4. 周期管线（`run_periodic_pipeline.py` + `scripts/run_periodic.sh`）

### 4.1 调度规则
- **默认日期**：`--date` 未给则用 CST 今天；`--date` 给定则按其判定。
- 触发判断（代码 `run_periodic_pipeline()`）：
  - `today.weekday()==0`（周一）→ 生成**上周周报**（`get_last_week_range`，上周一~周日）；
  - `today.day==1` → 生成**上月月报**（`get_last_month`）；
  - `today.month==1 and today.day==1` → 生成**上年年报**（`get_last_year`）。
- **冲突处理**：三条件各自独立 append 到 `tasks` 列表（顺序=周报→月报→年报），随后**按此顺序串行执行**（`acceptance_criteria.md` D4）。
- 无任一条件命中 → 直接返回 `{"tasks_run":[],"results":{}}`，今天无需生成。

### 4.2 lock 防重入
- 周期级 lock：`data/locks/periodic.lock`，`fcntl.flock(LOCK_EX|LOCK_NB)` **非阻塞**。
- 已被占用 → 跳过本次（warn），返回 `{"locked":true}`；lock 获取异常时**宁可执行也不静默跳过**（代码注释：极端情况放行），保证不因锁故障丢报告。
- 持锁期间写 PID（`os.ftruncate`+`os.write`）便于排查；完成后 `flock(LOCK_UN)` 释放。（`acceptance_criteria.md` D5）

### 4.3 总超时
- `_TOTAL_TIMEOUTS = {"weekly":1200, "monthly":1800, "yearly":2700}`（秒），墙钟 deadline，超时中止发送（`acceptance_criteria.md` B5）。

### 4.4 每类报告总控 + manifest 写入
- 每类报告逐个调用 `generate_periodic_report(report_type, period_info, project_root, send_email, recipient)`，逐类记耗时、success、sendable、report_path、audio_path、error、tts_success、podcast_review、paid_fallback_calls 等状态。
- **manifest**：`data/runs/periodic/{report_type}/{period_id}/manifest.json`，`period_id` 格式：weekly=`"%d-W%02d"`（ISO 周，如 `2026-W32`）、monthly=`"%Y-%m"`、yearly=`"%Y"`；manifest 含 `log_paths`（run_out 指向 `data/logs/periodic/periodic_{type}.log`，便于事后定位日志）(`acceptance_criteria.md` D6/D7)。
- `generate_periodic_report` 内部四阶段 + 交付门（`generate_periodic_report.py` 头注）：periodic_synthesis(聚合) → Phase1 分章生成 → Phase2 全局审稿 → Phase3 定向修订(≤2轮) → Phase4 终稿合并 + periodic_editor_review 质量门 + 口播稿 + TTS → delivery gate → HTML 邮件 + MP3。
- **聚合层优先回退**：synthesis 优先，失败回退历史报告，绝不让管线崩溃（B1）。
- **幂等**：同周期不重复发送（delivery ledger）（B7/D）。
- **质量门**（`periodic_editor_review`）：任何 high issue / review 执行失败 / 章节缺失 / coverage 不达标 → `sendable=false`，**绝不静默降级**（A 系列验收项）。
- **覆盖度**：coverage 要求周≥0.85 / 月≥0.90 / 年≥0.95，不达标不发（A2）。

---

## 5. 关键设计约束

### 5.1 日报 2026-08 起停发（只生成事件池供周期报告）
- `scripts/run_daily.sh`（真实代码注释）明确：**2026-08 起不再生成日报/播客/日邮，管线停在 `analyze`**，即只生成完整**事件池**（`events_analyzed.json`）供周/月/年报使用。
- 调用：`--end-step analyze --send-email false`、`--continue-on-error true`。停在 analyze 是因为事件池最后一步需产出的 `priority`/`weighted_score`（由 score 产出）与 `business_analysis`（由 analyze 产出）在此前必须完成。
- **⚠️ 明确警告（脚本注释）**：不能停在 `novelty_check`。链路重排后（commit `2afeb4d`）novelty 已移到 score/analyze **之前**，若 `--end-step novelty_check` 会令 score/analyze **永不执行**，导致事件池缺 priority/business_analysis 字段 → 周期报告源材料空泛（**08-10/11 事故**）。
- 事件池即周期报告唯一标准化输入源头（`periodic_synthesis` 从 `data/events/{date}/events_analyzed.json` 消费）。

### 5.2 launcher 最小 token
- `scripts/run_daily.sh` 头注：**"cron 端只需一行调用，agent 几乎不消耗 token（无需调查/汇报）"**。启动器自包含、可后台脱离运行（`setsid sh ... &`）。
- 周期 cron 配置 `config/cron_backup/periodic_0900_newspec.json` 是典型体现：agent 任务只被要求"后台启动脚本后回复一行『已启动 YYYY-MM-DD』即可"，**不要调查、不要 tail 日志、不要多行总结**，脚本自身负责自愈装包、判定是否生成、跑流程、发邮件、写日志。
- 目的：把耗 token 的编排/汇报移出 cron 热路径，管线由 python 脚本自驱动。

### 5.3 Agent 层 vs 脚本层 LLM 分离
- **脚本层 LLM**（结构化情报加工）：各 skill 通过 `skills/utils/llm_client.py`（及 `model_router.yaml` 统一路由）调用 Longcat/OpenCode 等做 filter、extract、analyze、report、podcast 生成/审稿；模型配置单一来源 `config/model_router.yaml`（`acceptance_criteria.md` E4），不硬编码。
- **Agent 层**（cron 编排）：`task_type: agent` 的任务仅负责调用 shell 启动器并**简短汇报**，不参与情报生成逻辑；agent 触发后立即返回，管线在脚本后台运行。
- 两层职责清晰分离：agent 不消耗 LLM 做数据加工，脚本不依赖 agent 做决策；这正是 5.2"最小 token"的实现基础。
- 附属健康机制：Step0 `pip_ensure.py` 在启动时自愈装缺失依赖到项目本地 `.venv_packages/`（保证离线/缺包环境可用，`acceptance_criteria.md` E1）。

---

## 6. 整体数据流图（ASCII）

```
                        ┌──────────────────────────────────────────────┐
                        │         cron 调度层（agent 最小 token）        │
                        │ daily 0 7 * * * · 日报 0 9 · periodic 0 9     │
                        │ cloak_early 0 5 · (周1/月初/1月1)            │
                        └───────┬───────────────────────┬──────────────┘
                        sh scripts/run_daily.sh          sh scripts/run_periodic.sh
                                │                                  │
                                ▼                                  │
  ┌──────────────── daily pipeline (STEP_ORDER V4, 29步) ────────┐  │
  │                                                              │  │
  │  采集 ──► source_url_monitor · collect(RSS/RSSHub/Web)       │  │
  │        ─► broad_search_discovery · cloakbrowser_collect(L1)  │  │
  │        ─► cloakbrowser_content_fetch(L1.5) · tophub_collect  │  │
  │        ─► xcrawl_enrich_articles · cloakbrowser_enrich(L2)   │  │
  │              │                                               │  │
  │  data/raw/{date}/*.json                                       │  │
  │              ▼                                               │  │
  │  merge_raw_articles —— URL日期交叉验证 + 硬日期门(新鲜)         │  │
  │  cloakbrowser_date_verify(L3)                                │  │
  │  filter —— 相关门(rule+LLM → cleaned/reference/rejected)     │  │
  │  quality_funnel_report · source_health_report(诊断·非阻塞)     │  │
  │  tavily_gap_search(条件补搜) · filter_merged(rule-only)       │  │
  │       │  clean==0 ──► 无信号分支(generate_no_signal)          │  │
  │       ▼                                                      │  │
  │  enrich_cleaned_fulltext → extract_events(events_raw.json)   │  │
  │  temporal_fact_validation(新鲜门: 文时 vs 事实时)             │  │
  │  novelty_check(新颖门: 账本比对 events_seen.jsonl)            │  │
  │  score(八维 P0/P1/P2/ARCHIVE) → analyze(opportunity/risk/    │  │
  │      watch + business_analysis)                              │  │
  │  evergreen_candidates                                         │  │
  │       │                                                      │  │
  │  2026-08 起动线停在 analyze（只产事件池供周期报告）              │  │
  │       ▼                                                       ▼
  │  ┌─ 播客: generate_podcast → 口播稿{date}.md                  │ ┌─ generate_periodic_report
  │  │       → edge-tts → audio/{date}.mp3                        │ │    periodic_synthesis(聚合)
  │  │       → podcast_review (5项≥3通过)                          │ │    Phase1分章→Phase2审稿
  │  ├─ 日报: generate_report(V1+V2)/generate_no_signal            │ │    →Phase3修订→Phase4终稿
  │  │       → editor_review → sendable终稿                        │ │    periodic_editor_review
  │  └─ 交付: send_daily_report_email(8项安全门·默认禁用)            │ │    质量门(coverage/章节)
  │          pipeline_alert(末步·仅关键问题告警)                     │ │    →口播稿+TTS
  │                                                              │ │    →delivery gate
  │                                                              │ │    →HTML邮件+MP3
  └──────────────────────────────────────────────────────────────┘ │
                                                                     ▼
  事件池 data/events/{date}/events_analyzed.json ◄────────────────────
```

分叉要点：
- **新鲜/相关/新颖三重门**：`filter`(相关) · `merge`硬日期门+`temporal_fact_validation`+`cloakbrowser_date_verify`(新鲜) · `novelty_check`(新颖)。
- **无信号分支**：`filter_merged` 后 `cleaned_count==0` 触发，输出诚实"无新增信号"日报。
- **周/月/年报**：`run_periodic_pipeline` 按周一/月初/1月1日判定，冲突周报→月报→年报串行，`periodic.lock` 防重入，总超时 1200/1800/2700s。
- **2026-08 后**主管道停在 `analyze`，日报/播客/日邮不再产出，事件池交给周期报告（见第 5.1 节）。


---

## 附注与已知文档偏移

1. `config/pipeline.yaml` 描述的是 **V2 10 步**流程，比运行代码的 **V4 29 步** `STEP_ORDER` 落后；审计以运行代码为准。
2. `skills/source_health_report/SKILL.md` 自我标注"暂未集成到 run_daily_pipeline 主流程"，但 `run_daily_pipeline.py` 的 `STEP_ORDER` 与 `STEP_DEFS` 已将 `source_health_report` 作为正式步骤，属文档滞后于代码。
3. 个别 skill 无 `SKILL.md`（`health_check`、`cloakbrowser_collect`、`tophub_collect`、`enrich_cleaned_fulltext`），其职责依据模块头部 docstring 与 `STEP_DEFS` 注释补全，已在第 2/3 节如实标注。

