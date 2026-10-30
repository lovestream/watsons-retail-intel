# 16｜DeepSeek执行：把全部LLM环节接入独立提示词库（2026-10-29）

> 用户最新明确要求：系统各处**凡真正调用大模型的环节**都必须分别由ChatGPT设计清晰可复用的Prompt，然后交给DeepSeek实施。ChatGPT已经在仓库提供[15份完整独立提示词](prompts/README.md)。DeepSeek现在的任务是**按真实调用点文件级接入代码、schema验收、回放验证**；不是再自己临时编提示词或只写一份建议文档。此前[15号经营邮件架构](15_20261029_periodic_email_editorial_system_and_free_tts.md)和[14号免费TTS](14_20261029_qwenpaw_offline_tts_sherpa_melo_install.md)继续有效；本16号为LLM调用的最新实施依据。

## 1. 当前代码审计发现（不能忽略）

ChatGPT已直接读取main各个源码：
- skills/filter_relevant_articles/filter_relevant_articles.py：_build_llm_prompt + llm_review_article，已有关联分类LLM，接P01。
- skills/extract_events/extract_events.py：LLM_SYSTEM_PROMPT / USER_TEMPLATE / SIMPLIFIED_RETRY，接P02。原代码虽已有occurred/announced/effective字段，但仍需每个主张绑定时间证据与交易状态，不可只抽一个日期。
- skills/temporal_fact_validation/temporal_fact_validation.py：**本是规则引擎**，P03仅用于关键日期/交易状态疑难案例，不要全面改成模型执行。
- skills/score_events/score_events.py：**八维规则评分**，保持唯一权威，不需要LLM Prompt；M2的二审P13仅做边界反证意见，不改写score。
- skills/analyze_business_impact/analyze_business_impact.py：已有LLM_SYSTEM_PROMPT/USER_TEMPLATE，接P04，禁止虚构内部经营数据。
- skills/periodic_synthesis/periodic_synthesis.py：保留规则跨周期聚簇；P05在构建合格fact pack后新增**选题策划**，低信号不硬凑。
- skills/generate_periodic_report/prompts.py：已有WEEKLY_CHAPTERS/MONTHLY_CHAPTERS/YEARLY_CHAPTERS、SYSTEM_PROMPT_BASE、REVIEW_SYSTEM_PROMPT、PODCAST_SUMMARY_PROMPT。**旧prompt硬要求周02=3趋势、周05=5–8建议、周06=下周3–5事项**，正是水话源头；按P06/07/10修订且同时校验规则门，保留长报告既定6/7/8章与质量门（有证据不足的章允许简短说明，不造事件）。
- skills/generate_periodic_report/generate_periodic_report.py：phase1_generate_chapters/phase2_review/phase3_revise/_generate_podcast_script有模型调用；接P06/07/10。Phase4最终审查主要规则程序，不应改为“模型给好评就放行”。
- skills/send_daily_report_email/periodic_mobile_digest.py：当前fix/periodic-email-mobile-digest-r3分支还是机械选前句，**没有独立LLM编辑**，应新增P08 Reader Brief Editor + P09事实校验；HTML renderer只吃经过校验的reader_brief.json，原报告不直接输出到收件人。
- skills/generate_daily_report、skills/editor_review、skills/generate_podcast、skills/podcast_review均存在旧日报LLM；参P12仅存量兼容，当前每日只采集、分析、留池，不恢复日报邮件或日播。
- skills/periodic_editor_review：规则型程序；不需要再加一个每章独立大模型审稿。P11只按需对自然口播稿做一次深度编辑检查。
- config/model_router.yaml：main存有旧OpenCode免费单一路由（默认与fallback同模型）；NAS现役实际主备以当前用户授权的SiliconFlow DeepSeek Flash＋RightCode百亿补贴为准，**先只读核对实际环境，不因Prompt改造擅自覆盖现役model_router或密钥**。让新业务LLM模块复用原模型客户端，避免单独配置第三套API。

## 2. 接入次序：避免一次增加十五次调用

**第一批（邮件质量直接受益、优先）**
A. P08独立编辑器：已核验facts/analysis + P05主题计划 → reader_brief.json。
B. P09成稿事实复核：仅对reader_brief中的重点主张审查一次，匹配原文章段落/交易状态/企业内部条件；如critical没解决则block，不把模型自己说的approve变成人审。
C. 固定renderer从审过的JSON生成竖向卡片、自然就地短链接、少量有条件行动。必须在真实W43与真实7月月报上展示“真正重新写过的编辑”，而不是摘前三句。HTML要保留原有附MP3和require_audio，禁SMTP。P08和P09用独立prompt版本及缓存，不重复调用相同数据无限消耗费用。

**新增前置层：月报/年报的分级输入必须落实（P14/P15）**
- 月报不能再从日报事件池独立写一份“大周报”：新增[P14月度跨周综合](prompts/14_monthly_cross_week_synthesis.md)，以约4份已经封账的周报和原始claim/source为主要输入；自然月可跨4～5个ISO周，程序切片、补齐、去重、做覆盖门及同口径比较后，调用**一次LLM**输出`monthly_synthesis.json`。
- 年报新增[P15年度跨月综合](prompts/15_yearly_cross_month_synthesis.md)，以12份已封账的月报和各月P14综合包为主要输入；程序先保证12个月完整、同事件跨月去重与原始证据链，调用**一次LLM**输出`yearly_synthesis.json`。12月还没封账不能在1月1日直接生成完整年报；年度fixture不能冒充真实业务。
- 统一链路：`weekly_report + original_fact_index → P14 monthly_synthesis → P05（月度选题）→ P06/P07长报告 → P08/P09邮件 → P10/P11播客`；年度以`12份monthly_report + monthly_synthesis + original_fact_index → P15 yearly_synthesis → P05（年度选题）→ P06/P07 → P08/P09 → P10/P11`。**P14/P15代替P05内部重复的跨期分析，不额外再造一套选题LLM，也不替代原有P05阅读优先级排序。**
- 月/年存每条重点结论的逐层溯源链`annual/monthly claim → monthly/weekly report → canonical_event_id → claim_id → source URL`，任一重要链缺失则hold，不得拿下一级报告文笔替代源核验。
- 原`periodic_synthesis`的事件真相层、覆盖度及去重规则要保留并扩展，不允许让新LLM自行计数、对齐日期或改八维评分。对P14/P15的实现须报告真实代码diff，**当前仅有提示词文档，不得声称已接入生产**。

**第二批（修正上游旧提示词，减少假事实）**
D. P01/P02/P04逐一替换现有LLM提示词；P03仅对重要歧义按需挂载，P13极少量可选。做老测试通过＋M2 SelectBench，不允许生产事件真实数据自动改verified、不允许被LLM擅自改变现有八维评分。
E. P05周期选题落地，把“选哪些新主题”与“写长报告”解耦。P06/07更新周/月/年分章写作、全局审稿和定点修订的Prompt，删除强制3趋势/5–8建议/下周五事项等模板膨胀要求；无证据时明确degraded/no_signal，不放宽R3质量门。
F. P10/P11口播生成与审校，和真实邮件共享同一facts pack；播客为**独立叙事**，不逐字照邮件、不过度“今天三件事”；当地TTS失败不得静默支付硅基语音费，按14/15号指令测试Sherpa-ONNX。

**第三批（仅兼容，不增加生产LLM）**
G. P12旧日报/日播LLM只在已有调用仍生效的情况下避免编事实，不能因为prompt库完整就恢复日报邮件。
H. P13只在P0/P1边界或危重大事实看法分歧时调用，不全部批量二审。

## 3. Prompt代码加载方式

- 15份md为**设计权威**，但不建议每次请求模型时把整个Markdown文件都传给模型。DeepSeek应从每份md的System Prompt/User Prompt章节提取纯文本，建立集中版本化registry（如skills/utils/prompt_registry.py + config/prompt_registry.yaml），并自动映射实际模块。使用明确的模板变量声明与渲染，启动或CI做必填变量检查；不要把“调用位置/程序验收说明”也塞给模型。
- Prompt输出只有一种指定类型：P06长报告Markdown，P10播客纯文本，其余以JSON为主；用Pydantic/jsonschema（已有依赖优先）校验字段、枚举、长度、fact_id与输入关系，不能只验证JSON能解析。
- 缓存键应包含prompt_version + model_id + 输入facts_sha/period，源证据变化必须失效；所有LLM日志只记录sha、状态和token数（若客户端已有），不明文输出密钥、内部收件人、敏感原文；不加任何金额监控面板。
- P01/P02/P03按需运行；P05/P08/P09为每周期少量调用；P07重审一次、有问题只修指定章节；P11对重要稿可选。避免为了一条邮件把所有模型调用乘以10。
- **重要契约**：旧模块已依赖具体字段，例如P01 recommended_pool、P02 events+temporal字段、P04 business_analysis、P07 overall_quality与revisions_needed。增加字段可用，但不得删旧字段或修改enum而不适配；P07新增blocked须由调用端明确处理，不能误当good。P08/P09是新增结构需实现schema & JSON-to-HTML，而不是把JSON直接发邮件。
- 程序分配唯一可信ID和数值，不可让模型生成事实真值；即使LLM说“source verified”，程序未确认来源照样hold。
- 不随意引入第二个权威事件库或AIHOT Web；模型可以思考，规则与事实源负责判断是否发出去。

## 4. 强制测试矩阵（不允许只看已提交的Prompt文件就报完成）

1. P01-P04：官方公告弱关键词不能BLOCK；没有正文进入UNKNOWN；文章中prompt injection不得执行；正文10/26报道10/23-25活动正确区分时间；拟收购签约不能写已交割；没有原文引文不能标verified；无屈臣氏内部库存时不能强推全平台上线。
2. P05-P07：单新闻不能拆成三条趋势；无前一周期数据不报环比；没有竞对不凑竞对；周06不出空泛“下周必须验证五件事”；对已过期动作识别并处理；2026年7月历史报告不作为2026年10月即时任务；低信号可优雅降级。
3. P08-P09：用**真实W43**、**真实2026-07月报**及无信号/传闻/变更/重复样本，输出可审reader_brief.json、每个claim的原文绑定审计、HTML。检查W43中“全平台同步上线专区”和“强制每会员月限购两件”不得成为无条件指令；2026-07中“签协议/媒体传闻/完成收购”不能混写；重要新闻必须有1～3个真的原文链接，而非零链接或文末附录。
4. P10/P11：口播日期/数字/品牌与brief一致，音频稿可自然朗读，不能强制“明天见/今天三件事”；模型审稿通过不算MP3试听通过。若使用本地TTS，需生成新的与script_SHA对应音频，不自动转SiliconFlow收费。
5. HTML在360/375/390/600px无横向滚动、首屏看到最重要结论、竞对单列、正文可读且没有审计/列表附录。真正读者的阅读计时/听音反馈须人工完成，DeepSeek不得编造成“已验收”。
6. P14/P15分层回归：月度4～5相交周、月底事件归属、已有4周但末周未封账、同一事件跨四周追踪而非四次计数、月报有误年报不可继承；年报12份月报覆盖不足、同一新闻多月转载、重大单次事件不能自动变年度长期趋势、不同统计口径不能相加、12月月报未封账时阻止1月1日假年报。提供P14/P15 schema、事实链、cache invalidation和`complete|degraded|incomplete`样本。
7. 所有失败门：事实critical→阻止真实发送；口播无效→阻止音频/邮件；LLM不可用→留待审核/降级，不胡编。冻结样本正反例与原回归和R3质量门综合验证。

## 5. DeepSeek立即执行与交付

在**隔离分支**（以E1最新＋fix/periodic-email-mobile-digest-r3为基线）连续实施第一批→第二批→必要第三批。将15份独立Prompt与代码绑定、记录模板版本、清晰列出哪些是新增LLM调用/哪些原有/哪些不启用，并为每个真正调用点做输入/输出的功能测试，不只是写提示词文档。优先交付真实W43新brief/HTML和2026-07新brief/HTML的内容明显改善；另外使用真实、连续可追溯的月报输入做P15年度综合试验，没有12个月合格数据就明确NO-GO而不是用fixture假装年报通过；这才是关键验收。

提交：
- 版本化Prompt registry及13份Prompt文件的引用路径，必要时在隔离分支添加原Prompt的原生模板副本，但不无故复制冗长MD。
- docs/reviews/NAS-LLM-PROMPT-INTEGRATION-ACCEPTANCE.md：真实代码diff、完整Prompt映射、实际运行测试数、两份读者简报+HTML+截图、重大claim审核结果、空信号/传闻/旧闻回放、是否不增加无用调用。
- 明确保留版本化未启用的可选Prompt，列不能部署的原因。用户没批准生产，所以不要合并main、写生产真实事件账本、重启QwenPaw/Docker/PID1、启停Cron或发SMTP。不要因为Prompt改造顺手更换模型API或TTS供应商；TTS按14/15号单独隔离评估和部署授权边界。

**验收不是“15份提示词都调用过”；验收是所有实际LLM调用有恰当独立Prompt，真实经营简报显著更有价值，不生成假事实、不制造多余费用。**
