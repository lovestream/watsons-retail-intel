# P0｜来源质量、跨日事件身份与周期去重：统一准入与验收标准（2026-11-01）

状态：**规则/门槛冻结供隔离影子验收**。本文件不改变`sources.yaml`、RSSHub/Tavily配额、Cron、事件账本、收件人、P02/LLM调用、正式邮件发送。依据PR #8 HEAD `6e8c835`的NAS隔离报告和实际代码阅读。与PR #4的“每日L0轻量留证、周一增量事实计算、月/年封账聚合”配套。

## 一、根因认定（精确到代码，不再靠增加黑名单）

1. **日内URL去重，不等于跨日文章去重**。现`skills/merge_raw_articles/merge_raw_articles.py`中的`seen_urls`只存在于单次运行，无法阻止10/1的文章于10/30重新进入采集。
2. **事件账本身份不稳定**。`skills/event_novelty_check/event_novelty_check.py::generate_cluster_key`基于event_type+正规化标题前40字符+实体集合；语义相同而标题重写会生成不同键。仅当同一cluster`fact_hash`不同，也直接设置`fact_has_new_delta=True` / `new_delta_fields=['fact_content']`，即**换措辞即可被当作新事实**。
3. **账本有同日重跑非幂等风险**。`save_ledger_entry`每次都追加JSONL，同日重跑可能继续累计`fact_seen_count`。这不是内容新增。
4. **周期聚类把重复出现当证据**。`skills/periodic_synthesis/periodic_synthesis.py::cluster_events`每次匹配都会增加`event_count`，随后用`event_count>=3`提高confidence；相同文章23次不能因此被评为高信度。其`days_active`只根据first/last两天求集合大小，最多2，不能代表真实不同天数。
5. **周期入口存在潜在旁路**。`_is_archive_event`允许满足`_is_republished_but_valid`的ARCHIVE重入，但此分支未独立复核新的business_scope；月/年`facts_pack.build_from_events`主要检查`reject`和文本hash，也不保证所有来自main合格事件。必须验证经过score拒绝的餐饮/招聘是否被周期重新载入。
6. **新人工CSV是语义聚合代理，不是事件金标准**。37条main→7组、104条reference→16组，目前人审列为空。菜鸟一组出现23次（10/01–10/30日期跨度），不等于23个独立来源、23个日期或23次业务变化。

## 二、先定“何为一条真实新情报”

四个分离实体，**不能混用**：

- `observation_id`：某个采集器、某次运行观察到一条来源。可以有23次；用于诊断采集源与历史覆盖。
- `article_id`：来源独立文章。URL去跟踪参数、主域名/规范路径、重定向终点/规范网址和来源原文版本建立稳定身份。同URL多次抓取是一个article及多个observation；跨媒体转载同文不必是多篇独立报道。
- `canonical_event_id`：**主体+业务动作+对象/品类/渠道+发生阶段**。例如“菜鸟|为淘宝闪购|接入配送”同阶段为同一事件；“宣布接入”与“已经覆盖新城市”可以是不同阶段，但须有各自原文证据。不能仅用模糊“主体+动作族”把不同事件永久合一。
- `claim_id/update_id`：同一事件的**新证据/新结果**（新合同、上线日期、覆盖城市、门店数、官方修订等），须有来源差异证据。标题改写、日期改成报道日、媒体转述、数字重新排版**均不能**自动产生update。

每个canonical_event保留`first_observed_at`、`last_observed_at`、`distinct_observation_days`、`observation_count`、`distinct_canonical_urls`、`independent_primary_sources`、`source_urls`、`published_at`、`occurred_at/precision`、`has_material_delta`、`delta_evidence`。**出现次数不参与事件重要性/真实性加分**。

去重判定按顺序：同规范URL+原始内容hash → 原文重复；同URL但hash变化→对照改动段落及业务delta；不同URL→按实体/动作/对象/阶段和证据关联，低置信进入review，不强行合并；同一事件确有新增经营事实→只生成一条对应更新并保留旧版本。原始observation不删除。

## 三、来源调整标准（分层决策，不按文章数量冲业绩）

**核心衡量指标**：`verified_unique_relevant_events`，即某来源对于全系统的**边际新增、经人审或证据支持的经营事实**。另记录`novel_business_updates`、`source_original_fulltext_rate`、`source_authoritative_rate`、`irrelevant_subject_rate`、`duplicate_observation_rate`、`P0/P1_unique_event_recall`、`collection_failures`、`query_cost_per_verified_unique`、`time_to_first_observation`。所有分母从实际run manifest产生；为零时显示N/A，不能伪造0%。

### 来源保护分组

| 分组 | 来源 | 规则 |
|---|---|---|
| A｜必须守住 | 屈臣氏中国官方、核心美妆个护竞对官方、主要即时零售平台的个护美妆/商家规则页；真正不可回溯的发布源 | **即使14天零新事实，也保留轻量发现**；可减少重复查询，但不得在无替代覆盖时停用 |
| B｜按效果优化 | 36氪即时零售、美妆专业媒体、与目标品类直接相关的资讯源和精准关键词搜索 | 优先`平台/竞对 × 个护美妆 × 具体动作`；去重后按唯一事实、正文取得率及花费调整 |
| C｜宽泛发现 | `即时零售 最新`、`门店`、`消费`、`本地生活`、宽泛36氪消费/虎嗅门店/零售门户等 | 先限制为低频哨兵/只发现URL；若连续**两段非重叠7天**样本均至少10条、经溯源**新增合格事实0条**、主体无关拒绝率≥90%，则提出下线候选；对照搜索必须证明零独有重要漏报才能实际关源 |

### 依据已报告数据的**候选动作**（尚未应用配置）

| 来源 | 原始候选 | main | 建议 |
|---|---:|---:|---|
| `rsshub_36kr_search_instant_retail` | 10 | 6 | **保留优先**；先对6条去重确定唯一新增数量，未确认前不得因60%就无条件扩额度 |
| `cloakbrowser_cls_beauty` | 135 | 23 | 保留美容垂直发现，但要核实23条中有多少是重复新闻、多少是高价值独有 |
| `tavily_gap_search` | 42 | 4 | 保持预算上限，以缺口主体/品类查询为主，去重结果决定是否触发验证搜 |
| `cloakbrowser_cls_instant_retail` | 153 | 0 | **降级为低频哨兵候选**，改窄为即时零售×美妆个护×动作；先证明其独有高价值召回没有丢失 |
| `rsshub_huxiu_search_store` | 93 | 0 | 停止把“门店”宽泛搜索当核心来源的候选；改为屈臣氏/丝芙兰/万宁等具名门店+动作 |
| `cloakbrowser_36kr_consumer` | 15 | 0 | 泛消费频道降级候选；保留36氪具名目标企业/渠道专搜 |
| `cloakbrowser_pinguan_beauty` | 53 | 0 | 尽管当前main为0，仍属个护美妆垂直来源，**不可仅因0%即删除**；查是否大量活动宣传/缺全文，转为目标品类经营动作专项 |
| `cloakbrowser_36kr_beauty` | 30 | 0 | 保留试探性检索，对全文质量/独有来源贡献审计后再调比例 |

**执行顺序（2026-11-01 用户修订）**：先由**历史数据离线模拟**多个 query/source 组合可召回的经营事件；选取**过去已经结束、具有真实来源证据的连续14个自然日**，以两个相邻7日窗口进行新旧链路离线/隔离回放对照，**不要求未来连续14天在线并行采集，也不新增生产 Cron**。历史 RSS 不能按日期回溯时，只能使用当日已留存快照或公开历史归档；无法恢复的日期/来源标记不可重建，不得以当前搜索结果伪称当日采集。对新增 RSS/官方页面另做一次性现实可用性试抓；历史回放不等于14天线上稳定性验证。完整任务书见 [Watsons 采集优化任务书](WATSONS-COLLECTION-OPTIMIZATION-TASKS-2026-11-01.md)（文档候选见 PR #10，合并前以该 PR 中的文件为准）。满足来源/主体覆盖保护与关键 P0/P1 无独有漏失，才可提出配置调整。**不得根据0% main直接物理删除或关闭**。用户未授权的真实 Cron/网络批量采集操作保持不动。

## 四、去重链路统一验收，不能只过准入层

- L0：同一URL23次观察必须是**23 observations**、**1 canonical article**（前提确系同一原稿），所有原始记录留存。
- L1：同一原文重抓不得再次排队P02；若hash有变化但无新业务事实，则保持同article/version并只刷新`last_seen`。
- L2：多来源提及同一主体+动作+对象+阶段时，只是同一canonical_event的多条`source_refs`，不能新增reportable事件；经过验证的新动作/新阶段，才新增update。
- L3周报：同一canonical event在同一周最多**一个主题卡**；同一篇被抓23次时`independent_primary_sources`仍按原始独立来源计算，不能将confidence从low刷成high。
- L4月报、L5年报：跨周复现保留历史追溯，但核心内容再次入选必须有经证据支持的`material_delta`；月/年累计数只按唯一事件/真实更新计，不按observation数计。
- 10轮相同输入确定性重放，`canonical_event_id`、去重决定、periodic核心卡顺序、数量及hash必须稳定；重复运行不能使`fact_seen_count`、`report_count`错误增加，也不能凭借保存顺序改变“最新”日期。
- **伪造有意义更新的反例**：“菜鸟接入淘宝闪购”文本A→B→A反复替换，仍1个canonical event且无新增；另给该事件“正式覆盖新城市”并附独立证据，允许1条material_delta，不可被过度去重。
- **周期入口需独立范围硬门**：`_is_archive_event`、`facts_pack.build_from_synthesis`和`build_from_events`均应复用业务范围；旧库ARCHIVE不可只因热度/旧闻豁免重入。用户点名必丢：野人先生、纯股市、招聘、寿司郎、SAP；必留：屈臣氏共创（用独立已知文本fixture）、有证据的屈臣氏和核心竞对经营动作。
- 仅对**可检验的真实身份**下结论：当前CSV的`occurrences`与`days`并非人工真值，无需强制把所有7+16分组当真实事件。

## 五、验收准入/失败门

1. 用**实际7组main + 16组reference**作为人工审查工作量的起点，每组检查代表与各成员是否真同一事件（重点展开“菜鸟23次”、`|其他59次`）；不是只批注代表标题即可宣称全组正确。
2. 必留的明确主营业务动作金标准**零漏报**、必丢噪声**零漏放**，但必须逐项报告样本量和具体ID；不在大样本未审时虚称总体精准率100%。
3. 对每个source返回：raw_observations、canonical_articles、human_verified_unique_events、new_material_deltas、fulltext_ratio、重复比、P0/P1边际独有贡献、单位成本。不能仅展示main数量。
4. 同一个文章被抓23次，**报告最多出现一个未更新主题**，不能因此提高confidence、趋势加速或生成新fact；有独立后续事实则恰当地显示更新。
5. 周/月/年各做无LLM**事实包影子测试**和必要LLM之后的出稿对照（只有额外费用授权后才做后者），并检查跨月/跨年边界、报道日期口径、拒发门。
6. 采集源配置只允许在影子配置上验证，通过后再另行确认并触及实际source YAML与Cron；正式邮件继续hold。
