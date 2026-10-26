# NAS R2-B-CUTOVER-PREP｜ChatGPT 独立验收与生产切换裁决

日期：2026-10-26；仓库：lovestream/watsons-retail-intel

> **结论：本轮“只读盘点、备份/恢复脚本、隔离调度和故障演练”交付通过；正式生产切换继续 NO-GO。** 无需再启动一轮泛化架构改造。用户尚未授权修改生产、启停Cron、重启QwenPaw容器、真实告警发送或合并代码。首次切换要按下面“缺一不可”的门禁单次执行，门禁未满足立即中止。
>
> AIHOT对标：此前在 [11号架构文档](../11_20261025_nas_qwenpaw_aihot_architecture_decision.md) 已有分析，但主要属于**规划**而不是R1/R2代码落地。独立状态表见 [AIHOT差距与借鉴实施顺序](AIHOT-ADOPTION-GAP-2026-10-26.md)。不得把R2上线与AIHOT全量改造绑在一起。

## 一、GitHub独立核验

- 查阅隔离分支 [r2b/cutover-prep](https://github.com/lovestream/watsons-retail-intel/tree/r2b/cutover-prep) 的 [NAS-R2B-CUTOVER-READINESS.md](https://github.com/lovestream/watsons-retail-intel/blob/r2b/cutover-prep/docs/reviews/NAS-R2B-CUTOVER-READINESS.md)、[演练JSON](https://github.com/lovestream/watsons-retail-intel/blob/r2b/cutover-prep/docs/reviews/artifacts/NAS-R2B-CUTOVER-DRILL-2026-10-26.json)、ops/r2b两个脚本、R2A supervisor草稿与代码。相较r2b/preflight ahead=2/behind=0，只添加4个文件（2文档、2备份/恢复脚本），没有在本阶段修改正式采集/模型代码。
- QwenPaw报告53项单测通过、演练中启动/去重/无信号/失败/告警恢复与隔离supervisor杀进程重启均通过；相关记录可查，但**ChatGPT未进NAS运行测试，也未现场验证宿主Docker或生产dirty文件**。
- 生产仍旧head 2464df9、dirty由58增长到**71项**（8 tracked modified +63 untracked）。报告明确没有生产备份、没有PID1控制面部署、没有暂停/恢复Cron、没有真实告警接收，诚实可信。不能将“测试证明脚本可用”称作“完整保护生产数据”。
- 本地报告说“没有可用merge base”可用于提醒不能快进，但GitHub远端compare实际上显示两个分支存在共同祖先（merge_base）；正确结论是**双方已分叉，不能直接快进和覆盖现役dirty工作树**，而不是远端绝无共同祖先。实际集成用最新main做三方diff/隔离合并检查。

## 二、已认可成果

1. 71 dirty按数据账本/周期产物/历史备份/临时脚本/3文档分层，不做reset --hard或git clean，主动标记一次性脚本含 credential-like 字面量，不上传公开仓库。
2. 备份脚本默认dry-run、执行需项目外输出目录，产出二进制patch、未跟踪文件tar、清单和SHA256；恢复只到空staging，隔离模拟验证通过。
3. 选单一PID1 supervisord方案；不假装宿主Synology Task Scheduler在当前容器权限下可用，也未启用第二套调度。
4. 新daily与旧periodic分别处置，旧daily要暂停，periodic只是暂时留旧任务；不以Agent口头回复作为“已启动”证据。
5. watchdog本地状态/去重/恢复与隔离autorestart演练有证据，**尚未真实送达用户**。旧periodic交付质量仍属R3。

## 三、生产切换前尚需满足的关键门禁（明确修订）

### C1 P0：备份脚本不能当完整生产数据快照

**实际代码漏洞边界**：ops/r2b/backup_worktree.sh 备份的是 `git diff --binary HEAD` 和 `git ls-files --others --exclude-standard`。后者**不包括.gitignore中忽略的文件**；仓库.gitignore明确排除 `data/raw/`、`data/cleaned/`、`data/events/`、`data/runs/`、`data/logs/`、`data/enriched/`、`reports/*/`、`podcasts/*/`。报告所列71 dirty只覆盖Git可见改动，不能覆盖约1.8GB运行数据。**生产切换前必须另外执行不破坏数据的完整状态保护**：正式事件与来源账本、投递记录、events/runs、周期报告与音频以及本次部署必要的配置/历史原件均应在NAS受控持久路径生成单独快照、校验sha或可读性、列明恢复顺序。涉及邮件/密钥的敏感文件仅NAS本地加严权限，绝不放GitHub。可以用现有NAS快照/备份能力，不必另造一个复杂备份程序。

### C2 P0：PID1 supervisor改动与重启后的持久性

报告拟直接编辑`/etc/supervisor/conf.d/supervisord.conf`并重启PID1以开启控制socket；需先核实**该文件在Docker容器重建/更新后是否持久存在**以及Docker restart policy、启动入口与原app/dbus/xvfb/xfce4进程关系。不能把“进程kill-9后可autorestart”的**隔离测试**等同于“容器重启或重建后配置仍存在”。如配置位于容器可写层而非持久挂载，需有可审计的宿主Docker配置/持久化注入方案和真实回滚出口；此处不批准盲目reboot PID1。现有PID1无supervisorctl控制面，必须先设计真实可用的复原方式，避免QwenPaw自身掉线。

### C3 P0：先审生产集成、密钥可读及新旧任务互斥

生产HEAD/新代码分支/main已分叉，生产dirty大量业务数据，**必须先核对差异、完整备份，再允许目标文件级集成**。新调度启动前确认原daily Cron暂停且没有旧daily shell/Python进程；新scheduler日期锁不能约束原Agent Cron。检查PID1继承的环境变量名、Python子进程读取已授权Secrets可用但不展示值。运行前按生产实际路径核对program name、project-root、数据卷、日志目录和Asia/Shanghai时区。所有步骤按真实输出验收；失败立即停。

### C4 P0：告警实际送达与监督盲区

目前只有local alerts/status sink。若用户同意邮件告警，先进行一条**经单独批准**的真实critical测试并确认收件成功；不能与报告发信幂等混淆。容器整体死亡的外部观察手段**尚未实现**，必须在生产授权时由用户明确接受风险，或依赖宿主DSM另行部署简单存活提醒。未验证之前不得宣称“掉电、Docker退出也会告警”。

### C5 P1：不要误以为周期报告已经恢复

新scheduler只承担daily的采集→extract→score→analyze，且daily不发邮件。旧periodic继续使用原有Agent调度，曾有失败、漏报及W40清洗后质检失效；它的邮件/音频质量与真实投递状态**尚未通过R3**。切换时不应把“恢复periodic Cron enabled=true”标记为“周/月/年报已恢复”。R3从最后交付成品重新质量门并单独授权真实发送。

### C6 P1：避免隐含额外自动化

不要建设费用台账、按时段切换、大规模新系统、两个新调度器。模型既定主SiliconFlow、备RightCode百亿补贴，后台由用户查看余额。已有代码仅保留错误、调用量和诊断。继续使用NAS现有Docker/QwenPaw，不调用Codex。

## 四、最终阶段裁决和下一动作

**R2-B-CUTOVER-PREP“报告/演练”通过；“生产切换”不予自动授权。** 不再追加新功能阶段，也不让QwenPaw反复重跑53项虚拟测试。可以依据本审查**准备一份可一眼核对的正式生产切换检查单**，由用户单独授权以下有副作用动作后再受控执行：

1. 做完整NAS项目快照+71 dirty工作区备份，核验恢复方式；
2. 确认Docker宿主侧配置持久化、supervisord控制面及回滚路径；
3. 文件级集成R1/R2相关代码，生产只启动daily计划入口，老daily Cron停用，periodic严格保留且不宣称恢复；
4. 确认模型Secret可被新调度Python进程读取；
5. 真实critical告警单次测试，或用户书面接受容器失联的外部告警盲区；
6. 一个真实闭合日的采集、事件池、manifest、去重、LLM健康结果验收，无重复执行/污染正式账本。

**当前不执行以上事项**，不启停Cron、不重启PID1、不合并main、不更改SMTP/账本、不发送真实告警。用户授予生产切换权限时，才可按门禁顺序一次性执行；任何前置条件不满足即NO-GO，不应该通过随意扩大开发来规避。

## 五、AIHOT方向的阶段边界

AIHOT官方文档里值得借鉴的是源分级与自适应采集、PASS/UNKNOWN/BLOCK宽预筛、同一标准独立双评分/SelectBench校准、跨媒体事件归组、独立来源热度。原系统已有多采集器、事件ID/novelty及八维分数，而非完全空白；但尚未完成这些**AIHOT特定**的对标验证与增强。**这部分排在恢复daily可靠运行并验收R3周期报告门禁之后，作为M2的首要任务；不捆绑R2日更生产切换。**

