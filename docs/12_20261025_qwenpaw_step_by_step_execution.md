# 12｜经营情报系统分阶段执行手册（Muse负责退役，QwenPaw负责NAS优化）

> 决策日期：2026-10-25。**系统始终部署在 NAS Docker 中，完全没有 NAS 迁移事项；不使用 Codex。**
> **分工：** ChatGPT 设计/验收；**Muse 只负责 M0 一次性清理其创建的 GitHub 内容**；QwenPaw + 已在NAS配置好的 DeepSeek Flash 执行 **M1–M7**。QwenPaw不负责删除Muse文件，也不负责为模型安装/配置API。
> 权威架构：[11｜NAS架构、AIHOT对标与模型选型](11_20261025_nas_qwenpaw_aihot_architecture_decision.md)。**M0已于2026-10-25由Muse实际完成GitHub清理，ChatGPT独立复审核验通过**，见[退役与复审记录](reviews/MUSE-RETIREMENT-2026-10-25.md)。下一步**只执行M1 NAS生产只读审计**，不得重跑M0；此结论不表示NAS生产调度、模型设置或投递已被检查/修改。

## 总规则

1. **M0已完成并通过仓库侧复审；不得重做退役删除。当前阶段为M1，仅由 NAS Docker里的QwenPaw做只读现状审计，提交实际证据和报告后停下。** M2–M7只有前一阶段经ChatGPT复审才推进。Muse不再介入生产。
2. 已在NAS连接好模型，**由用户自行选择 RightCode「百亿补贴」DeepSeek Flash**；按用户提供的价目¥0.6/百万未缓存输入、¥1.8/百万输出、¥0.02/百万缓存读取，价格不以峰谷时间切换。**不编写模型连接、Key、base URL、Provider切换或分时调度任务。**
3. 所有步骤以前一阶段的NAS真实输出为准。仓库已有审计仅为历史记录，不代表生产日志和Docker挂载已被新检查验证。
4. 禁止未经单独授权启用/停用生产cron、重建容器、删除事件账本/数据卷、改SMTP投递、发送测试邮件或上传Key/Cookie/内部报告至public GitHub。
5. **各阶段都要完整写入脱敏报告，附来源分母、验证方式、失败项目和代码commit；完成即停，等待ChatGPT复审。** 不要只声称“测试通过”而无实际输入/输出与复现方法。

---

## M0｜已完成：Muse的GitHub独立试验退役（历史流程留存，不要重执行）

### M0-0 事实与待清理清单

仓库：**lovestream/watsons-retail-intel**，生产默认分支 **main**。旧 Muse 独立试验包括：
- main上的 **muse/** 目录（历史试验任务书与内容）；
- **docs/10_muse_web_independent_architecture.md**（仅与Muse网页独立试验有关）；
- README.md、docs/README.md、docs/09_20261024_platform_strategy_and_reliability.md等内仍把Muse当**现役方向**的提示和入口；
- 未合并 **Draft PR #1** https://github.com/lovestream/watsons-retail-intel/pull/1 ，分支 **muse/pilot-2026-10-24**，审查已知HEAD 8418f299059122e523ef35848027677004ba7d9f。执行前必须核验PR最新HEAD、是否仍Draft/未合并，不把旧快照当现状。

**必须保留**：NAS QwenPaw实际运行的skills/、scripts/、config/、data/、reports/、podcasts/及生产服务/定时/邮件；docs/01–09里与生产架构及历史经验相关的文档；本项目当前最新 **docs/11、docs/12**；Git历史审计链（可以在现役文档写“旧Muse试验已退役”，但不要保留指向已删除文件的现役入口）。

### M0-1 Muse 在 GitHub 独立完成的具体动作

1. **只读核对**main与PR的目录和实际差异，列出文件级删除候选。检查旧 Muse 任务是否仍处于暂停；如果平台状态不可读，只能写“未独立验证”，不得擅称已经确认。绝不启用任务。
2. **可恢复备份**：先保存main与PR的准确HEAD/SHA及待删除文件清单；原则上保留历史提交和PR审查记录。若要删除远端试验分支，必须先验证GitHub历史可恢复或备份成功，并由用户明确批准。不得清理Git历史、强制推送或删掉可追溯记录。
3. **GitHub删除**：仅删除**确定属于Muse网页独立版的**main:muse/及docs/10；修正根README、docs/README和docs/09里已经过时的Muse“优先/独立建设”引导，改为指向当前docs/11、docs/12。不得碰 docs/11/12的NAS设计/执行内容（除必要的已完成状态标记），不做全仓无差别字符串删除。
4. **逐项复核提交**：变更文件只应是Muse与明确相关的文档引用。若出现Python运行代码、NAS部署相关配置、采集源、数据账本或报告，立即停止。GitHub写入可能需要每次点击批准；Muse应完成实际提交并**再次读回**，不要把“计划删除”说成“已经删除”。
5. **处理PR #1**：确认其从未合并，在main清理/备份完成后，提交一条“退役、迁移到现有NAS/QwenPaw长期方案”的结案说明，然后 **Close without merge**。默认**保留远端分支和Git历史作为备份**；删除远端分支另需用户明确授权及可恢复备份验证。不要merge，不生成新Muse功能。
6. 若Muse没有main写权限、GitHub工具无法删除/更新、需要额外授权或PR操作失败，**直接报告具体受阻文件/动作并停止**。不要把清理重新丢给QwenPaw，也不要误称完成。

### M0-2 交付与停止

向用户/ChatGPT回报：main前后HEAD、删除文件列表、修改文档列表、GitHub读回结果、PR关闭状态与是否仍保留分支、保留历史/可恢复方式、Muse两个后台任务的可验证暂停状态及无法验证处。全部写成一份简短的**GitHub退役验收记录**（仅公开安全信息）。**Muse完成后停下，不进入M1或采集/周报。**

### 发给 Muse 的唯一执行指令（可复制）

> 请执行自己创建的 Watsons Retail Intel **Muse独立试验退役清理**，并且只做此事。阅读GitHub main的 docs/12_20261025_qwenpaw_step_by_step_execution.md 中 M0全部步骤，按真实差异删除main中的Muse专属目录与独立试验文档、修正现役README索引。你不能访问/修改 NAS QwenPaw生产系统，不得动skills/scripts/config/data/reports/podcasts，也不能合并PR。提交并读回后，给未合并Draft PR #1写结案说明、Close without merge，默认保留试验分支作历史备份。GitHub操作需要用户确认就按平台流程请求批准，无法执行就停下报告。结束给出实际HEAD、文件清单和PR状态，**不要执行M1–M7**。

---

## M1｜当前唯一待执行：QwenPaw只读审计 NAS 生产现状（M0已复审通过）

> 在已存在的NAS Docker + QwenPaw环境**只读**盘点：实际容器/持久化卷/时区、工作区dirty状态、每日与周期任务的真实启用方式、最近14/30天日采集运行成功记录、来源列表和正文可打开率、原文时效和去重事件数、评分分布、周/月/年报及邮件送达记录、历史计费和失败情况。核对 skills/run_daily_pipeline、run_periodic_pipeline、scripts/run_daily.sh、run_periodic.sh、config/model_router.yaml、config/scoring.yaml、采集器与存储实现。注意用户已配置模型，**只核验实际使用状态，不配模型、不改Key、不自动换通道。** 形成docs/reviews/NAS-M1-READONLY-AUDIT.md，依据真实数据给出 KEEP/REFINE/REMOVE/DEFER清单与工程风险；不改生产、不发信，提交后停止。

## M2｜QwenPaw：AIHOT对标与冻结回归集（不改生产）

> 阅读 https://github.com/KKKKhazix/AIHOT 公开设计资料，按来源巡检/动态频率/PASS-UNKNOWN-BLOCK/独立双评分/事件归组/SelectBench逐项比对现有Python实现，先冻结至少30条真实历史样本（重要经营事实、非相关B2C、跨年旧闻、同事件重发、不同进展、公告日与生效日不同等）。真正高价值正例没有就标unmeasured，不用虚构例子凑P1。对真实采集/提取/归组/评分的结果进行盲审，输出 docs/reviews/NAS-M2-AIHOT-GAP-AND-BASELINE.md 和优先级，提交后停止。

## M3｜QwenPaw：先解决采集漏报与来源健康

> 按M1/M2真正的失败证据小步优化官方/垂媒栏目巡检、原始发布时间/正文验证、补搜与来源健康；先看每源成功率/独立高价值事件贡献，调频不能只看抓取量；SEARCH_ONLY不得声称官方覆盖，验证码/登录受限只能记录失败。维持正式历史事件与采集缓存兼容，至少两段闭合窗口对照，提交测试和源贡献表后停止。

## M4｜QwenPaw：事件去重、八维质量门与唯一账本

> 以已有账本作为唯一权威，不要自创新的第二个正式事件库；从M2验证过的缺陷入手，沙箱中验证 article_id/event_id分离、event/announcement/publication/effective/first_seen日期、SAME_OCCURRENCE与SAME_STORY区别、后续进展和superseded、首新增重复再运行1→0、权重程序重算和hard downgrade。使用UNKNOWN复核，不把标题列举当全文事实。真实数据先快照后改，非生产运行验证通过再申请切换，提交后停止。

## M5｜QwenPaw：既有模型的使用质量、调用成本和故障控制（不接入模型）

> 用户已经在NAS配置所有需要的模型，且倾向统一使用RightCode「百亿补贴」DeepSeek Flash，**选择由用户完成**。本阶段不重新配置Key、Provider、base URL，不切换模型，不开发峰谷调度，不做三渠道采购对照。只记录既有模型实际端到端调用的输入/输出/cache tokens、响应耗时、结构化JSON成功率、错误类型、每条有效事件/每次周报成本；根据现有代码完善可观测性、明确预算上限、429/超时有限重试、失败后不发不合格报告。涉及生产改动先影子运行；一阶段提交报告并停止。

## M6｜QwenPaw：周/月/年报、音频与邮件可靠性交付（dry-run）

> 每日只入池、不发日报。周/月/年从唯一已核实事件库按闭合窗口生成事实证据包、来源覆盖统计、少而精的经营洞察（事实、机制、影响、具体动作、KPI和反证）；用现有Flash制作内容，复用已存在音频工具和邮件流程。先在NAS测试目录dry-run检查 Markdown/HTML与MP3音频，可追溯来源、没有无依据销售数据。核查日期参数、退出码、覆盖率门禁和投递去重；**未经用户另行批准不真正发送邮件**。提交后停止。

## M7｜QwenPaw：确定性调度、影子运行、验收与切换

> 在原NAS现有Docker环境里，查明QwenPaw Agent cron与其他系统调度是否重叠；按照只有一个权威调度、程序无需模型先启动的原则，选取最简单可靠的NAS任务调度方式，先与生产隔离影子运行至少一个自然周，记录启动/完成/发现质量/费用/报表音频/失败恢复。提供准确启停、恢复与回滚步骤，**用户批准后才实际改正式cron和邮件投递**，不能双跑。提交门禁评估后停止。

---

## 第一条发给 QwenPaw 的任务（仅在Muse完成M0后）

> 你一直运行在现有NAS的Docker里，没有任何迁移任务。请阅读GitHub主分支 docs/11_20261025_nas_qwenpaw_aihot_architecture_decision.md、docs/12_20261025_qwenpaw_step_by_step_execution.md 和现有代码，**本轮只执行 M1，只读NAS环境审计**。Muse的旧内容由Muse自己清理，你不要处理；NAS各模型已由用户接好并自行选择RightCode「百亿补贴」DeepSeek Flash，你不要配置模型/Key或按峰谷安排任务。提供真实日志及来源贡献、调度与存储事实、保留/改造/删除候选清单，提交脱敏报告后暂停，等ChatGPT复审。
