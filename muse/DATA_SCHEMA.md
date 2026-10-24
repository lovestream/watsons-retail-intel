# DATA_SCHEMA — Muse 独立版事件结构、去重与报告格式

> 版本：v1.0（2026-10-24）。继承原项目事件字段与 `docs/acceptance_criteria.md` 硬门槛，按 Muse 可维护的 Markdown/JSON 轻量实现。

## 1. 事件记录字段（必填标 *）

| 字段 | 说明 |
|---|---|
| `event_id` * | 稳定标识，格式 `EV-YYYYMMDD-序号`（按事件发生日），一经分配不变 |
| `fact` * | 中性事实陈述（一句话，只写已证实内容） |
| `event_date` * | 事件发生日期（YYYY-MM-DD，北京时间） |
| `published_date` | 主要来源发表时间 |
| `first_seen` * | Muse 首次发现时间 |
| `region` * | 默认 `CN-mainland`；非大陆主体不得入池（见 PROJECT_RULES §2.3） |
| `topic` * | platform_rule / platform_move / competitor_move / category_trend / policy_change / data_signal / consumer_trend / new_product / supply_chain / marketing / background_only |
| `channels` * | instant_retail / b2c_flagship / tob_distribution（可多选） |
| `subjects` * | 涉及平台/品牌/竞对主体 |
| `primary_url` * | 主证据链接（缺失 → ARCHIVE） |
| `supporting_urls` | 独立佐证链接（转载不算独立） |
| `evidence` * | 证据摘录（关键原文短句；为空 → 最高 P2） |
| `source_type` * | official / tier4_media / tier3_vertical / tier2_aggregator / unknown |
| `verified` * | true / false（已打开原文核对时间与事实） |
| `verification_level` | L1 原始报道已读取 / L2 官方信息已核实 / L3 独立多源已核实（P1 须有首发或官方链接） |
| `priority` * | P0 / P1 / P2 / ARCHIVE |
| `status` * | new / updated / superseded / archived |
| `update_of` | 若为旧事件新进展，填被更新的 event_id |
| `last_updated` * | 最近更新时间 |

## 2. 存储方式（Muse 原生，轻量可审计）

- 事件账本：`muse/events/LEDGER-YYYY-MM.md`（按月一册，Markdown 表格 + 每条事件的证据小节）。
- 机器可读镜像（可选）：同目录 `ledger-YYYY-MM.json`，供后续会话快速检索与去重比对。
- 每次运行先读最近 30 天账本再写入，防止重复记录。
- 仓库公开：账本只含公开来源事实与链接，不含任何内部经营数据。

## 3. 去重规则

> 阈值继承原系统两层口径：事件级新颖性比对用标题（中文字符）相似度 ≥0.5 ＋主体实体交集判定同一事件；周期报告聚类用标题 Jaccard 0.65 / 实体重合 0.6（`config/periodic_report.yaml`）。另继承：同一 fact 在账本出现 ≥3 天判旧闻重发；回顾/复盘类文章强信号降级、弱信号只作背景。

1. **同事件合并**：主体相同 + 动作相同 + 时间相近（同一发布周期）→ 合并，保留最早 event_date、合并来源。
2. **进展更新**：同一主体同一事项出现新事实（新数字、新范围、新时间点）→ 新记录 `update_of` 指向原事件，原事件 status 改 updated。
3. **伪新动态**：旧事件被媒体换标题重发、无新事实 → ARCHIVE 并注明 dup_of。
4. **跨源转载**：内容同源（同通稿/同采访）→ 只算一个来源，不增加 verified 强度。

## 4. 经营分析结构（每个 P0/P1 必填）

```
事实（已证实，带链接与日期）
→ 机制（为什么这个变化会产生经营影响；含替代解释）
→ 影响（具体到渠道层级与业务变量：流量/转化/客单/复购/毛利/扣点/补贴/货盘/库存/履约/会员/品牌关系）
→ 建议动作（渠道 + 具体动作 + 负责人类型建议）
→ 验证（KPI + 时间窗）
→ 反证条件（什么证据出现时本判断作废）
内部验证依赖（如需屈臣氏内部数据，单列）
```

## 5. 报告格式

### 周报（6 章）
1. 本周核心判断 2. 主要趋势 3. 平台变化 4. 竞对变化 5. 品类与渠道影响 6. 下周优先动作（含待验证假设与指标）

### 月报（7 章）
关键判断 / 平台结构变化 / 品类演变 / 竞对行为 / 经营启示 / 下月动作 / 风险与机会

### 年报（8 章）
全年关键判断 / 阶段回顾 / 品类 / 平台 / 竞争格局 / 年度启示 / 下一年动作 / 长期风险

### R1 补充（2026-10-24）

- 事件时间三字段缺一不可：event_date（事实发生日）、published_date（首发 / 转载发表日）、first_seen（本系统首次发现日）；正文展示可用简写，但账本权威记录必须补齐必填字段并做 schema 校验，不得以表格自称“完备”却缺字段。
- 每次运行附 search_run_manifest：查询词、检索时刻、命中数、候选 ID、排除理由、失败来源。

### 通用
- 显著标注：报告类型、覆盖区间（起止日期，北京时间）、覆盖率状态（normal/不足披露）。
- 试运行/演示报告必须在标题与开头显著标注性质（如「72 小时试运行，非完整周报」）。
- 正文不出现 event_id 与内部分级标签（分级只在账本与附录候选清单中）。
