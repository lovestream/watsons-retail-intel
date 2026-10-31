# PR #3 → PR #2 集成与隔离验收报告｜2026-10-31

> 授权范围：**仅集成到 PR #2 候选分支做整体隔离验收**。
> 不包含：合并 main、修改 Cron、发送生产邮件。
> 收件人：417379770@qq.com（人工指定，一次性发送，不改配置）

## 1. 事实包统计核对（程序计算，非手写）

此前报告中「10 条事实」与「reported 6 + announced 1 + proposed 4 = 11」相互矛盾，
经程序核对，**正确答案为 10 条**，旧报告中的 11 是错误数字，已在本报告中更正。

| 项目 | 值 |
|---|---|
| 冻结包文件 sha256 | `853695d0268c…` |
| 包内 pack_sha256 | `ca15e2c1d7f6…` |
| manifest pack_sha256 | `ca15e2c1d7f6…`（三者一致 ✅）|
| len(facts) | **10** |
| status Counter | reported 5 / announced 1 / proposed 4 = **10** ✅ |
| date_role Counter | observed 10 = **10** ✅ |
| fact_id 唯一性 | F001–F010，无重复 ✅ |

逐条明细：

| fact_id | status | date_role | participation | time flags |
|---|---|---|---|---|
| F001 | reported | observed | direct | has_vague_growth |
| F002 | reported | observed | direct | has_campaign_ref |
| F003 | reported | observed | listed_only | has_campaign_ref |
| F004 | announced | observed | direct | - |
| F005 | proposed | observed | direct | - |
| F006 | proposed | observed | direct | - |
| F007 | proposed | observed | direct | - |
| F008 | reported | observed | direct | - |
| F009 | proposed | observed | direct | - |
| F010 | reported | observed | direct | - |

> 复核脚本：`/tmp/verify_pack.py`，含一致性断言，不一致即非零退出。

## 2. 条件一：原 47 项测试补跑（已完成）

在 NAS 隔离环境实际执行，**89 个用例全通过，0 失败**：

| 测试文件 | 用例数 | 结果 |
|---|---|---|
| `test_pipeline_r0_r3.py` | 16 | PASS |
| `test_prompt_integration.py` | 23 | PASS |
| `test_production_delivery_gate.py` | 13 | PASS |
| `test_temporal_fact_validation.py` | 10 | PASS |
| `test_p09_bounded_review.py` | 9 | PASS |
| `reader_brief/run_tests.py` | 18 | PASS |
| **合计** | **89** | **0 失败** |

**修复了 2 项过时断言**：`test_pipeline_r0_r3.py` 中两处仍断言 Provider 为 `siliconflow`，
而 2026-10-30 起已切换为 RightCode 唯一 Provider，已更新断言（非代码缺陷）。

集成到 PR #2 分支后**再次全量重跑，仍 89/89 通过**。

## 3. 条件二：保留完整回退能力（已具备）

| 项目 | 值 |
|---|---|
| 目标分支 | `release/periodic-reader-brief-20261030`（PR #2）|
| 集成提交 | `318d73f`（双亲：`b2da0b6` + `7991a3d`）|
| **回退点** | `b2da0b6ec795b0f8c55d505383ae7c889ad34e98` |
| 回退方式 | `PATCH /git/refs/heads/release/periodic-reader-brief-20261030 {"sha":"b2da0b6…","force":true}` |
| 记录文件 | `/tmp/rollback_record.json` |

PR #3 分支 `review/reader-brief-evidence-gates-20261031@7991a3d` **原样保留**，未被删除。

## 4. 条件三：集成后重新生成并验证音频（已完成）

基于集成后的 `318d73f` 代码重新生成：

| 项目 | 值 |
|---|---|
| audio 路径 | `…/reader_brief/podcast-audio.mp3` |
| 大小 | 1,288,892 字节 |
| audio_sha256 | `85036d1eff9f…`（新生成，与旧版不同）|
| generation | `from_p09_final_brief` |
| html SHA 匹配 | ✅ |
| audio SHA 匹配 | ✅ |
| script SHA 匹配 | ✅ |
| source_brief SHA 匹配 | ✅ |
| P09 gate | approved |
| delivery_state / sendable / deliverable | approved / true / **true** |

事实包在集成后重建，sha 仍为 `853695d0…`（10 条，reported 5 / announced 1 / proposed 4），
证明集成未改变数据链路。

## 5. 安全门回归（篡改拒发）

在 `WATSONS_PERIODIC_MAIL_MODE=reader_brief` 下走真实发送入口校验分支：

| 用例 | 结果 |
|---|---|
| 篡改 HTML / MP3 / brief / manifest 音频SHA / P09 置 blocked / 删除 MP3 | **6/6 全部拒发** |
| 正向对照：未篡改 dry_run | 通过（附件 1 个 MP3）|

## 6. 授权边界确认

| 项目 | 状态 |
|---|---|
| 合并 main | ❌ 未做 |
| 修改 Cron | ❌ 未做 |
| 发送生产邮件 | ❌ 未做（本轮为一次性人工指定收件人发送验收报告）|
| 生产事件账本 | ❌ 未改 |
| PR #2 | 仍 Draft |
| PR #3 | 仍 Draft，分支保留 |

## 7. 遗留

1. 真人音频试听（用户表示音频一般没问题，但正式发布前仍建议听一遍）。
2. 上游 `event_occurred_at` 日级精度仅 24%，是周期报告素材质量的长期瓶颈。
3. 10-12～18 缺口对月报的影响分析未做。
