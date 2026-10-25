# NAS R2-A：日管线隔离验证与调度可靠性整改

- 日期：2026-10-25
- 基线：R1-FIX（远端分支 `r1/pipeline-deepseek-flash-recovery`，head `b58dea6dc9dd46de57147c7c297bad589e0a3846`）
- R2-A 分支：`r2a/shadow-scheduler`（从 R1 分支 head 建立，未合并到 `main`）
- 复审文档：GitHub `main` 的 `docs/reviews/NAS-R1-FIX-CHATGPT-REVIEW-2026-10-25.md`（复审通过）
- 生产影响：**无**。未改正式 Cron、事件账本、Docker、SMTP；未发送任何日报/周报/MP3/邮件
- 结论：影子链路可完整运行，但 `needs_verification` 尚未接入生产链路；调度器与 watchdog 已完成可执行的隔离实现，尚未安装到生产 supervisor

> 本文只把“隔离影子测试通过”和“生产管线通过”分开陈述。影子测试不能代替生产验收。

## 1. 执行边界

本轮遵守以下约束：

- 使用 R1 分支已有的 SiliconFlow 主模型与 RightCode 备用通道；未新增 provider。
- 数据目录为隔离副本 `/tmp/r2a_shadow`，复制 2026-10-11 已闭合日的 raw/cleaned 与账本副本。
- 只运行到 `extract → temporal_fact_validation → novelty_check → score → analyze`。
- 正式 `data/event_ledger`、`data/delivery`、`reports`、`podcasts`、SMTP 均未被写入。
- 未开发费用台账、日金额预算或价格估算功能。
- 未将 R1 分支合并到 `main`，未进入 R2-B/M2。

## 2. 影子数据与命令

### 2.1 数据复制

```text
/tmp/r2a_shadow/data/raw/2026-10-11/
/tmp/r2a_shadow/data/cleaned/2026-10-11/
/tmp/r2a_shadow/data/event_ledger/events_seen.jsonl
/tmp/r2a_shadow/data/source_ledger/seen_urls.jsonl
/tmp/r2a_shadow/data/delivery/ledger.jsonl
/tmp/r2a_shadow/config/{pipeline,scoring,keywords,sources,llm_providers,model_router}.yaml
```

### 2.2 完整影子链路

```sh
cd /tmp/r2a_shadow
PYTHONPATH=/tmp/r2a_wt:/tmp/r2a_shadow \
/app/venv/bin/python3 -m skills.run_daily_pipeline.run_daily_pipeline \
  --project-root /tmp/r2a_shadow \
  --date 2026-10-11 \
  --start-step extract --end-step analyze \
  --use-llm true --force true --send-email false
```

结果：退出码 `0`，真实 LLM 请求成功，未发送邮件。日报/周报步骤未执行；但旧版编排器在 `--end-step analyze` 后仍自动尝试播客步骤，详见 §6。

### 2.3 影子契约审计

```sh
cd /tmp/r2a_shadow
PYTHONPATH=/tmp/r2a_wt \
/app/venv/bin/python3 -m ops.r2a.audit_shadow \
  --project-root /tmp/r2a_shadow --date 2026-10-11 \
  --out /tmp/r2a_shadow/data/logs/2026-10-11/r2a_shadow_audit.json
```

审计结果已脱敏复制到 `docs/reviews/artifacts/NAS-R2A-shadow-audit-2026-10-11.json`。

## 3. 影子链路结果

| 阶段 | 事件数 | 说明 |
|---|---:|---|
| cleaned articles | 7 | 输入文章 |
| events_raw | 50 | 从 7 篇文章抽取 50 条事实，说明抽取粒度偏细 |
| events_temporal | 50 | 时效状态完整写入 |
| events_scored_novelty | 50 | 新颖性状态完整写入 |
| events_scored | 50 | 评分完整 |
| events_analyzed | 50 | 商业分析完整 |

### 3.1 事件 ID 连续性

- raw / temporal / novelty / scored / analyzed 均为 50 个唯一 ID。
- `lost_vs_raw = []`，`extra_vs_raw = []`，`duplicate_raw_ids = []`。
- 结论：本轮影子链路没有发生历史性的“后一阶段丢失 event_id”。

### 3.2 日期与证据

- `evidence_text` 空：**0/50**。
- `source_url` 空：**0/50**。
- `event_occurred_at` 空：**21/50**。
- `event_time_precision`：day 14、week 1、month 14、quarter 6、year 6、unknown 9。
- 时效状态：`new_fact` 16、`updated_fact` 3、`ongoing_fact` 4、`historical_background` 4、`upcoming_fact` 3、`unknown_fact_time` 20。

结论：证据字段通过；**事件日期不完整**。21 条缺失事件日期，20 条时效状态为 `unknown_fact_time`。不能因为评分后多数被归档，就把它当作“日期门已通过”。

### 3.3 评分与可报告池

- 综合分缺失：0/50。
- 优先级：`P2 = 11`，`ARCHIVE = 39`。
- `hard_downgrade_count = 34`（含硬降级原因的事件数）。
- `report_eligibility`：core 50；这是 score 的写入行为，但日报实际还会按 `priority` 过滤 ARCHIVE。
- 可报告池（非 ARCHIVE 且 eligibility 非 archive）：**11**。

可报告池中发现 2 条明显域外内容：

- `E20261011_0044`：乘联会乘用车零售数据；
- `E20261011_0018`：波司登门店关闭。

这两条不是 3C/即时零售/个护美妆决策所需的品类情报。说明“相关度 ≤1 才强制归档”的规则仍会让 watsons_relevance=2 的宏观/服饰事件进入 P2。R2-A 只记录，不在本轮修改相关性策略。

### 3.4 商业分析配对

- `business_analysis` 缺失：0/50。
- `watsons_impact` 空：0/50。
- `recommended_action` 空：0/50。
- 唯一 impact 数：50/50。
- 重复 impact 分组：0。
- 低语义重叠告警：0/50。
- 有 `follow_up_questions` 的分析：32 条（其余为规则稿）。

在影子日期上，未观察到 2026-06-10 那类“分析串到别的事件”的现象。需要注意：低重叠检测只是回归告警，不是语义正确性的证明；生产验收仍需人工抽样。

## 4. 历史去重

在 50 条 `events_temporal` 复制到隔离日期 `2026-10-12` 后，执行 novelty + score（不调用 LLM）：

- novelty：`repeated = 50`，`new_today = 0`。
- `report_eligibility = reference`：50/50。
- `report_eligibility = core`：0。
- priority 仍为 `P2 = 11`、`ARCHIVE = 39`。

解释：

- 去重能正确把跨日重复事件移出核心报告池（core=0）。
- 但 score 不修改 `priority`。如果下游健康检查只看 P2 数量，会把重复事件误判为“今天仍有 11 条可报告”。watchdog 因此应统计 `reportable_count`，并同时检查 eligibility，不能只看 priority。
- 本轮不把 `reference` 强改为 ARCHIVE，避免破坏“历史背景可引用”的设计；但后续 score 契约应显式定义 `priority` 与 `eligibility` 的优先级。

## 5. `needs_verification` 接入结论

这是本轮最重要的质量缺口。

影子链路结果：

- `verification_status` 字段：**0/50**。
- `needs_verification = true`：**0/50**。
- 独立审计判定“事件日期缺失”：21/50。
- 传闻/消息源措辞：2 条（`E20261011_0041`、`E20261011_0046`）。

注入测试：

- 在一条 P2 事件的 novelty 输入中注入 `needs_verification=true`、`verification_status=needs_verification`。
- score 输出原样保留字段，但 priority 仍为 **P2**、report_eligibility 仍为 **core**，`downgrade_reasons` 没有相应条目。

结论：

1. **字段可以透传，但生产链路没有生成它，也没有用它对评分/报告资格降级。**
2. R1-FIX 的 `needs_verification` 只存在于验证测试模块，不是生产 `extract → analyze → score` 的契约。
3. 最小修复建议：在 extract 之后增加一个确定性 `verify_event_claims` 步骤（或把 R1 的评估函数接入），至少覆盖：
   - `event_occurred_at` 为空；
   - 内容日期与采集日期冲突；
   - 传闻/消息源措辞；
   - 证据与来源 URL 缺失。
4. score 收到 `needs_verification=true` 时，应至少强制降到 `P2` 或 `ARCHIVE`，并在 `downgrade_reasons` 写明 `needs_verification:*`；日报只能以“待核实”标签展示，不能当已确认事实。
5. 在接入前，health check 不得把“没有 verification 字段”报告为通过。

## 6. 与原管线其他步骤的兼容性

### 6.1 `--end-step` 后置副作用 bug

影子运行 `--end-step analyze` 后，编排器仍然自动进入 `generate_podcast`：

- 生成了隔离目录内的播客稿；
- 尝试了 TTS，但未生成 MP3；
- 未发送邮件，未生成日报。

这不是预期行为，也会浪费 LLM/TTS 时间并扩大影子测试副作用。已在 R2-A 分支加最小 guard：

`skills/run_daily_pipeline/run_daily_pipeline.py`

```python
podcast_within_scope = end_step is None or STEP_ORDER.index("generate_podcast") <= end_idx
if not gp_step_in_main and podcast_within_scope:
```

回归验证：在隔离目录执行 `--start-step score --end-step score`，日志显示：

- 只运行 `score`；
- 不出现 `generate_podcast` 步骤；
- `podcast_review` 跳过；
- `email_sent = false`；
- 退出码 0。

### 6.2 novelty 账本计数

旧版 `validate_novelty_check` 从输出 metadata 读取 `ledger_entries`，但 novelty 输出没有该字段，导致 run manifest 显示 `ledger_entries=0`，而日志实际加载 6060 条。已在 `event_novelty_check.py` 的 metadata 中补 `ledger_entries`。

回归：

```json
{"result_ledger": 6060, "metadata_ledger": 6060, "validator": {"ledger_entries": 6060}}
```

### 6.3 兼容性结论

- `extract → temporal → novelty → score → analyze` 的输入/输出文件契约在影子日成立。
- 事件 ID 连续性成立。
- 分析配对在影子日未见错位。
- `--end-step` 后置播客副作用已修复。
- `needs_verification` 未接入，属于生产链路缺口，不是影子脚本自身可修复的问题。
- 评分后的域外内容过滤仍有缺口，留给 R2-B 或专门的相关性整改。

## 7. 调度可靠性设计

### 7.1 当前失败模式

旧机制依赖 QwenPaw agent 被 cron 唤醒后回复“已启动”。这不能证明：

- Python 进程真的启动；
- 进程退出码为 0；
- 关键阶段全部完成；
- 事件池非空且分析完整。

因此 R2-A 把边界改为：**调度器 → 直接启动 Python launcher → launcher 写 manifest → watchdog 读 manifest**。

### 7.2 组件

| 组件 | 职责 |
|---|---|
| `ops/r2a/scheduler.py` | 计算 07:00 Asia/Shanghai 下一时点；`--loop` 由 supervisor 保活；`--once` 为可测试 tick；同日已有 success/no_signal 跳过；running 跳过；failed 默认不重试 |
| `ops/r2a/launcher.py` | 直接调用管线；每日期非阻塞锁；写 running 心跳与终态 manifest；真实 exit code；永远 `send_email=False` |
| `ops/r2a/watchdog.py` | 只读 manifest；发现未启动、失败、卡死、空池、分析不完整、48h 无健康状态、调度器心跳缺失/过期 |
| `ops/r2a/audit_shadow.py` | 影子链路契约审计 |
| `ops/r2a/supervisor/watsons-retail-intel-r2a.conf` | 草稿，未安装 |

### 7.3 Manifest 状态机

```text
scheduler tick
  -> launcher acquires date lock
  -> manifest status=running (heartbeat)
  -> pipeline
  -> manifest status=success | no_signal | failed
  -> exit_code=0 | 0 | 2
```

`no_signal` 只在 `cleaned_articles == 0` 时是成功状态；如果 cleaned > 0 但 events_raw=0，则必须是 `failed/empty_event_pool`。

### 7.4 Watchdog 告警

| code | 触发 |
|---|---|
| `missing_manifest` | 完全没有 launcher manifest；agent 回复“已启动”不算 |
| `run_failed` | 最新运行 failed |
| `stalled_running` | running 心跳超过 120 分钟 |
| `empty_event_pool` | cleaned>0 且 raw=0 |
| `empty_scored_pool` | raw>0 且 scored=0 |
| `incomplete_analysis` | scored>analyzed |
| `stale_healthy_manifest` | 最新健康终态超过 48 小时 |
| `scheduler_missing` / `scheduler_stalled` | 调度器心跳缺失/过期（`--require-scheduler`） |

watchdog 退出码：`0` 健康，`1` 至少一个 critical。

### 7.5 生产接入方式（未执行）

草稿使用 supervisord 保留两个常驻程序：

- `watsons-r2a-scheduler`：`python -m ops.r2a.scheduler --loop`；
- `watsons-r2a-watchdog`：每 5 分钟执行 `python -m ops.r2a.watchdog`，非零退出作为告警。

未修改 `/etc/supervisor/conf.d`。接入需要另行审批；接入后旧 agent cron 应作为独立变更停用，不能在同一变更里悄悄替换。

## 8. 故障注入与重复运行证据

### 8.1 单元/子进程测试

命令：

```sh
cd /tmp/r2a_wt
PYTHONPATH=/tmp/r2a_wt /app/venv/bin/python3 -m unittest discover -s tests/r2a -v
```

结果：`Ran 25 tests ... OK`。

覆盖：

- launcher 真实子进程 exit 0 / 2；
- 空事件池 → exit 2；
- pipeline exception → exit 2；
- no_signal（cleaned=0）→ exit 0；
- no_signal 但 cleaned>0 → failed；
- 关键步骤失败、分析不完整；
- watchdog：无 manifest、仅 agent 文本、失败、running 卡死、48h 陈旧、空信号、调度器缺失；
- scheduler：同日/次日时点、成功跳过、running 跳过、failed 重试开关。

子进程测试使用 `tests/r2a/fake_pipeline.py` 来驱动真实 launcher 的退出码与 manifest 路径；它**不**声称生产管线在 25 个测试中通过。真实生产管线证据见 §2/§3。

### 8.2 真实 launcher 冒烟（生产管线代码，单文章）

为了证明调度边界不是只靠 fixture，另建 `/tmp/r2a_launcher_real`，只放 1 篇已闭合文章，用真实 `ops.r2a.launcher` 直接调用生产 `run_daily_pipeline`：

```text
launcher exit=0
status=success, reason=complete_event_pool
counts: cleaned=1, raw=4, temporal=4, scored=4, analyzed=4
reportable_count=0
send_email=false
steps: extract/temporal/novelty/score/analyze 全部 success
podcast: 未生成
总耗时 11.3s
```

随后 watchdog 读取该真实 manifest：`ok=true`、`status=healthy`、无告警。

证据：`docs/reviews/artifacts/NAS-R2A-launcher-smoke-manifest.json`。

这不是完整 7 篇文章的影子日（完整影子日见 §2/§3），而是 launcher 边界的真实 Python 冒烟证据。

### 8.3 Scheduler 真实拉起 Python

用真实 `scheduler.py` 在隔离目录拉起真实 `launcher.py`；只把 pipeline module 换成
`tests/r2a/fake_pipeline.py`，避免再次产生 LLM 请求：

```text
scheduler exit=0
{"action":"launched","date":"2026-10-25","pid":18856,...}
child manifest: status=success, counts 1/2/2/2/2
第二次 tick: {"action":"skip","reason":"already_success"}
```

结论：scheduler 确实通过 `subprocess.Popen(start_new_session=True)` 启动了同一个
Python launcher，并靠 manifest 对同日重复 tick 去重；不依赖 agent 回复。

### 8.4 故障注入 fixture 驱动的真实 launcher

以下运行使用真实 `launcher.py` 进程，只把 `--pipeline-module` 替换为
`tests/r2a/fake_pipeline.py`，用于注入 success/empty 结果；它不证明生产管线成功。

```text
launcher exit=0
{"status":"success","reason":"complete_event_pool","exit_code":0,...}
watchdog exit=0
{"ok":true,"status":"healthy","latest_status":"success","alerts":[]}
```

空池注入：

```text
launcher exit=2
{"status":"failed","reason":"empty_event_pool","exit_code":2,...}
watchdog exit=1
{"ok":false,"status":"critical","latest_status":"failed","alerts":[{"code":"run_failed",...}]}
```


### 8.5 影子重复运行

- 本轮实际执行的是“次日重跑”：把 50 条 `events_temporal` 复制到 2026-10-12 后运行 novelty + score；50/50 `repeated`、core=0、reference=50，证明跨日历史去重生效。
- 同日重跑没有单独执行；代码路径中的同日状态仍是 `new_today`，本轮不把它当作已验证结论。
- 结论：重复运行不会把跨日旧事件留在核心报告池，但 score 的 priority 字段不随之改变，健康检查必须以 `reportable_count/eligibility` 为准。

## 9. 本轮代码变更

| 文件 | 变更 | 性质 |
|---|---|---|
| `ops/r2a/launcher.py` | 直接启动、锁、心跳、manifest、exit code | 新增 |
| `ops/r2a/scheduler.py` | 07:00 调度、同日去重、direct child | 新增 |
| `ops/r2a/watchdog.py` | 独立健康检测 | 新增 |
| `ops/r2a/run_state.py` | 原子 manifest 与计数 | 新增 |
| `ops/r2a/audit_shadow.py` | 影子契约审计 | 新增 |
| `ops/r2a/README.md` | 运行与回滚说明 | 新增 |
| `ops/r2a/supervisor/watsons-retail-intel-r2a.conf` | supervisor 草稿，未安装 | 新增 |
| `tests/r2a/` | 25 个测试 | 新增 |
| `skills/run_daily_pipeline/run_daily_pipeline.py` | `--end-step` 抑制后置播客 | 最小修复 |
| `skills/event_novelty_check/event_novelty_check.py` | metadata 补 ledger_entries | 最小修复 |
| `docs/reviews/NAS-R2A-SHADOW-AND-SCHEDULER-TEST.md` | 本文 | 新增 |
| `docs/reviews/artifacts/NAS-R2A-shadow-audit-2026-10-11.json` | 脱敏审计证据 | 新增 |
| `docs/reviews/artifacts/NAS-R2A-launcher-smoke-manifest.json` | 真实 launcher 冒烟 manifest | 新增 |

## 10. 未完成 / 留给复审

1. `needs_verification` 生产接入未做；本轮只给出最小修复建议和注入证据。
2. 事件日期缺失（21/50）未做抽取层修复。
3. 域外内容（汽车宏观、服饰）未做相关性策略修复。
4. supervisor 草稿未安装；生产 Cron 未改。
5. 未做真实生产日运行；影子测试不替代生产验收。
6. 未进入 R2-B/M2，未合并 R1 到 `main`。

## 11. 回滚

- 代码回滚：删除 `ops/r2a/`、`tests/r2a/`、本文与审计 artifact，并撤销两处最小修复。
- 调度回滚：不安装 supervisor 草稿即可；如果已安装，停止并删除两个 program，不触碰事件账本、Docker、SMTP。
- 数据回滚：所有影子数据在 `/tmp/r2a_shadow`，生产数据目录未被写入。
