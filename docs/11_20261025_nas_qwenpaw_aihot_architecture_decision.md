# 11｜NAS Docker + QwenPaw 经营情报系统：架构决策、AIHOT 对标与 DeepSeek 成本方案

> 日期：2026-10-25（北京时间）。**设计稿 / 待 QwenPaw 在现有 NAS Docker 环境逐阶段实施；不代表代码已修改、服务已重启、调度已变更。**
> 本篇为当前项目的**最新实施方向**；历史 Muse 独立试验（docs/10 和 muse/）不再是建设方向，历史证据应先备份后清理。执行命令见 [12｜分阶段执行手册](12_20261025_qwenpaw_step_by_step_execution.md)。
> 用户明确：系统**始终**部署在 NAS Docker 中，QwenPaw 是其中的 Agent/操作入口。**不存在迁移至 NAS**，也不使用 Codex 实施；方案由 ChatGPT 设计、复审，具体工作由 NAS 内 QwenPaw + DeepSeek Flash 执行。

## 0. 一句话决策

保持现有 NAS Docker 和 Python 管线，**确定性程序负责调度与采集、结构化事件库负责唯一真值、DeepSeek Flash 负责少量语义抽取/疑难复核和周月年报分析**；AIHOT 只借鉴验证有效的机制，不直接以 Node/PostgreSQL/网站框架推倒现有实现。GitHub 负责代码、设计、脱敏审计，而不是生产消息总线或全部抓取原文堆放处。Muse 不承担生产链路任何必经环节。

目标：每日自动入池，不发日报；周/月/年报（6/7/8章）+中文音频+经授权的邮件；范围限中国大陆即时零售×美妆个护及对屈臣氏渠道经营有价值的必要关联背景；事实/推断/建议严格分离。

### 原系统复用的现实证据（仓库资料，不等于NAS实时状态）

- 运行位置：NAS Docker 容器中的 /app/working/projects/watsons-retail-intel（**必须在 NAS 内实际核对路径和持久化挂载**）；QwenPaw Agent 及 Python Pipeline 已存在。
- 启动器 scripts/run_daily.sh 与 scripts/run_periodic.sh；实际主程序 skills/run_daily_pipeline/run_daily_pipeline.py 和 skills/run_periodic_pipeline/run_periodic_pipeline.py。旧审计记录约29步、含多源采集、全文补全、日期门、过滤、事件提取、novelty、score、analyze、报告、音频、邮件。
- config/scoring.yaml 八维 [0.18,0.24,0.12,0.12,0.10,0.14,0.06,0.04]，P0≥4.3、P1≥3.6、P2≥2.8；已存在 hard downgrade。需要程序重算，不允许模型自行改分或编造影响。
- config/model_router.yaml 默认与fallback目前均指向 OpenCode 标识 deepseek-v4-flash-free；**其名称不是官方/RightCode deepseek-flash 的 API 端点**。在不改变主流程的情况下先建立适配层。
- docs/09 记录可靠性/安全旧审计：Agent 型 cron 的启动风险、配置漂移、采集贡献不均、run_periodic.sh 日期未向Python CLI透传、发送幂等与 public 仓库跟踪Cookie风险。以上需NAS实查，不能把文档推断当线上运行事实。

## 1. 价格判定：全部按人民币 ¥ / 1M tokens，2026-10-25

### 比价（用户提供RightCode报价；DeepSeek官方中文版价格已核对）

| 渠道 / 时段 | 未缓存输入 | 输出 | 缓存命中输入 | 缓存创建 |
|---|---:|---:|---:|---:|
| RightCode 国模特惠 闲时 | ¥0.50 | ¥2.00 | ¥0.01 | ¥0 |
| RightCode 国模特惠 工作日高峰 | ¥1.00 | ¥4.00 | ¥0.02 | ¥0 |
| RightCode 百亿补贴（按用户所给固定价） | ¥0.60 | ¥1.80 | ¥0.02 | ¥0 |
| DeepSeek 官方 闲时 | ¥1.00 | ¥4.00 | ¥0.02 | — |
| DeepSeek 官方 工作日高峰 | ¥2.00 | ¥8.00 | ¥0.04 | — |

官方来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ 。官方 deepseek-flash 版本为 DeepSeek-V4.1-Flash-1017（请上线时再次核验）；峰时北京时间工作日09:00–12:00、14:00–18:00，其他时段及节假日闲时。第三方渠道型号/价格/可用性以 RightCode 实际账单与调用结果为准。

**结论：DeepSeek 官方API不是最低价。** 在这三种渠道、指定计价条件中：
- 对非缓存输入A、模型输出B、缓存命中输入C，单位均为百万tokens：国模特惠闲时费用 = 0.5A + 2B + 0.01C；百亿补贴 = 0.6A + 1.8B + 0.02C；官方闲时 = 1A + 4B + 0.02C。
- 国模特惠比百亿补贴划算的条件：B < 0.5A + 0.05C；相等则同价。采集辅助、文章抽取通常输入多输出少，优先国模特惠；极高输出占比的周报写作可能百亿补贴略省，不必为很小差价增加故障点。
- 假设月未缓存输入20M、输出4M、缓存命中0：国模特惠闲时¥18；百亿补贴¥19.2；官方闲时¥36；官方峰时¥72。
- **最便宜≠最适合生产**：关键条件是同模型版本、非思考/思考模式、JSON/工具调用兼容性、计费日志、稳定性、速率限制和可控重试。RightCode展示99.6%可用性不能替代项目自己测出的端到端成功率。可用一种渠道为主，异服务商为备用，先按预算批准后再启用备用；不要假装同一渠道的多Key实现容灾。

### 低成本运行方案

- 不调用模型的官方/RSS/网页列表巡检可在白天按需要分频运行。LLM 批处理安排北京时间**07:15后但09:00前**或**18:30后**，避免与每天07:00闭合采集窗口冲突；周一的报告必须等最后一个日窗口入池成功后再开始，实际起止以NAS真实耗时校准。
- 新闻硬规则过滤与URL去重用程序；低置信正文/重要候选做一次Flash抽取；P0/P1和门槛附近才做独立二次评审；周/月/年报在固定证据包基础上使用Flash撰写并审校。禁止全量新闻逐篇两次模型调用。
- 运行时记录 provider/channel/model_version、prompt/completion/cache tokens、计费金额、耗时和错误类型；按日/月设置硬预算，超限不再调用高价兜底或发未经审校的报告。
- DeepSeek官方默认可能是思考模式；常规JSON抽取建议明确非思考，重要综合分析可按实测质量决定思考开关。第三方渠道须做真实 API 探针，不得仅改 config/model_router.yaml 的字符串。

## 2. AIHOT 哪些借鉴、哪些不照搬

AIHOT参考：https://github.com/KKKKhazix/AIHOT （其README明确涵盖六类源：RSS、网页列表、JSON API、X、公众号、脚本外推；按实际更新自适应轮询、宽松预筛、独立双评分、事件聚类、来源热度、SelectBench样本校准）。

| 模块 | 取舍 | 原因与实现边界 |
|---|---|---|
| 信源健康、官网定向栏目、动态抓取频率 | **强烈借鉴** | 首先解决发现层漏报。记录列表访问、单篇打开、日期/正文校验、独立事件贡献和失败；不能把search_only当覆盖。 |
| PASS/UNKNOWN/BLOCK宽松预筛 | **借鉴** | 禁止不确定时用一个关键词规则直接丢弃重要新闻；UNKNOWN进入低成本复核队列。 |
| 两次独立评分 + SelectBench | **部分借鉴，重点验证** | 同一八维评分标准，第一评分与独立复审隔离；对临界值和高价值才二评；冻结真值样本用于客观迭代。 |
| URL→稿件→经营事件，事件与后续更新区分 | **强烈借鉴** | 不同公告日、事件日、生效日不能混；同事件归组与同主题跟进不能误并；不确定一律REVIEW，不套AIHOT“拿不准就合并”。 |
| 48h来源热度/半衰期 | **辅助借鉴** | 一份独立来源算一次；热度只辅助选题，不能把营销转发数量变成P1经营价值。 |
| 网站UI、Node.js 24、PostgreSQL、pg-boss | **暂不照搬** | 当前仅需可靠后台事件池+周报/音频/邮件，复用Python与NAS；如果实测瓶颈才评估替换栈。 |
| 日报网页精选 | **不采用** | 用户已决定停发日报；每日只积累，周/月/年报为主。 |

## 3. 精简后的推荐生产数据流（不改变运行位置）

1. **Scheduler（Docker所在NAS的系统级调度）**：不通过 Agent 思考才能启动脚本；QwenPaw仅作交互运维/人工触发。只有一个调度权威；先核查已有QwenPaw cron，切换前防双跑。
2. **Discover**：官方/垂媒RSS、栏目分页、合法JSON接口、Tavily/XCrawl补漏；记录每源抓取时间、文章URL、发布日线索和来源是否可靠，不绕验证码/私有商家后台。
3. **Fetch / Validate**：按原文核验文章、来源、发表时间、事实日/宣布日/生效日；无法核实全文或日期为 candidate_pending，不进入P1。
4. **Prescreen**：确定性地域/业务硬排除；PASS/UNKNOWN/BLOCK，UNKNOWN低成本模型复核，不以广泛关键词漏掉核心行业事件。
5. **Extract / Cluster / Novelty**：结构化事实 subject-action-object、证据URL/段落、publication/event/announcement/effective/first_seen，URL/正文/跨日事件三层去重；NEW、SAME_OCCURRENCE、SAME_STORY、REVIEW、BACKGROUND清楚分开；有效ID不得复活废止ID，更新必须指向有效旧ID。
6. **Score / Review**：程序读取config/scoring.yaml唯一权重，重算8维并执行硬降级；高价值或临界新事件才二次独立审查，复核结果有证据与分歧记录。
7. **Persist**：NAS本地 SQLite 或已存在可事务化事件库为**唯一写入权威**，镜像 JSONL/Markdown 自动生成，支持原子提交、检查点、锁、重复运行幂等。原始正文/HTML快照在NAS持久化卷，GitHub只推脱敏配置、精选样本/代码和必要审计；公共GitHub禁止私密信息和大批版权原文。
8. **Synthesize / Review / Deliver**：按周/月/年生成独立证据包和来源覆盖门禁，再用Flash撰写事实→机制→影响→动作→KPI→反证的经营判断，经过可核查事实审校后用现有TTS做MP3，最后由幂等发送器投递经授权收件人。覆盖不足时报告显著标记degraded，不可冒充正常完整周报。

### 数据边界与核心字段

Event: event_id / source_urls / source_tier / verification_level / subject / action / object / topic / channels / published_at / event_at / announcement_at / effective_at / first_seen_at / evidence_spans / relation / update_of / status / components[8] / weighted_score / priority / business_analysis / model_version / run_id。

同时分别保存 article_id 与 event_id；纯URL/标题重复、同一事实不同稿、同事件历史进展三类不可混淆。事件ID唯一性由数据存储保证，模型不能自行分配并覆盖正式ID。

## 4. 安全与上线门槛

**先只读审计**：NAS容器内实际服务、挂载、定时任务、过去14/30天采集→全文→事件→得分→报表→投递漏斗、财务与稳定性成本，禁止将旧文档当实时运行情况。扫描 public GitHub 追踪过的Cookie、密钥、联系信息；如有真实泄漏，先轮换凭证再处理仓库历史（仅删当前文件不够）。

**阶段实施，不一口气重写29步**：
- P0 删除/归档Muse遗留并保留备份、审计快照；禁止碰原QwenPaw生产卷。
- P1 源健康与发现层：保留真正贡献高价值事件的源，引入官方可巡检列表；SelectBench基线在筛选调整前冻结；修时间错位问题。
- P2 事件与评分：只改明确存在的问题，先完成旧数据兼容、去重/更新/硬降级/来源证据回归，不新增重复权威账本。
- P3 模型路由：RightCode国模特惠闲时优先（经探针验证），官方直连作备选候选，量化质量/费用/JSON成功率；凭证仅NAS secrets。
- P4 周月年报+播客邮件：生成报告与音频先dry run不发；以完成信号、覆盖门禁、业务审校、唯一投递键控制发送；由用户批准切换。
- P5 与旧配置/生产端对照影子运行一个完整自然周，验收后再切调度；旧容器配置可回滚，不删除历史正式账本。

正式切换最低要求：真实信源列表覆盖证据、旧闻时间防重、事件重复/进展用例、P1/P0正例或明确标unmeasured、评分精确可复算、至少两次相同窗口运行不重复写入、计划任务真实成功、单次邮件与失败恢复、所有秘密不入Git。

## 5. 执行授权与禁区

**ChatGPT负责设计和GitHub文档/验收；NAS中QwenPaw+DeepSeek Flash负责按阶段实施，不用Codex。** 当前本文件仅是方案，不直接变更Docker、cron、模型Key、邮件投递。每一阶段执行后在GitHub新增简短脱敏验收记录，保留运行日志在NAS，返回HEAD和关键真实数据给ChatGPT审查再推进。历史Muse PR #1不得合并；清理细节见文档12。