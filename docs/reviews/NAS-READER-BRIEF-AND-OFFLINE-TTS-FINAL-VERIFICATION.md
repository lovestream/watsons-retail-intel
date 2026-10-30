# 读者型周期简报 + 事实质量门：最终验证报告（2026-10-29）

**范围**：按 `docs/15_20261029_periodic_email_editorial_system_and_free_tts.md` 实现"可信事实包 → 独立经营编辑 → 结构化简报 JSON → 事实质量审查 → 手机 HTML"链路，并用真实数据重生成周报/月报/年报邮件。
**离线 TTS**：按用户 2026-10-29 指示 **不做**（sherpa-onnx / AISHELL3 / Melo 全部未安装、未测试）；试听音频改用既有免费 edge-tts（7.3.1）。
**未做**：未改生产 Cron、未动 PID1、未改正式事件账本、未发送任何真实邮件。

---

## 1. 为什么重做而不是修模板

旧链路（R3 移动简报）把完整 Markdown 报告机械切句成卡片：同一段结论被截断成多张卡、数字脱离上下文、竞对段落与"下一工作周关注点"模板硬拼。读者看到的是"报告碎片"，不是"经营简报"。

新链路的关键差异是**编辑层看不到长报告**。它只拿到事实包（每条带原文句、来源链接、日期、状态），因此：

- 不可能把上一版报告里的判断当"事实"复述（这是旧链路里"过度确定判断"的直接来源）；
- 每条判断必须挂 `fact_ids`，没有事实支撑的句子进不了终稿；
- 数字必须能在被引用事实原文中找到，找不到就被审查门拦下。

## 2. 实现（`skills/reader_brief/`）

| 模块 | 职责 | 关键约束 |
|---|---|---|
| `facts_pack.py` | 构建事实包 | 两种来源：`periodic_synthesis.json` 的 clusters（facts/source_urls）或事件池 `events_analyzed.json`（按日期区间聚合）。每条事实带 `fact_id/text/date/status/source.url`；无原文或无链接不入包；入包前剥离 event_id、优先级、置信度 |
| `editor.py` | 经营编辑层 | 三种周期三套骨架（周报=变化/竞对/动作；月报=持续趋势/取舍/动作；年报=结构/决策/动作）；强制 JSON；硬规则含"数字必须来自事实""proposed 不得写成已落地""不得写内部编号/评分""期限不得过期" |
| `claim_checker.py` | 事实质量门 | 8 类检查：schema 与条目上限、fact_id 存在性、**数字绑定**、状态一致、**期限失效**、越权指令（未在事实中的折扣/预算/上线/限购）、内部标记泄漏（event_id/P0/置信度/附录/标签）、过度确定词（警告级） |
| `render_mobile.py` | 手机 HTML | 单列卡片、无表格、`max-width:640px`、`100%` 自适应；来源做成行内短链（≤2 条）；动作卡不再重复正文；状态徽标只出现在事实类卡片 |
| `run_brief.py` | 编排 | 事实包 → 编辑 → 审查 → **失败回灌修订（最多 2 轮）** → 渲染；LLM 全挂时默认拒绝交付（`--allow-fallback` 才降级，且 HTML 页脚明示"仅陈述事实"） |
| `rerender.py` | 免 LLM 改版式 | 用已存 brief JSON 重渲染，改样式不重烧 LLM |

**模型兜底**：主池（OpenCode 免费池）本次实测持续返回 `HTTP 400 Model is unavailable`，因此编辑层在失败后自动走 **SiliconFlow 文本模型 `deepseek-ai/DeepSeek-V4.1-Flash`**（仅文本；与任何语音付费无关）。三份简报均由该兜底生成，`_meta.provider=siliconflow_text` 记录在产物 JSON 里，可审计。

## 3. 真实数据重生成结果

| 报告 | 数据来源 | 事实 | 条目 | 审查 | 修订轮 | 渲染 | 来源短链 |
|---|---|---|---|---|---|---|---|
| 周报 2026-W43（10/19–10/25） | 真实周度合成（13 条已核验事件） | 13 | 11 | **通过**（0 错 0 警） | 1 轮 | 5,903 字符 | 有 |
| 月报 2026-07 | 真实 7 月事件池（28 天，100 条去重事实） | 100 | 12 | **通过**（0 错 1 警） | 0 轮 | 8,387 字符 | 有 |
| 年报 2026 年初至今（1/1–9/30） | 真实事件池（115 天，60 条事实） | 60 | 10 | **通过**（0 错 1 警） | 0 轮 | 6,857 字符 | 有 |

三份产物：`docs/reviews/email-ux/2026-10-29/{weekly-2026-W43,monthly-2026-07,yearly-2026-ytd}.html`，逐份 `check` / `revision_rounds` / `html_sha256` 见 `manifest.json`。

**内容层面的修正（对旧版的具体纠错）**
- 周报不再出现"平台已确认"式表述：媒体来源一律写"据报道"，状态徽标区分"已发生/已宣布/媒体报道/拟推进"。
- 月报不再把单日爆点当月度趋势：4 条 trend 均为跨多日、多来源可复现的结构变化（仓店密度竞赛、美妆成为平台战略品类、补贴退坡、履约成本约束）。
- 年报用"结构—决策—动作"，与周/月骨架无复用：结构讲渠道权力与履约半径，决策讲"不定这件事的代价"，动作只留 2 条。
- 过期期限：审查门对 `actions/watch` 里的绝对日期做比对（今天=2026-10-29），过期即报错；三份终稿只剩相对时间窗（本周内/下周/下月/Q1）。

## 4. 手机阅读验收（真实渲染，不是只看 CSS）

用本地 chromium（Playwright）在 360/375/390/600px、DPR=2 下整页截图，并读取 `scrollWidth - innerWidth`：

| 报告 | 360px | 375px | 390px | 600px |
|---|---|---|---|---|
| 周报 W43 | 溢出 0px / 3,458px 高 | 0px / 3,282px | 0px / 3,106px | 0px / 2,561px |
| 月报 2026-07 | 0px / 3,801px | 0px / 3,701px | 0px / 3,569px | 0px / 2,867px |
| 年报 YTD | 0px / 3,831px | 0px / 3,730px | 0px / 3,655px | 0px / 2,681px |

截图 12 张：`docs/reviews/email-ux/2026-10-29/screenshots/`。

按 375px 截图逐屏自查后修掉的真实缺陷（都是"手机上读起来别扭"，不是 CSS 指标）：
1. H1 曾直接截断 `lead[:40]`，出现"…品牌投"这种半句 → 改为周期标签做标题，结论只出现在结论卡；
2. 动作卡标题重复正文（加粗句+同段文字）→ 标题改"动作 1/2/3"；
3. 观察项卡片重复 item → 只保留"看什么"；
4. 动作/观察项不该带"已发生/媒体报道"状态徽标（它们不是事件）→ 关闭；
5. 状态判定顺序错误："据报道…将…"曾被标成"拟推进" → 先判已发生，再判媒体披露前缀。

## 5. 测试

`tests/reader_brief/test_reader_brief.py` + `run_tests.py`（本机无 pytest，用同语义的最小运行器）：

```
10 passed, 0 failed, 10 total
```

覆盖：状态判定顺序、合成结果入包与来源绑定、正常稿通过、未知 fact_id、数字不在事实中、过期期限、状态越级、event_id 泄漏、渲染无表格/无内部标记/含来源链接、周期骨架互不复用。

## 6. 试听音频（edge-tts，非离线 TTS）

- 口播稿由 brief JSON 生成（`scripts/brief_to_podcast.py`）：1,286 字，结论→三个变化→竞对→三件事→下周观察。
- 音频：`docs/reviews/email-ux/2026-10-29/weekly-2026-W43.mp3`（1,700,640 字节，`zh-CN-XiaoxiaoNeural`）。本机无 `ffprobe`/`mutagen`，时长按语速估算约 4.5–5 分钟；播放器可直接播放验证。
- 付费语音（SiliconFlow CosyVoice）依然不在链路任何位置。

## 7. 代码与提交

- 分支 `feat/reader-brief`（基于 `origin/main` aa73cba）。
- 新增：`skills/reader_brief/{facts_pack,editor,claim_checker,render_mobile,run_brief,rerender}.py`、`scripts/{shoot_reader_brief,refresh_reader_brief_packs,brief_to_podcast}.py`、`tests/reader_brief/*`、本报告与 `email-ux/2026-10-29/*`。
- 同轮已推送主线修复：`edge-tts 7.2.8 → 7.3.1`（WSS 403 根因）、`pip_ensure` 版本自愈、TTS 重试与空音频拦截（commit `6b639e7`，main）。

## 8. 仍未做 / 需要单独授权

1. 生产切换：把 `send_periodic_report_email` 的移动简报渲染换成 `skills/reader_brief`（需改生产代码路径 + 幂等/回滚演练）。
2. 真实发送：向 `123399974@qq.com` 发送这三份邮件（本轮只产出 HTML/MP3，未发）。
3. 事件池补到 10 月：生产 `data/events` 目前到 2026-09-30，周报 W43 用的是真实周度合成数据；若要让生产链路自己产 W43，需要先补 10/19–10/25 事件。
4. 文本 LLM 主池：OpenCode 免费池当前不可用，是否把 SiliconFlow 文本模型设为编辑层的**声明式**第一顺位（而不是失败后兜底），需要业务确认（涉及费用口径）。
5. 离线 TTS：按指示不做。若日后要做，`docs/15` 里的 sherpa-onnx + AISHELL3/Melo 方案与压测项原样保留。
