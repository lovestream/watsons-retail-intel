# Watson's Retail Intel — 实现与流程文档

> **2026-10-28 最新用户体验要求**：[精简手机邮件预览](reviews/NAS-E1-W43-EMAIL-BODY-2026-10-28.html)及[周期邮件UX专项整改](reviews/NAS-PERIODIC-EMAIL-UX-REDESIGN-2026-10-28.md)。内部证据/评分/日志不进入收件邮件；一页经营重点、竞对纵向卡、3条行动、无全量来源附录；修复代码在`fix/periodic-email-mobile-digest`隔离分支，生产未启用。


> **2026-10-28 编辑验收**：[E1真实周期周报/播客独立审查](reviews/NAS-E1-CHATGPT-EDITORIAL-ACCEPTANCE-2026-10-28.md)：2026-10-19～25应为W43；部分自动写入的“人工verified”与0–5评分量纲不一致，正式发送前需修正证据与编辑口径，用户须实际听音。E1本轮未授权生产切换。


> **2026-10-27 当前审查**：[R2+R3+M2整合阶段复审与E1唯一安全任务](reviews/NAS-ONEPASS-INTEGRATION-CHATGPT-REVIEW-2026-10-27.md)：W32历史回放344条→37条规则核验→5条可报告；真实W40仍无达标事实包，已生成新音频仅对应W40冻结优质样例。下一步原文逐条证据恢复，非批量verified；生产NO-GO。


> **2026-10-26 晚间最新审查**：[R2+R3+M2一次性交付复审](reviews/NAS-ONEPASS-CHATGPT-REVIEW-2026-10-26.md)：R2备份、R3 W40质量门、M2 AIHOT advisory已提交；仍需补齐测试源码、解决TTS 403/禁止旧音频冒充新音频、修SelectBench证据映射并做三分支集成验证；生产NO-GO。


> **2026-10-26 DeepSeek执行总指令**：[13｜一次性交付：安全生产恢复准备、R3周/月/年报播客、AIHOT精准采集优化](13_20261026_deepseek_one_pass_recovery_r3_aihot.md)。安全开发连续推进；生产重启、调度切换与真实邮件待单独授权。


> **2026-10-26 最新审查**：R2-B切换准备已完成但生产仍NO-GO，见[正式复审](reviews/NAS-R2B-CUTOVER-CHATGPT-REVIEW-2026-10-26.md)；[AIHOT对标落实差距](reviews/AIHOT-ADOPTION-GAP-2026-10-26.md)明确区分“已有类似能力”“设计已借鉴”“专项未实施”。

本目录是「即时零售 × 个护美妆经营情报系统」的 **详细实现与全流程中文文档**，以仓库真实代码、配置与运行产物为准逐子系统审计整理。用途：供开发维护与 AI（如 ChatGPT）分析问题、制定优化方案、再分步解决。

> 说明：文档里多处标注了「代码 vs 文档/SKILL.md 的偏移」。**凡标注「以代码为准」处，均以 `.py` 源码与 `data/runs` / `data/logs` 实测产物为准**，因为一部分 `SKILL.md` 与历史描述已经滞后。

## 文档清单

| # | 文档 | 覆盖内容 |
|---|------|----------|
| 01 | [01_architecture_and_orchestration.md](01_architecture_and_orchestration.md) | 系统定位与目标、顶层模块划分、日报/周期管线完整编排、失败/降级语义、agent 层 vs 脚本层 LLM 分离、ASCII 数据流图 |
| 02 | [02_collection_pipeline.md](02_collection_pipeline.md) | 采集器总览（web_monitor / cloakbrowser / xcrawl / rsshub / tavily_gap / tophub）、来源清单、搜索词策略、新鲜度日期门（channel 1d / search 3d / broad 7d）、合并去重与账本 |
| 03 | [03_filtering_and_quality.md](03_filtering_and_quality.md) | 过滤引擎多层规则、关键词命中与排除词（资本市场/财报/IPO/旧闻/时间未知）、main/reference/reject 三池、质量漏斗、来源健康评级 |
| 04 | [04_event_processing.md](04_event_processing.md) | 事件抽取、时效/事实校验、新颖性去重、评分与优先级（stock/竞对IPO/港台拦截）、经营影响分析、`orig_idx` 索引、event_id 规则与数据结构 |
| 05 | [05_daily_report_generation.md](05_daily_report_generation.md) | 日报 10 节结构、规则稿+LLM 稿双轨、editor_review 防幻觉、成品清洗、无信号日报、2026-08 停发影响 |
| 06 | [06_periodic_report_generation.md](06_periodic_report_generation.md) | 周/月/年报调度、周期聚合层、覆盖度门禁、四阶段生成、周报 6 章、决策建议贴合屈臣氏三层渠道群、真实失效案例 |
| 07 | [07_podcast_and_email.md](07_podcast_and_email.md) | 口播稿规格与长度校验、TTS、播客质检、HTML 邮件（仅 MP3 附件）、幂等与安全门、告警、重复投递风险 |
| 08 | [08_llm_config_cron_data.md](08_llm_config_cron_data.md) | 配置体系（全部 YAML）、LLM 客户端与回退、调度 cron、数据模型与账本、健康检查与告警、已知运维问题 |
| **09** | [09_20261024_platform_strategy_and_reliability.md](09_20261024_platform_strategy_and_reliability.md) | **历史审计参考，不是现役指令**；Muse独立/混合设想均已退役 |
| 已退役 | Muse 独立试验（main 的 muse/ 与 docs/10 已于 2026-10-25 按 M0 删除） | 历史见未合并 Draft PR #1 与保留的远端试验分支，不再是现役入口 |
| **11（当前）** | **[11_20261025_nas_qwenpaw_aihot_architecture_decision.md](11_20261025_nas_qwenpaw_aihot_architecture_decision.md)** | **NAS Docker + QwenPaw/Python当前方案、AIHOT取舍、DeepSeek三渠道人民币比较及优化门禁** |
| **12（执行）** | **[12_20261025_qwenpaw_step_by_step_execution.md](12_20261025_qwenpaw_step_by_step_execution.md)** | **M0 已由 Muse 完成 GitHub 遗留清理；M1–M7 由 QwenPaw 优化 NAS 现有系统** |




> **2026-10-25 最新权威阶段与模型决策**：M0/M1/R0已完成；R1隔离测试已报告SiliconFlow主通道成功，**本轮只批准R1-FIX整改，未合并生产main**；R2/R3/M2仍暂停。**主：SiliconFlow（硅基流动）DeepSeek-V4.1-Flash；备：RightCode「百亿补贴」DeepSeek Flash**，有限付费回退、不分时切换。参阅[正式R1复审与整改指令](reviews/NAS-R1-CHATGPT-REVIEW-2026-10-25.md)。

## 当前改造方案（2026-10-25）

**唯一现役优化路线：一直部署在 NAS Docker 中的 QwenPaw/Python；SiliconFlow DeepSeek-V4.1-Flash 为主、RightCode「百亿补贴」DeepSeek Flash 受控备用。** 不迁移NAS、不使用Codex、不重新配置模型接口、不按峰谷时段切换。**M0 已由 Muse 完成 GitHub 试验清理**；M1、R0已通过，R1隔离测试完成但现需先整改R1-FIX；后续阶段经ChatGPT验收才推进。ChatGPT负责GitHub设计和复审；[12号手册](12_20261025_qwenpaw_step_by_step_execution.md)明确双方边界。Muse 内容已于 2026-10-25 按 M0 从 main 删除，NAS 调度未变更、未发送邮件；历史docs/09只能用于追溯，以11/12为准。

## 补充资料

- [顶层 README](../README.md) — 项目简介与快速开始
- [验收标准](acceptance_criteria.md) — 各环节验收指标
- [采集问题审计 (2026-08-11)](../AUDIT_20260811_collection.md) — 采集链路已发现的问题清单
- [采集优化日志](collection_optimization_log.md) / [管线质量诊断](pipeline_quality_diagnosis.md) — 历史诊断记录

## 阅读建议

1. 先读 **01**（数据流与编排），建立全局。
2. 再按数据流向读 **02 → 03 → 04**（采集→过滤→事件加工）。
3. 需要看报告本身质量时读 **05 / 06**；想排查「发出了没/发得好不好」读 **07**。
4. 要优化成本/稳定性/调度，读 **08**；当前NAS优化请读 **11 和 12**；Muse 试验文档已退役并从 main 删除。


> **2026-10-25 最新复审优先**：R1-FIX 已按用户确认的范围通过，**不再要求系统自建费用台账、日金额预算或单价估算**；SiliconFlow 主，RightCode 百亿补贴备。下一步只做**R2-A 隔离日管线与调度/健康检查验证**，不改正式Cron/生产账本/SMTP、不中途合并R1。详见 [R1-FIX最终审查与R2-A任务](reviews/NAS-R1-FIX-CHATGPT-REVIEW-2026-10-25.md)。
