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
| 10（待退役） | [10_muse_web_independent_architecture.md](10_muse_web_independent_architecture.md) | Muse历史试验设计，**非现役路线；M0中备份后删除** |
| Muse历史任务书（待退役） | [../muse/MASTER_GOAL.md](../muse/MASTER_GOAL.md) | **仅为历史试验；M0中备份后删除，禁止当作现役任务书** |
| **11（当前）** | **[11_20261025_nas_qwenpaw_aihot_architecture_decision.md](11_20261025_nas_qwenpaw_aihot_architecture_decision.md)** | **NAS Docker + QwenPaw/Python当前方案、AIHOT取舍、DeepSeek三渠道人民币比较及优化门禁** |
| **12（执行）** | **[12_20261025_qwenpaw_step_by_step_execution.md](12_20261025_qwenpaw_step_by_step_execution.md)** | **M0 Muse退役安全命令、M1–M7 QwenPaw实施阶段与验收规则** |

## 当前改造方案（2026-10-25）

**唯一现役优化路线：NAS现有Docker环境 + QwenPaw/Python + DeepSeek Flash（具体模型渠道待真实API测试）。** 系统一直在NAS，无迁移事项；由ChatGPT提供GitHub方案和复审，QwenPaw在NAS逐阶段施工，不用Codex。先按[12](12_20261025_qwenpaw_step_by_step_execution.md)处理Muse历史遗留，**当前未执行生产删除、模型替换或调度更改**。Muse独立实验资料和原 docs/10 待备份后清理；09仍作为旧审计材料可参考，11/12权威优先。

## 补充资料

- [顶层 README](../README.md) — 项目简介与快速开始
- [验收标准](acceptance_criteria.md) — 各环节验收指标
- [采集问题审计 (2026-08-11)](../AUDIT_20260811_collection.md) — 采集链路已发现的问题清单
- [采集优化日志](collection_optimization_log.md) / [管线质量诊断](pipeline_quality_diagnosis.md) — 历史诊断记录

## 阅读建议

1. 先读 **01**（数据流与编排），建立全局。
2. 再按数据流向读 **02 → 03 → 04**（采集→过滤→事件加工）。
3. 需要看报告本身质量时读 **05 / 06**；想排查「发出了没/发得好不好」读 **07**。
4. 要优化成本/稳定性/调度，读 **08**；当前NAS优化请读 **11 和 12**；Muse文档属于待退役历史。
