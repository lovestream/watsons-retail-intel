#!/usr/bin/env python3
"""Re-enable RSSHub huxiu routes."""
import yaml, sys

cfg_path = sys.argv[1] if len(sys.argv) > 1 else 'config/sources.yaml'
with open(cfg_path) as f:
    cfg = yaml.safe_load(f)

for src in cfg.get('sources', []):
    sid = src.get('id', '')
    # Re-enable huxiu article and channel feeds
    if sid in ('rsshub_huxiu_article', 'rsshub_huxiu_consumer_channel'):
        src['enabled'] = True
        print(f"Enabled: {sid}")
    # Re-enable huxiu search routes
    if sid.startswith('rsshub_huxiu_search') and not src.get('enabled'):
        src['enabled'] = True
        print(f"Enabled: {sid}")

with open(cfg_path, 'w') as f:
    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
print("Done")
