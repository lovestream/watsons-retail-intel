# R2 一次性上线候选：备份、控制面演练与发布边界

日期：2026-10-26  
分支：`release/r2-cutover-candidate`  
生产 HEAD（只读核验）：`2464df94e7ceb7b327eb912fd4fdeeb77ba22f11`  
结论：**代码与备份候选已就绪；正式生产切换仍为 NO-GO，等待单独授权。**

## 1. 本轮实际完成

1. 在生产工作区只读采集完整备份，备份目录在项目外：
   `/app/working/backups/watsons-retail-intel/cutover-20261026T140436Z`
2. 备份覆盖 tracked patch、63 个 untracked 文件、`data/reports/podcasts/config/memory` 业务数据；
   共 7,979 个业务文件，文件级 size+SHA256 manifest；restore staging 逐一校验
   `7979/7979` 通过、`bad=0`；备份总大小约 1.1GB。
3. `verify.txt` 9 类产物全部 `OK`；`config/email.yaml` 仅记录文件名，不进入备份正文。
4. 在隔离 `/tmp` 验证 supervisor control socket + `supervisorctl`：子进程 SIGKILL 后
   由 pid `4792` 自动重启为 `4795`，随后正常 shutdown。
5. 记录持久化事实：PID1 使用 `/etc/supervisor/conf.d/supervisord.conf`；`/etc` 位于容器
   writable layer，不是挂载卷；`/app/working` 才是持久 ext4。**supervisor 配置原样不持久。**

## 2. 生产现场边界

| 项 | 状态 |
|---|---|
| 生产代码 | 未改动 |
| 生产 data/ledger/报告/音频 | 未改动（仅备份读取） |
| 旧 Cron | 未暂停、未恢复 |
| supervisor | 未安装、未重启 PID1 |
| 邮件/真实告警 | 未发送 |
| main | 未合并 |

## 3. 上线前仍需单独批准

1. 按文件人工集成代码；禁止 `reset --hard`、`clean`、强制覆盖。
2. 为 `/etc/supervisor/conf.d` 设计持久化注入或明确接受容器重建后重装。
3. 增加控制面与 `scheduler`/`watchdog` programs，做一次受控 PID1 重启。
4. 暂停旧 daily Cron `a892335a`；periodic `7c4eb73d` 仅在切换窗口暂停并在首轮成功后恢复。
5. 确认 critical-only 告警实际可达，完成一次回滚演练，再批准单次日更切换。

## 4. 证据

- `docs/reviews/artifacts/R2B-BACKUP-AND-SUPERVISOR-DRYRUN-2026-10-26.json`
- `ops/r2b/backup_worktree.sh`（dry-run 默认、拒绝覆盖、restore 校验）
- 生产 `git status --porcelain`：71 项（8 tracked + 63 untracked）
