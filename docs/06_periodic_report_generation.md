# 06 周期报告生成子系统审计

> 范围：**周期报告生成**（周报 / 月报 / 年报）。覆盖调度器 `run_periodic_pipeline`、周期聚合层
> `periodic_synthesis`（含覆盖度门禁 `period_coverage_check`）、主生成器
> `generate_periodic_report` 四阶段流程、终稿质量门 `periodic_editor_review`、调度与交付配置
> `config/periodic_report.yaml`。
> 本文为**只读审计**，逐层引用真实文件路径、真实常量与真实运行产物
> （`data/runs/periodic/weekly/` 下 W32/W33/W36/W39/W40 等实际 manifest 对照）。
> 只读，未改动任何仓库文件、未运行 git、未发送邮件。

---

## 0. 子系统文件清单与数据流

### 0.1 核心文件（以真实代码为准）

| 角色 | 文件 | 说明 |
|---|---|---|
| **调度器** | `skills/run_periodic_pipeline/run_periodic_pipeline.py` | 每天 09:00 CST 由 cron 触发；判断今天应生成哪类报告；周期级 lock；写 run manifest；按类总超时 |
| **主生成器** | `skills/generate_periodic_report/generate_periodic_report.py`（1394 行） | 四阶段生成、口播稿、TTS、delivery gate、邮件 |
| **Prompt 模板** | `skills/generate_periodic_report/prompts.py` | WEEKLY/MONTHLY/YEARLY_CHAPTERS、SYSTEM_PROMPT_BASE、REVIEW_SYSTEM_PROMPT、PODCAST_SUMMARY_PROMPT |
| **周期聚合层** | `skills/periodic_synthesis/periodic_synthesis.py`（1190 行） | 覆盖度检查 / 时间过滤 / 聚类 / 趋势 / 对比 / 配额 / 分章证据 → `periodic_synthesis.json/.md/coverage_report.json` |
| **终稿质量门** | `skills/periodic_editor_review/periodic_editor_review.py` | `review_final_draft` / `rule_validate`，输出 `sendable` |
| **统一清洗器** | `skills/utils/report_sanitizer.py` | event_id / 判断标签 / 思考泄漏 / 空占位 / 规则兜底说明 / 孤儿表格的统一展示层清洗（周期与日报共用） |
| **统一配置** | `config/periodic_report.yaml` | coverage / source_caps / quota / clustering / trend / podcast / llm / delivery / chapters / email 单一配置源 |
| **run 产物** | `data/runs/periodic/{type}/{period_id}/manifest.json` | 每周期运行清单（三批整改 §33） |
| **聚合产物** | `data/periodic/{type}/{period_id}/periodic_synthesis.{json,md}` + `coverage_report.json` | 主生成器唯一标准化输入 |
| **交付产物** | `reports/weekly|monthly|yearly/…`.md、`podcasts/scripts/…`、`podcasts/audio/…` | 最终报告 / 口播稿 / MP3 |

### 0.2 数据流（三批整改 · 第一批核心）

```text
data/events/{date}/events_analyzed.json   (每日事件池，覆盖度门禁的事件源)
    ↓ period_coverage_check             (覆盖度 gate：normal/degraded/failing)
    ↓ period_time_filter                (按 effective_event_time 过滤周期边界，多读前后各1天)
    ↓ cluster_events                    (cluster_key/fact_hash/实体/标题 聚类)
    ↓ trend_detection                   (规则化趋势方向，需上一周期比较)
    ↓ select_by_quota                   (P0/P1 全保留 + 平台/竞对/品类/风险最低配额 + 剩余按 weighted_score)
    ↓ build_chapter_evidence            (每章只传本章维度证据)
    ↓ periodic_synthesis.json / .md / coverage_report.json
    ↓ 主生成器 generate_periodic_report
    Phase 1 分章生成 → Phase 2 全局审稿 → Phase 3 定向修订 → Phase 4 终稿＋质量门＋口播稿＋TTS
    ↓ delivery gate (sendable + require_audio)
    HTML 邮件 (send_periodic_report_email) + MP3
```

---

## 1. 调度规则（run_periodic_pipeline）

文件：`skills/run_periodic_pipeline/run_periodic_pipeline.py`

### 1.1 触发判定（每天 09:00 CST 由 cron 触发，函数内自判）

在 `run_periodic_pipeline()` 中按 `today` 判定（L141-164）：

- **周一**（`today.weekday() == 0`）→ 生成**上周周报**。`get_last_week_range`：上周一 = `today - 7`，上周日 = `last_monday + 6`。
- **每月 1 日**（`today.day == 1`）→ 生成**上月月报**。`get_last_month`：本月 1 日 − 1 天 = 上月末，取其 year/month。
- **1 月 1 日**（`today.month == 1 and today.day == 1`）→ 生成**上年年报**。`get_last_year`：`today.year - 1`。

**同日冲突**：三个条件相互独立，均命中则 `tasks` 列表按 **周报 → 月报 → 年报** 顺序追加，随后串行执行（L199 注释 `# 串行执行: 周报 → 月报 → 年报`）。

**无任务**：`if not tasks:` 记日志并返回 `{"tasks_run": [], "results": {}}`，不获得 lock、不写 manifest。

### 1.2 周期 ID

`_period_id()`（L57-67），与聚合层 `periodic_synthesis._period_id` 完全一致：
- weekly → `%d-W%02d`（ISO 年-周，按 start 的 isocalendar）
- monthly → `%Y-%m`
- yearly → `%Y`

### 1.3 lock 防重入（三批整改 §32）

`_acquire_lock()`（L86-108）：在 `data/locks/periodic.lock` 用 `fcntl.flock(fd, LOCK_EX | LOCK_NB)` **非阻塞**获取；获取成功写入 `os.getpid()`；已被占用则 `LOCK_NB` 抛 `OSError` → 记 "🔒 本次跳过（防重入）" 并返回 `{"tasks_run": [...], "locked": True}`。**极端情况**：lock 抛其他异常时返回 `True`（宁可执行也不静默跳过）。`_release_lock`（finally 中）解锁。

### 1.4 run manifest（三批整改 §33）

`_write_manifest()`（L70-84）在任务开始前写 `data/runs/periodic/{type}/{pid}/manifest.json`（status=`running`），任务结束后回写最终状态。关键字段（见真实 manifest）：

```json
{
  "report_type": "weekly",
  "period": {"start": "...", "end": "..."},
  "started_at": "...", "status": "success|failed|error", "finished_at": "...",
  "sendable": true|false, "delivery_allowed": true|false,
  "report_path": "./reports/weekly/2026/2026-W40.md", "audio_path": "...",
  "error": "", "elapsed_seconds": 133.2,
  "coverage": {...}, "tts_success": false, "tts": {...}, "podcast_review": {...},
  "paid_fallback_calls": 0,
  "log_paths": {"run_out": "./data/logs/periodic/periodic_weekly.log", "manifest_dir": "..."}
}
```

### 1.5 每类总运行超时（三批整改 §35）

调度器与配置双重定义（值一致）：

| 报告类型 | `_TOTAL_TIMEOUTS`（run_periodic_pipeline.py L41） | `config/periodic_report.yaml → llm.total_timeout_seconds` |
|---|---|---|
| weekly | **1200**（20 min） | **1200** |
| monthly | **1800**（30 min） | **1800** |
| yearly | **2700**（45 min） | **2700** |

主生成器内再建 `deadline = started_at + timeout_s` 墙钟，各 Phase 内检查（见 §4）。

---

## 2. 周期聚合层 periodic_synthesis

文件：`skills/periodic_synthesis/periodic_synthesis.py`

### 2.1 输出 `periodic_synthesis.json` 结构

主入口 `build_periodic_synthesis()`（L932）落盘 `data/periodic/{type}/{pid}/periodic_synthesis.json`（同时写 `.md` 与 `coverage_report.json`）。**真实**键集合（用 W40 实测）：

```json
{
  "report_type": "weekly",
  "period": {"start": "...", "end": "...", "label": "2026-W40 (09-28 ~ 10-04)"},
  "coverage": {"expected_days":7,"available_days":7,"healthy_days":7,"degraded_days":0,
               "coverage_ratio":1.0,"raw_ratio":1.0,"missing_dates":[],"degraded_dates":[],
               "status":"normal","normal":0.85,"minimum":0.7},
  "event_stats": {"raw_events":16,"period_events":3,"reportable_events":3,"clusters":1,
                  "selected_clusters":1,"high_priority_clusters":0,
                  "platform_clusters":1,"competitor_clusters":1,"risk_clusters":0},
  "previous_period_comparison": {"period_info":{...},"event_count":3,"cluster_count":2,"high_priority_count":0},
  "trend_clusters": [],                 // 方向 ∈ {accelerating, emerging, weakening}
  "channel_summary": {"即时零售":1,"B2C旗舰电商":0,"ToB/经销":0,"clusters_ir":["PC_..."]},
  "platform_summary": {"美团闪购":1},              // 按 platforms 统计
  "competitor_summary": {...},                     // 按 competitors 统计
  "category_summary": {...},                       // 按 event_types 统计
  "risk_summary": [...],                           // 主维度=risk 的 cluster
  "opportunity_summary": [...],                    // 方向 ∈ {emerging, accelerating}
  "chapter_evidence": {"01":[cluster,...],...,"06":[...]},   // 每章只含本章维度证据
  "all_clusters": [ ... selected clusters ... ]    // 配额选样后的全部 cluster
}
```

**事实纪律**：cluster 内 `facts`（事实证据）与 `representative_event_ids` / `trend_direction`（`analyst_hints` 类分析提示）字段分离；`event_id` 在聚合层内部保留（`representative_event_ids`），仅展示层（`report_sanitizer`）清除。

### 2.2 三个渠道群统计（channel_summary）

`_summarize_channels()`（L1167-1180）按屈臣氏电商三大渠道群聚合：
- **即时零售**：美团、京东秒送、京东到家、淘宝、饿了么、抖音小时达、朴朴、叮咚
- **B2C 旗舰电商**：天猫、京东旗舰、旗舰店
- **ToB / 经销**：当前恒为 `0`（事件池无此维度信号）

### 2.3 聚类与配额（select_by_quota，L787-888）

- 聚类：cluster_key / fact_hash / 实体 / 标题（Jaccard≥0.65 或实体重合≥0.6）四级。
- **第一层**：P0/P1 cluster 全保留；同平台高优上限 `platform_high_cap`（默认 2），超限降级为普通待选（避免淘宝/美团刷屏）。
- **第二层**：维度最低配额 platform/competitor/category/risk（周报 3/2/2/1）。
- **第三层**：剩余按 `weighted_score` 降序填充，总上限 `max_clusters`（周报 80 / 月报 150 / 年报 240）。
- 年报额外 `min_months: 12` 月份配额，保证全年覆盖。

### 2.4 趋势方向（detect_trend_direction，L628）

规则化判定，返回 emerging / accelerating / stable / weakening / reversing / **unclear**。同类匹配须 cluster_key 完全一致，或实体集合相等且动作词有交集，或标题 Jaccard≥0.7（严格避免单实体宽匹配误判）。判定阈值来自配置 `trend`：`accel_cluster_ratio=1.5`、`min_days_active=3`、`min_source_count=3`。

### 2.5 配置默认值与 YAML 合并

`_load_config()`（L50-77）内置与 `config/periodic_report.yaml` 一致的默认值，加载时 `default[k].update(v)` 合并。配置文件缺省也不崩。

---

## 3. 覆盖度门禁 period_coverage_check

文件：`skills/periodic_synthesis/periodic_synthesis.py::period_coverage_check`（L293-412）

### 3.1 判定逻辑与输出字段

遍历周期内每天（`_period_dates`），对每天 `data/events/{date}/events_analyzed.json`（事件池为主）判断：

- 文件存在且 `events` 为非空 list → `healthy += 1`（`ok=True`）。
- 文件存在但 events 为空/解析失败 → 标记 `degraded`。
- 文件不存在 → 检查 `data/runs/{date}/run_manifest.json`：若 `manual_stop` 为 true 或 `reason=="manual_stop"` 或 `skip_reason=="manual"` → **人为停机豁免**（见 3.3）；否则（`events_analyzed.json` 或旧文件 `events_scored_novelty.json` 存在 → `degraded`，两者都不存在 → `missing`）。

输出字段（真实）：

```json
{
  "expected_days": 7, "available_days": 2, "healthy_days": 2,
  "degraded_days": 0, "coverage_ratio": 0.2857, "raw_ratio": 0.2857,
  "missing_dates": ["2026-08-31","2026-09-01",...], "degraded_dates": [],
  "status": "failing", "normal": 0.85, "minimum": 0.7
}
```

两个比例定义（代码精确语义，L392-397）：
- `raw_ratio` = (healthy + degraded) / expected —— **status 判定**用
- `coverage_ratio` = healthy / expected —— **门禁数值**用（写进 manifest 与门禁比较的有效覆盖）

### 3.2 阈值（normal / minimum）与状态机

| 报告 | normal（≥即 normal） | minimum（≥即 degraded） | < minimum → failing |
|---|---|---|---|
| weekly | **0.85** | **0.70** | 覆盖度不达标 |
| monthly | 0.90 | 0.80 | 覆盖度不达标 |
| yearly | 0.95 | 0.90 | 覆盖度不达标 |

（`config/periodic_report.yaml → coverage`；代码默认值与文件一致。）

- `status = normal`（ratio≥normal）
- `status = degraded`（normal>ratio≥minimum）→ 编辑质量门记 medium issue（可发送）
- `status = failing`（ratio<minimum）→ **不可发送**：主生成器在 Phase 1 之前直接返回 `{"error": "coverage_failing", "sendable": false}`（L1202 附近），编辑质量门将 coverage 判为 high issue。

### 3.3 事件源与 manual_stop 豁免

- **事件源**：`data/events/{date}/events_analyzed.json`（事件池为主），**不回退日报文件**（`reports/daily/.../*.md` 不参与覆盖度判定）。旧数据回退仅到 `events_scored_novelty.json`（文件存在即视可用）。
- **manual_stop 豁免**：`data/runs/{date}/run_manifest.json` 标记人为停机的天（如 08-04~08-07 用户要求不生成日报）**不会**进入 `missing_dates/degraded_dates`（注释"人为停机不计入覆盖度失败"）。
- 实现细节（忠实披露）：豁免天代码即 `healthy += 0; continue` —— 既不进 missing，也**不增 healthy**，仍计入 `expected` 分母，因此对 `coverage_ratio` 仍有稀释作用；豁免语义是"免于被记为缺失天数"，而非"免费记成健康天"。

---

## 4. 四阶段生成 flow（generate_periodic_report）

文件：`skills/generate_periodic_report/generate_periodic_report.py`

主入口 `generate_periodic_report()`（L1169）：建立 `deadline = started_at + timeout_s`（§35）、设置 paid fallback 上限（§20），Step 0 先 `_load_or_build_synthesis()`。

- `_load_or_build_synthesis`：优先读已存在的 `data/periodic/{type}/{pid}/periodic_synthesis.json`；不存在则现场 `build_periodic_synthesis`。构建失败 → 回退 `_load_source_reports`（日报 md / 周报 md / 月报 md）走 legacy 路径 `phase1_generate_chapters_legacy`；连源材料都没有 → 返回 `{"success": false, "error": "no_synthesis_and_no_source"}`。
- **coverage.status == failing → 立即中止**（§3.2）：返回 `coverage_failing`，不进入任何生成 Phase。

### Phase 1 — 分章生成（`phase1_generate_chapters`）

逐章（`_get_chapters` 取模板）：
- 每章只吃本章 `chapter_evidence`（`_build_chapter_source`，截前 30 个 cluster）＋全局摘要 `_build_global_context`（覆盖度 + 上期对比 + 平台动作密度 Top8）。
- 主模型 `client.chat`（内部 OpenCode→付费兜底）最多 `range(1,4)` 重试 3 次；全部失败 → `_rule_fallback_chapter()` 规则兜底（审计 §9.2 主模型→fallback→rule→仍失败不可发送）。
- 章节级硬失败标记 `_FAILED_CHAPTER_MARKER = "__CHAPTER_GENERATION_FAILED__"`——**绝不进入终稿**。
- `deadline` 超时则 Phase 1 提前终止，只保留已生成章节。

> 规则兜底章节会写 `> 说明：本节为规则兜底生成（generation_method=rule_fallback）` 页脚；该页脚在展示层被 `report_sanitizer` 的 `_RULE_FALLBACK_RE` 清除（这也解释了为何落盘 W40.md 无此说明行）。

### Phase 2 — 全局审稿（`phase2_review`）

- 汇总全文，`response_format="json"`，用 `REVIEW_SYSTEM_PROMPT` 审视。
- **JSON 解析失败自动重试**：`result.get("parsed")` 为空时先 `robust_json_extract`，再强制一次"只输出一个 JSON 对象"重试（L466-480）。
- 审稿仍失败 → 返回 `{"overall_quality": "review_failed", "_review_failed": True}`，**绝不静默降级**（审计 §10.1），Phase 3 跳过优化。

### Phase 3 — 定向修订（`phase3_revise`）

- 依据 `revisions_needed[].chapter_id` 逐章重生成。
- **新旧章质量比较后才覆盖（审计 §17）**：`_quick_chapter_score` 规则评分（长度/标题/无标记/非兜底/行数），仅当 `not new_has_high and new_score >= old_score` 才覆盖；否则保留原版。
- `_chapter_has_high_issue` 检 event_id / A/B/C 标签 / "置信度" / "涉及事件"。
- `max_rounds=2`（配置 `llm.max_revision_rounds=2`）；轮间可再 `phase2_review` 复审。`deadline` 超时停止修订。

### Phase 4 — 终稿合并 + 质量门 + 口播稿 + TTS（`phase4_finalize`）

1. **必需章节检查**：每周 6 / 每月 7 / 每年 8 章；`_FAILED_CHAPTER_MARKER` 非空 → 记录 `failed_chapters` 且不可发送。
2. 合并终稿 `title = 即时零售 × 个护美妆经营{周报|月报|年报}｜{period_label}`。
3. **质量门先于清洗**：对原始终稿执行 `review_final_draft`（§5 编辑器）；`sendable = review_result.sendable and not failed_chapters`。
4. **落盘报告**：无论 sendable 与否，先 `sanitize_periodic_report(mode="final")` 后用 `_get_report_path` 写 `.md`（`reports/weekly|monthly|yearly/…`）。→ 这也是 W33 虽失败仍留有 `2026-W33.md` 的原因。
5. **sendable=false → 至此 return**（不生成口播/TTS/邮件）——W33 的 `tts=null`、`podcast_review=null` 正是此出口。
6. sendable=true → 生成口播稿 `_generate_podcast_script`（周期化措辞，禁"今天/昨天"，脚本长度按周3000-5000/月4500-7500/年7000-12000 字，超界重试一次）→ 播客规则质检 `_review_periodic_podcast`（§24，不烧 LLM token）→ TTS `_generate_tts`。
7. **TTS（§23/§26/§27）**：`_split_tts_chunks` 按段切块（≥800 字符）；逐块 `edge_tts`（`_generate_tts_chunk`，voice `zh-CN-XiaoxiaoNeural`、rate `+5%`）；多块 `ffmpeg concat` 拼接（`_concat_mp3`）；`mutagen` 或体积估算读时长；**duration 验收** target（周300-540s/月480-780s/年720-1260s，宽容 15%），不达标按 rate_plan `["+5%","+25%","+40%","-15%"]` 语速校准重试（过长加速、过短减速）；全部失败保留最后成功版本记 `out_of_range`（不阻塞交付但标 warning）。

### §35 总超时中止发送

主入口在进入 Phase 4 前检查 `time.time() > deadline` → 返回 `{"error": "total_timeout", "sendable": false}`，**中止发送**（L1214-1218）。各 Phase 内亦各自提前终止。

### Delivery gate（§27/§30）

```python
delivery_allowed = sendable
if delivery_allowed and require_audio and not tts_ok:
    delivery_allowed = False   # require_audio=true 但 TTS 失败 → 不发邮件
```

`require_audio = config/delivery.require_audio = true`（配置另有 `allow_email_without_audio:false`）。`delivery_allowed` 才调 `_send_report_email`（内部 `send_periodic_report_email`，recipient 默认 `123399974@qq.com`）。

---

## 5. 周报章节结构与决策建议贴合度

### 5.1 周报 6 章（`WEEKLY_CHAPTERS`，prompts.py）

| 章节 | 标题 | 本质 |
|---|---|---|
| 01 | 本周一句话总结 | ≤80 字，有判断力非流水账 |
| 02 | 本周三大趋势 | 趋势名 / 描述 100-200 字 / 支撑事件 2-3 个 / 对屈臣氏影响 |
| 03 | 平台格局变化 | 美团闪购/京东秒送/淘宝闪购/饿了么/抖音小时达/朴朴/叮咚 分平台 |
| 04 | 竞对动态汇总 | 丝芙兰/调色师/妍丽/名创优品/连锁药妆 等，表格 `| 竞对|本周动作|战略影响|屈臣氏建议|` |
| 05 | 本周经营建议 | 5-8 条，P0 立即行动/P1 本周跟进/P2 持续关注，含依据与预期效果 |
| 06 | 下周关注点 | 3-5 项，促销节点/政策生效/财报驱动 |

月报 7 章（01 本月核心结论 / 02 即时零售格局 / 03 个护美妆品类 / 04 竞争态势 / 05 月度启示 / 06 下月战略建议 / 07 风险与机会）；年报 8 章（01 年度核心判断 / 02 年度复盘 / 03 品类趋势 / 04 平台生态 / 05 竞争格局 / 06 年度战略启示 / 07 下一年战略建议 / 08 长期风险机会）。

### 5.2 决策建议贴合屈臣氏电商三层渠道群

`SYSTEM_PROMPT_BASE` 硬编码读者画像：服务**统管全部线上零售渠道**的屈臣氏电商负责人，分析必须覆盖三大渠道群：
- **即时零售**：美团闪购、京东秒送、京东到家、淘宝闪购、抖音小时达
- **B2C 旗舰电商**：天猫官方旗舰店、京东旗舰店
- **ToB / 经销**：天猫超市、京东自营、经销商体系

月报 05 / 年报 06 明确要求"对三大渠道群分别说明"。`SYSTEM_PROMPT_BASE` 事实纪律：趋势断言须有证据支撑；无真实 GMV/市占率严禁写"市场份额上升/下降"；不编造屈臣氏内部经营数据；只分析中国大陆；不得出现研报/股价/买入评级；建议须具体可执行（做什么/在哪个渠道/预期效果）。

### 5.3 终稿质量门规则（periodic_editor_review）

`review_final_draft` → `rule_validate`。**任何 high issue → sendable=false**；review 执行失败 → false；章节缺失/多余/顺序错 → HIGH(structure)；章节过短（02+ 章节 <120 字）→ HIGH；资本市场噪音（`_STOCK_NOISE_RE` 涨停/股价/研报/估值…）→ HIGH；泄漏 event_id / 判断标签（`_TAG_RE` 含多元素 `['K','X']`、描述式 `[A-必须关注]`）/ "置信度"/"涉及事件"/思考泄漏/空占位 → HIGH；无凭据份额/市占/GMV 结论 → HIGH；数字断言无证据（`_check_numeric_claims`，允许 ±10%）→ HIGH。`overall_quality = good|acceptable|needs_revision`。

---

## 6. 真实失效案例（对照 `data/runs/periodic/weekly/` 实际 manifest）

### W32（周期 08-03~08-09，运行日 08-10）— `_send_email` NameError

manifest `2026-W32/manifest.json`：`status="error"`，`error="name 'project_root' is not defined"`，`elapsed_seconds=824.0`。
- 覆盖度正常（synthesis 与 manifest 均 ratio=1.0），报告已生成 `2026-W32.md`（35023 字节，内容完整），但**邮件发送环节**引用未定义 `project_root` 抛 NameError。
- 修复 commit **94ee35f**：`_send_report_email`（现 `send_periodic_report_email`）补齐 `project_root` 参数传递。

### W33（08-10~08-16，运行日 08-17）— 覆盖率 7/7 正常但 LLM 429 + 质量门拦截，无音频无邮件

manifest `2026-W33/manifest.json`：`status="failed"`、`sendable=false`、`delivery_allowed=false`、`report_path=./reports/weekly/2026/2026-W33.md`、`audio_path=""`、`tts_success=false`、`tts=null`、`podcast_review=null`、`paid_fallback_calls=0`、`elapsed_seconds=474.8`、覆盖度 `status=normal`、ratio=1.0（7/7 healthy）。
- 覆盖度 **7/7 正常**、报告 `.md` 已落盘（12955 字节，趋势内容完整），但 LLM 侧 429 限流导致章节级失败，**质量门 sendable=false** → Phase 4 在口播/TTS 之前 return，故 `tts=null`、`podcast_review=null`、无音频、无邮件。
- 印证：sendable=false 时交付产物只有报告 md，播客链路完全不启动。

### W36（08-31~09-06，运行日 09-07）— coverage_failing（2/7）拦截

manifest `2026-W36/manifest.json`：`status="failed"`、`error="coverage_failing"`、`elapsed_seconds=0.1`、`report_path=""`（**未生成任何报告**）、覆盖度 `coverage_ratio=0.2857`、`healthy=2/7`、`missing_dates=[08-31,09-01,09-02,09-03,09-04]`、`status=failing`。
- 覆盖度 2/7 远低于 weekly minimum 0.7 → 主生成器在 Phase 1 前立即中止，零秒拦截，不产报告不产音频不产邮件。

### W39（09-21~09-27，运行日 09-28）— 同 W36

manifest：同样 `error="coverage_failing"`、ratio=0.2857、2/7 healthy、missing W39 的 5 天、status=failing、elapsed=0.1s。

### W34 / W35 / W37 / W38 — cron agent 层失败，未启动

`data/runs/periodic/weekly/` 中**没有** W34（08-17~23）、W35（08-24~30）、W37（09-07~13）、W38（09-14~20）的 `manifest.json`，也未在 `reports/weekly/2026/` 生成任何对应报告（目录里 W21-W33 连续后直接跳到 W40）。→ 说明这几周任务**在 cron / agent 层即失败**，调度器 `run_periodic_pipeline` 根本没被触发，未写 manifest、未取锁、未产任何产物。

### W40（09-28~10-04，运行日 10-05）— 成功但口播稿失败 → require_audio 拦截邮件（对照补充）

manifest `2026-W40/manifest.json`：`status="success"`、`sendable=true`、`delivery_allowed=false`、`report_path=./reports/weekly/2026/2026-W40.md`、`audio_path=""`、`elapsed_seconds=133.2`、覆盖度 ratio=1.0 normal。
- 报告 sendable=true（落盘 1284 字节，即规则兜底章节经 sanitizer 去页脚后的版本），但口播稿生成失败 → `podcast_review={"passed":false,"issues":["口播稿生成失败"]}`、`tts={"ok":false,"error":"no_script"}` → **require_audio=true 使 delivery gate 拦截邮件**（`delivery_allowed=false`）。→ 印证"成功报告也可能因无音频而不发邮件"的交付门语义。

---

## 7. 已修复 / 已提交（对照失败案例与代码现状）

> 注：本审计不运行 git；commit 号按既有记录引用，并在当前代码中逐一核对修复后的实现。

| Commit | 修复项 | 当前代码印证 |
|---|---|---|
| **94ee35f** | W32 邮件 `_send_email`/`send_periodic_report_email` 缺 `project_root` 的 NameError | `_send_report_email(L1120)` 显式接收 `project_root` 并传入；manifest W32 之 error 不再出现 |
| **2ef6722** | sanitizer 统一（周期/日报共用）+ 强信号清洗 + 多元素判断标签 | `skills/utils/report_sanitizer.py` `sanitize_periodic_report`；`_TAG_RE` 覆盖 `['K','X']`、`[A-必须关注]`、中文冒号；`_RULE_FALLBACK_RE/_INLINE_RE` 清规则兜底页脚 |
| **d823682** | TTS 分块生成 + duration 验收 + 语速校准超时 + fallback 上限 | `_split_tts_chunks`、`_concat_mp3`、`_generate_tts` rate_plan、`_mp3_duration/_estimate_mp3_duration`、`reset_paid_fallback_calls/set_max_paid_fallback_calls`（§20） |
| **842ce7e 等** | 周期报告三批整改其余项（覆盖度门禁、lock、manifest、chapter_evidence、paid fallback） | `period_coverage_check`、`_acquire_lock`、`_write_manifest`、`build_chapter_evidence`、主生成器 delivery gate 均已落实现有代码 |

---

## 8. 审计观察（仅供后续优化参考，改动需另行评估）

1. **W40 揭示"规则兜底章节"可过质量门**：editor_review 不检查 `rule_fallback`/`generation_method`，导致 LLM 全失败时整份规则兜底稿仍 `sendable=true`——建议后续在质量门/告警侧标记规则兜底占比，区分"真成功"与"降级成功"。
2. **manual_stop 豁免的数值语义**：豁免天既不计 missing 也不计 healthy（`healthy+=0; continue`），仍占 `expected` 分母，会稀释 `coverage_ratio`。若意图是"完全豁免"，需从 `expected`/`dates` 中剔除口径调整。
3. **覆盖度双比例语义**：`status` 用 raw_ratio(含 degraded)，门禁数值用 coverage_ratio(仅 healthy)，两者可能给出不同印象（如某周期多天 degraded 时 raw 达标但 healthy 比低）。
4. **W28-W40 覆盖缺口与 cron agent 稳定性**：W34/W35/W37/W38 未触达调度器，说明问题在 **agent/cron 触发层**而非本子系统，宜从 `cron_watsons_pipeline.json` / agent 层定位。
5. 配置一致性已达成：weekly 覆盖 normal 0.85 / minimum 0.7、总超时 1200/1800/2700 在 `config/periodic_report.yaml` 与代码默认值/调度器 `_TOTAL_TIMEOUTS` 三处一致。

---

