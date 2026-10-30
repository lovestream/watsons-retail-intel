# 独立复审：15份Prompt接入与W43简报 / P14-P15跨期综合（2026-10-29）

> **结论：NO-GO（暂不允许生产合并/自动发送）**。本审计仅阅读GitHub已提交的`feat/prompt-integration`分支源码和交付产物、独立打开3篇重要外部报道；未在NAS运行代码、未重放付费LLM、未发送SMTP、未改Cron/PID1/生产事件账本。截图文件存在，未对其进行逐像素视觉复审。当前分支审查HEAD为 `3e5244c7e4e380b5b8182f5ea76d8d1c61cced20`；审查报告写入后HEAD会推进。

## A. 有证据支持的进步

- `skills/reader_brief/editor.py` 实际接入P05选题和P08简报写作；`schema.py`实现fact_id、数值、链接、日期、状态及内部标记的确定性检查；`render_mobile.py`用结构化JSON单列渲染。
- W43产物存在：`docs/reviews/email-ux/2026-10-29-llm/{W43_fact_pack.json,topic_plan.json,reader_brief.json,p09_review.json,manifest.json,brief.html}`和四个宽度的截图；重点数量为2变化/1竞对/2行动/2观察/8排除。
- P14在2026-07的`M2026-07_gate.json`列出W27～W31五个相交周且返回`incomplete`、不调用LLM。**仅能证明这组缺少全部周报的负例正确处理，不能证明完整/部分缺失/已封账正例。**
- 文本语气与有条件行动较旧版有改善；网页没有直接展示内部事件ID与评分（依据`brief.html`文本）。

## B. P0：必须整改，否则不能通过

### P0-01 P09超时仍标pass和deliverable，交付状态与事实相矛盾

证据：
- `p09_review.json`: `gate.status=pass`, `decision=deterministic_only`, `degraded=true`, `review=null`，SiliconFlow请求超时300s且备用连接失败。
- 同批`manifest.json`: `deliverable=true`, `gate=pass`, `gate_summary="P09 approve（程序确定性检查通过）"`，`p09`子对象没有`degraded`。HTML页脚仍写“13条已核验事实”“附本期口播音频”，却没有本批次音频交付证明。
- `skills/reader_brief/p09_reviewer.py::review` LLM不可用时，只要确定性无critical就返回pass；`run_brief.py`仅在`gate.status==block`时阻止，且`deliverable`与P09真实执行状态无关。

要求：定义`approved|hold_for_review|blocked`三个互斥交付状态；P09不可用应是`hold_for_review`且`deliverable=false/sendable=false`，允许HTML预览但禁止SMTP；任何程序门错误直接blocked。manifest/p09_review/HTML的版本、结果、复核标志必须一致，禁止缓存旧摘要。未验证附件真实存在和SHA前不得写“附音频”。保留人工复核后明确放行入口，但不能自动当P09已审。

### P0-02 P14/P15正例有确定性模板渲染异常

证据：
- `docs/prompts/14_monthly_cross_week_synthesis.md`和`15_yearly_cross_month_synthesis.md`的User Prompt都要求`{send_at}`。
- `skills/periodic_synthesis/cross_period.py::monthly_synthesis/yearly_synthesis`提供`generated_at`，但payload均没有`send_at`。
- `skills/utils/prompt_registry.py::render`会因必填变量缺失抛ValueError。
- 2026-07仅回放了提前return的`incomplete`分支，没有进入`render(P14)`正例。

要求：补齐全部变量并建立**完整5周月报**和**12月完整年报**正例，mock LLM实际走完模板渲染、JSON/schema验收、缓存和输出；不得只测试缺输入负例。

### P0-03 覆盖门只验文件存在，不等于“已封账”

证据：`cross_period.py::collect_weekly/collect_monthly` 只判断报告Markdown路径能否读到非空内容，提取前600字符，未读取`data/runs/periodic/{type}/{period_id}/manifest.json`确认封账、质量门、source coverage、有效日期、版本。月/年`degraded`时仍会尝试调用LLM，并非一律严谨hold。

要求：依据实际manifest与事实索引校验`period_end/closed/quality_pass/source_coverage/report_sha`，区分`complete/degraded/incomplete`和`publishable`。自然月的相交ISO周需要按当月日期切片或由正式日级事实补齐；不能把跨月一整周当自然月信息。月初/年初在末周/12月未封账时须hold而不是假定已完成。若缺证据仅允许带缺口的内部草稿。

### P0-04 P14/P15不具备足够的综合输入和原始claim lineage

证据：`collect_weekly`与`collect_monthly`只给每份报告的标题或前600字符；`_event_claims`仅搜索硬编码`2026-*`日期目录，最多60条且仅取date/event_id/title/source_url，无原文claim、status、date_role、版本、跨月事件链。年报payload`monthly_synthesis_and_lineage_json=[]`，无法消费12个月P14结果；`_attach_lineage`只是附加报告摘要和覆盖门，没有逐条验证模型引用的fact_id/claim_id/url；LLM生成的P14/P15 JSON也没有结构与ID验证。

要求：P14必须提供真实周报的可追溯结构与其本月日级事实；P15必须提供12份月报**及相应12份P14结果和源claim索引**。程序在LLM之前去重并计算统计、在LLM之后逐条校验声明证据，禁止用截断摘要替代完整事实。去掉年份硬编码，缓存键加入真正的源版本、prompt、model；修改任一下游所依据月报时年报缓存必须失效。

### P0-05 月/年综合没有进入最终编辑器的有效输入

证据：`run_brief.py`的`pack["synthesis"]=synth.get("synthesis")`，但`editor.py::write_brief`传给P08的是`reader_fact_pack_json(pack)`，该函数仅返回`facts[]`扁平列表，没有`synthesis`。P05虽收到`previous=synth.get("synthesis")`，却把**当前**月/年综合包放到`previous_comparison`字段，语义错误。`--pack-in`分支更是能绕过月/年没有合格综合包的检查。

要求：P05/P08/P09共用冻结的period-specific结构包，月报显式读P14、年报读P15，核对当前期与上一期比较字段，拒绝`--pack-in`绕过门禁；增加正例断言“最终月/年邮件必须引用P14/P15的具体trend/structural_change及原始证据”。

## C. P1：重要整改

### P1-01 P05结果可引用不存在的事实，且P08没有遵守禁止发布标志

证据：`topic_plan.json`两个topic的`source_fact_ids=[]`、`should_publish=false`，但P08最终发表两个主题；`editor.py::_clusters`只传Cxxx、主题文案与claim文本，没有向模型提供有效fact_id；`_validate_topic_plan`默默过滤无效ID并设置false，却不阻断后续编辑。

要求：选题簇输入同时提供canonical_event_id、fact_ids、claim_ids；不合法topic必须拒绝或重新生成，不是过滤后照用；P08仅可选`should_publish=true`且有合法来源链的主题，未选主题另按显式规则处理，不能偷偷忽略P05结论。

### P1-02 SiliconFlow / RightCode的主备入口混在同一个primary轮询池

证据：`llm_client.py::_load_provider_endpoints`把两者都放入`self.endpoints`；`_try_pool`读取整个列表，而`chat()`始终传`model=actual_model`，所以RightCode条目的专属`deepseek-flash`不生效，可能携带SiliconFlow模型字符串发送到RightCode；后面又单独调用`_call_fallback`，存在重复访问。README/日志大量保留OpenCode过时描述，增加判断难度。

要求：只让SF在primary池，RC单独fallback一次并使用对应base_url/model/key；测试SF成功、SF超时、RC成功、RC失败、各自模型名及调用次数，并验证没有OpenCode真实调用路径/密钥泄露。

### P1-03 23项测试缺少可复现的已提交测试文件

证据：验收报告给出`PYTHONPATH=".venv_packages:." python3 tests/test_prompt_integration.py`、23 pass；实际分支树中**没有**`tests/test_prompt_integration.py`，`tests/reader_brief/run_tests.py`还导入不存在的`tests/reader_brief/test_reader_brief.py`。因此无法对GitHub当前HEAD独立复现23项。

要求：提交真实测试源码与固定环境依赖；CI应实际运行测试并上传日志；同时补齐P14/P15完整正例、P09超时fail-closed、P05无效引用、RightCode路由、覆盖边界、prompt变量和原始证据核验测试。报告必须标明commit SHA和命令实际运行输出，不能仅写数字。

### P1-04 长报告/播客旧硬约束仍在，与新版提示词冲突

证据：`skills/generate_periodic_report/prompts.py`周02仍写“提炼3个趋势”，播客原稿仍要求各周期“最值得推进的三件事”，月/年还有固定数量风险/机会；P06/P07/P10只是给旧`SYSTEM_PROMPT_BASE/REVIEW_SYSTEM_PROMPT/PODCAST_SUMMARY_PROMPT`末尾追加新System文本，原章节user规则未彻底消除；旧长报告生成入口也没有接P14/P15结果。

要求：在实际运行路径上消除旧硬凑条数要求，保持6/7/8章必要结构但内容按证据多少决定；让P14/P15流入原长报告的P06/P07及P10；所有章节、播客与邮件事实保持一致。

### P1-05 分支过旧，不能直接合并

`compare_commits(main,feat/prompt-integration)`返回`status=diverged`、`ahead_by=2`、`behind_by=29`。需先在新的集成工作树以最新main为基线整合，核对其它E1/R3/TTS修订，重新跑全量测试；不得强制覆盖主线或生产。

## D. 内容抽样复核（W43）

1. W43确有2变化/1竞对/2动作/2观察；但`F002`和最终HTML把原文的品牌**SK-II**误写为**S-II**。原文：https://finance.sina.com.cn/tech/roll/2026-10-22/doc-iniwaezt5950286.shtml
2. 丝芙兰活动简报说“报道未披露客流/转化”，但2026-10-26来源文明确有“三天4000余名美妆爱好者”参与的信息；需区别**活动参与人数**与门店客流及交易转化：后两项不能推断。原文：https://www.36kr.com/p/4042615186789256
3. 天猫“全面取消凑单/官方立减”的来源为36氪转载的行业评论，不是天猫官方规则中心；应自然归属为“据36氪所引行业分析”，经营动作是待官方类目与商家规则核对的建议，不得升级成已验证的屈臣氏当前适用规则。原文：https://www.36kr.com/p/4038146420627074
4. 原始`W43_fact_pack.json`里多个`date`为截断非ISO字符串（如“Fri, 23 Oc”“Thu, 22 Oc”），13条的`event_ids`为`E1-W42-*`而周期是`2026-W43`。需确认W42代表源批次而非错误周归属，并补齐publication/occurred/effective不同角色，绝不能把截断的媒体日期用于日级归属。
5. `facts_pack.py`由新闻短句+URL即入包并按词推导status/source_kind，不等于逐篇原文核验或平台官方确认。HTML写“13条已核验事实”证据等级过高；应改成与实际质量门严格对应的措辞。未绑定原始source片段的主张不能由P09仅查链接存在即宣布核验。
6. HTML页脚“附本期口播音频”没有与本批次音频路径、生成状态和SHA核对；未实际附档时删去声明。
7. 有4张截图、报告声明0px溢出，只能证明报告的浏览器指标存在；还须实测目标真实邮件客户端（Gmail/Outlook等）是否支持`<style>`中的CSS变量及响应式卡片，不能用Chromium结果代表邮件客户端验收。
8. P09“上一轮7处问题成功检出”在本次提交的`p09_review.json`里没有可复查的成功轮次审稿JSON、修订前后diff及复核通过记录，应补齐不可变对照证据；不能把上一轮成功合并算本轮已审。

## E. 改造顺序与验收门

**R0：先修安全/真实交付状态**：P09 fail-closed，审稿状态唯一来源；P05无ID的topic不得发表；删除无音频时“附音频”及无法证明时“已核验”的标记。针对W43重新生成完整对应的brief/p09_review/manifest/HTML，三者状态一致。

**R1：补齐P14/P15真正的正例闭环**：修`send_at`模板变量；以冻结的5相交周+跨月事件、12月真实结构包构造可追溯模拟输入，验证封账门、原始证据链、去重、缓存失效、正反例与P08真实使用结果。fixture只能用于代码正例，不得冒充真实经营年报。

**R2：合并到最新main的隔离工作树**：修正模型双provider路由；去掉旧章节/播客凑数约束；接通原长报告与独立邮件/播客的分层数据流。

**R3：可复现验收**：提交`tests/test_prompt_integration.py`及所需测试，CI真实执行；交付W43事实纠错前后diff、P09成功审稿与超时fail-closed样本、月/年完整与缺失覆盖样本、真实邮件客户端截图、音频存在与否的一致性、所有产物匹配同一commit SHA与facts_sha；原真实账本、生产Cron、SMTP、PID1不动。

**只有R0～R3全部通过，才考虑是否发起主线合并/生产发布的单独授权。** 本审查文档不代表已直接修复任何代码。
