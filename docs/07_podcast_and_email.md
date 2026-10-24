# 07 播客与邮件送达子系统

> 只读审计草稿 · 审计日期：2026-10-24 · 审计方式：仅读文件与真实日志，**未运行 git、未发送邮件、未改动仓库**。commit 号按既有记录引用（d823682 / 94ee35f 已在第 06 篇文档的 commit 表中核对），个别按任务提示引用的 commit（caef4cb / 78ba300 / e6550c0）因仓库内无正文可供 grep 验证，已在正文注明"未能独立核对"。

- 子系统覆盖：`skills/generate_podcast/`（口播稿 + TTS）、`skills/podcast_review/`（播客质检）、`skills/send_daily_report_email/`（日报/周期邮件送达）、`skills/send_admin_alert/`（告警）、`config/email.yaml`、`/app/working/workspaces/default/skills/email/mailcli.py`（SMTP 发送工具）。
- 编排位置（`skills/run_daily_pipeline/run_daily_pipeline.py` 第 9–16、93–116 行）：`generate_podcast → podcast_review → send_daily_report_email`；播客与日报并行（方案 C，从事件池直接生成），播客失败不阻塞日报。

---

## 1. 播客规格：口播稿

### 1.1 定位与两条路线（SKILL.md 与代码不一致）

- `skills/generate_podcast/SKILL.md` 描述的是**旧的"日报转口播"路线**：朗读对象是最终日报 `reports/daily/YYYY/MM/YYYY-MM-DD.md`，字数 **1800–2500 中文**（无信号 200–300）。
- `skills/generate_podcast/generate_podcast.py` 头部 docstring 描述的是**当前"方案 C / 事件池路线"**：直接从 `data/events/{date}/events_analyzed.json` 读取事件（含 `business_analysis`），与日报并行、互不依赖，口播稿 **3000–5000 中文字符（约 5–8 分钟）**，无信号时生成极简版。

> **审计观察**：SKILL.md 与代码是两套字数规格（1800–2500 vs 3000–5000），SKILL.md 明显滞后于方案 C。任务描述中的"5–8 分钟/3000–5000 字/每事件 5 维度"以**当前代码**为准。

### 1.2 业务顾问风格 + 纯文本无 Markdown

- 系统提示 `LLM_SYSTEM_PROMPT`（`generate_podcast.py`）明确：主播是"业务顾问式的深度解读，不是新闻播报"，听众是屈臣氏中高层管理者；**所有内容必须基于提供的事件数据，不得编造事实/数据/事件**；**禁止出现任何 Markdown 符号**（**加粗**、# 标题、--- 分隔线、- 列表）、**禁止 event_id**、禁止"据报道/有消息称"。
- 代码层兜底 `_strip_markdown()` 对 LLM 输出二次清洗：去掉 `**bold**`、`*italic*`、`# 标题`、`---`、`[text](url)`、`` `code` ``、`~~text~~`、行首 `-/*/+` 与有序列表号，并把 `event_id` 字样作为内置要求由 LLM 规避。
- 开头固定格式：「早安，这里是即时零售×个护美妆经营日报，{date}。」结尾固定以「今天听完只做三件事」收尾。

### 1.3 结构（LLM 提示词 8 段）

按 `LLM_SYSTEM_PROMPT` 的口播稿结构：
1. 核心判断（约 150 字）
2–4. 重点信号深度解读（**每条约 500–600 字，2–3 条，逐条做 5 维度分析**：①发生了什么 ②为什么重要 ③对屈臣氏意味着什么 ④值得学习或警惕什么 ⑤今天要盯什么指标）
5. 平台与竞对动态速览（约 400 字）
6. **今天值得学习的一件商家动作**（约 300 字，挑一个竞对/跨行业动作，分析底层逻辑 + 屈臣氏借鉴）
7. 机会点与建议动作（约 300 字，分商品/平台/营销/试点四维）
8. 风险提示与明日追踪（约 200 字）
结尾：今天听完只做三件事（3 条最具体可执行行动项）。

→ 任务点 1 的"每事件 5 维度分析"= 第 2–4 段的 5 维度；"今天听完只做三件事"+"今天值得学习的一件商家动作"均在提示词中强制。

### 1.4 长度校验（当前实现 vs 任务描述的双向校验）

任务描述的"双向校验（2900–5600 字，过低 3000 下自动重试扩充、过高重试压缩）"**未完整落地到当前 `generate_podcast.py`**。当前实际：

- LLM 生成阶段（`llm_generate_podcast_script`）：`min_chars_required = 2500`，共 2 次 attempt。
  - attempt 0 若 `cn_chars < 2500` → 拼接"上次生成只有 N 字，不合格，必须达到 3000–5000 字、每条 500–600 字深度分析"重试**扩充**。
  - attempt 1 若反而更短，则回退用第一次较长的结果。
  - 仅支持"过短→重试扩充"，**没有"过高→重试压缩"**。
- 生成后静态检查（非无信号）：`< 1800` 记 ⚠️"偏短"，`> 5500` 记 ⚠️"偏长"，**仅日志提示，不自动重试**。
- 质检侧上限：`podcast_review` 的 `COMMUTE_MAX_CHARS = 5000`、`COMMUTE_MAX_EST_MINUTES = 18`（见 §1.6）。

> **审计观察**：2900–5600 双向区间与 3000 字自动扩充/压缩，在 `generate_podcast.py` 中只有"2500 下限重试扩充 + 1800/5500 事后提示"，缺少上界重试压缩。若任务要求的 2900–5600 双向校验是目标态，当前代码仅实现其子集。

### 1.5 复述/泄漏要求（任务引用 caef4cb / 78ba300）

- 任务提示将"复述要求泄漏修复"关联到 commit caef4cb / 78ba300。仓库内对所有 `.md` 做 grep（含 MEMORY.md、docs、skills）**未命中这两个 commit 号**，read-only 又禁止跑 git，故**未能独立核对**这两个提交的实现位置。
- 就当前代码而言：`LLM_SYSTEM_PROMPT` **不再包含"按日报/按事件原样复述"的泄漏性指令**，而是反其道强调"不是新闻播报、禁止照读、必须口语化改写、不得出现 event_id/Markdown"。`podcast_review` 的检查 2 `check_report_similarity` 用 4-gram 重叠把"照读日报"作为硬性不合格项（阈值 `SIMILARITY_MAX=0.45`）。
- `_clean_report_for_display` 在邮件侧另做了"移除 LLM 思考泄漏（开头非标题文字）"（`send_daily_report_email.py`）。

### 1.6 播客质检（`skills/podcast_review/`）

`podcast_review.py` 主函数 `review_podcast` 对 `podcasts/scripts/{date}.md` 执行 5 项规则检查 + 结构完整性 + 大纲检查 + 可选 LLM 深度审稿：

| # | 检查 | 阈值（代码常量） | 说明 |
|---|------|------------------|------|
| 1 | `orality` 口语化 | `ORALITY_MIN_SCORE=60` | 书面语（综上所述/赋能/抓手/闭环/底层逻辑…）扣分，口语特征加分，句子平均长度>40 扣分 |
| 2 | `not_cloning_report` 不照读日报 | `SIMILARITY_MAX=0.45` | 4-gram 重叠，>45% 判"疑似照读"，>35% 提示改写 |
| 3 | `has_action_items` 行动建议 | `ACTION_MIN_COUNT=2` | 必须存在「今天听完…三件事」结尾，且编号动作 ≥2 条 |
| 4 | `no_new_facts` 不新增事实 | `FACT_NOVELTY_MAX=0` | 品牌名白名单+虚构数字（"NNN万/亿"）+虚构时间词（上周/前几天/据我了解）+事实句相似度 |
| 5 | `commute_fitness` 通勤适用 | `COMMUTE_MAX_CHARS=5000`, `COMMUTE_MAX_EST_MINUTES=18` | 中文>5000、预估>18 分钟、段落>45 记问题；按 240 字/分钟估时长 |

- 结构完整性：要求存在"开场/平台策略/本地生活/竞对商家/屈臣氏机会/三件事结尾"6 段，缺任何一段不计 sendable。
- LLM 深度审稿（`use_llm=true` 时）：5 维评分 + `overall`，与规则审稿合并（规则与 LLM 都判不过 → `confirmed`）；**不覆盖规则结果**。
- 结果落盘 `data/logs/{date}/podcast_review.json`（`passed`、`checks_passed`、`checks_total`、`script_chars`、`issues[]`）。
- pipeline 位置：`SKILL.md` 注明"只在 `generate_podcast` 成功时运行；失败不阻塞 pipeline；5 项至少 3 项通过才算可用"。但代码 `review_podcast` 实际 `sendable = all(checks.passed)`（全过才可发送），与 SKILL.md"3/5 项"描述存在出入。

---

## 2. TTS 音频生成

### 2.1 当前实现（`generate_podcast.py`）

- 引擎：**edge-tts**，默认语音 **`zh-CN-XiaoxiaoNeural`**（女声温暖），语速默认 `+0%`。可选 `Yunxi/Yunjian/Xiaoyi`（SKILL.md voice 表）。
- 流程：`generate_audio()` 同步包装 → `_tts_generate()` 异步 `edge_tts.Communicate(text, voice, rate=rate).save(output_path)`。成功判据：文件存在且 `size > 1000` 字节。
- 输出：`podcasts/scripts/{date}.md` + `podcasts/audio/{date}.mp3`，结果 JSON `data/logs/{date}/generate_podcast.json`（含 `script_length`、`audio_exists`、`audio_size_bytes`、`is_no_signal`、`tts_success` 等）。

### 2.2 与任务描述的"分块合成/超时/费用上限/无信号不生成"对照

- **分块合成（commit d823682）**：d823682（TTS 分块 + duration 验收 + 语速校准超时 + fallback 上限，`_split_tts_chunks` / `_concat_mp3` / `_generate_tts` rate_plan / `_mp3_duration` / `_estimate_mp3_duration`）在仓库中位于**周期播客/周期报告 TTS 链路**（已在 `docs/_draft/06` commit 表核对），**`generate_podcast.py`（日报/事件池口播稿 TTS）目前是整段单次 save，未见分块**。→ 分块合成是周期链路的特性，日报链路未采用。
- **超时/费用上限**：`send_daily_report_email._send_email` 有 60 秒超时；周期链路有 `paid_fallback_calls` 上限（`reset_paid_fallback_calls/set_max_paid_fallback_calls`，§20，见 06 篇）。`generate_podcast` 的 LLM 走 `deepseek-v4-flash-free`（`config/model_router.yaml` `generate_podcast` 条），默认免费模型。
- **无信号不生成（no-signal branch）**：**当前未实现"无信号就不生成音频"**。代码在 `is_no_signal=True` 时走 `NO_SIGNAL_TEMPLATE`（约 102 字，见 2.3 日志）后**仍继续调用 `generate_audio()`**。这直接对应任务点 4 的"已知问题 1"（见 §4.1）。

### 2.3 真实无信号/降级日志佐证

`data/logs/2026-10-09/generate_podcast.log`（无信号日）：
```
[Podcast] 事件类型: 无信号
[Podcast] 无信号日报，使用模板生成 (102 字)
[Podcast] 口播稿中文字符数: 102
[Podcast] 开始生成音频: voice=zh-CN-XiaoxiaoNeural, rate=+0%
[Podcast] ⚠️ 音频生成失败，口播稿已保存        ← TTS 虽被调用但失败
[Podcast]   事件类型: 无信号 | 口播稿:102 | LLM:否 | 音频:❌ 失败
```

`data/logs/2026-10-06/generate_podcast.log`（"正常"但核心为 0 条、LLM 失败 → 规则兜底）：
```
[Podcast] 事件类型: 正常(选材1条, core+new=0条)
[Podcast] 尝试 LLM 生成口播稿...   → 12s 后 [Podcast] LLM 失败，使用规则模板...
[Podcast] 口播稿中文字符数: 177    ⚠️ 口播稿偏短（177 < 1800）
[Podcast] 音频生成成功 ... 音频大小: 318 KB   ← 仍生成音频
```

→ 印证：事件池模式下，即使选材仅 1 条 / core+new=0，播客仍判"正常"并产出音频；LLM 失败时规则模板仅 177 字也照常 TTS。这是任务点 4 已知问题 1 的直接证据。

---

## 3. 邮件送达（`skills/send_daily_report_email/`）

### 3.1 发送方式与配置

- 通过 default agent 的 `/app/working/workspaces/default/skills/email/mailcli.py` 发送（腾讯企业邮 SSL）。mailcli 路径可被 `config/periodic_report.yaml` 的 `periodic_report.email.mailcli_path` 覆盖（第 100 行），缺省回落 `default` 工作区路径。
- `config/email.yaml`：仅配 `from`（"Watsons Retail Intel <watsons@watsons-retail.com>"）与 `to`（**`["123399974@qq.com"]`**）；**不存密码**，认证密码仅经环境变量 `email_key` 传入。
- 命令形态（`_send_email`）：`python mailcli.py send --to <addr> --subject <标题> --body-file <html> --html [--cc ...] [--attach 附件...] --json`；60s 超时；优先解析 `--json` 输出的 `ok` 字段，失败再回退文本判 `✅`。

### 3.2 HTML 正文（不含原始 .md 附件，仅 MP3）

- 正文 `_build_email_html`：`_clean_report_for_display`（移除事件 ID 各种格式 /`E\d{8}_\d{4}`/、判断标签【A】【B】【C】【R】【K】【X】、证据事件/置信度行、LLM 思考泄漏、规则兜底说明，删除空 event_id 表格列）→ `_repair_char_split` 修复"逐字换行"损伤 → `_keep_canonical_sections`（日报仅保留 01–09,11 章节）→ markdown-it（启用 table/strikethrough）转 HTML → 套 `EMAIL_CSS` 内联样式模板，含 header + audio-notice（"🎧 本期日报已生成 AI 播客音频，见邮件附件 MP3"）+ system-note + footer。
- 附件策略（`send_daily_report_email()` 与 CLI 默认）：
  - `attach_markdown=False`（**默认不附原始 .md**）——日报正文本就含全文；函数签名与 `--attach-markdown`（default "false"）一致。
  - `attach_podcast=True`：附加 `podcasts/audio/{date}.mp3`（`audio/mpeg`）。
  - `attach_script=False`：不附口播稿 .md。
- 周期报告 `send_periodic_report_email` 注释明确："**不附加 .md 原文：报告正文已在 HTML 中呈现，避免用户看到原始 Markdown 符号**"；仅附 MP3。
- **文档与代码矛盾点**：`send_daily_report_email/SKILL.md` 仍写"附件：日报 Markdown `attach_markdown=true`（默认）"；邮件常量 `SYSTEM_NOTE` 也仍写"附件包含完整 Markdown 日报和播客 MP3 音频"。**当前代码默认已改为不附 .md**，这两处为陈旧描述（`SYSTEM_NOTE` 当前未被 `_build_email_html` 引用，属遗留常量）。

### 3.3 幂等 / DRY RUN / 8 项安全门

- **DRY RUN**：`dry_run=True` 默认。DRY RUN 时只打印 To/Subject/HTML 长度/附件清单，标记为"未发送"，`sent=False`。
- **幂等**：
  - 日报：`data/logs/{date}/.email_sent` 标记文件，存在且非 dry_run 则跳过（返回 `note=already_sent`）；并限制仅允许补发"昨天"，更早日期直接拒绝。
  - 周期：`data/delivery/ledger.jsonl` 台账 + `_check_delivery_duplicate`（同 kind+subject+to 且 ok → 幂等跳过）——这是三批整改 §28，专治 W31 重复投递（见 §4.2）。台账只在**非 dry_run 真实发送成功**时写，避免 dry_run 污染幂等判断。
- **8 项安全门**（`_check_gate`，任一不过即拒绝发送并落 JSON）：
  1. `run_manifest.json` 存在
  2. `run_manifest.sendable == true`
  3. `final_report_file`（终稿）存在
  4. 终稿不以【待人工复核】开头
  5. `data/reviews/{date}/editor_review.md` 含「✅ 可发送」结论
  6. 终稿包含 8 个固定章节 `CORE_SECTIONS`（01 一句话结论 … 08 建议动作；无信号日报豁免）
  7. 终稿至少含 1 个 `event_id`（`E\d{8}_\d{4}`；无信号豁免；终稿做展示清理后改为从审稿报告/事件文件/draft 兜底核实）
  8. 邮件收件人非空

### 3.4 admin 告警（`skills/send_admin_alert/`）

- 独立于日报送达：健康检查/管线失败时发**纯文本**告警到管理员 **`51111211004@stu.ecnu.edu.cn`**（`ADMIN_ALERT_EMAIL` 环境变量可覆盖），走同一 `mailcli.py`，30s 超时；`healthy` 时默认不发（`--force` 可强发）。→ 注意告警收件人与日报收件人（123399974@qq.com）不同。

### 3.5 pipeline 集成（`run_daily_pipeline.py`）

- `send_daily_report_email` 默认**禁用**：`send_email=False`（`run_daily_pipeline.py` 第 14、1781、2640 行），需 `--send-email true` 启用，`--dry-run false` 真实发送。
- 播客失败不阻塞，邮件日志记 `warnings["播客音频缺失（generate_podcast 未成功）"]`。

---

## 4. 已知问题（对照真实日志/产物）

### 4.1 每日播客在事件池模式下仍生成 102 字无信号音频（与停日报冲突）

- 代码：`generate_podcast.py` `is_no_signal` 分支**不跳过 TTS**，仍用 `NO_SIGNAL_TEMPLATE`（约 102 字）生成音频（§2.2/§2.3）。
- 证据：`data/logs/2026-10-09/generate_podcast.log`「无信号日报，使用模板生成 (102 字)」→ 仍尝试生成音频（此日 TTS 失败，"无音频"即任务点 4 的 W33 一类情形）；`podcasts/audio/2026-10-09.mp3` 为 0 字节。
- 关联：周期链路在 `sendable=false` 时交付门**完全不起播客/TTS**（见 06 篇 W33），但**日报链路无此拦截**。→ "no-signal 不生成"的目标态未落到日报 `generate_podcast`。

### 4.2 W31 出现两次成功发送（重复投递风险）

- 证据 `data/logs/periodic/send_daily_report_email.log`（2026-08-08）：
  - `04:13:26` 发送周报 W31 成功（attach `weekly-2026-W31.mp3` 3.5MB）
  - `04:34:04` **再次**发送周报 W31 成功（attach 重新生成的 6.2MB mp3）→ 同一主题成功发送两次。
- 根因/整改：当时周期链路尚无幂等台账；三批整改 §28 引入 `data/delivery/ledger.jsonl` + `_check_delivery_duplicate`。**W32 之后同一主题再次尝试即被幂等拦下**（见 §5 日志：`已发送过相同主题邮件，跳过（幂等）`），证明整改已生效。

### 4.3 W32 NameError 补发（commit 94ee35f）

- 周期 W32 manifest：`error="name 'project_root' is not defined"`——邮件发送环节引用未定义 `project_root` 抛 NameError（详见 06 篇 §6）。
- 证据 `data/logs/periodic/send_daily_report_email.log`（2026-08-10）：
  - `01:13:52` 首次尝试（日志止于读周报/音频，未见 send 命令与成功——即 NameError 崩溃点）。
  - `12:49:59` **[DRY RUN]**（补发前先模拟，验证参数/附件：HTML 18048 字、附件 1×`weekly-2026-W32.mp3`）
  - `12:50:21` 幂等探测 → `已发送过相同主题邮件，跳过（幂等）`
  - `12:52:00` 真实补发成功（命令带 **`--json`**，`邮件发送成功: ['123399974@qq.com']`）。
- 修复：**94ee35f** 为 `_send_email`/`send_periodic_report_email` 补齐 `project_root` 传递；当前代码 `_mailcli_path(project_root or _PROJECT_ROOT)`、`send_periodic_report_email(..., project_root=...)` 已显式接收。

### 4.4 W33 无音频、无邮件（交付门语义）

- 周期 W33（08-10~16）manifest：`sendable=false`、`delivery_allowed=false`、`audio_path=""`、`tts=null`、`podcast_review=null`，覆盖度 7/7 正常但 LLM 429 限流致章节失败。
- 语义：`sendable=false → Phase4 在口播/TTS 之前 return`，故播客链路完全不启动；即便报告落盘也因 `require_audio` / 质量门不投递邮件。→ "成功报告也可能因无音频而不发"（06 篇 W40 亦印证）。

---

## 5. 真实日志片段佐证

### 5.1 日报成功发送（仅 MP3，无 .md）— `data/logs/2026-07-27/send_daily_report_email.log`

```
2026-07-26 23:37:16 send_daily_report_email 开始: date=2026-07-27, dry_run=False
2026-07-26 23:37:16 读取 manifest: sendable=True, no_signal=False
2026-07-26 23:37:16 读取终稿: .../reports/daily/2026/07/2026-07-27.md (4771 字符)
2026-07-26 23:37:16 安全门全部通过 ✅
2026-07-26 23:37:16 邮件标题: 即时零售 × 个护美妆经营日报｜2026-07-27
2026-07-26 23:37:16 HTML 正文长度: 9948 字符
2026-07-26 23:37:16 附加播客 MP3: .../podcasts/audio/2026-07-27.mp3 (3579840 bytes)
2026-07-26 23:37:16 发送命令: /app/venv/bin/python3 /app/working/workspaces/default/skills/email/mailcli.py send --to 123399974@qq.com --subject 即时零售 × 个护美妆经营日报｜2026-07-27 --body-file /tmp/watsons_email_m8xy9264/email_body_即时零售_×_个护美妆经营日报｜2026.html --html --attach /tmp/watsons_email_m8xy9264/attachments/podcast_2026-07-27.mp3
2026-07-26 23:37:21 邮件发送成功: ['123399974@qq.com']
2026-07-26 23:37:21 发送标记已写入: .../data/logs/2026-07-27/.email_sent
2026-07-26 23:37:21 发送结果: ok=True, sent=True, dry_run=False
```

→ 命令行明确**只 `--attach` 播客 MP3**、未附 `.md`，收件人为 `123399974@qq.com`，与 §3.2"仅 MP3"一致。

### 5.2 日报安全门拦截 — `data/logs/2026-08-07/send_daily_report_email.log`

```
send_daily_report_email 开始: date=2026-08-07, dry_run=False
读取 manifest: sendable=False, no_signal=False
读取终稿: .../reports/daily/2026/08/2026-08-07.md (40 字符)
[WARNING] 安全门未通过: run_manifest.sendable=False，不是 true
[WARNING] 安全门未通过: 审稿报告中未找到「✅ 可发送」结论
[WARNING] 安全门未通过: 终稿仅包含 0/8 个固定章节
[WARNING] 安全门未通过，邮件不发送
结果 JSON 已保存: .../send_daily_report_email.json
```

→ 8 项安全门（§3.3）逐条打印未通过原因并拒绝发送。

### 5.3 周期链路：W31 重复投递 + W32 幂等/补发 — `data/logs/periodic/send_daily_report_email.log`

```
2026-08-08 04:13:26 发送周报邮件: 2026-W31 (07-27 ~ 08-02) ... 邮件发送成功        ← 第 1 次
2026-08-08 04:34:04 发送周报邮件: 2026-W31 (07-27 ~ 08-02) ... 邮件发送成功        ← 第 2 次（重复投递）
2026-08-10 01:13:52 发送周报邮件: 2026-W32 (08-03 ~ 08-09)                          ← NameError 崩溃点
2026-08-10 12:49:59 发送周报邮件: 2026-W32 ... [DRY RUN] HTML 正文长度:18048 附件数:1 - weekly-2026-W32.mp3
2026-08-10 12:50:21 发送周报邮件: 2026-W32 ... 已发送过相同主题邮件，跳过（幂等）
2026-08-10 12:52:00 发送周报邮件: 2026-W32 ... --json ... 邮件发送成功            ← 94ee35f 后补发成功
```

### 5.4 交付台账 — `data/delivery/ledger.jsonl`

```
{"ts":"2026-08-09T06:31:25...","kind":"weekly","subject":"测试主题","to":"a@b.com","ok":true,"detail":"ok"}
{"ts":"2026-08-10T12:52:05.645300","kind":"weekly","subject":"即时零售 × 个护美妆经营周报｜2026-W32 (08-03 ~ 08-09)","to":"123399974@qq.com","ok":true,"detail":"邮件发送成功"}
```

→ W32 真实补发成功写入台账，供后续幂等判定。

### 5.5 播客生成日志（无信号 102 字 / 规则兜底 177 字）— §2.3 已内嵌

---

## 6. 审计观察（仅建议，未改动）

1. **SKILL.md 三处陈旧**：`generate_podcast`（1800–2500 旧路线）、`send_daily_report_email` 附件默认（`attach_markdown=true`）、`podcast_review` "3/5 项通过"均与当前代码不符；`SYSTEM_NOTE` 常量残留"含 Markdown 日报"文案且未被使用。
2. **长度双向校验未完整落地**：日报 TTS 链路缺"上界压缩重试"；2900–5600 区间与任务描述差异需确认是目标态还是已废弃。
3. **no-signal 仍产音频**：`generate_podcast.py` 的无信号分支未实现"不生成 TTS"，需决策是否对齐周期链路交付门语义（§4.4）。
4. **分块 TTS 未用于日报链路**：d823682 分块合成位于周期链路，日报 `generate_audio` 仍是整段单次 save。
5. 无法独立核对 caef4cb/78ba300（复述泄漏修复）与 e6550c0/94ee35f（W22 .md 附件整改）：e6550c0 在仓库正文未命中，94ee35f 已在 06 篇核对；W22 事件可从周期日志佐证（06-01 发 .md → 06-05 补发仅音频），但对应 commit 号仅按任务引用列出，未能 git 验证（只读约束）。

---

