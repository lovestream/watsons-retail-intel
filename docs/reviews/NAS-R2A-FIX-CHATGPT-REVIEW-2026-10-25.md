# NAS R2-A-FIX｜ChatGPT 独立复审、剩余边界与 R2-B 上线前门禁

日期：2026-10-25。项目：lovestream/watsons-retail-intel。

> **正式裁决：R2-A-FIX 隔离整改范围基本通过；尚不具备直接生产部署许可。** 不要求重新做一轮全量R2-A整改。剩余两项真实代码边界，放入R2-B**部署前的隔离验证与最小修复**。**R2-B正式安装supervisor、启停cron、合并主分支、修改持久数据/投递配置均未获授权**。当前生产仍未恢复，本审查也不代表生产模型/定时邮件已可用。
>
> 用户最新既定方案：NAS Docker中QwenPaw实际执行，ChatGPT设计/验收；主模型 SiliconFlow DeepSeek-V4.1-Flash，备用 RightCode「百亿补贴」DeepSeek Flash。费用与赠送额度用户直接查看服务商后台，**不做系统内计费台账、日金额预算或估价器**；保留运行健康所需调用次数与错误类型。始终不迁移NAS，不用Codex。

## 一、证据核验

- 分支：[r2a/shadow-scheduler](https://github.com/lovestream/watsons-retail-intel/tree/r2a/shadow-scheduler)；提交：[443b68a7ae937e6e2143f63e14a93e950eeaad89](https://github.com/lovestream/watsons-retail-intel/commit/443b68a7ae937e6e2143f63e14a93e950eeaad89)；GitHub比对**分支HEAD与该SHA identical**。该提交变更20个文件，前一提交5f78c1a；分支与main存在分叉（ahead6 / behind10），**禁止force push/直接假设能快进合并**。
- 逐项读取[QwenPaw整改报告](https://github.com/lovestream/watsons-retail-intel/blob/r2a/shadow-scheduler/docs/reviews/NAS-R2A-FIX-VERIFICATION.md)、固定历史影子与次日重复审计JSON、skills/report_policy.py、temporal_fact_validation、novelty_check、score_events、generate_daily_report、collect_daily_articles、ops/r2a四模块及supervisor草稿、test_r2a.py与test_r2a_fix.py。
- 源码共有**27+16=43个test_用例**；执行者报告43 tests OK。ChatGPT能核验代码、测试覆盖与提交内容，**没有进入NAS重跑43项或实际付费LLM调用**。

## 二、G1–G6 原定整改验证结论

| 项 | 判定 | GitHub客观证据 |
|---|---|---|
| G1 时间与核验 | **隔离主逻辑通过，保留一处日期回退边界** | 50条事件均持有verification_status，32条规则校验为verified，18条needs_verification；temporal→novelty→score不再全重置core。0038/0039没有可用日期仍待核验。 |
| G2 可报告池 | **通过隔离数据** | 共享is_reportable_event：P0/P1/P2、core、verified、new/updated、有fact/evidence/URL；2026-10-11真正reportable 5条，次日50/50重复→0。 |
| G3 调度采集入口 | **通过代码/模拟，未生产验证** | scheduled拒绝extract直启；supervisor草稿从health_check而非extract启动；scheduler直启Python。残留的采集“跳过/旧文件”成功边界需修。 |
| G4 watchdog | **本地告警闭环通过，真实通知尚无** | watchdog$status、alerts/events.jsonl/current.json/preview，去重及recovery测试，草稿不再以shell“|| true”吞码；但没有真正通知用户的独立送达链。 |
| G5 运行/LLM分层 | **通过隔离状态区分，仍需全链路质量门** | collection_health、llm_extract_health、llm_analyze_health、analysis_mode及degraded_analysis；LLM降级可完成池但warning不称全LLM成功。 |
| G6 域外过滤 | **通过当前固定反例** | 乘用车0044、波司登0018、外域0047/0048/0050进入ARCHIVE；即时零售正例保留，不盲删整个关键词。 |

### 固定窗口数据（对两份审计JSON已独立核对）

- 2026-10-11：7 cleaned → raw/temporal/novelty/scored/analyzed均50，**ID无丢失/重复**；verification_status为verified32 / needs_verification18；最终资格archive40/reference1/review3/tracking1/core5；**reportable_count=5**，ID为0026、0023、0036、0024、0035。
- 2026-10-12跨日回放：novelty repeated50，最终archive40/reference10，**reportable_count=0**。事件优先级数仍有P1/P2，但不会因此计入本期新事实。
- **不能把“32条verified”解释成32条人工核实真新闻**：它是来源/日期/证据字段和传闻等**确定性规则校验通过**；未保证事实本身经独立来源证实，也未检验分析建议正确性。五条可报告也只是“满足目前代码准入条件”，不是已获周报审校批准。

## 三、两项可从源码直接复现的残留阻断边界（R2-B部署前修）

### B1｜只有文章发表日期，可能仍被标成已核验事件

真实路径：temporal_fact_validation.py 的 validate_event_times 在没有 event_announced_at 时会将文章published_at作为announced_dt；随后 event_time_source 设为published_at。skills/report_policy.py evaluate_verification() 若发现announced，直接选它为anchor且把date_source标published_at，但只对published_fallback/collected_fallback/inferred_content_date添加“不明确”原因，**未对published_at本身添加原因**。因此**一条没有事件实际发生/明确公告日期的文章，可能仅凭文章发布日期被判verified**。固定审计中 verification_date_source_counts 显示 published_at=2；这不证明两条均进入最终5条，但揭示真实边界。

**最小整改**：用来源证明event_announced_at是真实公告原文时间，不能用article published_at的临时填充值证明事实发生/发布。给published_at来源的事件加needs_verification或review，直到有明确事件日期/公告证据；合法“发布即宣布”的规则公告需单独给有原文日期的正例，不能一概将正式官方公告误杀。补发布日≠事件日、仅有文章发表日、正式公告有明确日期三个测试；修前修后对比5条reportable，若真实新事件减少需解释证据。

### B2｜采集未真正成功，旧cleaned存在时仍有假成功路径

ops/r2a/launcher.py::classify_outcome()在start_step=collect时，required_critical**没有包含collect**；在start_step=health_check时虽要求出现collect，但允许其status=skipped/warning，且在非no_signal的结尾**没有强制collection_health==ok**。如果工作目录已有该日旧cleaned/events产物，采集步骤跳过或失效，后续继续消费缓存数据且result.ok为true时，仍可能status=success。scheduled guard要求从health_check/collect开始，并不等于当次collector确实执行成功。

**最小整改**：scheduled终态要求collect实际执行、来源检查可验证、输出与当前run_id/采集窗口一致；如果collect被skipped/failed、source_total=0、全部源出错或当次没有采集完成凭据，标failed/degraded且不能伪造no_signal。需要区分“确实检查来源但合法0篇”和“根本没有检查来源”。补collect skipped+历史cleaned、collect failed+旧事件池、无可检查source、真实成功但零新闻四种测试。应避免仅用文件mtime判断全部事实，优先明确产物run_id/manifest。

### 其他后续非本阶段生产许可事项

1. 实际supervisor尚未安装、NAS宿主持久挂载和重启后的恢复未验证、旧Agent cron可能与新scheduler双跑；R2-B须先完成启停/独立锁/回滚清单。
2. watchdog只是**本地告警sink，不等于真正送达用户**。R2-B必须明确真实通知通道的授权、去重、未启动/自身故障自监测；首次正式启用前测试实际接收并得到用户授权。
3. llm_extract_health / llm_analyze_health 目前基本以某步骤llm_success_count>0区分ok/degraded；**1/50成功仍可能显示ok**。保留成功数、尝试数、规则兜底比例，接入有分母的告警门，避免再次掩盖大面积退化。
4. 日报只做事件入池、不发送日报；最终用户关心**周/月/年报与音频/邮件**。不能因为日报top signals已用共享is_reportable_event，就认为所有周期聚合和发送链路已统一使用该准入规则。R3应从正式报告读取“证据已核验的本期事件”，最终清洗后再审稿（W40问题），并验证TTS、发送幂等。
5. 固定7篇文章的小窗口不能证明长期信源召回质量；后续M2基于AIHOT样本/真实采集改善，保留高价值背景而非盲目压低数量。

## 四、下一阶段建议与授权边界

**本次确认R2-A-FIX任务完成，建议进入R2-B“上线前准备/隔离验证”，但尚未批准其真实生产启停。** 如果用户确认继续，让NAS内QwenPaw：

1. 在现有r2a/shadow-scheduler隔离分支以最新main为对照，**只修B1、B2并补必要小测试**，另检查LLM成功率分母；不重新运行几十次LLM或重复大范围采集。
2. 完成NAS宿主上Docker真实挂载/凭证读取方式/supervisor可用性/当前agent cron状态**只读**审计，给出独立、可逆、无双跑的准确部署/回滚步骤；在隔离目录用模拟器验证计划调度、启动、重启、失败告警和恢复，**不得安装/启停正式任务**。
3. 确定“报告质量是否合格”的状态字段及周期报告准入的后续R3需求，不能让历史review/reference/needs_verification靠P1评分进入周报。
4. 提交 docs/reviews/NAS-R2B-PREFLIGHT-VERIFICATION.md，含B1/B2前后对照、全部测试、日调度和告警生存性、正式生产切换手册**草案**、依赖用户授权事项；GitHub独立分支提交后停止等ChatGPT再复审。

**不得擅自**：合并main、安装生产supervisor、停旧cron/启新cron、更新正式事件账本或邮件、发送真实告警、动模型/Key、重建NAS容器、改定价或添加费用统计。生产部署另需用户明确批准。

