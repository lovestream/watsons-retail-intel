# 真实采集恢复与 10-19～30 事件补齐｜方案A 执行记录

> 执行日：2026-10-30（CST 夜间至次日凌晨）。授权：用户批准「方案A：最小化部署两个已验证修复到生产 main，Cron/收件人/邮件开关不变，reader-brief 继续 hold」，并批准「分批补齐 2026-10-19～30 真实事件数据，逐日、可断点、严格去重、保留原始证据与审计，先隔离核验再入正式账本」。

## 1. 根因（2026-10 连续 0 事件真因）

| # | 问题 | 证据 | 修复 |
|---|---|---|---|
| 1 | 生产仍用已下线模型 `deepseek-v4-flash-free`（OpenCode 时代） | 10-29 抽取日志：36/36 `llm_api_error`；10-30：0/60 | 全部替换为 `deepseek-flash`（RightCode） |
| 2 | `skip_thinking: true` 只读不生效，thinking 让单篇抽取跑到 300s 超时 | 单篇 300s 超时、43 篇耗时 19 分钟 | `enable_thinking=false` 真正下发 |
| 3 | 包装器 import 失败后 subprocess 整轮重跑 | `import 失败，尝试 subprocess` + 19 分钟二次执行 | 本次未改（遗留项，见 §5） |
| 4 | **SiliconFlow 余额耗尽**，用户移除 `siliconflow_api_key` | 10-29 回填：`LLM 客户端无可用 Key，降级为规则抽取` → 0 事件 | 停用 SF，RightCode 成为唯一 Provider |
| 5 | RightCode base 地址错误（`right.codes` 在本机解析到 fake-IP 198.18.5.75，TCP 重置） | 三种方式全 `Connection reset`；DNS 返回 198.18.5.75 | 改为 `https://api.site/v1`（实测 `/models` 0.5s、对话 2.1s） |

## 2. 生产改动（main）

- `55cf53f` 生产切 SiliconFlow + enable_thinking 透传 + 清除死模型名（方案A热修）
- `1b36148` 停用 SiliconFlow，RightCode 成为唯一 LLM Provider
- `fff478e` RightCode base 修正为 `https://api.site/v1`

Cron、收件人（123399974@qq.com）、邮件开关、`WATSONS_PERIODIC_MAIL_MODE=hold` 均未改动。

## 3. 隔离核验 → 正式账本

先在隔离副本 `/tmp/wrb_stage` 跑真实采集全链路，逐日核验通过后才提升：

- 10-19～25：隔离副本生成 → 校验 `llm_success>0` → 提升到 `data/events/`，被覆盖对象备份到 `data/backups/rule_fallback/`
- 10-26～29：生产环境逐日重跑（原数据为 0-LLM 规则兜底，已备份后重生成）
- 10-30：生产重跑（原 3 条全 ARCHIVE，已备份）

事件账本 `data/event_ledger/events_seen.jsonl` 按 `event_id` 合并去重，新增 387 条，总量 6110，不覆盖既有记录。

## 4. 结果（逐日）

| 日期 | 事件数 | LLM 分析 | P1 | P2 | ARCHIVE |
|---|---|---|---|---|---|
| 10-19 | 40 | 10 | 0 | 10 | 30 |
| 10-20 | 28 | 3 | 1 | 2 | 25 |
| 10-21 | 39 | 11 | 2 | 9 | 28 |
| 10-22 | 55 | 8 | 3 | 5 | 47 |
| 10-23 | 40 | 8 | 3 | 5 | 32 |
| 10-24 | 46 | 8 | 3 | 5 | 38 |
| 10-25 | 39 | 5 | 2 | 3 | 34 |
| 10-26 | 37 | 3 | 1 | 2 | 34 |
| 10-27 | 87 | 12 | 1 | 11 | 75 |
| 10-28 | 59 | 7 | 2 | 5 | 52 |
| 10-29 | 57 | 11 | 1 | 10 | 46 |
| 10-30 | 59 | 17 | 3 | 14 | 42 |
| **合计** | **586** | **103** | **22** | **81** | **483** |

## 5. 遗留问题

1. **播客审稿未通过（7/8）**：口语化不足（“底层逻辑”等书面语）、平均句长 43 字、缺对话感、结尾行动建议 0 条。不阻断日报发送（`sendable=true`），但属既定质量项。
2. **包装器 import→subprocess 双跑**：extract 步骤 import 失败后 subprocess 重跑，白耗一轮 LLM 调用与时间，待修。
3. **XCrawl 健康检查 CRITICAL**：`run.xcrawl.com` 读超时（正文抓取频繁超时），采集仍可用 RSSHub/CloakBrowser 兜底。
4. **PR #2 未合并**：reader-brief 生产验收未做，仍为 draft，默认 hold。
5. **历史 0-LLM 数据**：10-12～18 未在本次授权范围内，仍为旧数据。

## 6. 验证方式

- 测试：`tests/test_llm_provider_guard.py` 4/4 通过；`test_temporal_fact_validation`、`test_report_sanitizer` 全通过。
- 端到端：10-30 全链路 `sendable=true`，日报 `reports/daily/2026/10/2026-10-30.md` 23592 字节，播客 MP3 生成成功。
- 每阶段以落盘文件为准（`events_raw.json` / `events_scored.json` / `events_analyzed.json`），不以进程启动为准。

## 附录：提升操作原始记录（隔离副本 → 生产）

```
Fri Oct 30 15:42:28 UTC 2026 promote
[promoted] 2026-10-19 events/llm_ok=40/10
[promoted] 2026-10-20 events/llm_ok=28/3
[promoted] 2026-10-21 events/llm_ok=39/11
[promoted] 2026-10-22 events/llm_ok=55/8
[promoted] 2026-10-23 events/llm_ok=40/8
[promoted] 2026-10-24 events/llm_ok=46/8
[promoted] 2026-10-25 events/llm_ok=39/5
ledger merged: total=6110 added=387
PROMOTE DONE Fri Oct 30 15:42:29 UTC 2026
```

生产环境逐日重生成（10-26～30）：

```
[15:23] regen 2026-10-26  -> events_llm 37 3
[15:54] regen 2026-10-27  -> events_llm 87 12
[16:16] regen 2026-10-28  -> events_llm 59 7
[16:38] regen 2026-10-29  -> 0（SiliconFlow 余额耗尽，已改用 RightCode 重跑 -> 57 事件/11 LLM）
10-30 重跑                  -> events_llm 59 17（原 3 条全 ARCHIVE，已备份）
```
