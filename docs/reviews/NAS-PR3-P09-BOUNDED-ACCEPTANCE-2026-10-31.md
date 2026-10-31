# PR #3 方案B（P09 有限审稿）NAS 验收报告｜2026-10-31

> 验收对象：PR #3 `80f51ab`（Draft，未合并）
> 环境：NAS 隔离副本 `/tmp/pr3b`，冻结 W43 事实包 `853695d0…`（10 条真实事实）
> 约束遵守：PR #2/#3 保持 Draft、正式邮件 hold、未改 Cron、未发信

## 1. 结论

**方案 B 达到 9/10 验收标准，且未发现重大错误被错误批准。**

| 指标 | 结果 | 门槛 | 判定 |
|---|---|---|---|
| 10 轮通过数 | **9/10** | ≥9 | ✅ |
| 通过率 | **0.90** | ≥0.90 | ✅ |
| 重大错误误放行 | **0** | 0 | ✅ |
| P09 调用轮次 | 全部 1 轮 | ≤2 | ✅ |
| 内容版本数 | 9（非同一稿重复）| — | ✅ |

## 2. 测试结果（NAS 隔离环境实测）

| 测试 | 结果 |
|---|---|
| `tests/test_p09_bounded_review.py`（方案B新增 9 用例） | **9/9 PASS** |
| `tests/reader_brief/test_reader_brief.py` | **18/18 PASS** |
| `tests/test_production_delivery_gate.py` | PASS |
| `tests/test_temporal_fact_validation.py` | PASS |

## 3. 10 轮稳定性逐轮明细

| 轮 | 状态 | P09 轮次 | 建议级问题数 | brief_sha（前8位） |
|---|---|---|---|---|
| 1 | approved | 1 | 1 | feaf1451 |
| 2 | approved | 1 | 4 | 4d57d7e1 |
| 3 | approved | 1 | 1 | 77e7c2a9 |
| 4 | approved | 1 | 4 | b20a57e2 |
| 5 | approved | 1 | 1 | 945b9167 |
| 6 | **失败（确定性门）** | — | — | — |
| 7 | approved | 1 | 2 | 7502d1d6 |
| 8 | approved | 1 | 3 | bfdf3912 |
| 9 | approved | 1 | 2 | 6495e84b |
| 10 | approved | 1 | 2 | ad047762 |

**唯一失败轮（run_06）** 被确定性门拦下，错误为：
`status_overclaim: signals[2] 未落地事项被写成已落地：属计划性信息，非已完成全量覆盖`
→ 这是**真实实质错误被正确拦截**，不是误报。

## 4. 零误放行验证（针对 9 轮 approved 稿）

| 检查项 | 结果 |
|---|---|
| 引用事实状态为 proposed/reported 却写成"已上线/已完成/已落地" | **0 处** |
| 引用不存在的 fact_id | **0 处** |
| 内部标记泄漏（event_id/置信度/【A】） | **0 处** |
| 屈臣氏被写成实际参与方（排除"未明确参与"等谨慎表述） | **0 处** |

## 5. 音频一致性（P09 最终稿同步生成）

| 项目 | 结果 |
|---|---|
| 音频路径 | `…/reader_brief/podcast-audio.mp3` |
| 大小 | 1,712,396 字节 |
| audio_sha256 与 manifest 一致 | ✅ |
| html_sha256 与 manifest 一致 | ✅ |
| generation | `from_p09_final_brief`（外部音频旁路已禁用） |
| manifest | delivery_state=approved / sendable=true / deliverable=true |

`scripts/stage_reader_brief.py` 已拒绝 `--audio-file` 旁路参数：
`--audio-file 旁路音频已禁用：本命令只能从P09最终稿同步生成MP3`

## 6. 文件篡改拒发测试（6/6 拦截）

在 `WATSONS_PERIODIC_MAIL_MODE=reader_brief` 下走**真实发送入口的校验分支**（不触 SMTP）：

| 用例 | 结果 | 错误码 |
|---|---|---|
| 篡改 HTML | 拒发 ✅ | reader_brief_approval_or_hash_mismatch |
| 篡改 MP3 字节 | 拒发 ✅ | 同上 |
| 篡改 reader_brief.json | 拒发 ✅ | 同上 |
| 篡改 manifest 音频 SHA | 拒发 ✅ | 同上 |
| P09 门置为 blocked | 拒发 ✅ | 同上 |
| 删除 MP3 | 拒发 ✅ | 同上 |
| **正向对照：未篡改** | **通过** ✅ | dry_run 正常，附件 1 个 MP3 |

> 说明：默认 hold 模式下所有用例都会以 `periodic_delivery_disabled` 拒发，
> 那不是校验生效。本报告的结果是**显式启用 reader_brief 模式后**取得的，
> 确保测到的是 SHA/状态校验本身。

## 7. 方案 B 行为验证

- **实质错误硬阻断**：run_06 的 status_overclaim 被拦，未被"建议化"放过。
- **非实质措辞仅建议**：9 轮通过稿中每轮 1–4 条建议级问题（如"原文无'名单'一词"），全部不阻断。
- **P09 最多两次**：10 轮全部 1 轮结束，未出现反复迭代至偶然通过。
- **重大问题证据不足时 hold**：由 9 个单测覆盖（`test_missing_source_quote_is_hold_not_advisory` 等）。

## 8. 未完成 / 边界

- 本轮为**文本+音频的隔离验收**，未做真实 SMTP 投递（按要求 hold）。
- 未做真人音频试听。
- 原 47 项测试未逐项重跑（本轮跑了 4 个核心测试文件，共 30+ 用例）。
- 10-12～18 缺口对月报的影响分析仍未做。
- P09 通过率不等于事实正确率；本次零误放行结论基于对 9 份 approved 稿的
  针对性核查（状态/数字/事实ID/标记泄漏），非全量语义复核。

## 9. 建议下一步

1. 人工试听 `podcast-audio.mp3`（真人主观听感是唯一未覆盖项）。
2. 若接受，可将 PR #3 合入 PR #2 后统一验收；合并与生产切换仍需单独授权。
3. 排查上游 `event_occurred_at` 日级精度（当前 24%），这是周期报告素材质量的长期瓶颈。
