# NAS M1｜ChatGPT 独立复审与 P0 故障恢复决策（2026-10-25）

> **结论：M1 阶段交付通过，生产健康验收未通过。** 因出现长时间采集中断、模型失效与报告质检假阳性，先插入受控 P0 恢复阶段 R0–R3，原 M2 AIHOT 深改后移。不迁移NAS、不用Codex、不恢复Muse。本文件是批准的下一轮实施指导，不代表已经修复生产环境。
>
> 依据：docs/reviews/NAS-M1-READONLY-AUDIT.md，初始提交 2464df94e7ceb7b327eb912fd4fdeeb77ba22f11；独立核验 GitHub 的 model_router/scoring 配置、daily/periodic 启动器及主流程代码。
>
> 用户运营决定：NAS里已经配置模型，最终倾向统一选 RightCode「百亿补贴」DeepSeek Flash（未缓存输入¥0.60/百万token，输出¥1.80/百万token，缓存命中输入¥0.02/百万token），不按峰谷调整。**QwenPaw Agent 有模型不等于 Python Pipeline 已经使用该模型。** 暂不重新录入或更换用户的Key/Provider；如必要，先诊断出最小连接缺口后征求批准。

## 一、独立核验与重要限制

- GitHub commit 2464df9 确实只新增 docs/reviews/NAS-M1-READONLY-AUDIT.md。生产代码、配置和账本不在此提交内。QwenPaw自述中途 reset --soft 后有坏提交、已恢复；从最终提交范围不能推导出它在NAS容器中的所有Git操作均没有影响。
- **代码直接证据**：config/model_router.yaml 的 default / fallback 同为 OpenCode deepseek-v4-flash-free；config/scoring.yaml 有 rule_fallback_max=P2，其他事实/证据硬降级；scripts/run_daily.sh 使用 --end-step analyze、--continue-on-error true 和 --send-email false；run_periodic.sh 没将传入的日期参数转发给Python，而Python支持 --date。
- **NAS二手证据**：过去日志、reports、jobs_history、delivery ledger的实际数据没有从NAS同步供ChatGPT直接读取。我审阅的是QwenPaw提供的审计明细，并非宣称自己已经进入NAS核对过每个文件。M1列出原始路径/键名及分母，足以支持启动安全诊断，不能把所有判断写成独立现场实测。

| 重点 | 校准后的事实 |
|---|---|
| 抽取失败 | 2026-09-26至10-11共16天逐日数相加，**extract 0成功/275次失败**。报告没有同等精度列出analyze逐日统计，需R0另行核验。不能直接扩展为所有模型、所有日期均100%失败。 |
| 断档 | M1记录2026-10-12至10-24共13天没有日事件产物，10-25 agent cron显示成功但无日采集日志，需核对触发时间、运行环境和日志目标，再断言根因。 |
| 评分 | rule_fallback 被封顶P2是有意质量门。**绝不能为制造P0/P1而放宽硬降级**；恢复真实抽取后P0/P1仍可能合法为0。 |
| 邮件 | M1中的2026-08-10 W32是**本地投递台账的最后成功记录**，非本人独立核验收件箱已经收到。后续十期周报及两期月报在审计内缺乏送达链。 |
| W40 | M1称仅1284字节、内容重复却sendable=True，需R0从NAS复现审稿/音频/真正投递三个独立状态，防止废稿发送。 |
| 来源 | cleaned约4–7%不等于可直接计算的召回率/漏报率；过高repeated也不证明事件匹配“准确”。AIHOT对标时必须有人工真值样本。 |

**安全补充**：M1记录工作树含正式账本、delivery台账和未追踪产物；严禁 git reset --hard、git clean -fd、盲目 git pull、覆盖数据卷。需确认真正Docker持久化挂载后在独立测试目录/分支做变更。公开M1报告中的具体收件邮箱已经另作脱敏提交；但旧Git历史与现有源配置仍可能包含该地址，不能声称已彻底脱敏。

## 二、为什么现在先不进入完整 M2

现有故障包括：调度层“Agent回复成功、实际Python可能没启动”，Python层LLM连续失败，报告层空洞重复稿竟能通过内容标记，告警与真实投递长期缺失。此时做AIHOT独立双评分、换技术栈或大量清理采集源，会在不可靠运行基础上制造更多变量。

**决定插入R0–R3最小恢复路径，只修已证实会阻断正确运行的能力，暂不重写29步管线。**

### R0｜下一轮唯一批准：P0定位与隔离测试（不更改生产）

请现有 NAS Docker 中的 QwenPaw + 当前用户已经选择的 DeepSeek Flash 执行：

1. 在NAS现场逐个核对10-11、10-12、10-25的调度事件、jobs_history、实际Shell/Python启动、运行日志、manifest和事件产物，画出**计划→Agent→exec→Python→各步骤终态→持久化数据**证据链。区别脚本没启动、日志路径错位、Python异常和当天确实无新信号；检查setsid、timeout、cwd和权限。
2. **分开统计extract和analyze的真实调用数**、成功数及错误分类（权限/网络/429/超时/JSON格式/其他），给每种失败一例脱敏日志。核对QwenPaw当前Agent选用的RightCode模型与skills/utils/llm_client.py实际使用的旧OpenCode入口是否完全独立。**只能只读查已有模型配置**；不能擅改Key、base_url、Provider、模型选择。
3. 在**独立隔离测试目录**用一条不含任何内部/个人资料的固定短文本试测Python LLM客户端，检查JSON结构、真实返回的模型ID、耗时、usage、失败类型和费用。若需要最小管线适配才能调用用户已配置的模型，只报告所需最小代码diff与凭证读取方式，**不得本轮修改生产连接**。
4. 读取NAS的W40废稿、审稿质量门、sendable字段定义、音频门和投递门，说明为何重复句子可标sendable；至少给出脱敏的误判复现和独立的content_quality_pass、audio_ready、delivery_allowed三个指标。复核daily的--continue-on-error true和periodic的日期参数行为及错误传播，不在本轮修改。
5. 在NAS宿主核对Docker实际bind mounts/volumes、存储持久性、生产工作树dirty状态、回滚可行性。列出缺失13天历史数据能合法核验/补采的范围，不能补的写coverage_gap。**不要批量补跑，不发邮件，不调整生产cron，不重启容器，不清理脏文件**。
6. 输出 docs/reviews/NAS-R0-P0-ROOT-CAUSE-AND-RECOVERY-PLAN.md，包含根因矩阵、P0最小修复文件清单、分层验收阈值、失败复现和回滚方案，附真实日志文件名与脱敏数字。提交后立即停止，供ChatGPT再次独立复审。

R0即使仅一项API探针成功也**不能**宣称整条管线已经恢复。若无法安全隔离，跳过探针只给诊断方案。

### R1｜待批准：恢复Python事件抽取与分析链路

- 先复用已配置模型的合法受限接口；若 Python 无法复用 QwenPaw Agent 的模型连接，仅在用户明确批准后做最小客户端适配，密钥仍仅保存在NAS Secret/已有安全存储中，不打印/推Git。
- 使用至少20条冻结真实/脱敏样本在独立测试环境证明结构化JSON、字段完整性、日期证据、错误/重试控制、费用可见、extract→analyze→score可连通，保留rule_fallback与其他硬降级。成功标准不是一个HTTP 200。
- 不自动补跑13天、不改正式调度/SMTP/账本。

### R2｜待批准：确定性调度与独立健康监控

- 拟用NAS宿主任务调度或经过验证的容器级调度**直接启动Python**，QwenPaw Agent只作交互运维。切换前影子验证、旧新不得双跑；正式启停须用户批准。
- 完成状态由真实退出码+关键manifest+JSON内容校验+数据健康时间戳计算；Agent说“已启动”不算成功。单独健康监视：24–48小时无完整日事件池即告警；无新新闻也必须有完整健康manifest。
- 测试正常/401/429/源为空/关键步骤失败/重复触发/容器重启，且错误不能被--continue-on-error假成功掩盖。

### R3｜待批准：周期内容质检与音频/投递恢复

- 冻结证据包生成周/月/年报；重复空洞的W40式废稿必须拦截。内容审稿、音频准备与最终可投递状态独立。
- 生成报告与MP3在NAS隔离目录 dry-run，验证来源真实性、重复度、空章节、播客真实可播放、报表周期和邮件幂等；未得到新的用户授权前**不发送真实邮件**。
- 音频缺失时允许不允许“仅HTML发送”、缺口期是否补发都由用户另外决定，绝不能默认群发历史报告。

## 三、下一步判定

**M1结束，R0立即成为唯一待执行阶段；原M2～M7全部暂停排队。** R0提交后ChatGPT复审，才决定R1、R2、R3实施许可。R3验证通过再恢复AIHOT对标与采集源优化的M2；R0/R1可先保存小型固定回归样本。

我们要先重新拥有：**确实启动的每天采集、确实有效的模型事件、能拒绝垃圾报告的审稿、能报告断档的告警**。在此之前不要追求P1数量、新闻抓取量或所谓“100%召回”。

