# 日报生成子系统 — 只读审计报告

> 审计对象：`/app/working/projects/watsons-retail-intel`（Watson's Retail Intel）
> 审计范围：**日报生成子系统**（generate_daily_report / editor_review / generate_no_signal_report + 相关调度与校验）
> 本报告以仓库中真实代码为准，仅做只读审计，不改动任何仓库内容。
> 主要依据：`skills/generate_daily_report/generate_daily_report.py`、`skills/generate_daily_report/SKILL.md`、`skills/editor_review/editor_review.py`、`skills/editor_review/SKILL.md`、`skills/generate_no_signal_report/generate_no_signal_report.py`、`skills/run_daily_pipeline/run_daily_pipeline.py`、`scripts/run_daily.sh`、`skills/send_daily_report_email/SKILL.md`、`config/email.yaml`、`docs/acceptance_criteria.md`、真实 manifest `data/runs/2026-10-08/`、真实初稿/终稿 `data/drafts/2026-05-21/`、`reports/daily/2026/05/2026-05-21.md`。

---

## 0. 子系统定位与产物总览

日报子系统负责把"事件池"（`data/events/{date}/events_analyzed.json`）转化为**可发送的经营日报**，并负责审稿定稿与无信号时的诚实输出。真实代码路径与产物：

| 环节 | 真实代码 | 输入 → 主要产物 |
|---|---|---|
| 日报生成 | `skills/generate_daily_report/generate_daily_report.py`（`generate_daily_report()`，约 1893 行起） | `data/events/{date}/events_analyzed.json`（主，`events_scored_novelty.json` 仅作旧链回退）→ `data/drafts/{date}/daily_report_draft_v1.md`、`daily_report_draft_v2.md`、`daily_report_draft.md`（兼容稿=V1） |
| 审稿定稿 | `skills/editor_review/editor_review.py`（`editor_review()`，约 1537 行起） | V1/V2 初稿 + `events_analyzed.json` → `data/reviews/{date}/editor_review.md`、终稿 `reports/daily/{year}/{month}/{date}.md`（展示版，已剥离内部标记） |
| 无信号日报 | `skills/generate_no_signal_report/generate_no_signal_report.py` | 采集/清洗/拒绝统计 + 源健康 → `data/reports/{date}/daily_report_{date}_no_signal.md` 与 `final_report_{date}.md` |
| 邮件（仅启用时） | `skills/send_daily_report_email/send_daily_report_email.py`（由 `run_daily_pipeline.py` 后循环调用，默认 disabled） | 终稿 + 播客 → HTML 邮件；受 8 项安全门约束 |
| 调度停止 | `scripts/run_daily.sh`（cron 启动器） | `--end-step analyze --send-email false`，事件池供周期报告 |
| 步骤校验 | `skills/run_daily_pipeline/run_daily_pipeline.py`（`validate_generate_report` L1047 / `validate_editor_review` L1117 / `STEP_VALIDATORS` L1567） | 写 `data/runs/{date}/run_manifest.json` |

> 说明：`skills/generate_daily_report/SKILL.md` 仍写"8 个固定章节"（旧版章节集）；当前代码实际是 **01–09 + 11（近期延续观察）** 共 10 节。SKILL.md 落后于代码，本报告以真实代码为准。

---

## 1. 日报结构：10 节（01–09 + 11 近期延续观察）

权威标题清单来自三处真实代码（三者一致，除下文"10 vs 11"标注）：

- `generate_daily_report.py` `VALID_SECTIONS`（L100-108）：`01 今日一句话结论 / 02 今日三条必听 / 03 即时零售重点变化 / 04 本地生活重点变化 / 05 竞对观察 / 06 对屈臣氏的机会点 / 07 风险预警 / 08 今日建议动作 / 09 每日八问`（共 9 节，不含 11）。
- V2 系统提示词第 12 条（`V2_SYSTEM_PROMPT`）强制标题格式，逐字为：
  `## 01 今日一句话结论`、`## 02 今日三条必听`、`## 03 即时零售重点变化`、`## 04 本地生活重点变化`、`## 05 竞对观察`、`## 06 对屈臣氏的机会点`、`## 07 风险预警`、`## 08 今日建议动作`、`## 09 每日八问`、`## 11 近期延续观察`。
- LLM 审稿系统提示词（`editor_review.py` `LLM_SYSTEM_PROMPT` 第 9/10 条）与终稿校验 `final_validate`（`editor_review.py` 约 L608 起）也以"01–09 + 11（可选）"为准。

> **"10 节"的含义与编号空缺**：日报共 10 节，固定编号为 **01–09 + 11**，**没有 10**。`11 近期延续观察` 是追踪/延续观察区。`editor_review.py _keep_canonical_sections` 的 `CANON = {"01".."09","11"}`（L821-823）亦确认规范编号不含 10。

各节职责与生成来源（真实代码）：

| # | 精确标题 | 职责 | 生成来源（真实函数） |
|---|---|---|---|
| 01 | `## 01 今日一句话结论` | 当日一句话总判断，跨事件综合研判（不只罗列事件） | `generate_one_line_summary()`（L480） |
| 02 | `## 02 今日三条必听` | 今日最值得关注的 3 条核心信号（P1 优先，最多 3 条） | `generate_signal_section(top_signals)`（L505）；`select_top_signals(events, max_signals=3)`（L376） |
| 03 | `## 03 即时零售重点变化` | 即时零售渠道（美团闪购/京东秒送/淘宝闪购/抖音小时达） | `generate_platform_section(instant_retail_events)`（L565） |
| 04 | `## 04 本地生活重点变化` | 本地生活/到店/团购渠道 | `generate_platform_section(local_life_events)` |
| 05 | `## 05 竞对观察` | 竞对（丝芙兰/妍丽/WOW/名创/盒马等）动作，建议表格呈现 | `generate_competitor_section()`（L606） |
| 06 | `## 06 对屈臣氏的机会点` | 机会点，按动作类型分类（商品/平台/营销/试点），有表格"事件依据"列 | `generate_opportunity_section()`（L858） |
| 07 | `## 07 风险预警` | 经营风险（`business_analysis.impact_type=="risk"`） | `generate_risk_section()`（L838） |
| 08 | `## 08 今日建议动作` | **今日唯一建议动作**（只 1 条，来自 P1+高置信+non-rule_fallback） | `generate_unique_action(unique_action_event)`（L741）；`select_unique_action()`（L453） |
| 09 | `## 09 每日八问` | 八个针对性追问，须紧扣当天事件，不得套话 | `generate_daily_eight_questions()`（L876） |
| 11 | `## 11 近期延续观察` | ongoing/tracking（`report_eligibility==tracking` / `novelty_status==ongoing`）事件的持续跟踪区，**严禁进入 02–05 正文节** | V2 规则稿 `generate_v2_by_rules`（L1422 附近）；`_build_tracking_section_items`（editor_review，自动补 11 节用） |

标题行：`# 即时零售 × 个护美妆经营日报｜{date}`（V1 规则稿 / LLM 稿）。V2 规则稿标题为 `# 经营日报总编版｜{date}`，LLM 重构稿标题由 LLM 生成（真实示例 `# 屈臣氏即时零售电商经营日报`）。

渠道权重（`CHANNEL_WEIGHTS`，L116-122）：即时零售 0.575（核心业务/🔴）、本地生活 0.225（战略/🟡）、传统电商 0.125（观察/⚪）、竞对观察 0.075（情报/🔵）。平台分组 `PLATFORM_SECTIONS`（L132 起）含美团闪购 / 京东秒送·京东到家 / 淘宝闪购·饿了么 / 抖音小时达等，`max_items` 3–5。

**两处章节编号偏差（需注意）**：
1. **V1 规则稿把 11 节写作 `## 10 近期延续观察`**（`generate_report_by_rules` L1021）；而 V2 规则稿、所有 LLM 提示词、校验规则都用 `## 11`。由于 `editor_review._keep_canonical_sections` 的 `CANON` 不含 10，展示清理会**丢弃 `## 10 近期延续观察` 整节**；且单稿模式下一旦存在 ongoing/tracking 事件，`rule_validate`/`final_validate` 会报"缺少章节: 11 近期延续观察"（高严重度）。这是代码内真实不一致，规范标题应为 `## 11 近期延续观察`。
2. 真实 LLM 重构稿偶发简化标题（如 `## 01 一句话结论`、`## 06 机会点`、`## 08 建议动作`）；校验对标题做**数字前缀宽容匹配**（`section_num = section.split()[0]`），不因措辞简化而误判。

---

## 2. 生成流程：规则稿 + LLM 稿双轨（V2 独立重构）

主函数 `generate_daily_report()`（`generate_daily_report.py` L1893）整体流程：

1. **事件加载与去重**：优先读 `data/events/{date}/events_analyzed.json`（新链，含 novelty+temporal+priority+weighted_score+business_analysis），缺失才回退 `events_scored_novelty.json`。加载后做**跨日话题去重**（`_deduplicate_against_previous_day`）+ **同日事件去重**（复用 `extract_events.deduplicate_events`，`similarity_threshold=0.50`）。
2. **事件筛选**：`select_events(used_events)` → P1 优先 P2、ARCHIVE 不入正文；`top_signals = select_top_signals(used_events, 3)`（最多 3 条）；`unique_action_event = select_unique_action(used_events)`（今日唯一建议动作，须 P1+高置信+非 rule_fallback）。
3. **Phase 1（规则稿，双轨并行）**：`ThreadPoolExecutor(max_workers=2)` 并行跑 `generate_report_by_rules(used_events,…)`（=V1，D 覆盖）与 `generate_v2_by_rules(used_events,…)`（=V2 规则稿，D 取舍/压缩）。单 future 超时 120s；失败则写 `# 即时零售 × 个护美妆经营日报｜{date}\n\n生成失败: {e}\n` 占位。
4. **Phase 2（LLM 稿，V2 独立重构）**：`use_llm=True` 且 LLM 可用时，`max_workers=2` 并行 `refine_with_llm(draft_v1,…)`（V1 润色）与 `refine_v2_with_llm(used_events,…)`（**V2 由事件池独立重构，不再是润色 V1**，见 `refine_v2_with_llm` L1490）。
   - **墙钟上限 `_PHASE2_DEADLINE = 420`（秒，7 分钟）**（L2101）。`as_completed(_refine_futures, timeout=_PHASE2_DEADLINE)` 超时未完成的稿**回退对应规则稿**，且 `_refine_executor.shutdown(wait=False)` **不等待挂起的 V2 线程立即放行**，保证 07:00 cron 当天产出可发送报告（注释原文："V2 是 thinking 模型独立重构，偶发耗时 30+ 分钟……设硬上限……超时未完成稿回退规则稿"）。V1 通常 ~90s，V2 给足余量。
   - LLM 返回 `<200` 字符 → 判为空/过短，回退规则稿。
   - **V2 event_id 保全守卫**（L2128-2140，防"LLM 稿丢全部 event_id"）：拿到 V2 LLM 稿后统计 `_rule_ids = len(re.findall(r"E\d{8}_\d+", draft_v2))`（规则稿）与 `_llm_ids = len(re.findall(r"E\d{8}_\d+", _result))`（LLM 稿）；**若规则稿有 event_id（`_rule_ids>0`）而 LLM 稿全丢（`_llm_ids==0`），则弃用 LLM 稿、保留规则稿**，日志明确记录"V2 LLM 稿丢失全部 event_id(规则稿N个→LLM稿0个)，弃用 LLM 稿、保留规则稿以通过校验"。原因注释：`editor_review.rule_validate` 要求终稿含 ≥1 个 event_id 做防幻觉校验，丢光会导致不可发送。
5. **校验**：`validate_report(draft_v1, all_events, …)` / `validate_report(draft_v2, …)`（L1631）。`passed = 无"缺少章节"且无"无 event_id"`（L1710）。
6. **写出**：V1→`daily_report_draft_v1.md`、V2→`daily_report_draft_v2.md`、兼容稿 `daily_report_draft.md`（**=V1 内容**）。返回结果含 `v1_llm_refined` / `v2_llm_refined`、`v1/v2_chinese_chars`、`validation_passed`、`unique_action_event_id` 等。

**V2 提示词强制 event_id（rule 9）**：`V2_USER_TEMPLATE` 第 9 条原文："**【强制】每个核心判断/信号/事件后必须用 `E2026...` 格式标注来源事件编号**（系统内部校验用，展示时自动移除，绝不能省略，否则报告无法通过校验发送）；不要给事件标注 A/B/C/R/K/X 判断标签"。`V2_SYSTEM_PROMPT` 第 15 条"`【event_id 用于溯源】每个核心判断后用 E2026... 标注`"、第 12 条"保留固定章节结构（01-09+11）"、第 10 条"tracking 事件只能放在 11 节"、第 14 条"【不要输出判断标签】不得写 A/B/C/R/K/X（不要写【A】、['A']、(A) 等任何形式）"。
事件选择硬规则（两稿共用）：ARCHIVE 不进正文、P1 优先 P2、low confidence 只能作"待验证线索"、rule_fallback 不得写强结论、唯一建议动作只 1 条。

---

## 3. editor_review 审校（含 anti-hallucination）

主函数 `editor_review()`（`editor_review.py` L1537），支持**单稿/双稿**两种模式：
- **双稿模式**（`daily_report_draft_v2.md` 存在且非空）：V1 规则校验 → 终稿以 **V2 为底稿** → `rule_compress`/规则校验。关键优化（L1705-1710 注释）：`dual_mode + V2 可用` 时**跳过 LLM 整篇重写**（该调用慢且易丢章节/超时），直接"用 V2 + 规则校验"，快速且确定。
- **单稿模式**（V2 不存在时自动回退）：规则校验 → LLM 审稿（或 `rule_compress`）→ 终稿校验。

### 3.1 rule_validate（规则校验，含防幻觉）
`rule_validate(draft, events, event_ids)`（L147），event_id 正则 `re.compile(r'E\d{8}_\d{4}')`。涉及 event_id/防幻觉的核心检查：
- **检查 2 —— 终稿/初稿须含 ≥1 个 event_id**（L190-197）：`found_ids = set(pattern.findall(draft))` 为空 → 高严重度 `missing_event_id`，fix"至少引用1个事件ID"。
- **检查 3 —— event_id 必须全部属于事件池（anti-hallucination）**（L200-206）：`invalid_ids = found_ids - event_ids` 非空 → 高严重度 `invalid_event_id`（"引用不存在的事件ID: …"，fix"删除或替换不存在的事件ID"）。这杜绝 LLM 编造事件编号。
- 其余检查：章节齐全（11 节仅在有 ongoing/tracking 事件时必检）、数字可溯源（无来源数字>5 触发 medium）、唯一建议动作仅 1 条且引用最优先合规事件、low-confidence 标记（🔍/⚠️待验证）、rule_fallback 标记（🔄规则兜底）、tracking 禁入 02–05、repeated/background 禁入核心信号、空话套话（low）等。

### 3.2 判断标签 A/B/K 无校验作用
在 `rule_validate` 全部检查中，**没有一项对 `A/B/C/R/K/X` 判断标签做正向校验**——标签只在展示层被剥离，从未被用于判定发送与否。因此提示词里"不要给事件标注 A/B/C/R/K/X"（`editor_review.LLM_SYSTEM_PROMPT` 第 12 条 / `LLM_USER_TEMPLATE` 第 12 条）是**单向禁令**而非校验项，属于"可从提示词去掉也不影响校验正确性"的冗余约束（去掉也不损害 anti-hallucination，因为 event_id 校验才是把关核心）。

### 3.3 final_validate（终稿校验）
`final_validate(final_md, events, event_ids)`（L608）独立复检终稿本身：缺章节（高）、**无 event_id（高 missing_event_id）**、**存在事件池外 ID（高 invalid_event_id）**、唯一动作条数与来源（高/中）、low/rf 标记、tracking 位置等。`passed = high_severity 问题数为 0`（L750-752）。
**sendable 判定**（L1838-1840）：`sendable = validation_passed and final_high == 0`，**只看终稿复检结果，不看初稿问题**。不可发送时终稿开头加 `> **【待人工复核】** …`。
**自动修复**：终稿缺 11 节但事件池存在 tracking/ongoing → 自动追加 `## 11 近期延续观察` 并复检（L1811-1835）；结构性问题未过 → `rule_compress` 再试一轮（`fixed_issue_count`）。

### 3.4 终稿只在展示层剥离内部标记，draft 内保留
- **draft（`data/drafts/{date}/*.md`）从不清理**，完整保留 event_id、判断标签、置信度等内部标记（供内部溯源与后续校验）。
- **终稿（展示层）**：L1860-1865 `display_md = _clean_report_for_display(final_md)` 写入 `reports/daily/{year}/{month}/{date}.md`（日志明确"终稿已写入（已清理内部标记）"）；`editor_review.md`（审稿报告 `reports/{date}/editor_review.md`）保留统计与内部内容。即**剥离只发生在"展示文件"这一层**。
- 证据：真实初稿 `data/drafts/2026-05-21/daily_report_draft_v1.md` 含 `**证据事件**：`E20260521_0045``、`**标签**：[A-必须关注] [X-需跨部门]`；真实终稿 `reports/daily/2026/05/2026-05-21.md` 的 `E2026` 引用已被清空（grep 计数 0）。

---

## 4. 成品清洗（`_clean_report_for_display`）

`editor_review.py` L897 `_clean_report_for_display(md_text)` 按序执行，覆盖用户点名的全部内部标记：

0. **LLM thinking 泄漏**：移除开头非标题文本（`^[^#\n].*?(?=## 01|# )`，L909）。
0.1 **逐字换行损坏**修复（`_repair_char_split`，L148 起，≥5 个单字符行判定打散并重拼）。
0.5 **去非法/重复章节**（`_keep_canonical_sections`，仅保留 01–09、11，各取首次，其余丢弃）。
1. **event_id 剥离**（L911-918，多格式）：`（`E…`:white_check_mark:，A）`、`（`E…`）`、`[`E…`]`、`` `E…` ``、裸 `E20260530_0037`（`E\d{8}_\d{4}` 正则）。
2. **判断标签剥离**（L920-935）：
   - `判断标签：A` 整行；`涉及事件：…` 整行（L921-923）
   - 内联 `【A】【B】【C】【R】【K】【X】`（L926-927）
   - **Python list** `['A']` / `["A"]` / `['K','X']`（含多元素）（L929-930）
   - **描述式** `[A-必须关注]`、`[A-必须关注 B-本周跟进]`（L931-933）
   - `"空"` 占位残留（事件正文被截断遗留的 `，空`/` 空`）（L935）
3. **证据事件/置信度行**：`- **证据事件**：…`、`- **置信度**：…`（L937-939）。
4. **残留标点修复**：空括号 `（、）`/`（）`、`[]`、孤儿 `对应事件：`、孤立破折号 `**对应事件**：—标题`（L941-947）、表格单元格前导冒号 `| ：描述 → | 描述`（L948）。
4.5 **孤儿表格列删除**（`_drop_event_id_table_columns`，L830；`_EVENT_ID_COL_NAMES={"事件依据","事件id","对应事件","event_id","eventid","事件编号"}` L827）：删除"表头名属事件ID类且该列内容全空"的整列（表头+分隔行+数据单元格），避免事件ID被清后留下空列。
5. **修复 markdown**（`_fix_markdown_inline` L988）：`###` / `---` / `>` 前补空行（防清理后标题/分隔线粘在前一行）。
6. **多余空行**压缩（`\n{3,}`→`\n\n`）、行尾空白清理（L951-957）。

> 审计发现（残留风险）：代码对 `**判断标签**：`、`**涉及事件**：`、`**证据事件**：`、`**置信度**：` 有整行删除；但历史 V1 模板用的是 **`- **标签**：`（非"判断标签"）** 前缀——当前清洗器没有对裸 `**标签**：` 的整行删除规则，若某稿只含 `**标签**：` 且标签内容被描述式正则剥掉后，会残留空行 `- **标签**：`。历史终稿 `2026-05-21.md` 仍能看到 `**标签**：[A-必须关注] [X-需跨部门]`，说明该日期的旧版清洗器尚未具备描述式标签剥离（后续已补强，见 2026-07-* 终稿无残留）。这是清洗逻辑与 V1 模板用词不一致造成的历史残留风险，建议保持一致用词。

---

## 5. 无信号日报（generate_no_signal_report）

### 5.1 触发时机
- **条件**：`filter_merged` 后 `cleaned_count == 0`（真实代码在 `run_daily_pipeline.py` L2066 附近：`if cleaned_count == 0 and step == "filter_merged"`）→ `no_signal=True`、`report_type="no_signal"`。
- 流水线层面：跳过 `extract / score / analyze / generate_report`（`remaining_skip`，L2077），把 `generate_report` **替换**为 `generate_no_signal_report`（L2100），**保留 `editor_review`**（无信号日报也需审计）（SKILL.md：`cleaned_count==0` 且 Tavily gap search 后仍为 0）。
- **这不是失败，是诚实输出**（SKILL.md 原文）。

### 5.2 内容模板（真实 `generate_no_signal_report.py` L192-266）
标题 `# 即时零售 × 个护美妆经营日报｜{date}（{weekday}）`，章节（注意编号**复用 01/02/03/05/06/08/09**，与原日报章节集不同）：
- `## 01 今日一句话结论`：坦诚"今日未发现足够高置信新增信号"，并注明"无信号日报 ≠ 系统故障"。
- `## 02 今日信息质量说明`：采集量（raw_count）/ Tavily 补搜量（gap_count / gap_query_count）/ 参考级（reference_count）/ 拒绝（rejected_count）/ 主要原因（main_reason）；子节 `### 采集来源分布`、`### 时间窗口分布`、`### 关键词命中率`、`### 来源健康度`（A 主选/B 备选/C 参考/D 停用）。
- `## 03 即时零售重点变化`：无新增，列举平台清单（美团闪购/京东到家/淘宝闪购/饿了么/抖音小时达）"暂无新动态"。
- `## 05 竞对观察`：无高置信竞对动作 + 可能原因。
- `## 06 对屈臣氏的机会点`：建议**转向内部经营数据复盘**、复核各平台指标、检查昨日追踪项、关注天气/节假日。
- `## 08 今日建议动作`：复核昨日追踪项和各平台核心经营指标。
- `## 09 每日八问`：8 条待办勾选式追问。

### 5.3 输出
- `data/reports/{date}/daily_report_{date}_no_signal.md`（主）+ **同内容**写 `data/reports/{date}/final_report_{date}.md`（兼容 pipeline，L275-277）。
- JSON 结果 `data/logs/{date}/generate_no_signal_report.json`：`sendable: True`、`report_type: "no_signal"`、含 raw_count/gap_count/gap_query_count/reference_count/rejected_count/main_reason/by_collector/top_sources/health_summary/filter_stats。返回值（SKILL.md 示例）：`{"ok":true,"report_type":"no_signal","sendable":true,…}`。

---

## 6. 2026-08 起日报停发及其影响（含与历史 manifest 的偏移）

### 6.1 停止方式（`scripts/run_daily.sh`）
当前 `scripts/run_daily.sh` 调用（真实）：
```sh
"$PY" -m skills.run_daily_pipeline.run_daily_pipeline \
  --project-root . --date "$TODAY" \
  --use-llm true --send-email false --recipient "$RECIPIENT" \
  --end-step analyze --resume false --continue-on-error true
```
- `--end-step analyze`：主管道只跑到 `analyze` 为止（`end_idx = STEP_ORDER.index("analyze")`，L1821），**不再进入 `generate_report` / `editor_review`**。
- `--send-email false`：`send_daily_report_email` 默认 disabled（`run_daily_pipeline.py` 后循环：`if send_email and not se_step_in_main` 才运行，L2443）。
- 脚本注释明确意图：**"2026-08 起不再生成日报/播客/日邮——停在 analyze（事件池最后一步生产完整字段：priority/weighted_score 由 score 产出、business_analysis 由 analyze 产出）"**；事件池供周/月/年报用。
- 脚本注释提醒：**不能停在 novelty_check**——链路重排（commit 2afeb4d）后 novelty 在 score/analyze 之前，停在 novelty_check 会导致 score/analyze 永不执行，事件池缺 priority/business_analysis → 周期报告源材料空泛（08-10/11 事故）。
- 磁盘证据：`data/drafts/` 最新日报稿为 **2026-08-01 / 08-02 / 08-07**（08-07 仅有裸 `daily_report_draft.md`），`reports/daily/2026/08/` 最后真日报为 **2026-08-02.md**（08-07.md 仅 87 字节残片）。

### 6.2 与历史 manifest 的偏移（标注）
真实 `data/runs/2026-10-08/run_manifest.json`（07:00 CST 主管道）显示：步骤止于 `analyze`（6.5s, event_count=4, p1=0/p2=1/archive=3）后，**仍执行了 `generate_podcast`（成功，`is_no_signal:true`，script_length=102，tts 出音频）与 `podcast_review`（warning，审稿未通过 0/5）**；`send_daily_report_email` 为跳过（`reason:"send_email=False (默认禁用)"`）；`generate_report` / `editor_review` / `evergreen_candidates` **未出现**在步骤表。
- 原因（代码级）：`run_daily_pipeline.py` 在主循环结束后，对 **`generate_podcast`（L2249 起）与 `podcast_review`（L2315 起）做 best-effort 后处理**——若主循环没跑到（`--end-step analyze`）就**无条件补跑**（"仅在主循环未处理时运行"），失败也不阻塞。因此**即便 `--end-step analyze`，播客仍被生成**。
- **偏移结论**：`run_daily.sh` 注释声称"不再生成日报/播客/日邮"，**但代码与 2026-10-08 manifest 证实：日报（V1/V2 + editor_review）与日报邮件确实停发，而 `generate_podcast`/`podcast_review` 仍被执行**（从事件池直生口播+音频，`is_no_signal` 兜底）。这是"停发"与"历史仍显示 generate_podcast"的冲突点，应在排障/评估资源占用时注意：停发仅约束于日报告/日邮，播客链路未被 `--end-step analyze` 关闭。

---

## 7. 已修复事故（代码级佐证）

### 7.1 "06-10 智障" — 分析与事件错位（commit 588b95f）
详见姐妹报告 `docs/_draft/04_event_processing.md §7.1`（此处摘要）：
- **根因**：`parallel_runner.batch_parallel_map` 的结果列表按 `orig_idx` 下标定位（`results[orig_idx]=result`），orig_idx 是完整事件列表索引；若调用方（旧版 `analyze_business_impact.py`）用**顺序自增下标**遍历 `_batch_results` 回取，会出现 `business_analysis` 与事件张冠李戴——A 事件的分析被挂到 B 事件上，报告出现完全对不上的"智障"输出。
- **已修复**（当前代码固化为两处防御）：`batch_parallel_map` 按 `max(orig_idx)+1` 分配结果长度；`analyze_business_impact.py`（1057-1095 行）显式 `_batch_results[orig_idx]` 回读，并有醒目注释"必须用 orig_idx 直接索引，绝不能用顺序自增的 _result_idx"。教训：任何 batch 并行调用都应按原始索引回填。
- 对日报子系统的关联：该事故正是"日报正文出现错位分析"的直接源头，属于事件加工环节（analyze）进入日报前必须保证的正确性。

### 7.2 V2 丢 event_id → 质检失败、发不出邮件（commit 732caab）
- **问题描述**：V2 LLM 重构稿偶发**无视提示词而丢弃全部 event_id**；而 `editor_review.rule_validate`（检查 2，≥1 event_id）+ `final_validate`（检查 2）把"无 event_id"判为**高严重度**，导致 `sendable=False`；`send_daily_report_email` 8 项安全门第 7 条"终稿至少包含 1 个 event_id（无信号豁免）"（`send_daily_report_email/SKILL.md` / 代码 L780-786）随之不过 → **邮件发不出**。
- **代码级防护（规则稿 guard 兜底）已固化**：`generate_daily_report.py` Phase 2（L2128-2140）判断 `规则稿event_id数>0 且 LLM稿event_id数==0` → **弃 LLM 稿、保留规则稿**（日志：`V2 LLM 稿丢失全部 event_id(规则稿N个→LLM稿0个)，弃用 LLM 稿、保留规则稿以通过校验`）。即：**规则稿保底的 event_id 正是事件池溯源/防幻觉校验的兜底**——即使 LLM 全丢，规则稿仍能提供 ≥1 个事件池内 ID，避免整日报告不可发送。这正是用户所述"本可被规则稿 guard 兜住"的机制。
- 佐证路径：`editor_review.py` `rule_validate` L190-206 / `final_validate` L643-660 / `send_daily_report_email` 安全门 7。

---

## 8. 校验与交付链（发送前的最后硬闸）

`send_daily_report_email` 8 项安全门（`skills/send_daily_report_email/SKILL.md`；真实代码 L729 起），任一不过则拒绝发送：
1. `run_manifest.json` 存在
2. `sendable=true`
3. `final_report_file` 存在
4. 终稿不以 `【待人工复核】` 开头（L751）
5. `editor_review.md` 结论为「✅ 可发送」
6. 终稿含 8 个固定章节（无信号豁免）
7. **终稿至少含 1 个 event_id（无信号豁免）**（L780-786，`EVENT_ID_PATTERN`）
8. 收件人存在

邮件配置 `config/email.yaml`：`from: "Watsons Retail Intel <watsons@watsons-retail.com>"`，`to: ["123399974@qq.com"]`（`run_daily.sh` 亦 `RECIPIENT="123399974@qq.com"`）。SMTP/密码由 default 工作区 `mailcli.py` 管理（环境变量 `email_key`），本仓库不存密码。

---

## 9. 与验收标准（`docs/acceptance_criteria.md`）对照

| 验收项 | 标准 | 本子系统实现 |
|---|---|---|
| A7 内部标记 | 展示层 0 event_id / 0 判断标签 / 0 思考泄漏 / 0 "置信度·涉及事件" / 0 空占位 | `editor_review.report_sanitizer`=`_clean_report_for_display` + `rule_validate`/`final_validate` |
| A8 事实-分析分离 | 趋势结论必须有证据支撑 | `validate_report` 数字回溯、`V2_SYSTEM_PROMPT` 事实/解释/判断结构 |
| D1 邮件正文 | 报告内容在 HTML 邮件正文、不附 .md 原文（周期报告约束，日报仍可 `attach_markdown=true`） | `send_daily_report_email` |
| B7 幂等 | 同周期不重复发送（delivery ledger） | `send_daily_report_email`（`data/delivery/`，manifest 判定） |

---

## 10. 审计小结与风险提示

1. **章节编号残留不一致**：V1 规则稿写 `## 10 近期延续观察`，规范/校验/展示用 11；`_keep_canonical_sections`（CANON 无 10）会丢弃该节、单稿模式可能误判"缺 11 节"。建议 V1 规则稿统一为 11。
2. **清洗器与 V1 模板用词不一致**：`- **标签**：`` 前缀无整行删除规则，描述式 `[A-必须关注]` 剥离后可能残留空行（2026-05-21 历史终稿即残留标签，后续已补强，07 月起无残留）。建议统一为 `**判断标签**` 或补 `**标签**` 行规则。
3. **`SKILL.md` 落后于代码**：`generate_daily_report/SKILL.md` 仍写"8 个固定章节"，代码实为 01–09+11；`no_signal` 章节号（01/02/03/05/06/08/09）与日报章节集（01–09+11）不同，阅读时容易混淆。
4. **停发边界**：`--end-step analyze --send-email false` 关闭的是"日报生成 + 日邮"，**未关闭 generate_podcast**（best-effort 后处理仍跑）。排障/资源评估时切勿把 manifest 里的 generate_podcast 误判为日报仍在发。
5. **防幻觉主闸门**：`rule_validate` / `final_validate` 的"≥1 event_id + 全部 event_id 属于事件池" + Phase2"规则稿保底 event_id"守卫，三者构成对 LLM 幻觉编号的完整防线，是日报可发送性的核心保障。

---

