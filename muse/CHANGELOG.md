# CHANGELOG — Muse 独立版

## 2026-10-24 · v0.1（S0 接管 + S1 启动）

- 读取并提炼原仓库规则：MASTER_GOAL、docs/01–10、acceptance_criteria、config（sources / source_packs / keywords / scoring / search_policy / periodic_report）。
- 建立 Muse 版基础文件：README、PROJECT_RULES v1.0、SOURCE_STRATEGY v1.0、DATA_SCHEMA v1.0、OPERATIONS v1.0、TEST_PLAN（含 S0 实测结果）。
- 能力实测：GitHub HTTPS 读取 ✅；git 直推 ❌（SSH 被重置、gh 未登录）；GitHub 连接器 ⏳ 未连接，已向用户发送授权链接；网页搜索 ✅；持久化（工作区+记忆）✅；cron 平台能力 ✅（本项目任务待 S1 后创建）。
- 启动 S1：最近 72 小时（2026-10-21 13:15 ~ 2026-10-24 13:15，北京时间）真实搜索与首次试运行报告。
- 未触碰：原 QwenPaw 任何代码、配置、调度、账本与报告目录；未发任何邮件；未启用播客。

## 2026-10-24 · v0.2（S1 试运行完成 + GitHub 打通）

- S1 完成：四维度共约 80 组查询，原始候选 8 条，跨维度去重合并 1 条（妍丽处罚双维度命中），采纳 P1 ×2（浙江化妆品监管改革执行进展；天猫双 11 开卖 4 小时美妆个护数据）、归档留痕 ×4、待核实线索 ×1（淘宝闪购佣金传闻，未证实）、P0 ×0。产出 `events/LEDGER-2026-10.md`、`reports/PILOT-2026-10-24-72H.md`、`runs/RUNLOG.md`。主编亲自复核 3 条核心来源原文。
- GitHub：用户完成 OAuth 授权与仓库选择后，连接器读取实测 ✅；写入改走独立分支 + PR（不直接动 main）。git 直推仍不可用（SSH 被重置）。
- DATA_SCHEMA 去重阈值按原系统两层口径修订（事件级标题相似度 0.5；周期聚类 Jaccard 0.65）。
