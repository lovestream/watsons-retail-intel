# 13｜DeepSeek / QwenPaw 一次性交付总指令：生产恢复、R3播客、AIHOT精选优化

> **2026-10-29 覆盖此前所有“邮件修CSS/取前三句”阶段任务**：最新业务内容＋移动邮件设计与免费TTS联合执行见[15号权威指令](15_20261029_periodic_email_editorial_system_and_free_tts.md)。先重构读者简报内容链路，不能用禁词零命中/截图无溢出替代经营事实正确性；免费离线TTS在同一NAS上对照AISHELL3/Melo。维持隔离开发、生产操作需授权。


> **2026-10-28 读者邮件反馈优先级（最新）**：不要把内部审计要求写给邮件收件人。[新版W43手机邮件预览](reviews/NAS-E1-W43-EMAIL-BODY-2026-10-28.html)已按用户意见直接精简；[UX审查和执行规范](reviews/NAS-PERIODIC-EMAIL-UX-REDESIGN-2026-10-28.md)为最新接续任务。邮件正文禁出现P1/P2、评分、人工/官方核验长解释、完整来源清单与附录；竞对改竖向卡片；删掉整章下周验证和“为什么下一工作周关键”。源材料与审计留后台。邮件renderer已在隔离分支`fix/periodic-email-mobile-digest`，需用真实W43及月年报样本验收，不得动生产或发信。


> **2026-10-28 最新 E1 编辑验收**：已收到2026-10-19~25真实周期周报与MP3隔离候选，但[ChatGPT人工内容审查](reviews/NAS-E1-CHATGPT-EDITORIAL-ACCEPTANCE-2026-10-28.md)确认：ISO周次是**W43非W42**；E1手写`weighted_score=74–92`与生产0–5八维评分冲突，全13条P1且`verified_by=E1-manual`是脚本写入，不等于业务人员签字；报道型内容不能升级成平台官方确认；口播有过期“本周内”行动。现在请DeepSeek在隔离分支**一次性做最小编辑修正**后重新出对应MP3，供用户/业务收件人试听签收；真实账本、main、Cron、PID1、SMTP仍未获授权。


> **2026-10-27 最新执行优先级（覆盖本文此前D1–D5）**：R2+R3+M2已在`integration/onepass-20261026`汇总，测试71项由QwenPaw报告通过，SiliconFlow新TTS已跑通。**现在唯一待推进的隔离内容工作是[E1：真实目标周事件原文证据补全及R3全链路](reviews/NAS-ONEPASS-INTEGRATION-CHATGPT-REVIEW-2026-10-27.md)**。特别注意：344/37/5发生在**W32**，不是W40；W40音频来自样例稿。先恢复真实目标周证据，修回放脚本日期角色与分析文件覆盖，最终真实稿才能配音。**不自动切生产、合并main、修改Cron或发送邮件**。


> **2026-10-26 最新复审与返工边界**：本文件A–D三工作包均有隔离交付。新验收详见[一次性交付复审 D1–D5](reviews/NAS-ONEPASS-CHATGPT-REVIEW-2026-10-26.md)。现在连续修正：R3/M2测试源码GitHub缺失（gitignore test_*.py）、R3真实TTS 403/旧MP3不可作本期音频、M2 SelectBench source_url/evidence_url字段错配、R2+R3+M2兄弟分支尚未集成、supervisor配置位于非持久层。只做安全开发/集成验证；生产切换仍须明确授权。


> **最新执行任务（2026-10-26）**。发起人要求尽快把系统做成真正可用的服务，**不要再把每一项无风险代码改动拆成新申请、反复请示**。DeepSeek（NAS Docker内的QwenPaw）是执行者，ChatGPT做架构与最终验收。
>
> 权威输入：[R2-B-CUTOVER正式复审](reviews/NAS-R2B-CUTOVER-CHATGPT-REVIEW-2026-10-26.md)、[AIHOT落地差距](reviews/AIHOT-ADOPTION-GAP-2026-10-26.md)、[R2-B切换准备](https://github.com/lovestream/watsons-retail-intel/blob/r2b/cutover-prep/docs/reviews/NAS-R2B-CUTOVER-READINESS.md)、[R1–R2已验证分支](https://github.com/lovestream/watsons-retail-intel/tree/r2b/cutover-prep)、[11号架构](11_20261025_nas_qwenpaw_aihot_architecture_decision.md)。遇到旧docs/12历史状态描述时，以本文优先。
>
> **技术基线**：始终在原NAS Docker运行，不迁移、不启用Codex/Muse。主模型 SiliconFlow（硅基流动）DeepSeek-V4.1-Flash；备模型 RightCode「百亿补贴」DeepSeek Flash。用户在供应商后台看费用，**不建设费用台账、每日金额预算、计价器，也不按峰谷调度**。每日只采集/加工/积累，不发日报；目标为**周/月/年经营情报（原6/7/8章）、中文播客MP3、授权邮件**；中国大陆即时零售×美妆个护、屈臣氏业务相关。

## 总工作规则：连续执行，只有高风险生产动作需暂停

- **本次直接授权范围（无需每完成一小项都回来请示）**：GitHub内创建隔离分支/PR与文档，安全读取生产现状，完成代码修复与单测，脱敏冻结样本复核，独立测试目录的模拟采集/报告/音频/SMTP dry-run，修正备份脚本，**在NAS受控持久目录做独立的只读式数据拷贝备份和SHA/恢复到staging验证（不覆盖原件）**，准备好文件级发布候选及安全回滚清单。
- **当前不直接授予的高风险动作**：修改现役QwenPaw/PID1 supervisor配置、重启容器/PID1、安装/启动正式定时服务、启停任何真实Agent Cron、合并远端main或将隔离代码覆盖生产checkout、改正式事件账本/持久配置、修改模型Key/SMTP、发出真实周报/音频/告警邮件、补跑历史正式缺口、向公开仓库上传私密原文/密钥。这些动作按下文**生产切换闸门**一次性列出，交用户明确批准；未批准不能执行。
- 在已授权的安全开发范围内**自动连续推进A→B→C→D**，不因已知阶段的中间小问题或Git API读写而反复要求用户确认；遇技术障碍先尝试安全替代方案，留具体可复现证据。任何阶段不能称生产已恢复，除非有真实运行证据。
- **每个交付有具体可验证的GitHub分支、commit、测试和报告**。尽量不破坏NAS production dirty checkout。远端GitHub main存有较新复审文档；R2代码在r2b/cutover-prep，**分支已分叉且生产HEAD=2464df9并有动态变化的71项左右dirty文件**。先fetch/比对；禁止`reset --hard`、`git clean`、`git push --force`、直接checkout覆盖生产；若TLS不可用，在GitHub API提交后务必读取远端sha/tree校验。

## A｜先把上线安全问题一次搞定（最高优先级）

1. **真实全量备份而非只备份Git dirty**：现有`ops/r2b/backup_worktree.sh`只保存`git diff --binary HEAD`和`git ls-files --others --exclude-standard`，忽略`.gitignore`里的`data/raw|cleaned|events|runs|logs|enriched`及reports/podcasts；原1.8GB左右正式data**不在71项清单覆盖范围**。修正脚本和恢复说明：区分A)版本库已跟踪修改、B)未跟踪文件、C)被忽略但必须保留的业务数据/报告/音频/事件账本/投递记录、D)Secret和部署配置。对C制作独立快照或使用NAS已有可信的快照机制，核对SHA/文件数/恢复到空staging，不能只声称“tar能列出”。**不复制超出必要权限的Secrets到Git或开放目录**，在NAS本地限权保存。全过程不删除/修改原文件、拒绝覆盖旧备份。备份目录必须在原项目之外且位于持久卷、先检查可用容量；备份并非容器外异地副本，应写清此限制。
2. **只读验证实际运行控制面是否可靠**：检查PID1的`/etc/supervisor/conf.d/supervisord.conf`处于哪个Docker层、容器重建/升级后能否保留新配置、控制socket、QwenPaw主app/dbus/xvfb/xfce4受影响范围、crash/restart、回滚办法。无法从容器确认宿主volume/restart policy时明确列出宿主需要的**唯一、最小核实项**；不要臆称容器内探测DSM失败就证明NAS宿主调度不存在。若变更需要重启PID1，目前**不得重启**。
3. **71项dirty及文件级集成**：再次只读核对最新数量与文件类型（会随运行增长），专门保护账本/报告/历史，检出仅用于诊断的`scripts/_*`，不提交敏感脚本；选定**一个隔离发布分支从最新origin/main创建**，按文件复核并引入已审R1+R2A+R2B代码，不能整体覆盖/改生产工作树。运行项目原有测试与R1的38项、R2的53项，记录实际运行数量与失败点；对于不能并测的依赖在独立环境明确说明。不要只运行fake_pipeline就宣称生产采集验证通过。
4. **定时/邮件两条业务线各自有计划**：新scheduler仅取代旧`watsons-daily-0700`，从health_check/collect到analyze，不发日报；**旧periodic仍与周/月/年报相关**，不能把两个Cron永远一起停掉而使播客断供。计划先停旧daily并核验无旧脚本进程，使用一个权威Python定时器，再单独处理periodic。日期锁只约束新launcher，不能挡旧Agent Cron，因此互斥必须验证。能减少PID1改动的安全且有权限的替代可评估，但只选**一种经过演练的**。
5. **真实告警闭环**：现有watchdog local sink不等于收到通知。准备一条与报告邮件隔离的critical报警、去重与恢复、最小信息脱敏、送达确认步骤。未经用户明确授权，**只做dry-run，不发真实SMTP**。容器整体停止的失联盲区应由外部只读监测或用户接受风险来明确处理，不能虚称覆盖。

A交付：隔离分支可执行发布候选、NAS本地新备份和恢复证据、生产发布/回滚可执行runbook、`docs/reviews/NAS-ONEPASS-CUTOVER-SAFETY-2026-10-26.md`，用真正的文件数/sha/测试结果，而非只写建议。

## B｜R3：周/月/年报＋播客产出质量，直接做代码和隔离验证

本次无需再等一次“允许写R3代码”的确认，**允许隔离实现，但生产投递需独立批准**。

1. **先从已有周期管线做真实追踪**：原`run_periodic.sh` → Python weekly/monthly/yearly（原6/7/8章）→ 证据包→经营分析→清洗稿→质量审校→TTS/MP3→邮件，确认每一步确实接收到正确`--date`、前置产物与失败退出码。区分“邮件发送标记”和用户邮箱真实收件，不伪造投递。
2. **修W40漏洞**：最终发送给收件人的**清洗后文本/HTML/MP3脚本**才是质量门验收对象；清洗前审核通过不得沿用`sendable=True`。校验每节实际信息量、重复与模板兜底、章节6/7/8结构、事实证据链接、日期和数字、来源覆盖、事实/推断/建议区分；空数据/全fallback/时间核验失败时明确blocked/degraded，不可硬凑或发水稿。
3. **统一事件入口**：周期聚合必须从最终可报告/已核验的新事实池中读取、纳入合法更新和具有注明的高价值背景，不能从原raw/cleaned抓几条P1就升格。当前`verification_status=verified`仅表示规则级日期/证据检查，**并不是人工事实真实性审查**；对高影响主张保留交叉来源及机器/人工复核状态，无法独立核实就标注“待确认”或阻止发布。避免将报告流水线变成一串无人审校的成功标记。
4. **投递独立门禁**：MP3音频完整性和实际播放时长/非零文件；HTML及附件可读；SMTP dry-run→测试消息仅在用户明确授权后真实发信；投递键幂等，无失败重发风暴。默认不发真实邮件。
5. 冻结W40错误回归 + 正常充足证据周 + 确实无信号周 + 缺章节/重复/空MP3/SMTP失败等固定负例；尽量复用已存在测试数据，不重跑大量昂贵模型请求。所有产物只落到隔离目录。

B交付：`docs/reviews/NAS-R3-PERIODIC-QUALITY-VERIFICATION.md`、代码/测试、最终清洗稿片段与音频dry-run证据（不公开私密报告全文）、剩余生产邮件授权要求。

## C｜M2：把AIHOT真正吸收到信息发现和精选，不搬站、不换技术栈

本次无需再等“允许写M2隔离代码”的重复批准，**可在独立隔离分支实施小范围有效增强**，但不接入正式抓取调度直至验收。

参考官方仓库https://github.com/KKKKhazix/AIHOT，实际实施前读取它的README、选择/筛选/采集文件并固定commit，**不能只看我们自己的转述就假称对标已完成**。不搬AIHOT的Node/PostgreSQL/站点，不搞第二套权威事件数据库，不恢复日报推送。

实施优先级：

1. **冻结SelectBench式样本**：以屈臣氏相关中国大陆即时零售+美妆个护官方公告/平台政策/竞品重要动作/时效错误/域外负例/同一事件多篇报道/进展事件建立人工可解释的正反例与证据URL，先量化漏报数、误报、事实错日期及跟进误并；R1的20和R2的50只是结构/旧窗口，不能冒充已标注的完整召回基准。
2. **完善发现覆盖**：RSS、官网列表、JSON API、网页分页、合法搜索补漏逐源测“发现列表→正文可读→日期核实→有效事件贡献”。按来源更新频率与贡献决定少量合理轮询；不碰验证码/登录后台或非法抓取，不为了看起来好扩大噪声采集。
3. **PASS/UNKNOWN/BLOCK三态预筛**：明确业务硬不相关/确定相关/信息不足；UNKNOWN走低成本待复核，不因宽泛关键词排除重要新政策。仅在固定真值集上确实降低漏报，才启用。
4. **同事件多来源聚簇/跨日更新**：保留现有cluster_key/fact_hash/novelty ledger，加入同源转载、官网原文与媒体改写、合法new_delta测试，不能把不同事实误并。
5. **双评分与热度只做证据驱动的最小版**：八维评分及规则权重始终唯一；只对接近P0/P1边界/重大业务事件做独立二次复评，展示分歧。来源热度按**独立来源**计，不能用刷屏转载给经营价值加分；没有改善就不加功能。不要每条事件自动翻倍LLM调用。
6. **必须对比M2前后**：高价值官方公告漏报/错误过滤、报告候选precision/recall、域外误入、同事件去重、跟进保留、单源贡献与采集失败、运行复杂度；如指标退化则隔离回滚。

C交付：`docs/reviews/NAS-M2-AIHOT-BENCHMARK-AND-ADOPTION.md`，固定AIHOT参考commit、差距到代码/测试的映射、可复制SelectBench结果和明确未实施项。

## D｜交付与最终报审（一次完成，不要半途交草稿要求用户继续批准）

- 有序建立独立GitHub分支/PR：**R2发布候选 / R3质量门 / M2 AIHOT增强**，每一份代码/报告可单独审查、回滚；可以并行做安全开发，但不合并生产；提交后GitHub读回SHA、diff和文档/脱敏测试证据。
- 汇总形成一页`docs/reviews/NAS-RECOVERY-RELEASE-CHECKLIST-2026-10-26.md`，分为“已完成/未完成/需要用户批准”的真实三态。列**唯一**需要用户确认的生产动作（例如NAS PID1重启、旧daily Cron暂停、新daily启用、真实测试告警、真实周期邮件；不把这些偷偷混作安全开发授权）。若无法核实宿主控制能力或备份完整性就停在NO-GO，但继续完成其它已获授权的安全开发工作。
- 只报实际运行的测试数与样本数；**严禁把全部43/53个单测源码存在、fake成功、local alert sink、published_at校验、GitHub提交成功，称作真实生产已经恢复**。
- 成功后一次性交付GitHub链接、三个工作包的SHA、备份验证、功能演示、遗留生产授权事项。**已授权范围内不要每写一份报告就停下反复等ChatGPT批准；仅在高风险生产切换动作前停止**。

目标是**让爱人能长期收到有价值的经营情报与播客**，不是无限期扩展审计、建设AIHOT网站、增加运维费用面板或玩模型优惠时间。
