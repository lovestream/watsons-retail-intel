# R2-B｜supervisor 控制面持久化部署与回滚草案

日期：2026-10-26  
状态：**隔离设计 + 模拟验证；未安装生产配置、未重启 PID1、未启停 Cron。**  
关联：`release/r2-cutover-candidate`、`ops/r2a/supervisor/`、`ops/r2b/supervisor_persistence/`

## 1. 已核实的事实

- PID1 是 `/usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf`。
- `/app/working` 是持久挂载；`/etc` 不是挂载卷，属于容器可写层。容器重建后
  `/etc/supervisor/conf.d/` 中手工写入的文件会丢失。
- 当前生产 `supervisord.conf` **没有** `[unix_http_server]`、`[rpcinterface]`、
  `[supervisorctl]`，所以 `/var/run/supervisor.sock` 不存在；这不是程序错误，是控制面
  尚未配置。不能把 `supervisorctl status` 的失败误报成 R2 程序失败。
- R2 的 1.1GB 业务备份与 7,979/7,979 恢复校验已完成，见
  `docs/reviews/artifacts/R2B-BACKUP-AND-SUPERVISOR-DRYRUN-2026-10-26.json`。本轮不重复
  拷贝，只核对既有证据。

## 2. 推荐方案：持久源文件 + 宿主只读 bind mount

持久源：

```
/app/working/projects/watsons-retail-intel/ops/r2b/supervisor_persistence/watsons-retail-intel-r2b.conf
```

NAS 容器/Compose 增加一条只读挂载：

```yaml
volumes:
  - /app/working/projects/watsons-retail-intel/ops/r2b/supervisor_persistence/watsons-retail-intel-r2b.conf:/etc/supervisor/conf.d/watsons-retail-intel-r2b.conf:ro
```

这样容器重建后，`supervisord` 启动时仍能从 `/etc/supervisor/conf.d/` 看到同一份
持久文件；不需要改写镜像 entrypoint，也不需要在 `/etc` 里保存唯一副本。

**重要边界**：如果 NAS 的 Compose 不能增加 volume，或宿主机实际挂载路径不是
`/app/working/projects/watsons-retail-intel`，此方案不成立。必须先在宿主机核对
`docker inspect` 的 Mounts，再决定是否采用；不能只凭容器内路径假设。

## 3. 安装与回滚

安装脚本默认 dry-run，不碰 supervisor：

```sh
ops/r2b/supervisor_persistence/install_config.sh
ops/r2b/supervisor_persistence/install_config.sh --root /tmp/persist-test --apply
```

生产文件复制仅在单独批准后执行：

```sh
ops/r2b/supervisor_persistence/install_config.sh --root / --apply
supervisorctl -s unix:///var/run/supervisor.sock reread
supervisorctl -s unix:///var/run/supervisor.sock update
```

回滚：

```sh
supervisorctl -s unix:///var/run/supervisor.sock stop watsons-r2a-scheduler watsons-r2a-watchdog
rm -f /etc/supervisor/conf.d/watsons-retail-intel-r2b.conf   # 仅移除挂载点/副本
supervisorctl -s unix:///var/run/supervisor.sock reread
supervisorctl -s unix:///var/run/supervisor.sock update
```

不删除台账、报告、备份；不改 Docker；不回滚业务数据。

## 4. 隔离验证结果

- 在临时 root 中安装配置，删除整个 `etc/` 后重装，文件 SHA256 前后一致：
  `1106ff1015ee3d67cbca76acb548a1e55b6ced98c7d493e841b068ec5c0289fd`。
- 用临时配置启动 `supervisord`，`[unix_http_server]` +
  `[rpcinterface]` 生效，`supervisorctl status` 能列出两个程序；说明控制面 section
  可随持久文件加载。程序命令在测试中被替换为 `/bin/sleep`，没有启动真实调度器。
- 生产只读检查：`/app/working` 是 mount，`/etc` 不是 mount；生产控制 socket 不存在；
  未安装、未重启。详见 `docs/reviews/artifacts/R2B-SUPERVISOR-PERSISTENCE-2026-10-26.json`。

## 5. 仍然需要的生产动作（逐项单独批准）

1. 在 NAS 宿主机核对容器 Mounts，确认持久源和 `/etc/supervisor/conf.d` 挂载点。
2. 修改 Compose/容器配置，增加只读 bind mount；不要直接编辑非持久 `/etc` 后宣称持久。
3. 经批准后复制/挂载配置，执行一次 `reread/update`；确认控制 socket 出现。
4. 经批准后做一次完整的 stop/start/rollback 演练，并确认两个程序真实 RUNNING、
   watchdog 告警可读；再进行 PID1 重启演练。
5. 旧 Agent Cron 暂停、真实 critical 告警测试、首次日更切换，均另行授权。

本设计不授权也不执行上述任何一项。
