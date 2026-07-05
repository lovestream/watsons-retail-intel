#!/usr/bin/env python3
"""Fix sources.yaml: enable time-based feeds, reduce search redundancy."""

import yaml

with open('config/sources.yaml') as f:
    cfg = yaml.safe_load(f)

# ── 统一的关键词列表（用于 collection_keywords） ──
RETAIL_AND_BEAUTY_KW = [
    "即时零售", "美团闪购", "京东秒送", "淘宝闪购", "抖音小时达", "饿了么",
    "前置仓", "闪购", "到家", "配送",
    "屈臣氏", "丝芙兰", "名创优品", "调色师",
    "美妆", "个护", "护肤", "防晒", "彩妆", "面膜",
    "电商", "零售", "门店", "线下", "O2O", "本地生活",
    "天猫", "京东", "抖音电商", "小红书",
    "大众点评", "美团到店", "到店", "团购", "核销",
    "会员", "私域", "复购", 
    "精华", "面霜", "玻尿酸", "视黄醇", "烟酰胺",
]

# ── Enable and enhance time-based feeds ──
FEED_CONFIGS = {
    "rsshub_huxiu_article": {
        "enabled": True,
        "collection_keywords": RETAIL_AND_BEAUTY_KW,
        "filterout_days": 1,
    },
    "rsshub_huxiu_consumer_channel": {
        "enabled": True,
        "collection_keywords": RETAIL_AND_BEAUTY_KW,
        "filterout_days": 3,
    },
    "rsshub_huxiu_business_channel": {
        "enabled": True,
        "collection_keywords": RETAIL_AND_BEAUTY_KW,
        "filterout_days": 3,
    },
    "rsshub_latepost": {
        "enabled": True,
        "collection_keywords": RETAIL_AND_BEAUTY_KW,
        "filterout_days": 3,
    },
}

# ── Disable redundant searches ──
SEARCHES_TO_DISABLE = [
    "rsshub_huxiu_search_meituan_flash",
    "rsshub_huxiu_search_jd_instant",
    "rsshub_huxiu_search_taobao_flash",
    "rsshub_huxiu_search_douyin_hour",
    "rsshub_huxiu_search_douyin_ecom",
    "rsshub_huxiu_search_convenience_store",
    "rsshub_huxiu_search_jd",
    "rsshub_huxiu_search_tmall",
]

for s in cfg.get('rsshub_sources', []):
    sid = s.get('id', '')

    if sid in FEED_CONFIGS:
        for k, v in FEED_CONFIGS[sid].items():
            s[k] = v
        print(f"  ENABLED: {sid}")

    if sid in SEARCHES_TO_DISABLE:
        s['enabled'] = False
        print(f"  DISABLED: {sid}")

# Add filterout_days and collection_keywords to remaining search routes
for s in cfg.get('rsshub_sources', []):
    if s.get('enabled', True) and s.get('method') == 'rsshub' and 'search' in s.get('route', ''):
        s['filterout_days'] = 3
        if 'collection_keywords' not in s:
            s['collection_keywords'] = RETAIL_AND_BEAUTY_KW

with open('config/sources.yaml', 'w') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print("\nDone.")
