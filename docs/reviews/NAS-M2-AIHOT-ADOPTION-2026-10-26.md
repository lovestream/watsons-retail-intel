# M2：AIHOT 可用做法的隔离采用与边界

日期：2026-10-26  
分支：`m2/aihot-adoption`  
结论：**采用 tri-state 预筛、独立来源计数、事件关系与 source-yield 审计；全部保持 advisory，不打开生产自动拦截。**

## 1. 采用的内容

- `skills/m2/tri_state_prefilter.py`：PASS / BLOCK / UNKNOWN 三态。只把明确命中
  “即时零售/个护美妆 + 具体商业动作”的文本标 PASS；股票/资本、招聘/加盟、港台海外、
  否定语义标 BLOCK；证据不足或语义冲突进入 UNKNOWN 复核队列，不做静默丢弃。
- `skills/m2/source_yield.py`：只读 source coverage/yield 审计，按来源统计 events、
  unique URLs、verified、reportable、活跃日期；不写账本。
- `skills/m2/event_relationship.py`：same_event / update / related / unrelated，并只按
  独立域名计数，避免同文转载把 heat 抬高；heat 与 editorial priority 分离。
- `skills/m2/selectbench.py` + `tests/m2/fixtures/selectbench.jsonl`：24 条手写、
  可追溯到 evidence URL 的 fixture。
- `skills/filter_relevant_articles.py`：接入 tri-state **annotation**；现有硬过滤仍是
  生产判定，annotation 不改变 ARCHIVE/PASS 结果。

## 2. 验证

- SelectBench 24/24：`precision_pass=1.0`、`recall_pass=1.0`、`precision_block=1.0`、
  `false_block=0`、`false_pass=0`。
- 但 24 条为手工小样本，`safe_to_enable=false`（阈值要求 ≥40 条且 precision≥0.90、
  recall≥0.80、block precision≥0.95、false_block=0）。**不把 24/24 当作生产精度。**
- M2 单测 9/9 通过；本分支全仓 62 项通过（M2 新增 + 既有 R1/R2A 集）。
- 负例覆盖：股价续创新高、招聘、香港本地、否定语义、空正文。
- 在 `periodic_synthesis` 中只新增独立域名计数与 heat 分离；不改变
  `report_eligibility`/`verification_status`/priority 的任何判定。

## 3. 生产 source audit 的真实结果

只读审计生产账本（2026-10-26）：

- 67 个来源，6,235 条事件；
- `total_reportable=0`，抽样 12 个来源的 `verified=0`；
- 原因不是 M2 预筛，而是生产事件普遍缺少 `verification_status=verified`（当前数据里
  该字段为空/未设置）。

因此本轮**不能**声称 M2 提高了同窗口报告质量或价值；只能交付隔离机制和审计工具。
在核验链路恢复、出现 `verified + core + new/updated` 事件之前，任何“采纳后质量提升”
的数字都会是伪造。建议保持 advisory，先修事实核验，再用同一窗口做 A/B。

## 4. 边界

- 没有启用生产自动拦截；没有修改生产账本、报告、邮件或 Cron。
- SelectBench 是 fixture，不是生产集；source-yield 是只读审计，不是成本/收益系统。
- 生产采用需要第二次批准：更大人工标注集 + 同窗口对照 + 回滚开关。

## 5. 证据

- `docs/reviews/artifacts/M2-SOURCE-YIELD-2026-10-26.json`
- `tests/m2/test_m2_adoption.py`（9 项）
- `tests/m2/fixtures/selectbench.jsonl`（24 条）
