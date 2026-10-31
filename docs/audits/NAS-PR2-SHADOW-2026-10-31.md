# PR #2 W43 真实数据 Shadow 验收报告｜2026-10-31

> 环境：隔离副本 `/tmp/wrb_shadow`（PR#2 分支 c1f9f9d→b2da0b6 + 生产 586 条真实事件）。
> 约束遵守：PR 保持 Draft、reader-brief hold、未合并、未改 Cron、未发送任何邮件。

## 1. 验收范围

W43（2026-10-19～25）真实周报：reader_brief 全链路（periodic_synthesis → P05 选题 → 编辑 → P09 审稿 → 移动端 HTML → MP3）。

## 2. 通过项

| 项目 | 结果 |
|---|---|
| 预检 production_preflight | blocked=[]（事件最新 10-30、59 条分析） |
| 全链路跑通 | EXIT=0，HTML+manifest 落盘 |
| P09 两轮审稿收敛 | 12 问题/6 major → 5 问题/1 major → approved |
| delivery_state | approved，sendable=true |
| 移动端渲染 | 360/375/390/600px 全部 0 横向溢出 |
| 标记泄漏 | event_id/置信度/【A】/var(--/已核验 全部 0 |
| 正文质量 | 标题具体；"据媒体报道"归属；外推改条件句；行动含同口径指标 |
| MP3 | 1.09MB 生成成功（edge-tts） |

## 3. 本轮发现并已修复（提交 b2da0b6）

1. **跨主体因果外推**（啤酒案例→屈臣氏结论）→ 提示词 7b/7c 硬约束。
2. **competitor_cards 缺 claim_ids** → 模板补字段 + 完整性硬要求。
3. **omitted_fact_ids 22 条被硬断**（上限 12）→ 24 条软上限；取舍说明与全量统计分离。

## 4. 遗留阻断项（合并前必须解决）

| # | 阻断 | 证据 | 建议 |
|---|---|---|---|
| 1 | **跨轮收敛不稳定**：3 次重跑分别 5/“已落地”错标/10 个问题 | status_overclaim、occur date 错标、单店口径外推 | 编辑提示词补"日期=发文日≠事件发生日""单店口径禁止额外限定"；或 P09 三轮上限 |
| 2 | **音频门未在 shadow 打通** | manifest.audio.path=''（本次 MP3 为旁路生成，未回写 manifest） | stage_reader_brief 集成 audio_only 分支 |
| 3 | **无正式 Gmail/Outlook 客户端渲染验证** | 无真实账户 | 生产侧人工验收 |
| 4 | LLM 输出随机性 | 同输入不同轮次不同问题集 | temperature 已 0；需更多轮次统计通过率 |
| 5 | PR 分支模型名曾为已停用 SF | 已在 shadow 手工对齐 RightCode | PR 分支需合入 RightCode 修复（或合并时以 main 为准） |

## 5. 验证资产

- HTML: `data/runs/periodic/weekly/2026-W43/reader_brief/brief.html`
- Manifest/P09/Pack: 同目录（sha256 已记录）
- 截图: /tmp/w43_{360,375,390,600}.png
- MP3: `weekly-2026-W43.mp3` (1.09MB)
- 日志: /tmp/shadow_w43.log / _w43b / _w43c
