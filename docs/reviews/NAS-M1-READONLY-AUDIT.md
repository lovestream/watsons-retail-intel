# NAS Docker 经营情报系统 — M1 全面只读审计

- **阶段**: M1（只读，不修改生产）
- **审计时间**: 2026-10-25 15:0x CST（Asia/Shanghai）
- **审计对象**: NAS Docker 内真实运行的 QwenPaw（v2.2.2.post1，容器 hostname `2622cf59e13c`）+ Watsons 经营情报管线 `/app/working/projects/watsons-retail-intel`
- **依据文档**: 已读取仓库主分支 `docs/11_20261025_nas_qwenpaw_aihot_architecture_decision.md`（架构/AIHOT 对标/选型）与 `docs/12_20261025_qwenpaw_step_by_step_execution.md`（分阶段手册，本审计按其中 M1 清单执行）。M0 已由用户/ChatGPT 在 GitHub 完成并复审通过（远程 main 已到 `c4f98bd`）；**本阶段不处理任何 Muse 相关文件，不重跑 M0。**
- **执行约束**: 只读；不改代码/任务/模型连接/正式账本/邮件投递；不配 Key、不切换 Provider、不做闲时调度（模型由用户选定 RightCode「百亿补贴」DeepSeek Flash，价目与选型见第 11 篇 §1，本审计仅核验实际使用状态）；不把 Key/Cookie/内部消息或大批版权原文上传 public GitHub。

---

## 0. 审计方法、口径与验证方式

- **方法**: 全部结论来自本容器内**真实文件**与 `jobs_history`/`data/runs`/`data/logs`/`data/events`/`data/periodic`/账本/交付台账，以及 QwenPaw 会话层 token 计量。
- **参考文档获取**: 第 11/12 篇位于**远程主分支**（初版因本容器到 GitHub TLS 不稳未能访问；后经 `git fetch origin` 读取到 `origin/main:docs/11|12`）。本稿已按其口径修订。
- **口径/分母**:
  - “每日事件数/新颖性/评分”分母 = 当日 `data/runs/<date>/run_manifest.json` 的 extract/novelty/score 统计。
  - “管线 LLM 成功率”分母 = 当日 extract/analyze 的 `llm_success_count + llm_failed_count`。
  - “source_health 分级”分母 = `source_health_report.json` 的 `source_count` 与各源 `cleaned_count/total_count`。
  - “filter 漏斗”分母 = `quality_funnel_report.json` 的 `total_in_funnel`（= 当日处理总量）。
  - “正文可打开率”代理 = 同日 `by_content_status`（empty/thin/moderate/rich）。因采集器抓取失败常落为 empty/thin，用其近似“可读率”。
- **验证方式**: 对每个结论给出 `文件路径/键名`；未进行任何写操作。

---

## 1. 环境与持久化（只读核对）

| 项 | 实测 | 说明 |
|---|---|---|
| 容器 | hostname `2622cf59e13c`，root | 与第 11 篇“NAS Docker 内 QwenPaw”一致 |
| QwenPaw 版本 | `2.2.2.post1` | |
| 时区 | 系统 UTC，cron 任务时区 `Asia/Shanghai`（11/12 篇均为北京时间口径） | 注意日志/clock 是 UTC，业务标签是 CST |
| 工作区 | `/app/working/projects/watsons-retail-intel`（管线），`/app/working/workspaces/watsonmainagent`（QwenPaw） | 路径与第 11 篇描述一致 |
| 工作区 dirty 态 | `git status`：`M data/delivery/ledger.jsonl`、`M data/event_ledger/events_seen.jsonl`、`M data/source_ledger/seen_urls.jsonl`、`M data/periodic/weekly/W32/periodic_synthesis.*`、`M AUDIT_*`；大量 untracked 运行产物（data/evergreen、data/backups、scripts/_*.py 等） | 这些是实时运行/试验产物，**本审计未提交**；仅新提交评审文档 |

> 持久化卷/挂载无法从容器内 100% 确证（需 NAS 侧 docker inspect），本稿如实标注为“未独立验证”，不臆断。

---

## 2. 定时任务真实启用状态

`jobs.json` 仅 2 个任务；**无独立健康检查任务**：

| 任务 | ID | schedule | tz | type | timeout | 语义 |
|---|---|---|---|---|---|---|
| watsons-daily-0700 | a892335a… | `0 7 * * *` | Asia/Shanghai | agent | 120s | 极简 prompt：`setsid sh scripts/run_daily.sh…` 并回“已启动” |
| watsons-periodic-0900 | 7c4eb73d… | `0 9 * * *` | Asia/Shanghai | agent | 120s | 同上，改 `run_periodic.sh` |

**P0 关键缺陷**：agent 型 cron 的 `success` = agent 回复成功，**不代表脚本真的启动/跑完**。证据见 §4（今天 daily cron success 但无 `_run_daily.out`）。这对应第 11 篇 §3.1“调度不经过 Agent 思考、只有一个权威调度”的整改方向——目前调度仍依赖 agent 回复，且无启动心跳。

---

## 3. 指定脚本/配置的核验结果（M1 要求逐项核对）

| 对象 | 结论 |
|---|---|
| `scripts/run_daily.sh` | 存在且可读；自包含（pip_ensure→`run_daily_pipeline --end-step analyze --send-email false`）；即**日报 2026-08 起只产事件池、不发日报邮件**。但脚本注释与运行 manifest 存在偏移：管线后段仍 best-effort 跑 `generate_podcast/podcast_review`（见 §7.3） |
| `scripts/run_periodic.sh` | 存在；委托 `run_periodic_pipeline`；日期注入待核（第 11 篇已提示 run_periodic.sh 日期未透传 CLI 的旧风险） |
| `skills/run_daily_pipeline/run_daily_pipeline.py` | 真实主程序；step 顺序/失败降级以代码为准（已读 docs/01） |
| `skills/run_periodic_pipeline/run_periodic_pipeline.py` | 真实主程序；周一/每月1日/1月1日判定、lock、超时（已读 docs/06） |
| `config/model_router.yaml` | **当前仍为历史 OpenCode `deepseek-v4-flash-free`**，且 `global_fallback` 与各任务 default/fallback **是同一模型（非独立备用）**。今日 periodic 日志实测：`generate_periodic_report 使用 deepseek-v4-flash-free, fallback=[deepseek-v4-flash-free]`。→ 与第 11 篇“不要按旧仓库配置推断在线模型；M1 只核对实际调用”一致：**管线实际仍指向 OpenCode 且为同模型兜底**，是 P0 问题 |
| `config/scoring.yaml` | 八维权重 `[strategic .18, watsons_relevance .24, …]`；阈值 `P0≥4.3 / P1≥3.6 / P2≥2.8`；硬降级 `rule_fallback_max=P2`、`confidence_low_max=P2`、`watsons_relevance_low_max=P2` 等（与第 11 篇 §0 描述一致） |

---

## 4. 最近 14–30 天真实运行情况（逐日证据）

### 4.1 cron 执行（`jobs_history/`，最新在前）

- **daily**: 09-08~09-25 多为 `403 OpenCode-free-tier`/`400 MissingSessionID`；09-26~10-11 基本 success（10-01/10-03 `APIConnectionError`）；**10-12~10-19 `403`、10-20~10-22 `410`、10-23~10-24 `400 server_error`**；**10-25 success**（agent 模型切到 siliconflow 后恢复）。
- **periodic**: 与 daily 几乎一致（09-26~10-11 ok，10-12~10-24 全失败，10-25 ok）。

### 4.2 逐日「cron × 实际产物」

| 日期段 | cron | 有 runs | 有 events | 备注 |
|---|---|---|---|---|
| 09-25 | err | ✗ | ✗ | |
| 09-26~10-11 | ok（个别 conn err） | ✔ | ✔ | 管线质量见 4.3 |
| 10-12~10-24 | err | ✗ | ✗ | **连续 13 天无任何运行/数据** |
| 10-25 | ok | ✗ | ✗ | **daily 脚本实际未启动**（无 `_run_daily.out`）；仅 periodic 09:00 建日志目录，内容“今日无需生成周期报告” |

### 4.3 09-26~10-11 管线质量（每日 `run_manifest.json`）

| 日期 | extract LLM 成功/失败 | novelty | score | ok | sendable |
|---|---|---|---|---|---|
| 09-26 | 0/21 | new2 bg1 rep1 | P0=0 P1=0 P2=3 A=1 | F | F |
| 09-27 | 0/16 | rep3 | P2=2 A=1 | F | F |
| 09-28 | 0/15 | new1 rep3 | P2=3 A=1 | F | F |
| 09-29 | 0/17 | new1 rep3 | P2=2 A=2 | F | F |
| 09-30 | 0/19 | new1 rep4 | P2=1 A=4 | F | F |
| 10-01 | 0/22 | new1 rep3 | P2=2 A=2 | F | F |
| 10-02 | 0/21 | rep4 | P2=2 A=2 | F | F |
| 10-03 | 0/14 | new1 rep3 | P2=2 A=2 | F | F |
| 10-04 | 0/10 | rep3 | P2=1 A=2 | F | F |
| 10-05 | 0/10 | rep4 | P2=1 A=3 | F | F |
| 10-06 | 0/11 | rep3 o1 | P2=2 A=2 | F | F |
| 10-07 | 0/12 | rep4 | P2=1 A=3 | T | F |
| 10-08 | 0/12 | rep4 | P2=1 A=3 | T | F |
| 10-09 | 0/19 | new1 rep4 | P2=1 A=4 | F | F |
| 10-10 | 0/26 | rep4 | P2=1 A=3 | F | F |
| 10-11 | 0/30 | rep3 | P2=1 A=2 | F | F |

- **管线 LLM 成功率 0%（16 天全部）**：extract/analyze 每天失败 10–30 次，事件全靠规则兜底 → 因 `rule_fallback_max=P2` 等硬降级，**评分 P0=P1 恒为 0**（机制链，非偶然）。
- **新颖性**：每天 repeated 3–4/4，`new_today` 0–2 → **去重逻辑在工作，但新信号极匮乏**（根源在产新/时间未知，见 §5/§6）。

---

## 5. 采集来源有效性

- `config/sources.yaml`：`tavily_sources: []`；web_monitor 主源 10 个 enabled；RSSHub 搜索路由约 30 个 enabled。
- `source_health_report.json`（2026-10-11）：`raw87 → cleaned7/ref24/rej42`；分级 **A=1, B=9, C=3**；唯一 A 级 `rsshub_huxiu_search_store`（yield 0.5）；大量 `rsshub_36kr_*` 路由 `cleaned=0、yield=0`、仅 `keep_secondary_limit`。
- **正文可打开率（代理 `by_content_status`）**（2026-10-08，n=45）：`empty21 / thin2 / moderate6 / rich16` → 近半数空正文（抓取失败）。
- 历史阻塞清单（记录于旧审计/仓库，需 NAS 实查）：CBO化妆品财经/联商网 DNS 超时、9 个 AJAX 墙站点、Tavily 432 额度、XCrawl 超时（仅搜索端可用）。第 11 篇 §2 建议强烈借鉴 AIHOT“信源健康/官网定向栏目/动态抓取频率”，本项正好对应。
- **结论（P1→P0 上游）**：源多而有效命中低；A 级仅 1；需先解决“发现层漏报与来源分级”（对应第 11 篇 P1 阶段）。

---

## 6. 信息筛选与漏报

- 过滤漏斗：
  - `2026-10-08`: raw45 → cleaned2(4.44%)/reference29(64.44%)/rejected14(31.11%)；time `unknown27/uncertain12/in_window6`。
  - `2026-10-10`: raw109 → cleaned8(7.34%)/reference46(42.2%)/rejected55(50.46%)；time `unknown62/in_window24/uncertain23`。
- **结论**：cleaned 命中率约 4–7%、reference 池过大、时间 `unknown_time` 占一半 → “少而精”未达成；AIHOT 的 PASS/UNKNOWN/BLOCK 宽松预筛值得借鉴（防止关键行业事件被广泛关键词误杀，也减少不确定项黑洞）。

**“漏报”实测清单**：
- 日报：按既定 2026-08 起停发（只入池）——非缺陷；但系统仍每日生成播客（见 §7.3）。
- 周报：W33(08-17)失败、W34/W35(08-24/31)cron 失败、W36(09-07)覆盖度 0.29、W37/W38(09-14/21)cron 失败、W39(09-28)覆盖度 0.29、**W40(10-05)sendable=True但无音频不交付**、W41(10-12)/W42(10-19)cron 失败。→ **自 08-10 W32 起，无任何一期周报真正送达邮件。**
- 月报：2026-08（应 09-01）、2026-09（应 10-01）均缺失（`reports/monthly` 止于 07）。

---

## 7. 历史事件去重、评分、报告、音频/邮件

### 7.1 去重与新颖性
- 事件账本 `data/event_ledger/events_seen.jsonl` = **6104 行**；来源账本 `seen_urls.jsonl` = 1409 行。
- 逐日 novelty 显示 repeated 占比 75–100% → 去重机制正常，产新匮乏。符合第 11 篇“以唯一账本为权威、事件ID不复活”的方向；账本即已存在唯一权威，**不应另建第二账本**（对应 doc12 M4 的约束）。

### 7.2 评分分布
- 近 16 天全为 P2/ARCHIVE，**P0=P1=0**；根因=规则兜底被 `rule_fallback_max=P2` 封顶 + LLM 0% 成功。评分本身（权重/阈值/硬降级）与第 11 篇 §0 一致，属“程序重算、不允模型改分”的既有设计——KEEP；P0/P1=0 是 LLM 失效的连带，非评分逻辑错误。

### 7.3 播客(TTS)与邮件
- **音频**：每日播客 09-26~10-11 仍逐日生成（`podcasts/scripts|audio/`），脚本多数为 **102 字符的无信号稿** → 与“停日报、无信号不生成音频”目标**冲突**（generate_podcast 无条件执行 + no-signal 分支仍 TTS）。
- **邮件**：
  - `config/email.yaml` to=`["123399974@qq.com"]`（公开已知收件人）。
  - `.email_sent` 标记最近至 **08-02**；交付台账 `data/delivery/ledger.jsonl` 仅 2 行，最后一条 = **W32 周报 08-10 12:52 “邮件发送成功”**。
  - **W40**：`reports/weekly/2026/2026-W40.md` 仅 **1284 字节**，01–04 各节**重复同一句**，05/06 为通用模板——却 `sendable=True`（仅被“必须音频”门拦下）。→ **周期质检只查 event_id/内部标记/结构，没查“内容空洞重复”**（P0）。
  - 周期 `require_audio=true`：W40 sendable=True 但因 tts=False 不交付 → “审计通过”与“可交付”语义未分离，导致“有报告无邮件”静默。

### 7.4 历史计费（QwenPaw 会话层 token 计量，近 35 天 2026-09-20~10-25）
- 合计 **46.8M tokens / 516 次调用**。
- 按模型：`siliconflow-cn:deepseek-ai/DeepSeek-V4-Flash` **33.7M（379 次，当前 agent 模型）**；`opencode-custom:space-bunny-free` 7.16M(66)；`opencode:space-bunny-free` 5.91M(71)。
- 说明：这是 QwenPaw agent 会话层计量（含 cron 触发 agent 的思考与运维会话）；**管线 python 内 OpenCode deepseek 调用是否被本计量完全覆盖未独立验证**——第 11 篇要求只读核对 token 计量，M1 如实记录；成本与可观测治理在 M5/P3 阶段推进。

---

## 8. 系统可靠性 / 健康检查 / 告警

- **无独立健康检查 cron**（`jobs.json` 仅 2 个）；`pipeline_alert.json` 最近至 **06-04**；13 天断供期因 agent 未启动脚本，**管线级告警从未触发**。→ 健康检查/告警需挂在 cron/调度层（对应第 11 篇 §3.1“唯一权威调度”）。
- **cron 假成功**贯穿恢复日：agent 回一句“已启动”即 success，无脚本心跳/结果回读。
- Git：远程 main 已由 M0 推进至 `c4f98bd`；本审计仅在其上新增评审文档（单 commit）。

---

## 9. 现有模块 KEEP / REFINE / REMOVE / DEFER（按 AIHOT 对标，第 11 篇 §2）

| 模块 | 建议 | 依据 / AIHOT 对标 |
|---|---|---|
| 日程/调度（2 个 agent cron + 极简 prompt） | **REFINE** | 需“脚本确已启动完成”心跳 + 失败告警 + 唯一权威调度；AIHOT“确定性调度”借鉴 |
| 架构分层（agent 触发层 vs 管线执行层） | **KEEP（结构对）** | 分离合理；补成功回读 |
| 采集来源矩阵（sources/search_policy/日期门） | **REFINE** | 强烈借鉴 AIHOT“信源健康/官方可巡检栏目/动态频率”；砍 yield=0 的 36KR 路由，降 unknown_time |
| 过滤/质检（filter/funnel/source_health/reference） | **KEEP（机制在）** | 借鉴 PASS/UNKNOWN/BLOCK 预筛，避免广泛关键词误杀；reference 池过大需阈值 |
| 事件抽取/LLM 分析 | **REFINE（P0）** | 管线 LLM 0% 成功主备无效（default=fallback）；RightCode 由用户选，不代设计 |
| 去重/新颖性（events_seen 账本） | **KEEP** | 唯一账本即权威，不另建；机制正常 |
| 评分（scoring.yaml 八维+硬降级） | **KEEP** | 程序重算、P0/P1=0 为 LLM 失效连带；AIHOT“独立双评分”部分借鉴(M2 前不动) |
| 周期报告（聚合+覆盖度门禁+编辑审校） | **REFINE（P0）** | 补“内容空洞/重复”检测；分离“sendable”与“可交付(音频/邮件)”语义；degraded 显著标记而非静默 |
| 日报链路 | **KEEP（停发设计）** / REFINE：去掉无条件每日播客 | 无信号不生成音频；与既定“只入池”一致 |
| 邮件交付（HTML+仅 MP3、幂等、安全门） | **KEEP（设计对）** | 补失败重试/告警；避免静默不发 |
| 告警/健康检查 | **REFINE（P0）** | 挂 cron/调度层，断供即告警 |
| Node/PostgreSQL/网站框架（AIHOT 栈） | **DEFER** | 第 11 篇 §2“暂不照搬”；仅当 Python+NAS 实测瓶颈再评 |
| 模型路由/分时调度 | **DEFER / 用户自行** | 用户选定 RightCode「百亿补贴」；不做接入与闲时调度 |

---

## 10. 拟优化清单（P0/P1/P2，供 ChatGPT 复审后再定整改范围；本阶段未执行任何整改）

**P0（决定系统“能否跑出可用情报并真正送达”）**
1. 修复管线 LLM 主备（当前同模型兜底无效；extract/analyze 100% 失败）——接入方式由用户选（RightCode 已备），不代设计。
2. cron 增加“脚本启动/完成”校验与启动心跳；断供（连续 N 天无产物）触发告警；健康检查移到调度层。
3. 周期质检补“内容空洞/重复”检测（拦截 W40 型废稿）；分离 sendable 与可交付语义。
4. 明确 10-12~10-24 断供期数据缺口的补采集/标记策略（不硬造），并处理“cron success 但脚本没跑”的假成功。

**P1**
5. 采集源收敛 + source_health 阈值落地 + unknown_time/reference 池治理（AIHOT 发现层借鉴）。
6. 每日播客“无有效信号不生成音频”开关落地。
7. 在 LLM 恢复后补/重生成近期周报（W40~W42）与 8/9 月报；覆盖不足显著标 degraded，不做假完整。

**P2**
8. AIHOT 对标纵深（M2 冻结回归集），再评估独立双评分与 SelectBench 取舍。
9. 明文 opencode_keys 等改环境变量/Secret（历史风险，属于 M5/P3 或运维线）。
10. 成本可观测与预算上限治理（M5/P3）。

---

## 11. 证据来源清单（真实读取，未修改）

- `jobs.json`(2 任务) / `jobs_history/{daily,periodic}.json` / `agent.json`(active_model) / QwenPaw token 计量
- `data/runs/{09-26..10-11}/run_manifest.json`（逐日 extract/novelty/score）
- `data/runs/periodic/weekly/{W32,W33,W36,W39,W40}/manifest.json`
- `data/logs/2026-10-11/source_health_report.json`、`data/logs/2026-10-08|10|10/quality_funnel_report.json`
- `reports/weekly/2026/2026-W40.md`、`reports/monthly/2026/*.md`、`podcasts/{scripts,audio}/`（最近产物）
- `data/delivery/ledger.jsonl`、`data/logs/**/.email_sent`、`config/email.yaml`、`skills/send_admin_alert/send_admin_alert.py`(常量)
- `config/sources.yaml`、`config/model_router.yaml`、`config/scoring.yaml`、`config/periodic_report.yaml`
- 文档 `docs/01–08`（本仓库）、`docs/11`、`docs/12`（远程主分支经 git fetch 读取）

---

## 12. 交付与 Git 提交号

- 本评审文档为 M1 唯一交付产物；**未修改任何现有代码/任务/模型配置/账本，未发送邮件，未处理 Muse 文件**。
- 提交：在远程主分支 `c4f98bd`（含 M0 复审结论）之上新增**一个**脱敏评审 commit（见 `git log` 最近一条）。
