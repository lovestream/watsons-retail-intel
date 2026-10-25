# NAS R2-A｜ChatGPT 复审与 R2-A-FIX 整改指令

日期：2026-10-25。项目：lovestream/watsons-retail-intel。

> **裁决：R2-A隔离验证与问题发现阶段通过；生产切换不通过。下一轮只批准 R2-A-FIX，不进入R2-B、R3或M2。** 系统一直在NAS Docker，QwenPaw执行、ChatGPT设计验收，不迁移、不使用Codex。用户已决定主SiliconFlow DeepSeek-V4.1-Flash、备RightCode百亿补贴；费用在供应商后台查询，**不开发本系统费用台账、日金额预算或估价器**。

## 审查证据与边界

直接读取了 [R2-A隔离分支](https://github.com/lovestream/watsons-retail-intel/tree/r2a/shadow-scheduler) 的 [完整报告](https://github.com/lovestream/watsons-retail-intel/blob/r2a/shadow-scheduler/docs/reviews/NAS-R2A-SHADOW-AND-SCHEDULER-TEST.md)、两份脱敏JSON审计、launcher/scheduler/watchdog/run_state/supervisor代码、25条测试源码和原日管线STEP_ORDER。

R2-A分支相对main已分叉（工具比较ahead 5、behind 9），后续严禁覆盖main。25 tests OK及网络模型调用来自QwenPaw的运行记录，ChatGPT没有直接进入NAS重放全部测试，不将影子成功冒充生产已恢复。

## 一、已通过的隔离成果

- 2026-10-11影子闭合日：7篇cleaned文章→50条raw事件→50条temporal/novelty/scored/analyzed；事件ID保持连续，无空evidence_text和source_url。
- 跨日复制50条事件后，novelty标记50/50 repeated、reference；原priority仍P2=11/ARCHIVE=39。事件新颖性识别初步有效，但最终reportable_count仍有漏洞。
- launcher产生running/terminal manifest与退出码，scheduler可以真正启动Python，watchdog能区分缺manifest、失败和stale heartbeat。部分实际launcher smoke成功，旧--end-step越界生成播客的副作用已在隔离分支加guard，novelty ledger_entries metadata已有改动。未安装supervisor、未切生产。

## 二、上线阻断项

### G1 P0｜核验状态未进入正式事件链，时间资格又被后续步骤重置

R2-A影子发现：**21/50缺event_occurred_at**；**20/50 unknown_fact_time**（两组可重叠，不可相加）；**50/50未生成verification_status**。注入needs_verification=true的P2事件经过score仍为P2/core，未触发降级。

更严重的交叉证据：影子 temporal 的report_eligibility为 core=22、reference=15、review=9、tracking=4，而score之后变成core=50。必须查清是否score重置前置时间/新颖性审核资格，建立“高分不得覆盖低置信资格”的单调约束。

R2-A-FIX应复用R1-FIX已测的日期冲突/传闻判据，把verify_event_claims或等效规则集成到真实extract后流程，并在temporal、novelty、score、analyze与最终报告候选池传递。published_at、event_occurred_at、announced_at/effective_at、collected_at必须分清；缺事件日期、证据不足、传闻、日期矛盾以needs_verification/review或reference保留但**不能作为已证实本期新事实**。合法未知背景可以留档，不必全部删掉。

### G2 P0｜reportable_count误把reference当作本期新事件

ops/r2a/run_state.py中的reportable_count只排除priority=ARCHIVE、eligibility=archive，却没有排除reference/review/tracking。跨日重复50/50后，理论上仍可将P2=11计为可报告，和“core=0”的结论矛盾。应与正式报告最终选稿规则建立共用判定函数：有证据、已核验、eligibility符合core、新事件/合法进展、priority达标才算本期可报告；reference仍可作为背景但不当新增新闻。负例要求50/50重复跨日reportable=0，且合法更新保留。

### G3 P0｜supervisor草稿跳过采集，可能形成另一种假成功

ops/r2a/supervisor/watsons-retail-intel-r2a.conf配置scheduler使用 --start-step extract --end-step analyze；真正STEP_ORDER在extract前还包含collect、源发现、正文补全与过滤等十余步。影子之所以成功，是因为先把cleaned历史数据复制进隔离目录。正式调度不能只从extract开始。须选择“同一调度从collect及必要前置步骤启动”或“独立采集任务有已验证成功/覆盖manifest、再由事件任务启动”的一个权威方案。补同日采集失败/采集未启动/存在旧cleaned输入负例。

### G4 P0｜watchdog脚本实际把失败吞掉，未接入通知

supervisor草稿的watchdog死循环使用“... watchdog ... || true; sleep 300”，会吞掉退出码1，且根本没有真实通知出口。必须在隔离环境证明watchdog异常状态能够进入独立、去重、可追踪的本地告警sink与恢复记录，退出/事件不能被吞掉；是否接入现有通知通道待后续生产切换授权。模拟13天无manifest以及agent回复“已启动”但没有manifest，均应critical，不再误报正常。

### G5 P1｜no_signal和LLM成功不可混为技术/内容正常

现有launcher只要上游结果ok且cleaned=0、no_signal=true，就可判正常no_signal。真实采集器全部失败导致cleaned=0时不能算“当天没有新闻”；需检查上游源/采集成功manifest。另一份真实launcher smoke的analyze阶段llm_success_count=0，但因分析事件数齐全仍标success；应区分pipeline_completed、llm_extract_health、llm_analyze_health和content_ready。可降级保留事件，但不能假装分析已经由LLM完整生成。

### G6 P1｜域外内容与抽取过细

R2-A的11条P2可报告事件混入2条明显域外：乘用车零售E20261011_0044与波司登门店E20261011_0018（2/11约18%）。中国大陆即时零售×个护美妆是硬边界；仅因“零售”“门店”不够进入新事实池。采用小范围相关性门禁，增加这两条负例与真实美妆即时零售正例，不能粗暴屏蔽整个汽车/服饰关键词以致真正的平台规则漏报。7篇产生50条事实的抽取粒度偏细需说明原因，但本轮不为降低数量而盲目改prompt上限。

## 三、下一轮唯一执行范围：R2-A-FIX

1. 继续在隔离r2a/shadow-scheduler分支；先fetch核对远端main与当前分支分叉；不得reset --hard、git clean、force push、覆盖生产脏工作树。
2. 优先修G1–G4，G5/G6补明确质量拦截/降级机制与对应回归。最少修改现有代码，复用temporal/novelty/filter/score，不创建第二份事件权威账本，不大改29步。
3. 固定2026-10-11影子窗口，对比修改前后raw数、verified/needs_verification、core/reference/review/tracking、真正reportable_count和2条域外事件的归属；另测跨日重复50/50→新事实池0，真实新事件与合法updated事件不误删。
4. 在独立目录做调度+watchdog故障注入：采集成功/失败、上游未运行、当日旧文件、no_signal、任务没启动、agent虚报已启动、watchdog告警持续/恢复。输出本地dry-run告警日志，不发真实通知。
5. 提交必要代码/测试/脱敏证据和 docs/reviews/NAS-R2A-FIX-VERIFICATION.md，写明SHA、变更文件、测试分母、已通过/待确认与回滚；完成即停止等ChatGPT复审。
6. 禁止安装/启动生产supervisor、切正式cron、合并生产main、变更Docker/事件账本/SMTP、真实报告/播客/邮件投递、补跑断档、系统模型费用记账、提前R2-B/R3/M2。

## 四、验收门槛

**必须有反例**：缺事实日期、传闻、日期冲突、needs_verification被score升格、跨日reference、乘用车零售、波司登门店、无采集却标no_signal、旧cleaned冒充当天、无manifest的假启动、watchdog异常被吞；**必须有正例**：官网规则的发布日期与生效日、即时零售个护新事实、合法updated事实、来源确实检查完毕且无新闻的正常no_signal。所有负例不进本期已核实新事实池、正例不被无故误删；跨日重复reportable应为0。模拟watchdog必须保留critical和可审计告警记录，但本轮不得真正发送。

R2-A-FIX后仍须独立复审和正式切换授权，不能自动进入R2-B。
