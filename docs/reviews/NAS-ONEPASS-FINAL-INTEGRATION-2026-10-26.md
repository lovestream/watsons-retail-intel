# NAS ｜ R2 + R3 + M2 一次性整合交付（D1–D5）

日期：2026-10-26（执行环境时间 2026-10-27）  
状态：**隔离集成候选已完成；生产代码、PID1、Cron、真实邮件/告警均未改动。**  
权威分支：`integration/onepass-20261026`  
代码候选 commit：`7590632`（本文件为该 commit 之后的交付说明）

## 0. 结论先行

- D1：R3/M2 测试源码已进入 Git，`.gitignore` 已修复；从干净 clone 跑 **71/71** 全过。
- D2：`edge-tts` 403 已绕过，使用项目已有 **SiliconFlow CosyVoice2** 真实生成新 MP3；
  **462.624 秒**，不是旧 W27 音频，不是“假装成功”。
- D3：SelectBench 扩到 **59 条**（40 条真实文章 URL + 19 条合成回归），字段不一致已修；
  **59/59**，但仍 **advisory**，`safe_to_enable=false`。
- D4：R2/R3/M2 已合入一个可测试候选；W32 隔离回放 344 条事件，**verified=0**，
  R3 严格门正确返回 `blocked_no_verified_core_events`，没有把旧池批量标 verified。
- D5：supervisor 持久化方案已落地为草案 + 脚本 + `/etc` 清空重装模拟；
  宿主 bind mount 尚需 NAS 宿主机核验和单独授权。

**不能宣称“生产已稳定生成高质量周报/播客”**：代码候选和 TTS 通道已通，但当前生产
事件池没有 `verified` 事实，周期报告被严格门拦截。这是数据核验缺口，不是用降级门或
伪标 verified 可以解决的问题。

## 1. 分支与提交

| 分支 | 作用 | 本地 HEAD（交付前） |
|---|---|---|
| `release/r2-cutover-candidate` | R2 部署/备份/回滚底座 | 既有 |
| `r3/periodic-quality` | R3 周期质量门与真实 TTS | `be44441` |
| `m2/aihot-adoption` | M2 预筛、来源产出、SelectBench | `7dd3fd0` |
| `integration/onepass-20261026` | **最终集成候选** | `7590632` |

集成分支已合入 R3、M2，并包含本报告、D5 持久化草案、D4 回放脚本。

## 2. D1｜测试源码、`.gitignore`、干净 clone

### 修复

`.gitignore` 原先使用递归 basename 模式 `test_*.py`，导致 `tests/**/test_*.py`
不进入 Git。已改为：

```gitignore
/test_*.py
```

即只忽略项目根目录的一次性测试脚本，`tests/` 下的正式测试全部可提交。

### 测试结果（新 clone，工作树干净）

命令：

```sh
git clone --branch integration/onepass-20261026 --single-branch . /tmp/onepass_clean
PYTHONPATH=".:/app/working/projects/watsons-retail-intel/.venv_packages:$PYTHONPATH" \
  /app/venv/bin/python3 -m unittest discover -s tests -v
```

结果：**Ran 71 tests — OK**。

| 测试集 | 数量 | 说明 |
|---|---:|---|
| R2-A | 53 | 影子调度、SQLite、watchdog、end-step 等 |
| R3 | 8 | 6 项质量/交付门禁 + 2 项 TTS 适配器 |
| M2 | 10 | 9 项原有 + 1 项 event-pool 字段映射回归 |
| **合计** | **71** | 0 fail / 0 error |

测试源码已实际存在于集成分支：
`tests/r2a/`、`tests/r3/test_periodic_quality_gate.py`、
`tests/r3/test_tts_provider.py`、`tests/m2/test_m2_adoption.py`。

## 3. D2｜中文 TTS 403 的真实解决

### 问题

`edge-tts` 连接 Microsoft speech endpoint 返回 HTTP 403；此前用 W27 历史 MP3 做 fixture，
只能验证附件链路，不能证明“本期文本生成了音频”。

### 方案

使用项目已授权的 **SiliconFlow `FunAudioLLM/CosyVoice2-0.5B`**：

- 复用现有环境变量 `siliconflow_api_key`，没有新增第三方 Key、没有新建付费系统；
- 800 字左右分块，ffmpeg concat；
- 语速梯度 `+55% / +70% / +85% / +40%`，使 3,000 字口播稿落在 5–8 分钟；
- `edge-tts` 仅作 fallback，不再作为主路径；
- 没有任何“复制旧 MP3”分支。

### 实跑证据

从干净 clone 重跑 `tests/r3/run_frozen_dryrun.py`：

- 口播稿：3,098 字；
- **新合成音频：462.624 秒**；
- 音频 7,402,412 bytes；
- SHA256：`7858491c0b9c7efb3de2ee59e4d9d4ad0ab3f0197c86dade572259abc4d3bb61`；
- `script_audio_match=true`，`delivery_allowed=true`；
- 邮件 dry-run 收件人：`review-test@example.invalid`，**未真实发送**；
- 报告 HTML 正文 4,762 字符，附件 1 个 MP3。

证据：

- `docs/reviews/artifacts/R3-REAL-TTS-2026-W40.mp3`
- `docs/reviews/artifacts/R3-FROZEN-DRYRUN-2026-10-26.json`
- `docs/reviews/NAS-R3-PERIODIC-QUALITY-2026-10-26.md`

**边界**：这是 W40 冻结优质样例稿，不是 W40 当前真实报告；W40 真实报告被质量门拦截，
不能用坏稿生成音频。生产新音频要等真实 `verified` 事件与可发送报告。

## 4. D3｜SelectBench 修正与扩容

### 修复

- 每行同时输出 `source_url` 与 `evidence_url`；缺失时显式映射，不再出现字段不一致。
- `evidence_strength` 区分 `article_url` / `homepage` / `listing_or_unknown`。
- `real_source` 与 `fixture_synthetic` 分开统计。
- 新增过期公告、财务/IPO、跨平台联动、海外品牌中国市场正例。
- 新增 event-pool 适配：`event_title`/`fact` 映射后再预筛，避免“事件全空 → 全 BLOCK”。

### 数据与结果

- 总样本：**59**
- 真实文章 URL：**40**
- 已逐条重新核验的真实样本：**0**（诚实标记）
- 合成/首页回归：19
- 结果：**59/59**，`false_block=0`，`false_pass=0`，`traceability_ok=true`
- 旧规则同一样本：**52/59**（6 个财务/IPO/旧公告漏网）
- `safe_to_enable=false`：要求至少 40 条**真实且已核验**样本，当前不满足。

结论：这是 **advisory** benchmark，不是生产精度证明，也不直接上线预筛硬门。

证据：

- `tests/m2/fixtures/selectbench.jsonl`
- `docs/reviews/artifacts/M2-SELECTBENCH-2026-10-26.json`
- `docs/reviews/NAS-M2-AIHOT-ADOPTION-2026-10-26.md`

## 5. D4｜R2 + R3 + M2 集成与同窗回放

### 集成

- `integration/onepass-20261026` 合入 R3、M2，保留 R2 候选代码。
- 干净 clone 71/71 通过。
- R3 真实样例 Phase4 + 真实 TTS + 邮件 dry-run 通过（见 D2）。
- 未合并到 `main`，未覆盖生产，未写正式账本。

### W32 隔离回放

脚本：`ops/r2b/e2e_replay.py`  
窗口：`2026-08-03` ~ `2026-08-09`  
方式：生产快照复制到临时 project root，只读；不写生产。

结果：

- 原始事件：**344**
- M2：PASS 219 / UNKNOWN 110 / BLOCK 15
- 核验重跑：`verified=0`，`needs_verification=344`
- 主要原因：`event_date_missing` 230、`event_date_missing_published_fallback` 114、
  `event_date_not_explicit` 114；少量传闻标记
- R3 候选状态：`blocked_no_verified_core_events`
- 集群数：0；覆盖 7 天均为 healthy，但无可报告 verified 事件

这说明两件事：

1. 现有旧池不能靠批量标 `verified` 生成周报；会污染正式报告。
2. 需要修复“从原始证据提取/核验事件日期”的链路，或在隔离回放中逐条复核；
   当前严格门拦截是正确行为。

证据：`docs/reviews/artifacts/ONEPASS-E2E-REPLAY-2026-10-26.json`

## 6. D5｜supervisor 持久化

设计文档：`docs/reviews/NAS-SUPERVISOR-PERSISTENCE-DESIGN-2026-10-26.md`

### 已核实

- PID1：`/usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf`
- `/app/working` 是持久挂载；`/etc` 是容器可写层，非持久。
- 生产 `supervisord.conf` 尚无控制 socket；`supervisorctl status` 失败是控制面未配置，
  不是 R2 程序失败。

### 推荐方案

持久配置源放在：

```
/app/working/projects/watsons-retail-intel/ops/r2b/supervisor_persistence/watsons-retail-intel-r2b.conf
```

NAS 容器增加只读 bind mount：

```
.../watsons-retail-intel-r2b.conf:/etc/supervisor/conf.d/watsons-retail-intel-r2b.conf:ro
```

容器更新/重建后挂载自动恢复；不需要把唯一副本写进非持久 `/etc`。

### 隔离验证

- 临时 root 安装 → 删除整个 `etc/` → 重装：SHA256 前后一致
  `1106ff1015ee3d67cbca76acb548a1e55b6ced98c7d493e841b068ec5c0289fd`。
- 临时 `supervisord` 配置下 `[unix_http_server]`/`[rpcinterface]` 生效，
  `supervisorctl status` 可列出两个程序；程序命令替换为 `/bin/sleep`，没有启动真实调度器。
- 生产未安装、未重启、未改 Cron。
- 既有 1.1GB 备份：7,979/7,979 恢复通过，`business-data.tar.gz` 实际 SHA256 与
  `verify.txt` 一致；本轮未重复拷贝。

证据：`docs/reviews/artifacts/R2B-SUPERVISOR-PERSISTENCE-2026-10-26.json`

## 7. 仍然阻塞 / 需要授权的事项

### 7.1 生产周期报告的数据阻塞

当前生产 W32 快照 344 条事件中 0 条 verified。要真正生成 R3 周报，必须：

1. 在隔离回放/已备份快照中，从原始文章重新提取事件日期与证据；
2. 逐条人工复核或接入独立核验；
3. 缺日期/证据的保留 `needs_verification`/`reference`，不进入报告核心；
4. 不得批量把历史事件标 verified。

这一步尚未完成，因此不能宣称“生产已能稳定生成高质量经营报告”。

### 7.2 生产动作（全部未做，逐项授权）

- 覆盖生产代码；
- 安装 supervisor 持久配置 / 增加宿主 bind mount；
- 重启 PID1 或 stop/start supervisor 程序；
- 暂停/切换正式 Cron；
- 真实邮件、真实 critical 告警；
- 首次受控日更/周更切换。

### 7.3 部署前仍需确认

- NAS 宿主机 `docker inspect` 的 `/app/working` 真实源路径；
- Compose 是否能增加只读 volume；
- 生产控制 socket 配置后的一次受控重启与回滚演练；
- 真实报告/音频/邮件的人工验收。

## 8. 一句话交付判断

**隔离候选可测试、可回放、可审计；生产切换和真实高质量周期报告尚未完成，原因是事件核验
数据缺口与生产授权，而不是把门禁降级或伪造音频。**
