#!/usr/bin/env python3
"""Fix generate_platform_section with proper string handling"""

FILE = "skills/generate_daily_report/generate_daily_report.py"

with open(FILE) as f:
    lines = f.readlines()

# Find the function start
start_idx = None
for i, line in enumerate(lines):
    if 'def generate_platform_section' in line:
        start_idx = i
        break

# Find the end (next top-level def)
end_idx = start_idx + 1
while end_idx < len(lines):
    line = lines[end_idx]
    if line.strip() and not line[0].isspace():
        break
    if line.startswith('def ') or line.startswith('class '):
        break
    if 'return' in lines[end_idx] and 'sections.values' in lines[end_idx]:
        end_idx += 1
        break
    end_idx += 1

print(f"Replacing lines {start_idx+1} to {end_idx}")

# Write new function using triple quotes for complex strings
new_func = r'''def generate_platform_section(events: List[dict]) -> str:
    """生成03 平台变化解读（按优先级加权）。"""
    sections = {}
    for section_name, section_cfg in PLATFORM_SECTIONS.items():
        # 支持新旧两种配置格式
        if isinstance(section_cfg, dict):
            keywords = section_cfg["keywords"]
            priority = section_cfg.get("priority", 2)
            max_items = section_cfg.get("max_items", 3)
        else:
            keywords = section_cfg
            priority = 2
            max_items = 3

        relevant = []
        for ev in events:
            text = json.dumps(ev, ensure_ascii=False)
            if any(kw in text for kw in keywords):
                relevant.append(ev)
        if relevant:
            relevant.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
            priority_map = {1: "🔴 核心渠道", 2: "🟡 战略渠道", 3: "⚪ 观察渠道"}
            priority_label = priority_map.get(priority, "")
            section_lines = [f"### {section_name} {priority_label}", ""]
            for ev in relevant[:max_items]:
                title = ev.get("title", "未知")
                summary = ev.get("summary", ev.get("llm_summary", ""))
                if len(summary) > 150:
                    summary = summary[:147] + "..."
                channels = ev.get("affected_channels", [])
                section_lines.append(f"- **{title}**")
                if summary:
                    section_lines.append(f"  {summary}")
                if channels:
                    ch_str = "、".join(channels[:3])
                    section_lines.append(f"  涉及渠道：{ch_str}")
                section_lines.append("")
            sections[section_name] = "\n".join(section_lines)
        else:
            sections[section_name] = f"### {section_name}\n\n今日未发现足够高质量新增信号。\n"
    return "\n".join(sections.values())
'''

lines[start_idx:end_idx] = [new_func]

with open(FILE, "w") as f:
    f.writelines(lines)

# Verify
import ast
with open(FILE) as f:
    content = f.read()
ast.parse(content)
print("COMPILES OK")
