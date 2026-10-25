# NAS R2-B｜ChatGPT 上线前独立复审与生产切换门禁

复审日期：2026-10-26。项目：lovestream/watsons-retail-intel。

> **裁决：R2-B 上线前预检与隔离代码修复验收通过；正式生产切换尚未获批，不可直接部署。** 当前下一步是**收敛式的切换准备（R2-B-CUTOVER-PREP，只读核验、隔离演练、交付可执行方案）**，不是再次开发一轮大功能，也不是安装 supervisor/启停 Cron/合并 main。完成后以一份简短门禁表请用户决定是否真正启用。
>
> 既定策略保持：NAS Docker 中 QwenPaw 负责执行，ChatGPT 负责架构与复审；模型主用 SiliconFlow DeepSeek-V4.1-Flash，备用 RightCode 百亿补贴 DeepSeek Flash；用户在两家后台查消费与免费额度，**不开发金额台账、日预算、估价功能**，保留必要的调用成功/失败与主备路由诊断。

## 一、已核验的 GitHub 事实

- [R2-B 预检报告](https://github.com/lovestream/watsons-retail-intel/blob/r2b/preflight/docs/reviews/NAS-R2B-PREFLIGHT-VERIFICATION.md)，分支 [r2b/preflight](https://github.com/lovestream/watsons-retail-intel/tree/r2b/preflight)；与已审 R2-A-FIX 分支 compare 显示 **ahead=1, behind=0，新增/修改9文件**，内容集中在B1、B2、LLM健康分母、测试与报告。相对main则已分叉，不能直接快进/覆盖。
- 直接核对了 skills/report_policy.py、temporal_fact_validation.py、collect_daily_articles.py、run_daily_pipeline.py、ops/r2a/launcher.py、测试源码、脱敏 [B1冻结窗口证据](https://github.com/lovestream/watsons-retail-intel/blob/r2b/preflight/docs/reviews/artifacts/NAS-R2B-B1-frozen-window.json)；在 tests/r2a 中 **35+18=53 个 test_ 用例**，执行者报告53 tests OK。ChatGPT未进入NAS现场重跑53次，也未直接读取未提交的58份生产改动。
- B1 直接代码证据：来源为 published_at 的临时补日期现在附加 event_date_only_article_publication，降级needs_verification。B1冻结窗口两事件 0028、0029 从verified→needs_verification，5条原可报告新事实没有变化。注意：**日期与证据的规则级 verified 不等于人工/独立来源事实认证**。
- B2 直接代码证据：collector返回 collection_completed、collection_date，管线透传至步骤stats；scheduled终态要求collect status=success、collection_health=ok、已完成、本次目标日期一致、source_total>0，跳过/失败、旧cleaned有文件也不作为成功。模拟测试覆盖失败与合法无信号；**当前没有采集独立run_id，且实采数据卷未在本轮运行**，不能说已做产物级端到端验证。
- LLM健康直接代码证据：对有llm_failed_count的步骤用 success/(success+failed) 判定，低于0.8标降级；extract、analyze模块确实返回相应统计字段。对缺分母的旧统计仍存在兼容分支，后续正式验收要用实际stage stats核验，不可把单个HTTP200视为整个批次成功。

## 二、预检成果与待解阻断项

| 核验项 | 复审裁定 | 生产限制 |
|---|---|---|
| B1 时间来源 | **通过** | 保留有明确公告日期的正例，只有文章发布时间的事实不能升级 |
| B2 采集确实执行 | **隔离测试通过** | 本次采集成功标记比旧文件mtime靠谱，但生产完整采集尚未证明 |
| 故障与LLM降级 | **代码层通过** | 应有实际一次完整管线和退化告警数据，不能只依赖fake_pipeline |
| NAS持久卷/宿主 | **部分核实** | 已根据/proc/1/mountinfo确认容器内持久卷；**未获NAS宿主docker inspect级证据**，不可把推断当完整宿主审计 |
| Supervisor | **阻断** | PID1使用supervisord，但没有有效supervisorctl控制接口/socket；**不可无授权修改PID1配置或重启QwenPaw服务** |
| 原Agent Cron | **阻断** | daily与periodic两个现役旧Agent定时任务，旧脚本不共用R2-A日期锁，生产切换必须先获得暂停授权并排除并发 |
| 实际告警送达 | **阻断** | watchdog的status.json和本地sink通过，**尚无外部独立告警渠道/测试接收**；容器本身挂掉时本地watchdog也会消失 |
| 生产工作区 | **阻断** | QwenPaw报告现役工作副本HEAD仍为2464df9、含58个未提交变更；GitHub无法独立观察这些NAS现场改动。禁止reset --hard、git clean、force push或整体覆盖/回滚 |
| 与main集成 | **阻断** | r2b隔离分支包含R1/R2A/R2B代码且与main分叉；必须人工文件diff、冲突检查、备份，不能直接git pull/merge覆盖脏现场 |
| R3报告/邮件 | **单列后续** | 只形成质量门需求，并未证明周/月/年报、最终清洗审校、音频及SMTP已修好；不能以本阶段通过宣称整个系统恢复 |

**额外指出：老Agent Cron本来长期不能稳定执行，因此“回滚到旧Cron”是回到旧行为，**并非保证业务已经恢复**。必须在回滚预案中如实标出：紧急情况下可停止新任务、保留原始数据，但旧Cron恢复后仍需验证执行和交付，不能宣称一键业务恢复。

## 三、避免过度设计：优先选择最低风险的控制方式

不要为了一个每日定时采集任务，先把QwenPaw的PID1 supervisor控制接口硬改一遍。优先做**NAS宿主现有任务调度能力**的只读核实，若有可安全授权的 Synology Task Scheduler/现有Docker容器exec入口，可以设计从宿主**直接启动同一个Python launcher**的简单调度，并由独立于QwenPaw Agent的健康观察机制核验；但宿主执行权限、容器名、重启行为、账号与告警能力都**尚未验证**，不能把此替代方案当已落实。

若主机任务调度不可用，再考虑经过用户授权的受控PID1 supervisor改造。无需同时维持多个新调度体系；**选一个被真实演练验证的权威调度器**，不依赖Agent口头“已启动”。本轮不可擅动现役Cron与PID1。

特别留意两个Cron不是同一功能：daily仅负责采集/入池，periodic关系周/月/年报。新R2 launcher默认跑到analyze，并不替代周期报告的邮件流程。正式切换方案必须写清**daily和periodic分别如何处置**，不得只把两个旧Cron一起停掉而未说明报告服务仍不可用。

## 四、下一轮唯一任务：R2-B-CUTOVER-PREP（只读+隔离，不切生产）

QwenPaw仅执行以下收尾，不做新的系统功能扩张：

1. **生产58个dirty变更分层**：在NAS现场只读生成文件名/类型/是否关联配置、代码、正式账本/运行数据/Secret的清单，分为必须保护、需人工合并、可忽略生成产物。禁止上传真实数据、私密路径/密钥/报告原文到public GitHub。制作NAS本地**不含明文公开泄漏**的可验证备份与恢复脚本草案；先只读核对备份目标和空间，不直接执行危险覆盖。
2. **找到最简单的调度控制入口**：只读确认NAS宿主任务调度能否直接、安全调用容器Python（若可用，优先于改PID1）。如不可用，明确所需最小PID1配置改造、影响范围及风险；不能同时启用两个新调度方案。
3. **完整dry-run切换排练**：独立state/data副本、冻住旧Cron的模拟状态；让候选启动器执行真实的程序代码/模拟器：当日已执行、采集失败、合法零新闻、容器重启、旧Cron仍在跑、双跑保护、watchdog崩溃和告警恢复。不碰正式数据卷，不使用真实SMTP。
4. **通知方案**：只提出一种用户可批准、独立于Agent的低噪声critical通知通道，明确报警去重/恢复/失联监测；未批准前只能本地预览。不要新建收费/成本监测系统，不擅发邮件。
5. **一页操作Runbook**：精确区分daily与periodic计划、停旧→验无并发→启新→首个健康manifest→回滚；列出切换可完成时间窗的条件，不猜测具体时点。讲清旧Cron回滚不能保证恢复业务。
6. 生成 docs/reviews/NAS-R2B-CUTOVER-READINESS.md，含测试通过/未通过、58个改动的统计分类、持久化与服务控制能力、唯一调度方案、真实告警依赖、R3遗留事项、明确待用户授权清单。隔离分支提交后立即停止，不合并main、不安装/启停正式服务。

**生产上线授权独立于本报告。** 不允许现阶段安装/启动supervisor、暂停/恢复旧Cron、修改NAS正式数据卷/SMTP/Key、真实告警投递、完整历史补跑或合并main。后续如果用户批准实际切换，再按照验收后的Runbook一次性受控执行，不应再设无限层前置阶段。

## 五、签署建议

**R2-B-PREFLIGHT 通过（技术与风险识别），生产切换 NO-GO。** 最后一个收敛型准备任务完成且用户批准后，方可进入受控切换；R3周期周/月/年报与音频/真实邮件质量验收仍属单独阶段。

