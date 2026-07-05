#!/usr/bin/env python3
"""Final sources.yaml fix based on live RSSHub route testing."""

import yaml, copy

with open('config/sources.yaml') as f:
    cfg = yaml.safe_load(f)

# ── 基于实测的搜索路由决策 ──
# 新鲜度实测 (2026-05-18):
#   美妆: May 15 ✅    防晒: May 11 ✅    电商: May 14 ✅    零售: May 14 ✅
#   即时零售: May 6 ⚠️   小红书: May 1 ⚠️
#   屈臣氏: Mar ❌   护肤: Feb ❌   个护: Sep 2023 ❌   面膜: Apr 2024 ❌

# 🔴 死路由（无近期内容）→ 禁用
DEAD_SEARCHES = [
    "rsshub_huxiu_search_personal_care",   # 个护 → 最新 Sep 2023
    "rsshub_huxiu_search_skincare",        # 护肤 → 最新 Feb 2026
    "rsshub_huxiu_search_sunscreen",       # 防晒 → 最新 May 11 ⚠️ 保留但降级
    "rsshub_huxiu_search_sephora",         # 丝芙兰 → 最新 Dec 2025
    "rsshub_huxiu_search_wowcolour",       # WOW COLOUR → 无结果
    "rsshub_huxiu_search_yanli",           # 妍丽 → 无结果
    "rsshub_huxiu_search_local_life",      # 本地生活 → 最新 Mar 2026
    "rsshub_huxiu_search_dianping",        # 大众点评 → 无新鲜内容
    "rsshub_huxiu_search_douyin_local",    # 抖音本地生活 → 无新鲜内容
]

# 🟡 保留但加宽窗口（14天）
WIDE_WINDOW_SEARCHES = [
    "rsshub_huxiu_search_instant_retail",  # 即时零售 → 最新 May 6
    "rsshub_huxiu_search_xiaohongshu",     # 小红书 → 最新 May 1
]

# 🟢 高产出路由（7天窗口足够）
PRODUCTIVE_SEARCHES = [
    "rsshub_huxiu_search_beauty",          # 美妆 → 最新 May 15
    "rsshub_huxiu_search_watsons",         # 屈臣氏 → 保留（虽然旧，但关键）
    "rsshub_huxiu_search_miniso",          # 名创优品 → 保留
]

for s in cfg.get('rsshub_sources', []):
    sid = s.get('id', '')

    if sid in DEAD_SEARCHES:
        if sid == "rsshub_huxiu_search_sunscreen":
            s['filterout_days'] = 14  # 防晒保持但加宽窗口
            s['enabled'] = True
            print(f"  WIDENED: {sid} (14-day window)")
        else:
            s['enabled'] = False
            print(f"  DISABLED: {sid}")

    elif sid in WIDE_WINDOW_SEARCHES:
        s['enabled'] = True
        s['filterout_days'] = 14
        print(f"  ENABLED+14d: {sid}")

    elif sid in PRODUCTIVE_SEARCHES:
        s['enabled'] = True
        s['filterout_days'] = 7
        print(f"  ENABLED+7d: {sid}")

    # ── 时间型 feed：不设 collection_keywords，让下游 filter 全量处理 ──
    elif sid in ("rsshub_huxiu_article", "rsshub_huxiu_consumer_channel",
                  "rsshub_huxiu_business_channel", "rsshub_latepost"):
        s['enabled'] = True
        s['filterout_days'] = 1  # 时间feed只看当天
        # 移除 collection_keywords — 让下游 filter 做全文本匹配
        s.pop('collection_keywords', None)
        print(f"  ENABLED (no pre-filter): {sid} (filterout=1d)")

    # 清理所有搜索路由的 collection_keywords（搜索路由本身就是关键词过滤的）
    if s.get('enabled') and 'search' in s.get('route', '') and 'collection_keywords' in s:
        del s['collection_keywords']

with open('config/sources.yaml', 'w') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print("\nDone.")
print("  Feeds (no pre-filter): huxiu_article, huxiu_consumer, huxiu_business, latepost")
print("  Searches (7d): beauty, watsons, miniso")
print("  Searches (14d): instant_retail, xiaohongshu, sunscreen")
