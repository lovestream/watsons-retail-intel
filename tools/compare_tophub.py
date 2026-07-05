#!/usr/bin/env python3
"""对比 tophub 搜到的文章 vs 我们 pipeline 实际采到的"""
import json, sys

# Load what we collected
merged = json.load(open(sys.argv[1]))['articles']
cleaned = json.load(open(sys.argv[2]))

# Tophub articles from user's search (May 17 window, within our collection range)
tophub_finds = [
    {"title": "国金证券：阿里巴巴-W维持买入评级 AI业务加速增长", "source": "sina", "date": "2026-05-17"},
    {"title": "阿里Q4电商CMR同比增长8% 即时零售强劲增长57%", "source": "msn/google", "date": "2026-05-17"},
    {"title": "即时零售万亿风口 聚合配送站长成创业新选择", "source": "9kd.com", "date": "2026-05-16"},
    {"title": "阿里健康统一指挥B2C与O2O医药业务 补上即时零售拼图", "source": "sina/google", "date": "2026-05-17"},
    # May 18 afternoon (outside our window, for reference)
    {"title": "茅台调整i茅台和门店购酒时间 或瞄准即时零售", "source": "huxiu", "date": "2026-05-18"},
    {"title": "淘宝闪购年度活跃骑士超千万", "source": "huxiu/sina", "date": "2026-05-18"},
    {"title": "东方甄选入局即时零售 将登陆淘宝美团京东", "source": "sohu/ebrun", "date": "2026-05-18"},
    {"title": "美团闪购推出酒水即时零售月度专区", "source": "ebrun", "date": "2026-05-18"},
]

print("=" * 60)
print("对比：tophub找到的文章 vs 我们pipeline实际采到")
print("=" * 60)

for art in tophub_finds:
    title = art['title']
    source = art['source']
    date = art['date']
    
    # Check merged
    in_merged = False
    in_cleaned = False
    reject_reason = ""
    
    for a in merged:
        t = a.get('title','')
        if any(kw in t for kw in title[:20].split()):
            in_merged = True
            break
    
    if isinstance(cleaned, list):
        for a in cleaned:
            t = a.get('title','')
            if any(kw in t for kw in title[:20].split()):
                in_cleaned = True
                break
    
    window = "✅窗口内" if date == "2026-05-17" or (date == "2026-05-16" and "20:02" in art.get('time','')) else "❌窗口外"
    
    status = "✅已采+清洗" if in_cleaned else ("⚠️已采未清洗" if in_merged else "❌未采到")
    print(f"[{date}] {source.ljust(15)} {title[:50]}")
    print(f"  {window} | {status}")
    if not in_merged:
        print(f"  → 漏采！来源{source}不在我们采集源中")
    print()

# Summary
window_articles = [a for a in tophub_finds if a['date'] in ('2026-05-17','2026-05-16')]
missed = [a for a in window_articles if not any(
    any(kw in m.get('title','') for kw in a['title'][:20].split())
    for m in merged
)]
print(f"窗口内文章: {len(window_articles)}篇, 漏采: {len(missed)}篇")
print(f"漏采来源: {set(a['source'] for a in missed)}")
