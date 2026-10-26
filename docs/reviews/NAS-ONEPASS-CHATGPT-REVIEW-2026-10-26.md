# 2026-10-26｜R2生产候选、R3周期质量、M2 AIHOT 一次性交付独立复审

> **裁决：三条隔离工作包已有真实代码和证据，接受为阶段性成果；R2日更正式切换 NO-GO、R3正式音频/邮件 NO-GO、M2生产拦截 NO-GO。直接授权一轮范围严格收敛的隔离补齐（D1–D5），不再每个小问题单独请示；不得自行执行有副作用的生产重启/Cron/SMTP/主分支合并。**
>
> 模型继续 SiliconFlow DeepSeek-V4.1-Flash 主、RightCode「百亿补贴」备用，费用在后台看，不另做费用台账。项目始终在NAS Docker/QwenPaw。用户每日只要数据入池、周/月/年报+中文播客+经授权的邮件，不恢复日报邮件。

## 1. 本次可验证的GitHub事实

最新三个隔离分支，均从同一代码发布基线出发：

- [release/r2-cutover-candidate](https://github.com/lovestream/watsons-retail-intel/tree/release/r2-cutover-candidate) + [R2候选报告](https://github.com/lovestream/watsons-retail-intel/blob/release/r2-cutover-candidate/docs/reviews/NAS-R2-CUTOVER-CANDIDATE-2026-10-26.md)：基于NAS本地read-only拷贝报告，项目外持久目录的cutover备份约1.1GB，business-data共7,979文件，恢复staging校验7979/7979；旧Cron/PID1/main均未变更。
- [r3/periodic-quality](https://github.com/lovestream/watsons-retail-intel/tree/r3/periodic-quality) + [R3验证报告](https://github.com/lovestream/watsons-retail-intel/blob/r3/periodic-quality/docs/reviews/NAS-R3-PERIODIC-QUALITY-2026-10-26.md)：已修改periodic_synthesis、editor_review、generate_periodic_report和report_sanitizer，W40规则兜底稿拦截，正常fixture终稿通过，SMTP为dry-run；edge-tts返回403，**音频取自旧W27而非本期文本新生成**。
- [m2/aihot-adoption](https://github.com/lovestream/watsons-retail-intel/tree/m2/aihot-adoption) + [M2验证报告](https://github.com/lovestream/watsons-retail-intel/blob/m2/aihot-adoption/docs/reviews/NAS-M2-AIHOT-ADOPTION-2026-10-26.md)：新增三态预筛annotation、source-yield audit、event_relationship、SelectBench24案例；advisory未对生产事件入池作硬拦截。真实旧池67个来源/6235个事件，但因缺verification_status，reportable=0，**不能据24/24手工样本宣称生产精度或真实漏报改善**。
- GitHub compare证实R3相对R2候选 ahead1/behind0；M2相对R2候选ahead1/behind0；**R3与M2是兄弟分支、相互diverged**，尚未经整合分支测试，不能把任何一个单独分支视作三包全量。
- 复审读取了三个报告和机器审计JSON、R2完整备份脚本、R3 Phase4/清洗/周期候选、M2分类/SelectBench/来源/关系代码与Git忽略规则。ChatGPT没有登陆NAS重放全部测试，也没有亲自验证备份盘上的字节；执行者的SHA/7979检查记录是可读取的脱敏结果。

## 2. 已认可的内容

1. R2先前遗漏.gitignore运行数据备份的问题有实质修复：新业务归档包含data/reports/podcasts/config/memory，做文件级SHA和恢复staging验证，且不覆盖现役生产；**备份仍在同一持久卷，不是灾备，另外排除的SMTP/Secret配置需在上线前单独核实可恢复**。容器内PID1 supervisor配置位于**非持久的/etc可写层**已明确查明，不应直接编辑后当作长期可恢复。
2. R3终于改为对**清洗后的终稿**做质量检查，且不把规则兜底全章、章节缺失、W40重复、证据来源不足当成正常可发。内容质量和音频/邮件质量的状态已分层，不宜为提高发信率而降低门槛。
3. M2具体参考AIHOT已固定commit ce8348477438cce2b417c1403290a511c812a7c5；已写入tri-state和来源收益审计的可用代码，不是纯设计文档。保持advisory最为稳妥，尚无高价值漏报真实对照样本不应直接更改硬过滤。

## 3. 直接确认的整改要求

### D1 P0｜R3/M2测试文件漏提交，不能独立复现

GitHub R3分支报告声称新增6项测试、M2声称新增9项；但按报告引用的 **tests/r3/test_periodic_quality.py** 与 **tests/m2/test_m2_adoption.py** 查询远端均404，GitHub compare中也没有独立的test_*.py新文件，只有R3的run_frozen_dryrun.py与M2的fixtures。原仓库.gitignore存在通配 `test_*.py`，会**匹配子目录所有同名前缀的测试文件**。需要核对NAS本地确切文件名，**将全部声称通过的测试源码提交远端**（按路径纠正.gitignore或显式git add -f，后续建议改为只忽略根目录的一次性test脚本），读取远端后用**新clone/干净worktree**执行R1+R2+R3+M2综合测试。缺源码则必须下调已通过项宣称，不允许只上传test output summary。

### D2 P0｜R3真实TTS尚不能生成，旧MP3 fixture不能通过本期音频交付门

R3 runner `tests/r3/run_frozen_dryrun.py`将`_generate_tts`直接替换成从W27复制旧MP3，并伪返回ok/duration。此为链路模拟可接受，但**不能证明本周报正文与W27音频对应**，实际edge-tts是HTTP403。生产上线前务必寻找**NAS现成或可授权的合法可用中文TTS路径**（优先诊断网络/API/软件版本，或已可用的本地TTS；不用未经批准新外部Key或绕限流），至少产生**从本期冻结口播稿生成的全新中文音频**，核对音频哈希、可播时长、所用稿件哈希、无旧文件复用；否则报告可留审核稿但`delivery_allowed=false`，不能发送带旧MP3的邮件。W40坏稿仍必须blocked。
另外runner中存在**真实收件地址硬编码到GitHub测试脚本**（请勿转述/重复该邮箱地址），应改为仅dry-run合成测试地址（如`review-test@example.invalid`）或从仅本地环境读取且不提交。检查其他新增文件中是否泄露私人联系/credential片段。

### D3 P0｜M2 SelectBench证据字段错配，24/24需修正审计真实性

`tests/m2/fixtures/selectbench.jsonl`24项用了`source_url`，`skills/m2/selectbench.py::evaluate()`导出时却读`c.get("evidence_url")`，导致返回每项`evidence_url=null`。其中部分样本的URL只指平台主页，并不能直接证明所编写的事件；24例是**人工短样本/用于逻辑回归，不是24条均已核实事实**。

补映射与完整性校验，禁止证据字段为null仍输出“所有案例可追溯”。对首页与无法访问/无法证明案例加`fixture_synthetic`/`evidence_unverified`标注，真实来源样本需具体报道/公告原文URL与人工标签，**指标按真实有效证据子集与合成回归子集分开报告**。扩大至≥40**多来源**标注数据且补PASS被误BLOCK、跨平台联动、公告新旧时效、海外品牌中国市场正例；需有pre/post误报、漏报，不因手写24/24就safe_to_enable。当前M2仍advisory。

### D4 P1｜综合发布候选必须兼容R3与M2，恢复旧池不能假造verified

R3严格核心候选，M2看到旧池6235事件的reportable0说明当前未切换的生产旧数据与新事实规则不兼容。严禁为了让周报有素材而批量将旧事件自动标verified。可在**隔离回放/已备份快照**中从原始证据重跑核验，让缺证据的留review/reference；用同一真实闭合窗口把R2日报入池→R3周报候选→M2预筛annotation贯通，无正式账本写入。保留更新事件/高价值背景按可验证标签引用，不可把原始P1当新事实。

R3和M2从R2发布候选分叉，应建一个**全新集成分支**，在其上合入两条分支的**代码和测试**，解决同一readiness.md的潜在冲突，不要直接merge main或覆盖脏生产checkout；综合验证后交出唯一候选HEAD、文件级变更和回滚方法。

### D5 P0｜R2实际生产切换依旧没有授权/持久化控制面仍阻断

R2备份与本地supervisor模拟通过，但**/etc配置不持久**；没有验证Docker重建后控制socket、program仍可用，也没有经授权停原Agent daily Cron或真实发送critical邮件。建议只做一个**安全、经宿主配置验证的配置持久化方案**，禁止直接向非持久/etc写生产配置后宣称自动运行；先独立测试完整stop/restart/rollback，在未获用户批准前不得重启PID1、启停任何Cron或动生产账本。即使R3/M2整改完成，daily正式切换依旧单独批准，periodic旧实现与R3真实发送也另行批准。

## 4. 下一轮唯一安全工作指令（已授权可连续完成）

请DeepSeek/QwenPaw**直接在隔离环境一次完成D1–D4和D5配置设计/模拟，不要每修一个测试就回来问**：

- 补齐R3 6项、M2 9项真实测试源码的远端提交，并修.gitignore忽略模式；从全新clone综合运行，不能只依据NAS本地脏worktree。
- R3实际解决TTS生成（或严谨报告仍不可用并保持delivery blocked），不允许换旧播客文件伪造本期音频；消除新增测试里硬编码私人收件信息。
- M2修SelectBench证据映射与URL强度；至少将模拟/有来源/已人工核对区分，并持续仅annotation，不启用自动BLOCK。
- 构建一个R2+R3+M2整合候选，做同窗端到端隔离回放，给出完整测试结果及通过/不通过、此时生产旧池如何处理的无损计划。
- 校验R2目前NAS同卷备份的SHA/恢复证据，但不重复拷1.1GB只为汇报；提交supervisor容器重建持久化部署/回滚**草案和必要宿主核验项**，不执行生产重启、Cron启停。
- 交付`docs/reviews/NAS-ONEPASS-FINAL-INTEGRATION-2026-10-26.md`，逐项答复D1–D5，列精确GitHub HEAD、测试文件及用例数、真实新MP3证据或明确阻断、SelectBench证据核验、集成状态和**仅剩需要用户确认的生产动作**。更新README阶段状态，完成后停下供ChatGPT总体审查；**不要求对已授权安全开发再请示**。

**本复审不批准合并main、覆盖现役生产checkout、Docker/PID1重启、启停旧cron、新调度/SMTP/告警正式发送、新第三方付费Key接入或正式历史补跑。**
