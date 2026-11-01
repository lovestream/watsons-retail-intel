# 架构决策草案：采集频率与周/月/年计算解耦（2026-11-01）

状态：**方案设计；不授权Cron变更/生产邮件/回填/主线合并**。审查基点：PR #2 `release/periodic-reader-brief-20261030@7dd6b8d`，main `2a9ee6b`。本文件是依据代码与配置的设计，不代表NAS真实jobs.json已核对。

## 1. 目标、原理和最重要的约束

核心目标不是「把七次Cron变成一次」，而是：**可靠捕捉不可恢复的公开来源、避免重复下载/LLM分析、按闭合周期形成证据可追溯的简报**。

总耗费近似为：`存储/抓取与查询 + LLM处理的输入token和模型费用 + 失败重试 + TTS/分发`。即便每周只执行一次，若仍要对七天相同篇数的文章逐篇调用LLM，模型token成本未必降低。真正节约来自**进入LLM前的跨日URL/内容去重、低成本筛选、已处理事件缓存、同一事实跨周期直接复用**。

**结论**：推荐`每日轻量发现与证据快照 + 周一按账本增量分析并生成周报 + 月初/年初使用封账下级结果二次合成`。不推荐当前条件下完全停用每日采集。

### 现行可证实代码约束

- `scripts/run_daily.sh`现为每日完整 `collect→...→analyze`，虽然取消了日报与日邮，但每天仍多次调用LLM。与之相对，`run_periodic_pipeline`已经在周一/每月1日/每年1月1日判断需生成的报告；需优先优化**日常处理工作量**，不是重建周期报告Cron。
- `config/sources.yaml`部分RSSHub路由`filterout_days=1`，含 `/latepost?limit=10`、`/36kr/newsflashes?limit=30` 等；原始发现结果本身是**滚动列表和截断源**。周一一次拉取无法保证找回周二出现过、周六已翻页的新闻；单次改一周`start_time/end_time`也无法恢复站点已不提供的历史条目。
- `skills/collect_daily_articles/...::compute_time_window` 默认覆盖前一天07:00到当天07:00；`config/search_policy.yaml` Tavily首选`time_range=day`、不足时`week`；将run_date改成周一不等于真正的7天完整历史搜索。
- `skills/periodic_synthesis/...::period_coverage_check`逐日检查`data/events/YYYY-MM-DD/events_analyzed.json`。若仅周一写一份七天汇总，其余六天被视为missing，周/月/年报告可能因此被误判`failing`。必须先改覆盖计算与每日分区的物化方式。
- `config/periodic_report.yaml`目前weekly.normal=85%、monthly.normal=90%、yearly.normal=95%。这些是针对每日事件文件的旧定义，不宜直接挪用来衡量轻采集。
- `run_periodic_pipeline`已有封账重试，但周跨月、跨年与真实报告完整度可能不一致，不能因为到1日就视为上周/上月全部完成。
- 上游事件`event_occurred_at`日级证据不足（用户验收称487条中118条），**减少抓取频率不会改善这个瓶颈**。应从URL/HTML源、发布时间和事件发生时间独立建模，切勿把发布日期/首次观察日伪装成发生日期。

## 2. 三层数据管道和建议频率

| 层 | 任务 | 首选频率 | 模型调用 | 输出/持久化 |
|---|---|---|---|---|
| L0 信源监控与原始快照 | RSSHub/原生RSS/高时效官方源URL、标题、原文HTML/正文（合法可访问时）、公开时间、首次观察时间、内容hash、抓取状态 | 每天1次，滚动极快源按实测加为每天2次 | **0** | append-only `raw_observations`和`source_health`；source run manifest |
| L1 增量入池/轻量初筛 | URL规范化、标题hash、跨日近重、日期合理性/是否已在事件账本、规则相关性；必备数据补抓 | 每日L0后，轻量增量 | **默认0**，只对经业务验证的少数高价值增量可选LLM | `article_ledger`、缺口/待抽取队列 |
| L2 本期批量事实计算 | 对**未抽取/发生实质变化**的候选文章分批P01/P02/P03/P04+评分；逐篇checkpoint，重试仅处理缺口 | 周一早上，须在L0完整收集上周末来源后 | 按候选文章数量，支持总token预算和分级熔断 | 原子物化 `data/events/YYYY-MM-DD/events_analyzed.json` 或新event store+分区视图；每条追溯到raw observation |
| L3 周报 | 冻结上周本地时间`周一00:00~周日23:59:59`业务归属，P05/P08/P09/音频/HTML/manifest | 周一，L2与coverage门完成后 | 只选择本周重大变化 | Wxx 周报闭合manifest |
| L4 月报 | 读取当月**已闭合的事实/周报**，按自然月事实归属重新去重，P14跨周综合后P08/P09 | 月初，最后一周相关事实完全闭合**之后** | 汇总级LLM，不重新逐篇抽取 | YYYY-MM 月报闭合manifest |
| L5 年报 | 读取已闭合的12个月报/年度事实账本，P15跨月综合 | 次年1月，12月月报闭合之后 | 汇总级LLM，不重新抓取全年新闻 | YYYY 年报闭合manifest |

L0 不等于完整抓全网：仍需每类来源「发现窗口是否可追溯」「路由条目是否饱和」「是否要分页/增量游标」等实证。抓取成功但`0`条新文章要有`zero_new_verified`，区别于服务故障。L0保存原始证据和采集元数据，但禁止把observed_at当published_at或occurred_at。

### 可替代方案：完全每周只采一次

可作为**对照实验**，但不能直接上线。至少连续4周并行验证：同样的公开来源列表，周一单次“回溯一周搜索+抓取”对比每日L0累积；按唯一URL和经核对的P0/P1事件计算相对召回、严重遗漏、来源覆盖和成本。预设建议门槛：高优先级有效事实召回≥95%，零关键平台/竞对重大遗漏；超过预算、遇到来源滚动截断或者周末难以回溯，判不合适。达到这一经验标准前**保留每日L0**，尤其RSS和新闻快讯路由。

## 3. 调度时钟与跨周期边界（Asia/Shanghai）

- 周报：2026-11-02周一处理`2026-10-26 00:00:00`至`2026-11-01 23:59:59`，晨间进行覆盖补采，然后分析/出稿。11月2日晨新消息如果是新发生或首次发布，不得误放入上周；反之晚发布的上周事件，应由`event_time/date_role`核实，并保留late-arrival修订账本。
- 月报：例如**2026-10-01~10-31**，在2026-11-01（周日）不能直接要求最后跨月周（10/26~11/01）已封账；等2026-11-02周报和逐日事实视图齐全后再生成10月月报。即便借用跨月周报，也必须滤掉11/01事实。采用`dependencies_closed`事件，而非“到了每月1日=立即可发”。
- 年报：次年1月1日通常不能假定上一年12月月报和跨年周已经闭合。应等待`12个月份都complete`、源覆盖门通过后再跑；跨年周/迟到材料按归属时间切分。
- **迟到新闻**：文章发布时间与事件发生日期分别保留，按公开证据确定period membership；若发生日未知，只能对“本期观察到/本期报道”做归属，不能声称“本期发生”。支持freeze_at、late_arrival_count和修订版号，避免迟到事实重写已发邮件而不留痕。
- 正式邮件继续hold；报告已生成、审稿通过、音频存在只是`sendable`候选，不能自动改变SMTP开关。

## 4. 改造点（不得仅改Cron）

1. 新建L0 lightweight入口，复用RSS/官方源函数，但跳过LLM过滤、P02/P04、评分和日报发送。L0应以`source+URL/content_hash`写**追加型历史记录**，原文和引用证据可追溯，不覆盖昨日日志；附带来源错误码、上次成功、最大返回条数饱和标记、抓取窗口和采集时间。
2. 跨源稳定去重：`canonical_url + stable article_id`、内容变化hash和`first_seen_at/last_seen_at`、原文发布日期及精度。相同文章跨周只做增量事实更新；不可把新文章简单按标题文本同义粗暴去掉。
3. 周一增量批处理：优先高价值+未抽取的数据，按稳定ID分段；每段`checkpoint`且事务式写结果，限制LLM请求次数/总token/预计费用，失败不自动重做整批；未知日期按发布日期（且明确source）进入观察池，不编发生日。
4. 覆盖门重写为**来源级采集成功率 + 时间区间覆盖 + 事实抽取已处理率 + 关键平台/竞对业务覆盖**四维。逐日新事实=0是正常结果而非missing。周报不再依赖“每天必须有非空events_analyzed.json”；可提供每日`no_signal_validated`封账记录。月报/年报复用这一证据链。
5. L2以原始`event_occurred_at`精度字段区分`day/week/month/quarter/unknown`；只有确有证据的日级日期能填`occurred_on`。对时效事件另保留`published_at`、`first_observed_at`、`revised_at`。提高24%需做上游原文证据检索/提取器质量提升，不得靠推断补齐。
6. L3/L4/L5基于封账manifest产生任务依赖图：`daily_source_windows_closed -> weekly_events_closed -> weekly_report_closed -> monthly_report_closed -> yearly_report_closed`；缺依赖hold，重复运行幂等，无已封账证据绝不宣称complete。
7. 现行旧的`config/cron_daily_0700.json`、`cron_watsons_pipeline.json`和`config/runtime.yaml.schedule`可能是历史规格，实际agent jobs.json不一定匹配。上线前必须获取**NAS当前真实cron/jobs.json、工作目录SHA和环境变量**，按现网灰度，不以仓库旧配置直接覆写。
8. 注意每日真实新闻入池的数据时效依赖：如果周一才抽取，本周重大突发也只在下周看到；产品如未来要P0即时预警，可让L1规则触发不经LLM的告警候选，或仅对重大事件小额LLM提前抽取，但不恢复全量日报。

## 5. 迁移、评测与回滚

阶段0：保留现生产运行不动，新增只读baseline收集：7日的原始URL、唯一文章数、有效P0/P1、token与费用、各来源feed条数上限、逐日覆盖、任务耗时和失败/重复LLM。不得读取或改写真实收件人/邮件密码。

阶段1：隔离worktree运行每日无LLM L0/L1并与旧每日完整流程并行比较，至少连续2周，核对快讯的历史丢失风险、正文快照留存、去重及过量来源。零成本不可保证，网络/储存仍需预算。

阶段2：周一对冻结L0数据做L2批量增量计算和周报shadow，保留现行周/月/年结果作对照；验证分批中断恢复、逐日事件视图、fact时间角色、P09最终审稿稿音频三哈希，并做10轮可复现测试、模拟feed超时/周一漏跑。

阶段3：用近2个完整月做跨周/跨月交界回放，尤其 2026-10-31/2026-11-01 边界；核对10-12~18缺口，不能靠变更覆盖算法把真实缺口变成正常。验证月报P14/年报P15不重新抓全月/全年原始文章且不引用边界外事实。

阶段4：**另行取得生产Cron改动授权后**，只切换计算频率，留一个可一键恢复旧日管线的开关。至少4个连续周闭环验收，任何高优先级重大漏报、覆盖下降、重复抽取/邮件、TTS语义不一致、LLM预算超限或核心来源故障均立即回退；保持正式邮件hold直到独立审批。

### 测量仪表建议

`source_route_snapshot_count, route_capacity_saturated, unique_url_count, first_seen_age_hours, missing_source_windows, high_priority_recall, extraction_backlog, extraction_skip_cached, llm_tokens_used, llm_spend_estimate, p09_first_pass_rate, p09_material_false_approval_count, period_source_coverage, valid_zero_signal_days, weeks_closed, months_closed, late_arrival_count, report_ready_at`。

**合格标准不是「少跑六天」，而是「覆盖不降、实质错误不增加、交付可追溯，网络+LLM总耗费确实下降」。**
