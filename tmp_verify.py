import ast
ast.parse(open('/app/working/projects/watsons-retail-intel/skills/broad_search_discovery/broad_search_discovery.py').read())
print('✓ Syntax OK')
import yaml
yaml.safe_load(open('/app/working/projects/watsons-retail-intel/config/source_packs.yaml'))
print('✓ source_packs.yaml OK')