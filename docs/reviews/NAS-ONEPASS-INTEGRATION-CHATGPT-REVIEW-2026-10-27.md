# 2026-10-27｜R2+R3+M2整合验收：唯一内容瓶颈的精确定位

## 裁决

**整合分支技术与隔离演示阶段通过（有条件），生产报表数据质量仍未过门；生产正式切换没有授权。** 不再重复实现R1、R2、R3、M2已有功能；下一项唯一安全开发任务是 **E1：基于真实目标周的“原文→事实证据→日期语义→事件资格→经营分析→周期报告”无损补证闭环**。E1可连续进行隔离代码与证据回放，无需每小项等待审批，但绝不批量标历史事件为verified，也不写真实生产账本/邮件。

既定：NAS Docker、QwenPaw内DeepSeek执行，ChatGPT复审；SiliconFlow DeepSeek-V4.1-Flash主、RightCode百亿补贴备用；中文播客复用已授权SiliconFlow CosyVoice2；平台后台查看用量/费用，不创建金额台账/日金额预算。每日只入池不发日报；最终交付周/月/年报+中文MP3+授权邮件。

## A. 已独立核实的GitHub证据

- [integrated分支](https://github.com/lovestream/watsons-retail-intel/tree/integration/onepass-20261026) 当前GitHub HEAD（审查时）：`ca39da634f24705a4fcc31acdb5a84c12172350e`；相对main已分叉（ahead7/behind4），不可快进/强推，不能覆盖有dirty数据的生产工作树。
- [整合交付报告](https://github.com/lovestream/watsons-retail-intel/blob/integration/onepass-20261026/docs/reviews/NAS-ONEPASS-FINAL-INTEGRATION-2026-10-26.md)：报告71项单测在干净clone通过；GitHub实有`tests/r3/test_periodic_quality_gate.py`、`tests/r3/test_tts_provider.py`、`tests/m2/test_m2_adoption.py`，以前缺测试代码的问题已修。
- [W32隔离回放](https://github.com/lovestream/watsons-retail-intel/blob/integration/onepass-20261026/docs/reviews/artifacts/ONEPASS-E2E-REPLAY-2026-10-26.json)：**2026-08-03～2026-08-09（W32）**复制7天数据，`raw_events=344`；规则层验出`verified=37`、`needs_verification=307`，真正入报候选`reportable_events=5`，聚为4簇；`r3_candidate_status=candidate_review_required`。这里344是跨日原始事件池口径；合成器内部`synthesis_event_stats.raw_events=76,period_events=67`，口径**并不等于344**，应一并解释。307是未通过规则审查总量，不能误称“307条全部缺日期/证据”：还含重复/时效冲突/传闻等不同原因。
- **切勿混淆 W40**：[新TTS证据](https://github.com/lovestream/watsons-retail-intel/blob/integration/onepass-20261026/docs/reviews/artifacts/R3-FROZEN-DRYRUN-2026-10-26.json)属于 **2026-09-28～2026-10-04（W40）冻结优质样例稿**。真实SiliconFlow TTS生成约462.6秒新MP3、dry-run邮件通过；但未证明W40真实事件有足够优质信息，更未生成“真实W40报告配音”。
- [SelectBench](https://github.com/lovestream/watsons-retail-intel/blob/integration/onepass-20261026/docs/reviews/artifacts/M2-SELECTBENCH-2026-10-26.json)已有59样本、40类article URL但`verified_real_evidence_n=0`，仍advisory、`safe_to_enable=false`，符合未将合成测试当生产质量的边界。
- [supervisor持久化草案](https://github.com/lovestream/watsons-retail-intel/blob/integration/onepass-20261026/docs/reviews/NAS-SUPERVISOR-PERSISTENCE-DESIGN-2026-10-26.md)只在隔离目录通过，真实NAS宿主Docker Mounts及挂载生效尚未核验，未授权生产PID1/cron/SMTP操作。
- ChatGPT本轮直接读取GitHub源码、JSON与报告；**没有连接NAS重新执行71项测试、重放真实音频/检查备份容器数据字节，执行者的日志与SHA是报告证据，不是生产真实邮件验收**。

## B. 源码层三个质量边界，E1必须最小修复/澄清

### B1 W32不能代替W40，避免时间窗误用

`ops/r2b/e2e_replay.py`默认日期为**2026-08-03～08-09**；报告里344/5统计属于W32，不能套用到W40。必须以实际目标周（若要重建W40，则2026-09-28～10-04）独立检查文章、原文快照、事件和覆盖；如果缺数据，要报告无法重建的源与天，**不能用W32事件、W40样例、文章发布时间拼凑W40“真实周报”**。W32保留为旧数据回归样本；也应抽取最近已闭合的真实周作为未来正常运营验收，避免无限修补历史。

### B2 不能把正文任意一个日期冒充“事件发生日期”

`e2e_replay.py::_explicit_event_date`只用简单正则匹配`20(25|26)年M月D日`，`_apply_explicit_date_verification`一旦符合周窗口，就**直接写event_occurred_at=日期, event_time_source=occurred**。正文中日期可能是记者发稿、引用历史数据、会议、预告或规则生效日期，不一定是该事件实际发生时刻；仅命中年/月/日并不足以完成事实核验。必须要求原文证据片段、日期所属角色(occurred/announced/effective/published)、主体和动作归属一致，并记录是否来自原始公告，冲突置needs_verification。**保留只能从发布时间推断的review状态**。复核是逐条可追溯证据审核，不能自动把307条批量升成verified。

注意：`explicit_date_verified_before_synthesis=64`来自函数遍历**两个事件文件**（events_analyzed及events_scored_novelty），不能直接称64个不同的事实被核验。必须以`(date,event_id)`为键去重，输出真实unique计数、原文证据与核验结论，避免分母漂移。

### B3 隔离回放会覆盖分析事件，不能据此证明完整经营分析报告

`ops/r2b/e2e_replay.py::_prefer_scored_pool`对存在的`events_analyzed.json`执行`analyzed.write_bytes(scored.read_bytes())`（只在temp，不污染生产，但**丢失分析层语义/数据，后续合成器优先读取这份文件**）。这只能证明评分池中的事件资格筛选，不足以证明带有真正`business_analysis`的生产R3报告能走通。建议按事件ID非破坏性join：保留已分析字段，仅把核验与资格覆盖合并进去；对数据不匹配记录差异，不“用评分文件改名冒充分析完成”。同时区分`pipeline_completed`、`llm_analyze_success`、`report_quality`与`audio_generated`。

另：`tests/r3/run_frozen_dryrun.py`中的`script_audio_match=true`实际计算为“稿件和MP3的两个SHA都不为空”，不是ASR比对音频语义或文本一致性。可以称“从这份冻结稿调用TTS并留下关联产物”，**不能把该bool解读为语义逐字吻合**；如需证明完全一致，应有可追踪`input_script_sha256`→TTS run→`audio_sha256`，必要时抽样听音或语音转写对比，按能力如实标注。

## C. 下一项唯一任务：E1（只在隔离数据和代码）

**目标：重建一个“真实目标周”的可用事实证据包；不必粗暴逐条人工审307条。**

1. **先定窗口并盘点可用源**：W32是回归样本；真实W40=2026-09-28～10-04单独统计raw articles、原文HTML/正文快照、源URL、各日分析事件、缺日期、缺证据和已合格core数量。若真正希望验收能持续周报，再选最近一个**已经完整结束、数据确有存档**的自然周跑同一规则。不得把两个周期结果混入同一报告或用fixture代替真实稿。
2. **筛选要复核的候选，而不是全量LLM盲跑**：从已保存的原始文章按中国大陆×即时零售×美妆个护的相关性、非旧闻/不重复、P0/P1和高经营价值、关键平台政策/竞品动作排序；保留全部原始事件ID与未入选原因。先审最可能形成高质量事实包的候选，必要时再扩批；数目由真实证据与收益决定，不设置强迫凑够的配额。
3. **原文证据回收/复抽取**：优先NAS本地合法归档的cleaned/enriched/raw文章，只有缺失才受限补抓公开原文（不得访问私人后台或绕过限制）。每条记录article_id、source_url、具体段落或句子、主体/动作/对象、数字与单位、published/announced/occurred/effective各自的原文证据、可信来源、正文快照哈希。LLM可以提出候选日期与事实，但**规则核查原文、人工抽检高影响事实**；AI审查不声称human_verified。没有依据的继续needs_verification/reference；传闻不可作为已核实新事实。
4. **去重/进展与资格处理**：同事件多稿合并，合法updated保留，旧内容进reference，海外/域外及营销噪声保留排除原因；所有更新只写**隔离事件快照**，不覆盖正式event ledger或原始article/HTML。严格实行核验结果→temporal→novelty→score→analyze→periodic的一致性，不允许后续步骤重新把review提升core。
5. **保留真正分析字段**：修上面B2/B3，只允许最小安全代码改动。对照W32回归结果与真实目标周，对每条潜在最终入稿事实检查fact/evidence/date/source/eligibility/analysis和出处，不要`_prefer_scored_pool`覆盖掉业务分析。真正走`periodic_synthesis → generate_periodic_report → clean final → final review`，报告通过才以这份**真实目标周口播稿**调用SiliconFlow TTS生成独立MP3，并对齐input_script_sha256与audio_sha256；邮箱保持dry-run，禁止发真邮件。
6. **质量与停止线**：是否达标由来源覆盖、不同经营主题、可执行洞察、章节6/7/8、关键数字原文证据、非兜底、可追溯信息密度判断，**不是通过更多verified数量强凑**。如果实证内容不足，输出`candidate_review_required`或`blocked`和缺失清单，保留已有成果；**不放宽质量门、不编造周报、不用优质样例冒充真实周报**。
7. **交付一次完成**：在现有`integration/onepass-20261026`隔离分支（或其派生安全分支）提交代码、自动/人工复核判据、少量脱敏证据样本、目标周审计与真实报告dry-run/音频记录，报告`docs/reviews/NAS-E1-REAL-PERIOD-EVIDENCE-RECOVERY.md`。至少记录W32基线344/37/5、目标周真实来源与事件数、自动/人工/无法核验各数、最终可报告数、是否真正生成带分析的最终稿、新MP3是否来自**相同真实稿**、仍失败根因与测试SHA。报告中不得包含私密报道全文、真实Email地址、Key。**完成已授权安全工作后停止等待ChatGPT复审**。

## D. 生产安全边界

- 仍没有授权：合并main、把代码覆盖生产工作区、修改生产事件/来源账本、触碰数据卷、NAS宿主挂载、重启PID1/Docker、安装/启动正式supervisor、新旧Cron启停、真实SMTP/alert投递、擅自补跑历史数据。
- 数据证据闭环与生产切换是**两类阻断**：前者决定真实周报能否通过质量门；后者决定是否安全、经授权运行在生产。不能把“唯一硬阻塞”理解为生产已经批准。
- 不建设新计费系统、人工全量复核平台、第二套权威账本或AIHOT网站；优先修目标周证据，任务结束后产出一份明确的生产操作授权清单即可。

