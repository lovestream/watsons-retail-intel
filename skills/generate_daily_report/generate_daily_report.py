#!/usr/bin/env python3
"""
generate_daily_report.py — 日报生成技能

读取 events_analyzed.json，生成 Markdown 格式的经营日报初稿。
支持 LLM 辅助润色 + 规则模板兜底。

CLI:
  python generate_daily_report.py --project-root ... --date 2026-04-26
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ── 项目路径 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("generate_daily_report")

# ── LLM 客户端 ──
_sys_path_added = False
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
    _sys_path_added = True

try:
    from skills.utils.llm_client import (
        get_llm_client, check_llm_config, test_llm_connection,
        robust_json_extract,
    )
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

if _sys_path_added and _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)


# ===================== 常量 =====================

VALID_SECTIONS = [
    "01 今日一句话判断",
    "02 今日最值得关注的3个信号",
    "03 平台变化解读",
    "04 竞对与品牌动作",
    "05 品类与场景机会",
    "06 对屈臣氏的经营提示",
    "07 今日唯一建议动作",
    "08 明日追踪清单",
]

# ── 平台关键词 ──
PLATFORM_SECTIONS = {
    "美团闪购": ["美团闪购", "美团到家", "美团即时"],
    "京东秒送 / 京东到家": ["京东秒送", "京东小时达", "京东到家", "达达配送"],
    "淘宝闪购 / 饿了么": ["淘宝闪购", "饿了么", "蜂鸟即配"],
    "抖音小时达": ["抖音小时达", "抖音即时"],
    "天猫 / 京东传统电商": ["天猫", "天猫旗舰店", "天猫超市", "京东旗舰店", "京东自营", "京东"],
}

# ── 经营提示分类 ──
BUSINESS_TIPS_CATEGORIES = {
    "即时零售": ["美团闪购", "京东秒送", "京东到家", "淘宝闪购", "抖音小时达", "饿了么",
                "即时零售", "闪购", "到家业务"],
    "天猫 / 京东 B2C": ["天猫", "天猫旗舰店", "B2C"],
    "天猫超市 / 京东自营": ["天猫超市", "京东自营"],
    "经销商与分销": ["分销", "经销商"],
    "会员与私域": ["会员", "私域", "复购", "留存"],
}


# ===================== 工具函数 =====================

def resolve_path(project_root: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(project_root, rel_path)


def safe_get(d: dict, key: str, default: Any = "") -> Any:
    return d.get(key, default) or default


# ===================== 事件筛选与排序 =====================

def select_events(events: List[dict]) -> Tuple[List[dict], dict]:
    """筛选和排序事件，返回 (使用的事件, 统计信息)。

    规则：
    1. 排除 priority=ARCHIVE
    2. P1 优先于 P2
    3. action_level priority: immediate > test > watch
    4. weighted_score 降序
    """
    # 排除 ARCHIVE
    used = [ev for ev in events if ev.get("priority") != "ARCHIVE"]

    # 排序
    action_order = {"immediate": 0, "test": 1, "watch": 2}
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "ARCHIVE": 3}

    used.sort(key=lambda ev: (
        priority_order.get(ev.get("priority", "P2"), 2),
        action_order.get(ev.get("business_analysis", {}).get("action_level", "watch"), 2),
        -ev.get("weighted_score", 0),
    ))

    # 统计
    stats = {
        "used_event_count": len(used),
        "p1_used_count": sum(1 for ev in used if ev.get("priority") == "P1"),
        "p2_used_count": sum(1 for ev in used if ev.get("priority") == "P2"),
        "low_confidence_used_count": sum(
            1 for ev in used if ev.get("confidence") == "low"),
        "rule_fallback_used_count": sum(
            1 for ev in used if ev.get("extraction_method") == "rule_fallback"),
        "core_signal_count": 0,
    }

    return used, stats


def select_top_signals(events: List[dict], max_signals: int = 3) -> List[dict]:
    """选择今日最值得关注的信号。

    规则：
    - P1 优先
    - action_level=immediate/test 优先
    - 非 low confidence 优先
    - 最多 max_signals 条
    """
    candidates = [ev for ev in events if ev.get("priority") != "ARCHIVE"]

    def signal_priority(ev):
        p = 0 if ev.get("priority") == "P1" else 1
        al = ev.get("business_analysis", {}).get("action_level", "watch")
        al_score = {"immediate": 0, "test": 1, "watch": 2}.get(al, 2)
        conf = 0 if ev.get("confidence") == "high" else 1
        ws = -ev.get("weighted_score", 0)
        return (p, al_score, conf, ws)

    candidates.sort(key=signal_priority)
    return candidates[:max_signals]


def select_unique_action(events: List[dict]) -> Optional[dict]:
    """选择今日唯一建议动作事件。

    规则：
    - priority=P1
    - action_level=immediate 或 test
    - confidence != low
    - extraction_method != rule_fallback
    """
    candidates = [
        ev for ev in events
        if ev.get("priority") == "P1"
        and ev.get("business_analysis", {}).get("action_level") in ("immediate", "test")
        and ev.get("confidence") != "low"
        and ev.get("extraction_method") != "rule_fallback"
    ]

    if not candidates:
        return None

    # 按 weighted_score 降序
    candidates.sort(key=lambda ev: -ev.get("weighted_score", 0))
    return candidates[0]


# ===================== 规则模板生成 =====================

def generate_one_line_summary(events: List[dict], date_str: str) -> str:
    """生成01 今日一句话判断。"""
    top_signals = select_top_signals(events, 3)
    if not top_signals:
        return f"{date_str} 即时零售×个护美妆领域未发现重大新增经营信号。"

    first = top_signals[0]
    ba = first.get("business_analysis", {})
    title = first.get("event_title", "")
    impact = ba.get("impact_type", "watch")

    if impact == "opportunity":
        prefix = "今日即时零售个护美妆领域出现增长机会："
    elif impact == "risk":
        prefix = "今日即时零售个护美妆领域出现风险信号："
    else:
        prefix = "今日即时零售个护美妆领域需关注："

    summary = f"{prefix}{title}"
    if len(summary) > 80:
        summary = summary[:77] + "…"

    return summary


def generate_signal_section(signals: List[dict]) -> str:
    """生成02 信号章节。"""
    if not signals:
        return "今日未发现高质量新增信号。"

    lines = []
    for i, ev in enumerate(signals, 1):
        ba = ev.get("business_analysis", {})
        title = ev.get("event_title", "")
        fact = ev.get("fact", "") or ""
        ev_id = ev.get("event_id", "")
        conf = ev.get("confidence", "")
        ba_conf = ba.get("confidence", "")
        em = ev.get("extraction_method", "")
        impact = ba.get("impact_type", "watch")
        watsons_impact = ba.get("watsons_impact", "")
        action = ba.get("recommended_action", "")
        metrics = ba.get("tracking_metrics", [])
        channels = ba.get("affected_channels", [])

        # 置信度标注
        conf_label = ba_conf or conf
        if conf == "low" or ba_conf == "low":
            conf_label = "低⚠️待验证"
        elif conf == "high" and ba_conf == "high":
            conf_label = "高"
        else:
            conf_label = "中"

        if em == "rule_fallback":
            conf_label += "（规则兜底）"

        # impact_label
        impact_labels = {"opportunity": "增长机会📈", "risk": "风险信号⚠️",
                         "watch": "观察信号👁️", "noise": "背景信息"}
        impact_label = impact_labels.get(impact, "信号")

        lines.append(f"### 信号{i}：{title}")
        lines.append("")
        lines.append(f"- **类型**：{impact_label}")
        lines.append(f"- **事实**：{fact[:200]}")
        lines.append(f"- **经营含义**：{watsons_impact[:200]}")
        lines.append(f"- **建议动作**：{action[:200]}")
        lines.append(f"- **追踪指标**：{'、'.join(metrics[:5]) if metrics else '待确认'}")
        lines.append(f"- **涉及渠道**：{'、'.join(channels[:4]) if channels else '待确认'}")
        lines.append(f"- **证据事件**：`{ev_id}`")
        lines.append(f"- **置信度**：{conf_label}")
        lines.append("")

    return "\n".join(lines)


def generate_platform_section(events: List[dict]) -> str:
    """生成03 平台变化解读。"""
    sections = {}
    for section_name, keywords in PLATFORM_SECTIONS.items():
        relevant = []
        for ev in events:
            if ev.get("priority") == "ARCHIVE":
                continue
            ba = ev.get("business_analysis", {})
            channels = ba.get("affected_channels", [])
            title = ev.get("event_title", "")
            fact = ev.get("fact", "")
            combined = f"{title} {fact} {' '.join(channels)}"

            if any(kw in combined for kw in keywords):
                relevant.append(ev)

        if relevant:
            lines = [f"### {section_name}", ""]
            for ev in relevant[:3]:  # 每个平台最多3条
                ba = ev.get("business_analysis", {})
                title = ev.get("event_title", "")
                fact = ev.get("fact", "") or ""
                action = ba.get("recommended_action", "")
                ev_id = ev.get("event_id", "")
                conf = ev.get("confidence", "medium")
                conf_tag = "⚠️待验证" if conf == "low" else ""

                lines.append(f"- {title}{conf_tag}")
                lines.append(f"  事实：{fact[:150]}")
                if action:
                    lines.append(f"  建议：{action[:120]}")
                lines.append(f"  [`{ev_id}`]")
                lines.append("")
            sections[section_name] = "\n".join(lines)
        else:
            sections[section_name] = f"### {section_name}\n\n今日未发现足够高质量新增信号。\n"

    return "\n".join(sections.values())


def generate_competitor_section(events: List[dict]) -> str:
    """生成04 竞对与品牌动作。"""
    competitor_names = ["丝芙兰", "万宁", "调色师", "话梅", "妍丽", "WOW COLOUR",
                       "名创优品", "KK集团"]
    competitor_events = []
    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        title = ev.get("event_title", "")
        fact = ev.get("fact", "")
        combined = f"{title} {fact}"
        if any(name in combined for name in competitor_names):
            competitor_events.append(ev)

    if not competitor_events:
        return "今日未发现高置信竞对新增动作。"

    lines = []
    for ev in competitor_events[:5]:
        ba = ev.get("business_analysis", {})
        title = ev.get("event_title", "")
        fact = ev.get("fact", "") or ""
        action = ba.get("recommended_action", "")
        ev_id = ev.get("event_id", "")
        conf = ev.get("confidence", "medium")
        conf_tag = "⚠️待验证" if conf == "low" else ""

        lines.append(f"- **{title}**{conf_tag}")
        lines.append(f"  {fact[:150]}")
        if action:
            lines.append(f"  经营建议：{action[:120]}")
        lines.append(f"  [`{ev_id}`]")
        lines.append("")

    return "\n".join(lines)


def generate_category_section(events: List[dict]) -> str:
    """生成05 品类与场景机会。"""
    category_events = []
    category_keywords = ["美妆", "个护", "护肤", "彩妆", "防晒", "面膜", "洗护",
                        "香氛", "口腔护理", "男士护理", "品类", "GMV", "增长"]

    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        ba = ev.get("business_analysis", {})
        bvs = ev.get("business_variables", []) or ba.get("affected_business_variables", [])
        title = ev.get("event_title", "")
        fact = ev.get("fact", "")
        combined = f"{title} {fact}"

        if any(kw in combined for kw in category_keywords):
            category_events.append(ev)

    if not category_events:
        return "今日未发现明确的品类趋势新增信号。"

    lines = []
    for ev in category_events[:5]:
        ba = ev.get("business_analysis", {})
        title = ev.get("event_title", "")
        bvs = ba.get("affected_business_variables", []) or ev.get("business_variables", [])
        metrics = ba.get("tracking_metrics", [])
        ev_id = ev.get("event_id", "")

        lines.append(f"- **{title}**")
        lines.append(f"  关联变量：{'、'.join(bvs[:4]) if bvs else '无'}")
        if metrics:
            lines.append(f"  追踪指标：{'、'.join(metrics[:4])}")
        lines.append(f"  [`{ev_id}`]")
        lines.append("")

    return "\n".join(lines)


def generate_tips_section(events: List[dict]) -> str:
    """生成06 对屈臣氏的经营提示。"""
    sections = {}
    for cat_name, keywords in BUSINESS_TIPS_CATEGORIES.items():
        tips = []
        for ev in events:
            if ev.get("priority") == "ARCHIVE":
                continue
            ba = ev.get("business_analysis", {})
            channels = ba.get("affected_channels", [])
            title = ev.get("event_title", "")
            fact = ev.get("fact", "")
            bvs = ba.get("affected_business_variables", []) or ev.get("business_variables", [])
            combined = f"{title} {fact} {' '.join(channels)} {' '.join(bvs)}"

            if any(kw in combined for kw in keywords):
                action = ba.get("recommended_action", "")
                if action:
                    conf = ev.get("confidence", "medium")
                    conf_tag = "（待验证）" if conf == "low" else ""
                    tips.append(f"- {action[:100]}{conf_tag} [`{ev.get('event_id','')}`]")

        if tips:
            sections[cat_name] = f"### {cat_name}\n\n" + "\n".join(tips[:3]) + "\n"
        else:
            sections[cat_name] = f"### {cat_name}\n\n今日无新增经营提示。\n"

    return "\n".join(sections.values())


def generate_unique_action(unique_event: Optional[dict]) -> str:
    """生成07 今日唯一建议动作。"""
    if not unique_event:
        return (
            "今日不建议贸然推动新增动作，建议以复核高价值线索和追踪平台变化为主。"
        )

    ba = unique_event.get("business_analysis", {})
    return "\n".join([
        f"- **建议动作**：{ba.get('recommended_action', '待确认')}",
        f"- **对应事件**：`{unique_event.get('event_id', '')}` — {unique_event.get('event_title', '')}",
        f"- **负责方向**：{ba.get('owner_hint', '待分派')}",
        f"- **今天要看的指标**：{'、'.join(ba.get('tracking_metrics', ['待确认'])[:4])}",
        f"- **为什么是今天最值得做**：{unique_event.get('priority', 'P1')}级信号，"
        f"加权评分{unique_event.get('weighted_score', 0):.2f}，"
        f"直接影响屈臣氏在{', '.join(ba.get('affected_channels', ['相关渠道'])[:2])}的经营。",
    ])


def generate_tracking_list(events: List[dict]) -> str:
    """生成08 明日追踪清单。"""
    # P2 + watch 事件优先，low confidence 标注待验证
    tracking = []
    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        ba = ev.get("business_analysis", {})
        action_level = ba.get("action_level", "watch")
        if action_level in ("watch",) or ev.get("priority") == "P2":
            title = ev.get("event_title", "")
            ev_id = ev.get("event_id", "")
            conf = ev.get("confidence", "medium")
            em = ev.get("extraction_method", "")
            questions = ba.get("follow_up_questions", [])

            tag = ""
            if conf == "low":
                tag += "⚠️待验证"
            if em == "rule_fallback":
                tag += "🔄规则兜底"

            item = f"- {title}{tag} [`{ev_id}`]"
            if questions:
                item += f" — {questions[0][:60]}"
            tracking.append(item)

    if not tracking:
        return "今日无新增追踪事项。"

    return "\n".join(tracking[:5])


def generate_report_by_rules(events: List[dict], date_str: str,
                              unique_action_event: Optional[dict]) -> str:
    """规则模板生成完整日报。"""
    top_signals = select_top_signals(events, 3)
    non_archive = [ev for ev in events if ev.get("priority") != "ARCHIVE"]

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
        generate_platform_section(non_archive),
        "",
        "---",
        "",
        "## 04 竞对与品牌动作",
        "",
        generate_competitor_section(non_archive),
        "",
        "---",
        "",
        "## 05 品类与场景机会",
        "",
        generate_category_section(non_archive),
        "",
        "---",
        "",
        "## 06 对屈臣氏的经营提示",
        "",
        generate_tips_section(non_archive),
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
        generate_tracking_list(non_archive),
        "",
        "---",
        "",
        f"*本日报由 Watsons Retail Intel 系统自动生成，仅供内部参考。*",
    ]

    _now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    lines.append(f"*生成时间：{_now}*")

    return "\n".join(lines)


# ===================== V1 LLM 润色 =====================

LLM_SYSTEM_PROMPT = """你是屈臣氏电商经营日报的编辑。

你的任务是修改一份已经由规则生成的日报初稿，使其更加专业、精炼、有洞察力。

规则：
1. 只能修改措辞和组织方式，不能新增事件池中没有的事实。
2. 不能虚构数据。
3. 所有 event_id 引用必须保留。
4. low confidence 事件必须标注"待验证"。
5. rule_fallback 事件不能写成强结论。
6. 保持8个固定章节结构。
7. 今日唯一建议动作只能是一条。
8. 字数控制在1500-3000字。
9. 输出完整的 Markdown 文本。"""

LLM_USER_TEMPLATE = """请优化以下日报初稿。只修改措辞和表达，不新增事实，保留所有 event_id 引用。

日期：{date}

--- 日报初稿 ---

{draft}

--- 要求 ---
1. 保持8个章节结构不变
2. 使措辞更专业精炼
3. 突出经营洞察
4. 控制总字数在1500-3000字
5. 所有 `E2026...` 格式的 event_id 必须原样保留
6. 输出完整 Markdown"""


# ===================== V2 经营总编重构稿 =====================

V2_SYSTEM_PROMPT = """你是屈臣氏即时零售电商经营日报的总编。

你的任务是基于事件池独立重构一份经营日报，不是润色初稿，而是重新取舍、压缩和聚焦。

你必须遵守：
1. 直接从事件池重新取舍，不照搬v1结构和内容。
2. 核心信号最多3条，只选最值得屈臣氏电商负责人今天关注的事件。
3. 唯一建议动作只能1条，来自P1+高置信+immediate/test事件。
4. confidence=low 的事件只能作为"待验证线索"，不能写成确定性结论，必须标注⚠️待验证。
5. extraction_method=rule_fallback 的事件只能作为"规则提取线索"，不能写成强结论，必须标注🔄规则兜底。
6. 如果一个事件两者兼具，必须同时标注⚠️待验证和🔄规则兜底。
7. 不得新增事件池外的事实和数据。
8. 更强调屈臣氏电商负责人今天该盯什么、问谁、看什么指标。
9. 字数控制在1600-2400中文字符。
10. 保留8个固定章节。
11. 所有核心判断必须保留 event_id（E日期_编号格式）。
12. 输出完整 Markdown 文本。"""

V2_USER_TEMPLATE = """请基于以下事件池独立重构一份经营日报，偏取舍、压缩和经营判断。

日期：{date}
核心信号上限：3条
唯一建议动作候选：{action_candidate}

--- 事件池 ---

{events_summary}

--- 要求 ---
1. 核心信号最多3条，只选最值得屈臣氏电商负责人关注的事件
2. 唯一建议动作只1条，来自合规事件
3. low confidence 事件只作为待验证线索，标注⚠️待验证
4. rule_fallback 事件只作为规则提取线索，标注🔄规则兜底
5. 更强调"今天该盯什么、问谁、看什么指标"
6. 控制在1600-2400中文字符
7. 保留所有 event_id 引用
8. 保留8个固定章节
9. 输出完整 Markdown"""


def generate_v2_by_rules(
    events: List[dict],
    date_str: str,
    unique_action_event: Optional[dict],
) -> str:
    """规则模板生成 V2 经营总编重构稿。

    相比 V1 全覆盖风格，V2 更克制：
    - 核心信号最多3条，只选 P1 或高置信事件
    - 唯一建议动作只1条
    - low confidence / rule_fallback 只列线索
    - 1600-2400 中文字符
    - 强调"今天该盯什么"
    """
    non_archive = [ev for ev in events if ev.get("priority") != "ARCHIVE"]
    top_signals = select_top_signals(non_archive, 3)

    # ── 01 一句话判断 ──
    if top_signals:
        first = top_signals[0]
        ba = first.get("business_analysis", {})
        impact = ba.get("impact_type", "watch")
        if impact == "opportunity":
            judge = f"今日即时零售个护美妆领域出现增长机会：{first.get('event_title', '')}"
        elif impact == "risk":
            judge = f"今日即时零售个护美妆领域出现风险信号：{first.get('event_title', '')}"
        else:
            judge = f"今日即时零售个护美妆领域需关注：{first.get('event_title', '')}"
    else:
        judge = f"{date_str} 即时零售×个护美妆领域未发现重大新增经营信号。"

    # ── 02 核心信号（最多3条） ──
    signal_lines = []
    for i, ev in enumerate(top_signals, 1):
        ba = ev.get("business_analysis", {})
        conf = ev.get("confidence", "")
        em = ev.get("extraction_method", "")
        title = ev.get("event_title", "")
        fact = ev.get("fact", "") or ""
        watsons_impact = ba.get("watsons_impact", "")
        action = ba.get("recommended_action", "")
        metrics = ba.get("tracking_metrics", [])
        channels = ba.get("affected_channels", [])
        ev_id = ev.get("event_id", "")

        # 标记
        tags = ""
        if conf == "low":
            tags += "⚠️待验证"
        if em == "rule_fallback":
            tags += "🔄规则兜底"

        impact_label = {"opportunity": "📈", "risk": "⚠️", "watch": "👁️",
                        "noise": "📋"}.get(ba.get("impact_type", "watch"), "👁️")

        signal_lines.append(f"### 信号{i}：{title}{tags}")
        signal_lines.append("")
        signal_lines.append(f"- **事实**：{fact[:150]}")
        if watsons_impact:
            signal_lines.append(f"- **对屈臣氏的影响**：{watsons_impact[:150]}")
        signal_lines.append(f"- **今天该看什么指标**：{'、'.join(metrics[:4]) if metrics else '待确认'}")
        signal_lines.append(f"- **涉及渠道**：{'、'.join(channels[:3]) if channels else '待确认'}")
        signal_lines.append(f" [`{ev_id}`]")
        signal_lines.append("")

    # ── 03-06 合并平台/竞对/品类/提示为简表 ──
    platform_items = []
    competitor_items = []
    category_items = []
    tips_items = []

    for ev in non_archive:
        ba = ev.get("business_analysis", {})
        title = ev.get("event_title", "")
        fact = ev.get("fact", "") or ""
        action = ba.get("recommended_action", "")
        ev_id = ev.get("event_id", "")
        conf = ev.get("confidence", "")
        em = ev.get("extraction_method", "")
        channels = ba.get("affected_channels", [])

        # 低置信 / 规则兜底 只出线索
        is_clue = conf == "low" or em == "rule_fallback"
        tag = ""
        if conf == "low":
            tag += "⚠️待验证"
        if em == "rule_fallback":
            tag += "🔄规则兜底"

        # 跳过已选为核心信号的
        if ev_id in [s.get("event_id", "") for s in top_signals]:
            continue

        combined = f"{title} {fact} {' '.join(channels)}"
        item = f"- {title}{tag}：{fact[:80]} [`{ev_id}`]"

        # 平台
        platform_matched = False
        for sec_name, kws in PLATFORM_SECTIONS.items():
            if any(kw in combined for kw in kws):
                platform_items.append(item)
                platform_matched = True
                break

        # 竞对
        competitor_names = ["丝芙兰", "万宁", "调色师", "话梅", "妍丽", "WOW COLOUR"]
        if any(n in combined for n in competitor_names):
            competitor_items.append(item)

        # 品类
        cat_kws = ["美妆", "个护", "护肤", "彩妆", "防晒", "面膜", "洗护", "香氛", "品类", "GMV", "增长"]
        if any(kw in combined for kw in cat_kws):
            category_items.append(item)

        # 提示
        if action and not is_clue:
            tips_items.append(f"- {action[:80]}{tag} [`{ev_id}`]")

    # ── 07 唯一建议动作 ──
    if unique_action_event:
        ba = unique_action_event.get("business_analysis", {})
        action_section = "\n".join([
            f"- **建议动作**：{ba.get('recommended_action', '待确认')}",
            f"- **对应事件**：`{unique_action_event.get('event_id', '')}` — {unique_action_event.get('event_title', '')}",
            f"- **负责方向**：{ba.get('owner_hint', '待分派')}",
            f"- **今天要看的指标**：{'、'.join(ba.get('tracking_metrics', ['待确认'])[:4])}",
            f"- **为什么是今天最值得做**：{unique_action_event.get('priority', 'P1')}级信号，"
            f"加权评分{unique_action_event.get('weighted_score', 0):.2f}",
        ])
    else:
        action_section = "今日不建议贸然推动新增动作，建议以复核高价值线索和追踪平台变化为主。"

    # ── 08 明日追踪 ──
    tracking = []
    for ev in non_archive:
        ba = ev.get("business_analysis", {})
        if ev in top_signals:
            continue
        title = ev.get("event_title", "")
        ev_id = ev.get("event_id", "")
        conf = ev.get("confidence", "")
        em = ev.get("extraction_method", "")
        questions = ba.get("follow_up_questions", [])

        tag = ""
        if conf == "low":
            tag += "⚠️待验证"
        if em == "rule_fallback":
            tag += "🔄规则兜底"

        item = f"- {title}{tag} [`{ev_id}`]"
        if questions:
            item += f" — {questions[0][:60]}"
        tracking.append(item)

    # ── 组装 ──
    _now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# 即时零售 × 个护美妆经营日报｜{date_str}",
        "",
        "---",
        "",
        "## 01 今日一句话判断",
        "",
        judge,
        "",
        "---",
        "",
        "## 02 今日最值得关注的3个信号",
        "",
    ]
    lines.extend(signal_lines)
    lines.extend([
        "---",
        "",
        "## 03 平台变化解读",
        "",
    ])
    if platform_items:
        lines.extend(platform_items[:6])
    else:
        lines.append("今日未发现足够高质量新增信号。")
    lines.extend(["", "---", "", "## 04 竞对与品牌动作", ""])
    if competitor_items:
        lines.extend(competitor_items[:4])
    else:
        lines.append("今日未发现高置信竞对新增动作。")
    lines.extend(["", "---", "", "## 05 品类与场景机会", ""])
    if category_items:
        lines.extend(category_items[:4])
    else:
        lines.append("今日未发现明确的品类趋势新增信号。")
    lines.extend(["", "---", "", "## 06 对屈臣氏的经营提示", ""])
    if tips_items:
        lines.extend(tips_items[:5])
    else:
        lines.append("今日无新增经营提示。")
    lines.extend([
        "",
        "---",
        "",
        "## 07 今日唯一建议动作",
        "",
        action_section,
        "",
        "---",
        "",
        "## 08 明日追踪清单",
        "",
    ])
    lines.extend(tracking[:5])
    lines.extend([
        "",
        "---",
        "",
        f"*本日报由 Watsons Retail Intel 系统自动生成，仅供内部参考。*",
        f"*生成时间：{_now}*",
    ])

    return "\n".join(lines)


def refine_v2_with_llm(
    events: List[dict],
    date_str: str,
    unique_action_event: Optional[dict],
    llm_client,
    model: str = None,
) -> Optional[str]:
    """使用 LLM 独立重构 V2 经营总编稿。

    V2 不是润色 V1，而是基于事件池重新取舍。
    """
    # 构建事件池摘要
    events_lines = []
    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        ba = ev.get("business_analysis", {})
        ev_line = (
            f"[{ev.get('priority','')}] {ev.get('event_id','')} "
            f"{ev.get('event_title','')} | "
            f"conf={ev.get('confidence','?')} "
            f"method={ev.get('extraction_method','?')} "
            f"action={ba.get('action_level','?')} "
            f"score={ev.get('weighted_score',0):.2f} | "
            f"事实：{ev.get('fact','')[:120]} | "
            f"建议：{ba.get('recommended_action','')[:80]} | "
            f"指标：{'、'.join(ba.get('tracking_metrics',[])[:3])}"
        )
        events_lines.append(ev_line)
    events_summary = "\n".join(events_lines[:20])

    # 构建唯一建议动作候选
    if unique_action_event:
        ba = unique_action_event.get("business_analysis", {})
        action_candidate = (
            f"{unique_action_event.get('event_id')} - "
            f"{unique_action_event.get('event_title')} "
            f"(action_level={ba.get('action_level','?')})"
        )
    else:
        action_candidate = "无合规事件，建议保留提示文字"

    user_prompt = V2_USER_TEMPLATE.format(
        date=date_str,
        action_candidate=action_candidate,
        events_summary=events_summary,
    )

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=V2_SYSTEM_PROMPT,
            response_format="text",
            temperature=0.3,
            max_tokens=4096,
            model=model,
        )

        _used_model = result.get("model", model or "")
        logger.info(f"V2 LLM 重构使用模型: {_used_model}")

        content = result.get("content", "")
        if not content.strip():
            reasoning = result.get("reasoning_content", "")
            if reasoning.strip():
                content = reasoning

        if content.strip():
            return content.strip()

        logger.warning("V2 LLM 返回为空，使用规则模板")
        return None

    except Exception as e:
        logger.warning(f"V2 LLM 重构失败: {e}")
        return None


def refine_with_llm(draft: str, date_str: str, llm_client,
                     model: str = None) -> Optional[str]:
    """使用 LLM 润色日报初稿。

    Args:
        draft: 规则生成初稿
        date_str: 日期字符串
        llm_client: LLM 客户端
        model: 覆盖默认模型（None 使用 llm_client 默认模型）
    """
    user_prompt = LLM_USER_TEMPLATE.format(date=date_str, draft=draft)

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=LLM_SYSTEM_PROMPT,
            response_format="text",
            temperature=0.3,
            max_tokens=4096,
            model=model,
        )

        _used_model = result.get("model", model or "")
        logger.info(f"日报 LLM 润色使用模型: {_used_model}")

        content = result.get("content", "")

        # thinking model: check reasoning_content
        if not content.strip():
            reasoning = result.get("reasoning_content", "")
            if reasoning.strip():
                # 尝试从 reasoning 中提取 markdown
                content = reasoning

        if content.strip():
            return content.strip()

        logger.warning("LLM 返回为空，使用规则初稿")
        return None

    except Exception as e:
        logger.warning(f"LLM 润色失败: {e}")
        return None


# ===================== 输出校验 =====================

def validate_report(report: str, events: List[dict],
                    unique_action_event: Optional[dict]) -> Tuple[bool, List[str]]:
    """校验生成的日报。

    Returns:
        (passed, warnings)
    """
    warnings = []

    # 1. 是否包含8个固定章节
    for section in VALID_SECTIONS:
        if section not in report:
            warnings.append(f"缺少章节: {section}")

    # 2. 是否包含至少1个 event_id
    event_ids = [ev.get("event_id", "") for ev in events if ev.get("event_id")]
    found_ids = [eid for eid in event_ids if eid in report]
    if not found_ids:
        warnings.append("日报中未发现任何 event_id 引用")

    # 3. 是否出现低置信度事件写成强结论
    low_conf_events = [ev for ev in events if ev.get("confidence") == "low"]
    for ev in low_conf_events:
        title = ev.get("event_title", "")
        if title and title in report:
            # 检查附近是否有"待验证"标记
            idx = report.index(title)
            context = report[max(0, idx - 50):idx + len(title) + 50]
            if "待验证" not in context and "⚠️" not in context and "低" not in context:
                warnings.append(f"低置信度事件可能缺少标注：{title[:30]}")

    # 4. rule_fallback 事件不能写成强结论
    rf_events = [ev for ev in events if ev.get("extraction_method") == "rule_fallback"]
    for ev in rf_events:
        title = ev.get("event_title", "")
        if title and title in report:
            idx = report.index(title)
            context = report[max(0, idx - 50):idx + len(title) + 50]
            if "规则兜底" not in context and "规则提取" not in context and "🔄" not in context:
                warnings.append(f"rule_fallback事件可能缺少标注：{title[:30]}")

    # 5. 今日唯一建议动作合规
    section_07 = report.find("07 今日唯一建议动作")
    if section_07 >= 0:
        section_end = report.find("---", section_07 + 1)
        if section_end < 0:
            section_end = len(report)
        section_07_text = report[section_07:section_end]

        if unique_action_event:
            # 检查包含对应 event_id
            uid = unique_action_event.get("event_id", "")
            if uid and uid not in section_07_text:
                warnings.append(f"今日唯一建议动作缺少事件引用：{uid}")
        else:
            # 无合格事件时应包含提示文字
            if "不建议贸然" not in section_07_text:
                warnings.append("无合格事件时，建议动作应说明'不建议贸然推动'")

    # 6. Markdown 非空
    if len(report.strip()) < 200:
        warnings.append(f"日报内容过短：{len(report.strip())}字符")

    # 7. 字数检查 (1500-3000)
    char_count = len(report.replace("\n", "").replace(" ", ""))
    if char_count < 800:
        warnings.append(f"日报字数可能不足：约{char_count}字 (去除空白)")

    # 8. ARCHIVE 不应出现在正文中（允许在追踪清单中出现）
    archive_events = [ev for ev in events if ev.get("priority") == "ARCHIVE"]
    for ev in archive_events:
        title = ev.get("event_title", "")
        if title and title in report:
            # 检查是否仅出现在追踪清单中
            idx = report.index(title)
            section_before = report[max(0, idx - 200):idx]
            if "02 今日最值得关注的" in section_before:
                warnings.append(f"ARCHIVE事件出现在核心信号中：{title[:30]}")

    passed = len([w for w in warnings if "缺少章节" in w or "event_id" in w]) == 0
    return passed, warnings


# ===================== 主函数 =====================

def generate_daily_report(
    project_root: str,
    date: str,
    events_file: Optional[str] = None,
    reference_file: Optional[str] = None,
    output_file: Optional[str] = None,
    use_llm: bool = True,
) -> dict:
    """日报生成主函数。"""
    errors: List[str] = []

    # ── 路径 ──
    if not events_file:
        events_file = resolve_path(project_root, f"data/events/{date}/events_analyzed.json")
    if not reference_file:
        reference_file = resolve_path(project_root, f"data/cleaned/{date}/reference_articles.json")

    drafts_dir = resolve_path(project_root, f"data/drafts/{date}")
    log_dir = resolve_path(project_root, f"data/logs/{date}")
    os.makedirs(drafts_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    if not output_file:
        output_file = os.path.join(drafts_dir, "daily_report_draft.md")
    log_file = os.path.join(log_dir, "generate_daily_report.log")

    # ── 日志 ──
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
    logger.addHandler(fh)

    try:
        logger.info("=" * 60)
        logger.info(f"开始生成日报: date={date}")
        logger.info(f"  events_file: {events_file}")
        logger.info(f"  output_file: {output_file}")

        # ── 加载事件 ──
        if not os.path.exists(events_file):
            error_msg = f"事件文件不存在: {events_file}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {"ok": False, "date": date, "errors": errors}

        with open(events_file, "r", encoding="utf-8") as f:
            events_data = json.load(f)

        all_events = events_data.get("events", [])
        if not all_events:
            logger.warning("事件列表为空")
            draft = f"# 即时零售 × 个护美妆经营日报｜{date}\n\n今日无新增经营信号。\n"
            with open(output_file, "w", encoding="utf-8") as out:
                out.write(draft)
            return {"ok": True, "date": date, "event_count": 0,
                    "used_event_count": 0, "core_signal_count": 0, "errors": errors}

        logger.info(f"加载 {len(all_events)} 条事件")

        # ── 加载参考文章（可选） ──
        reference_articles = []
        if os.path.exists(reference_file):
            try:
                with open(reference_file, "r", encoding="utf-8") as f:
                    ref_data = json.load(f)
                reference_articles = ref_data.get("articles", [])
                logger.info(f"加载 {len(reference_articles)} 篇参考文章")
            except Exception as e:
                logger.warning(f"参考文章加载失败: {e}")

        # ── 事件筛选 ──
        used_events, stats = select_events(all_events)
        top_signals = select_top_signals(used_events, 3)
        unique_action_event = select_unique_action(used_events)
        stats["core_signal_count"] = len(top_signals)

        logger.info(f"使用事件: {stats['used_event_count']}")
        logger.info(f"P1: {stats['p1_used_count']}, P2: {stats['p2_used_count']}")
        logger.info(f"核心信号: {stats['core_signal_count']}")

        if unique_action_event:
            logger.info(f"唯一建议动作事件: {unique_action_event.get('event_id')} "
                         f"- {unique_action_event.get('event_title', '')[:50]}")
        else:
            logger.info("唯一建议动作事件: 无合格事件")

        # ═══════════════════════════════════════════
        # Phase 1: V1 + V2 规则稿 并行生成
        # ═══════════════════════════════════════════
        logger.info("Phase 1: 并行生成 V1/V2 规则稿...")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        _gen_futures = {}
        with ThreadPoolExecutor(max_workers=2) as _gen_executor:
            _gen_futures[_gen_executor.submit(
                generate_report_by_rules, used_events, date, unique_action_event
            )] = "v1"
            _gen_futures[_gen_executor.submit(
                generate_v2_by_rules, used_events, date, unique_action_event
            )] = "v2"

            for _future in as_completed(_gen_futures):
                _label = _gen_futures[_future]
                try:
                    _result = _future.result(timeout=120)
                    if _label == "v1":
                        draft_v1 = _result
                        logger.info(f"V1 规则初稿长度: 约{len(draft_v1.replace(chr(10),'').replace(' ',''))}字")
                    else:
                        draft_v2 = _result
                        logger.info(f"V2 规则稿长度: 约{len(draft_v2.replace(chr(10),'').replace(' ',''))}字")
                except Exception as e:
                    logger.error(f"{_label.upper()} 规则稿生成失败: {e}")
                    if _label == "v1":
                        draft_v1 = f"# 即时零售 × 个护美妆经营日报｜{date}\n\n生成失败: {e}\n"
                    else:
                        draft_v2 = f"# 经营日报总编版｜{date}\n\n生成失败: {e}\n"

        llm_refined_v1 = False
        llm_refined_v2 = False
        _report_model = None
        _report_fallback = None

        # ═══════════════════════════════════════════
        # Phase 2: V1 + V2 LLM 润色 并行
        # ═══════════════════════════════════════════
        if use_llm:
            try:
                llm_client = get_llm_client()
                if llm_client.available:
                    # ── 加载模型路由（一次）──
                    try:
                        from skills.utils.model_router import get_model_for_skill
                        _report_model, _report_fallback = get_model_for_skill(
                            "generate_daily_report")
                        logger.info(f"模型路由: generate_daily_report 默认={_report_model}, "
                                    f"fallback={_report_fallback}")
                    except Exception as e:
                        logger.warning(f"模型路由加载失败: {e}，使用默认模型")

                    # ── 并行 LLM 润色 V1 + V2 ──
                    logger.info("Phase 2: 并行 LLM 润色 V1 + V2...")
                    _refine_futures = {}
                    with ThreadPoolExecutor(max_workers=2) as _refine_executor:
                        _refine_futures[_refine_executor.submit(
                            refine_with_llm, draft_v1, date, llm_client, model=_report_model
                        )] = "v1"
                        _refine_futures[_refine_executor.submit(
                            refine_v2_with_llm, used_events, date, unique_action_event,
                            llm_client, model=_report_model
                        )] = "v2"

                        for _future in as_completed(_refine_futures):
                            _label = _refine_futures[_future]
                            try:
                                _result = _future.result(timeout=180)
                                if _result and len(_result) > 200:
                                    if _label == "v1":
                                        draft_v1 = _result
                                        llm_refined_v1 = True
                                        logger.info("V1 LLM 润色成功")
                                    else:
                                        draft_v2 = _result
                                        llm_refined_v2 = True
                                        logger.info("V2 LLM 重构成功")
                                else:
                                    logger.warning(f"{_label.upper()} LLM 润色返回为空或过短，使用规则稿")
                            except Exception as e:
                                logger.warning(f"{_label.upper()} LLM 润色失败: {e}，使用规则稿")
                else:
                    logger.warning("LLM 不可用，使用规则初稿")
            except Exception as e:
                logger.warning(f"LLM 初始化失败: {e}，使用规则初稿")

        # ═══════════════════════════════════════════
        # 校验
        # ═══════════════════════════════════════════
        v1_passed, v1_warnings = validate_report(draft_v1, all_events, unique_action_event)
        v2_passed, v2_warnings = validate_report(draft_v2, all_events, unique_action_event)
        stats["validation_passed"] = v1_passed
        stats["validation_warnings"] = v1_warnings

        if v1_warnings:
            logger.warning(f"V1 校验警告 ({len(v1_warnings)}):")
            for w in v1_warnings:
                logger.warning(f"  ⚠️ {w}")
        else:
            logger.info("V1 校验通过")

        if v2_warnings:
            logger.warning(f"V2 校验警告 ({len(v2_warnings)}):")
            for w in v2_warnings:
                logger.warning(f"  ⚠️ {w}")
        else:
            logger.info("V2 校验通过")

        # ═══════════════════════════════════════════
        # 写出
        # ═══════════════════════════════════════════
        v1_file = os.path.join(drafts_dir, "daily_report_draft_v1.md")
        v2_file = os.path.join(drafts_dir, "daily_report_draft_v2.md")
        compat_file = output_file  # daily_report_draft.md 向后兼容指向 v1

        with open(v1_file, "w", encoding="utf-8") as f:
            f.write(draft_v1)
        logger.info(f"V1 已写入: {v1_file}")

        with open(v2_file, "w", encoding="utf-8") as f:
            f.write(draft_v2)
        logger.info(f"V2 已写入: {v2_file}")

        # 向后兼容：daily_report_draft.md 指向 v1
        with open(compat_file, "w", encoding="utf-8") as f:
            f.write(draft_v1)
        logger.info(f"兼容稿已写入: {compat_file}")

        import re as _re
        result = {
            "ok": True,
            "date": date,
            "events_file": events_file,
            "reference_file": reference_file,
            "output_file": compat_file,
            "v1_file": v1_file,
            "v2_file": v2_file,
            "log_file": log_file,
            "event_count": len(all_events),
            "used_event_count": stats["used_event_count"],
            "core_signal_count": stats["core_signal_count"],
            "unique_action_event_id": unique_action_event.get("event_id") if unique_action_event else None,
            "v1_llm_refined": llm_refined_v1,
            "v2_llm_refined": llm_refined_v2,
            "v1_length": len(draft_v1),
            "v2_length": len(draft_v2),
            "v1_chinese_chars": len(_re.findall(r'[\u4e00-\u9fff]', draft_v1)),
            "v2_chinese_chars": len(_re.findall(r'[\u4e00-\u9fff]', draft_v2)),
            "v1_validation_passed": v1_passed,
            "v2_validation_passed": v2_passed,
            "validation_passed": v1_passed,
            "validation_warnings": v1_warnings,
            "errors": errors,
        }

        for k in ["p1_used_count", "p2_used_count", "low_confidence_used_count",
                   "rule_fallback_used_count"]:
            result[k] = stats.get(k, 0)

        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    except Exception as e:
        error_msg = f"日报生成失败: {e}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return {"ok": False, "date": date, "errors": errors}

    finally:
        logger.removeHandler(fh)
        fh.close()


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="日报生成技能")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--events-file", default=None, help="分析后事件文件路径")
    parser.add_argument("--reference-file", default=None, help="参考文章文件路径")
    parser.add_argument("--output-file", default=None, help="兼容输出文件路径(daily_report_draft.md)")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用LLM润色 (true/false)")
    parser.add_argument("--test-llm", action="store_true",
                        help="测试 LLM 连接")

    args = parser.parse_args()

    if args.test_llm:
        if not _LLM_AVAILABLE:
            print("❌ llm_client 不可用")
            sys.exit(1)
        result = test_llm_connection()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("ok") else 1)

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = generate_daily_report(
        project_root=args.project_root,
        date=args.date,
        events_file=args.events_file,
        reference_file=args.reference_file,
        output_file=args.output_file,
        use_llm=use_llm,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()