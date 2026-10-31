# PR #3 决策记录｜选择B：P09 bounded review，不降低为advisory（2026-10-31）

## 决策与原因

**采用B。** P09仍是正式发送硬门，但审稿权力收窄为**可以具体定位的事实/归因/来源/时间/金额/状态/经营能力错误**。语气、同义词、可选择的更细限定不是硬门。限首轮审稿 + 一次定点更正后的复核，**至多两次P09模型调用**；后续任何模型再改文稿均为禁止。A（完全advisory）不可接受：字段/日期/数字规则并不能发现全部跨主体因果推断。

原始 W43 10轮实验在六批中最好仅2/10；不能宣称新版修复后已达到9/10。这里所有代码仍在PR #3 Draft，未在NAS重跑、未合并至PR #2/main、未动Cron、未发送SMTP。

## 审稿范围

| 类别 | 样例 | 操作 |
|---|---|---|
| material | 报道谈「拟合作」却写已收购；不同品牌的GMV并到一起；文章发表日当事件日；啤酒案例无条件外推到屈臣氏；虚构屈臣氏授权与库存 | 提供稿内原句、合法claim_id、原始URL、事实包内原文摘录、具体矛盾；block或定点更正 |
| minor/advisory | 文章未用「名单」一词但不影响主体与数量；已经知道的日期要求加“2026”作无意义重复；单纯措辞、简洁度 | 不阻断、不二次修订、不升级 |
| ambiguous | 审稿模型说“重大错误”，但找不到稿内句子、合法来源或对应claim | `hold_for_review`，不得误放行，也不能无休止要求更细 |
| P09不可用 | 超时、不是合法JSON、校验器发现决定/问题互相矛盾 | `hold_for_review`，绝不默认通过 |
| 原程序门错误 | claim↔fact↔URL绑定、数字、时间、元数据等违背确定性门 | `blocked`，无需浪费P09调用 |

## 本次已经提交的代码

- `docs/prompts/09_reader_claim_checker.md` 明确真正可证伪的10类material+非实质advisory，要求结构化的稿内原文、来源摘录、claim ID，禁止逐词苛求。
- `skills/reader_brief/issue_policy.py` 负责issue分类：模型只有severity=major或decision=block不能单独成为硬门；必须验证稿内被批句存在、原事实包claim/url/证据引文匹配，并核对其所属卡片；若重大问题缺证则hold，不能自动放行。
- `skills/reader_brief/p09_reviewer.py` 强制最多两轮，第一轮只有minor可直接approve，即使模型误选block/revise；已验证重大问题最多一次replacement并二次审核；二次仍material则block、缺证则hold。记录首/次轮结果。
- `docs/prompts/08_reader_brief_editor.md` 明确无最低字数，10条事实可以写300–600字或更短，不得为了700–1200字填充断言。
- `config/prompt_registry.yaml` 在候选分支把P08采样温度降为0.1、P09降为0，减少不必要漂移（**温度低不等于确定性**）。
- `tests/test_p09_bounded_review.py` 9个无联网单元反例：审稿误报措辞不得block、真实material应block、缺引文hold、修订最多两次调用、第二轮仍有新实质问题应block等。

## 还应整改和核查的差错

1. **W43 统计口径**：当前报告同时写「10条事实」和「reported 6 + announced 1 + proposed 4 = 11」。如为互斥枚举，至少一项计数错；若一条事实被多标签，则要明确各标签非互斥并给出每条fact_id状态。验收前冻结10条事实的JSON+SHA，输出机器计算的`Counter`，不使用手写统计。
2. **上游日期精度**：无明确日级时间就不要填写`occurred_on`。可以忠实写「据X媒体本周报道」，不是要求补造日级日期。487事件中day级118条这一数据需要完整导出依据。
3. **reported与proposed**：媒体的「reported」仅说明新闻归属，不意味着事情未发生；不能由状态枚举本身推断事实真假。长期须拆为`claim_attribution`与`real_world_execution_status`，目前关键词推断仍有保守误杀风险，必须核查。
4. **证据质量边界**：issue_policy仅验证模型引用属于原fact pack，不能证明原文章全文本身真假；关键材料仍需要原文网页/快照并人工或独立程序核实，特别是官方规则与交易完成。
5. **RightCode**：真实路由需验证SiliconFlow模型名`deepseek-ai/DeepSeek-V4.1-Flash`和RightCode`deepseek-flash`分别按对应端点/密钥选择，绝不一刀切全局模型名。
6. **音频/客户端**：PR #3现有P09终稿音频三段SHA改动仍未见NAS真实合成、篡改阻断、试听及Gmail/Outlook客户端证据；仍是独立阻断项。

## NAS下一阶段严格验收，不发信

在独立worktree运行新版P09单测、原47项和新增音频/日期测试后，再用**同一份冻结的真实W43 10条可用事实包**至少10轮独立重跑P05/P08/P09：记录每轮版本、模型、来源SHA、决定、硬门/软门问题、是否错批。候选收敛目标≥9/10，建议20轮≥19/20，但这个比率只是**正常文稿可交付率**；同时准备至少10类反例保证有重大错误的文稿**零误放行**。少量事实不足、真实日期不明确、模型服务不可用时可以hold，不能为了通过率放松硬门。将6批旧实验与新版单独对比。

**生产边界不变**：PR #2和PR #3均保持Draft，reader-brief hold，未授权真实发信、合并main、改Cron或补10-12～18事件。

## 执行定位

本决定已在GitHub PR #3的独立复审分支落实为Prompt+程序门+反例测试。是否达到生产验收取决于NAS运行的可复现工件，**不是本文件或已提交代码即代表验证通过**。
