# S3-A2 冻结回归集（先冻结标签与 URL，再评测，不得事后改标签）

> 冻结时间：2026-10-24（S3-A 阶段，评测前）。本文件只含标签与 URL，不含评测结果；评测结果写入另一文件 `runs/S3-A2-REGRESSION-RESULTS-2026-10-24.md`，并记录本文件的 SHA-256 以证明冻结后未改标签。
> 正例说明：本项目历史中无经证实的 P0/P1 新发现正例，因此 P0/P1 召回率不可计算，不得人为造 P1；本集用 P2 / 观察 / ARCHIVE / 背景作为可核验的正向匹配项，另设负例检验误报。

| # | 标签 | 事件 / 事实 | URL（冻结） |
|---|---|---|---|
| F1 | P2 正向 | 浙江化妆品监管通气会，2026-10-22，P2 2.88 | https://zj.ifeng.com/c/8xPd1luAxEO ；官网原文页 https://mpa.zj.gov.cn/col/col1229136205/art/2026/art_bbf969304d314025b22b453a687d70fc.html |
| F2 | OBSERVATION 正向 | 湖南“净网清源”专项行动，官方 2026-10-12，行动期 2026-10 至 2027-01 | https://amr.hunan.gov.cn/amr/xxx/xtdtx/202610/t20261012_34079563.html |
| F3 | ARCHIVE 正向 | 深圳骑手安全激励活动，宣布 2026-10-20、启动 2026-10-22 | https://www.sz.gov.cn/cn/xxgk/zfxxgj/bmdt/content/post_13037897.html |
| F4 | BACKGROUND 正向 | 天猫双 11 开卖数据，2026-10-20，纯 B2C，不得升 P1 | https://www.cs.com.cn/ssgs/01/2026/10/22/detail_2026102210046490.html |
| F5 | ARCHIVE 正向 | 妍丽广告违法罚款 3000 元，文号深市监福处罚〔2026〕福田120号 | https://k.sina.com.cn/article_1644114654_61ff32de02002c1me.html |
| F6 | BACKGROUND 正向 | 美团官旗闪电仓，首发 2026-09-28，窗口外背景，不得算本周新事件 | https://m.ebrun.com/710340.html |
| N1 | 负例：旧闻 | 淘宝闪购佣金叙述，锚点 2026-02-13，不得算 10 月新动态 | https://finance.sina.com.cn/roll/2026-02-13/doc-inhmtwhv9531562.shtml |
| N2 | 负例：误读口径 | “124 个品牌破亿”误读为美妆品牌数，实为全平台总数，不得作为美妆 P1 依据 | 与 F4 同源（中证网 2026-10-22），口径纠正见 LEDGER |
| N3 | 负例：ARCHIVE 非 P1 | WOW BEAUTY 上海首店，线下单店动作，不得升 P1 | https://www.sohu.com/a/1090706127_121286085 |
| N4 | 负例：营销榜单非 P1 | 抖音商城双 11 开门红热卖品牌榜，平台营销口径，无绝对成交额 | https://www.163.com/dy/article/LA0P3IEK05118HA4.html |

> 评测方法（后填结果文件）：用现行采集查询（四平台 × 美妆/个护/护肤/防晒/双11/闪电仓/招商/佣金/补贴/履约）与原文判断，逐条记录命中/未命中、命中方式（栏目巡检 / 关键词搜索 / 未命中）、漏报原因；命中不等于入池正确，还须核对关系（SAME_OCCURRENCE / SAME_STORY / NEW / BACKGROUND）与定级是否与冻结标签一致。
