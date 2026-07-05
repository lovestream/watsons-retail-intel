#!/usr/bin/env python3
"""Add judgment labels, risk/opportunity sections, and 8 daily questions"""

FILE = "skills/generate_daily_report/generate_daily_report.py"

with open(FILE) as f:
    content = f.read()

# 1. Add judgment label function before generate_report_by_rules
judgment_func = '''
def assign_judgment_labels(event: dict) -> List[str]:
    """为事件分配判断标签（A/B/C/R/K/X）。"""
    labels = []
    title = event.get("event_title", "") + " " + event.get("title", "")
    summary = event.get("summary", "") + " " + event.get("llm_summary", "")
    combined = title + " " + summary
    priority = event.get("priority", "")
    channels = event.get("affected_channels", [])
    
    # A类：必须关注 — 影响平台流量/费用/规则/销售结构
    a_keywords = ["规则", "算法", "流量", "佣金", "费率", "政策", "下架", "处罚", 
                  "罚款", "调整", "变更", "新规", "门槛", "准入", "排名"]
    if any(kw in combined for kw in a_keywords) or priority in ("P0", "P1"):
        labels.append("A")
    
    # B类：本周跟进 — 有业务机会
    b_keywords = ["机会", "入驻", "合作", "招商", "试点", "活动", "大促", "618",
                  "双11", "营销", "资源", "补贴", "扶持", "新品牌"]
    if any(kw in combined for kw in b_keywords):
        labels.append("B")
    
    # C类：趋势观察
    c_keywords = ["趋势", "增长", "份额", "市场", "行业", "报告", "数据", "调研"]
    if any(kw in combined for kw in c_keywords) and "A" not in labels:
        labels.append("C")
    
    # R类：风险预警
    r_keywords = ["风险", "下滑", "下降", "亏损", "关闭", "裁员", "处罚", "投诉",
                  "负面", "危机", "涨价", "缺货", "断货", "合规", "假货"]
    if any(kw in combined for kw in r_keywords):
        labels.append("R")
    
    # K类：竞对可借鉴
    k_keywords = ["竞对", "丝芙兰", "妍丽", "WOW", "名创优品", "便利蜂", "盒马",
                  "学习", "借鉴", "案例", "最佳实践", "创新", "打法"]
    if any(kw in combined for kw in k_keywords):
        labels.append("K")
    
    # X类：需跨部门协同
    x_keywords = ["采购", "营运", "商品", "门店", "BD", "协同", "联合", "总部",
                  "供应链", "仓储", "物流", "系统", "技术"]
    if any(kw in combined for kw in x_keywords):
        labels.append("X")
    
    return labels if labels else ["C"]  # 默认为趋势观察


def format_judgment_label(labels: List[str]) -> str:
    """格式化判断标签为可读字符串。"""
    label_map = {
        "A": "A-必须关注", "B": "B-本周跟进", "C": "C-趋势观察",
        "R": "R-风险预警", "K": "K-竞对可借鉴", "X": "X-需跨部门",
    }
    return " ".join(label_map.get(l, l) for l in labels)


def generate_risk_section(events: List[dict]) -> str:
    """生成风险预警段落。"""
    risk_events = []
    for ev in events:
        labels = ev.get("judgment_labels", [])
        if "R" in labels:
            risk_events.append(ev)
    
    if not risk_events:
        return "今日暂无重大风险预警。建议持续关注平台规则变化和竞对动态。"
    
    lines = []
    for ev in risk_events[:3]:
        title = ev.get("event_title", ev.get("title", "未知"))
        summary = ev.get("summary", ev.get("llm_summary", ""))
        if len(summary) > 120:
            summary = summary[:117] + "..."
        impact = ev.get("impact_channels", ev.get("affected_channels", []))
        impact_str = "、".join(impact[:2]) if impact else "相关渠道"
        lines.append(f"- **{title}**")
        if summary:
            lines.append(f"  {summary}")
        lines.append(f"  影响渠道：{impact_str}")
        lines.append("")
    
    return "\\n".join(lines)


def generate_opportunity_section(events: List[dict]) -> str:
    """生成对屈臣氏的机会点段落。"""
    # 筛选有机会点的事件
    opp_events = []
    for ev in events:
        labels = ev.get("judgment_labels", [])
        priority = ev.get("priority", "")
        if "B" in labels or "K" in labels or priority in ("P0", "P1"):
            opp_events.append(ev)
    
    if not opp_events:
        return "今日暂无明确业务机会点。建议关注平台动态和竞对动作，等待更明确的信号。"
    
    lines = []
    for ev in opp_events[:5]:
        title = ev.get("event_title", ev.get("title", "未知"))
        summary = ev.get("summary", ev.get("llm_summary", ""))
        if len(summary) > 100:
            summary = summary[:97] + "..."
        channels = ev.get("affected_channels", [])
        labels = ev.get("judgment_labels", [])
        label_str = format_judgment_label(labels)
        
        lines.append(f"- **{title}** [{label_str}]")
        if summary:
            lines.append(f"  {summary}")
        if channels:
            lines.append(f"  涉及渠道：{'、'.join(channels[:3])}")
        
        # 添加行动建议
        action_keywords = {
            "入驻": "建议评估入驻可行性和ROI",
            "活动": "建议对接平台活动资源位",
            "规则": "建议立即研读规则变化影响",
            "补贴": "建议评估补贴政策利用空间",
            "竞对": "建议分析竞对策略并制定应对方案",
        }
        for kw, suggestion in action_keywords.items():
            if kw in title or kw in summary:
                lines.append(f"  💡 {suggestion}")
                break
        lines.append("")
    
    return "\\n".join(lines)


def generate_daily_eight_questions(events: List[dict], date_str: str) -> str:
    """生成每日必答8问。"""
    non_archive = [ev for ev in events if ev.get("priority") != "ARCHIVE"]
    p0_events = [ev for ev in non_archive if ev.get("priority") == "P0"]
    p1_events = [ev for ev in non_archive if ev.get("priority") == "P1"]
    risk_events = [ev for ev in non_archive if "R" in ev.get("judgment_labels", [])]
    opp_events = [ev for ev in non_archive if "B" in ev.get("judgment_labels", [])]
    
    # Q1: 今天哪个平台变化最值得关注？
    top_event = p0_events[0] if p0_events else (p1_events[0] if p1_events else None)
    q1 = top_event.get("event_title", "今日无高优先级事件") if top_event else "今日无高优先级事件"
    
    # Q2: 对哪个渠道影响最大？
    q2_channels = top_event.get("affected_channels", ["待确认"]) if top_event else ["待确认"]
    q2 = "、".join(q2_channels[:2])
    
    # Q3-4: 影响判断（基于关键词分析）
    q3 = "需结合具体事件分析流量分配影响"
    q4 = "需结合具体事件分析货盘和促销影响"
    if top_event:
        combined = top_event.get("event_title", "") + " " + top_event.get("summary", "")
        if any(kw in combined for kw in ["流量", "算法", "排名", "权重"]):
            q3 = "是，可能影响平台流量分配，建议密切关注"
        if any(kw in combined for kw in ["货盘", "促销", "SKU", "选品", "品类"]):
            q4 = "是，可能影响品类货盘和促销方式"
    
    # Q5: 竞对借鉴
    k_events = [ev for ev in non_archive if "K" in ev.get("judgment_labels", [])]
    q5 = k_events[0].get("event_title", "今日无明确竞对借鉴") if k_events else "今日无明确竞对借鉴"
    
    # Q6: 风险预警
    q6 = risk_events[0].get("event_title", "今日无重大风险预警") if risk_events else "今日无重大风险预警"
    
    # Q7: 周会讨论机会
    q7_items = [ev.get("event_title", "") for ev in opp_events[:3]]
    q7 = "、".join(q7_items) if q7_items else "今日暂无明确周会议题"
    
    # Q8: 今日最建议推动的一件事
    q8 = top_event.get("event_title", "建议复盘昨日执行情况") if top_event else "建议复盘昨日执行情况"
    
    return f"""**Q1: 今天哪个平台变化最值得关注？**
{q1}

**Q2: 这个变化对屈臣氏哪个渠道影响最大？**
{q2}

**Q3: 是否会影响平台流量分配？**
{q3}

**Q4: 是否会影响品类货盘和促销方式？**
{q4}

**Q5: 竞对有没有值得借鉴的动作？**
{q5}

**Q6: 有没有需要马上预警的风险？**
{q6}

**Q7: 有哪些机会可以放进本周周会讨论？**
{q7}

**Q8: 今天最建议推动团队做的一件事是什么？**
{q8}'''

'''

# Insert before generate_report_by_rules
marker = "def generate_report_by_rules("
idx = content.index(marker)
content = content[:idx] + judgment_func + content[idx:]

print("Added judgment functions")

# 2. Update generate_report_by_rules to use new structure
old_report = '''def generate_report_by_rules(events: List[dict], date_str: str,
                              unique_action_event: Optional[dict]) -> str:
    """规则模板生成完整日报。"""
    top_signals = select_top_signals(events, 3)
    non_archive = [ev for ev in events if ev.get("priority") != "ARCHIVE"]
    # 正文事件池：排除tracking（tracking仅用于09节）
    body_events = [ev for ev in non_archive
                   if ev.get("report_eligibility") != "tracking"]

    lines = [
        f"# 即时零售 × 个护美妆经营日报｜{date_str}",
        "",
        "---",
        "",
        "## 01 今日一句话判断",
        "",
        generate_one_line_summary(events, date_str),
        "",
        "---",
        "",
        "## 02 今日最值得关注的3个信号",
        "",
        generate_signal_section(top_signals),
        "",
        "---",
        "",
        "## 03 平台变化解读",
        "",
        generate_platform_section(body_events),
        "",
        "---",
        "",
        "## 04 竞对与品牌动作",
        "",
        generate_competitor_section(body_events),
        "",
        "---",
        "",
        "## 05 品类与场景机会",
        "",
        generate_category_section(body_events),
        "",
        "---",
        "",
        "## 06 对屈臣氏的经营提示",
        "",
        generate_tips_section(body_events),
        "",
        "---",
        "",
        "## 07 今日唯一建议动作",
        "",
        generate_unique_action(unique_action_event),
        "",
        "---",
        "",
        "## 08 明日追踪清单",
        "",
        generate_tracking_list(body_events),
    ]'''

new_report = '''def generate_report_by_rules(events: List[dict], date_str: str,
                              unique_action_event: Optional[dict]) -> str:
    """规则模板生成完整日报（V2: 按渠道权重+判断标签+8问）。"""
    top_signals = select_top_signals(events, 3)
    non_archive = [ev for ev in events if ev.get("priority") != "ARCHIVE"]
    body_events = [ev for ev in non_archive
                   if ev.get("report_eligibility") != "tracking"]
    
    # 为所有事件分配判断标签
    for ev in body_events:
        if "judgment_labels" not in ev:
            ev["judgment_labels"] = assign_judgment_labels(ev)
    
    # 按渠道分类事件
    instant_retail_events = []
    local_life_events = []
    traditional_ecom_events = []
    competitor_events = []
    
    for ev in body_events:
        combined = ev.get("event_title", "") + " " + ev.get("summary", "") + " " + ev.get("title", "")
        channels = ev.get("affected_channels", [])
        labels = ev.get("judgment_labels", [])
        
        # 竞对事件优先分类
        if "K" in labels or any(kw in combined for kw in ["丝芙兰", "妍丽", "WOW", "名创优品", "便利蜂", "盒马", "竞对"]):
            competitor_events.append(ev)
        # 即时零售
        elif any(kw in combined for kw in ["即时零售", "美团闪购", "京东秒送", "淘宝闪购", "抖音小时达", "饿了么", "到家", "前置仓", "闪电仓"]):
            instant_retail_events.append(ev)
        # 本地生活
        elif any(kw in combined for kw in ["本地生活", "大众点评", "抖音本地", "到店", "团购", "抖音本服"]):
            local_life_events.append(ev)
        # 传统电商
        elif any(kw in combined for kw in ["天猫", "京东超市", "京东自营", "抖音电商", "抖音商城"]):
            traditional_ecom_events.append(ev)
        else:
            # 默认按affected_channels分类
            if any("即时" in c or "到家" in c for c in channels):
                instant_retail_events.append(ev)
            elif any("本地" in c or "到店" in c for c in channels):
                local_life_events.append(ev)
            else:
                instant_retail_events.append(ev)  # 默认归入即时零售
    
    # 风险事件
    risk_events = [ev for ev in body_events if "R" in ev.get("judgment_labels", [])]
    # 机会事件
    opp_events = [ev for ev in body_events if "B" in ev.get("judgment_labels", []) or "K" in ev.get("judgment_labels", [])]
    
    lines = [
        f"# 即时零售 × 个护美妆经营日报｜{date_str}",
        "",
        "---",
        "",
        "## 01 今日一句话结论",
        "",
        generate_one_line_summary(events, date_str),
        "",
        "---",
        "",
        "## 02 今日三条必听",
        "",
        generate_signal_section(top_signals),
        "",
        "---",
        "",
        "## 03 即时零售重点变化（占比55-60%）",
        "",
        generate_platform_section(instant_retail_events),
        "",
        "---",
        "",
        "## 04 本地生活重点变化（占比20-25%）",
        "",
        generate_platform_section(local_life_events),
        "",
        "---",
        "",
        "## 05 竞对观察（占比10%）",
        "",
        generate_competitor_section(competitor_events) if competitor_events else "今日无重大竞对动态。",
        "",
        "---",
        "",
        "## 06 对屈臣氏的机会点",
        "",
        generate_opportunity_section(body_events),
        "",
        "---",
        "",
        "## 07 风险预警",
        "",
        generate_risk_section(body_events),
        "",
        "---",
        "",
        "## 08 今日建议动作",
        "",
        generate_unique_action(unique_action_event),
        "",
        "---",
        "",
        "## 09 每日八问",
        "",
        generate_daily_eight_questions(body_events, date_str),
    ]'''

assert "def generate_report_by_rules" in content, "generate_report_by_rules not found!"
content = content.replace(old_report, new_report, 1)

print("Updated generate_report_by_rules")

with open(FILE, "w") as f:
    f.write(content)

import ast
ast.parse(content)
print("COMPILES OK")
