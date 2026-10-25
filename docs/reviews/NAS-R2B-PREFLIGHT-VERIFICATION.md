# NAS R2-B｜上线前准备与隔离验证报告

日期：2026-10-25。项目：lovestream/watsons-retail-intel。

> **状态：仅完成隔离代码、测试与只读预检；未获生产切换许可。**
>
> 本分支没有合并 `main`，没有安装生产 supervisor，没有启停 QwenPaw Agent Cron，没有修改正式事件账本、SMTP/投递配置或模型 Key，没有发送日报/周报邮件，也没有发送真实告警。

## 1. 范围与结论

本轮按 `NAS-R2A-FIX-CHATGPT-REVIEW-2026-10-25.md` 的 R2-B 门禁执行：

1. 修复 B1：文章 `published_at` 被临时填充为事件日期后，可能仅凭文章发布时间通过 `verified`。
2. 修复 B2：采集步骤 skipped/failed、但旧 cleaned/events 文件存在时，仍可能被判成功。
3. 只读核实 NAS Docker 挂载、持久化目录、当前 Agent Cron、supervisor 可用性和模型凭证引用方式。
4. 设计新旧调度互斥、独立告警、异常恢复和一键回滚方案。
5. 保留 SiliconFlow 主模型、RightCode「百亿补贴」备用模型；不开发费用台账、日金额预算或估价器。
6. 明确周期报告的后续 R3 质量门：当前 `verification_status=verified` 只是规则校验，不等于人工事实审校。

**结论：B1/B2 的隔离修复和 53 项测试已通过；生产上线仍被 supervisor 控制面、Cron 切换授权、真实告警通道、生产工作区状态和 main 合并授权阻断。不得直接切换生产。**

## 2. 分支与证据边界

| 项 | 值 |
|---|---|
| 最新 `main`（只读） | `9324ba713d8bd11ff59f06e10cf8d6bcc1e7010d` |
| R2-A-FIX 基线 | `443b68a7ae937e6e2143f63e14a93e950eeaad89`（`r2a/shadow-scheduler`） |
| 本报告分支基线 | `r2b/preflight`，从 `443b68a` 派生 |
| `main...r2a/shadow-scheduler` | ahead 6 / behind 12（GitHub Compare） |
| 生产工作副本 | `/app/working/projects/watsons-retail-intel`，`HEAD=2464df9`，58 个未提交变更 |

说明：

- R2-A-FIX 的代码在 `r2a/shadow-scheduler`，最新 `main` 没有 `ops/r2a/` 和 `skills/report_policy.py` 的新实现。因此本轮没有把 `main` 直接当代码基线，而是以 `r2a` 的已复审代码为父提交，另建 `r2b/preflight` 隔离分支；`main` 仅用于对照和审阅文档。
- `main` 与本分支已经分叉，禁止 force push，禁止假设快进合并。
- 生产工作副本 2464df9 与隔离分支不是同一代代码，且有 58 个未提交文件；生产合并/部署前必须先做人工 diff 和备份，不能直接覆盖工作区。

## 3. B1：文章发布日期不再单独证明事件已核验

### 3.1 根因路径

`skills/temporal_fact_validation/temporal_fact_validation.py` 在事件没有 `event_announced_at` 时，会把文章 `published_at` 临时填入 `announced_dt`，并把 `event_time_source` 设为 `published_at`。

`skills/report_policy.py::evaluate_verification()` 原先会对 `published_fallback`、`collected_fallback` 和 `inferred_content_date` 加“不明确”原因，却没有对 `published_at` 加原因；因此可能出现“文章 10 月 10 日发布，事件本身没有发生/公告日期，但被当作 10 月 10 日宣布并 verified”的边界。

### 3.2 最小修复

- 在 `report_policy.py` 增加：
  - `date_source == "published_at"` → `needs_verification`
  - 原因标记：`event_date_only_article_publication`
- 保留合法正例：
  - 抽取器明确给出 `event_announced_at`，且来源能证明是公告原文日期 → 仍可 `verified`。
  - 正式规则/公告明确日期，即使文章晚几天发布 → 仍可 `verified`。
- 没有把“官方发布即宣布”一刀切误杀；只有 fallback 到文章发布时间的路径被降级。

### 3.3 新增测试

`tests/r2a/test_r2a_fix.py`：

- `test_article_publication_is_not_an_event_date`
  - 仅有文章发布时间 → 不通过，原因包含 `event_date_only_article_publication`。
- `test_explicit_announcement_date_survives_later_article`
  - 明确公告日期 + 更晚文章发布 → 通过，`verification_date_source=announced`。

### 3.4 冻结窗口对照

固定窗口：2026-10-11，7 篇 cleaned → 50 条 raw/temporal/scored 事件。分别在 R2-A-FIX 代码与本分支代码上从 `temporal_fact_validation` 跑到 `score`，不调用 LLM、不发邮件、不写正式数据。

| 指标 | 修前 | 修后 |
|---|---:|---:|
| `verified` | 34 | 32 |
| `needs_verification` | 16 | 18 |
| `verification_date_source=published_at` | 2 | 2 |
| 最终 reportable | 5 | 5 |

被降级的两条：

- `E20261011_0028`：`verified` → `needs_verification`，来源 `published_at`
- `E20261011_0029`：`verified` → `needs_verification`，来源 `published_at`

最终仍可报告的 5 条：

`E20261011_0023`、`E20261011_0024`、`E20261011_0026`、`E20261011_0035`、`E20261011_0036`

原因：这 5 条都有显式抽取的 `announced` 日期，不是文章发布时间 fallback；两条 `published_at` 事件原本就是 ARCHIVE，不在最终 5 条内。因此没有因为 B1 修复减少真实可报告新事件。

完整机器可读证据：`docs/reviews/artifacts/NAS-R2B-B1-frozen-window.json`。

## 4. B2：采集未真正完成时不得判成功

### 4.1 修复内容

采集器 `skills/collect_daily_articles/collect_daily_articles.py` 在正常完成来源循环并写入产物后，返回：

- `collection_completed: true`
- `collection_date: <本次采集日期>`

失败/提前返回路径不写这两个字段。`skills/run_daily_pipeline/run_daily_pipeline.py` 将字段带入 collect 步骤统计。

`ops/r2a/launcher.py` 新增 scheduled 终态证明 `_scheduled_collection_proof()`，要求：

1. `collect` 步骤存在且 `status=success`；
2. `collection_health=ok`；
3. `collection_completed is True`；
4. `collection_date` 存在且等于本次目标日期；
5. `source_total > 0`；
6. 不是“全部来源失败/无可检查来源”。

同时修正 `classify_outcome()`：

- scheduled 模式无论从 `health_check` 还是 `collect` 启动，`required_critical` 都包含 `collect`；
- `collect=skipped`、`collect=failed`、旧 cleaned/events 存在，均不能进入 success；
- 合法空窗（来源确实检查过、当天 0 篇）仍可返回 `no_signal`；
- 没有采集完成凭据时，不伪造 `no_signal`。

### 4.2 新增测试

`tests/r2a/test_r2a.py`：

- `test_scheduled_requires_owned_collection_even_with_cached_pool`
  - collect skipped + 旧 cleaned/events 计数正常 → `collection_not_verified`，exit 2。
- `test_scheduled_collect_failure_with_old_events_is_not_success`
  - collect failed + 旧事件池 → failed，exit 2。
- `test_scheduled_no_checkable_sources_is_failed`
  - `source_total=0` → failed，`collection_health=no_sources`。
- `test_scheduled_collection_window_mismatch_is_failed`
  - 采集日期与目标日期不一致 → failed，错误含 `collection_date_mismatch`。
- `test_scheduled_verified_empty_day_is_no_signal`
  - 采集真实完成、来源检查过、0 篇 → `no_signal`，`collection_proof=verified`。
- Launcher 子进程：
  - scheduled 完整运行写入 `collection_proof=verified`；
  - scheduled collect failure + 缓存池 → 失败。

### 4.3 证明强度与残留限制

当前证明是“本次 launcher 步骤 + 采集器完成标记 + 采集日期 + 来源计数”，不是 collector 独立 UUID。生产流水线目前没有把 launcher `run_id` 传给采集器；R2-B 不为此扩大改造范围。该证明已经足以排除“仅凭旧文件 mtime/旧 cleaned 目录判成功”，但不宣称它是完整产物级 run_id 追踪。后续如接入独立采集 runner，应补 `collection_run_id`。

## 5. LLM 健康度：保留分母

`ops/r2a/launcher.py` 新增 `_llm_stats()`：

- 读取 `llm_success_count` 和 `llm_failed_count`；
- 计算 `attempts = success + failed`；
- 计算 `success_ratio`；
- `success == 0` 或 `ratio < 0.8` → `degraded`；
- manifest 增加 `llm_extract_attempts`、`llm_extract_success_ratio`、`llm_analyze_attempts`、`llm_analyze_success_ratio`。

新增测试：`1 成功 / 49 失败` → `degraded`，`attempts=50`，`success_ratio=0.02`。这修复了“1/50 成功仍显示 ok”的掩盖风险。

没有新增计费台账、价格估算或日金额预算；只保留运行健康所需调用次数与错误类型。

## 6. NAS 只读预检

以下均为只读检查，未修改任何生产文件或服务。

### 6.1 Docker / 挂载 / 持久化

容器内没有 `/var/run/docker.sock`，无法进行 `docker inspect`；以下结论来自 `/proc/1/mountinfo` 和容器内文件系统，不能替代 NAS 宿主机侧 Docker API 审计。

| 容器路径 | 宿主机卷来源 | 模式 |
|---|---|---|
| `/app/working` | `qwenpaw-data`（`/@docker/volumes/qwenpaw-data/_data`） | rw |
| `/app/working.secret` | `qwenpaw-secrets`（`/@docker/volumes/qwenpaw-secrets/_data`） | rw |

- 生产项目：`/app/working/projects/watsons-retail-intel`
- 数据目录：`/app/working/projects/watsons-retail-intel/data`，约 1.8G
- 报告目录：约 1.9M
- `/app/working` 是持久卷；容器重启后项目与 `data` 保留。
- 但 **R2-B scheduler/watchdog 不会因容器重启自动恢复**，除非写入 PID1 supervisor 配置；当前生产 supervisor 中没有这两个程序。

### 6.2 当前 Agent Cron

| 名称 | ID | 时间 | 类型 | timeout | 状态 |
|---|---|---|---|---:|---|
| `watsons-daily-0700` | `a892335a-4c55-4bce-8e57-6be07aa1764a` | `0 7 * * *` Asia/Shanghai | agent | 120s | enabled |
| `watsons-periodic-0900` | `7c4eb73d-6a9d-402a-81ff-0ffd88383651` | `0 9 * * *` Asia/Shanghai | agent | 120s | enabled |

旧启动脚本仍存在且可执行：

- `scripts/run_daily.sh`（2266 bytes）
- `scripts/run_periodic.sh`（1781 bytes）

当前两个 Agent Cron 会唤醒 QwenPaw agent，再启动旧脚本。它们与新的 Python scheduler 不是同一入口，不能假设 R2-A 的日期锁能约束旧脚本。

### 6.3 Supervisor

- 已安装 `supervisord 4.2.5`，并且是容器 PID1：
  - `/usr/bin/python3 /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf`
- **实际配置没有 `[unix_http_server]` / `[supervisorctl]` 段。**
- `supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status` 返回：
  - `Error: .ini file does not include supervisorctl section`
- `/var/run/supervisor.sock` 不存在。

因此：

- supervisor 具备进程托管能力，但当前没有可用的控制面；
- 不能直接 `supervisorctl reread/update`；
- 新增 R2-B 程序需要修改 PID1 supervisor 配置并做受控重启，或采用独立 supervisor 进程；两者都必须单独授权，不能在 R2-B 预检中执行。
- 修改 PID1 配置会带来 QwenPaw 主进程/桌面服务重启风险，不能当作“普通新增 program”。

### 6.4 模型凭证引用

当前进程环境变量名（只核实名称，未打印值）：

- `siliconflow_api_key`
- `rightcode_bybt`
- `email_key`

凭证文件：

- `/app/working.secret/` 权限 `600`
- `envs.json` 与 `providers/custom/*.json` 为明文 JSON（文件权限保护）
- QwenPaw provider registry 与报告管线是两套配置：QwenPaw 当前 active provider 是 OpenCode 相关免费模型；报告管线继续使用 SiliconFlow 主 + RightCode 备用。

生产 supervisor 只能引用变量名/0600 文件，不能把 Key 写进 supervisor 配置、日志、仓库或报告。建议在 supervisor program 的 `environment=` 中使用：

- `PIPELINE_LLM_API_KEY="%(ENV_siliconflow_api_key)s"`
- `PIPELINE_LLM_FALLBACK_KEY="%(ENV_rightcode_bybt)s"`

但该映射尚未在 PID1 环境中验证，属于部署前检查项；本轮未写入、未重启。

## 7. 生产切换方案草案（未授权，未执行）

### 7.1 强制顺序：先停旧，再启新

不得让旧 Agent Cron 与新 scheduler 同时运行。R2-A 日期锁只约束新 launcher，旧 shell 脚本不共享该锁。

建议顺序：

1. **冻结窗口**：确认当天旧日报/周期任务未在运行；备份生产 `data/ops`、Cron spec、supervisor 配置和项目工作区 diff。
2. **暂停旧 Cron**（需用户明确批准）：
   - `qwenpaw cron pause a892335a-4c55-4bce-8e57-6be07aa1764a --agent-id watsonmainagent`
   - `qwenpaw cron pause 7c4eb73d-6a9d-402a-81ff-0ffd88383651 --agent-id watsonmainagent`
3. **确认无双跑**：检查 `ps` 中没有旧 `run_daily.sh` / `run_periodic.sh` / 旧 pipeline 进程；若旧任务仍在跑，等待或经批准终止，不能直接启动新任务。
4. **部署候选代码**：在隔离分支验收后，按人工 diff 合并到生产代码；先备份工作区，不使用 `reset --hard` / `clean`。
5. **写入 supervisor 配置但 `autostart=false`**：先只做配置语法检查，不启动。
6. **一次性验证**：用隔离 state/date 或 `--dry-run` 验证 scheduler 选择窗口、启动 pipeline、日期锁、watchdog；不写正式账本。
7. **批准后启动**：先启动 scheduler/watchdog，观察首轮 manifest、`collection_proof`、watchdog `status.json`；确认后再保持旧 Cron 暂停。
8. **验收**：至少验证一个完整调度窗口、一次失败注入、一次恢复；才允许进入长期运行。

### 7.2 互斥与恢复

- 新旧互斥依赖“旧 Cron 已暂停 + 无旧进程 + 新 scheduler 独占启动”，不能依赖文件 mtime。
- 新 scheduler 自身用日期锁；同一日期已有 running 时跳过。
- 失败后默认 `existing_failed_no_retry`，需人工用 `--retry-failed`（或明确批准的重试策略）恢复，避免自动重复烧钱。
- 容器重启后：
  - 若 R2-B 未加入 PID1 supervisor，调度器不会自动恢复；
  - 加入后必须确认 `autorestart`、`startsecs`、日志路径和 PID1 重新加载方式。
- 生产工作副本当前有 58 个未提交文件；部署前必须逐项 diff，不能把隔离分支直接覆盖。

### 7.3 一键回滚清单

出现 scheduler/watchdog 异常、双跑、首轮采集失败或用户要求停止时：

1. 停止新 scheduler/watchdog（通过已授权的 supervisorctl；当前控制面不可用，需先解决）。
2. 将新 supervisor program 设为 `autostart=false` 或移除配置。
3. 恢复旧 Cron：
   - `qwenpaw cron resume a892335a-4c55-4bce-8e57-6be07aa1764a --agent-id watsonmainagent`
   - `qwenpaw cron resume 7c4eb73d-6a9d-402a-81ff-0ffd88383651 --agent-id watsonmainagent`
4. 确认旧 Cron `enabled=true` 且旧脚本存在。
5. 保留 R2-B 的 `data/ops` 与新日志供审计，不删除证据。
6. 回滚后检查是否已有当天旧任务运行，避免恢复瞬间与新进程重叠。

**当前 blocker：没有 supervisor 控制 socket，无法做到真正一键停止/启动。** 上线前必须先授权并验证 supervisor 控制面改造，否则只能人工信号/重启容器，不能称为可靠回滚。

## 8. 独立告警与异常恢复

### 8.1 当前状态

R2-A watchdog 只是本地告警 sink：

- `data/ops/watchdog/status.json`
- `data/ops/alerts/events.jsonl`
- `data/ops/alerts/current.json`
- 失败时保留 exit code；恢复时写 recovery。

它**不等于真正送达用户**。本轮没有发送真实告警邮件，也没有配置外部通知。

### 8.2 R2-B 告警门

生产启用前必须满足：

1. 明确授权一个独立通知通道（不能默认复用报告邮件）。
2. 告警去重与恢复事件可审计。
3. “未启动/心跳过期/自身故障”可被外部检测：
   - supervisor autorestart 只能覆盖进程退出；
   - watchdog 自身死掉需要第二个观察者或外部 dead-man's switch；
   - 外部服务账号/URL 未获授权，当前不能配置。
4. 首次启用前做一次测试接收并取得用户确认。
5. 失败告警不能以 `|| true` 吞掉退出码。

在通知通道未授权前，R2-B 只能报告“本地告警记录正常”，不能宣称“用户会收到告警”。

### 8.3 异常恢复

- collect skipped/failed → `collection_not_verified`，不生成“无新闻”假成功。
- 全来源失败/无可检查来源 → failed，不进入 no_signal。
- LLM 成功分母不足 → degraded warning，不声称全 LLM 成功。
- scheduler 已有 failed → 默认不自动重试；人工确认后使用 `--retry-failed`。
- watchdog 记录 recovery 后，仍需人工核对当天 manifest 和采集窗口。

## 9. 周期报告 R3 质量门需求

### 9.1 不能混淆的两个概念

`verification_status=verified` 当前只表示：

- 事件有可解析日期；
- 日期窗口/未来日期/传闻等确定性规则通过；
- 有 fact/evidence/source_url；
- 能通过 `report_policy` 的准入规则。

它**不表示**：

- 事实已被独立来源证实；
- 分析建议正确；
- 人工审校通过；
- 周/月/年报可以无条件使用。

### 9.2 建议字段

R3 应为事件与报告增加明确状态，而不是复用 `verified`：

| 字段 | 建议值 | 含义 |
|---|---|---|
| `verification_status` | `verified / needs_verification / rejected` | 当前规则级日期/证据门 |
| `fact_review_status` | `unreviewed / machine_checked / human_verified / rejected` | 事实复核级别 |
| `fact_review_sources` | URL 列表 | 独立来源/原始公告 |
| `fact_reviewed_at` | ISO 时间 | 复核时间 |
| `fact_reviewed_by` | 人或流程 ID | 责任链 |
| `analysis_review_status` | `unreviewed / reviewed / rejected` | 业务分析审校 |
| `report_quality_status` | `draft / machine_pass / human_pass / blocked` | 报告最终质量状态 |

### 9.3 周期报告准入

R3 周/月/年报至少要求：

1. 只从正式报告/正式事件账本读取本期事件，不直接读取原始中间文件。
2. `report_eligibility=core`。
3. `verification_status=verified`。
4. `fact_review_status` 至少 `machine_checked`；P0/P1 或高影响建议要求 `human_verified`。
5. `analysis_review_status=reviewed` 才能进入最终稿。
6. `review`、`reference`、`needs_verification` 不能因为 P1 评分被周期聚合“捞回”。
7. 最终清洗/去标记后再做一次质量门，避免 W40 式“预清洗稿过门、交付稿失败”。
8. TTS、HTML 邮件、附件和发送幂等分别验证；音频/邮件失败不能回写成报告成功。

R2-B 只提交该需求，不在本轮实现 R3 状态机，也不把当前规则校验冒充人工事实审校。

## 10. 验证命令与结果

隔离测试：

```bash
cd /tmp/r2b_wt
PYTHONPATH=. /app/venv/bin/python3 -m unittest discover -s tests/r2a -v
```

结果：`Ran 53 tests ... OK`。

边界验证：

- 未调用真实付费 LLM；
- 未执行真实采集；
- 未发送邮件；
- 未写正式 `data/events` 账本；
- 未修改 QwenPaw Cron；
- 未安装/启动 supervisor；
- 未修改 `/app/working.secret`。

## 11. 授权矩阵与剩余阻断

| 事项 | 当前状态 | 需要 |
|---|---|---|
| 合并 `main` | 未做 | 用户/ChatGPT 复审后明确批准 |
| 生产代码切换 | 未做 | 人工 diff、备份、批准 |
| 安装 R2-B supervisor | 未做 | 修改 PID1 配置/控制面并批准重启 |
| 暂停旧 Cron | 未做 | 明确批准 |
| 启动新 scheduler | 未做 | 旧 Cron 已停、无旧进程、控制面可用 |
| 真实告警通知 | 未做 | 授权独立通道并测试接收 |
| 外部 dead-man's switch | 未做 | 外部账号/URL 授权 |
| 模型/Key 变更 | 未做 | 不需要；保持现状 |
| 费用台账/预算 | 未做 | 按用户要求不做 |
| R3 周期质量门 | 仅需求 | 后续单独实现和审阅 |

## 12. 停止点

本报告和隔离分支提交后停止，等待 ChatGPT 复审。

在复审通过且用户明确批准生产切换之前，不得：

- 合并 `main`；
- 安装生产 supervisor；
- 启停原 Agent Cron；
- 修改正式事件账本、SMTP、投递配置或模型 Key；
- 发送邮件或真实告警；
- 宣称生产已恢复。
