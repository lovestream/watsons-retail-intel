> **后续决策（2026-10-25）**：用户已明确取消F1费用台账/日金额预算要求；R1-FIX的F2–F5整改已通过审查，允许进入R2-A隔离验证。**本篇的“F1须整改”“R1-FIX阻塞”是此前历史要求，不再适用。** 请以 [R1-FIX最终审查](NAS-R1-FIX-CHATGPT-REVIEW-2026-10-25.md) 为准。


# NAS R1｜ChatGPT 独立复审与 R1-FIX 指令（2026-10-25）

> **阶段判定：隔离 R1 连接与基本调用验证通过；正式合并和生产启用暂缓。下一步只批准 R1-FIX。**
>
> **最终模型顺序：SiliconFlow（硅基流动）DeepSeek-V4.1-Flash 主通道；RightCode「百亿补贴」DeepSeek Flash 备用。** 用户希望优先使用 SiliconFlow 已有免费/赠送额度；不按峰谷分时运行，不再将 RightCode 设为正常默认。只有符合故障、额度和预算条件才触发备用。系统始终在 NAS Docker，不迁移，不使用 Codex。
>
> 已读取远端 R1 独立分支 r1/pipeline-deepseek-flash-recovery 中的 llm_pipeline_adapter.py、llm_client.py、llm_providers.yaml、R1 报告、16 条单测、20 条样本与最终真实调用汇总。main 与该分支比较：ahead=2、behind=0，变更11个文件。执行者提供真实请求与测试记录，但 ChatGPT 没有在 NAS 中重放付费API请求。

## 一、已经通过的部分

- 配置主 siliconflow、备 rightcode-bybt，禁用旧 OpenCode 与昂贵 GPT 自动兜底。R1 最终汇总记录 40 次模型HTTP请求全部走 SiliconFlow，fallback_calls=0，40次无重试；20次JSON抽取和20次分析均返回，16项离线单测报告通过。
- 主备的独立小规模直连探针有记录；RightCode 在启用 enable_thinking=false 时返回 HTTP200 但内容空，因此该参数仅应用于 SiliconFlow。
- 调用封装含状态码分类、400 fail-fast、429/5xx有限重试、过程级请求上限、逐通道tokens统计；正式生产main、cron、账本和邮件并未在本分支被修改。
- **上述仅证明连接/结构化可用，并不能证明20条真实新闻的时间、证据与商业结论已经全部正确。**

## 二、必须整改的阻塞问题

### F1 P0｜RightCode备用计费接口是假实现，生产不可合并

adapter的 get_paid_fallback_calls() 恒返回 0，set_max_paid_fallback_calls() 是空实现，get_status()里 paid_fallback_allowed=False、paid_fallback_calls=0 是固定值，**与 allow_fallback=true 和真实可自动扣费的备用行为矛盾**。原有下游若据此限制昂贵调用会失效。需正确维护备用调用/成功/失败次数与tokens，恢复兼容的次数上限，并有明确预算/熔断规则；主通道成功时不得扣备用。过程级每进程200请求上限不能替代跨进程、跨重启的每日备用付费预算。首次正式启用备用前必须明示可审计的默认总额度/预算；超限不自动扣费。

### F2 P0｜可用性、模型ID与自动回退策略不一致

- available 只返回主通道状态；主通道因配额/认证错误失效而RightCode仍可用时会返回False。至少分开报告 primary_available、fallback_available、any_usable、degraded_using_paid_fallback，健康检查不能把付费备用运行当完全正常。
- _resolve_model() 收到“另一provider的具体模型ID”时可能把该ID原样发送到当前provider；应只允许本provider ID，或把已知跨provider ID映射为当前provider的模型名，并添加单测。
- 当前 allow_fallback=true 会在模型400、认证失败、配额、429、5xx、格式问题等广泛场景考虑付费备用。需要明确的可回退白名单、每任务及每日额度和告警。正常SiliconFlow→不调用RightCode；只有确实不可用/额度用尽、且预算许可→RightCode；双路失败→失败并告警。不能静默转昂贵GPT。
- 现有语义“已配置RightCode”的确是备用授权，但**不是无限预算授权**。

### F3 P0｜所谓0600凭证文件缺权限检查

CredentialRef.resolve() 直接打开密钥文件，未检查实际 POSIX mode / symlink 情况，因此尚未做到“只读取0600文件”。应拒绝组/其他用户可读写的密钥文件（并检查符号链接与目录边界），补安全单测。返回值 key_used 目前含密钥前后字符的掩码；改成只返回provider/credential_channel，不含Key真实字节。环境变量读取保持兼容，不需用户重新在QwenPaw注册模型。

### F4 P1｜20条模型成功不等于20条事实校验通过

R1 真实测试 run_20_real.py **在抽取后把事件 published_at 强制覆盖为 fixture.published_at**。例如 R1S13 的fixture时间为2026-09-26，正文却以“财联社9月13日讯”开篇，不能宣称模型已经修正或核实日期。R1S07 报道原文是“听说得物男士护肤环比增长150%-200%”，模型事件则转述为确切的增长事实，忽略了证据只是转述线索的限制。请对日期冲突、来源传闻新增 needs_verification/uncertain 断言，严禁绕过时效/可信度门。20/20 pairing是数字或标题双字命中，仅确定性规则匹配，不是人工语义事实审校；P1=5也不是经过人工确认的5条高价值事实。

### F5 P1｜全链路与预算边界仍未验证

- R1没有进行“主通道真实故障→自动调用RightCode”的最终20样本测试。补模拟故障注入：主永久失败、免费额度耗尽、429、5xx、无凭证、RightCode也失败、预算耗尽。
- 当前过程级HTTP上限的检查与递增不在同一锁里，多线程可略微超限；需原子预占。
- 请把“独立API/JSON结构化验证”与“正式采集—日期核验—去重—入池—评分真实流程验证”分开。禁止把单日影子采集擅自取代原定R2的定时调度/看门狗任务。
- SiliconFlow免费券/剩余额度/有效期需要由用户账户或授权的NAS工具实查。**成功HTTP200不等于免费计费**。SiIiconFlow正式单价及模型收费政策可能变，实际账单为准。

## 三、批准的唯一后续工作：R1-FIX，仍隔离，不能合并生产

请 QwenPaw 在现有 r1/pipeline-deepseek-flash-recovery 分支上小步修改，不改正式main，不操作生产cron/账本/SMTP/密钥/容器。

交付门槛：
1. 真实备用调用计数、付费上限和健康状态；主成功时RightCode=0，主失效→备用成功/备用失败/额度耗尽的模拟测试全部通过，不能绕过付费预算。
2. 0600权限测试、无秘钥字节输出、跨provider模型ID不串用，并发限额原子性测试通过。
3. R1S07/R1S13负例得到相应不确定状态；不再把发布日期强制覆盖当作“已核验”。
4. 附加必要单测，尽量使用模拟器，**不要重新进行几十上百次真实付费测试**；报告明确未测项，不能声称生产已经恢复。
5. 在该隔离分支提交代码、测试、R1报告修订和一份 docs/reviews/NAS-R1-FIX-VERIFICATION.md，回报准确SHA与变更文件、主/备用次数及证据；完成后立即停止，等待ChatGPT复审。

当前状态：R0已通过；R1隔离基础验收合格但R1-FIX阻塞正式合并；R2/R3/M2–M7保持暂停。
