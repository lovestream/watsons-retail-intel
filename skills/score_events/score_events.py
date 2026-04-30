#!/usr/bin/env python3
"""
score_events.py — 事件评分技能

对 extract_events 输出的 events_raw.json 中的每个事件进行
多维度评分、硬降级判定、分级排序，输出 events_scored.json。

评分维度 (0-5):
  strategic_importance, watsons_relevance, impact_scope,
  source_credibility, data_richness, actionability,
  time_sensitivity, novelty

分级: P0 / P1 / P2 / ARCHIVE

硬降级规则:
  - confidence=low              → 最高 P2
  - extraction_method=rule_fallback → 最高 P2
  - source_credibility < 2       → 最高 P2
  - watsons_relevance < 3       → 最高 P2
  - event_type=background_only   → 最高 P2
  - event_type=unclear           → 最高 P2
  - source_url 缺失             → ARCHIVE
  - fact 为空                   → ARCHIVE
  - evidence_text 为空          → 最高 P2

CLI:
  python score_events.py --project-root ... --date 2026-04-26
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
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
logger = logging.getLogger("score_events")

# ===================== 默认配置 =====================

DEFAULT_WEIGHTS = {
    "strategic_importance": 0.18,
    "watsons_relevance": 0.24,
    "impact_scope": 0.12,
    "source_credibility": 0.12,
    "data_richness": 0.10,
    "actionability": 0.14,
    "time_sensitivity": 0.06,
    "novelty": 0.04,
}

DEFAULT_THRESHOLDS = {
    "P0": 4.3,
    "P1": 3.6,
    "P2": 2.8,
    "ARCHIVE": 0.0,
}

DEFAULT_SOURCE_TIER_MAP = {
    "meituan_official": 5, "jd_daojia_official": 5, "eleoda_official": 5,
    "watsons_official": 5, "nmpa": 5, "sec_filing": 5,
    "company_earnings": 5, "company_announcement": 5,
    "latepost": 4, "36kr": 4, "36kr_newsflashes": 4,
    "caixin": 4, "wallstreetcn": 4, "jiemian": 4, "huxiu": 4,
    "cbndata": 3, "cosmetic_observation": 3, "beauty_evolution": 3,
    "retail_observation": 3, "instant_retail_watch": 3,
    "sohu": 2, "sina": 2, "toutiao": 2, "baijiahao": 2,
    "wechat_article": 2,
    "_unknown": 1,
}

DEFAULT_EVENT_TYPE_IMPORTANCE = {
    "platform_rule": 5, "platform_move": 4, "competitor_move": 4,
    "data_signal": 4, "category_trend": 3, "policy_change": 5,
    "consumer_trend": 3, "new_product": 3, "supply_chain": 3,
    "marketing": 2, "background_only": 1, "unclear": 1,
}

DEFAULT_ACTIONABLE_VARIABLES = {
    "促销": 5, "折扣": 5, "满减": 5, "补贴": 5, "平台资源位": 5,
    "货盘": 4, "SKU": 4, "选品": 4, "客单价": 4, "复购": 4,
    "转化率": 4, "流量": 3, "投流": 3, "门店覆盖": 3, "履约": 3,
    "私域": 3, "会员": 3, "竞争格局": 2, "品类机会": 2,
    "拉新": 3, "留存": 3, "供应链": 3, "仓储": 3,
    "合规": 3, "备案": 3, "成分": 3,
}

DEFAULT_DATA_KEYWORDS = [
    "GMV", "增长率", "订单量", "门店数", "用户数", "市场份额",
    "突破", "增长", "提升", "下降", "同比", "环比",
    "亿", "万", "%", "SKU", "渗透率", "市占率", "营收", "利润",
]

# ── 屈臣氏相关性关键词 ──
WATSONS_DIRECT_KEYWORDS = [
    "屈臣氏", "Watsons", "watsons",
]
WATSONS_CHANNEL_KEYWORDS = [
    "美团闪购", "美团到家", "京东到家", "京东秒送", "淘宝闪购",
    "抖音小时达", "饿了么", "蜂鸟即配", "即时零售",
]
WATSONS_CATEGORY_KEYWORDS = [
    "美妆", "个护", "护肤", "彩妆", "防晒", "面膜", "洗护",
    "香氛", "口腔护理", "男士护理",
]
WATSONS_COMPETITOR_KEYWORDS = [
    "丝芙兰", "万宁", "调色师", "话梅", "妍丽", "WOW COLOUR",
    "名创优品", "KK集团",
]

# ── 影响范围关键词 ──
IMPACT_NATIONAL = ["全国", "全网", "全平台", "全国性", "行业", "市场", "整体"]
IMPACT_MULTI = ["多平台", "多城市", "多渠道", "跨境", "连锁"]
IMPACT_SINGLE = ["单平台", "单品牌", "单个", "某", "一家"]

# ── 数据模式 ──
DATA_VALUE_PATTERN = re.compile(
    r"(\d+[\.\d]*)\s*(亿|万|千|%|倍|元|块|美元|人民币)"
    r"|(\d+[\.\d]*%)\s*"
    r"|(增长率|增速|同比|环比|渗透率|市占率|GMV|SKU|营收|利润)"
)


# ===================== 配置加载 =====================

def load_yaml(path: str) -> dict:
    """加载 YAML 配置文件。"""
    if not os.path.exists(path):
        logger.warning(f"配置文件不存在: {path}")
        return {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except ImportError:
        # 无 PyYAML → 简易解析
        logger.warning("PyYAML 不可用，尝试简易解析")
        return _parse_yaml_simple(path)


def _parse_yaml_simple(path: str) -> dict:
    """极简 YAML 解析（只支持 key: value 一级和二级）。"""
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line and not line.startswith("-"):
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # 去掉行内注释
                if " #" in val:
                    val = val[:val.index(" #")].strip()
                if val:
                    try:
                        result[key] = float(val) if "." in val else int(val)
                    except ValueError:
                        result[key] = val
    return result


def resolve_path(project_root: str, rel_path: str) -> str:
    """将相对路径解析为绝对路径。"""
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(project_root, rel_path)


# ===================== 评分函数 =====================

def score_strategic_importance(event: dict, event_type_importance: dict) -> Tuple[float, List[str]]:
    """评分维度1：战略重要性 (0-5)"""
    event_type = event.get("event_type", "unclear")
    reasons = []

    # 基于 event_type 基线
    base = event_type_importance.get(event_type, 1)

    # fact 内容加强
    fact = event.get("fact", "") or ""
    title = event.get("event_title", "") or ""
    combined = f"{title} {fact}"

    # 高战略标志
    high_strategic = ["即时零售", "平台政策", "格局变化", "市场重塑", "战略",
                     "退出", "关闭", "停止", "收购", "合并"]
    if any(kw in combined for kw in high_strategic):
        base = min(base + 1, 5)
        reasons.append(f"含高战略关键词")

    # 平台政策/规则
    if event_type == "platform_rule":
        reasons.append("平台规则变更直接影响经营")

    score = min(max(base, 0), 5)
    if not reasons:
        reasons.append(f"event_type={event_type} 基线分={base}")
    return score, reasons


def score_watsons_relevance(event: dict) -> Tuple[float, List[str]]:
    """评分维度2：屈臣氏相关度 (0-5)"""
    title = event.get("event_title", "") or ""
    fact = event.get("fact", "") or ""
    evidence = event.get("evidence_text", "") or ""
    entities = event.get("entities", {}) or {}
    combined = f"{title} {fact} {evidence}".lower()

    reasons = []

    # 5分：直接提到屈臣氏
    if any(kw.lower() in combined for kw in WATSONS_DIRECT_KEYWORDS):
        reasons.append("直接提到屈臣氏")
        return 5, reasons

    # 4分：涉及屈臣氏核心即时零售渠道
    if any(kw in combined for kw in WATSONS_CHANNEL_KEYWORDS):
        reasons.append("涉及屈臣氏核心即时零售渠道")
        return 4, reasons

    # 3分：竞对或个护美妆即时零售
    has_competitor = any(kw.lower() in combined for kw in WATSONS_COMPETITOR_KEYWORDS)
    has_category = any(kw in combined for kw in WATSONS_CATEGORY_KEYWORDS)
    if has_competitor and has_category:
        reasons.append("竞对+个护美妆即时零售")
        return 3, reasons
    if has_competitor:
        reasons.append("涉及竞对")
        return 3, reasons
    if has_category and any(kw in combined for kw in WATSONS_CHANNEL_KEYWORDS):
        reasons.append("个护美妆+即时零售渠道")
        return 3, reasons

    # 2分：泛电商/泛零售
    generic_retail = ["电商", "零售", "门店", "消费", "品牌"]
    if any(kw in combined for kw in generic_retail):
        reasons.append("泛零售相关")
        return 2, reasons

    # 1分：弱相关
    if has_category:
        reasons.append("仅涉及个护美妆品类")
        return 1, reasons

    # 0分：无关
    reasons.append("与屈臣氏/即时零售/个护美妆无直接关联")
    return 0, reasons


def score_impact_scope(event: dict) -> Tuple[float, List[str]]:
    """评分维度3：影响范围 (0-5)"""
    title = event.get("event_title", "") or ""
    fact = event.get("fact", "") or ""
    evidence = event.get("evidence_text", "") or ""
    combined = f"{title} {fact} {evidence}"

    reasons = []

    # 基于实体数量和类型
    entities = event.get("entities", {}) or {}
    platforms = entities.get("platforms", []) or []
    competitors = entities.get("competitors", []) or []
    companies = entities.get("companies", []) or []
    categories = entities.get("categories", []) or []

    # 5分：全国性平台、全国性渠道、头部平台政策
    if any(kw in combined for kw in IMPACT_NATIONAL):
        reasons.append("涉及全国性/行业性影响")
        if len(platforms) >= 2:
            reasons.append(f"涉及{len(platforms)}个平台")
        return 5, reasons

    # 4分：多平台/多城市/多渠道
    if len(platforms) >= 2 or len(competitors) >= 2:
        reasons.append(f"涉及多平台({len(platforms)})或多竞对({len(competitors)})")
        return 4, reasons
    if any(kw in combined for kw in IMPACT_MULTI):
        reasons.append("多渠道/多城市影响")
        return 4, reasons

    # 3分：单平台但具代表性
    event_type = event.get("event_type", "")
    if event_type in ("platform_rule", "platform_move", "policy_change"):
        reasons.append(f"单平台但类型={event_type}具代表性")
        return 3, reasons

    # 2分：单品牌/单活动
    if len(platforms) == 1 or len(competitors) == 1:
        reasons.append("单平台或单竞对")
        return 2, reasons
    if any(kw in combined for kw in IMPACT_SINGLE):
        reasons.append("局部影响")
        return 2, reasons

    # 1分：零星案例
    if event_type in ("marketing",):
        reasons.append("营销活动影响有限")
        return 1, reasons

    # 默认2
    reasons.append("影响范围一般")
    return 2, reasons


def score_source_credibility(event: dict, source_tier_map: dict) -> Tuple[float, List[str]]:
    """评分维度4：来源可信度 (0-5)

    规则8: source_url 缺失 → 直接 ARCHIVE（硬降级在 apply_hard_downgrade 处理）
    规则3: source_credibility < 2 → 最高 P2（硬降级在 apply_hard_downgrade 处理）
    """
    source_name = event.get("source_name", "") or ""
    source_url = event.get("source_url", "") or ""
    reasons = []

    # source_url 缺失 → 可信度很低
    if not source_url.strip():
        reasons.append("source_url 缺失")
        return 0, reasons

    # 直接匹配
    if source_name in source_tier_map:
        tier = source_tier_map[source_name]
        tier_labels = {5: "官方/监管", 4: "头部权威媒体", 3: "行业/垂类媒体", 2: "聚合/自媒体", 1: "未知来源"}
        reasons.append(f"来源={source_name} → {tier_labels.get(tier, '未知')} (Tier {tier})")
        return float(tier), reasons

    # 模糊匹配
    for key, tier in source_tier_map.items():
        if key == "_unknown":
            continue
        if key in source_name or source_name in key:
            reasons.append(f"来源≈{key} → Tier {tier}")
            return float(tier), reasons

    # 未知来源
    reasons.append(f"未知来源: {source_name} → Tier 1")
    return 1, reasons


def score_data_richness(event: dict, data_keywords: list) -> Tuple[float, List[str]]:
    """评分维度5：数据丰富度 (0-5)"""
    fact = event.get("fact", "") or ""
    title = event.get("event_title", "") or ""
    evidence = event.get("evidence_text", "") or ""
    combined = f"{title} {fact} {evidence}"

    reasons = []
    found_keywords = []

    # 检查数据关键词
    for kw in data_keywords:
        if kw in combined:
            found_keywords.append(kw)

    # 检查数字模式
    value_matches = DATA_VALUE_PATTERN.findall(combined)

    # 5分：明确 GMV、增长率、订单量等量化数据
    high_value = ["GMV", "增长率", "订单量", "门店数", "用户数", "市场份额",
                  "营收", "利润", "渗透率", "市占率"]
    if any(kw in combined for kw in high_value) and value_matches:
        reasons.append(f"含明确量化数据({', '.join(found_keywords[:5])})")
        return 5, reasons

    # 4分：含业务结果或量化描述
    if value_matches and len(found_keywords) >= 2:
        reasons.append(f"含业务结果数据({', '.join(found_keywords[:5])})")
        return 4, reasons

    # 3分：具体动作但数据较少
    if value_matches or len(found_keywords) >= 2:
        reasons.append(f"含部分数据({', '.join(found_keywords[:4])})")
        return 3, reasons

    # 2分：方向性描述
    direction_words = ["提升", "增长", "下降", "下降", "突破", "加速", "放缓", "加大"]
    if any(w in combined for w in direction_words):
        reasons.append("方向性描述无具体数据")
        return 2, reasons

    # 1分：仅有观点
    opinion_words = ["认为", "观点", "趋势", "预计", "可能"]
    if any(w in combined for w in opinion_words):
        reasons.append("仅有观点/预测")
        return 1, reasons

    # 0分：无事实数据
    if not fact.strip():
        reasons.append("fact 为空")
        return 0, reasons

    reasons.append("缺乏量化数据")
    return 1, reasons


def score_actionability(event: dict, actionable_variables: dict) -> Tuple[float, List[str]]:
    """评分维度6：可执行性 (0-5)"""
    bvs = event.get("business_variables", []) or []
    event_type = event.get("event_type", "")
    fact = event.get("fact", "") or ""
    title = event.get("event_title", "") or ""
    combined = f"{title} {fact}"

    reasons = []

    # 基于 business_variables
    max_actionability = 0
    matched_vars = []
    for bv in bvs:
        if bv in actionable_variables:
            score = actionable_variables[bv]
            if score > max_actionability:
                max_actionability = score
            matched_vars.append(bv)

    # 5分：可直接转化
    direct_action_types = ["platform_rule", "policy_change", "data_signal"]
    if event_type in direct_action_types and max_actionability >= 4:
        reasons.append(f"类型={event_type}+高可执行变量({', '.join(matched_vars[:3])})")
        return 5, reasons

    # 4分：可转化为追踪或试点
    if max_actionability >= 4:
        reasons.append(f"含高可执行变量({', '.join(matched_vars[:3])})")
        return 4, reasons

    # 3分：可作为经营观察
    if max_actionability >= 3:
        reasons.append(f"含中等可执行变量({', '.join(matched_vars[:3])})")
        return 3, reasons

    # 2分：仅背景理解
    if max_actionability >= 2:
        reasons.append(f"含低可执行变量({', '.join(matched_vars[:3])})")
        return 2, reasons

    # 基于 event_type 本身的可执行性
    type_actionability = {
        "platform_rule": 5, "policy_change": 5, "competitor_move": 4,
        "data_signal": 4, "platform_move": 3, "category_trend": 3,
        "supply_chain": 3, "new_product": 3, "consumer_trend": 2,
        "marketing": 2, "background_only": 1, "unclear": 1,
    }
    type_score = type_actionability.get(event_type, 1)

    if matched_vars:
        reasons.append(f"变量可执行性低但类型={event_type}(分={type_score})")
    else:
        reasons.append(f"无可执行变量，类型={event_type}(分={type_score})")

    return float(max(type_score, max_actionability)), reasons


def score_time_sensitivity(event: dict, now: datetime) -> Tuple[float, List[str]]:
    """评分维度7：时效性 (0-5)

    映射: today=5, recent=4, background=2, old_background=1, 未知=0
    同时基于 published_at 距 now 的时间差微调。
    """
    ts_raw = event.get("time_sensitivity", "") or ""
    published_at = event.get("published_at", "") or ""
    reasons = []

    # 基于 time_sensitivity 字段
    ts_map = {
        "today": 5,
        "recent": 4,
        "background": 2,
        "old_background": 1,
    }
    base_score = ts_map.get(ts_raw, 0)

    if base_score > 0:
        reasons.append(f"time_sensitivity={ts_raw} → {base_score}分")
    else:
        reasons.append(f"time_sensitivity={ts_raw or '未知'} → 0分")

    # 基于 published_at 微调
    if published_at:
        try:
            # 解析发布时间
            if "T" in published_at:
                pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            else:
                pub_dt = datetime.fromisoformat(published_at)

            # 确保 timezone-aware
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone(timedelta(hours=8)))

            now_aware = now.replace(tzinfo=timezone(timedelta(hours=8))) if now.tzinfo is None else now
            delta_hours = (now_aware - pub_dt).total_seconds() / 3600

            if base_score == 0:
                # time_sensitivity 未知时，基于时间差推断
                if delta_hours < 24:
                    base_score = 5
                    reasons.append(f"published_at在24h内，推断时效性=5")
                elif delta_hours < 72:
                    base_score = 4
                    reasons.append(f"published_at在72h内，推断时效性=4")
                elif delta_hours < 168:
                    base_score = 2
                    reasons.append(f"published_at在一周内，推断时效性=2")
                else:
                    base_score = 1
                    reasons.append(f"published_at超过一周，推断时效性=1")
            elif base_score == 2:
                # background 但实际新鲜 → 根据时间差上调
                if delta_hours < 24:
                    base_score = 4
                    reasons.append(f"background标注但24h内发布，提升时效性到4")
                elif delta_hours < 72:
                    base_score = 3
                    reasons.append(f"background标注但72h内发布，提升时效性到3")
        except (ValueError, TypeError) as e:
            logger.debug(f"解析 published_at 失败: {published_at}, {e}")

    return float(min(max(base_score, 0), 5)), reasons


def score_novelty(event: dict, all_events: list, event_index: int) -> Tuple[float, List[str]]:
    """评分维度8：新颖度 (0-5)

    基于:
    - novelty 字段
    - 与同批其他事件的相似度（去重判别）
    """
    novelty_raw = event.get("novelty", "") or ""
    fact = event.get("fact", "") or ""
    reasons = []

    novelty_map = {"new": 5, "follow_up": 3, "unclear": 3, "old_background": 1}
    base_score = novelty_map.get(novelty_raw, 0)

    # novelty 未知但有 fact 内容 → 基于内容推断
    if base_score == 0 and fact:
        # 如果与前面事件不重复，推断为 new 级别
        is_duplicate = False
        if event_index > 0:
            for j, prev_ev in enumerate(all_events[:event_index]):
                prev_fact = prev_ev.get("fact", "") or ""
                if prev_fact and SequenceMatcher(None, fact, prev_fact).ratio() > 0.85:
                    is_duplicate = True
                    break
        if not is_duplicate:
            base_score = 3  # 首次出现的事件推断为中等新颖度
            reasons.append(f"novelty={novelty_raw or '空'}，内容无重复 → 推断=3")
        else:
            reasons.append(f"novelty={novelty_raw or '空'} → 0分")

    if base_score > 0 and not reasons:
        reasons.append(f"novelty={novelty_raw} → {base_score}分")
    elif not reasons:
        reasons.append(f"novelty={novelty_raw or '未知'} → 0分")

    # 与前面事件的简单去重检查
    fact = event.get("fact", "") or ""
    if fact and event_index > 0:
        for j, prev_ev in enumerate(all_events[:event_index]):
            prev_fact = prev_ev.get("fact", "") or ""
            if not prev_fact:
                continue
            ratio = SequenceMatcher(None, fact, prev_fact).ratio()
            if ratio > 0.85:
                reasons.append(f"与事件{j+1}高度重复(相似度={ratio:.2f})")
                base_score = max(base_score - 2, 0)
                break

    return float(min(max(base_score, 0), 5)), reasons


# ===================== 加权总分与分级 =====================

def compute_weighted_score(scores: dict, weights: dict) -> float:
    """计算加权总分 (0-5)"""
    total = 0.0
    for dim, weight in weights.items():
        dim_score = scores.get(dim, 0)
        total += dim_score * weight
    return round(total, 3)


def classify_priority(weighted_score: float, thresholds: dict) -> str:
    """基于加权总分判定优先级（降级前）"""
    p0 = thresholds.get("P0", 4.3)
    p1 = thresholds.get("P1", 3.6)
    p2 = thresholds.get("P2", 2.8)

    if weighted_score >= p0:
        return "P0"
    elif weighted_score >= p1:
        return "P1"
    elif weighted_score >= p2:
        return "P2"
    else:
        return "ARCHIVE"


def apply_hard_downgrade(
    event: dict, scores: dict, priority: str, reasons: List[str]
) -> Tuple[str, List[str]]:
    """应用硬降级规则。

    规则1: confidence=low → 最高 P2
    规则2: extraction_method=rule_fallback → 最高 P2
    规则3: source_credibility < 2 → 最高 P2
    规则4: watsons_relevance < 3 → 最高 P2
    规则5: event_type=background_only → 最高 P2
    规则6: event_type=unclear → 最高 P2
    规则7: source_url 缺失 → ARCHIVE
    规则8: fact 为空 → ARCHIVE
    规则9: evidence_text 为空 → 最高 P2
    """
    PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "ARCHIVE": 3}
    current_rank = PRIORITY_ORDER.get(priority, 2)

    confidence = event.get("confidence", "low") or "low"
    extraction_method = event.get("extraction_method", "") or ""
    source_url = event.get("source_url", "") or ""
    fact = event.get("fact", "") or ""
    evidence_text = event.get("evidence_text", "") or ""
    event_type = event.get("event_type", "unclear") or "unclear"
    source_cred = scores.get("source_credibility", 0)
    watsons_rel = scores.get("watsons_relevance", 0)

    # 规则7: source_url 缺失 → ARCHIVE
    if not source_url.strip():
        reasons.append("硬降级: source_url缺失 → ARCHIVE")
        return "ARCHIVE", reasons

    # 规则8: fact 为空 → ARCHIVE
    if not fact.strip():
        reasons.append("硬降级: fact为空 → ARCHIVE")
        return "ARCHIVE", reasons

    # 规则1: confidence=low → 最高 P2
    if confidence == "low":
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append("硬降级: confidence=low → 最高P2")
            priority = "P2"

    # 规则2: extraction_method=rule_fallback → 最高 P2
    if extraction_method == "rule_fallback":
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append("硬降级: extraction_method=rule_fallback → 最高P2")
            priority = "P2"

    # 规则3: source_credibility < 2 → 最高 P2
    if source_cred < 2:
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append(f"硬降级: source_credibility={source_cred}<2 → 最高P2")
            priority = "P2"

    # 规则4: watsons_relevance < 3 → 最高 P2
    if watsons_rel < 3:
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append(f"硬降级: watsons_relevance={watsons_rel}<3 → 最高P2")
            priority = "P2"

    # 规则5: event_type=background_only → 最高 P2
    if event_type == "background_only":
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append("硬降级: event_type=background_only → 最高P2")
            priority = "P2"

    # 规则6: event_type=unclear → 最高 P2
    if event_type == "unclear":
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append("硬降级: event_type=unclear → 最高P2")
            priority = "P2"

    # 规则9: evidence_text 为空 → 最高 P2
    if not evidence_text.strip():
        if current_rank < PRIORITY_ORDER["P2"]:
            reasons.append("硬降级: evidence_text为空 → 最高P2")
            priority = "P2"

    return priority, reasons


# ===================== 主函数 =====================

def score_events(
    project_root: str,
    date: str,
    events_file: Optional[str] = None,
    scoring_file: str = "config/scoring.yaml",
    output_file: Optional[str] = None,
) -> dict:
    """事件评分主函数。

    Args:
        project_root: 项目根目录
        date: 日期字符串 (YYYY-MM-DD)
        events_file: 事件文件路径（默认 data/events/{date}/events_raw.json）
        scoring_file: 评分配置文件（默认 config/scoring.yaml）
        output_file: 输出文件路径（默认 data/events/{date}/events_scored.json）

    Returns:
        dict: 评分结果摘要
    """
    errors: List[str] = []

    # ── 路径 ──
    if not events_file:
        events_file = resolve_path(project_root, f"data/events/{date}/events_raw.json")
    events_dir = os.path.dirname(events_file)
    log_dir = resolve_path(project_root, f"data/logs/{date}")
    os.makedirs(events_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    if not output_file:
        output_file = os.path.join(events_dir, "events_scored.json")
    log_file = os.path.join(log_dir, "score_events.log")

    # ── 日志 ──
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
    logger.addHandler(fh)

    try:
        logger.info("=" * 60)
        logger.info(f"开始评分: date={date}")
        logger.info(f"  events_file: {events_file}")
        logger.info(f"  output_file: {output_file}")

        # ── 加载配置 ──
        scoring_path = resolve_path(project_root, scoring_file)
        config = load_yaml(scoring_path)

        weights = config.get("weights", DEFAULT_WEIGHTS)
        thresholds = config.get("thresholds", DEFAULT_THRESHOLDS)

        # 确保阈值完整
        for key in ["P0", "P1", "P2", "ARCHIVE"]:
            if key not in thresholds:
                thresholds[key] = DEFAULT_THRESHOLDS[key]

        # 确保 ARCHIVE=0
        thresholds["ARCHIVE"] = 0.0

        source_tier_map = config.get("source_tier_map", DEFAULT_SOURCE_TIER_MAP)
        event_type_importance = config.get("event_type_importance", DEFAULT_EVENT_TYPE_IMPORTANCE)
        actionable_variables = config.get("actionable_variables", DEFAULT_ACTIONABLE_VARIABLES)
        data_keywords = config.get("data_keywords", DEFAULT_DATA_KEYWORDS)

        # 权重归一化
        total_weight = sum(weights.values())
        if abs(total_weight - 1.0) > 0.01:
            logger.warning(f"权重总和 {total_weight:.3f} != 1.0，将归一化")
            for k in weights:
                weights[k] = weights[k] / total_weight

        logger.info(f"权重: {json.dumps(weights, ensure_ascii=False)}")
        logger.info(f"阈值: P0>={thresholds['P0']}, P1>={thresholds['P1']}, "
                     f"P2>={thresholds['P2']}, ARCHIVE<{thresholds['P2']}")

        # ── 加载事件 ──
        if not os.path.exists(events_file):
            error_msg = f"事件文件不存在: {events_file}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {"ok": False, "date": date, "errors": errors}

        with open(events_file, "r", encoding="utf-8") as f:
            events_data = json.load(f)

        raw_events = events_data.get("events", [])
        if not raw_events:
            logger.warning("事件列表为空")
            result = {
                "ok": True, "date": date,
                "input_file": events_file, "output_file": output_file,
                "log_file": log_file, "event_count": 0,
                "p0_count": 0, "p1_count": 0, "p2_count": 0,
                "archive_count": 0, "errors": errors,
            }
            # 写空结果
            with open(output_file, "w", encoding="utf-8") as out:
                json.dump({"events": [], "metadata": result}, out, ensure_ascii=False, indent=2)
            return result

        logger.info(f"加载 {len(raw_events)} 条事件")

        # ── 评分时间 ──
        now = datetime.now(timezone(timedelta(hours=8)))

        # ── 逐条评分 ──
        scored_events = []

        for i, event in enumerate(raw_events):
            event_id = event.get("event_id", f"ev_{i+1}")
            event_title = event.get("event_title", "") or event.get("fact", "")[:50]
            logger.info(f"评分事件 {i+1}/{len(raw_events)}: {event_title[:50]}")

            # 八维度评分
            si, si_r = score_strategic_importance(event, event_type_importance)
            wr, wr_r = score_watsons_relevance(event)
            inp, inp_r = score_impact_scope(event)
            sc, sc_r = score_source_credibility(event, source_tier_map)
            dr, dr_r = score_data_richness(event, data_keywords)
            ac, ac_r = score_actionability(event, actionable_variables)
            ts, ts_r = score_time_sensitivity(event, now)
            no, no_r = score_novelty(event, raw_events, i)

            scores = {
                "strategic_importance": si,
                "watsons_relevance": wr,
                "impact_scope": inp,
                "source_credibility": sc,
                "data_richness": dr,
                "actionability": ac,
                "time_sensitivity": ts,
                "novelty": no,
            }

            # 加权总分
            weighted_score = compute_weighted_score(scores, weights)

            # 评分理由
            score_reasons = []
            for dim, r_list in [
                ("strategic_importance", si_r),
                ("watsons_relevance", wr_r),
                ("impact_scope", inp_r),
                ("source_credibility", sc_r),
                ("data_richness", dr_r),
                ("actionability", ac_r),
                ("time_sensitivity", ts_r),
                ("novelty", no_r),
            ]:
                for r in r_list:
                    score_reasons.append(f"[{dim}] {r}")

            # 初始分级
            priority = classify_priority(weighted_score, thresholds)

            # 硬降级
            downgrade_reasons: List[str] = []
            priority_after, downgrade_reasons = apply_hard_downgrade(
                event, scores, priority, downgrade_reasons
            )

            if priority_after != priority:
                logger.info(f"  硬降级: {priority} → {priority_after} "
                            f"(score={weighted_score:.3f})")
                priority = priority_after

            # 构建评分后事件
            scored_event = {**event}
            scored_event["scores"] = scores
            scored_event["weighted_score"] = weighted_score
            scored_event["priority"] = priority
            scored_event["score_reasons"] = score_reasons
            scored_event["downgrade_reasons"] = downgrade_reasons

            scored_events.append(scored_event)

            logger.info(f"  score={weighted_score:.3f} priority={priority} "
                        f"si={si} wr={wr} inp={inp} sc={sc} "
                        f"dr={dr} ac={ac} ts={ts} no={no}")

        # ── 排序 ──
        PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "ARCHIVE": 3}
        scored_events.sort(
            key=lambda ev: (
                PRIORITY_ORDER.get(ev["priority"], 2),
                -ev["weighted_score"],
                -ev.get("scores", {}).get("source_credibility", 0),
                -ev.get("scores", {}).get("data_richness", 0),
                -ev.get("scores", {}).get("time_sensitivity", 0),
            )
        )

        # ── 统计 ──
        p0_count = sum(1 for e in scored_events if e["priority"] == "P0")
        p1_count = sum(1 for e in scored_events if e["priority"] == "P1")
        p2_count = sum(1 for e in scored_events if e["priority"] == "P2")
        archive_count = sum(1 for e in scored_events if e["priority"] == "ARCHIVE")

        by_event_type = Counter(e.get("event_type", "unknown") for e in scored_events)
        by_priority = Counter(e["priority"] for e in scored_events)
        by_confidence = Counter(e.get("confidence", "unknown") for e in scored_events)

        avg_score = sum(e["weighted_score"] for e in scored_events) / len(scored_events) if scored_events else 0

        # 降级原因统计
        all_downgrade_reasons = []
        for e in scored_events:
            all_downgrade_reasons.extend(e.get("downgrade_reasons", []))
        downgrade_stats = Counter(all_downgrade_reasons)

        top_10 = [
            {
                "event_id": e.get("event_id", ""),
                "event_title": e.get("event_title", "")[:60],
                "priority": e["priority"],
                "weighted_score": e["weighted_score"],
                "watsons_relevance": e.get("scores", {}).get("watsons_relevance", 0),
            }
            for e in scored_events[:10]
        ]

        # ── 输出 ──
        output_data = {
            "events": scored_events,
            "metadata": {
                "version": "2.0",
                "date": date,
                "source_file": events_file,
                "created_at": now.isoformat(),
                "total_events": len(scored_events),
                "p0_count": p0_count,
                "p1_count": p1_count,
                "p2_count": p2_count,
                "archive_count": archive_count,
                "by_event_type": dict(by_event_type),
                "by_priority": dict(by_priority),
                "by_confidence": dict(by_confidence),
                "avg_weighted_score": round(avg_score, 3),
                "top_10_events": top_10,
                "downgrade_stats": dict(downgrade_stats),
                "weights": weights,
                "thresholds": {k: v for k, v in thresholds.items() if k in ("P0", "P1", "P2")},
            },
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"评分完成: {len(scored_events)} 条事件")
        logger.info(f"  P0={p0_count}  P1={p1_count}  P2={p2_count}  ARCHIVE={archive_count}")
        logger.info(f"  avg_weighted_score={avg_score:.3f}")
        logger.info(f"  by_priority: {dict(by_priority)}")
        logger.info(f"  by_event_type: {dict(by_event_type)}")
        logger.info(f"  downgrade_stats: {dict(downgrade_stats)}")
        for item in top_10:
            logger.info(f"  TOP: [{item['priority']}] {item['weighted_score']:.3f} "
                         f"wr={item['watsons_relevance']} {item['event_title']}")

        result = {
            "ok": True,
            "date": date,
            "input_file": events_file,
            "output_file": output_file,
            "log_file": log_file,
            "event_count": len(scored_events),
            "p0_count": p0_count,
            "p1_count": p1_count,
            "p2_count": p2_count,
            "archive_count": archive_count,
            "errors": errors,
        }

        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    except Exception as e:
        error_msg = f"评分失败: {e}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return {"ok": False, "date": date, "errors": errors}

    finally:
        logger.removeHandler(fh)
        fh.close()


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="事件评分技能")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--events-file", default=None, help="事件文件路径")
    parser.add_argument("--scoring-file", default="config/scoring.yaml", help="评分配置文件")
    parser.add_argument("--output-file", default=None, help="输出文件路径")

    args = parser.parse_args()

    result = score_events(
        project_root=args.project_root,
        date=args.date,
        events_file=args.events_file,
        scoring_file=args.scoring_file,
        output_file=args.output_file,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()