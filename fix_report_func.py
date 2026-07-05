#!/usr/bin/env python3
"""Fix the generate_platform_section function with proper escape sequences"""

FILE = "skills/generate_daily_report/generate_daily_report.py"

with open(FILE) as f:
    lines = f.readlines()

# Find the function and fix it
new_func_lines = [
    'def generate_platform_section(events: List[dict]) -> str:\n',
    '    """生成03 平台变化解读（按优先级加权）。"""\n',
    '    sections = {}\n',
    '    for section_name, section_cfg in PLATFORM_SECTIONS.items():\n',
    '        # 支持新旧两种配置格式\n',
    '        if isinstance(section_cfg, dict):\n',
    '            keywords = section_cfg["keywords"]\n',
    '            priority = section_cfg.get("priority", 2)\n',
    '            max_items = section_cfg.get("max_items", 3)\n',
    '        else:\n',
    '            keywords = section_cfg\n',
    '            priority = 2\n',
    '            max_items = 3\n',
    '\n',
    '        relevant = []\n',
    '        for ev in events:\n',
    '            text = json.dumps(ev, ensure_ascii=False)\n',
    '            if any(kw in text for kw in keywords):\n',
    '                relevant.append(ev)\n',
    '        if relevant:\n',
    '            relevant.sort(key=lambda x: x.get("signal_score", 0), reverse=True)\n',
    '            # 优先级标记\n',
    '            priority_label = {1: "\\U0001f534 核心渠道", 2: "\\U0001f7e1 战略渠道", 3: "\\u26aa 观察渠道"}.get(priority, "")\n',
    '            lines = [f"### {section_name} {priority_label}", ""]\n',
    '            for ev in relevant[:max_items]:\n',
    '                title = ev.get("title", "未知")\n',
    '                summary = ev.get("summary", ev.get("llm_summary", ""))\n',
    '                if len(summary) > 150:\n',
    '                    summary = summary[:147] + "..."\n',
    '                channels = ev.get("affected_channels", [])\n',
    '                lines.append(f"- **{title}**")\n',
    '                if summary:\n',
    '                    lines.append(f"  {summary}")\n',
    '                if channels:\n',
    '                    lines.append(f"  涉及渠道：{\'、\'.join(channels[:3])}")\n',
    '                lines.append("")\n',
    '            sections[section_name] = "\\n".join(lines)\n',
    '        else:\n',
    '            sections[section_name] = f"### {section_name}\\n\\n今日未发现足够高质量新增信号。\\n"\n',
    '    return "\\n".join(sections.values())\n',
]

# Find the start of the function
start_idx = None
for i, line in enumerate(lines):
    if 'def generate_platform_section' in line:
        start_idx = i
        break

if start_idx is None:
    print("ERROR: Function not found")
    exit(1)

# Find the end of the function (next def or class at same indent)
end_idx = start_idx + 1
while end_idx < len(lines):
    line = lines[end_idx]
    if line.strip() and not line.startswith(' ') and not line.startswith('\t'):
        break
    if line.startswith('def ') or line.startswith('class '):
        break
    end_idx += 1

# Also include the return line
for i in range(start_idx, end_idx):
    if 'return' in lines[i] and 'sections.values' in lines[i]:
        end_idx = i + 1
        break

print(f"Replacing lines {start_idx+1}-{end_idx}")
print(f"Old function ({end_idx - start_idx} lines):")
for i in range(start_idx, min(start_idx+3, end_idx)):
    print(f"  {lines[i].rstrip()}")
print(f"  ...")

# Replace
lines[start_idx:end_idx] = new_func_lines

with open(FILE, "w") as f:
    f.writelines(lines)

print(f"New function ({len(new_func_lines)} lines)")

# Verify
import ast
with open(FILE) as f:
    content = f.read()
ast.parse(content)
print("COMPILES OK")
