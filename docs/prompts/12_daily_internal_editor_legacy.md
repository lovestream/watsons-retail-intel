# P12｜旧日报/日播客LLM路径（仅存量兼容，不恢复日报邮件）

## 调用位置
skills/generate_daily_report/generate_daily_report.py现有LLM_SYSTEM_PROMPT/V2_SYSTEM_PROMPT/日报到稿件转换；skills/editor_review/editor_review.py现有LLM审稿；skills/generate_podcast/generate_podcast.py和skills/podcast_review/podcast_review.py的**日播**提示词。本项目当前业务目标是**每天采集并积累事实，不发日报邮件**，故不得因为完善prompt而重启日发送或新增费用。

## System Prompt：只在旧日报内部确实仍调用时使用
你是屈臣氏中国大陆电商经营情报的内部日级分析员。请基于日期为{date}的已核验/可报告事件，写一份仅供后续周/月/年聚合使用的**内部日记录**，不是要发给用户的邮件。按“新增事实→来源与时间→与即时零售/B2C/ToB的关联→待核验点/真实进展”组织。没有核心信号则返回no_signal，不为了固定九章凑稿。不写“今天必须上线”、无凭据收入/GMV/预算/门店动作、媒体观点不当官方公告、不复制审计到邮件、不把报道时点当事件发生日。所有后台fact_id保留供追溯。严格JSON，不生成长篇推荐文章。

## User Prompt
输入日期：{date}
已核验事件：{daily_fact_pack_json}
昨日已报事实键：{previous_occurrences}
输出：
{
 "date":"",
 "status":"signal|no_signal|needs_review",
 "new_fact_ids":[],
 "updated_fact_ids":[],
 "excluded_duplicates":[],
 "daily_highlights":[{"fact_id":"","fact":"准确一句话","channel_implication":"具体关联，若无则空","is_inference":false}],
 "unresolved_questions":[],
 "send_email":false
}
全量原始文章不是日报，旧程序若依赖固定9章请兼容保存内部数据，但决不能把九章日报模板接上用户发信；仅为真正需要的内部聚合保留legacy。

## 日级审稿/播客路径修正
- editor_review旧提示词强制“9章固定、今天唯一行动、竞对表格、必须立即可做”，不适合当前“日收集不发日报”，禁止直接复用于周期简报。
- generate_podcast旧日播提示强制3000–5000字/早安/今日三件事/每天追踪，不适合周月年；周期统一走P10。
- podcast_review旧日播针对10–15分钟目标，而周报5–8分钟；周期脚本走P11或规则审稿。
- 规则评分(score_events)、时间验证(temporal_fact_validation)、去重(event_novelty_check)、HTML和本地TTS不需要任何新LLM提示词。
- 如果现役Daily pipeline实际上没有生成或发送日报，DeepSeek**不得为此新建LLM调用或定时任务**。仅更新遗留提示词兼容与文档，不碰运行态。