# SOURCE_REGISTRY — 核心信源清单（R3，WEEK-TO-DATE-PILOT）

> 窗口：2026-10-19 00:00 至 2026-10-24 14:01（北京时间，Asia/Shanghai），cutoff ISO 8601：`2026-10-24T14:01:11+08:00`。
> R3 修正：R2 JSON 声明 34 源、实际只有 21 条，且缺 owner/home/section/topic/rhythm/last_checked/latest/new/failure 等字段，所谓“同源、字段一致”不成立。本版列出全部 35 条（R2 的 34 条 + R3 新增的深圳市政府官网，此前仅为事件来源），每条字段齐全，未核实处填 null，不猜域名、不猜栏目 URL。
> 命名（R3）：源级别 source_tier = T1 官方 / T2 垂类媒体 / T3 商业媒体 / T4 搜索与社交（仅线索）；核验级别 verification_level = V1 原始报道/转载已读取 / V2 官方信息已核实 / V3 独立多源已核实。旧 L1/L2/L3 不再作为核验级别使用，旧 Lx 与新 Tx 含义一一对应，仅改名。
> 机器版：`SOURCE_REGISTRY.json`（与本表同源，字段一致；JSON 中 checked_sources=22、column_opened_sources=9、column_failed_sources=1）。
> 关键区分：栏目页真正打开 ≠ 搜索摘要；失败 ≠ 无发布；未查 ≠ 已覆盖。栏目核验详见 `runs/COLUMN-CHECK-R3-2026-10-24.md` 与 `runs/SEARCH-MANIFEST-R3-2026-10-24.csv`。

## 字段说明

`source_id, name, owner_entity, home_url, feed_or_section_url, tier, topic, expected_update_rhythm, last_checked_at, reachable, latest_seen_at, new_items, verified_items, unique_qualified_events, failure_reason`

- reachable：opened_column（栏目页真正打开）/ opened_article（单篇打开）/ opened_homepage / search_only / reprint_only / opened_reprint / column_failed（栏目尝试失败）/ not_checked（未查）。
- new_items：该来源在窗口内栏目/检索中列出的条目数（栏目核验以栏目页清单为准；搜索层级以 R2 检索数为准）。
- verified_items：打开原文并锚定绝对日期的条目数（去重，同一事件多篇转载按事件来源数计，详见 failure_reason）。
- unique_qualified_events：该来源独家贡献的合格事件数（去重后；ARCHIVE/观察项单独注明，不混入 P1/P2）。

| source_id | name | owner_entity | home_url | feed_or_section_url | tier | topic | expected_update_rhythm | last_checked_at | reachable | latest_seen_at | new_items | verified_items | unique_qualified_events | failure_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| meituan-shangou | 美团闪购官方 | 美团 |  |  | T1 | 即时零售平台 | 不定期，活动/规则 | 2026-10-24 | search_only | 无窗口内可锚定日期 | 0 | 0 | 0 | 通用搜索抓不到 App 内/商家后台/公众号发布；站内搜索 0 返回不是无发布证明；R3 未做栏目页打开 |
| jd-miaosong | 京东秒送 / 京东到家官方 | 京东 |  |  | T1 | 即时零售平台 | 不定期 | 2026-10-24 | search_only | 无秒送专项可锚定日期 | 0 | 0 | 0 | 返回多为 B2C 领券文，秒送专项未被搜索返回；R3 未做栏目页打开 |
| taobao-shangou | 淘宝闪购 / 饿了么官方 | 阿里 |  |  | T1 | 即时零售平台 | 不定期 | 2026-10-24 | search_only | 更名报道 2026-10-04 / 10-13（窗口外） | 0 | 0 | 0 | 窗口内只返回通用双 11 节奏文，无闪购专项；R3 未做栏目页打开 |
| douyin-hour | 抖音小时达 / 抖音电商官方 | 字节跳动 |  |  | T1 | 即时零售平台 | 不定期 | 2026-10-24 | search_only | 抖音电商首日 2026-10-15（窗口外） | 0 | 0 | 0 | 无小时达专项返回；抖音电商数据属窗口外背景；R3 未做栏目页打开 |
| watsons-cn | 屈臣氏中国大陆官方 | 屈臣氏中国 |  |  | T1 | 品牌 / 渠道 | 不定期 | 2026-10-24 | search_only | 无大陆可锚定日期 | 0 | 0 | 0 | 返回多为香港/马来西亚/台湾，按地域排除；R3 未做栏目页打开 |
| nmpa | 国家药监局 | 国家药监局 | https://www.nmpa.gov.cn/ | https://www.nmpa.gov.cn/hzhp/index.html | T1 | 监管 | 不定期公告 | 2026-10-24 | opened_column | 2026-10-23 | 1 | 0 | 0 | R3 栏目打开：窗口内美妆 1 条（上海调研），无即时零售内容；中文栏目此前文本抓取 412 失败已由浏览器补验 |
| zj-mpa | 浙江省药监局 | 浙江省药监局 | https://mpa.zj.gov.cn/ | https://mpa.zj.gov.cn/col/col1229215895/index.html | T1 | 监管 | 不定期公告 | 2026-10-24 | opened_column | 2026-10-10（政策通知，窗口外） | 0 | 1 | 1 | R3 栏目打开：窗口内美妆 0 条；verified 1 为窗口外政策原文页（浙药监规〔2026〕3号），通气会数字仍来自转载，核验 V1 |
| samr | 市场监管总局 | 市场监管总局 | https://www.samr.gov.cn/ | https://www.samr.gov.cn/xw/sj/index.html | T1 | 监管 | 不定期公告 | 2026-10-24 | opened_column | 2026-10-23（司局栏目，非美妆） | 13 | 0 | 0 | R3 栏目打开：无化妆品专属司局/专题；司局窗口内 13 条均非美妆 |
| hunan-mpa | 湖南省市场监管局 | 湖南省市场监管局 | https://amr.hunan.gov.cn/ | https://amr.hunan.gov.cn/amr/xxx/mtzsx/index.html | T1 | 监管（省级） | 不定期公告 | 2026-10-24 | opened_column | 2026-10-21 | 12 | 5 | 1 | R3 栏目打开：10-14 转载页确认存在；verified 5 为官方原文 10-12、官网转载 10-14、省政府门户 10-21、栏目跟进 10-21、华声在线 10-21（去重后同一故事）；事件 ID 已迁移为 EV-20261012-01 |
| sz-gov | 深圳市政府官网 | 深圳市政府 | https://www.sz.gov.cn/ | https://www.sz.gov.cn/cn/xxgk/zfxxgj/bmdt/ | T1 | 地方政府 / 履约 | 不定期 | 2026-10-24 | opened_column | 2026-10-23 | 30 | 3 | 1 | R3 新增注册源（此前仅为事件来源，未计入 R2 的 34 源；R3 注册总数因此为 35）。栏目第 1–3 页 30 条全覆盖，美妆 0 条；verified 3 为政府官网页、中新网首发、新浪转载（同一骑手事件） |
| ebrun | 亿邦动力 | 亿邦动力 | https://www.ebrun.com/ | https://www.ebrun.com/information/retail/ | T2 | 零售 / 电商 | 每日 | 2026-10-24 | opened_column | 2026-10-23 | 52 | 1 | 0 | R3 栏目打开：零售栏目 52 条，即时零售×美妆 0 条；verified 1 为 R2 窗口外 09-28 官旗闪电仓锚定，窗口内单篇未逐篇打开 |
| linkshop | 联商网 | 联商网 | https://www.linkshop.com/ | http://www.linkshop.cn/web/index.htm | T2 | 零售 | 每日 | 2026-10-24 | column_failed | 无可锚定栏目条目 | 0 | 0 | 0 | R3 栏目失败：linkshop.com 两次超时，linkshop.cn 空白/隐私错误；不计入打开，不计入无发布；公众号 linkshop2012 路径未核验 |
| pinguan | 品观 | 品观 | https://www.pinguan.com/ | https://www.pinguan.com/media | T2 | 美妆 | 每日 | 2026-10-24 | opened_column | 2026-10-22 | 6 | 0 | 0 | R3 栏目打开：媒体栏目 6 条，标题均无即时零售字样；下一条 2026-10-16 窗口外 |
| jumeili | 聚美丽 | 金华聚美网络科技有限公司 | https://www.jumeili.cn/ | https://www.jumeili.cn/news/ | T2 | 美妆 | 每日 | 2026-10-24 | opened_column | 2026-10-24（相对时间换算，近似） | 22 | 0 | 0 | 域名纠正：jumeili.com 超时，正确域名 jumeili.cn；22 条为严格窗口内条目，约 14:40 的 1 条已过 cutoff 排除 |
| qingyan | 青眼 | 青眼网络科技（武汉）有限公司 | https://www.iqingyan.cn/ | https://www.iqingyan.cn/ | T2 | 美妆 | 每日 | 2026-10-24 | opened_column | 2026-10-23 | 15 | 0 | 0 | 域名纠正：qingyan.com 连接重置，正确站 iqingyan.cn；栏目为首页主文章列表（独立文章列表页未确认），15 条中无即时零售×美妆项 |
| c2cc | 化妆品观察 | 化妆品观察 |  |  | T2 | 美妆 | 每日 | 2026-10-24 | search_only | 2026-10-10（Q3 大盘，窗口外） | 0 | 0 | 0 | 原站未打开；R3 未做栏目页打开 |
| cbndata | CBNData | CBNData |  |  | T2 | 数据 / 消费 | 不定期 | 2026-10-24 | search_only | 无 CBNData 原创可锚定日期 | 0 | 0 | 0 | 返回为 CBE 榜单等非 CBNData 素材，口径不明；R3 未做栏目页打开 |
| 36kr | 36氪 | 36氪 |  |  | T3 | 商业 | 每日 | 2026-10-24 | search_only | 旧文为主（618、2025-07-25） | 0 | 0 | 0 | 时间戳陷阱：2025 年 10 月新法易误读为 2026 年 10 月；R3 未做栏目页打开 |
| jiemian | 界面新闻 | 界面新闻 |  |  | T3 | 商业 | 每日 | 2026-10-24 | search_only | 2026-10-18（宁波台风暂停，窗口外、非美妆） | 0 | 0 | 0 | 窗口边界案例：差约 4.5 小时即不入池；R3 未做栏目页打开 |
| latepost | 晚点 | 晚点 |  |  | T3 | 商业 | 不定期 | 2026-10-24 | search_only | 无晚点原创可锚定日期 | 0 | 0 | 0 | 返回多为领券广告与 B2C 战报；R3 未做栏目页打开 |
| huxiu | 虎嗅 | 虎嗅 |  |  | T3 | 商业 | 每日 | 2026-10-24 | search_only | 窗口外背景（9 月大会、9 月底虎门） | 0 | 0 | 0 | 无窗口内原创返回；R3 未做栏目页打开 |
| cs-cn | 中证网 / 中国证券报 | 中国证券报 | https://www.cs.com.cn/ |  | T3 | 财经 | 每日 | 2026-10-24 | opened_article | 2026-10-22 15:24（天猫 4 小时数据，事件 2026-10-20） | 1 | 1 | 0 | 打开原文 1 篇；中信研报 2026-10-23 属资本市场，硬排除；R3 未做该站栏目页打开 |
| meituan-wechat | 美团官方公众号 / 新闻中心 | 美团 |  |  | T1 | 即时零售平台 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖；不得冒充已检查 |
| jd-wechat | 京东官方公众号 / 新闻中心 | 京东 |  |  | T1 | 即时零售平台 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| ali-wechat | 阿里官方公众号 / 新闻中心 | 阿里 |  |  | T1 | 即时零售平台 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| proya-official | 珀莱雅官方 | 珀莱雅 |  |  | T1 | 品牌官方 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| loreal-cn | 欧莱雅中国官方 | 欧莱雅中国 |  |  | T1 | 品牌官方 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| winona-official | 薇诺娜官方 | 薇诺娜 |  |  | T1 | 品牌官方 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| sephora-cn | 丝芙兰中国官方 | 丝芙兰中国 |  |  | T1 | 品牌 / 渠道 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| mannings-cn | 万宁中国官方 | 万宁中国 |  |  | T1 | 品牌 / 渠道 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| yicai | 第一财经 | 第一财经 |  |  | T3 | 财经 | 每日 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| wallstreetcn | 华尔街见闻 | 华尔街见闻 |  |  | T3 | 财经 | 每日 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| caixin | 财新 | 财新 |  |  | T3 | 财经 | 每日 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| cosmetics-news | 化妆品报 | 化妆品报 |  |  | T2 | 美妆 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |
| retail-review | 零售商业评论 | 零售商业评论 |  |  | T2 | 零售 | 不定期 |  | not_checked |  |  | 0 | 0 | R3 未查，不计入覆盖 |

## 本轮覆盖小结（分维度，不用单一文章数）

- 信源检测覆盖：注册 35 源，已查 22 源（R2 21 + R3 新增深圳政府官网），未查 13 源（见上表 not_checked）。
- 栏目页真正打开：10 个尝试中 9 个打开（官方 5 + 垂媒 4），1 个失败（联商网）；栏目窗口内列出条目合计 151 条（见 COLUMN-CHECK）。
- 成功打开原文并锚定绝对日期：见各源 verified_items 与 COLUMN-CHECK；其余多为搜索摘要，记 search_only，不冒充栏目巡检。
- 时间窗口覆盖：10-19 00:00–07:00 缺口仍为关键词检索结论（栏目页逐页核对只覆盖到各栏目首屏窗口下界，分页深处未全核），缺口为「未发现」不是「已证明无」。
- 重点平台维度覆盖：美团闪购、京东秒送、淘宝闪购、抖音小时达四平台均已检索，窗口内经核实的专项战报 / 新政 / 招商 / 补贴 / 入驻均为 0，空窗≠静默。
- 域名纠正（R3）：聚美丽 jumeili.com 超时，正确为 jumeili.cn；青眼 qingyan.com 连接重置，正确为 iqingyan.cn；旧 .com 域名不再作为已验证栏目 URL。
- 国家医保局：R2 L1 巡检中已查其 10-21 基金数据（业务无关，排除），不计入本注册表的 35 源，避免把无关源计入美妆覆盖。