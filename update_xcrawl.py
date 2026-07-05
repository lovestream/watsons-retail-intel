#!/usr/bin/env python3
"""Update XCrawl sources in sources.yaml with news-tracking queries."""
import yaml

with open('config/sources.yaml', 'r') as f:
    sources = yaml.safe_load(f)

new_xcrawl = [
    # ─── 即时零售热点追踪（最高优先级） ───
    {"id": "xcrawl_01", "name": "XCrawl-热点-即时零售最新", "query": "即时零售 最新", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 120, "max_results": 10},
    {"id": "xcrawl_02", "name": "XCrawl-热点-即时零售动态", "query": "即时零售 动态", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 120, "max_results": 10},
    {"id": "xcrawl_03", "name": "XCrawl-热点-即时零售万亿市场", "query": "即时零售 万亿", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 115, "max_results": 10},
    {"id": "xcrawl_04", "name": "XCrawl-热点-美团即时零售", "query": "美团 即时零售", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 115, "max_results": 10},
    {"id": "xcrawl_05", "name": "XCrawl-热点-京东到家即时零售", "query": "京东到家 即时零售", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 115, "max_results": 10},
    {"id": "xcrawl_06", "name": "XCrawl-热点-淘宝闪购即时零售", "query": "淘宝闪购 即时零售", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 115, "max_results": 10},
    {"id": "xcrawl_07", "name": "XCrawl-热点-美团的闪购闪电仓", "query": "美团闪购 闪电仓", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 110, "max_results": 10},
    {"id": "xcrawl_08", "name": "XCrawl-热点-美团京东阿里即时零售", "query": "美团 京东 阿里 即时零售", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 110, "max_results": 10},
    {"id": "xcrawl_09", "name": "XCrawl-热点-小象超市即时配送", "query": "小象超市 即时配送", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 110, "max_results": 10},
    {"id": "xcrawl_10", "name": "XCrawl-热点-外卖大战即时零售", "query": "外卖大战 即时零售", "category": "热点", "role": "news_tracking", "tier": "tier1_core", "daily_priority": 110, "max_results": 10},
    # ─── 屈臣氏核心 ───
    {"id": "xcrawl_11", "name": "XCrawl-屈臣氏-屈臣氏最新动态", "query": "屈臣氏 最新", "category": "屈臣氏", "role": "core_search", "tier": "tier1_core", "daily_priority": 100, "max_results": 10},
    {"id": "xcrawl_12", "name": "XCrawl-屈臣氏-屈臣氏门店即时零售", "query": "屈臣氏 门店 即时零售", "category": "屈臣氏", "role": "core_search", "tier": "tier1_core", "daily_priority": 100, "max_results": 10},
    {"id": "xcrawl_13", "name": "XCrawl-屈臣氏-Watsons中国扩张", "query": "屈臣氏 开店 扩张 中国", "category": "屈臣氏", "role": "core_search", "tier": "tier1_core", "daily_priority": 100, "max_results": 10},
    {"id": "xcrawl_14", "name": "XCrawl-屈臣氏-屈臣氏美妆到家", "query": "屈臣氏 美妆 到家 线上", "category": "屈臣氏", "role": "core_search", "tier": "tier1_core", "daily_priority": 95, "max_results": 10},
    # ─── 美妆个护即时零售 ───
    {"id": "xcrawl_15", "name": "XCrawl-美妆-美妆即时零售最新", "query": "美妆 即时零售 最新", "category": "美妆个护", "role": "core_search", "tier": "tier1_core", "daily_priority": 100, "max_results": 10},
    {"id": "xcrawl_16", "name": "XCrawl-美妆-美妆个护外卖到家", "query": "美妆 个护 外卖 到家", "category": "美妆个护", "role": "core_search", "tier": "tier1_core", "daily_priority": 95, "max_results": 10},
    {"id": "xcrawl_17", "name": "XCrawl-美妆-美妆前置仓闪电仓", "query": "美妆 前置仓 闪电仓", "category": "美妆个护", "role": "core_search", "tier": "tier1_core", "daily_priority": 90, "max_results": 10},
    # ─── 竞品动态 ───
    {"id": "xcrawl_18", "name": "XCrawl-竞品-丝芙兰最新动态", "query": "丝芙兰 最新 动态 中国", "category": "竞品", "role": "competitor_signal", "tier": "tier2_supplement", "daily_priority": 80, "max_results": 8},
    {"id": "xcrawl_19", "name": "XCrawl-竞品-名创优品美妆零售", "query": "名创优品 美妆 零售 最新", "category": "竞品", "role": "competitor_signal", "tier": "tier2_supplement", "daily_priority": 80, "max_results": 8},
    {"id": "xcrawl_20", "name": "XCrawl-竞品-话梅调色师美妆集合店", "query": "话梅 调色师 美妆 集合店", "category": "竞品", "role": "competitor_signal", "tier": "tier2_supplement", "daily_priority": 80, "max_results": 8},
    # ─── 品牌渠道 ───
    {"id": "xcrawl_21", "name": "XCrawl-品牌-美妆品牌即时零售渠道", "query": "美妆 品牌 即时零售 渠道", "category": "品牌", "role": "core_search", "tier": "tier2_supplement", "daily_priority": 80, "max_results": 8},
    {"id": "xcrawl_22", "name": "XCrawl-品牌-护肤品即时配送最新", "query": "护肤品 即时配送 线上 最新", "category": "品牌", "role": "core_search", "tier": "tier2_supplement", "daily_priority": 80, "max_results": 8},
    # ─── 渠道策略 ───
    {"id": "xcrawl_23", "name": "XCrawl-渠道-抖音电商美妆即时零售", "query": "抖音 美妆 即时零售 电商", "category": "渠道", "role": "core_search", "tier": "tier2_supplement", "daily_priority": 75, "max_results": 8},
    {"id": "xcrawl_24", "name": "XCrawl-渠道-小红书美妆零售", "query": "小红书 美妆 零售 电商", "category": "渠道", "role": "core_search", "tier": "tier2_supplement", "daily_priority": 75, "max_results": 8},
    {"id": "xcrawl_25", "name": "XCrawl-渠道-私域美妆零售会员", "query": "私域 美妆 零售 会员 最新", "category": "渠道", "role": "core_search", "tier": "tier2_supplement", "daily_priority": 75, "max_results": 8},
    # ─── 行业趋势 ───
    {"id": "xcrawl_26", "name": "XCrawl-行业-即时零售行业报告数据", "query": "即时零售 行业 报告 数据", "category": "行业", "role": "background", "tier": "tier3_archive", "daily_priority": 60, "max_results": 8},
    {"id": "xcrawl_27", "name": "XCrawl-行业-美妆零售消费趋势", "query": "美妆 零售 消费 趋势 2026", "category": "行业", "role": "background", "tier": "tier3_archive", "daily_priority": 60, "max_results": 8},
    {"id": "xcrawl_28", "name": "XCrawl-行业-本地生活O2O最新动态", "query": "本地生活 O2O 最新 动态", "category": "行业", "role": "background", "tier": "tier3_archive", "daily_priority": 60, "max_results": 8},
]

# Add required fields
for s in new_xcrawl:
    s["enabled"] = True
    s["method"] = "xcrawl"
    s["source_type"] = "xcrawl"
    s["allow_old"] = False

# Remove old xcrawl from rsshub_sources
rsshub = sources.get("rsshub_sources", [])
new_rsshub = [s for s in rsshub if not (s.get("id", "").startswith("xcrawl_") and s.get("method") == "xcrawl")]
old_xcrawl_in_rsshub = len(rsshub) - len(new_rsshub)

# Remove old xcrawl from other_sources
other = sources.get("other_sources", [])
new_other = [s for s in other if not s.get("id", "").startswith("xcrawl_")]
old_xcrawl_in_other = len(other) - len(new_other)

# Add new xcrawl sources
new_other.extend(new_xcrawl)

sources["rsshub_sources"] = new_rsshub
sources["other_sources"] = new_other

with open("config/sources.yaml", "w") as f:
    yaml.dump(sources, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f"Removed {old_xcrawl_in_rsshub} old xcrawl from rsshub, {old_xcrawl_in_other} from other_sources")
print(f"Added {len(new_xcrawl)} new XCrawl sources to other_sources")
print(f"rsshub_sources: {len(new_rsshub)}, other_sources: {len(new_other)}")