# 11｜NAS Docker + QwenPaw 经营情报系统：架构决策、AIHOT 对标与 DeepSeek 成本方案

> 日期：2026-10-25（北京时间）。**设计稿 / 待 QwenPaw 在现有 NAS Docker 环境逐阶段实施；不代表代码已修改、服务已重启、调度已变更。**
> 本篇为当前项目的**最新实施方向**；历史 Muse 独立试验（docs/10 和 muse/）不再是建设方向；**由 Muse 自己负责在 GitHub 做一次性退役清理**，不委派 NAS 上 QwenPaw。具体清理边界与后续 QwenPaw 分工见 [12｜分阶段执行手册](12_20261025_qwenpaw_step_by_step_execution.md)。
> 用户明确：系统**始终**部署在 NAS Docker 中，QwenPaw 是其中的 Agent/操作入口。**不存在迁移至 NAS**，也不使用 Codex 实施；方案由 ChatGPT 设计、复审；Muse 清理由 Muse 完成，后续优化由 NAS 内 QwenPaw + 已配置的 DeepSeek Flash 执行。用户已在 NAS 配好模型，**无需本任务书指导 API Key、Provider、接口接入或模型切换**。




> **2026-10-25 最新权威阶段与模型决策**：M0/M1/R0已完成；R1隔离测试已报告SiliconFlow主通道成功，**本轮只批准R1-FIX整改，未合并生产main**；R2/R3/M2仍暂停。**主：SiliconFlow（硅基流动）DeepSeek-V4.1-Flash；备：RightCode「百亿补贴」DeepSeek Flash**，有限付费回退、不分时切换。参阅[正式R1复审与整改指令](reviews/NAS-R1-CHATGPT-REVIEW-2026-10-25.md)。

> **2026-10-25 最新复审优先**：R1-FIX 已按用户确认的范围通过，**不再要求系统自建费用台账、日金额预算或单价估算**；SiliconFlow 主，RightCode 百亿补贴备。下一步只做**R2-A 隔离日管线与调度/健康检查验证**，不改正式Cron/生产账本/SMTP、不中途合并R1。详见 [R1-FIX最终审查与R2-A任务](reviews/NAS-R1-FIX-CHATGPT-REVIEW-2026-10-25.md)。

## 0. 一句话决策

保持现有 NAS Docker 和 Python 管线，**确定性程序负责调度与采集、结构化事件库负责唯一真值、DeepSeek Flash 负责少量语义抽取/疑难复核和周月年报分析**；AIHOT 只借鉴验证有效的机制，不直接以 Node/PostgreSQL/网站框架推倒现有实现。GitHub 负责代码、设计、脱敏审计，而不是生产消息总线或全部抓取原文堆放处。Muse 不承担生产链路任何必经环节。

目标：每日自动入池，不发日报；周/月/年报（6/7/8章）+中文音频+经授权的邮件；范围限中国大陆即时零售×美妆个护及对屈臣氏渠道经营有价值的必要关联背景；事实/推断/建议严格分离。

### 原系统复用的现实证据（仓库资料，不等于NAS实时状态）

- 运行位置：NAS Docker 容器中的 /app/working/projects/watsons-retail-intel（**必须在 NAS 内实际核对路径和持久化挂载**）；QwenPaw Agent 及 Python Pipeline 已存在。
- 启动器 scripts/run_daily.sh 与 scripts/run_periodic.sh；实际主程序 skills/run_daily_pipeline/run_daily_pipeline.py 和 skills/run_periodic_pipeline/run_periodic_pipeline.py。旧审计记录约29步、含多源采集、全文补全、日期门、过滤、事件提取、novelty、score、analyze、报告、音频、邮件。
- config/scoring.yaml 八维 [0.18,0.24,0.12,0.12,0.10,0.14,0.06,0.04]，P0≥4.3、P1≥3.6、P2≥2.8；已存在 hard downgrade。需要程序重算，不允许模型自行改分或编造影响。
- 仓库中的 config/model_router.yaml 仍记载历史 OpenCode 标识 deepseek-v4-flash-free，但用户已在 NAS 中配置好可选模型；**先由 M1 只读核对当前实际调用路径，不能依据旧仓库配置推断在线模型，更不再安排单独的 API 接入/模型配置任务**。
- docs/09 记录可靠性/安全旧审计：Agent 型 cron 的启动风险、配置漂移、采集贡献不均、run_periodic.sh 日期未向Python CLI透传、发送幂等与 public 仓库跟踪Cookie风险。以上需NAS实查，不能把文档推断当线上运行事实。

## 1. 模型渠道：SiliconFlow主通道，RightCode「百亿补贴」有条件备用

**用户最新决策（2026-10-25）**：SiliconFlow的DeepSeek-V4.1-Flash作主通道，利用已持有的可用免费券/额度；RightCode百亿补贴DeepSeek Flash为受控付费备用，只有主路不可用或额度耗尽且未超过备用预算时才允许自动接管。不按峰谷调度，不另行购买或更改QwenPaw Agent现有Provider。R1正在隔离开发Python独立调用适配器，**不是生产已接通**；若Python进程要读取额外Secret引用，必须经安全审批。

RightCode 「百亿补贴」DeepSeek Flash，按用户提供的报价，单位均为**人民币 ¥ / 百万 tokens**：

| 未缓存输入 | 输出 | 缓存创建 | 缓存读取 |
|---:|---:|---:|---:|
| ¥0.60 | ¥1.80 | ¥0 | ¥0.02 |

RightCode 「国模特惠」闲时 ¥0.50 输入 / ¥2.00 输出 / ¥0.01 缓存读取；在工作日峰时会涨价。因此尽管某些输入量较大的任务在闲时用特惠可能略省，**用户不再为了省一点成本而专门安排峰谷时段**；RightCode百亿补贴仅为备用，免费/优惠额度耗尽不等于已完成备案预算，按后台真实计费结算。

估算示例：每月未缓存输入20M、输出4M、缓存命中0，则百亿补贴约 ¥19.20；该数字不包括搜索/API/爬取等费用。该数字仅是RightCode备用独立承担全部调用时的示例，而不是目前主用渠道预测。隔离R1采用主/备路由，但备用调用预算、凭证权限与负例质量门未通过，不得上线；不建立自动分时切换。

### 运行安排不再受模型折扣时段约束

- 白天或夜间采集频率按**来源更新节奏和业务时效**确定；模型调用按采集完成信号/完整性门禁触发，不以价格时段来阻断。
- 仍应减少浪费：硬规则过滤/去重先做，低置信/重要候选用Flash复核，高价值或分数临界才二次评分，周/月/年报对冻结事实包分析。
- M1 只读盘点实际 token 计量与执行结果；后续只做可观测、预算上限、429/超时处理及质量验证。**不修改用户已设置的模型连接、不重填 Key、不强制重新测试所有渠道**。

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
- P0 **Muse 自己**清理其创建的GitHub独立试验（经核对的Muse文件与引用、关闭Draft PR但不合并；保留可恢复历史）；QwenPaw不执行此阶段。
- P1 源健康与发现层：保留真正贡献高价值事件的源，引入官方可巡检列表；SelectBench基线在筛选调整前冻结；修时间错位问题。
- P2 事件与评分：只改明确存在的问题，先完成旧数据兼容、去重/更新/硬降级/来源证据回归，不新增重复权威账本。
- P3 **模型运行质量与成本治理**：用户已有QwenPaw模型注册，但Python独立调用需要R1的adapter/凭证引用；采用SiliconFlow主、RightCode百亿补贴备，不做峰谷切换；R1-FIX必须完善付费回退计数、预算、密钥权限与负例事实校验，审查通过才考虑生产启用。
- P4 周月年报+播客邮件：生成报告与音频先dry run不发；以完成信号、覆盖门禁、业务审校、唯一投递键控制发送；由用户批准切换。
- P5 与旧配置/生产端对照影子运行一个完整自然周，验收后再切调度；旧容器配置可回滚，不删除历史正式账本。

正式切换最低要求：真实信源列表覆盖证据、旧闻时间防重、事件重复/进展用例、P1/P0正例或明确标unmeasured、评分精确可复算、至少两次相同窗口运行不重复写入、计划任务真实成功、单次邮件与失败恢复、所有秘密不入Git。

## 5. 执行授权与禁区

**ChatGPT负责设计与复审；Muse只负责一次性清理自己创建的GitHub试验内容；NAS中QwenPaw+用户已配置的DeepSeek Flash负责后续系统优化，不用Codex。** 当前仅为方案，不直接变更Docker、cron、模型Key、邮件投递。Muse清理完成后，由QwenPaw从M1只读审计开始；每阶段提交脱敏验收记录和HEAD，ChatGPT复审后推进。历史Muse PR #1不得合并；清理任务见文档12。