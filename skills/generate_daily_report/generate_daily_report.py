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


def _get_seasonal_context() -> str:
    """基于当前日期动态生成季节性上下文。"""
    from datetime import datetime, timezone, timedelta
    CST = timezone(timedelta(hours=8))
    now = datetime.now(CST)
    month = now.month
    date_str = now.strftime("%Y年%m月%d日")
    
    if month in (5, 6):
        promo = "618临近，重点关注618预售、平台补贴策略、竞对618布局。严禁提双11/双12/年货节等非当前节点的大促。"
    elif month in (10, 11):
        promo = "双11临近，重点关注双11预售、平台玩法变化、竞对双11策略。严禁提618/双12/年货节等非当前节点的大促。"
    elif month == 12:
        promo = "双12和年货节临近，关注年末促销和跨年活动。严禁提618/双11等已过去的大促。"
    elif month in (1, 2):
        promo = "年货节和春节消费旺季，关注节日礼盒、新年促销。严禁提618/双11/双12等非当前节点的大促。"
    else:
        promo = "关注当前月份的正常经营节奏，严禁提任何非当前季节的大促节点（如618、双11等）。"
    
    return f"当前日期：{date_str}。{promo}"


def _confidence_tag(ev: dict) -> str:
    """返回事件置信度标注标签。

    P1事件或高加权分（ws>=3.5 + source_cred>=3）的low confidence不标⚠️，
    改标🔍（数据支撑但仍需关注）。
    """
    conf = ev.get("confidence", "medium")
    em = ev.get("extraction_method", "")
    is_p1_or_high_score = (
        ev.get("priority") == "P1"
        or (ev.get("weighted_score", 0) >= 3.5
            and ev.get("scores", {}).get("source_credibility", 0) >= 3)
    )
    if conf == "low":
        if is_p1_or_high_score:
            return "🔍"
        else:
            return "⚠️待验证"
    if em == "rule_fallback":
        return "🔄规则兜底"
    return ""

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
    "01 今日一句话结论",
    "02 今日三条必听",
    "03 即时零售重点变化",
    "04 本地生活重点变化",
    "05 竞对观察",
    "06 对屈臣氏的机会点",
    "07 风险预警",
    "08 今日建议动作",
    "09 每日八问",
]

# ── 平台关键词（按业务优先级排列）──
# priority: 1=即时零售(90%业务), 2=本地生活(战略), 3=传统电商(仅重大变化)
# ── 渠道权重配置（按业务占比）──
CHANNEL_WEIGHTS = {
    "即时零售": {"weight": 0.575, "label": "核心业务", "emoji": "🔴"},
    "本地生活": {"weight": 0.225, "label": "战略渠道", "emoji": "🟡"},
    "传统电商": {"weight": 0.125, "label": "观察渠道", "emoji": "⚪"},
    "竞对观察": {"weight": 0.075, "label": "竞对情报", "emoji": "🔵"},
}

# ── 判断标签 ──
JUDGMENT_LABELS = {
    "A": "必须关注—影响平台流量/费用/规则/销售结构，需要当天知道",
    "B": "本周跟进—有业务机会，需要团队验证",
    "C": "趋势观察—暂不行动，持续关注",
    "R": "风险预警—可能对销售/利润/履约/价格体系造成负面影响",
    "K": "竞对可借鉴—可转化为屈臣氏动作",
    "X": "需跨部门协同—需采购/营运/商品/门店/平台BD推进",
}

PLATFORM_SECTIONS = {
    # ═══ 即时零售渠道（55-60%权重）═══
    "美团闪购": {
        "keywords": ["美团闪购", "美团到家", "美团即时", "美团即时零售"],
        "channel": "即时零售",
        "max_items": 5,
    },
    "京东秒送 / 京东到家": {
        "keywords": ["京东秒送", "京东小时达", "京东到家", "达达配送"],
        "channel": "即时零售",
        "max_items": 5,
    },
    "淘宝闪购 / 饿了么": {
        "keywords": ["淘宝闪购", "饿了么", "蜂鸟即配", "天猫闪购"],
        "channel": "即时零售",
        "max_items": 5,
    },
    "抖音小时达": {
        "keywords": ["抖音小时达", "抖音即时零售", "抖音闪购"],
        "channel": "即时零售",
        "max_items": 5,
    },
    # ═══ 本地生活渠道（20-25%权重）═══
    "大众点评 / 抖音本地生活": {
        "keywords": ["大众点评", "点评", "抖音本地生活", "抖音本服", "抖音团购", "抖音到店", "抖音来客", "本地生活", "到店"],
        "channel": "本地生活",
        "max_items": 3,
    },
    # ═══ 传统电商渠道（10-15%权重，仅重大变化）═══
    "天猫 / 京东传统电商": {
        "keywords": ["天猫", "天猫旗舰店", "天猫超市", "京东旗舰店", "京东自营", "京东超市"],
        "channel": "传统电商",
        "max_items": 2,
    },
}

# ── 经营提示分类（按动作类型，非频道名）──
BUSINESS_TIPS_CATEGORIES = {
    "商品动作": ["商品", "SKU", "货盘", "选品", "品类", "套装", "定价", "组合",
                "美妆", "个护", "护肤", "彩妆", "防晒", "面膜", "洗护", "香氛",
                "资质", "备案", "合规", "正品", "溯源"],
    "平台动作": ["入驻", "开店", "上线", "渠道", "平台", "美团", "京东", "淘宝",
                "抖省省", "小时达", "闪购", "到家", "秒送", "API", "对接",
                "履约", "配送", "仓储", "门店仓", "前置仓"],
    "营销动作": ["营销", "促销", "会员", "私域", "直播", "话题", "品牌",
                "信任", "背书", "预算", "投放", "拉新", "复购", "首单",
                "礼金", "满减", "大促", "流量", "转化"],
    "试点动作": ["试点", "测试", "试验", "AB测试", "标杆", "实验",
                "门店试点", "城市试点", "双平台"],
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
    2. 排除 report_eligibility=archive (background事件)
    3. P1 优先于 P2
    4. action_level priority: immediate > test > watch
    5. weighted_score 降序
    """
    # 排除 ARCHIVE 和 archive 类新闻性
    used = [ev for ev in events
            if ev.get("priority") != "ARCHIVE"
            and ev.get("report_eligibility") != "archive"]

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
        "novelty_stats": {
            "core": sum(1 for ev in used if ev.get("report_eligibility") == "core"),
            "tracking": sum(1 for ev in used if ev.get("report_eligibility") == "tracking"),
            "reference": sum(1 for ev in used if ev.get("report_eligibility") == "reference"),
        },
    }

    return used, stats


def _deduplicate_against_previous_day(events: List[dict], project_root: str,
                                       date_str: str) -> List[dict]:
    """跨日话题去重：如果今天 top 信号的话题与昨天重复，降级为 reference。

    比较 cluster_key（事件聚类键）和 fact_hash（事实指纹）。
    被降级的事件 report_eligibility 从 core → reference，不进入核心信号。
    """
    from datetime import datetime, timedelta
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return events

    yesterday_file = os.path.join(
        project_root, f"data/events/{yesterday}/events_scored_novelty.json")
    if not os.path.exists(yesterday_file):
        logger.info(f"  跨日去重: 昨日事件文件不存在，跳过")
        return events

    try:
        with open(yesterday_file, "r", encoding="utf-8") as f:
            yesterday_data = json.load(f)
        yesterday_events = yesterday_data.get("events", [])
    except Exception:
        return events

    # 提取昨天 core/tracking 事件的聚类键和事实指纹
    yesterday_keys = set()
    yesterday_hashes = set()
    yesterday_titles = set()
    yesterday_topics = set()  # 话题前缀（用于模糊匹配）
    for ev in yesterday_events:
        if ev.get("report_eligibility") in ("core", "tracking"):
            ck = ev.get("cluster_key", "")
            fh = ev.get("fact_hash", "")
            if ck:
                yesterday_keys.add(ck)
                # 提取话题前缀：去掉类型标签，取核心实体
                # 格式: "type:话题关键词:实体列表"
                parts = ck.split(":")
                if len(parts) >= 2:
                    topic = parts[1][:15]  # 话题关键词前15字
                    yesterday_topics.add(topic)
                # 也提取实体字段（第3段）做交集匹配
                if len(parts) >= 3:
                    entities = [e.strip() for e in parts[2].split("|")]
                    for ent in entities:
                        if ent and len(ent) >= 2:
                            yesterday_topics.add(ent)
            if fh:
                yesterday_hashes.add(fh)
            # 也用标题关键词做模糊匹配
            title = ev.get("event_title", "")
            if title:
                yesterday_titles.add(title[:20])

    if not yesterday_keys and not yesterday_hashes:
        return events

    downgraded = 0
    for ev in events:
        if ev.get("report_eligibility") != "core":
            continue
        ck = ev.get("cluster_key", "")
        fh = ev.get("fact_hash", "")
        title_prefix = ev.get("event_title", "")[:20]

        # 精确匹配 cluster_key 或 fact_hash
        if (ck and ck in yesterday_keys) or (fh and fh in yesterday_hashes):
            ev["report_eligibility"] = "reference"
            ev["_cross_day_downgrade"] = True
            downgraded += 1
            logger.info(f"  跨日去重: 降级 {ev.get('event_title', '')[:50]} "
                       f"(cluster_key={ck[:20]})")
        # 话题前缀匹配：同一实体+话题连续两天出现
        # 注意：仅当话题关键词有实质性重叠时才降级（避免同一实体不同事件被误杀）
        elif ck:
            parts = ck.split(":")
            matched = False
            if len(parts) >= 2:
                topic = parts[1][:15]
                # 话题匹配要求：topic 至少 5 个字符，且与昨日话题有 60%+ 字符重叠
                if topic and len(topic) >= 5:
                    for yt in yesterday_topics:
                        if len(yt) < 5:
                            continue
                        topic_chars = set(topic)
                        yt_chars = set(yt)
                        overlap_ratio = len(topic_chars & yt_chars) / max(len(topic_chars | yt_chars), 1)
                        if overlap_ratio >= 0.6:
                            matched = True
                            logger.info(f"  跨日去重(话题): 降级 {ev.get('event_title', '')[:50]} "
                                       f"(topic={topic}, overlap={overlap_ratio:.2f})")
                            break
            # 实体交集匹配：至少2个非泛化实体重叠（排除泛词和超级实体）
            # 超级实体：每天都出现的大公司/平台名，仅凭它们不足以判断重复
            if not matched and len(parts) >= 3:
                _generic = {"美妆", "个护", "电商", "零售", "品牌", "消费", "平台", "直播", "供应链", "即时零售"}
                _super_entities = {"京东", "京东物流", "美团", "阿里巴巴", "阿里", "淘宝", "拼多多",
                                   "抖音", "快手", "腾讯", "百度", "苏宁", "苏宁易购", "屈臣氏",
                                   "饿了么", "盒马", "叮咚买菜", "朴朴超市", "山姆"}
                today_entities = set(e.strip() for e in parts[2].split("|") if len(e.strip()) >= 2)
                yesterday_entities = set(e for e in yesterday_topics if len(e) >= 2)
                # 排除泛词和超级实体
                today_specific = today_entities - _generic - _super_entities
                yesterday_specific = yesterday_entities - _generic - _super_entities
                overlap = today_specific & yesterday_specific
                if len(overlap) >= 2:
                    matched = True
                    logger.info(f"  跨日去重(实体): 降级 {ev.get('event_title', '')[:50]} "
                               f"(entities={','.join(list(overlap)[:3])})")
            if matched:
                ev["report_eligibility"] = "reference"
                ev["_cross_day_downgrade"] = True
                downgraded += 1
        # 模糊匹配：标题前20字完全相同
        if not ev.get("_cross_day_downgrade"):
            title_prefix = ev.get("event_title", "")[:20]
            if title_prefix and title_prefix in yesterday_titles:
                ev["report_eligibility"] = "reference"
                ev["_cross_day_downgrade"] = True
                downgraded += 1
                logger.info(f"  跨日去重(模糊): 降级 {ev.get('event_title', '')[:50]}")

    if downgraded > 0:
        logger.info(f"  跨日去重: 共降级 {downgraded} 个重复话题事件")
    else:
        logger.info(f"  跨日去重: 无重复话题")

    return events


def select_top_signals(events: List[dict], max_signals: int = 3) -> List[dict]:
    """选择今日最值得关注的信号。

    新颖性优先级:
    1. report_eligibility=core 且 novelty_status=new_today/updated_today
    2. report_eligibility=core 且 novelty_status=ongoing
    3. priority=P1/P2
    4. weighted_score 降序

    限制:
    - repeated 事件不得进入"今日最值得关注的信号"
    - background 事件不得进入核心正文
    - 如果 core 事件少于3条，不从 repeated/background 硬凑
    """
    # 排除 ARCHIVE 和 archive 类
    candidates = [ev for ev in events
                  if ev.get("priority") != "ARCHIVE"
                  and ev.get("report_eligibility") != "archive"]

    # 排除 repeated — 不得进入核心信号
    core_candidates = [ev for ev in candidates
                       if ev.get("report_eligibility") == "core"]

    # 如果 core 候选不足，不硬凑
    signal_candidates = core_candidates if core_candidates else []

    def signal_priority(ev):
        # 新颖性优先
        novelty_order = {"new_today": 0, "updated_today": 0, "ongoing": 1}
        novelty_score = novelty_order.get(ev.get("novelty_status", ""), 2)
        # 报告资格优先
        eligibility_order = {"core": 0, "tracking": 1, "reference": 2, "archive": 3}
        eligibility_score = eligibility_order.get(ev.get("report_eligibility", ""), 3)
        # 然后 priority
        p = 0 if ev.get("priority") == "P1" else 1
        # action_level
        al = ev.get("business_analysis", {}).get("action_level", "watch")
        al_score = {"immediate": 0, "test": 1, "watch": 2}.get(al, 2)
        # confidence
        conf = 0 if ev.get("confidence") == "high" else 1
        # weighted_score 降序
        ws = -ev.get("weighted_score", 0)
        return (eligibility_score, novelty_score, p, al_score, conf, ws)

    signal_candidates.sort(key=signal_priority)
    return signal_candidates[:max_signals]


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
    """生成01 今日一句话结论。"""
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

        # 置信度标注: P1事件不标⚠️待验证（已通过加权分验证）
        conf_label = ba_conf or conf
        is_p1_or_high_score = (
            ev.get("priority") == "P1"
            or (ev.get("weighted_score", 0) >= 3.5
                and ev.get("scores", {}).get("source_credibility", 0) >= 3)
        )
        if (conf == "low" or ba_conf == "low") and not is_p1_or_high_score:
            conf_label = "低⚠️待验证"
        elif conf == "low" or ba_conf == "low":
            # P1或高加权分+权威来源的low confidence → 标为"中"而非"低"
            conf_label = "中🔍"
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
    """生成03 即时零售重点变化（按优先级加权）。"""
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
def generate_competitor_section(events: List[dict]) -> str:
    """生成05 竞对观察。"""
    competitor_names = ["丝芙兰", "万宁", "调色师", "话梅", "妍丽", "WOW COLOUR",
                       "名创优品", "KK集团", "便利蜂", "全家", "711", "罗森", "美宜佳",
                       "盒马", "永辉", "大润发", "山姆"]
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
        conf_tag = _confidence_tag(ev)

        lines.append(f"- **{title}**{conf_tag}")
        lines.append(f"  {fact[:150]}")
        if action:
            lines.append(f"  经营建议：{action[:120]}")
        lines.append(f"  [`{ev_id}`]")
        lines.append("")

    return "\n".join(lines)


def generate_category_section(events: List[dict]) -> str:
    """生成06 对屈臣氏的机会点。"""
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
    """生成06 对屈臣氏的机会点（按动作类型×优先级四层结构）。

    参考格式：
    ### 商品动作
    - **立即可做**：...
    - **建议试点**：...
    - **需要总部支持**：...
    - **持续观察**：...
    """
    # 优先级标签映射
    URGENCY_LABELS = {
        "immediate": "立即可做",
        "test": "建议试点",
        "plan": "需要总部支持",
        "watch": "持续观察",
    }
    URGENCY_ORDER = ["immediate", "test", "plan", "watch"]

    sections = {}
    for cat_name, keywords in BUSINESS_TIPS_CATEGORIES.items():
        # 按优先级分组
        buckets: dict[str, list[str]] = {u: [] for u in URGENCY_ORDER}
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
                if not action:
                    continue
                action_level = ba.get("action_level", "watch")
                ev_id = ev.get("event_id", "")
                conf_tag = f"（{_confidence_tag(ev)}）" if _confidence_tag(ev) else ""
                tip = f"- **{URGENCY_LABELS.get(action_level, '持续观察')}**：{action[:120]}"
                buckets.setdefault(action_level, []).append(tip)

        # 组装该类别
        lines = [f"### {cat_name}", ""]
        has_any = False
        for urgency in URGENCY_ORDER:
            items = buckets.get(urgency, [])
            if items:
                lines.extend(items[:2])  # 每种优先级最多2条
                has_any = True
        if not has_any:
            lines.append("今日无新增经营提示。")
        sections[cat_name] = "\n".join(lines) + "\n"

    return "\n".join(sections.values())


def generate_unique_action(unique_event: Optional[dict]) -> str:
    """生成08 今日建议动作。"""
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
    """生成09 每日八问。"""
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

            tag = _confidence_tag(ev)

            item = f"- {title}{tag} [`{ev_id}`]"
            if questions:
                item += f" — {questions[0][:60]}"
            tracking.append(item)

    if not tracking:
        return "今日无新增追踪事项。"

    return "\n".join(tracking[:5])



def assign_judgment_labels(event: dict) -> list:
    """为事件分配判断标签（A/B/C/R/K/X）。"""
    labels = []
    title = event.get("event_title", "") + " " + event.get("title", "")
    summary = event.get("summary", "") + " " + event.get("llm_summary", "")
    combined = title + " " + summary
    priority = event.get("priority", "")
    
    # A类：必须关注
    a_kw = ["规则", "算法", "流量", "佣金", "费率", "政策", "下架", "处罚", "调整", "变更", "新规"]
    if any(kw in combined for kw in a_kw) or priority in ("P0", "P1"):
        labels.append("A")
    
    # B类：本周跟进
    b_kw = ["机会", "入驻", "合作", "招商", "试点", "活动", "大促", "618", "补贴", "扶持"]
    if any(kw in combined for kw in b_kw):
        labels.append("B")
    
    # C类：趋势观察
    c_kw = ["趋势", "增长", "份额", "市场", "行业", "报告", "数据"]
    if any(kw in combined for kw in c_kw) and "A" not in labels:
        labels.append("C")
    
    # R类：风险预警
    r_kw = ["风险", "下滑", "下降", "亏损", "关闭", "处罚", "投诉", "负面", "涨价", "缺货"]
    if any(kw in combined for kw in r_kw):
        labels.append("R")
    
    # K类：竞对可借鉴
    k_kw = ["竞对", "丝芙兰", "妍丽", "WOW", "名创优品", "便利蜂", "盒马", "借鉴", "案例"]
    if any(kw in combined for kw in k_kw):
        labels.append("K")
    
    # X类：需跨部门协同
    x_kw = ["采购", "营运", "商品", "门店", "BD", "协同", "联合", "总部"]
    if any(kw in combined for kw in x_kw):
        labels.append("X")
    
    return labels if labels else ["C"]


def format_judgment_label(labels: list) -> str:
    """格式化判断标签。"""
    m = {"A": "A-必须关注", "B": "B-本周跟进", "C": "C-趋势观察",
         "R": "R-风险预警", "K": "K-竞对可借鉴", "X": "X-需跨部门"}
    return " ".join(m.get(l, l) for l in labels)


def generate_risk_section(events: list) -> str:
    """生成风险预警段落。"""
    risk_events = [ev for ev in events if "R" in ev.get("judgment_labels", [])]
    if not risk_events:
        return "今日暂无重大风险预警。"
    lines = []
    for ev in risk_events[:3]:
        title = ev.get("event_title", ev.get("title", "未知"))
        summary = ev.get("summary", ev.get("llm_summary", ""))
        if len(summary) > 120:
            summary = summary[:117] + "..."
        impact = ev.get("affected_channels", [])
        impact_str = "、".join(impact[:2]) if impact else "相关渠道"
        lines.append(f"- **{title}**")
        if summary:
            lines.append(f"  {summary}")
        lines.append(f"  影响渠道：{impact_str}")
    return "\n".join(lines)


def generate_opportunity_section(events: list) -> str:
    """生成机会点段落。"""
    opp_events = [ev for ev in events if "B" in ev.get("judgment_labels", []) or "K" in ev.get("judgment_labels", [])]
    if not opp_events:
        return "今日暂无明确业务机会点。"
    lines = []
    for ev in opp_events[:5]:
        title = ev.get("event_title", ev.get("title", "未知"))
        summary = ev.get("summary", ev.get("llm_summary", ""))
        if len(summary) > 100:
            summary = summary[:97] + "..."
        labels = ev.get("judgment_labels", [])
        lines.append(f"- **{title}** [{format_judgment_label(labels)}]")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines)


def generate_daily_eight_questions(events: list, date_str: str) -> str:
    """生成每日必答8问。"""
    non_archive = [ev for ev in events if ev.get("priority") != "ARCHIVE"]
    p0 = [ev for ev in non_archive if ev.get("priority") == "P0"]
    p1 = [ev for ev in non_archive if ev.get("priority") == "P1"]
    risk = [ev for ev in non_archive if "R" in ev.get("judgment_labels", [])]
    opp = [ev for ev in non_archive if "B" in ev.get("judgment_labels", [])]
    
    top = p0[0] if p0 else (p1[0] if p1 else None)
    q1 = top.get("event_title", "今日无高优先级事件") if top else "今日无高优先级事件"
    q2 = "、".join(top.get("affected_channels", ["待确认"])[:2]) if top else "待确认"
    
    q3 = "需结合具体事件分析"
    q4 = "需结合具体事件分析"
    if top:
        c = top.get("event_title", "") + " " + top.get("summary", "")
        if any(kw in c for kw in ["流量", "算法", "排名"]):
            q3 = "是，可能影响流量分配"
        if any(kw in c for kw in ["货盘", "促销", "SKU"]):
            q4 = "是，可能影响货盘和促销"
    
    k_ev = [ev for ev in non_archive if "K" in ev.get("judgment_labels", [])]
    q5 = k_ev[0].get("event_title", "今日无竞对借鉴") if k_ev else "今日无竞对借鉴"
    q6 = risk[0].get("event_title", "今日无风险预警") if risk else "今日无风险预警"
    q7_items = [ev.get("event_title", "") for ev in opp[:3]]
    q7 = "、".join(q7_items) if q7_items else "暂无明确议题"
    q8 = top.get("event_title", "建议复盘昨日执行") if top else "建议复盘昨日执行"
    
    lines = [
        "**Q1: 今天哪个平台变化最值得关注？**", q1, "",
        "**Q2: 对屈臣氏哪个渠道影响最大？**", q2, "",
        "**Q3: 是否会影响平台流量分配？**", q3, "",
        "**Q4: 是否会影响品类货盘和促销方式？**", q4, "",
        "**Q5: 竞对有没有值得借鉴的动作？**", q5, "",
        "**Q6: 有没有需要马上预警的风险？**", q6, "",
        "**Q7: 有哪些机会可以放进本周周会讨论？**", q7, "",
        "**Q8: 今天最建议推动团队做的一件事是什么？**", q8,
    ]
    return "\n".join(lines)

def generate_report_by_rules(events: List[dict], date_str: str,
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
        
        if "K" in labels or any(kw in combined for kw in ["丝芙兰", "妍丽", "WOW", "名创优品", "便利蜂", "盒马", "竞对"]):
            competitor_events.append(ev)
        elif any(kw in combined for kw in ["即时零售", "美团闪购", "京东秒送", "淘宝闪购", "抖音小时达", "饿了么", "到家", "前置仓"]):
            instant_retail_events.append(ev)
        elif any(kw in combined for kw in ["本地生活", "大众点评", "抖音本地", "到店", "团购"]):
            local_life_events.append(ev)
        elif any(kw in combined for kw in ["天猫", "京东超市", "京东自营", "抖音电商", "抖音商城"]):
            traditional_ecom_events.append(ev)
        else:
            instant_retail_events.append(ev)
    
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
        "## 03 即时零售重点变化",
        "",
        generate_platform_section(instant_retail_events),
        "",
        "---",
        "",
        "## 04 本地生活重点变化",
        "",
        generate_platform_section(local_life_events),
        "",
        "---",
        "",
        "## 05 竞对观察",
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
    ]

    # 近期延续观察
    ongoing = []
    for ev in non_archive:
        if ev in top_signals:
            continue
        if ev.get("novelty_status") == "ongoing" or ev.get("report_eligibility") == "tracking":
            title = ev.get("event_title", "")
            ev_id = ev.get("event_id", "")
            first_seen = ev.get("first_seen_at", "")
            ongoing.append(f"- {title} (首见{first_seen}) [`{ev_id}`]")

    if ongoing:
        lines.extend(["", "---", "", "## 10 近期延续观察", ""])
        lines.extend(ongoing[:5])

    lines.extend(["", "---", "", "*本日报由 Watsons Retail Intel 系统自动生成，仅供内部参考。*"])
    _now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    lines.append(f"*生成时间：{_now}*")

    return "\n".join(lines)



# ===================== V1 LLM 润色 =====================

LLM_SYSTEM_PROMPT = """你是屈臣氏电商经营日报的编辑。

{seasonal_context}

你的任务是修改一份已经由规则生成的日报初稿，使其更加专业、精炼、有洞察力。

规则：
1. 只能修改措辞和组织方式，不能新增事件池中没有的事实。
2. 不能虚构数据。
3. 所有 event_id 引用必须保留。
4. confidence=low的P1或高加权分事件可正常陈述；其余low confidence标⚠️待验证作为线索。
5. rule_fallback 事件不能写成强结论。
6. 保持固定章节结构（01-09 + 11）：01一句话结论、02三条必听、03即时零售重点变化、04本地生活重点变化、05竞对观察、06机会点、07风险预警、08建议动作、09每日八问、11近期延续观察。tracking事件只能放在11节。
7. 今日唯一建议动作只能是一条。
8. 篇幅由信息量决定：事件多信息量大就多写，事件少就精简。关键是分析和建议的质量，不设固定字数限制。
9. 输出完整的 Markdown 文本。
10. 内容分布权重：即时零售55-60%、本地生活20-25%、传统电商10-15%、竞对10%。
11. 每个事件必须标注判断标签（A-必须关注/B-本周跟进/C-趋势观察/R-风险预警/K-竞对可借鉴/X-需跨部门）。
12. 09节每日八问必须完整回答，11节近期延续观察包含tracking/ongoing事件。"""

LLM_USER_TEMPLATE = """请优化以下日报初稿。只修改措辞和表达，不新增事实，保留所有 event_id 引用。

日期：{date}

--- 日报初稿 ---

{draft}

--- 要求 ---
1. 保持章节结构不变（01-09 + 11近期延续观察），tracking事件只能放在11节
2. 使措辞更专业精炼
3. 突出经营洞察
4. 篇幅由信息量决定，不设固定字数限制，关键是分析和建议的质量
5. 所有 `E2026...` 格式的 event_id 必须原样保留
6. 内容分布：即时零售55-60%、本地生活20-25%、传统电商10-15%、竞对10%
7. 每个事件标注判断标签（A/B/C/R/K/X）
8. 每日八问必须完整回答
9. 输出完整 Markdown"""


# ===================== V2 经营总编重构稿 =====================

V2_SYSTEM_PROMPT = """你是屈臣氏即时零售电商经营日报的总编。

{seasonal_context}

你的任务是基于事件池独立重构一份经营日报，不是润色初稿，而是重新取舍、压缩和聚焦。

## 内容质量标准
每条核心信号必须包含：
- **事实**：客观准确的事件描述，引用具体数据和来源
- **解释**：为什么发生、背后的驱动因素和行业逻辑
- **判断**：对竞争格局、市场趋势的短期和中长期影响
- **对屈臣氏的意义**：为什么屈臣氏电商负责人要在意这个信号、具体影响什么业务指标

## 规则
1. 直接从事件池重新取舍，不照搬v1结构和内容。
2. 核心信号最多3条，只选最值得屈臣氏电商负责人今天关注的事件。
3. 唯一建议动作只能1条，来自P1+高置信+immediate/test事件。
4. 06经营提示必须按动作类型分类（商品动作/平台动作/营销动作/试点动作），每个分类内按优先级排列：立即可做→建议试点→需要总部支持→持续观察。每条建议必须具体可执行，含时间节点或量化目标。
5. 仅confidence=low的事件需要标记：P1或高加权分(ws>=3.5+source_cred>=3)→标🔍；其余low→标⚠️待验证。medium/high不标任何标记。
6. extraction_method=rule_fallback 的事件标🔄规则兜底，不能写成强结论。
7. 以上两条可叠加（如：🔍🔄规则兜底 或 ⚠️待验证🔄规则兜底）。
8. 不得新增事件池外的事实和数据。
9. 更强调屈臣氏电商负责人今天该盯什么、问谁、看什么指标。
10. report_eligibility=tracking的事件（即novelty=ongoing的重复事件）只能放在11节近期延续观察，严禁进入02-05节正文。
11. 篇幅由信息量决定：事件多写长、事件少写短，不设固定字数限制。每条分析说透，每条建议写具体。
12. 保留固定章节结构（01-09 + 11近期延续观察），包括：一句话结论、三条必听、即时零售重点变化、本地生活重点变化、竞对观察、机会点、风险预警、建议动作、每日八问、近期延续观察。tracking事件只能放在11节。
13. 内容分布权重：即时零售55-60%、本地生活20-25%、传统电商10-15%、竞对10%。每个事件标注判断标签（A/B/C/R/K/X）。
14. 输出完整 Markdown 文本。
15. 表格使用GFM格式：表头行后必须跟对齐分隔行（如 |---|---|），确保表格可正确渲染。
16. ### 标题前后留空行，--- 分隔线前后留空行，列表项之间用空行分隔。"""

V2_USER_TEMPLATE = """请基于以下事件池独立重构一份经营日报，偏取舍、压缩和经营判断。

日期：{date}
核心信号上限：3条
唯一建议动作候选：{action_candidate}

--- 事件池 ---

{events_summary}

--- 内容要求 ---
1. 核心信号最多3条，每条必须包含：事实、解释、判断、对屈臣氏的意义
2. 唯一建议动作只1条，包含具体负责人方向、时间节点、预期效果
3. 06经营提示按动作类型分类（商品动作/平台动作/营销动作/试点动作），每个分类内：立即可做→建议试点→需要总部支持→持续观察，每条建议具体可执行
4. confidence=low的P1或高加权分事件标🔍，其余low标⚠️待验证
5. rule_fallback 事件只作为规则提取线索，标注🔄规则兜底
6. 04竞争格局建议用表格呈现（竞对 | 动作 | 影响 | 建议动作）
7. 05品类机会建议用表格呈现（机会点 | 关联变量 | 事件依据）
8. 篇幅由信息量决定，把每条分析和建议写透，不设固定字数限制
9. 保留所有 event_id 引用
10. 保留固定章节结构（01-09 + 11近期延续观察），tracking事件只能放在11节
11. 表格用GFM格式，###和---前后留空行
12. 输出完整 Markdown"""


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
    - 篇幅由事件信息量自然决定
    - 强调"今天该盯什么"
    - 每条建议具体可执行，不用"分析""评估""跟踪""监测"等观察性动词
    """
    non_archive = [ev for ev in events
                   if ev.get("priority") != "ARCHIVE"
                   and ev.get("report_eligibility") != "archive"]
    top_signals = select_top_signals(non_archive, 3)

    # 新颖性分组
    core_events = [ev for ev in non_archive
                   if ev.get("report_eligibility") == "core"
                   and ev.get("novelty_status") in ("new_today", "updated_today")]
    tracking_events = [ev for ev in non_archive
                       if ev.get("report_eligibility") == "tracking"]
    repeated_events = [ev for ev in non_archive
                       if ev.get("report_eligibility") == "reference"
                       and ev.get("novelty_status") == "repeated"]

    # ── 01 一句话判断 ──
    if core_events:
        first = core_events[0]
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
        tags = _confidence_tag(ev)

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
        tag = _confidence_tag(ev)

        # 跳过已选为核心信号的
        if ev_id in [s.get("event_id", "") for s in top_signals]:
            continue

        combined = f"{title} {fact} {' '.join(channels)}"
        label = assign_judgment_labels(ev)
        item = f"- {title}{tag}：{fact[:80]} [`{ev_id}`] {label}"

        # 平台
        platform_matched = False
        for sec_name, sec_cfg in PLATFORM_SECTIONS.items():
            kws = sec_cfg.get("keywords", []) if isinstance(sec_cfg, dict) else sec_cfg
            if any(kw in combined for kw in kws):
                platform_items.append(item)
                platform_matched = True
                break

        # 竞对
        competitor_names = ["丝芙兰", "万宁", "调色师", "话梅", "妍丽", "WOW COLOUR",
                           "名创优品", "便利蜂", "全家", "711", "罗森", "美宜佳",
                           "盒马", "永辉", "大润发", "山姆"]
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

    # ── 08 明日追踪 + 近期延续观察 ──
    tracking = []
    ongoing_items = []
    for ev in non_archive:
        ba = ev.get("business_analysis", {})
        if ev in top_signals:
            continue
        title = ev.get("event_title", "")
        ev_id = ev.get("event_id", "")
        conf = ev.get("confidence", "")
        em = ev.get("extraction_method", "")
        novelty = ev.get("novelty_status", "")
        eligibility = ev.get("report_eligibility", "")

        tag = _confidence_tag(ev)

        # ongoing → "近期延续观察"
        if novelty == "ongoing" or eligibility == "tracking":
            first_seen = ev.get("first_seen_at", "")
            ongoing_items.append(f"- {title}{tag} (首见{first_seen}) [`{ev_id}`]")
            continue

        # repeated → 简短条目标记
        if novelty == "repeated":
            tracking.append(f"- ⟳ {title} [`{ev_id}`]")
            continue

        item = f"- {title}{tag} [`{ev_id}`]"
        questions = ba.get("follow_up_questions", [])
        if questions:
            item += f" — {questions[0][:60]}"
        tracking.append(item)

    # 如果 core 事件为0，生成无信号提示
    if not core_events:
        judge = f"{date_str} 即时零售×个护美妆领域未发现高质量新增信号。"

    # ── 组装 ──
    _now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# 即时零售 × 个护美妆经营日报｜{date_str}",
        "",
        "---",
        "",
        "## 01 今日一句话结论",
        "",
        judge,
        "",
        "---",
        "",
        "## 02 今日三条必听",
        "",
    ]
    lines.extend(signal_lines)
    # 按渠道分组平台事件
    instant_retail_items = []
    local_life_items = []
    traditional_ecom_items = []
    for item in platform_items:
        if any(kw in item for kw in ["美团闪购", "京东秒送", "淘宝闪购", "天猫闪购", "抖音小时达", "即时零售", "半日达", "秒送"]):
            instant_retail_items.append(item)
        elif any(kw in item for kw in ["大众点评", "到店", "本服", "抖音本地生活", "团购"]):
            local_life_items.append(item)
        elif any(kw in item for kw in ["天猫电商", "京东电商", "传统电商"]):
            traditional_ecom_items.append(item)
        else:
            instant_retail_items.append(item)

    lines.extend([
        "---",
        "",
        "## 03 即时零售重点变化",
        "",
    ])
    if instant_retail_items:
        lines.extend(instant_retail_items[:5])
    else:
        lines.append("今日未发现即时零售新增信号。")
    lines.extend(["", "---", "", "## 04 本地生活重点变化", ""])
    if local_life_items:
        lines.extend(local_life_items[:3])
    else:
        lines.append("今日未发现本地生活新增信号。")
    if traditional_ecom_items:
        lines.extend(["", "---", "", "## 传统电商", ""])
        lines.extend(traditional_ecom_items[:2])
    lines.extend(["", "---", "", "## 05 竞对观察", ""])
    if competitor_items:
        lines.extend(competitor_items[:4])
    else:
        lines.append("今日未发现高置信竞对新增动作。")
    lines.extend(["", "---", "", "## 06 对屈臣氏的机会点", ""])
    if category_items:
        lines.extend(category_items[:4])
    else:
        lines.append("今日未发现明确的品类趋势新增信号。")
    # 风险预警
    risk_events = [ev for ev in non_archive
                   if ev.get("business_analysis", {}).get("impact_type") == "risk"]
    risk_lines = []
    if risk_events:
        for ev in risk_events[:3]:
            ba = ev.get("business_analysis", {})
            label = assign_judgment_labels(ev)
            risk_lines.append(f"- ⚠️ {ev.get('event_title', '')}：{ba.get('watsons_impact', '')[:100]}{label}")
    lines.extend(["", "---", "", "## 07 风险预警", ""])
    if risk_lines:
        lines.extend(risk_lines)
    else:
        lines.append("今日无明确风险预警。")
    lines.extend([
        "",
        "---",
        "",
        "## 08 今日建议动作",
        "",
        action_section,
        "",
        "---",
        "",
        "## 09 每日八问",
        "",
    ])
    lines.extend(generate_daily_eight_questions(events, date_str))
    lines.extend(["", "---", "", "## 11 近期延续观察", ""])
    lines.extend(tracking[:5])
    # 新增: 近期延续观察区域
    if ongoing_items:
        lines.extend([
            "",
            "---",
            "",
            "## 11 近期延续观察",
            "",
            "以下事件在此前日期已出现，持续跟踪中：",
            "",
        ])
        lines.extend(ongoing_items[:5])
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
    # 构建事件池摘要（排除 ARCHIVE 和 tracking 事件）
    events_lines = []
    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        if ev.get("report_eligibility") == "tracking":
            continue  # tracking事件仅用于11节近期延续观察，不入LLM事件池
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

    formatted_system = V2_SYSTEM_PROMPT.format(seasonal_context=_get_seasonal_context())

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=formatted_system,
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
            # V2 是独立重构，不从 V1 draft 回补 tracking 节（V2 无 draft 引用）
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
    formatted_system = LLM_SYSTEM_PROMPT.format(seasonal_context=_get_seasonal_context())

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=formatted_system,
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
            # V2 是独立重构，不从 V1 draft 回补 tracking 节（V2 无 draft 引用）
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
    section_07 = report.find("08 今日建议动作")
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

    # 7. 极短内容警告（仅作提示，不强制）
    char_count = len(report.replace("\n", "").replace(" ", ""))
    if char_count < 500:
        warnings.append(f"日报内容极短：约{char_count}字，请检查是否生成异常")

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


# ===================== 播客→日报转化 =====================

_PODCAST_TO_REPORT_SYSTEM = """你是屈臣氏即时零售经营日报编辑。你的任务是将一段口播稿（播客脚本）转化为结构化的书面日报。

## 转化规则
1. 内容必须与播客完全一致——不添加任何播客中没有的事实、数据、品牌名或建议
2. 保留播客中所有具体数字、品牌、平台、建议动作和追踪指标
3. 转化为简洁的书面表达，去掉口语化连接词（"说白了"/"注意了"等）
4. 按照下方固定章节结构重新组织内容

## 输出格式（严格 Markdown）

# 即时零售 × 个护美妆经营日报｜{date}

---

## 01 今日一句话结论

（从播客开场的核心判断提炼一句话）

---

## 02 今日三条必听

### 信号N：（事件标题）（判断标签）

- **事实**：（发生了什么）
- **经营含义**：（为什么重要 + 对屈臣氏意味着什么）
- **建议动作**：（值得学习/警惕什么的具体建议）
- **追踪指标**：（今天盯什么指标）
- **涉及渠道**：（相关平台/渠道）

---

## 03 即时零售重点变化

（从播客"平台策略变化"部分提取即时零售相关内容，按平台分条列出）

---

## 04 本地生活重点变化

（从播客"本地生活/门店机会"部分提取内容）

---

## 05 竞对观察

（从播客"竞对或商家动作"部分提取内容）

---

## 06 对屈臣氏的机会点

（从播客"屈臣氏机会点"部分提取，列出具体机会和建议）

---

## 07 风险预警

（从播客中提取风险相关内容，如无明确风险则写"今日无重大风险信号"）

---

## 08 今日建议动作

（直接使用播客结尾的"今天听完只做三件事"）

---

## 判断标签说明
- A-必须关注：影响平台流量/费用/规则/销售结构，需要当天知道
- B-本周跟进：有业务机会，需要团队验证
- C-趋势观察：暂不行动，持续关注
- R-风险预警：可能对销售/利润/履约/价格体系造成负面影响

## 注意
- 判断标签根据事件重要性自行标注（A/B/C/R）
- 如果播客某个章节写了"今天暂无相关信号"，日报对应章节也直接写"今日无新增信号"
- 严禁添加播客中没有提到的任何内容
"""

_PODCAST_TO_REPORT_USER = """请将以下播客脚本转化为结构化日报。

日期：{date}

--- 播客脚本 ---
{podcast_script}
---

请直接输出完整的 Markdown 日报，不要有任何解释性前缀或后缀。"""


def _generate_report_from_podcast(
    project_root: str,
    date: str,
    podcast_script_path: str,
    output_file: str,
    use_llm: bool,
    errors: List[str],
) -> Optional[dict]:
    """从播客脚本生成日报。成功返回 result dict，失败返回 None。"""
    try:
        with open(podcast_script_path, "r", encoding="utf-8") as f:
            podcast_script = f.read().strip()

        if len(podcast_script) < 200:
            logger.warning(f"  播客脚本过短 ({len(podcast_script)} chars)，跳过")
            return None

        logger.info(f"  播客脚本长度: {len(podcast_script)} chars")

        if not use_llm or not _LLM_AVAILABLE:
            logger.info("  LLM 不可用，回退到事件池模式")
            return None

        # 调用 LLM 转化
        client = get_llm_client()
        system_prompt = _PODCAST_TO_REPORT_SYSTEM.format(date=date)
        user_prompt = _PODCAST_TO_REPORT_USER.format(
            date=date, podcast_script=podcast_script
        )

        logger.info("  调用 LLM 将播客转化为日报...")
        resp = client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=system_prompt,
            max_tokens=8192,
        )
        response = resp.get("content", "") if isinstance(resp, dict) else str(resp)

        if not response or len(response.strip()) < 100:
            logger.warning("  LLM 返回为空或过短")
            return None

        draft = response.strip()

        # 基本校验：必须包含日报标题
        if f"# 即时零售" not in draft and f"经营日报" not in draft:
            # LLM 可能没按格式输出，加个标题
            draft = f"# 即时零售 × 个护美妆经营日报｜{date}\n\n{draft}"

        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', draft))
        logger.info(f"  日报生成成功: {chinese_chars} 中文字符")

        # 保存 draft
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(draft)
        logger.info(f"  日报初稿已保存: {output_file}")

        # 同时保存到终稿位置（editor_review 会再处理）
        year = date[:4]
        month = date[5:7]
        final_dir = os.path.join(project_root, "reports", "daily", year, month)
        os.makedirs(final_dir, exist_ok=True)
        final_path = os.path.join(final_dir, f"{date}.md")
        with open(final_path, "w", encoding="utf-8") as f:
            f.write(draft)
        logger.info(f"  终稿已保存: {final_path}")

        return {
            "ok": True,
            "date": date,
            "source": "podcast_conversion",
            "podcast_script_path": podcast_script_path,
            "chinese_chars": chinese_chars,
            "output_file": output_file,
            "final_file": final_path,
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"  播客转日报异常: {e}")
        errors.append(f"podcast_conversion_error: {e}")
        return None




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
        # 优先使用 novelty 版本 (含 report_eligibility)
        novelty_path = resolve_path(project_root, f"data/events/{date}/events_scored_novelty.json")
        analyzed_path = resolve_path(project_root, f"data/events/{date}/events_analyzed.json")
        if os.path.exists(novelty_path):
            events_file = novelty_path
            logger.info(f"  使用 novelty 版本: {novelty_path}")
        elif os.path.exists(analyzed_path):
            events_file = analyzed_path
        else:
            events_file = analyzed_path  # will fail later with file-not-found
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

        # ═══════════════════════════════════════════
        # 快速路径: 播客脚本已存在 → 转化为结构化日报
        # ═══════════════════════════════════════════
        podcast_script_path = resolve_path(project_root, f"podcasts/scripts/{date}.md")
        if os.path.exists(podcast_script_path):
            logger.info(f"  检测到播客脚本: {podcast_script_path}")
            result = _generate_report_from_podcast(
                project_root, date, podcast_script_path, output_file, use_llm, errors
            )
            if result is not None:
                return result
            logger.warning("  播客转日报失败，回退到事件池模式")

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

        # ── 跨日话题去重 ──
        all_events = _deduplicate_against_previous_day(all_events, project_root, date)

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