#!/usr/bin/env python3
"""Fix filter_relevant_articles.py: CloakBrowser uncertain_date → main"""

path = 'skills/filter_relevant_articles/filter_relevant_articles.py'
with open(path) as f:
    content = f.read()

old = """    if time_status == "uncertain_date":
        # CloakBrowser 文章：日期提取失败，但采集端已过滤>3天旧文
        # 核心命中 → reference（保留但不进 cleaned）
        if core_hit:
            return _noise_downgrade("reference", f"uncertain_date+core_{core_type}→reference")
        # 高分文章 → reference
        if rule_score >= 4:
            return _noise_downgrade("reference", f"uncertain_date score={rule_score}>=4→reference")
        # 低分文章 → reject
        return "reject", f"uncertain_date score={rule_score}<4→reject\""""

new = """    if time_status == "uncertain_date":
        # CloakBrowser 文章：日期提取失败，但采集端已过滤>3天旧文
        # V5: CloakBrowser 是最可靠的新鲜来源，核心命中应直接进 main
        is_cloakbrowser = bool(
            article.get("collector") == "cloakbrowser" or
            "cloakbrowser" in article.get("_source_file", "")
        )
        if is_cloakbrowser and core_hit and rule_score >= 3:
            return _noise_downgrade("main", f"cloakbrowser+uncertain_date+core_{core_type} score={rule_score}>=3→main")
        if core_hit and rule_score >= 5:
            return _noise_downgrade("main", f"uncertain_date+core_{core_type} score={rule_score}>=5→main")
        if core_hit:
            return _noise_downgrade("reference", f"uncertain_date+core_{core_type}→reference")
        if rule_score >= 4:
            return _noise_downgrade("reference", f"uncertain_date score={rule_score}>=4→reference")
        return "reject", f"uncertain_date score={rule_score}<4→reject\""""

if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)
    print("OK: CloakBrowser uncertain_date fix applied")
else:
    print("FAIL: old text not found")

# Also disable time-based feeds
import yaml
with open('config/sources.yaml') as f:
    cfg = yaml.safe_load(f)

FEEDS_TO_DISABLE = [
    "rsshub_huxiu_article",
    "rsshub_huxiu_consumer_channel", 
    "rsshub_huxiu_business_channel",
    "rsshub_latepost",
]
for s in cfg.get('rsshub_sources', []):
    if s.get('id') in FEEDS_TO_DISABLE:
        s['enabled'] = False
        print(f"DISABLED: {s['id']}")

with open('config/sources.yaml', 'w') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
