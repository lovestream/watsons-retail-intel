# Watson's Retail Intel — 实现与流程文档

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
| **09** | [09_20261024_platform_strategy_and_reliability.md](09_20261024_platform_strategy_and_reliability.md) | 旧 QwenPaw 自有 API 与稳定性改造参考；**Muse 混合联动设想已被第 10 篇取代** |
| **10** | **[10_muse_web_independent_architecture.md](10_muse_web_independent_architecture.md)** | **Muse 网页版独立建设：GitHub 共享规则、无本地 Agent 直连、并行研究与验收** |
| **Muse 任务书** | **[../muse/MASTER_GOAL.md](../muse/MASTER_GOAL.md)** | **可交给 Muse 网页 Agent 直接实施的完整总体目标、模块、路线、验收和首轮启动指令** |

## 当前改造方案（2026-10-24）

**Muse 独立版优先**：先读 [10 Muse 网页端独立架构](10_muse_web_independent_architecture.md)，再将 [Muse 总任务书](../muse/MASTER_GOAL.md) 交给 Muse 直接执行。**旧 QwenPaw 继续优化**时再参阅 [09 平台与 API 路线](09_20261024_platform_strategy_and_reliability.md)。两套路线均为待实施方案，不能据此认定 Muse 已授权、定时任务已创建、邮件已发送。

## 补充资料

- [顶层 README](../README.md) — 项目简介与快速开始
- [验收标准](acceptance_criteria.md) — 各环节验收指标
- [采集问题审计 (2026-08-11)](../AUDIT_20260811_collection.md) — 采集链路已发现的问题清单
- [采集优化日志](collection_optimization_log.md) / [管线质量诊断](pipeline_quality_diagnosis.md) — 历史诊断记录

## 阅读建议

1. 先读 **01**（数据流与编排），建立全局。
2. 再按数据流向读 **02 → 03 → 04**（采集→过滤→事件加工）。
3. 需要看报告本身质量时读 **05 / 06**；想排查「发出了没/发得好不好」读 **07**。
4. 要优化成本/稳定性/调度，读 **08**；准备 Muse 独立版直接读 **10 和 Muse 总任务书**。
