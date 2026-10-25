# NAS R1-FIX｜ChatGPT 独立复审及下一阶段指令

日期：2026-10-25；仓库：lovestream/watsons-retail-intel。

## 决定

**R1-FIX 按已授权范围通过。允许开始 R2 的隔离日管线影子测试、可靠调度与独立健康检查开发，但未经另行验收，不得改变正式 Cron/容器/事件账本/SMTP/周报发送。R1 分支不直接合并 main。**

**用户最新明确决定（高于此前审查要求）**：不建设本系统的模型调用金额台账、日金额预算、模型单价估算/对账程序。费用与免费额度直接在 SiliconFlow 和 RightCode 后台查看。Python 程序仅保留运行诊断必要的请求次数、重试数、调用来源、切换原因、失败类型、基本 tokens/usage（若原接口自然返回），不为结算重复建设统计系统。**不能继续把旧 F1“费用统计/日金额预算”作为合并门槛。**

模型始终为 **SiliconFlow（硅基流动）DeepSeek-V4.1-Flash 主用，RightCode「百亿补贴」DeepSeek Flash 备用**；主通道正常时不使用备用。不按峰谷时段切换。SiliconFlow 是否仍有免费额度/是否开始扣费，由用户直接从后台确认；Python 不推断HTTP200就是免费。

## GitHub 核验范围

- 隔离分支：r1/pipeline-deepseek-flash-recovery。
- 本轮 HEAD：b58dea6dc9dd46de57147c7c297bad589e0a3846。
- 修改代码与测试提交：beb4e9e475f994358ede115e31249f895b4463e4；报告更新提交：b58dea6dc9dd46de57147c7c297bad589e0a3846。
- 查阅了分支的 llm_pipeline_adapter.py、llm_client.py、llm_providers.yaml、filter_relevant_articles.py、extract_events.py、R1原报告、测试用例、负例 JSON、运行脚本。分支相对main已发生分叉（main后续新增了审查文档），**未来不能直接假设快进合并**。
- QwenPaw报告 38 tests OK（34 adapter/policy +4 fact verification）及20个模拟样本；GitHub存在对应测试与产物。ChatGPT**没有访问NAS重跑这38个测试，也没有重新调用付费API**，因此判断为“代码检查+报告证据支持通过隔离阶段”，不等于生产端到端已验收。

## 逐项审查

| 项 | 判定 | 核实结论 |
|---|---|---|
| F1 费用台账/日金额预算 | **用户取消该需求，不再作为阻塞** | 没新增费用台账。get_paid_fallback_calls/status不再固定返回零，health用的 fallback_calls/fallback_success、usage仍有计数。set_max_paid_fallback_calls现可选设置，默认0不启用额外上限；不为它增加计费工程。 |
| F2 主备路由 | **通过隔离逻辑审查** | 主/备/可用/降级状态分离；黑名单/白名单；跨Provider模型ID映射。单测覆盖主故障→备接管、主正常不触发备用、错误抑制等。白名单允许连接、配额、不可用及必要格式失败；不能扩大为所有错误均走备用。 |
| F3 凭证 | **通过隔离逻辑审查** | 文件0400/0600、常规文件、排除符号链接和允许目录约束；敏感key_used返回渠道标签而非密钥片段。密钥仍不进Git。生产容器的实际Secret挂载与权限仍需R2确认。 |
| F4 事实核验负例 | **测试层通过，生产层未完成** | R1S07“听说”与R1S13日期冲突在测试里标needs_verification；新测试不再使用fixture日期覆盖抽取日期。但assess_verification()目前主要位于 tests/r1/run_20_samples.py，被run_20_real.py和模拟器使用，**尚不能宣称正式事件入池管线已自动执行此核验或阻止不确定事件晋升**。这属于R2端到端影子测试的必查项及后续日期/来源质量门改造。 |
| F5 故障/并发 | **通过隔离逻辑审查** | 错误与并发边界测试在GitHub可读，HTTP总额度使用同锁原子预留。旧OpenCode与昂贵GPT不可被自动调用。38 tests OK系执行者报告，需R2 shadow复核版本一致。 |

### 保留的非阻塞说明

- fallback_attempts_per_task=1 是**尝试一个备用Provider**的限制，不是“RightCode最多只能发一个HTTP请求”：该Provider内部可能进行有限的429/5xx重试。保留最小次数/错误日志即可，不需金额台账。
- Python的fallback使用无需用户每次确认，但如果主通道免费额度用尽后仍返回成功并正常计费，API程序无法自动判断免费券；按用户要求通过后台人工查看，不设计额外额度查询。
- 事实样本20/20只是结构化抽取与分析通路成功，不等于人工事实核验20/20。R1不解决整个来源覆盖/发布时间核验/舆情真假；属于R2影子测试+后续M2采集质量优化。
- 原生产 shell 和Agent调度长期存在“假成功”、异常不告警及周期稿质检漏网等缺陷，本轮R1-FIX没改它们；**生产仍未恢复**。

## 下一轮：R2-A 隔离影子链路＋确定性调度/看门狗整改方案

为了不破坏生产，将R2先分“隔离验证”与“正式切换审批”两部分。下一轮**仅执行R2-A**：

1. 基于最新origin/main核对现有r1分支和生产dirty工作树，继续隔离工作树，不做reset --hard、clean或force push。R1改动不擅自合并main；用R1分支代码继续模拟/隔离运行。
2. 使用**已冻结的、非敏感/可追溯文章或一天闭合的复制数据**进行与实际生产一致的 extract→analyze→score 日管线影子验证。目标是检查各skill调用契约（含老 model_router.yaml 等遗留配置）、事件ID/日期/证据/hard downgrades/入池行为，且不写正式账本。新增必要断言，尤其明确 needs_verification 在生产链路能否落入事件、能否降级；如尚未接入，只报告最小修复建议，不得称日期门已修好。
3. 只读取现有NAS Cron和实际 shell 启动关系，在独立目录建立**确定性启动器/健康探针的草案与故障注入测试**：实际exit code、running/finished manifest、无信号日可区分成功、关键步骤失败标failed、48小时无正常manifest能提醒。对照过去“Agent口头success但0 tool_use”场景证明不误报成功。**暂不安装/启用正式任务或健康告警**。
4. 不发日报、周报、MP3或邮件；不停止/改动现有QwenPaw agent cron；不补跑全部断档；不重建Docker或触碰正式数据卷、凭证。若影子需要真实AI请求，先限定少量且只用现有已授权通道，保持必要错误/请求计数，不做金钱统计。
5. 输出docs/reviews/NAS-R2A-SHADOW-AND-SCHEDULER-TEST.md，含真实执行命令、shadow产物相对路径、各阶段成功/失败/数据验收、日志与测试结论、故障注入、最小变更文件及如何回滚；单独分支commit并停止，等ChatGPT复审。

**特别说明**：此次没有批准R2-B正式调度切换，没有批准R1代码合并main，没有批准R3真实邮件投递，没有批准M2 AIHOT大范围重构。完成R2-A报告后立即停止。

