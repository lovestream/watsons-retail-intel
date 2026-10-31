# W43 稳定性 10 轮实测报告｜2026-10-31

> 冻结事实包：`canonical_weekly_pack.json`（W43 2026-10-19～25，真实生产事件）
> 脚本：`scripts/measure_reader_brief_stability.py --runs 10`
> 结论：**未达 9/10 阈值，不具备并入 PR #2 的条件。**

## 1. 六次完整 10 轮结果

| # | 关键改动 | 通过 | 主要失败码 |
|---|---|---|---|
| 1 | 原始事实包（27 条，全 observed） | 0/10 | occurred_date_unbound ×8 |
| 2 | 接入 event_occurred_at | 0/10 | occurred_date_unbound ×7、status_overclaim ×4 |
| 3 | 精度门 + 周期窗口过滤 | 0/10 | BLOCKED_p09 ×8 |
| 4 | 参与度/自相矛盾校验 | 2/10 | status_overclaim ×3 |
| 5 | 事实去重 0.92 + occurred_on 可选 | 2/10 | P09 ×8（全"屈臣氏"同源）|
| 6 | 周报豁免重复旧闻 + 时间语义门 | **2/10** | BLOCKED_p09 ×7、number_not_in_fact ×4 |

**最高 2/10，从未接近 9/10。**

## 2. 已定位并修复的真实缺陷（均为确定性 bug，非调阈值）

| 缺陷 | 证据 | 修复 |
|---|---|---|
| 事实包无周期过滤，混入 2022-11/2025-08/10-18 旧闻 | F012 日期 2025-08-01、F022 日期 2022-11-11 | `build_from_synthesis` 加周期窗口 |
| `event_occurred_at` 精度不足却被当精确日 | precision=quarter 的事件写着 2026-10-30 | 精度门：仅 day 级可用 |
| 事实近似重复 | 17 条中 7 组相似度 0.83–0.99 | `_dedupe_facts` 阈值 0.92 |
| 周报误杀"本周持续发酵"事件 | 62 条旧闻 0 条入选，其中 9 条高相关 | `_is_republished_but_valid` 豁免 |
| 事实含周期外时间 | "截止11月11日""双11期间" | `time_scope` 门 + 提示词口径规则 |
| 状态靠关键词判定 completed | "已上线"即判已完成 | `infer_status` 优先级 reported>proposed>announced>completed |
| 屈臣氏被写成参与方 | 27 条 major 同源 | `participation` 字段 + `watsons_role_upgraded` 守卫（先误报后收窄）|
| 同一事实既 omitted 又被引用 | run_01 报出 | `omitted_but_used` 校验 |

**事实池质量提升**：W43 事件池 34 → 39 条；事实包 27 → 10 条（去重+时间过滤后），全部落在周期内，状态分布 reported 6 / announced 1 / proposed 4，**completed 为 0**（不再靠关键词臆断完成）。

## 3. 未收敛的根因（本轮无法解决）

1. **上游 `event_occurred_at` 无日级精度**
   487 条事件中 day 级仅 118 条（24%），且集中在 ARCHIVE 事件；P1/P2 中仅 38% 有日级时间。
   → 竞对卡片 `occurred_on` 天然缺证据。

2. **P09 判定已进入"无限精确化"循环**
   近轮批评为：「原文没有'名单'一词」「未证明是平台正式上线产品」「'当天'未说明是哪天」。
   这些是**对同一句话反复要求更细粒度限定**，任何一轮修订都会引出下一轮新问题。
   → 需要产品决策：要么明确"证据阈值"，要么接受 P09 仅作 advisory。

3. **素材量不足导致的连带效应**
   W43 只有 10 条可用事实，模型必须在少量素材上写满 700–1200 字，
   天然产生"扩展性表述"，而这些扩展正是 P09 攻击点。

## 4. 测试结果（NAS 隔离环境实测）

| 测试 | 结果 |
|---|---|
| `tests/reader_brief/test_reader_brief.py` | 18/18 PASS |
| `tests/test_production_delivery_gate.py` | PASS |
| `tests/test_temporal_fact_validation.py` | PASS |
| `tests/test_llm_provider_guard.py`（main 分支） | 4/4 PASS |

## 5. 未完成项（明确未做）

- 最终稿音频生成（PR#3 `audio.py` 未接入链路）
- 文件篡改拒发端到端测试（单测通过，未做真实 SHA 篡改实验）
- 原 47 项测试在 NAS 的完整重跑
- 10-12～18 缺口对月报的影响分析
- 真人音频试听

## 6. 建议

**不要**继续在提示词/校验上加码。下一步应由你决策二选一：

- **A（推荐）**：把 P09 从"硬门"降为 advisory（提示不阻断），交付以**确定性校验**（事实绑定/数字/日期/状态）为准；这样 W43 的 10 条事实包 + 确定性门是能稳定通过的。
- **B**：明确写死 P09 的判定标准与停止条件（例如"仅拦截可证伪的错误"，不接受主观精确化要求），并限制最多 2 轮。

在此之前，PR #2/#3 均保持 Draft，不合并、不发信。
