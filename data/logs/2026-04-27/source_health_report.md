# 信息源健康度报告 ｜ 2026-04-27

**生成时间**: 2026-04-27T23:27:59.576320+08:00

## 总览

| 指标 | 数值 |
|------|------|
| 信息源数 | 5 |
| 原始文章 | 53 |
| 清洗文章 | 0 |
| 参考文章 | 0 |
| 拒绝文章 | 0 |
| 时间窗口内 | 24 |
| 近窗口 | 2 |
| 旧文章 | 27 |

## 分级分布

| 级别 | 数量 | 含义 |
|------|------|------|
| 🟢 A | 0 | 时效高+产出高 |
| 🟡 B | 4 | 时效高但产出低 |
| 🟠 C | 1 | 过时但有匹配关键词 |
| 🔴 D | 0 | 过时且无匹配 |

## 1. A类源 (keep_primary)

> 无 A类源

## 2. B类源 (keep_secondary_limit)

| source_name | total | in_win | near_win | old | matched_kw | cleaned | yield | old_ratio | recent_ratio |
|-------------|-------|--------|----------|-----|------------|---------|-------|-----------|--------------|
| rsshub_huxiu_article | 13 | 13 | 0 | 0 | 1 | 0 | 0.0% | 0% | 100% |
| rsshub_huxiu_consumer_channel | 8 | 7 | 1 | 0 | 0 | 0 | 0.0% | 0% | 100% |
| rsshub_huxiu_search_douyin_hour | 2 | 2 | 0 | 0 | 1 | 0 | 0.0% | 0% | 100% |
| rsshub_latepost | 10 | 2 | 1 | 7 | 9 | 0 | 0.0% | 70% | 30% |

## 3. C类源 (reference_only)

| source_name | total | in_win | near_win | old | matched_kw | cleaned | yield | old_ratio | recent_ratio |
|-------------|-------|--------|----------|-----|------------|---------|-------|-----------|--------------|
| rsshub_huxiu_search_watsons | 20 | 0 | 0 | 20 | 20 | 0 | 0.0% | 100% | 0% |

## 4. D类源 (disable_candidate)

> 无 D类源

## 5. old_ratio 最高的前10个源

| # | 源 | total | old_ratio | recent_ratio | grade |
|---|-----|-------|-----------|--------------|-------|
| 1 | rsshub_huxiu_search_watsons | 20 | 100.0% | 0.0% | 🟠 C |
| 2 | rsshub_latepost | 10 | 70.0% | 30.0% | 🟡 B |
| 3 | rsshub_huxiu_article | 13 | 0.0% | 100.0% | 🟡 B |
| 4 | rsshub_huxiu_consumer_channel | 8 | 0.0% | 100.0% | 🟡 B |
| 5 | rsshub_huxiu_search_douyin_hour | 2 | 0.0% | 100.0% | 🟡 B |

## 6. cleaned_yield 最高的前10个源

| # | 源 | total | cleaned | yield_rate | grade |
|---|-----|-------|---------|------------|-------|
| 1 | rsshub_huxiu_article | 13 | 0 | 0.0% | 🟡 B |
| 2 | rsshub_huxiu_consumer_channel | 8 | 0 | 0.0% | 🟡 B |
| 3 | rsshub_huxiu_search_douyin_hour | 2 | 0 | 0.0% | 🟡 B |
| 4 | rsshub_huxiu_search_watsons | 20 | 0 | 0.0% | 🟠 C |
| 5 | rsshub_latepost | 10 | 0 | 0.0% | 🟡 B |

## 7. 建议降级为 reference_only 的源

- **rsshub_huxiu_search_watsons**: old_ratio=100.0%, matched_kw=20, total=20

## 8. 建议禁用的源

> 无需禁用

## 附录：全源明细

| source_name | total | in_win | near_win | old | matched_kw | cleaned | ref | rejected | old_r | recent_r | yield | grade | rec |
|-------------|-------|--------|----------|-----|------------|---------|-----|---------|-------|----------|-------|-------|-----|
| rsshub_huxiu_article | 13 | 13 | 0 | 0 | 1 | 0 | 0 | 0 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_huxiu_consumer_channel | 8 | 7 | 1 | 0 | 0 | 0 | 0 | 0 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_huxiu_search_douyin_hour | 2 | 2 | 0 | 0 | 1 | 0 | 0 | 0 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_huxiu_search_watsons | 20 | 0 | 0 | 20 | 20 | 0 | 0 | 0 | 100% | 0% | 0.0% | 🟠 C | reference_only |
| rsshub_latepost | 10 | 2 | 1 | 7 | 9 | 0 | 0 | 0 | 70% | 30% | 0.0% | 🟡 B | keep_secondary_limit |
