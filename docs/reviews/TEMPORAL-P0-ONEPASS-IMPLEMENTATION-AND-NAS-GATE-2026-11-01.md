# 事件时间P0一次性修复：交付、验收与未验证边界（2026-11-01）

状态：**隔离分支代码候选，未在NAS运行，不能声称生产可交付。** 本文只读地记录统一改动集和一个NAS验收关口；仓库写入不等于生产授权。

基线：PR #2 `7dd6b8d`。已有审计报告提出：487件中118件日级（24%）、229条相对日期污染源、P1违反2/5、P2违反91.4%。**各统计口径、事件集合、评审样本与误差定义必须由NAS报告重核；本文不把这些数字当作已被独立核实的结果。**

## 1. 本分支修复范围（完整P0包，不分散上线）

| 范围 | 改动和硬性语义 |
|---|---|
| 相对日期 | `extract_content_dates`不再按处理时刻解析“今天/昨天/3天前”；无可信原稿发表日则仅保留相对表达和偏移位置；有可靠日期才作日级解析；“N小时前”无发表时刻则不推算日期 |
| 多日期与伪精确 | 删除`max(dates)`推测发生日期，删除从月份/季度/今年双11造单日的路径；无证据时occurred空值且precision=unknown（非day精度可保留） |
| 文章发表和宣布 | 删除published→announced自动赋值；真实宣布与计划生效仍为独立字段 |
| 发生日共同合同 | 新增`date_contract.py`：校验真实日历日、precision=day、置信、实际原文连续引文、引用中日期唯一或有事件专属的证据短句、角色不矛盾；不从日期值倒推出原文缺失年份 |
| 原始证据 | 时效验证优先核对原文全文；原文缺失不得用模型生成的fact/evidence_text拼出假文章再反证模型 |
| 周/月/年下游 | `facts_pack._occurred_index`和`occurrences_ok`复用统一可信发生日判定；移除`published`精度白名单；多事件cluster不再无条件用第一个事件的发生日代表全部；防重复整库扫描 |
| P02输入完整性 | 原LLM首调只看正文前2000字、简化重试前800字；现首调最多4200字，保留前部上下文并优先选正文后部**原文时间段落**；简化重试最多2600字（需对比token成本） |
| P02与P03提示词 | 内置P02时间角色约束增强，并同步文档。P03虽在prompt_registry标enabled，但没有在本分支新增全量LLM复审；不应误说P03已启用 |
| 可重复性 | 时效主函数按`--date`指定上海业务日构造as-of日期，避免回放因执行时钟不同发生漂移；原始article/observed仍各自存档 |

**说明**：硬门只证明日期满足必要的结构、原文子串和角色条件，不能替代人工对多事件语义的核查；存在日期的事实也可能只有发表、统计或生效时间，诚实的unknown不是失败。

## 2. 新增/调整的离线测试

- `tests/test_temporal_p0_contract.py`新增21个直接反例和正例：未锚定昨日、原稿锚定、处理日漂移、只有日期无法解析N小时前、多个日期、published≠announced、计划生效日误用、季度伪精确、月份默认1日、低置信与未知精度、原文引用不符、非法日期、模型自造证据、cluster串日、月报旁路、跨年等。
- `tests/test_temporal_fact_validation.py`旧测试中显式发生日期的正例加入模拟原文证据、真实day精度与角色；“今年618”营销节点不能自动当6/18日发生，新事件positive改用明确动作案例。
- `scripts/audit_temporal_p0_shadow.py`全程只读，通过data/events/*/events_analyzed.json及关联原文计算：原始主张字段覆盖、可信来源证据数、失配数、原文缺失率、按priority/precision分层。只输出stdout；不是独立人工真值审查。

### NAS隔离执行（不能直接对生产pipeline跑命令）

在隔离worktree、使用只读挂载的历史事件数据集（不得修改或覆盖历史目录）：

```bash
PYTHONPATH=".venv_packages:." python3 -m py_compile \
  skills/temporal_fact_validation/date_contract.py \
  skills/temporal_fact_validation/temporal_fact_validation.py \
  skills/reader_brief/facts_pack.py \
  skills/extract_events/extract_events.py \
  scripts/audit_temporal_p0_shadow.py
PYTHONPATH=".venv_packages:." python3 -m pytest -q \
  tests/test_temporal_p0_contract.py \
  tests/test_temporal_fact_validation.py \
  tests/reader_brief/test_reader_brief.py \
  tests/test_p09_bounded_review.py \
  tests/test_production_delivery_gate.py
PYTHONPATH=".venv_packages:." python3 scripts/audit_temporal_p0_shadow.py \
  --project-root /path/to/readonly-history
```

另外：重跑先前“原47项”的**确切测试清单**（不是只跑新增用例），做包含P01-P09、周/月/年事实包、音频三哈希与拒发门的全链路影子验收。任何真实邮件、TTS收费调用、LLM联网或NAS写入都不在本次授权内。

## 3. 487条真实样本的终验条件

1. **数据对账**：记录原始487个事件ID、源数据commit/冻结SHA，核对118个day、229条relative污染源和P1/P2分母定义。缺原始全文的事件单列，不可误称真实source verified。
2. **差错复核**：对原“P1违反2/5、P2违反91.4%”的实际样本逐项复测，输出before/after错误类型表、保留unknown数量、可恢复真日级比例。审查者对“模型引用句真实但日期归属错误”另计重错。
3. **一致性**：同一冻结包在不同系统执行日重放，所有事件的time role / date precision / fact pack哈希稳定；没有把月/季度日期改成第一天。
4. **真实反例**：发布、宣布、计划生效、已完成、统计截止、今年双11、转载原稿、上周、相对日及跨年全部正确分离；多事件cluster不串日。
5. **下游**：周报/月报/年报各用一次“伪发生日期”故障注入，所有date_role=occurred旁路均fail-closed，source observed可在“本期报道/观察”语义下正常使用。
6. **质量/成本**：不减少P0/P1可验证事实和竞对素材的真实覆盖；统计P02平均字符、LLM token增量与失败率。新证据字段的准确性不能仅通过字符串存在判定。
7. **交付门**：存在一个重大日期误放行、P09不稳定或时效高优先级回归失败，整轮hold。不因20多个单元测试通过就允许生产邮件。

## 4. 回滚、代码边界与状态

- 分支：`fix/temporal-evidence-p0-batch-20261101`，独立于PR #4的采集架构设计；不修改主线或PR #2工作分支。
- 文件只涉及时间解析、原文事实抽取上下文、周/月/年事实包可信时间门、测试和只读审计脚本、文档；不改实时Cron、邮件开关/收件人或生产账本。
- **尚未实测**：未运行NAS历史487条对照、原47项回归、真值抽样/LLM成本和生产前全链路影子验证，因此不得将本PR“测试待跑”称为“已全部修好”。
- 后续任何部署、历史回填、Cron改动、PR合并或SMTP发送另行授权；如验收不通过，保留PR #2的原始候选分支和全部历史事件文件，可直接放弃本隔离PR而不影响运行系统。
