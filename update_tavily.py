#!/usr/bin/env python3
import yaml

with open('config/sources.yaml', 'r') as f:
    sources = yaml.safe_load(f)

rsshub = sources.get('rsshub_sources', [])

# Update tavily queries - make them more news-tracking
tavily_updates = {
    'tavily_01': {'query': '即时零售 最新 动态', 'name': 'Tavily-即时零售最新动态'},
    'tavily_02': {'query': '美团 京东 阿里 即时零售 大战', 'name': 'Tavily-即时零售三国杀'},
    'tavily_03': {'query': '美妆 个护 外卖 到家 最新', 'name': 'Tavily-美妆个护即时零售'},
    'tavily_04': {'query': '屈臣氏 最新 即时零售 门店', 'name': 'Tavily-屈臣氏最新动态'},
}

for s in rsshub:
    if s.get('id') in tavily_updates:
        upd = tavily_updates[s['id']]
        s['query'] = upd['query']
        s['name'] = upd['name']

with open('config/sources.yaml', 'w') as f:
    yaml.dump(sources, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print("Updated Tavily queries:")
for s in rsshub:
    if s.get('id') in tavily_updates:
        print(f"  {s['id']}: {s['query']}")