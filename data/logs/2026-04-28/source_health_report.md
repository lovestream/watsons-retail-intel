# 信息源健康度报告 ｜ 2026-04-28

**生成时间**: 2026-04-29T06:55:19.226307+08:00

## 总览

| 指标 | 数值 |
|------|------|
| 信息源数 | 8 |
| 原始文章 | 71 |
| 清洗文章 | 1 |
| 参考文章 | 10 |
| 拒绝文章 | 60 |
| 时间窗口内 | 31 |
| 近窗口 | 1 |
| 旧文章 | 39 |

## 分级分布

| 级别 | 数量 | 含义 |
|------|------|------|
| 🟢 A | 1 | 时效高+产出高 |
| 🟡 B | 5 | 时效高但产出低 |
| 🟠 C | 2 | 过时但有匹配关键词 |
| 🔴 D | 0 | 过时且无匹配 |

## 1. A类源 (keep_primary)

| source_name | total | in_win | near_win | old | matched_kw | cleaned | yield | old_ratio | recent_ratio |
|-------------|-------|--------|----------|-----|------------|---------|-------|-----------|--------------|
| rsshub_36kr_search_meituan_flash | 1 | 1 | 0 | 0 | 1 | 1 | 100.0% | 0% | 100% |

## 2. B类源 (keep_secondary_limit)

| source_name | total | in_win | near_win | old | matched_kw | cleaned | yield | old_ratio | recent_ratio |
|-------------|-------|--------|----------|-----|------------|---------|-------|-----------|--------------|
| rsshub_36kr_newsflashes | 20 | 20 | 0 | 0 | 2 | 0 | 0.0% | 0% | 100% |
| rsshub_36kr_search_beauty | 2 | 2 | 0 | 0 | 0 | 0 | 0.0% | 0% | 100% |
| rsshub_36kr_search_instant_retail | 2 | 2 | 0 | 0 | 1 | 0 | 0.0% | 0% | 100% |
| rsshub_36kr_search_taobao_flash | 4 | 4 | 0 | 0 | 2 | 0 | 0.0% | 0% | 100% |
| rsshub_huxiu_search_douyin_hour | 2 | 1 | 1 | 0 | 1 | 0 | 0.0% | 0% | 100% |

## 3. C类源 (reference_only)

| source_name | total | in_win | near_win | old | matched_kw | cleaned | yield | old_ratio | recent_ratio |
|-------------|-------|--------|----------|-----|------------|---------|-------|-----------|--------------|
| rsshub_36kr_search_watsons | 20 | 1 | 0 | 19 | 7 | 0 | 0.0% | 95% | 5% |
| rsshub_huxiu_search_watsons | 20 | 0 | 0 | 20 | 20 | 0 | 0.0% | 100% | 0% |

## 4. D类源 (disable_candidate)

> 无 D类源

## 5. old_ratio 最高的前10个源

| # | 源 | total | old_ratio | recent_ratio | grade |
|---|-----|-------|-----------|--------------|-------|
| 1 | rsshub_huxiu_search_watsons | 20 | 100.0% | 0.0% | 🟠 C |
| 2 | rsshub_36kr_search_watsons | 20 | 95.0% | 5.0% | 🟠 C |
| 3 | rsshub_36kr_newsflashes | 20 | 0.0% | 100.0% | 🟡 B |
| 4 | rsshub_36kr_search_beauty | 2 | 0.0% | 100.0% | 🟡 B |
| 5 | rsshub_36kr_search_instant_retail | 2 | 0.0% | 100.0% | 🟡 B |
| 6 | rsshub_36kr_search_meituan_flash | 1 | 0.0% | 100.0% | 🟢 A |
| 7 | rsshub_36kr_search_taobao_flash | 4 | 0.0% | 100.0% | 🟡 B |
| 8 | rsshub_huxiu_search_douyin_hour | 2 | 0.0% | 100.0% | 🟡 B |

## 6. cleaned_yield 最高的前10个源

| # | 源 | total | cleaned | yield_rate | grade |
|---|-----|-------|---------|------------|-------|
| 1 | rsshub_36kr_search_meituan_flash | 1 | 1 | 100.0% | 🟢 A |
| 2 | rsshub_36kr_newsflashes | 20 | 0 | 0.0% | 🟡 B |
| 3 | rsshub_36kr_search_beauty | 2 | 0 | 0.0% | 🟡 B |
| 4 | rsshub_36kr_search_instant_retail | 2 | 0 | 0.0% | 🟡 B |
| 5 | rsshub_36kr_search_taobao_flash | 4 | 0 | 0.0% | 🟡 B |
| 6 | rsshub_36kr_search_watsons | 20 | 0 | 0.0% | 🟠 C |
| 7 | rsshub_huxiu_search_douyin_hour | 2 | 0 | 0.0% | 🟡 B |
| 8 | rsshub_huxiu_search_watsons | 20 | 0 | 0.0% | 🟠 C |

## 7. 建议降级为 reference_only 的源

- **rsshub_36kr_search_watsons**: old_ratio=95.0%, matched_kw=7, total=20
- **rsshub_huxiu_search_watsons**: old_ratio=100.0%, matched_kw=20, total=20

## 8. 建议禁用的源

> 无需禁用

## 附录：全源明细

| source_name | total | in_win | near_win | old | matched_kw | cleaned | ref | rejected | old_r | recent_r | yield | grade | rec |
|-------------|-------|--------|----------|-----|------------|---------|-----|---------|-------|----------|-------|-------|-----|
| rsshub_36kr_newsflashes | 20 | 20 | 0 | 0 | 2 | 0 | 0 | 20 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_36kr_search_beauty | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_36kr_search_instant_retail | 2 | 2 | 0 | 0 | 1 | 0 | 0 | 2 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_36kr_search_meituan_flash | 1 | 1 | 0 | 0 | 1 | 1 | 0 | 0 | 0% | 100% | 100.0% | 🟢 A | keep_primary |
| rsshub_36kr_search_taobao_flash | 4 | 4 | 0 | 0 | 2 | 0 | 0 | 4 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_36kr_search_watsons | 20 | 1 | 0 | 19 | 7 | 0 | 0 | 20 | 95% | 5% | 0.0% | 🟠 C | reference_only |
| rsshub_huxiu_search_douyin_hour | 2 | 1 | 1 | 0 | 1 | 0 | 0 | 2 | 0% | 100% | 0.0% | 🟡 B | keep_secondary_limit |
| rsshub_huxiu_search_watsons | 20 | 0 | 0 | 20 | 20 | 0 | 10 | 10 | 100% | 0% | 0.0% | 🟠 C | reference_only |
