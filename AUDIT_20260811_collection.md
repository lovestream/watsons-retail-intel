# 信息收集/筛选/过滤机制全链路审计报告 — 2026-08-11

> 范围: 采集(collect) → 补漏(broad_search/tavily_gap) → 富化(enrich) → 合并(merge) → 过滤(filter) → 提取(extract) → 时效(temporal) → 新颖性(novelty) → 评分(score) → 报告(report)
> 数据基准: 2026-08-08 ~ 08-11 实际运行日志 + 事件池 + 漏斗报告

---

## 一、全链路漏斗现状（08-10 / 08-11）

| 阶段 | 08-10 | 08-11 |
|---|---|---|
| 采集总量 | 91 | 106 |
| cleaned | 8 (8.8%) | 11 (10.4%) |
| reference | 39 (42.9%) | 47 (44.3%) |
| rejected | 44 (48.4%) | 48 (45.3%) |
| 事件 | 24 | 20 |
| 可报告 | 2 | 8 |
| 最终利用率 | 2.2% | 7.5% |

**核心矛盾**: 采集 100 条 → 只有 8-11 条 cleaned → 20 事件 → 8 可报告。大量采集白费。

## 二、近 7 天事件来源贡献分布（关键！）

| 通道 | 事件数 | 占比 | 健康度 |
|---|---|---|---|
| cloakbrowser (品观/财联社/华尔街见闻) | 176 | 59% | ✅ 主力 |
| rsshub (36kr 搜索/快讯) | 51 | 17% | ✅ 稳定 |
| tavily | 46 | 15% | ⚠️ 产出低, 部分 bug |
| xcrawl | 23 | 8% | ⚠️ 慢/空转严重 |
| tophub | 0 | 0% | ❌ 验证码墙, 已废 |
| web_monitor (12 直连源) | 0 | 0% | ❌ 采集了但全被过滤 |
| broad_search | 0 (事件层) | 0% | ⚠️ 只做线索发现 |

---

## 三、发现的问题（按优先级排序）

### 🔴 P0-1: tavily_gap_search 连续多天 0 产出 — int 711 崩溃
- **证据**: 08-11 日志 18 个查询全部 `'int' object has no attribute 'lower'`，耗时 34.83s 全空转，0 条结果。
- **根因**: `config/keywords.yaml` 第 259 行 `- 711`（便利店 7-11）被 YAML 解析成 int。`tavily_gap_search._load_all_keywords()` 无 `str()` 防御 → `kw.lower()` 崩。
- **影响**: tavily_gap 补漏通道形同虚设（近 7 天该通道 0 贡献），每次还白烧 6 个 tavily key 的额度（虽然崩溃发生在匹配阶段，但搜索已发出）。
- **修法**: ① YAML `"711"` 加引号（根治）② `_load_all_keywords` 里 `str(w)` 防御（兜底）。
- **注意**: `collect_daily_articles.load_all_keywords()` 已有 `str(w)` 防御，只有 tavily_gap 漏了。

### 🔴 P0-2: tophub 采集已废 — 站点加验证码墙
- **证据**: 08-10/11 tophub 6 个查询全部执行但 `→ 提取到 0 篇`。浏览器实测 tophub.today 返回"安全验证"页（IP 风控验证码），h3 结构 0 命中。
- **影响**: 每天空转 ~20s + 6 次 Playwright 启动，0 产出。
- **修法**: 建议**停用 tophub_collect**（enabled=false），或仅在能过验证码的环境（生产机带 cookie）启用。另修 `cloakbrowser_enrich.py` L283 的防御 bug：`tophub_data.get("articles", ...)` 在 tophub 文件是 `[]` 时抛 `'list' object has no attribute 'get'`（虽只 warning，但日志每天刷错误）。

### 🟠 P1-3: xcrawl 采集慢+空转 — 7 源失败各耗 374s
- **证据**: 08-11 collect 阶段 16 分钟，其中 xcrawl_11/05/15/14/17/21/23 **7 个源全部失败，每个耗时 374.89s**（30s timeout × 3 retry + 等待），合计 ~43 分钟空转（并行下 ~16 分钟墙钟）。
- **根因**: run.xcrawl.com 频繁 ReadTimeout（网络不稳）。
- **影响**: 每天 collect 阶段被拖慢 10+ 分钟；成功源也慢（xcrawl_01 333s）。
- **修法**: ① 降 timeout 30s→20s、retry 3→2 ② 对连续 3 天失败 0 产出的源自动降权/停用 ③ 失败源日志加"连续失败计数"，超过 N 次自动 disable。

### 🟠 P1-4: web_monitor 12 直连源全采全滤 — 0 事件贡献
- **证据**: source_url_monitor_summary 显示 131 链接、111 新链接，但 `newly_discovered_count=0`，且 raw_articles_all 里无任何 web_monitor/direct 前缀文章；近 7 天事件池 0 条来自 web_monitor。
- **根因**: 直连源抓到的多是 RSS/首页快讯，标题短、无正文 → 规则分低 → reference/reject。只有 36kr 快讯(经 rsshub) 幸存。
- **影响**: 12 个源（36kr/晚点/界面/亿邦/联商/赢商/品观/聚美丽/青眼/C2CC）采集动作白做。
- **修法**: ① 对 web_monitor 源强制走全文抓取（enrich）再过滤，而不是只用标题 ② 或降级为"线索源"（只产出 newly_discovered URL 给 cloakbrowser 抓全文）③ 长期 0 贡献的源停用。

### 🟠 P1-5: 采集源利用率极低 — 44-48% 被 reject
- **证据**: 08-10 rejected 44 条、08-11 rejected 48 条。top_matched_keywords 显示"零售"命中 16、"即时零售"12、"美团"9 —— 大量宽泛词命中但内容无关（如"零售业整体趋势"）。
- **根因**: 采集查询词宽（如"零售"、"美团"、"京东"单字词），搜索结果相关度参差。
- **影响**: LLM 复核（36 篇/天）和 extract（57 次 LLM 调用/天）大量消耗在最终被拒的文章上。
- **修法**: ① 采集查询词收紧（"美团"→"美团闪购 最新"、"京东"→"京东秒送/京东到家"）② filter 规则分提高"review"门槛时对"仅命中单泛词"的文章直接 reject 而不是送 LLM ③ 对连续 N 天 0 事件贡献的源自动降权。

### 🟡 P2-6: filter 步骤重复执行（filter + filter_merged 两次全量过滤）
- **证据**: 08-11 filter 日志出现 2 次"开始过滤"+ Phase1 各 106 篇（23:41 和 23:43）。第一次 36 篇送 LLM，第二次 0 篇（因 filter 结果已缓存）——但规则评分仍全量跑。
- **影响**: 计算浪费小（第二次无 LLM），但日志噪音 + 潜在大输入时重复。
- **修法**: filter_merged 仅对新增文章（tavily_gap 补进来的）评分，不重跑全量。

### 🟡 P2-7: 采集查询词与事件贡献失衡 — 24 个 enabled 源里多数低效
- **证据**: rsshub 48 源里 ~20 disabled；enabled 的 huxiu 搜索多数 0-1 条/天（如"门店"、"新零售"、"本地生活"搜索每天 0-1 条），36kr 搜索稳定 2-3 条。
- **修法**: ① 对连续 7 天 0 产出的 rsshub 搜索源自动标记/停用 ② 把有效源的 limit 提高（36kr 搜索 limit 20→30）。

### 🟡 P2-8: 报告阶段无 daily 报告补发机制
- **证据**: 08-08、08-09 有完整 events 数据但无 run_manifest、无 daily 报告、无邮件。08-10/11 只生成了"素材包"（供周报）。
- **影响**: 用户 08-08/09 没收到日报。周报依赖的日报素材也缺。
- **修法**: pipeline 若因中断没跑到报告阶段，应有"补跑报告"的机制（或至少日志醒目告警）。需确认 08-08/09 是刻意停用日报还是中断。

### 🟡 P2-9: 报告文件名不一致（2026-08-07.md vs 08-07.md）
- **证据**: reports/daily/2026/08/ 下既有 `2026-08-07.md`（带年份）也有 `08-01.md`（不带年份）。用户/下游脚本若按固定名查找会漏。
- **修法**: 统一命名（建议带年份），或至少文档注明。

### 🟢 P3-10: cloakbrowser_enrich 的 tophub 读取 bug（见 P0-2）
### 🟢 P3-11: broad_search_discovery 每 4 分钟仅产出 0 事件（只做 URL 线索）
- 建议检查它发现的新 URL 是否真的进了 cloakbrowser 抓取队列（newly_discovered_count=0 时说明全被跳过）。

---

## 四、做得好的（不要动）
- ✅ **novelty 检查纯规则**（历史账本 5958 条对比），不烧 LLM
- ✅ **extract 57 次调用 0 失败**，LLM 稳定性好
- ✅ **score 硬降级机制**（历史背景/资本市场/watsons_relevance≤1 → ARCHIVE）工作正常
- ✅ **filter 的回顾文检测、未来日期拦截**（V6）有效
- ✅ **collect_daily_articles 的关键词 str() 防御**（618/711 问题只影响 tavily_gap）
- ✅ cloakbrowser 主力通道健康（品观 23 条/天）

---

## 五、建议修复优先级执行顺序
1. **P0-1 tavily 711 bug**（5 分钟修完，恢复 tavily_gap 通道）
2. **P0-2 tophub 停用**（避免每天空转 + 修 cloakbrowser_enrich 防御 bug）
3. **P1-3 xcrawl 超时降级**（timeout 20s/retry 2 + 连续失败自动停用）
4. **P1-4 web_monitor 处理**（决定：走全文抓取 or 降线索源 or 停用）
5. **P1-5 采集词收紧 + filter 泛词直接拒**
6. **P2-6 filter_merged 增量**、**P2-7 低效源自动停用**
7. **P2-8 补发机制**（确认 08-08/09 状态）
