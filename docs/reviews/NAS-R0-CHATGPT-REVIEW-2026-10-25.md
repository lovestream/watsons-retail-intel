# NAS R0｜ChatGPT 独立复审与 R1 隔离实施许可（2026-10-25）

> **裁决：R0通过，根因已基本闭环；NAS生产尚未恢复。** 允许 QwenPaw 在既有 NAS Docker 中执行**隔离R1：Python Pipeline DeepSeek Flash 模型连接与质量恢复**，不得改正式cron、正式模型凭证/注册表、生产账本、SMTP或Docker部署；完成提交后即停，供ChatGPT复审。原R2/R3/M2–M7继续暂缓。
>
> R0报告：[NAS-R0-P0-ROOT-CAUSE-AND-RECOVERY-PLAN.md](NAS-R0-P0-ROOT-CAUSE-AND-RECOVERY-PLAN.md)，单文件提交 [4506183](https://github.com/lovestream/watsons-retail-intel/commit/4506183dced11e5623248d496ca433cdc77845ca)。GitHub可验证此提交**仅新增报告**，不能据此宣称NAS全部只读过程已由ChatGPT现场检查。

## 一、通过项和证据等级

1. **调度失败及假成功**：R0从NAS cron history、trace与日志定位10/12–10/24模型级失败和10/25“只有自然语言已启动、没有任何工具调用”。这些是NAS由QwenPaw读取的证据；ChatGPT尚未直接访问NAS原始trace。R2须在OS级调度器及独立看门狗层彻底解决。
2. **Python模型连接已经失效**：16个有记录的日期 extract 0/275，analyze 0/26；隔离探针对旧 OpenCode 四入口4/4返回HTTP400。GitHub代码直接证实Python配置仍用OpenCode deepseek-v4-flash-free，且同模型自备份。**此处不是改评分阈值的问题**。
3. **用户现有RightCode选择未传递给Python**：QwenPaw Agent/provider注册表和Python llm_client.py是两个独立调用层。既有 llm_client.py 有一条默认禁用的right.codes高价GPT备用实现，不是用户选定的DeepSeek Flash百亿补贴入口。确认需补**Python应用层适配**，但不是要求用户重新购买、配置一个模型或重新填密钥。
4. **W40假阳性及告警缺位**：同一批内容在清洗前后质检结果相反；R0有NAS隔离复现。生产质量门需最终落盘/发送前执行，Agent成功不等于Shell成功。R3、R2分别修复。
5. **边界清楚**：报告承认宿主Docker inspect、RightCode Python探针尚未验证；错误条目有根因和后续验证动作。没有将无模型成功的规则兜底当作正常LLM加工。

## 二、两项必须修正的统计/源码归因

**校正A：14,841不能写成已核实的独立HTTP请求数。** R0说由日志中客户端 `available_keys=... calls=...` 累加。GitHub `skills/utils/llm_client.py`的`get_status()`返回实例当前累计的`total_calls`，而不是一次调用的增量。若日志多次输出同一实例的累计快照，直接求和就会重复计算。R1必须通过**真实单请求计数/单进程末尾总计、唯一request_id和响应usage**重新确定：应用任务次数、尝试次数、实际HTTP请求、重试次数、错误文本出现次数、输入输出tokens与费用分别统计。30,724次 `Model is unavailable` 是日志文本出现数，**也不是30,724次HTTP请求**。此前14,841仅保留“待重算的日志累计统计”。

**校正B：run_daily.sh没有直接丢弃自己内部的stderr。** GitHub源码明确把`{ ... }`的标准输出与stderr重定向追加到`data/logs/<date>/_run_daily.out`；由于`set -e`，内部命令失败可能在打印“done”前退出。R0的“stderr丢弃”应精确限定在**外层Agent用setsid启动时可能重定向到/dev/null的启动证据/错误**，以及Shell缺独立完成状态/退出码回读的问题，而不应笼统说shell脚本把内部stderr都扔掉。R2按层修复并用失败注入验证。

**校正C：不能把已证实失效的OpenCode设成“备用通道”。** R0建议`fallback=OpenCode`不合格；备用只能是**经过真实探针验证、独立且已授权计费**的可用入口。若无合格备用，主路不可用时应明确失败/降级、停止无意义重试，并报警，不能回退到永久HTTP400入口，更不能自动改走旧`gpt-5.4-xhigh`高价通道。

**校正D：MEMORY.md“记忆更新”与本次远端提交分开核对。** GitHub可验证的提交4506183只含R0报告；如果QwenPaw改的是本地工作区记忆，不能称本次GitHub已提交MEMORY变更。下一步不应为同步记忆误覆盖生产脏工作树。

## 三、R1 最小任务：仅隔离开发与测试，不切生产

### R1-1 明确模型与凭证边界

- **目标通道**：用户已选RightCode「百亿补贴」DeepSeek Flash，按人民币固定费率；不做分时切换、不重新选型、不自动启用昂贵GPT备用。
- 在NAS读取Python llm_client、model_router和现有QwenPaw provider注册表**元数据**，明确怎样在**符合已有权限/最小特权**情况下让Python读到已配置的DeepSeek Flash凭证。**不要假设Agent中能用的密钥天然可以给Python使用**；不得强行解析未稳定承诺的私有注册表格式，不复制Key到源码、日志、Git、公开报告或新明文配置。若需要用户补充对Python进程的Secret读取授权，只陈述准确的最小阻塞点并停止该步骤。
- 做provider无关的客户端适配（安全读取已存在凭证的引用、model_id映射、基础JSON/非思考模式、超时及429/5xx有限重试、400永久错误fail-fast）。维持原`chat()`调用契约兼容；避免遍历4个已失效OpenCode入口造成请求风暴。旧OpenCode标记为disabled，不得当作fallback。
- 只在**隔离git分支/工作树、隔离测试输出路径**修改代码和配置草案。用户原NAS的QwenPaw服务、Provider注册表、生产Secret、生产cron、正式账本及SMTP全部保持不变。

### R1-2 先小样本探针，再完整链路样本

- 第一关：用不含个人或内部信息的固定中文短文本，执行1次**实际Python适配器**调用，记录HTTP状态类别、真实model_id、非思考JSON、usage、延迟、计费渠道（敏感内容脱敏）。如果连接失败就停下，不能继续大规模试验。
- 第二关：在冻结20条真实但已脱敏、来源可核实的样本里测结构化事实抽取/时间字段/证据链接、再测analyze和score串联。要求至少**19/20结构化解析通过**；全部被接收的事件必须满足必填证据字段和日期核验；明确质量错误，不能只测HTTP200。
- 第三关：模拟HTTP400永久错误、429可重试、超时、空content、非法JSON、凭证缺失、重复执行。检查fail-fast、次数限制、熔断、记录trace与费用；`total_failures`正确自增，调用统计区分真实HTTP请求数/重试/usage/重复日志文本。计费探针有明确少量调用预算；如无法从实际usage估价，就设严格调用次数上限并明确“金额未核实”，不无限重试。
- 第四关：`extract→analyze→score`在**隔离事件池**输出有效数据，并复算config/scoring.yaml的八维加权和硬降级；完全失败时产物状态`degraded/failed`，不能标为LLM成功；LLM无成功不等于故意修改P0/P1门槛。
- 保留最小兼容性变更和回滚diff；**不得**操作真实日报调度、正式历史账本、无信号播客或周报邮件（这些分别由R2/R3管）。

### R1-3 验收交付和停止条件

- 应交付：Python适配器与必要配置草案（开发分支/隔离工作树）、可重复的测试脚本、脱敏运行日志/usage汇总、20条样本准确性汇总、请求/失败/费用计数校验、回滚说明。
- 写`docs/reviews/NAS-R1-PIPELINE-LLM-RECOVERY-TEST.md`（不提交Key、私密原文、付费账户信息）；单阶段commit，在隔离Git分支推送并告知GitHub准确分支/commit。若工作树不干净，不能`git reset --hard`、`git clean -fd`或`git push --force`。
- **本轮不允许把R1改动合入生产main或切换生产Provider/Key/cron/SMTP**；影子测试无论成功失败，提交后立即停止等待ChatGPT独立审查。若现有权限不支持安全获得RightCode调用凭证，允许按实际情况只完成代码/模拟器测试并报告阻塞，不得假称模型链路已经恢复。

## 四、后续排期

**当前：R0已结案，下一步R1隔离开发测试；R2（可靠调度与独立告警）、R3（最终交付稿质量与音频/发送门）和M2–M7仍未批准。** 不补跑9月/10月缺口，不补发历史邮件，不拆掉当前NAS Docker QwenPaw，不重启正式容器。每阶段经ChatGPT审查结果决定下一阶段。

