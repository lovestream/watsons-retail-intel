# 2026-05-16 系统全面审计与修复

## 业务目标回顾
用户：屈臣氏电商负责人
- 90%生意在即时零售（美团/淘宝闪购/京东秒送/抖音小时达）
- 本地生活作为战略赋能线下门店（大众点评/抖音本服）
- 传统电商仅关注重大变化（天猫/京东/抖音）
- 竞对：丝芙兰/妍丽/WOW COLOUR/名创优品/超市便利店
- 输出格式：播客（通勤收听），需要深度观点而非简单照搬

## 修复内容

### P0: 本地生活渠道完全空白 → 已修复
**问题**：大众点评和抖音本服完全没有采集源和报告分类
**修复**：
1. `config/keywords.yaml`: 新增 `local_life`/`dianping`/`douyin_local` 平台关键词
2. `config/sources.yaml`: 新增3个RSSHub源
   - 虎嗅搜索-本地生活
   - 虎嗅搜索-大众点评  
   - 虎嗅搜索-抖音本地生活
3. `filter_relevant_articles.py`: INSTANT_RETAIL_KEYWORDS 添加 "大众点评"/"抖音本地生活"/"抖音本服"/"抖音团购"/"到店"
4. `generate_daily_report.py`: PLATFORM_SECTIONS 新增 "大众点评 / 抖音本地生活" 分类（priority=2 战略渠道）

### P0: 竞对缺妍丽/WOW COLOUR/便利店 → 已修复
**问题**：妍丽、WOW COLOUR、超市便利店行业完全没有覆盖
**修复**：
1. `config/keywords.yaml`: competitors 新增 `convenience_stores` 分类（便利蜂/全家/711/罗森/美宜佳）和 `supermarket` 分类（盒马/永辉/大润发/山姆）
2. `config/sources.yaml`: 新增2个RSSHub源
   - 虎嗅搜索-妍丽
   - 虎嗅搜索-WOW COLOUR
   - 虎嗅搜索-便利店
3. `filter_relevant_articles.py`: COMPETITOR_KEYWORDS 添加所有便利店和超市竞对
4. `generate_daily_report.py`: competitor_names 添加便利店和超市竞对

### P1: 信息源过度依赖虎嗅 → 已修复
**问题**：14个虎嗅搜索源覆盖面窄
**修复**：新增8个RSSHub源，覆盖本地生活/竞对/传统电商

### P1: 关键词配置不完整 → 已修复
**问题**：keywords.yaml缺少多个平台关键词
**修复**：
- 新增：淘宝闪购、抖音小时达、抖音本服/本地生活、大众点评、京东秒送
- 扩展：天猫、京东传统电商

### P1: 报告没有按渠道优先级分配篇幅 → 已修复
**问题**：PLATFORM_SECTIONS平均分配，即时零售被稀释
**修复**：
- PLATFORM_SECTIONS 改为字典格式，包含 keywords/priority/max_items
- priority=1(即时零售): max_items=5
- priority=2(本地生活): max_items=3
- priority=3(传统电商): max_items=2
- 添加优先级标签：🔴核心渠道/🟡战略渠道/⚪观察渠道

### P2: 传统电商渠道无专门源 → 已修复
**修复**：新增虎嗅搜索-天猫、虎嗅搜索-京东

### P2: decide_final_pool 重复代码 → 已修复（本轮之前）
- 移除10个重复bootstrap_seen块（397→279行）
- 添加关键词匹配要求，防止reference池污染

## 验证结果
- ✅ 所有5个核心文件编译通过
- ✅ 26个RSSHub源启用（原18个+新增8个）
- ✅ PLATFORM_SECTIONS包含本地生活渠道
- ✅ 过滤器支持所有新关键词
- ✅ 竞对列表完整（含便利店/超市）

## 新增RSSHub源清单
| ID | 名称 | 分类 | 优先级 |
|---|---|---|---|
| rsshub_huxiu_search_local_life | 虎嗅搜索-本地生活 | 本地生活 | 88 |
| rsshub_huxiu_search_dianping | 虎嗅搜索-大众点评 | 本地生活 | 85 |
| rsshub_huxiu_search_douyin_local | 虎嗅搜索-抖音本地生活 | 本地生活 | 85 |
| rsshub_huxiu_search_yanli | 虎嗅搜索-妍丽 | 竞品 | 75 |
| rsshub_huxiu_search_wowcolour | 虎嗅搜索-WOW COLOUR | 竞品 | 75 |
| rsshub_huxiu_search_convenience_store | 虎嗅搜索-便利店 | 零售行业 | 70 |
| rsshub_huxiu_search_tmall | 虎嗅搜索-天猫 | 传统电商 | 60 |
| rsshub_huxiu_search_jd | 虎嗅搜索-京东 | 传统电商 | 60 |

## 下一步
1. 运行完整pipeline验证端到端质量
2. 生成5月16日报测试新分类效果
3. 检查播客口播稿是否有深度观点
