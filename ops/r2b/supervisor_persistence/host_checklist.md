# NAS 宿主核验清单（只读）

在 NAS 宿主机执行，不要先改 Compose：

```sh
docker ps --format '{{.Names}} {{.Image}}'
docker inspect <container> --format '{{json .Mounts}}' | jq .
docker inspect <container> --format '{{.HostConfig.Binds}}'
```

核对：

- `/app/working` 对应的宿主机目录存在且是持久卷；
- `/app/working/projects/watsons-retail-intel/ops/r2b/supervisor_persistence/watsons-retail-intel-r2b.conf` 存在；
- 计划挂载目标为 `/etc/supervisor/conf.d/watsons-retail-intel-r2b.conf`，只读；
- 容器内 `/etc` 不是独立持久卷（当前事实）；
- 修改 Compose 前保存原文件与 `docker inspect` 输出。

容器内只读核验：

```sh
ops/r2b/supervisor_persistence/verify_persistence.sh
```

通过标准：`/app/working is a mount`、持久源存在、控制 socket 可访问（在批准安装后）。
任何一项不满足都停止，不得用“手工写 /etc”替代持久化方案。
