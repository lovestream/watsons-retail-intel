# PR #8 准入硬门 NAS 全量回放验收（2026-11-01）

> 验收对象：PR #8 `fix/business-intel-admission-once-20261101@9fac080`（Draft）
> 环境：隔离副本 `/tmp/pr8` + 生产 10 月全量事件（只读）
> 性质：**代码未部署；本报告只说明隔离回放结果，不代表生产已切换。**

## 1. 测试结果

| 项目 | 结果 |
|---|---|
| `tests/test_business_scope_admission.py`（18 正反例） | 初跑 **17/18**，修复后 **18/18** |
| 合计 pytest（含 temporal/reader_brief/gate/p09） | **82 passed** |

### 初跑失败项（已修复）

`test_county_retail_expansion_user_selected_pass` 失败：
「山姆、奥乐齐、盒马掘金“县级”市场」被判定 `reject`。

**根因**：全角引号导致「“县级”市场」无法匹配「县级市场」；且“掘金”不在运营词表。
**修复**：新增 `_norm()` 去除引号/空白，运营词表补充「掘金/抢滩/加码/深耕」等。

## 2. 全量回放结果（629 条事件）

| decision | 数量 | 占比 |
|---|---|---|
| reject | 488 | 77.6% |
| reference | 104 | 16.5% |
| **main** | **37** | **5.9%** |

main 的构成：

| 理由 | 数量 |
|---|---|
| beauty_instant_retail_move | 23 |
| named_retail_channel_expansion | 6 |
| watsons_operating_move | 5 |
| beauty_competitor_operating_move | 3 |

## 3. 金标准核对（你审核过的有效/无效样本）

### 应保留（5 个场景）

| 场景 | 结果 |
|---|---|
| 美团闪购 × 丝芙兰/屈臣氏直播试点 | ✅ 全部保留 |
| 山姆会员商店江阴店开业（第70家/第4家县级市） | ✅ 修复后保留 |
| 盒马双路线下沉县域 | ✅ 全部保留 |
| 奥乐齐沿沪宁线布局县域 | ✅ 全部保留 |
| 屈臣氏有效创新/共创 | ⚠️ 10 月事件库中未检索到该条，无法验证 |

### 应剔除（12 类）

野人先生(15)、华致酒行(67)、上美股份高薪招聘(1)、花西子高薪招聘(1)、
乐刻运动(4)、寿司郎(19)、SAP发布(1)、喜茶(1)、霸王茶姬(6)、
OpenAI(12)、奥迪(17)、2022年双11(2)

**结果：全部剔除干净。**

### 汇总

| 指标 | 值 |
|---|---|
| 应保留误杀 | **0** |
| 应剔除漏放 | **0** |

> 初跑时曾出现「山姆江阴误杀 1」「华致酒行漏放 12」，均已修复并复验归零。

## 4. 搜索源评估（按准入通过率）

| 来源 | 总量 | main | reference | reject | 有效率 |
|---|---|---|---|---|---|
| cloakbrowser_cls_beauty | 135 | 23 | 34 | 78 | **17%** |
| rsshub_36kr_search_instant_retail | 10 | 6 | 1 | 3 | **60%** |
| rsshub_36kr_search_beauty | 25 | 3 | 1 | 21 | 12% |
| tavily_gap_search | 42 | 4 | 13 | 25 | 10% |
| cloakbrowser_cls_instant_retail | 153 | 0 | 11 | 142 | **0%** |
| rsshub_huxiu_search_store | 93 | 0 | 4 | 89 | **0%** |
| cloakbrowser_pinguan_beauty | 53 | 0 | 18 | 35 | 0% |
| cloakbrowser_36kr_beauty | 30 | 0 | 6 | 24 | 0% |
| cloakbrowser_36kr_consumer | 15 | 0 | 1 | 14 | 0% |
| tavily_03 | 9 | 0 | 0 | 9 | 0% |

### 高噪音源（reject ≥90% 且 ≥10 条）

| 来源 | 条数 | reject率 | 主要拒绝理由 |
|---|---|---|---|
| cloakbrowser_cls_instant_retail | 153 | 93% | unrelated_industry_subject(130) |
| rsshub_huxiu_search_store | 93 | 96% | unrelated_industry(47) / not_watsons_beauty(42) |
| cloakbrowser_36kr_consumer | 15 | 93% | not_watsons_beauty(9) / unrelated_industry(5) |

### 建议（**不擅自改配置**）

- `cloakbrowser_cls_instant_retail`：153 条产出 0 条 main，130 条因主体无关被拒。
  建议收窄该源的关键词，而非直接删除（它可能仍有少量即时零售信号）。
- `rsshub_huxiu_search_store`：96% 拒绝，建议复核其 query 是否过宽。
- `rsshub_36kr_search_instant_retail`：有效率 60%，**应加大配额**。

**决策依据应是召回率，不是拒绝率**——需先确认这些源是否贡献了 unique 的 P1 事件，
再决定收窄或删除。本报告只提供数据，不执行改源。

## 5. 人工核查清单（已生成待填）

| 文件 | 内容 |
|---|---|
| `docs/reviews/business_scope/A_main_37_needs_review.csv` | 37 条 main 全量 |
| `docs/reviews/business_scope/B_reference_sample_40.csv` | reference 抽样 40/104 |

字段含：event_id / day / priority / decision / reason / title / source / published，
外加 3 个人工填写列（判断/理由/备注）。

**37 条 main 需逐条确认**——这是准入硬门的实际交付面，比之前的 25 条口径更严。

## 6. 未完成（不能宣称已修好）

1. **人工核查 37 条 main + 40 条 reference 未做**
2. **搜索源调整未执行**（需你决策：按召回率收窄还是保留）
3. **跨日去重全链路未验证**（本回放只到准入层，未接去重+出稿）
4. **周/月/年影子回归未跑**（需 LLM）
5. `屈臣氏共创` 场景在 10 月事件库中未命中，无法验证该规则

## 7. 边界

PR #8 保持 Draft；PR #2/#4/#6 未合并；main、生产 Cron、历史账本、邮件设置均未修改；
正式邮件 hold；未回填任何历史数据。
