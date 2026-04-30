# 采集层优化记录

## 2026-04-29 采集诊断

### 问题定位
- 34个RSSHub源全部可用（34/34返回200）
- 采集300篇原始文章
- 去重后136篇
- **但日报窗口(04-28 07:00~04-29 07:00)内只有11篇**
- 过滤后只有1篇进main池

### 根因分析
1. **虎嗅搜索源返回相关性排序的旧文** — 89/90 main池文章是`old`时间状态
2. **36kr快讯当天有33篇但全是不相关的财经新闻** — rule_score只有0-2
3. **Tavily搜索51篇也全在窗口外** — 搜索引擎返回相关性而非时间性结果
4. **过滤层LLM复核没运行** — review级别的文章没过LLM就直接reject了
5. **参考文章(reference池)没进入事件抽取** — 147篇reference被完全忽略

### 修改清单

#### 1. 过滤层修改 (`filter_relevant_articles.py`)
- `make_rule_decision`: review门槛从3降到2
- `decide_final_pool`: review文章无LLM结果→归入reference而非reject
- `decide_final_pool`: in_window/near_window即使rule_score低→也归reference

#### 2. 事件抽取修改 (`extract_events.py`)
- 新增reference池文章加载逻辑
- reference文章标记`is_reference=True`
- 优先保留main池，reference按rule_score排序裁剪

#### 3. 效果
- 修改前: 1篇main + 147篇reference(忽略) + 152篇reject
- 修改后: 90篇main + 104篇reference + 106篇reject
- 合计可用文章从1篇提高到194篇

### 待解决
1. **当天新鲜数据源不足** — 需要增加按时间排序的新闻源（虎嗅最新文章、36kr最新、晚点LatePost等）
2. **RSS排序参数** — 已有`sorted=true`但虎嗅搜索源似乎不按时间排序
3. **搜索时效性** — Tavily搜索关键词需要加入日期限定词（如"近日"、"最新"、"2026年4月"）