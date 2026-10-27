# NAS E1 — 真实周期证据恢复（2026-W42 / 2026-10-19 ~ 10-25）

**状态**：隔离环境内完成；报告与音频达到格式/证据门，可进入人工验收；生产未切换、未发真实邮件、未改正式账本。  
**分支**：`e1/real-period-recovery`  
**隔离代码**：`/tmp/e1_wt`；**隔离报告**：`/tmp/e1_report`  
**生产影响**：`production_untouched=true`。Cron、PID1、容器、正式账本、生产代码、邮件均未改动。

---

## 1. 结论先行

本周没有把“采集日”当成“事件日”。W42（10/19–10/25）重新采集后得到 718 篇原始文章、21 篇清洗稿、300 条分析事件、191 个唯一事实哈希；在这 300 条中，人工逐条核验出 **13 条可追溯证据事件**，其中：

- **5 条为事件发生型（event_occurrence）**：有可核验的发生日期与事实链；
- **8 条为报道型（reported_on / article_claim）**：原文可追溯，但只能证明“媒体在本周这样报道”，不能证明事件本身在本周发生。

13 条事件进入隔离周报后聚合为 **12 个聚类**；生成 2026-W43 周报 **16,707 字符、6 章**，编辑质检 `sendable=true / overall_quality=good`，0 个 event_id 泄漏、0 个 A/B/K 标签、0 个“涉及事件/置信度”内部词，表格列一致。口播稿 **3,682 字**，包含“本周听完最值得做的三件事”和“本周最值得学习的一件商家动作”；音频 **478.4 秒（约 7 分 58 秒）**，7,655,084 字节。

本轮恢复还暴露并修复了两个会让“证据明明存在、报告却说没有”的真实缺陷：

1. **事件发生日被文章发布日覆盖**：丝芙兰 SEPHORiA 发生在 10/23–25，但追溯文章发布于 10/26；旧逻辑按 `published_at` 过滤，把该事件排除在 W42 之外，导致第 04 章错误写成“本周直接竞对实质动作事件为零”。已改为：`date_semantics=event_occurrence` 时优先使用 `effective_event_time`。
2. **`--rebuild-synthesis true` 被静默忽略**：CLI 参数没有传给 `_load_or_build_synthesis`，生成器继续读取旧 `periodic_synthesis.json`，所以即使代码修好，报告仍可能使用不含丝芙兰的旧聚类。已修复并验证重建后 `competitor_clusters=1`。

---

## 2. 周期与数据底账

| 项目 | 数值 |
|---|---:|
| 周期 | 2026-10-19 ~ 2026-10-25（W42；报告标为 2026-W43） |
| 覆盖天数 | 7 / 7 |
| 原始文章 | 718 |
| 清洗文章 | 21 |
| 分析事件 | 300 |
| 唯一事实哈希 | 191 |
| 发布时间落在本周内的原始文章 | 32 |
| 其中标题相关的本周文章 | 8 |
| 人工核验后可报告事件 | 13 |
| 事件发生型 / 报道型 | 5 / 8 |
| 周报聚类 | 12 |
| 周报可报告事件 | 13 |

说明：718 篇中只有 32 篇的发布时间落在本周，能直接支撑“本周发生”的公开原文更少，因此对缺失原文使用了**证据快照回取**，而不是把采集日当事件日。所有快照、原文哈希、证据片段哈希、来源 URL、发布时间和日期语义保存在 `data/e1/2026-10-19_2026-10-25/sources/` 与 `curated_events.json`。

---

## 3. 13 条可追溯事件

### 3.1 事件发生型（5 条，可作为“本周发生”的事实）

| ID | 事件 | 日期语义 | 来源 |
|---|---|---|---|
| E1-W42-001 | 天猫双11 于 10/20 20:00 开卖，4 小时 124 个品牌成交破亿 | event_occurrence | 36氪 |
| E1-W42-002 | 天猫双11 美妆开门红 TOP5：珀莱雅、修丽可、兰蔻、雅诗兰黛、SK-II | event_occurrence | 新浪科技/亿邦动力 |
| E1-W42-003 | 天猫双11 个护开门红 TOP5：卡诗、Off&Relax、护舒宝、潘婷、欧莱雅 | event_occurrence | 新浪科技/亿邦动力 |
| E1-W42-004 | 丸美小红笔眼霜 4.0 进入天猫双11 去黑眼圈眼霜榜 TOP4 | event_occurrence | 36氪 |
| E1-W42-013 | 丝芙兰 SEPHORiA 玩妆节 10/23–25 上海张园：4000 余名爱好者、60 余品牌、40 余场大师课 | event_occurrence | 36氪 |

### 3.2 报道型（8 条，只能证明“本周有媒体这样报道”）

| ID | 报道事实 | 日期语义 | 来源 |
|---|---|---|---|
| E1-W42-005 | 双11 预售 90 分钟，丝塔芙大白罐登付定金人数榜首 | reported_on | 36氪 |
| E1-W42-006 | 双11 平台规则转向：取消凑单与定金预售，预算转向货架场与复购 | reported_on | 36氪 |
| E1-W42-007 | 即时零售五平台加码餐食：朴朴厨房日均 2200 单、京东七鲜小厨 1800 家 | reported_on | 虎嗅/联商网 |
| E1-W42-008 | 自营零售深度定制：六成受访零售企业采用深度定制 | reported_on | 36氪 |
| E1-W42-009 | 商场 C 位转向体验与潮玩：门店从卖货转向场景 | reported_on | 36氪 |
| E1-W42-010 | AI 素材工业化：5 人团队日产千条视频，单条成本降至 5 元以内 | reported_on | 36氪 |
| E1-W42-011 | 到店团购竞争：抖省省 DAU 约 2100 万，淘宝闪购团购仍限三城 | reported_on | 36氪 |
| E1-W42-012 | 双11 需求分化：即时零售承接临时缺货，大牌美妆仍是计划性大促硬通货 | reported_on | 36氪 |

报道型事件在报告中使用时均以“据本周报道”“媒体转述”限定，不写成平台已确认的既成事实；涉及“六成企业”“5 元以内”等数字时保留媒体口径，不作为屈臣氏经营数据。

---

## 4. 拒绝与排除示例

审计 JSON：`data/e1/audit.json`；全量快照在 `data/e1/2026-10-19_2026-10-25/`。

| 示例 | 拒绝理由 | 核验结果 |
|---|---|---|
| “美团闪购双11上线直播工具并与丝芙兰、屈臣氏试点” | source_stale | 追溯原文为 CBNData 2022-11-17，不是 2026 事件 |
| “京东11.11屈臣氏抢先购预热活动上线” | event_outside_window | 事件日期 2026-10-16，早于本周起始日 |
| “京东秒送屈臣氏国庆出行个护活动” | event_outside_window | 事件日期 2026-09-30，国庆活动已结束 |

审计记录的排除原因计数：`source_stale=11`、`no_archived_source=2`、`archive_or_off_domain=159`、`duplicate_repetition=109`、`event_outside_window=12`、`not_manual_selected=7`。这些是筛选过程中的原因分布（同一事件可能命中多个维度），不对 300 条做未经核验的“全部已核验”声明。

没有对历史事件做批量 `verified` 标记；只有本轮人工核对过的 13 条进入可报告集。

---

## 5. 本轮修复的缺陷

### 5.1 发生日 vs 发布日：丝芙兰事件被错误排除

- **现象**：`E1-W42-013` 的 `effective_event_time=2026-10-23`，但追溯文章 `published_at=2026-10-26`；旧 `get_effective_event_time()` 只看 `published_at → first_seen_at → collected_at`，把事件过滤到周期外。第 04 章因此写成“直接竞对实质动作事件为零”，与事件池中的丝芙兰证据矛盾。
- **修复**：`skills/periodic_synthesis/periodic_synthesis.py` 对 `date_semantics=event_occurrence` 优先读取 `effective_event_time`，并返回来源标记 `effective_event_time`；其余事件仍沿用发布/采集时间优先级。
- **验证**：单元测试 `test_event_occurrence_wins_over_later_publication` 通过；重建后 `period_events=13`、`competitor_clusters=1`，第 04 章出现丝芙兰动作表。

### 5.2 `--rebuild-synthesis` 参数被忽略

- **现象**：CLI 传入 `--rebuild-synthesis true`，但 `_load_or_build_synthesis()` 没有 `force_rebuild` 参数，仍读取旧聚合 JSON；这会让人误以为“已重建”，实际报告继续使用旧证据包。
- **修复**：`skills/generate_periodic_report/generate_periodic_report.py` 增加 `force_rebuild` 参数，存在旧文件时仅在 `force_rebuild=False` 才复用；主入口把 `force_rebuild_synthesis` 传入。
- **验证**：修复后显式重建，`periodic_synthesis.json` 的 `event_stats.reportable_events` 从 12 变为 13，聚类从 11 变为 12。

### 5.3 竞对实体元数据缺失（E1 构建侧）

- **现象**：丝芙兰事件最初只有 `companies=["丝芙兰"]`，没有 `entities.competitors`，聚类与第 04 章无法按竞对维度取到它。
- **修复**：E1 构建脚本把 `competitors` 写入事件实体；重建后聚类 `PC_202610_0004` 带 `competitors=["丝芙兰"]`。
- **验证**：第 04 章从“零竞对动作”改为“丝芙兰一条完整动作链”，并附“情报稀疏本身是信号”的边界说明。

### 5.4 报告数字与口播稿问题（生成侧）

- 首轮报告出现无证据的“20%/60%”阈值，质检 `sendable=false`；已改为“高于测试首周基线”的表述，重新质检通过。
- 首轮口播稿 5,828 字且缺少行动段/商家动作；改为 3,682 字的受控版本，保留 3 个行动和丝芙兰案例，规则质检通过。
- 音频原为 558 秒（偏长），最终为 478.4 秒，符合 5–8 分钟目标。

---

## 6. 前后对比

| 指标 | 恢复前（W42 无可用周报） | 恢复后（隔离候选） |
|---|---:|---:|
| 可追溯事件 | 0 | 13 |
| 事件发生型 | 0 | 5 |
| 报道型（限定使用） | 0 | 8 |
| 周报聚类 | 0 | 12 |
| 直接竞对聚类 | 0 | 1（丝芙兰） |
| 报告字符 / 章数 | 无 | 16,707 / 6 |
| 编辑质检 | 无 | sendable=true, overall_quality=good |
| event_id 泄漏 / 判断标签 / 内部词 | — | 0 / 0 / 0 |
| 口播稿 | 无 | 3,682 字，含 3 行动 + 商家动作 |
| 音频 | 无 / 旧稿 558 秒 | 478.4 秒，7.3 MB |

W40 的质量样本（344 条事件、5 条可报告、4 个聚类）只作为历史对照，不作为本周真实业务周报使用；本文件只把 W42 人工恢复集当作真实周期验证。

---

## 7. 验证证据

### 7.1 单元测试

```text
PYTHONPATH=".venv_packages:." /app/venv/bin/python3 -m unittest tests.test_e1_date_semantics_and_merge -v
Ran 4 tests in 0.013s
OK
```

覆盖：事件发生日优先于后发文章、旧明确日期不升级为 verified、scored 池合并保留 business_analysis、日期上下文不把“发布研报”当事件。

### 7.2 报告/音频检查（`docs/reviews/artifacts/e1-final-checks.json`）

```json
{
  "report_chars": 16707,
  "chapters": 6,
  "event_id_leaks": 0,
  "judgment_tags": 0,
  "internal_terms": 0,
  "sephora_present": true,
  "table_columns_consistent": true,
  "quality_sendable": true,
  "quality": "good",
  "synthesis": {
    "raw_events": 13,
    "period_events": 13,
    "reportable_events": 13,
    "clusters": 12,
    "selected_clusters": 12,
    "competitor_clusters": 1
  },
  "audio_seconds": 478.416,
  "audio_bytes": 7655084,
  "script_chars": 3682
}
```

音频 SHA-256：`aa90a423dcf1825e1e781288bea9fef4ac45844d828dfcc9a298dabeb47b18a0`。

### 7.3 编辑质检

`docs/reviews/artifacts/e1-final-review.json`：

```json
{"sendable": true, "overall_quality": "good", "high_issues": [], "medium_issues": [], "low_issues": [], "failed_checks": []}
```

### 7.4 产物路径

- 周报：`reports/weekly/2026/2026-W43.md`
- 口播稿：`podcasts/scripts/weekly-2026-W43.txt`
- 音频：`podcasts/audio/weekly-2026-W43.mp3`
- 聚合包：`docs/reviews/artifacts/e1-periodic-synthesis.json`
- 审计：`data/e1/audit.json`、`data/e1/2026-10-19_2026-10-25/audit.json`
- 事件与快照：`data/e1/2026-10-19_2026-10-25/`

---

## 8. 边界与未完成项

1. **不是生产交付**：报告只在隔离目录生成；未发真实邮件，未附加 MP3 给收件人，未写正式账本，未切 Cron，未重启 PID1/容器。
2. **报告业务结论仍需人工验收**：`sendable=true` 是格式与证据门，不等于“所有经营判断都成立”。丝芙兰仅 1 天活跃、单一来源；8 条报道型事实不是事件发生证明；竞对监测覆盖仍偏平台侧。
3. **W32 历史回放仍独立**：W32 的 344 条事件、37 条 selective replay、5 条 reportable / 4 个聚类不并入本周期，也不做批量 verified 标记。
4. **未改正式生产代码**：本轮代码改动停在 `e1/real-period-recovery`，需要单独授权后才能合并。
5. **未生成真实邮件**：`send_email=false`；如需邮件，必须另做 dry-run 与显式收件人确认。

---

## 9. 生产操作授权清单（需要用户逐项批准）

以下操作**本轮均未执行**，需要单独授权：

1. 将 `e1/real-period-recovery` 的代码改动合并到生产分支。
2. 用恢复后的 13 条事件替换/补充正式 W42 账本，并决定 8 条报道型事件是否以 `article_claim` 身份保留。
3. 将 2026-W43 周报与 MP3 作为真实周期产物发布/归档。
4. 真实发送周报邮件（HTML 正文 + MP3 附件，不发 .md 附件），收件人 123399974@qq.com。
5. 切换 Cron 到新周期任务，或暂停旧周期任务。
6. 安装/持久化 supervisor 并重启 PID1（或等价进程管理）。
7. 启用真实 critical-only 告警及其去重/恢复/静默策略。
8. 执行一次回滚演练，并确认回滚版本与备份校验值。

---

## 10. 验收建议

- 先验收本文件的 13 条事件表与 3 个拒绝示例，确认“发生型/报道型”边界。
- 再读 `reports/weekly/2026/2026-W43.md` 第 04 章，确认丝芙兰动作与“情报稀疏”边界没有互相矛盾。
- 最后试听 `podcasts/audio/weekly-2026-W43.mp3`，确认 7 分 58 秒、3 个行动和商家动作完整。
- 任何生产合并、邮件发送、Cron 切换，均以第 9 节逐项授权为准。
