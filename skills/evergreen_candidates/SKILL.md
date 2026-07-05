# evergreen_candidates — 高价值旧文转知识库

从 reference_articles.json 和 rejected_articles.json 中提取高价值历史文章，
存入 evergreen 知识库，不污染日报但可用于周报/月报/经营分析补充。

## 入选条件
- time_status = old 或 unknown_time
- rule_score >= 8
- page_type in [article, report, news, unknown]
- 非 noise 类型 (非促销/产品页)

## 用途
1. 行业背景知识库
2. 周报/月报素材
3. 低信号日解释背景
4. 经营分析Agent补充上下文

## 输出
- data/evergreen/YYYY-MM-DD/evergreen_candidates.json
- data/logs/YYYY-MM-DD/evergreen_candidates.log