# R3 周期报告质量与播客交付门禁：验证报告

日期：2026-10-26  
分支：`r3/periodic-quality`  
结论：**质量门与交付门禁已按 R3 落地；W40 被正确拦截；正常样例通过；已用冻结口播稿真实生成新 MP3，未复用历史音频。**

## 1. 解决的问题

旧链路有三个真实漏洞：

1. 质量门校验的是“清洗前 draft”，清洗后才展示/发送，导致 fallback 标记、模板水文可能先通过再被删除。
2. 规则兜底章节被当作正常章节；W40 六章全部兜底、跨章重复、证据簇仅 1 个，仍可能进入发送流程。
3. 音频只检查 `tts ok`，没有把口播稿质量、MP3 非零/可读时长一起作为交付条件。

## 2. 代码改动

- `periodic_synthesis.py`：周期报告只取 `core + verified + new/updated + fact/evidence/source_url`
  事件，未核实内容不再填充章节；新增独立域名计数与 heat 分离。
- `periodic_editor_review.py`：新增规则兜底比例、章节过短、跨章重复、证据簇/独立来源不足、
  建议章节缺失等硬检查；缺失章节按“期望章节”判断，不再用实际章节 id 自证齐全。
- `generate_periodic_report.py`：先清洗成最终展示对象，再对该对象做质量门；
  fallback 统计显式传入；Phase4 在 `sendable=false` 时跳过播客与邮件；
  口播稿质量与音频校验进入 `delivery_allowed`。
- `report_sanitizer.py`：补上无方括号的 `判断标签：A` 行清洗（历史泄漏形式）。

## 3. 验证结果

| 用例 | 结果 |
|---|---|
| 真实 W40 报告 | `sendable=false`，命中 `chapter_too_short`、`fallback_or_template`、`insufficient_evidence`、`repeated_content` |
| 正常周报 fixture | `sendable=true`，`overall_quality=good`，无 high/medium |
| 缺失第 06 章 | `sendable=false`，明确报告 `章节缺失: 06` |
| 单证据簇 | 命中 `独立证据簇不足` 与 `独立来源链接不足` |
| 脏展示文本 | 清洗后无 event_id、判断标签、规则兜底标记，且仍通过正常门 |
| Phase4 全兜底章节 | `sendable=false`，`delivery_blockers=[quality_gate]`，无音频、无邮件 |
| 正常样例 Phase4 | `sendable=true`、`delivery_allowed=true`，报告 1,589 字、口播稿 3,098 字、**新合成音频 462.624s** |
| 邮件 dry-run | `ok=true`，HTML 4,762 字，附件 1 个 MP3，未真实发送 |
| 终稿内部标记 | event_id/判断标签/涉及事件/置信度/规则兜底/按事件密度排序 全部为 false |

单测：`tests/r3` **8/8** 通过（6 项质量门/USE + 2 项 TTS 适配器；测试源码已提交）；从干净 worktree 运行本分支全仓测试。

## 4. 交付证据哈希

- 报告 SHA256：`8dad8fddd80973558a8b6c72a753923b101667bf373f8569c317da9f71894c63`
- 口播稿 SHA256：`3e0ccbf8a6fdd09720c4efb3b22d9bec57682f370c80c961c45d54bad70bae12`
- 音频 SHA256：`7858491c0b9c7efb3de2ee59e4d9d4ad0ab3f0197c86dade572259abc4d3bb61`
- 邮件 HTML SHA256：`c83e926b71c65326faf37a583b12b8c9e8a4c0f7473b9e2ceeaebe6188a67832`

完整 JSON：`docs/reviews/artifacts/R3-FROZEN-DRYRUN-2026-10-26.json`。

## 5. 明确的阻塞与边界

- `edge-tts` 在本环境连接 Microsoft speech endpoint 返回 **HTTP 403**。已改用项目已有的
  SiliconFlow `FunAudioLLM/CosyVoice2-0.5B` 中文 TTS（复用现有 `siliconflow_api_key`，
  不新增第三方 Key），从冻结口播稿合成 **462.624 秒新 MP3**；音频哈希与口播稿哈希成对记录，
  `script_audio_match=true`。旧 W27 音频不再作为交付证据。
- TTS 分块按 800 字切分后以 ffmpeg concat 拼接；`+55%` 语速使 3,098 字口播稿落在
  5-8 分钟目标内；edge-tts 仅保留为不可达时的 fallback，不伪造成功。
- 报告与邮件均为 frozen fixture dry-run；收件人固定为 `review-test@example.invalid`，
  没有真实发送邮件，没有改生产报告/账本。
- 生产事件池当前普遍缺少 `verification_status=verified`，因此 R3 收紧后无法用未核实事件凑章节。
  这是有意的质量约束；生产周报恢复需要先恢复事实核验链路，不能用放宽门禁代替。
