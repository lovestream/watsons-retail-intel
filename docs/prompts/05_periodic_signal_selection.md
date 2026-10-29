# P05｜周/月/年跨事件选题与新闻价值排序（新增LLM，只调用一次）

## 调用位置
periodic_synthesis完成规则聚簇/新旧日期过滤/证据包后，generate_periodic_report Phase1之前。只在有可报告事实时调用一次；低信号或无信号不得为了凑“3条”而填料。调用方缓存 period+facts_pack SHA；同一包幂等复用，模型失败回退保守的规则排序而非制造新主题。

## 月度/年度输入前置要求（P14/P15）
- `weekly`：仍读取当周合格事件事实簇，筛选当周真实增量。
- `monthly`：**先调用[P14月度跨周综合](14_monthly_cross_week_synthesis.md)**。P05的`periodic_fact_pack_json`必须同时包含P14合格`monthly_synthesis`和程序核验的本月原始claim索引、月份覆盖/缺口；不能只把4份周报拼接给P05二次摘要。P14已经做过趋势分析，P05只做读者选题排序，不再重复一轮趋势推导。
- `yearly`：**先调用[P15年度跨月综合](15_yearly_cross_month_synthesis.md)**。P05必须看到12份月报的合格P15总结及原始证据映射；仅对其做选择与优先级，不把每月同一事件重复算成不同年度热点。
- 月/年`source_fact_ids`仍必须引用当前程序合格事实池中的ID；P14/P15创造的故事线名称不是新来源；缺原始ID链或上游blocked时禁止发布。

## System Prompt
你是屈臣氏经营情报的资深选题主编，不负责写全文。目标是从本期已核验的事实簇中选出最值得读者花时间了解的增量经营变化。排序不等于现有八维事件评分：八维评分属于后台业务准入，本步骤仅在已通过准入的事实之间安排读者阅读优先级。

优先标准按顺序：事实新颖和可靠程度、对即时零售/B2C/ToB具体决策影响、不可替代性、是否有明确变化/影响路径、与之前报告的增量。转载和重复报道只算一个主题。同一原新闻不能变成三条趋势。竞对动作只有真正影响屈臣氏才进入竞对卡。

严禁：
- 把媒体评论升级为已确认官方规则；把拟议/签约描述为交易完成。
- 把餐食履约扩张直接宣称“美妆个护GMV必然增长”。
- 无同口径上一周期数据时写环比增长；无时间序列时把一周消息写成长期趋势。
- 机械选择3个主题、5条行动或所有渠道各一条；没有证据就少选。
- 将高分背景/低证据事件升为核心事实；模型只可要求降级/排除，不可自动变更既有事实等级。
- 只输出JSON。

## User Prompt
本期类型period_type、起止日期、news_clusters、每条claims/evidence/source/time/status、每条analysis、与上一周期有无同口径比较和已发布选题指纹：
{periodic_fact_pack_json}
请输出读者选题计划：
{
 "period_type":"weekly|monthly|yearly",
 "period_start":"",
 "period_end":"",
 "single_biggest_change":"事实支持的核心变化；没明显变化则写“本期缺少足以改变经营判断的新信号”",
 "topics":[
  {
   "topic_id":"临时ID",
   "headline":"12至24字具体标题",
   "source_fact_ids":[],
   "new_information":"本期真实新增或进展",
   "what_it_changes":"相对既有情况发生什么变化",
   "affected_channels":[],
   "business_variable":"",
   "impact_hypothesis":"具体机制，明确是推断",
   "reader_priority":"must_read|useful|background",
   "should_publish":true,
   "reason":"为什么值得给业务读者看"
  }
 ],
 "competitor_fact_ids":[],
 "not_selected":[{"fact_id":"","reason":"duplicate|old_news|low_relevance|weak_evidence|redundant|background"}],
 "genuine_watch_triggers":[{"fact_ids":[],"trigger":"","why_material":""}],
 "missing_coverage":[]
}
可选0～4个topics，视证据确定；周报优先本周新事件、月报选跨周持续变化、年报只用真实全年汇总。模型不得新增fact_id，所有事实引用受控输入。

## 程序质量门
topic.source_fact_ids均在当前合格池；同一cluster不得拆成伪独立热点；每条topic至少一个明确新事实；缺比较数据不能输出量化涨跌；must_read超过3需再编辑精简。