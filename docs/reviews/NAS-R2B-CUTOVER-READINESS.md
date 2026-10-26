# NAS R2-B｜生产切换准备与一次性受控切换门禁

日期：2026-10-26。项目：lovestream/watsons-retail-intel。

> **本轮结论：只读核验与隔离排练完成；生产切换仍为 NO-GO，但已收敛为“一次受控日更切换”的 CONDITIONAL GO。**
>
> 本轮没有修改生产代码、生产数据卷、SMTP、模型 Key、正式事件账本或旧 Cron；没有安装/启动生产 supervisor，没有发送邮件或真实告警，没有合并 `main`。本报告与隔离分支提交后停止，等待用户/ChatGPT 最终切换审批。

## 0. 给审查者的一页结论

| 问题 | 裁定 |
|---|---|
| 是否建议再把 R2-B 扩展成新系统？ | **否。** 本轮只做切换准备，不新增功能。 |
| 是否找到宿主 Synology Task Scheduler 的可用入口？ | **否。** 容器内无 `/usr/syno`、无 `synoschedtask`、无 Docker socket，DSM 5000/5001 无可用响应；不能把宿主调度当成已落实。 |
| 唯一选择的调度方式 | **现有 PID1 `supervisord`**。它是容器内唯一已安装、可做进程托管的权威执行器；需要一次性增加 control socket + 两个 program，并由用户批准一次受控重启。 |
| 是否同时开发多套调度？ | **否。** 只保留 supervisor 这一套；宿主 Task Scheduler 未被选用。 |
| 生产 58 个 dirty 变更怎么处理？ | 现场复核为 **71 项（8 tracked modified + 63 untracked）**；其中 **52 项是数据/账本/报告运行产物，必须保护**；3 项文档需人工决定；16 个 `_*.py|sh` 是一次性诊断脚本，不进入发布代码。没有核心 `skills/`/`ops/`/`tests/` dirty 代码。 |
| 代码怎么集成？ | **不能 `git merge/pull` 整体覆盖**；生产 HEAD `2464df9` 与 R2-B 分支没有可用 merge base，必须按文件人工 diff、备份、测试后再受控部署。 |
| daily 旧 Cron | 切换时暂停，由新 supervisor scheduler 取代；不再恢复旧 daily。 |
| periodic 旧 Cron | **本轮不切换**。只在切换窗口暂停，首轮新 daily 成功后恢复原 periodic；周/月/年报质量仍属 R3。 |
| 真实告警 | **尚未可宣称送达**。建议只用一份 SMTP critical 邮件通道，去重/恢复/静默；容器整体死亡的外部 dead-man 仍依赖未验证的宿主侧检查，属于上线前必须批准的残留项。 |
| 现在能否直接切换？ | **不能。** 备份已于 2026-10-26 执行并 restore 校验通过（7,979/7,979）；仍需：代码人工集成、supervisor 控制面配置与重启、旧 Cron 暂停授权、真实告警接收测试、切换窗口确认。完成这些后可按 Runbook 一次性切换。 |

**建议批准方式：** 批准“日更切换到 supervisor”这一条；periodic 保持旧实现；不批准任何新的调度器、费用台账或自动重试扩张。

## 1. 基线、分支与证据边界

| 项 | 值 |
|---|---|
| GitHub `main`（只读） | `6c224aed1062c0ac92b209ecf141dda00330ef80` |
| R2-B 预检远端分支 | `r2b/preflight` = `db95334006f1933af402b36ee0476bd086a8b431` |
| 本地预检提交/树 | `db4e385`，tree `6fbc65d242290d5756da6adad628182395afc8ef`（与远端预检树一致） |
| 生产工作副本 | `<PROD_ROOT>`，HEAD `2464df94e7ceb7b327eb912fd4fdeeb77ba22f11` |
| 本报告分支 | `r2b/cutover-prep`，从 R2-B 预检树派生；不合并 `main` |
| 隔离测试 | `PYTHONPATH=. python3 -m unittest discover -s tests/r2a -v` → **Ran 53 tests, OK** |
| 生产数据 | 生产持久卷 3.6T，已用 1.4T，可用 2.2T；项目 `data` 约 1.8G |

边界说明：

- 生产 HEAD 是 M1 审计提交，不是 R2-A/R2-B 代码；本地历史存在 graft，不能把 `r2b/preflight` 当成生产 HEAD 的快进分支。
- 本轮所有演练使用 `/tmp` 下的 fake pipeline/隔离 state；没有调用真实付费 LLM、没有真实采集、没有 SMTP、没有正式 `data/events`。
- 本报告不包含报告原文、事件正文、密钥、SMTP 凭据或真实告警接收地址。生产文件清单只以类别/路径族统计；完整 `status.txt` 只保存在本地备份中。

本文用 `<PROD_ROOT>` 表示生产 checkout，用 `<BACKUP_ROOT>` 表示项目外持久备份目录；公开文档不展开宿主机绝对路径。

## 2. 生产工作区：71 项 dirty 变更分层

### 2.1 数量口径

2026-10-25 预检记录为 58 项；2026-10-26 现场复核为 **71 项**：

- **8 项 tracked modified**：3 个文档（`AUDIT_20260811_collection.md`、`README.md`、`docs/README.md`）+ 5 个正式数据/报告文件（3 个 ledger、W32 的 `periodic_synthesis.json/.md`）。
- **63 项 untracked**：47 个数据/报告/备份/锁/临时文件 + 16 个 `scripts/_*.py|sh`。

这不是新增核心代码；58→71 的差异来自预检后继续生成的运行产物/临时文件。**不做 `reset --hard`、`clean`、`checkout` 或整体覆盖。**

### 2.2 分类与处置

| 类别 | 数量 | 代表路径族（不展开正文） | 体积 | 处置 |
|---|---:|---|---:|---|
| 正式账本/投递记录 | 3 tracked | `data/event_ledger/`、`data/source_ledger/`、`data/delivery/` | 3.42 MB | **必须保护**；不进发布提交；备份后原样保留 |
| 周期报告产物 | 17（2 tracked + 15 untracked） | `data/periodic/weekly/2026-W32...W43/` | 0.65 MB | **必须保护**；W33/W36/W39/W40/W43 是缺失周的证据，不得清理 |
| 既有备份 | 2 untracked | `data/backups/*.bundle`、`*.tar.gz` | 237.52 MB | **保护**；不是本次 71 项的新备份，不能替代 cutover 备份 |
| evergreen 候选 | 27 untracked | `data/evergreen/2026-07-06...08-07/` | 0.15 MB | **保护**；可重建但当前不删 |
| 周期锁 | 1 untracked | `data/locks/periodic.lock` | 5 B | **保护**；内容为旧 PID 25923，进程已不存在；`flock` 才是实际锁，不按文件内容删除 |
| 临时音频 | 2 untracked | `data/tmp/podcast/part_03_*.mp3` | 2.18 MB | **保护**；生成物，不进入 Git |
| 文档修改 | 3 tracked | 审计/README | 0.01 MB | **人工 diff**；不与分支文档自动覆盖 |
| 一次性脚本 | 16 untracked | `scripts/_backfill_missing.sh`、`_check_*`、`_send_w31*`、`_splice_*`、`_strip_*`、`_test_*` | 0.04 MB | **保留作取证，不合并**；其中 1 个含 credential-like 字面量，禁止上传/公开 |

**核心结论：这 71 项里没有 `skills/`、`ops/`、`tests/` 的生产代码修改。** 因此“保护工作区”与“集成 R2-B 代码”是两件事：

1. 数据/报告/锁/备份：保护，绝不进入发布提交。
2. 一次性脚本：只作现场取证，不能当生产模块。
3. R2-B 代码：来自隔离分支，必须按文件人工 diff；不能拿这 16 个脚本或整体 working tree 代替。

### 2.3 已观察到的运行状态

- 审计时 `pgrep` 未发现旧 `run_daily.sh` / `run_periodic.sh` / 对应 pipeline 进程。
- `data/locks/periodic.lock` 内容为 PID 25923，`ps -p 25923` 无进程；属于遗留 PID 内容，不应按“活跃进程”处理，也不应未备份就删除。
- 旧 Cron 仍启用：
  - daily `a892335a-4c55-4bce-8e57-6be07aa1764a`，`0 7 * * *` Asia/Shanghai。
  - periodic `7c4eb73d-6a9d-402a-81ff-0ffd88383651`，`0 9 * * *` Asia/Shanghai。
- 旧 daily 脚本当前只跑到 `analyze` 生成事件池，不发日报；旧 periodic 仍负责周/月/年报。因此 **不能把两个旧 Cron 当成同一个功能一起停掉**。

## 3. 备份与恢复：先保护，后集成

### 3.1 现有备份不够

`data/backups/` 内两份 2026-08-09 备份：

- bundle：`git bundle verify` 显示 complete history；SHA256 `378bb6fe...`。
- code tar：166 entries；SHA256 `0b45f6fd...`。

它们验证完整，但日期早于当前 71 项 dirty；**不能覆盖本轮现场**。当前可用空间 2.2T，足以放新快照。

### 3.2 新备份草案

新增：

- `ops/r2b/backup_worktree.sh`：默认 dry-run；`--execute --output-root <BACKUP_ROOT>` 时才写入项目外目录；未给 output-root 的 execute 会拒绝运行。
- `ops/r2b/restore_worktree.sh`：默认只校验；`--execute` 只解到空 staging 目录，**不在生产原地恢复**。

备份内容：

- `tracked.patch`：`git diff --binary HEAD`，覆盖 8 项 tracked 修改；
- `untracked.tar.gz`：全部 63 项 untracked（含数据/报告/脚本），保留相对路径；
- `status.txt`、`HEAD`、`diffstat.txt`、`untracked.list.z`、`SHA256SUMS`、`verify.txt`；
- 目录权限 700，文件 600；拒绝覆盖已有备份目录。

目标路径（执行时）：

```text
<BACKUP_ROOT>/cutover-<UTC>/
```

该路径在项目外、同一持久卷内，能防误覆盖，但**不能防整卷/宿主机故障**；真正的异地副本需要用户在宿主侧另行批准。备份可能含私有报告/诊断内容，禁止提交或上传。

### 3.3 已验证

- 在临时 git 仓库注入 4 项 dirty（2 tracked + 2 untracked）：backup dry-run 不写文件；execute 生成 patch/tar/SHA；`sha256sum -c` 全 OK；restore dry-run 校验；restore execute 只写空 staging；未触碰生产。
- 对生产工作区只做 **dry-run**：识别 `head=2464df9`、`dirty_entries=71`、`untracked=63`；确认没有创建备份目录。
- 2026-10-26 已在项目外执行真实备份：71 项、7,979 个业务文件、restore `7979/7979`、`bad=0`；证据见 `artifacts/R2B-BACKUP-AND-SUPERVISOR-DRYRUN-2026-10-26.json`。

## 4. 调度入口：为什么只选 supervisor

### 4.1 宿主 Task Scheduler 核实结果

在容器内只读检查：

- 无 `/usr/syno`、`/volume1`、`/volumeUSB1`；
- 无 `synoschedtask` / `docker` 可执行文件；
- 无 `/var/run/docker.sock`；
- `host.docker.internal:5000/5001` 返回 empty reply，不能作为 DSM API 使用；
- 无法取得宿主任务列表、容器名、DSM 账号或“重启后仍执行”的证据。

**结论：宿主 Task Scheduler 不能从当前 QwenPaw 容器内验证，也不能被写成“已经可用”。** 因此不选它，避免把未经核实的宿主入口当生产调度。

### 4.2 唯一选择：现有 PID1 supervisord

现状：

- PID1 是 `supervisord 4.2.5`，已安装并托管 `app`、`dbus`、`xvfb`、`xfce4`；
- 但没有 `[unix_http_server]` / `[rpcinterface:supervisor]` / `[supervisorctl]`，`/var/run/supervisor.sock` 不存在；
- 因此当前无法 `supervisorctl reread/update/start/stop`，也无法做到可靠回滚控制。

选择它的理由：

1. 已是容器 PID1，不需要新装第二套调度器；
2. 能提供进程存活/自动重启/退出码保存；
3. 能在容器重启后恢复（配置 `autostart=true`）；
4. 唯一需要的一次性改动是增加控制面和两个 program；不修改 `program:app`。

### 4.3 最小配置改动（尚未安装）

在 `/etc/supervisor/conf.d/supervisord.conf` 增加：

```ini
[unix_http_server]
file=/var/run/supervisor.sock
chmod=0700

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock
```

再追加两个 program（复用已审的 `ops/r2a/supervisor/watsons-retail-intel-r2a.conf`，但必须按生产路径核对）：

- `watsons-r2b-scheduler`：
  - `python3 -m ops.r2a.scheduler --project-root <PROD_ROOT> --state-dir <PROD_ROOT>/data/ops --loop --mode scheduled --start-step health_check --end-step analyze --hour 7 --minute 0 --window-minutes 90`
  - `autostart=true`、`autorestart=true`、`startsecs=5`、`stopsignal=TERM`、日志到 `data/logs/`；
- `watsons-r2b-watchdog`：
  - `python3 -m ops.r2a.watchdog --state-dir <PROD_ROOT>/data/ops --max-age-hours 48 --stale-minutes 120 --require-scheduler --loop --interval 300`
  - `autostart=true`、不吞 exit code；只写本地 sink，通知通道另行配置。

关键约束：

- **先只加 control socket，不加 program，重启一次 PID1**；验证 `supervisorctl status` 可用。
- 切换时再写入 program；`reread/update` 不重启 QwenPaw。
- 配置里不写任何 Key；子进程沿用现有 0600 secret/环境变量，部署前只验证变量名和可读性，不打印值。
- supervisor 以 root 运行；socket 0700；这是容器内单用户控制面，仍需用户批准。

### 4.4 风险

- 第一次增加 control socket 需要重启 PID1；`app`/桌面服务会短暂重启。必须在无日报/周报/采集运行、非 07:00/09:00 的窗口执行，并由用户批准。
- 这一改动比“宿主直接调 Python”风险高，但宿主入口不可验证；在没有宿主访问权限的前提下，它是唯一可审计、可持久化的方案。
- 不选独立 `supervisord`：它无法在容器重启后自动存活，会制造第二套控制面，正是本轮要避免的过度设计。

## 5. 隔离排练结果

完整机器可读证据：`docs/reviews/artifacts/NAS-R2B-CUTOVER-DRILL-2026-10-26.json`。

| 场景 | 实际结果 | 判定 |
|---|---|---|
| scheduled success | manifest `success`，`collection_proof=verified`，`collection_health=ok` | 通过 |
| 同日重复 tick | `skip / already_success` | 通过（双跑保护） |
| 模拟容器重启后再次 tick | 新进程读旧 state，仍 `skip / already_success` | 通过（无重复） |
| 采集失败 + 缓存池 | `failed / no_signal_without_verified_collection` | 通过（不伪造空窗） |
| 合法零新闻 | `no_signal / no_input_articles_after_successful_collection`，proof verified | 通过 |
| watchdog 失败 | exit 1，sink 写 `run_failed` critical，`active` 1 条 | 通过（本地） |
| watchdog 恢复 | exit 0，写 `recovered`，`active=[]` | 通过（本地） |
| 同一失败重复检查 | events 3→3，无重复 active | 通过（去重） |
| supervisor 隔离控制面 | scheduler/watchdog 均 RUNNING；kill -9 watchdog 后 PID 28746→28768 自动重启 | 通过（隔离） |
| 模拟旧 cron 仍在跑 | 以 `scripts/run_periodic.sh` 为 argv0 的假进程被 `pgrep` 命中 2 条；停止后精确进程消失 | 通过（检测门） |
| 备份/恢复脚本 | 临时仓库 patch/tar/SHA/ staging 全部通过；生产只 dry-run | 通过 |
| 宿主 Task Scheduler | 不可访问/不可验证 | **不通过**；因此不选 |
| 生产首轮真实采集 | 未执行（不允许） | **未验证** |

限制：

- 这些是 fake pipeline 和隔离 state；不等于生产实采、真实 LLM、真实周报/邮件。
- supervisor 隔离测试证明的是控制面和自动重启语义；生产 PID1 尚未修改，不能宣称生产已受控。
- watchdog 本地 sink 不等于用户收到告警；真实投递未测。

## 6. daily 与 periodic 的分别处置

### 6.1 daily（旧 `a892335a`）

- 切换窗口内**暂停**，不恢复旧 daily。
- 新 `watsons-r2b-scheduler` 每天 07:00 启动同一 Python launcher，从 `health_check` 开始，跑到 `analyze`，只生成事件池供周期报告使用；R2-B 不生成日报、不发日报邮件。
- 新 scheduler 的日期锁保证同日 `success/no_signal` 或 `running` 不重复启动；`failed` 默认不自动重试。

### 6.2 periodic（旧 `7c4eb73d`）

- **本轮不切换**。旧 periodic 仍负责周/月/年报；R3 的 HTML、MP3、最终清洗和 SMTP 质量门没有在本轮验收，不能把新 scheduler 冒充周期报告替代品。
- 切换窗口内暂停，首轮新 daily 完成后恢复原 periodic；恢复后仍按原 09:00 执行。
- 旧 periodic 的锁是 `flock`，不是文件 PID；`periodic.lock` 内容虽为旧 PID，但没有活跃 holder。恢复前仍需复核，不删除文件。
- 因为旧 periodic 不共享新 scheduler 的日期锁，必须遵守：
  1. 切换日选 **非周一、非月初、非 1 月 1 日**；
  2. 切换在 09:00 之后，确认当天 periodic 已完成；
  3. 新 daily 首轮若到 08:30 仍未 terminal，当天暂停 periodic，待 daily 结束后人工恢复；
  4. 不在 07:00–09:00 之间做 supervisor 重启。

### 6.3 互斥边界

- 新旧 daily 的互斥靠“旧 Cron 已暂停 + `pgrep` 无旧进程 + scheduler 日期锁”，不靠文件 mtime。
- daily 与 periodic 是不同功能，允许在不同时段并存；但同一时段不得同时写事件池/账本。
- 旧 periodic 回滚只是回到旧行为，**不保证业务已恢复**；它长期不稳定，必须在回滚后单独验证执行和交付。

## 7. 真实告警：只提一个低噪声通道

建议通道：**现有 SMTP 邮件，仅发送 critical 告警，不与日报/周报共用主题和收件流程。**

规则：

- 只对 `critical` 发信；warning 只进本地 sink。
- 去重键：`code + run_id + date + severity`；同一 active 事件只发一次。
- 恢复：写 `recovered` 事件；是否发恢复邮件由用户选择，默认只记本地，避免噪声。
- 不包含文章正文、报告原文、Key、完整路径；只含 code、date、run_id、manifest 摘要。
- 不使用 `|| true` 吞 watchdog exit code。
- 首次上线前必须做一次“测试接收”，用户确认收到后才算真实通道可用。

**未解决的失联监测：** 如果容器/PID1/watchdog 整体死亡，容器内 watchdog 无法发信。当前没有已验证的宿主 dead-man 入口；宿主 Task Scheduler 仍不可访问。可选的下一步是用户在 DSM 宿主配置一个只读心跳检查（例如每 15 分钟检查 manifest/heartbeat，异常时用 DSM 通知），但这需要宿主权限、容器名和接收测试，**本轮没有配置，也不能写成已实现**。

因此本轮只能声称“本地告警记录/去重/恢复已验证”，**不能声称“用户一定会收到 critical 告警”**。真实告警是切换前置门，不是切换后的补丁。

## 8. 一次性受控切换 Runbook

### 8.1 前置条件（全部满足才可开始）

1. 用户明确批准：备份、代码人工集成、supervisor control socket + PID1 重启、暂停旧 Cron、启动新 scheduler/watchdog、测试告警接收。
2. 切换窗口：**周二至周四，09:00 之后，非周一/月初/1 月 1 日**；当天旧 daily 已为 `success`/合法 `no_signal`，旧 periodic 无待执行任务；无采集/报告进程。
3. `ops/r2b/backup_worktree.sh --execute --output-root <BACKUP_ROOT>` 成功，`verify.txt` 全 OK；71 项 dirty 有完整快照；旧备份和新快照都在。
4. R2-B 代码已按文件人工 diff、冲突审查、53 项隔离测试通过；没有 `git merge/pull/reset/clean` 整体覆盖。
5. `data/locks/periodic.lock` 已复核无活跃 holder；保留原文件。
6. 真实告警通道已测试接收，或用户明确接受“容器整体死亡时无外部告警”的残留风险。
7. `supervisorctl` 控制面已可用；`supervisorctl status` 能看到 `app` 等原程序。

### 8.2 切换步骤

1. **备份**：运行 `backup_worktree.sh --execute --output-root <BACKUP_ROOT>`；记录 `HEAD`、71 项、SHA256；不改工作区。
2. **暂停旧 Cron**（需批准）：
   ```bash
   qwenpaw cron pause a892335a-4c55-4bce-8e57-6be07aa1764a --agent-id watsonmainagent
   qwenpaw cron pause 7c4eb73d-6a9d-402a-81ff-0ffd88383651 --agent-id watsonmainagent
   ```
3. **确认无双跑**：
   ```bash
   pgrep -af 'scripts/run_daily\.sh|scripts/run_periodic\.sh|run_daily_pipeline|run_periodic_pipeline'
   pgrep -af 'ops\.r2a\.scheduler|ops\.r2a\.launcher'
   ```
   有输出则中止，不杀进程，不启动新任务。
4. **控制面**：仅在尚未启用时，增加 socket/rpc 配置并**一次**重启 PID1；验证 `supervisorctl status`。若已有控制面，跳过重启。
5. **代码部署**：按人工 diff 把已审 R2-B 代码合入生产；只动代码/测试/配置，不覆盖 `data/` 和 3 个文档修改；部署后跑隔离测试与 `py_compile`。任何冲突/测试失败 → 中止并回滚代码，不启动 scheduler。
6. **写入 program**：加入 scheduler/watchdog，`autostart=true`；`supervisorctl reread && supervisorctl update`。若当前不在 07:00 窗口，scheduler 只计算下一槽，不会立即跑。
7. **启动并观察**：
   ```bash
   supervisorctl start watsons-r2b-scheduler watsons-r2b-watchdog
   supervisorctl status
   ```
   检查 `data/ops/scheduler/heartbeat.json` 和 `data/ops/watchdog/status.json`；首个 slot 前不得有 manifest。
8. **首轮 07:00**：等 manifest terminal；检查 `status`、`reason`、`collection_proof=verified`、`collection_health=ok`、`reportable_count`。若失败，按第 8.4 回滚/恢复；不自动重试。
9. **恢复 periodic**：只有首轮 daily 为 `success` 或合法 `no_signal`，且无旧进程时，恢复旧 periodic：
   ```bash
   qwenpaw cron resume 7c4eb73d-6a9d-402a-81ff-0ffd88383651 --agent-id watsonmainagent
   ```
   daily 旧 Cron 保持暂停。
10. **观察窗**：至少一个完整调度日 + 一次失败注入/恢复（可在隔离环境）；确认无重复、无告警风暴、无数据覆盖。

### 8.3 中止条件

任一条出现即中止，不继续：

- 备份校验失败或磁盘不足；
- 代码 diff 冲突/测试失败；
- `pgrep` 发现旧或新进程重叠；
- supervisor 重启后 `app` 不健康；
- 首轮 manifest 为 `collect=failed/skipped`、`collection_proof != verified` 或 `content_ready=false`；
- 出现无信号但采集未完成；
- 告警通道未确认却要求真实依赖。

### 8.4 回滚

1. `supervisorctl stop watsons-r2b-scheduler watsons-r2b-watchdog`。
2. 将 program 改为 `autostart=false`，`supervisorctl reread && supervisorctl update`；保留 `data/ops` 和日志，不删除。
3. 恢复旧 daily：
   ```bash
   qwenpaw cron resume a892335a-4c55-4bce-8e57-6be07aa1764a --agent-id watsonmainagent
   ```
4. 若 periodic 已暂停，恢复 `7c4eb73d...`。
5. 确认旧 Cron enabled，旧脚本存在；手工触发一次旧 daily/periodic 并检查日志，不能只回复“已恢复”。
6. 代码回滚按备份 `tracked.patch` 反向审查；不做 `reset --hard`/`clean`；数据保持原样。
7. **诚实标注**：回滚到旧 Cron 是回到旧行为，不保证旧 Cron 能稳定执行或报告能交付。

## 9. R3 遗留（不得混入本次切换验收）

以下没有在本轮证明，不能宣称整个系统恢复：

- 周/月/年报的最终清洗、质量门、HTML 邮件、MP3、TTS、SMTP 幂等；
- 报告事件分析配对、判断标签剥离、空占位/表格残留；
- `verification_status=verified` 只是规则级，不等于人工事实复核；
- W33/W36/W39/W40/W43 等周期产物需单独审计来源和发送状态；
- 周期报告仍由旧 periodic 运行，R2-B 不替代它。

## 10. 授权矩阵

| 事项 | 当前状态 | 需要 |
|---|---|---|
| 备份 71 项到项目外 | 脚本已测，未执行 | 用户批准 `--execute` + 空间确认 |
| 合并 `main` | 未做 | 独立批准；本轮禁止 |
| 生产代码集成 | 未做 | 人工逐文件 diff + 测试 + 批准 |
| supervisor control socket/PID1 重启 | 未做 | 明确批准维护窗口 |
| 安装/启动 scheduler/watchdog | 未做 | 控制面完成 + 旧 Cron 暂停 + 批准 |
| 暂停/恢复旧 Cron | 未做 | 明确批准 |
| 真实告警接收 | 未做 | 通道/收件测试；dead-man 另批 |
| 宿主 Task Scheduler | 不可验证 | 宿主权限/容器名/账号；本轮不选 |
| 费用台账/预算/估价 | 未做 | 按用户要求不做 |
| R3 周期质量 | 仅需求 | 后续独立阶段 |

## 11. 停止点

本报告、备份/恢复脚本草案和隔离演练证据提交到 `r2b/cutover-prep` 后立即停止：

- 不合并 `main`；
- 不安装/启动生产 supervisor；
- 不暂停/恢复旧 Cron；
- 不修改正式数据卷、SMTP、Key 或报告；
- 不发送真实告警/邮件；
- 不宣称生产已恢复。

等待 ChatGPT 对“一次性受控日更切换”给出批准或拒绝；periodic 与 R3 不在本次批准范围内。

## 附录 A：证据文件

- `docs/reviews/artifacts/NAS-R2B-CUTOVER-DRILL-2026-10-26.json`：隔离 scheduler/watchdog/supervisor/备份演练结果。
- `ops/r2b/backup_worktree.sh`、`ops/r2b/restore_worktree.sh`：默认 dry-run/仅 staging 的备份恢复草案。
- 生产完整 dirty 清单只保存在本地备份的 `status.txt`，不上传公开仓库。

## 附录 B：本轮未做的事项（明确）

- 没有运行 `backup_worktree.sh --execute`；
- 没有运行生产 `supervisord` 配置检查/启动；
- 没有调用 `qwenpaw cron pause/resume`；
- 没有读取或发送真实报告邮件；
- 没有调用真实 LLM/采集；
- 没有修改 `main` 或生产工作树。
