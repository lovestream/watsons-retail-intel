# NAS R0 — P0 故障根因与恢复计划（只读定位 + 隔离探针）

- **阶段**：R0（P0 故障定位与隔离测试）
- **执行日期**：2026-10-25（Asia/Shanghai）
- **代码基线**：`origin/main` = `fc3796bdec9fd25548ca0678252e4b3c062f271c`（本地工作副本当时停在 `2464df9`，落后 6 个提交）；本报告在基于远端 tarball 的隔离目录中编写，未覆盖任何既有复审文档。
- **结论**：五类 P0 现象均已定位到可验证的直接原因；**Python 管线未恢复，Agent 可用不等于管线可用**。本轮未修改生产代码、模型配置、Cron、事件账本、Docker 部署或邮件设置，未补采、未补发。
- **证据等级**：`已确认` = 有文件/日志/运行记录直接支持；`高置信推测` = 行为证据充分但未读对应源码；`未查明` = 现有权限/证据不足。

---

## 0. 执行边界与证据强度

**允许并实际执行**
- 只读检查：`jobs.json`、`jobs_history/`、`inbox_traces/`、`data/logs/`、`data/runs/`、`data/events/`、`reports/`、`scripts/`、`skills/`、`config/`、QwenPaw 日志与 provider 注册表（仅看元数据，不打印密钥）。
- 隔离目录 `/tmp/r0_probe/` 内用**固定短文本**调用现有 Python LLM 客户端；不写生产目录、不发邮件、不产生正式事件。
- 隔离目录 `/tmp/r0_main/` 基于 `origin/main` tarball 编写本报告。

**明确未做**
- 未改生产 Cron/调度、未改模型 Key/Provider/base_url/模型选择、未改 SMTP/收件人、未改事件账本、未重启容器、未清理脏文件、未批量补采、未补发任何历史报告、未进入 R1–R3 或 M2。

**证据强度限制（需在复审时承认）**
- 容器内无 Docker CLI/daemon，只能读取 `/proc/self/mountinfo` 证明挂载与持久化；**宿主机 Docker 的 volume/bind 配置未独立核验 → 未查明**。
- QwenPaw cron 的 `success` 判定源码未逐行审阅；本报告只依据 `jobs_history` 与 trace 行为判定（高置信）。
- `jobs_history` 每任务仅保留最近 50 条，2026-09-08～09-15 的调度记录已被挤出 → 该段**未查明**。

---

## 1. 根因矩阵（P0）

| ID | 现象 | 结论 | 直接原因 | 关键证据 | 影响 |
|---|---|---|---|---|---|
| RC-01 | 10-12～10-24 无每日采集 | 已确认 | QwenPaw Agent cron 在调用工具前即失败：10-12～19 HTTP 403 FreeTier；10-20～22 HTTP 410（`space-bunny-free` 已下线）；10-23～24 HTTP 400（模型不可用） | `jobs_history`（daily job）逐日 error；同期无 `data/logs/<date>/` | 13 天零采集、零事件、零报告，且无告警 |
| RC-02 | 10-25 cron success 但无产物 | 已确认 | Agent 未调用任何工具，直接回复“已启动”；cron 以“Agent 正常返回”判 success | 10-25 07:00 trace 仅 2 个事件（user+assistant），无 `tool_use`；无 `_run_daily.out`、无 `events/runs/drafts` | 假成功掩盖零执行；与 RC-01 叠加形成最长断供 |
| RC-03 | 16 天 extract 0 成功/275 失败 | 已确认 | Python 管线主模型 `deepseek-v4-flash-free`（OpenCode 入口）已被下线，所有入口返回 HTTP 400 “Model is unavailable” | 16 天逐日日志；全量日志该错误 36,804 次（窗口内 30,724 次）；隔离探针 4/4 入口 400 | 抽取全部退化为规则兜底，事件质量与覆盖面崩塌 |
| RC-04 | analyze 亦 0 成功 | 已确认 | 同一模型连接失败；空内容导致解析失败 | 16 天 analyze 日志 `LLM成功=0`，合计失败 26；10-11 manifest `llm_success_count=0` | 经营分析全部走规则/模板，报告空洞 |
| RC-05 | RightCode「百亿补贴」DeepSeek Flash 未生效 | 已确认（调用链）/未查明（可复用性） | RightCode 只注册在 QwenPaw provider 注册表；Python `llm_client.py` 完全不读取该注册表，付费兜底仍是另一 provider 的另一模型且默认关闭、环境变量未注入 | 代码 grep 0 命中；`/proc/*/environ` 无 `rightcode_key`；`ALLOW_PAID_FALLBACK` 默认关；provider 文件存在且模型标记可用 | 用户已付费选择的模型对管线零贡献 |
| RC-06 | W40 空洞重复仍 `sendable=True` | 已确认 | 质量门校验**清洗前**草稿：规则兜底说明行把章节长度顶过阈值；清洗后说明行被删，交付稿每节仅 61 字。质量门未对交付稿复核，且无重复/全兜底检测 | W40 预清洗稿 935 字/每节 127 字且 gate 通过；交付稿 559 字/61 字且当前 gate 判 5 节过短；manifest `sendable=true` | 废稿被标“可发”，仅靠音频缺失阻止投递 |
| RC-07 | 长期无产出却无告警 | 已确认 | 无独立看门狗；cron 无失败通知；`run_daily.sh` 丢弃退出码与 stderr；`pipeline_alert` 因 `--end-step analyze` 永不执行；告警只查“当天 manifest”，无法发现“当天根本没跑” | `scripts/run_daily.sh`；`run_daily_pipeline.py` STEP_ORDER；`pipeline_alert.py`；jobs 无 notify | 13+18 天断供静默，直到人工审计才发现 |
| RC-08 | 步骤“成功”语义失真 | 已确认 | `extract` 只要有规则兜底事件即 success（0 次 LLM 成功也 success）；`podcast` 0 字稿也 success；`send_email` 设计性 skipped | 10-11 manifest：extract success 且 `llm_failed_count=30`；podcast success 且 `script_length=102`、review `chars=0` | 健康面板全绿，故障被统计口径吞掉 |
| RC-09 | 客户端失败计数恒为 0 | 已确认 | `llm_client` 的 `total_failures` 初始化后从未自增；日志长期打印 `failures=0` | 10-11 日志 `calls=1251, failures=0` 与步骤层 30 次失败矛盾 | 可观测性误导，掩盖 RC-03 |
| RC-10 | 周期日期参数不生效 | 已确认 | `run_periodic.sh` 接受 `$1` 但调用 Python 时**不转发 `--date`**，注释还声称“无需传入” | `scripts/run_periodic.sh` 命令行 | 无法用启动器按指定日期补跑/回归（历史补发仍禁止） |

---

## 2. Q1：10-12～10-24 断供，以及 10-25 “success 但无产物”

### 2.1 调度层时间线（daily 07:00，`jobs_history`）

| 日期区间 | Agent 层结果 | 含义 | 是否产生当日日志/事件 |
|---|---|---|---|
| 09-16～09-17 | `MissingSessionID` | Agent 会话不可用 | 否 |
| 09-18～09-25 | HTTP 403 `FreeTierError` | Agent 所用免费模型配额/权限失效 | 否 |
| 09-26～10-11 | Agent 多数 success（个别 `APIConnectionError`） | 启动命令执行了，但管线 LLM 全失败（RC-03） | 有日志/事件（质量极低） |
| 10-12～10-19 | HTTP 403 `FreeTierError` | 未进入工具调用 | 否 |
| 10-20～10-22 | HTTP 410（`space-bunny-free` deprecated） | 模型下线 | 否 |
| 10-23～10-24 | HTTP 400 “Model is unavailable” | 模型不可用 | 否 |
| 10-25 | success（约 6 秒） | **未调用工具**，仅文本回复 | 否 |

> 09-08～09-15 的调度记录因 50 条上限被挤出，**未查明**；但该区间同样没有 `data/logs` 目录。

### 2.2 10-25 假成功的唯一直接证据

- trace：`inbox_traces/fd8cd29a-…`（07:00 任务）只有 2 个事件，`assistant` 内容为“已启动 2026-10-25，脚本后台运行中”，**无 `tool_use`**。
- 对照：同日 09:00 周期 trace `322aef9f-…` 有真实 shell 调用，`_run_periodic.out` 记录 `今天无需生成周期报告`、`exit=0` —— 证明工具链可用，问题是 daily 这次模型没有调用工具。
- 产物核验：`data/logs/2026-10-25/` 仅 `_run_periodic.out`；`data/events/2026-10-25`、`data/runs/2026-10-25`、`data/drafts/2026-10-25` 均不存在。

### 2.3 模型切换时间线（解释“为什么 10-25 Agent 是活的”）

QwenPaw 日志 builder 记录：
- 截至 10-24 01:00 UTC（=09:00 CST）仍为 `opencode-custom/space-bunny-free`；
- 10-24 02:23 UTC 起切换为 `siliconflow-cn/deepseek-ai/DeepSeek-V4-Flash`；
- 10-25 07:00 CST（=10-24 23:00 UTC）daily 已使用 siliconflow —— **Agent 模型可用，但未执行工具**。

**判定**：10-25 的根因不是模型不可用，而是“无强制工具调用 + 成功标准只看 Agent 回复”。属 RC-02。

---

## 3. Q2：16 天 extract 0/275、analyze 0/26；两套模型连接必须分开

### 3.1 连接归属（已确认）

| 层 | Provider | 模型 | 凭证/入口 | 本轮状态 |
|---|---|---|---|---|
| QwenPaw Agent | siliconflow-cn（10-25 起） | `deepseek-ai/DeepSeek-V4-Flash` | QwenPaw provider 注册表 | 可用；10-25 daily 正常返回但未调用工具 |
| Python Pipeline | OpenCode 入口池 | `deepseek-v4-flash-free` | 3 个 Key 池 + 无 Key 入口，`skills/utils/llm_client.py` | **全部 HTTP 400，不可用** |
| Python 付费兜底（既有） | right.codes | `gpt-5.4-xhigh` | 环境变量 `rightcode_key`；默认关闭 | 未注入环境变量，默认不启用 |
| 用户新配置 | RightCode provider | `deepseek-flash`（百亿补贴） | QwenPaw provider 注册表 | 未接 Python（RC-05） |

**因此：Agent 成功一次不代表 Python 管线成功；本轮探针也没有让管线恢复。**

### 3.2 16 天逐日统计（09-26～10-11，日志齐全 16/16 天）

| 日期 | extract 成功 | extract 失败 | analyze 成功 | analyze 失败 | Python HTTP 调用 | “Model is unavailable”计数（全步骤） |
|---|---|---|---|---|---|---|
| 2026-09-26 | 0 | 21 | 0 | 3 | 1323 | 2951 |
| 2026-09-27 | 0 | 16 | 0 | 2 | 702 | 1502 |
| 2026-09-28 | 0 | 15 | 0 | 3 | 675 | 1511 |
| 2026-09-29 | 0 | 17 | 0 | 2 | 963 | 2099 |
| 2026-09-30 | 0 | 19 | 0 | 1 | 972 | 2078 |
| 2026-10-01 | 0 | 22 | 0 | 2 | 1080 | 2354 |
| 2026-10-02 | 0 | 21 | 0 | 2 | 1161 | 2513 |
| 2026-10-03 | 0 | 14 | 0 | 2 | 936 | 2114 |
| 2026-10-04 | 0 | 10 | 0 | 1 | 864 | 1934 |
| 2026-10-05 | 0 | 10 | 0 | 1 | 432 | 208 |
| 2026-10-06 | 0 | 11 | 0 | 2 | 1026 | 2360 |
| 2026-10-07 | 0 | 12 | 0 | 1 | 567 | 1217 |
| 2026-10-08 | 0 | 12 | 0 | 1 | 621 | 1339 |
| 2026-10-09 | 0 | 19 | 0 | 1 | 855 | 1803 |
| 2026-10-10 | 0 | 26 | 0 | 1 | 1413 | 3019 |
| 2026-10-11 | 0 | 30 | 0 | 1 | 1251 | 1722 |
| **合计** | **0** | **275** | **0** | **26** | **14,841** | **30,724** |

口径：
- extract 成功/失败取 `extract_events.log` 汇总行；analyze 取 `analyze_business_impact.log` 的 `LLM成功/失败` 累加。
- HTTP 调用为客户端统计行 `available_keys=…, calls=…` 累加；**该统计不含 failures（RC-09）**。
- 400 计数为当日全部步骤日志中 `Model is unavailable` 出现次数，>275 是因为 filter 等其他 LLM 步骤同样调用同一死模型。
- 全量日志（含 09-05～09-07）共 36,804 次；窗口内 30,724 次。

### 3.3 错误分类

| 错误类型 | 窗口内证据 | 数量/比例 | 说明 |
|---|---|---|---|
| 模型不可用（HTTP 400） | `Model is unavailable` | 30,724 次；为窗口内主导且唯一可见的根错误 | 4 个入口一致，说明不是单 Key 问题 |
| 空内容导致解析失败 | extract 日志 `content长度=0`；analyze `LLM返回无法解析` | analyze 26 次失败 | RC-03 的下游结果 |
| 429 / 超时 / 网络 | 窗口日志未见该类记录 | 未发现 | 不等于不存在，取决于日志是否记录 HTTP 细节 |
| JSON 结构错误 | 无有效返回可供校验 | 无法统计 | 需 R1 用固定样本重新建分类 |

### 3.4 隔离探针（实际测试结果，不是“管线已恢复”）

- 脚本：`/tmp/r0_probe/probe_opencode.py`（固定短文本，要求仅输出一个 JSON）。
- 结果：**4/4 入口 HTTP 400 “Model is unavailable”，单次约 0.7 秒**；客户端 `content_len=0`，无 usage、无模型 ID 返回。
- 客户端计数器：`total_calls` 增长，`total_failures` 仍为 0（RC-09 复现）。
- 结论：Python 管线当前**不可能**完成 extract/analyze；这不是 Agent 层问题。
- 未用 RightCode Key 发起付费探针（避免本轮引入新凭证使用与费用）；其可用性仅有 provider 注册表“模型可用”标记，**未独立验证**。

---

## 4. Q3：RightCode「百亿补贴」DeepSeek Flash 为什么没体现为管线成功调用

### 4.1 调用链现状（已确认）

```
用户已在 QwenPaw 注册 RightCode provider（模型 deepseek-flash）
        │
        └── QwenPaw provider 注册表（仅 Agent 使用）
                 ╳  无桥接
Python skills/utils/llm_client.py
        ├── primary: OpenCode 入口池 → deepseek-v4-flash-free → HTTP 400（已死）
        └── fallback: right.codes / gpt-5.4-xhigh
                 ├── 需要环境变量 rightcode_key（当前所有进程均无）
                 └── 需要 ALLOW_PAID_FALLBACK=1（默认关闭）
```

证据：
1. 全仓（`scripts/`、`skills/`、`config/`）检索 `rightcode` / `rightapi` / `百亿` / `补贴`：**0 命中**。
2. `llm_client.py` 只读取自身常量与 `rightcode_key`，**没有 provider 注册表读取层**。
3. `/proc/*/environ` 扫描：无任何进程带 `rightcode_key`；`run_daily.sh` 不 source 任何 env 文件。
4. `config/model_router.yaml` 各技能主/备均为 `deepseek-v4-flash-free`（同模型互为备份，等于无备份）。
5. QwenPaw 侧该 provider 的模型发现报错 `'str' object has no attribute '_set_private_attributes'`，走静态模型列表（潜在 QwenPaw bug，待确认）。

### 4.2 最小适配所需（仅报告，本轮不改）

- 新增一个“provider 解析”层：优先读取现有 provider 注册表（只读，不复制 Key），映射 `deepseek-flash`；无法读取时才允许环境变量注入。
- `model_router.yaml` 需区分 `primary` 与 `fallback`，禁止 fallback=primary。
- 保留 OpenCode 作为备路，但健康检查必须先探测主路；400 属永久错误，应 fail-fast，不重试 9 次。
- 凭证读取方式：只从既有安全存储读取，日志不得输出 Key；费用字段需记录 usage。
- **待确认**：Python 是否有权只读复用 QwenPaw 注册表的同一 Key；若无，需要用户批准的独立凭证方案。

---

## 5. Q4：W40 为什么 `sendable=True`

### 5.1 事实

- 交付文件 `reports/weekly/2026/2026-W40.md`：559 字符（1284 bytes）；章节正文长度 69/61/61/61/107/62 字；02/03/04 正文完全相同。
- manifest：`sendable=true`、`delivery_allowed=false`、`tts_success=false`（`no_script`）、`podcast_review.passed=false`。
- 生成日志：6 节 LLM 全部失败（HTTP 400）→ 规则兜底；Phase 2 审稿 905 字符；**Phase 3 打印“审稿失败，跳过优化（不可发送）”**；Phase 4 仍写出报告并 `sendable=True`。

### 5.2 误判复现（隔离、只读）

- 用同一规则兜底函数重建**清洗前**草稿：935 字符，各节正文 127/127/127/127/173/128。
- 对预清洗稿跑 `rule_validate`：`sendable=True`，`high_issues=[]` —— 因为每节含内部说明行（`> 说明：本节为规则兜底生成…`），长度达标。
- 对**交付稿**跑同一 `rule_validate`：`sendable=False`，`failed_checks=['chapter_too_short']`，命中 02/03/04（61 字）、05（107 字）、06（62 字）。
- 结论：**质量门校验对象 ≠ 交付对象**。`sanitize_periodic_report` 删除内部说明行后，报告已不满足质量门自身阈值，但质量门没有再跑一次。

### 5.3 质量门还缺什么

| 缺口 | 后果 |
|---|---|
| 无重复段落/章节雷同检测 | 02/03/04 完全相同的废稿可通过 |
| 无“全部章节为规则兜底”检测 | 无任何 LLM 成功仍可 sendable |
| Phase 3 审稿失败不传导 | 日志写“不可发送”，字段仍 true |
| `sendable` 与音频/投递混用 | 内容状态被误读为可投递；实际 delivery_allowed=false |
| 无最小信息量/来源多样性门槛 | 一篇文章重复三遍也算“三大趋势” |

**建议语义拆分（R3）**：`content_quality_pass`（内容门）、`audio_ready`（音频门）、`delivery_allowed`（投递门，必须前两者同时为真 + 去重 + 覆盖率达标）。当前 manifest 只有 `sendable/delivery_allowed`，缺 `content_quality_pass` 显式字段。

---

## 6. Q5：为什么长期没有可靠告警

1. **没有独立看门狗**：仅 2 个 cron（daily 07:00、periodic 09:00），无“产物存在性/新鲜度”检查任务；job 无 notify 配置，`jobs_history` 的 error 无人消费。
2. **启动器丢弃证据**：`run_daily.sh` 用 `set -e` + `setsid … >/dev/null 2>&1 </dev/null`，退出码和 stderr 不落盘，无 status/heartbeat 文件；10-11 日志无完成行，manifest `ok=false`，但没有任何消费者。
3. **告警步骤被结构性跳过**：`pipeline_alert` 位于 `STEP_ORDER` 末尾，而 `run_daily.sh` 固定 `--end-step analyze` ⇒ **该步骤从未执行**。
4. **告警模型只能查“已发生的运行”**：`pipeline_alert` 基于当天 `run_manifest.json`；**当天根本没跑就没有 manifest，也就没有任何告警**，无法发现 13/18 天静默。
5. **收件人默认值不是用户**：`pipeline_alert` 的管理员地址来自环境变量，未设置时使用一个非用户默认地址；即使执行也可能发错人。
6. **假绿灯语义**（RC-08）：extract 0 次 LLM 成功仍 success；podcast 0 字稿仍 success；`tophub_collect` 失败只让 `ok=false` 且被归为补充步骤不告警；`send_email` 设计性 skipped。
7. **周期侧同样静默**：W39 failed、W40 内容假阳性、W41～W43 无运行；没有任何一条告警进入用户邮箱。

---

## 7. 生产状态、持久化与回滚可行性

- **挂载/持久化**：`/app/working` 与 `/app/working.secret` 均位于 NAS ext4 卷（`/dev/mapper/cachedev_0`），读写挂载；生产数据不在容器临时层，容器重启不会丢失（与既往“重启丢 SSH Key”不同层面，SSH Key 在 `/root`，不在该卷）。
- **宿主 Docker**：容器内无 docker CLI/daemon，**bind mount/volume 的宿主侧配置未独立核验 → 未查明**。
- **生产工作树 dirty**：本地 `2464df9` 上有 8 个已跟踪文件修改（含事件账本、投递台账、W32 合成文件）与 50 个未跟踪项（备份、evergreen 等）。**未执行 reset/clean/stash，未触碰**。
- **远端更新**：`origin/main` 已到 `fc3796bd`（比本地多 6 个提交：M1 复审文档、邮箱脱敏、docs/11/12 状态更新）。本轮报告在基于远端 tarball 的隔离目录编写，未覆盖 `NAS-M1-CHATGPT-REVIEW-2026-10-25.md` 或 `NAS-M1-READONLY-AUDIT.md`。
- **回滚**：本轮无生产改动，无需回滚；报告本身为新增文件，回滚=删除该文件（或 revert 单个提交），不影响运行。

---

## 8. 覆盖缺口与可合法核验范围（不补采）

| 区间 | 日志 | 事件 | 运行 manifest | 可核验内容 | 补采 |
|---|---|---|---|---|---|
| 2026-09-08～09-25 | 无 | 无 | 无 | 只能核验“缺失”；调度记录部分被 50 条上限挤出 | **不做**；需重新采集授权 |
| 2026-09-26～10-11 | 有（16 天） | 有（质量极低） | 有 | 可完整复算 LLM 失败、步骤终态、事件数 | 不做 |
| 2026-10-12～10-24 | 无 | 无 | 无 | 只能核验“缺失” | **不做** |
| 2026-10-25 | 仅周期日志 | 无 | 无 | 只能核验“未启动” | **不做** |
| 周报 W34～W38 | — | — | 无 | 缺失 | 不做 |
| 周报 W39 | — | — | 有（failed） | 可读失败原因 | 不做 |
| 周报 W40 | — | — | 有（内容假阳性、无音频） | 可复现误判 | 不补发 |

任何“补齐 13/18 天”的动作都超出 R0，且需要新的采集授权与来源可用性验证；本报告只登记 `coverage_gap`。

---

## 9. R1–R3 最小整改建议（本轮不执行）

> 原则：只修已证实阻断正确运行的能力；每项独立提交、可单独回滚；先影子/干跑，不直接改生产投递。

### R1｜恢复 Python 事件抽取与分析链路（模型连接 + 失败语义）

- **修改文件（建议）**
  - `skills/utils/llm_client.py`：provider 解析层；主/备分离；400 永久错误 fail-fast；修 `total_failures`；记录 model/usage/latency。
  - `config/model_router.yaml`：`primary=RightCode deepseek-flash`、`fallback=OpenCode`（或按复审决定），禁止同模型互备。
  - `skills/extract_events/extract_events.py`、`skills/analyze_business_impact/analyze_business_impact.py`：0 次 LLM 成功时状态降为 `degraded`，不得 success；保留 rule_fallback。
  - `skills/health_check/health_check.py`：固定短文本探针，400/空内容判失败。
- **风险**：付费调用成本；Key 读取边界；新 provider 兼容性；重试策略变更导致长尾延迟。
- **测试标准**：≥20 条冻结真实/脱敏样本；结构化 JSON 成功率 ≥18/20；每条记录真实 model_id、usage、latency；0 次“静默空内容”；extract→analyze→score 全链路在隔离目录跑通；单日费用上限可见。
- **回滚**：`LLM_PROVIDER_MODE=opencode` 一键回旧路（保留但不默认）；单提交 revert；不触碰账本。
- **前置确认**：Python 是否能只读复用 QwenPaw provider 的 Key。

### R2｜执行可验证化与告警（调度层）

- **修改文件（建议）**
  - `scripts/run_daily.sh`、`scripts/run_periodic.sh`：写 `data/status/<date>.json`（pid/started/exit/artifacts）；`tee` 日志；去掉关键路径静默退出；周期脚本转发 `--date`。
  - 新增 `scripts/watchdog_daily.sh`（07:40/08:10 检查当日 events/log/manifest，缺失即告警；带状态文件去重）。
  - `skills/run_daily_pipeline/run_daily_pipeline.py`：`--end-step analyze` 时不再强制跑 podcast（避免继续烧死模型）；或显式 `--skip-podcast`。
  - `skills/health_check/pipeline_alert.py`：可被 launcher 无条件调用；新增“无 manifest/连续 N 天无事件”；收件人可配。
- **风险**：重复告警、误报、cron 变更需用户批准；watchdog 自身失败。
- **测试标准**：模拟“未启动”“启动即失败”“0 事件”“0 LLM 成功”四类，均触发一次且仅一次告警；健康日不告警；退出码/status 文件与产物一致；干跑不发邮件。
- **回滚**：watchdog 任务停用即恢复原状；启动器改动独立 commit revert；不改 Cron 结构直至批准。

### R3｜周期报告质量门与三态投递

- **修改文件（建议）**
  - `skills/periodic_editor_review/periodic_editor_review.py`：在 **sanitize 后**再跑一次门；新增重复段落/章节雷同、全章规则兜底检测。
  - `skills/generate_periodic_report/generate_periodic_report.py`：Phase 3 失败 → `content_quality_pass=false`；输出三态字段；`sendable` 仅作兼容别名。
  - `skills/utils/report_sanitizer.py`：清洗不得删掉质量门依赖的“必要长度”，或质量门必须在清洗后执行（二者取一）。
  - `skills/run_periodic_pipeline/run_periodic_pipeline.py`：投递前要求 `content_quality_pass && audio_ready && coverage_ok && 未重复投递`。
- **风险**：历史可发报告被新门拦截；音频生成失败导致周期性不投递；规则阈值误杀。
- **测试标准**：W40 必须 `content_quality_pass=false`；W31/W32 等合格样本仍通过；重复章节样本被拦截；三态字段与实际邮件/音频一致；W40 回归脚本可重复。
- **回滚**：`PERIODIC_STRICT_GATE=0` 关闭新规则；单提交 revert；不补发历史。

---

## 10. 分层验收阈值（给复审用）

| 阶段 | 最低通过标准 |
|---|---|
| R0（本轮） | 五问均有文件级证据；Agent/Pipeline 连接分开；W40 误判可复现；无生产改动；报告提交并可读回 |
| R1 | ≥20 样本中 ≥18 条结构化 JSON 成功；model_id/usage/latency 可见；0 次静默空内容；extract→analyze→score 连通；费用上限生效；失败时有明确错误分类 |
| R2 | 四类故障注入均告警且仅一次；健康日零误报；status/exit 与产物一致；watchdog 连续 7 天影子运行无漏报 |
| R3 | W40 被拦；W31/W32 不误杀；三态一致；重复内容检测命中；投递去重；周期日期参数可用 |

---

## 11. 失败复现方法（脱敏、只读）

1. **调度断供**
   - 读 `jobs_history` 中 daily job，按 `run_at` 打印 status/error 类别；对照 `data/logs/<date>`、`data/events/<date>` 是否存在。
   - 预期：10-12～10-24 只有 error，无产物。
2. **10-25 假成功**
   - 读 `inbox_traces/fd8cd29a-…` 的 events，统计 `tool_use` 数量。
   - 预期：0；assistant 文本声称已启动。
3. **管线模型失败**
   - 在隔离目录运行 `probe_opencode.py`（固定短文本）。
   - 预期：4/4 HTTP 400；`content_len=0`；`total_failures=0`。
4. **16 天统计**
   - 用 `agg_llm.py` 读取各日 `extract_events.log` / `analyze_business_impact.log` 汇总行。
   - 预期：0/275、0/26、HTTP 14,841。
5. **W40 误判**
   - 用规则兜底函数重建预清洗稿 → `rule_validate` 通过；对交付稿 → `chapter_too_short` 失败。
   - 预期：同一内容，两种结论。
6. **告警缺失**
   - 检查 `STEP_ORDER` 与 `run_daily.sh` 的 `--end-step analyze`；检查 `pipeline_alert` 是否只在主循环执行；检查 job notify 配置。
   - 预期：告警步骤永不执行；无独立看门狗。

---

## 12. 待确认事项（复审裁定）

1. 09-08～09-15 的调度结果（历史被 50 条上限挤出）；09-08～09-25 是否与 10-12～10-24 同等计入 P0 断供。
2. QwenPaw cron 的 `success` 判定是否仅等于“Agent 无异常完成”（需读 `crons/manager.py`）。
3. Python 是否可只读复用 QwenPaw provider 注册表的 RightCode Key；若不可，采用何种凭证边界。
4. RightCode provider 模型发现异常（`'str' object …`）是否需上报 QwenPaw。
5. 每日是否仍维持“只入池、不发日报”（当前 `reports/daily` 为空是否为预期）。
6. W39/W40/W41～W43 的处置策略（本轮禁补发）。
7. 告警收件人统一为哪个地址（报告中不写个人邮箱）。
8. 宿主机 Docker 的 volume/bind 与宿主 cron 是否还有第二套调度（容器内不可见）。

---

## 13. 本轮明确未做（合规声明）

- 未改生产 Cron、模型配置/Key/Provider/base_url、事件账本、Docker 部署、SMTP/收件人。
- 未重启容器、未清理脏文件、未 stash/reset/clean、未强制推送。
- 未批量补采、未补跑 13/18 天、未补发任何日报/周报/月报、未发送测试邮件。
- 未进入 R1、R2、R3 或 M2。

---

## 附录 A：证据文件（脱敏路径）

- 调度与历史：`jobs.json`、`jobs_history/`、`inbox_traces/fd8cd29a-…`、`inbox_traces/322aef9f-…`
- 运行产物：`data/logs/<date>/`、`data/runs/<date>/run_manifest.json`、`data/events/<date>/`
- 管线代码：`skills/utils/llm_client.py`、`skills/run_daily_pipeline/run_daily_pipeline.py`、`skills/health_check/pipeline_alert.py`、`scripts/run_daily.sh`、`scripts/run_periodic.sh`
- 周期：`data/runs/periodic/weekly/2026-W40/manifest.json`、`reports/weekly/2026/2026-W40.md`、`data/logs/2026-10-05/_run_periodic.out`
- Agent 模型：QwenPaw 运行日志 builder 行（仅模型/provider 名，不含密钥）
- Provider 元数据：`/app/working.secret/providers/custom/…`（仅模型名与可用标记，未读取/输出 Key）

## 附录 B：统计口径

- 日志缺失即视为“未运行”，不以“无新信号”解释缺失（无信号日仍应产生日志与 manifest）。
- extract 失败为文章级失败计数；analyze 失败为事件级失败计数；两者分母不同，不可相加成单一“失败率”。
- 400 计数为跨步骤文本出现次数，仅用于证明错误类型与量级，不作为调用成功率分母。
- 所有数值均来自 2026-10-25 现场只读统计；未使用任何推测数字填充。
