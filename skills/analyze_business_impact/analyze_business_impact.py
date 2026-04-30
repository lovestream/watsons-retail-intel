#!/usr/bin/env python3
"""
analyze_business_impact.py — 经营分析技能

读取 events_scored.json，对每条评分事件进行屈臣氏电商经营影响分析，
输出 events_analyzed.json。

支持 LLM 分析 + 规则降级模式。

CLI:
  python analyze_business_impact.py --project-root ... --date 2026-04-26
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
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
logger = logging.getLogger("analyze_business_impact")

# ===================== LLM 客户端 =====================

# 确保项目根目录在 sys.path 中
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
    logger.warning("llm_client 不可用，仅支持规则分析模式")

if _sys_path_added and _PROJECT_ROOT in sys.path:
    sys.path.remove(_PROJECT_ROOT)


# ===================== 常量 =====================

# ── 合法值枚举 ──
VALID_IMPACT_TYPES = {"opportunity", "risk", "watch", "noise"}
VALID_ACTION_LEVELS = {"immediate", "test", "watch", "archive"}
VALID_OWNER_HINTS = {
    "即时零售运营", "天猫运营", "京东运营", "商品/货盘团队",
    "价格/活动团队", "数据分析", "平台BD", "会员/私域",
    "管理层关注", "暂不分派",
}
VALID_ANALYSIS_CONFIDENCE = {"high", "medium", "low"}

# ── 渠道映射 ──
CHANNEL_KEYWORDS = {
    "美团闪购": ["美团闪购", "美团到家", "美团即时"],
    "京东秒送": ["京东秒送", "京东小时达"],
    "京东到家": ["京东到家", "达达配送"],
    "淘宝闪购": ["淘宝闪购", "淘宝即时"],
    "抖音小时达": ["抖音小时达", "抖音即时"],
    "饿了么": ["饿了么", "蜂鸟即配"],
    "天猫官方旗舰店": ["天猫旗舰店"],
    "京东旗舰店": ["京东旗舰店"],
    "天猫超市": ["天猫超市"],
    "京东自营": ["京东自营"],
    "经销商分销": ["分销", "经销商"],
    "即时零售(泛)": ["即时零售", "即时电商", "闪购", "到家业务"],
    "B2C电商(泛)": ["电商", "线上渠道"],
}

# ── 泛渠道推断：当只匹配到泛渠道时，默认关联屈臣氏核心即时零售渠道 ──
INSTANT_RETAIL_DEFAULT_CHANNELS = ["美团闪购", "京东到家"]

# ── 经营变量映射 ──
BUSINESS_VARIABLE_MAP = {
    "GMV": ["GMV", "交易额"],
    "订单量": ["订单量", "订单"],
    "客单价": ["客单价"],
    "转化率": ["转化率", "转化"],
    "复购率": ["复购", "复购率"],
    "流量": ["流量", "曝光", "UV", "PV"],
    "履约时效": ["履约", "配送时效", "送达"],
    "缺货率": ["缺货", "断货"],
    "门店覆盖": ["门店覆盖", "门店数", "门店"],
    "SKU": ["SKU", "货盘", "选品"],
    "搜索排名": ["搜索排名", "排名", "坑位"],
    "平台券": ["平台券", "优惠券", "满减", "补贴"],
    "活动坑位": ["坑位", "资源位", "活动位"],
    "会员复购": ["会员", "私域", "留存"],
    "价格竞争力": ["价格", "折扣", "促销", "价格战"],
    "竞争格局": ["竞争", "竞对", "格局"],
}

# ── event_type → 默认 impact_type 映射 ──
EVENT_TYPE_IMPACT = {
    "platform_rule": "risk",
    "platform_move": "opportunity",
    "competitor_move": "risk",
    "data_signal": "opportunity",
    "category_trend": "watch",
    "policy_change": "risk",
    "consumer_trend": "watch",
    "new_product": "opportunity",
    "supply_chain": "risk",
    "marketing": "opportunity",
    "background_only": "noise",
    "unclear": "noise",
}

# ── event_type → 默认 owner_hint 映射 ──
EVENT_TYPE_OWNER = {
    "platform_rule": "平台BD",
    "platform_move": "即时零售运营",
    "competitor_move": "即时零售运营",
    "data_signal": "数据分析",
    "category_trend": "商品/货盘团队",
    "policy_change": "平台BD",
    "consumer_trend": "商品/货盘团队",
    "new_product": "商品/货盘团队",
    "supply_chain": "商品/货盘团队",
    "marketing": "价格/活动团队",
    "background_only": "暂不分派",
    "unclear": "暂不分派",
}


# ===================== LLM 提示词 =====================

LLM_SYSTEM_PROMPT = """你是屈臣氏电商经营分析Agent。

你的服务对象是屈臣氏电商负责人。她负责即时零售渠道，包括美团闪购、京东秒送、京东到家、淘宝闪购、抖音小时达；也负责B2C渠道，包括天猫官方旗舰店、京东旗舰店；还负责To B渠道，包括天猫超市、京东自营，以及经销商分销渠道。

你的任务是读取已经评分的事件，判断该事件对屈臣氏电商经营的意义。

你不得修改事件事实。
你不得虚构数据。
你不得写日报。
你不得输出长篇文章。
你不得把低可信事件包装成强建议。

你必须基于输入事件中的 fact、evidence_text、source_url、priority、scores、confidence、extraction_method 进行分析。

请输出严格 JSON：
{
  "event_id": "",
  "business_analysis": {
    "impact_type": "opportunity|risk|watch|noise",
    "affected_channels": [],
    "affected_business_variables": [],
    "watsons_impact": "",
    "recommended_action": "",
    "action_level": "immediate|test|watch|archive",
    "owner_hint": "",
    "tracking_metrics": [],
    "follow_up_questions": [],
    "confidence": "high|medium|low"
  }
}

字段规则：

impact_type：
- opportunity：可能带来增长机会
- risk：可能带来竞争、价格、流量、履约或组织风险
- watch：值得观察但不宜立即动作
- noise：对经营意义弱

action_level：
- immediate：今天就值得推动
- test：建议小范围试点或验证
- watch：继续观察
- archive：归档

限制规则：
1. priority=ARCHIVE 时，action_level 必须为 archive。
2. confidence=low 时，action_level 不能为 immediate。
3. extraction_method=rule_fallback 时，action_level 不能为 immediate。
4. source_credibility < 2 时，action_level 不能为 immediate。
5. recommended_action 必须具体，不得写"加强关注""持续优化"这类空话。
6. 如果不能提出具体动作，action_level 应为 watch 或 archive。
7. tracking_metrics 必须是可追踪指标，如 GMV、订单量、转化率、客单价、履约时效、缺货率、活动坑位、平台券、搜索排名、门店覆盖、会员复购等。
8. owner_hint 可从以下选择：即时零售运营、天猫运营、京东运营、商品/货盘团队、价格/活动团队、数据分析、平台BD、会员/私域、管理层关注、暂不分派"""

LLM_USER_TEMPLATE = """请分析以下事件对屈臣氏电商经营的影响：

event_id: {event_id}
event_title: {event_title}
event_type: {event_type}
fact: {fact}
evidence_text: {evidence_text}
source_name: {source_name}
source_url: {source_url}
published_at: {published_at}
priority: {priority}
weighted_score: {weighted_score}

scores:
  strategic_importance: {si}
  watsons_relevance: {wr}
  impact_scope: {inp}
  source_credibility: {sc}
  data_richness: {dr}
  actionability: {ac}
  time_sensitivity: {ts}
  novelty: {no}

confidence: {confidence}
extraction_method: {extraction_method}
business_variables: {bvs}
entities: {entities}

请输出严格 JSON。"""


# ===================== 工具函数 =====================

def resolve_path(project_root: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(project_root, rel_path)


# ===================== 规则分析 =====================

def rule_infer_affected_channels(event: dict) -> List[str]:
    """从事件内容和 entities 推断受影响渠道。

    1. 先从 entities.platforms 取明确平台
    2. 再从文本关键词匹配
    3. 如果只有泛渠道，补充默认核心渠道
    """
    channels = []
    specific_channels = set()
    generic_channels = set()

    # 1. 从 entities.platforms 取
    entities = event.get("entities", {}) or {}
    platforms = entities.get("platforms", []) or []
    # entities 中的平台名可能与 CHANNEL_KEYWORDS key 不完全一样
    for p in platforms:
        matched = False
        for ch_name, keywords in CHANNEL_KEYWORDS.items():
            if p in keywords or p == ch_name:
                specific_channels.add(ch_name)
                matched = True
                break
        if not matched:
            specific_channels.add(p)

    # 2. 从文本关键词匹配
    combined = " ".join([
        event.get("event_title", "") or "",
        event.get("fact", "") or "",
        event.get("evidence_text", "") or "",
    ])
    for channel, keywords in CHANNEL_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                if channel.endswith("(泛)"):
                    generic_channels.add(channel)
                else:
                    specific_channels.add(channel)
                break

    # 3. 合并：具体渠道优先
    all_channels = list(specific_channels)

    # 如果没有具体渠道但有泛渠道，补充默认核心渠道
    if not all_channels and generic_channels:
        all_channels = list(INSTANT_RETAIL_DEFAULT_CHANNELS)

    # 去除"(泛)"后缀
    cleaned = []
    for ch in all_channels:
        ch = ch.replace("(泛)", "")
        if ch not in cleaned:
            cleaned.append(ch)

    # 如果从 entities + 文本都无法推断，检查 business_variables
    if not cleaned:
        bvs = event.get("business_variables", []) or []
        instant_bvs = ["平台资源位", "补贴", "转化率", "流量"]
        if any(bv in bvs for bv in instant_bvs):
            cleaned = list(INSTANT_RETAIL_DEFAULT_CHANNELS)

    return cleaned or ["待确认"]


def rule_infer_affected_variables(event: dict) -> List[str]:
    """从事件内容推断受影响经营变量。"""
    combined = " ".join([
        event.get("event_title", "") or "",
        event.get("fact", "") or "",
        event.get("evidence_text", "") or "",
    ])
    # 先从 business_variables 取
    bvs = event.get("business_variables", []) or []
    variables = list(bvs)

    # 补充从文本匹配
    for var_name, keywords in BUSINESS_VARIABLE_MAP.items():
        for kw in keywords:
            if kw in combined and var_name not in variables:
                variables.append(var_name)
                break

    return variables or ["待确认"]


def rule_infer_impact_type(event: dict) -> str:
    """从 event_type 推断 impact_type。"""
    event_type = event.get("event_type", "unclear")
    return EVENT_TYPE_IMPACT.get(event_type, "noise")


def rule_infer_owner_hint(event: dict) -> str:
    """从 event_type 推断 owner_hint。"""
    event_type = event.get("event_type", "unclear")
    return EVENT_TYPE_OWNER.get(event_type, "暂不分派")


def rule_infer_tracking_metrics(event: dict) -> List[str]:
    """从事件内容推断追踪指标。"""
    variables = rule_infer_affected_variables(event)
    metric_map = {
        "GMV": "GMV",
        "订单量": "订单量",
        "客单价": "客单价",
        "转化率": "转化率",
        "复购率": "复购率",
        "流量": "流量/UV",
        "履约时效": "履约时效",
        "缺货率": "缺货率",
        "门店覆盖": "门店覆盖数",
        "SKU": "SKU数",
        "搜索排名": "搜索排名",
        "平台券": "平台券使用率",
        "活动坑位": "活动坑位数",
        "会员复购": "会员复购率",
        "价格竞争力": "价格差异率",
        "竞争格局": "竞对市占率",
        "促销": "活动GMV",
        "折扣": "折扣力度",
        "满减": "满减参与率",
        "补贴": "补贴ROI",
        "平台资源位": "资源位数",
        "货盘": "货盘覆盖度",
        "选品": "选品命中率",
        "投流": "投流ROI",
        "私域": "私域转化率",
        "会员": "会员增长率",
        "品类机会": "品类增速",
    }
    metrics = []
    for var in variables:
        if var in metric_map:
            metrics.append(metric_map[var])
    return metrics or ["待定"]


def rule_infer_follow_up_questions(event: dict) -> List[str]:
    """生成后续追问。"""
    questions = []
    impact_type = rule_infer_impact_type(event)
    event_type = event.get("event_type", "unclear")

    if impact_type == "opportunity":
        questions.append("该机会的时效窗口有多长？")
        questions.append("屈臣氏在该渠道的当前份额和增速如何？")
    elif impact_type == "risk":
        questions.append("风险发生的可能性和时间节点？")
        questions.append("屈臣氏现有应对措施是否充分？")
    elif impact_type == "watch":
        questions.append("需要观察哪些前置指标变化？")

    if event_type in ("platform_rule", "policy_change"):
        questions.append("政策细则和生效时间确认？")
    if event.get("confidence") == "low":
        questions.append("数据来源是否可交叉验证？")

    return questions[:3]  # 最多 3 个


def rule_infer_watsons_impact(event: dict, impact_type: str,
                               channels: List[str]) -> str:
    """规则推断屈臣氏影响描述。"""
    event_type = event.get("event_type", "unclear")
    fact = event.get("fact", "") or ""
    title = event.get("event_title", "") or ""
    wr = event.get("scores", {}).get("watsons_relevance", 0)

    if wr >= 4:
        prefix = "直接影响屈臣氏"
    elif wr >= 3:
        prefix = "间接影响屈臣氏"
    else:
        prefix = "对屈臣氏影响较弱"

    # 渠道描述：待确认时用更自然的表述
    channel_display = "、".join(channels[:3])
    if channel_display in ("待确认", "暂不确定"):
        channel_display = "相关渠道"
    type_desc = {
        "opportunity": "增长机会",
        "risk": "潜在风险",
        "watch": "观察信号",
        "noise": "背景信息",
    }
    desc = type_desc.get(impact_type, "信号")

    return f"{prefix}在{channel_display}的{desc}：{title[:80]}"


def rule_infer_recommended_action(event: dict, impact_type: str,
                                   channels: List[str]) -> str:
    """规则推断建议动作。"""
    event_type = event.get("event_type", "unclear")
    priority = event.get("priority", "P2")
    bvs = event.get("business_variables", []) or []

    # ARCHIVE 级别
    if priority == "ARCHIVE":
        return "归档备查，无需立即行动"

    # 基于 event_type 和 bvs 生成具体建议
    action_templates = {
        "platform_rule": "确认{channel}规则变更细节，评估对屈臣氏经营参数的影响",
        "platform_move": "评估{channel}新动作对屈臣氏流量和坑位的影响",
        "competitor_move": "追踪竞对在{channel}的布局节奏，对比屈臣氏同期表现",
        "data_signal": "核实{channel}数据变化，更新内部经营看板",
        "policy_change": "确认政策合规要求，评估屈臣氏受影响品类和SKU",
        "category_trend": "评估品类趋势对屈臣氏货盘结构和选品的影响",
        "consumer_trend": "分析消费趋势对屈臣氏目标客群和营销策略的启示",
        "new_product": "评估新品对屈臣氏同品类和自有品牌的冲击或机会",
        "supply_chain": "确认供应链变化对屈臣氏备货和履约的影响",
        "marketing": "评估营销活动对屈臣氏同档期流量和转化的影响",
    }

    template = action_templates.get(event_type, "")
    # 如果渠道是"待确认"，替换为更自然的表述
    channel_str = channels[0] if channels else "相关渠道"
    if channel_str in ("待确认", "暂不确定"):
        if event_type in ("competitor_move",):
            channel_str = "其所在渠道"
        else:
            channel_str = "相关渠道"

    if template:
        action = template.format(channel=channel_str)
    else:
        action = f"关注{channel_str}相关变化"

    # 补充具体建议
    if "平台资源位" in bvs or "补贴" in bvs:
        action += "，核算补贴ROI和资源位效率"
    if "SKU" in bvs or "货盘" in bvs:
        action += "，复盘屈臣氏对应品类SKU覆盖"
    if "客单价" in bvs or "复购" in bvs:
        action += "，对比屈臣氏同渠道客单价和复购趋势"

    return action


def rule_infer_action_level(event: dict) -> str:
    """规则推断 action_level。"""
    priority = event.get("priority", "P2")
    scores = event.get("scores", {})
    ac = scores.get("actionability", 0)
    wr = scores.get("watsons_relevance", 0)

    if priority == "ARCHIVE":
        return "archive"
    if priority == "P1" and ac >= 4 and wr >= 4:
        return "test"
    if priority == "P1":
        return "test"
    if priority == "P2" and wr >= 3:
        return "watch"
    return "watch"


def rule_infer_analysis_confidence(event: dict) -> str:
    """规则推断分析置信度。"""
    confidence = event.get("confidence", "low")
    sc = event.get("scores", {}).get("source_credibility", 0)
    dr = event.get("scores", {}).get("data_richness", 0)

    if confidence == "high" and sc >= 4 and dr >= 4:
        return "high"
    elif confidence == "high" or (sc >= 3 and dr >= 3):
        return "medium"
    else:
        return "low"


def rule_analyze_event(event: dict) -> dict:
    """对单条事件进行规则分析。

    Returns:
        business_analysis dict
    """
    impact_type = rule_infer_impact_type(event)
    channels = rule_infer_affected_channels(event)
    variables = rule_infer_affected_variables(event)
    action_level = rule_infer_action_level(event)
    owner_hint = rule_infer_owner_hint(event)
    metrics = rule_infer_tracking_metrics(event)
    questions = rule_infer_follow_up_questions(event)
    watsons_impact = rule_infer_watsons_impact(event, impact_type, channels)
    recommended_action = rule_infer_recommended_action(event, impact_type, channels)
    analysis_confidence = rule_infer_analysis_confidence(event)

    return {
        "impact_type": impact_type,
        "affected_channels": channels,
        "affected_business_variables": variables,
        "watsons_impact": watsons_impact,
        "recommended_action": recommended_action,
        "action_level": action_level,
        "owner_hint": owner_hint,
        "tracking_metrics": metrics,
        "follow_up_questions": questions,
        "confidence": analysis_confidence,
        "downgrade_reasons": [],
    }


# ===================== LLM 分析 =====================

def _build_analysis_prompt(event: dict) -> str:
    """构建 LLM 分析 prompt。"""
    scores = event.get("scores", {})
    entities = event.get("entities", {})

    # 精简 entities 输出
    ent_summary = {}
    for k, v in entities.items():
        if isinstance(v, list) and v:
            ent_summary[k] = v

    return LLM_USER_TEMPLATE.format(
        event_id=event.get("event_id", ""),
        event_title=event.get("event_title", "") or "",
        event_type=event.get("event_type", "unclear"),
        fact=(event.get("fact", "") or "")[:500],
        evidence_text=(event.get("evidence_text", "") or "")[:300],
        source_name=event.get("source_name", "") or "",
        source_url=event.get("source_url", "") or "",
        published_at=event.get("published_at", "") or "",
        priority=event.get("priority", "P2"),
        weighted_score=event.get("weighted_score", 0),
        si=scores.get("strategic_importance", 0),
        wr=scores.get("watsons_relevance", 0),
        inp=scores.get("impact_scope", 0),
        sc=scores.get("source_credibility", 0),
        dr=scores.get("data_richness", 0),
        ac=scores.get("actionability", 0),
        ts=scores.get("time_sensitivity", 0),
        no=scores.get("novelty", 0),
        confidence=event.get("confidence", "low"),
        extraction_method=event.get("extraction_method", ""),
        bvs=event.get("business_variables", []),
        entities=ent_summary,
    )


def llm_analyze_event(event: dict, llm_client, max_retries: int = 1,
                       model: str = None) -> dict:
    """对单条事件进行 LLM 经营分析。

    Args:
        event: 事件字典
        llm_client: LLM 客户端
        max_retries: 最大重试次数
        model: 覆盖默认模型（None 使用 llm_client 默认）

    Returns:
        business_analysis dict (含 downgrade_reasons)
    """
    event_id = event.get("event_id", "unknown")

    # 构建 prompt
    user_prompt = _build_analysis_prompt(event)

    result = llm_client.chat(
        messages=[{"role": "user", "content": user_prompt}],
        system_prompt=LLM_SYSTEM_PROMPT,
        response_format="json",
        temperature=0.15,
        max_tokens=2048,
        model=model,
    )

    _used_model = result.get("model", model or "")
    logger.info(f"事件 {event_id} LLM分析使用模型: {_used_model}")

    content = result.get("content", "")
    parsed = result.get("parsed")

    # 尝试从 reasoning_content 提取（thinking model 支持）
    if (not parsed or not isinstance(parsed, dict)) and content:
        parsed = robust_json_extract(content)

    # 如果仍然没有，尝试从 result 中获取 raw response
    if not parsed or not isinstance(parsed, dict):
        # 再试 robust_json_extract
        raw_content = result.get("content", "")
        if raw_content:
            parsed = robust_json_extract(raw_content)

    if not parsed or not isinstance(parsed, dict):
        logger.warning(f"事件 {event_id} LLM返回无法解析")
        return None

    # 从 LLM 结果中提取 business_analysis
    ba = parsed.get("business_analysis", {})
    if not ba:
        # 可能 LLM 直接返回了分析字段
        if "impact_type" in parsed:
            ba = parsed
        else:
            logger.warning(f"事件 {event_id} LLM返回缺少business_analysis")
            return None

    # 校验和规范化
    ba = _validate_analysis(ba, event)
    return ba


def _validate_analysis(ba: dict, event: dict) -> dict:
    """校验和规范化 LLM 输出的 business_analysis。"""
    # impact_type
    if ba.get("impact_type") not in VALID_IMPACT_TYPES:
        ba["impact_type"] = rule_infer_impact_type(event)

    # affected_channels
    channels = ba.get("affected_channels", [])
    if not isinstance(channels, list) or not channels:
        ba["affected_channels"] = rule_infer_affected_channels(event)

    # affected_business_variables
    variables = ba.get("affected_business_variables", [])
    if not isinstance(variables, list) or not variables:
        ba["affected_business_variables"] = rule_infer_affected_variables(event)

    # watsons_impact
    if not ba.get("watsons_impact", "").strip():
        ba["watsons_impact"] = rule_infer_watsons_impact(
            event, ba["impact_type"], ba["affected_channels"])

    # recommended_action
    if not ba.get("recommended_action", "").strip():
        ba["recommended_action"] = rule_infer_recommended_action(
            event, ba["impact_type"], ba["affected_channels"])

    # action_level
    if ba.get("action_level") not in VALID_ACTION_LEVELS:
        ba["action_level"] = rule_infer_action_level(event)

    # owner_hint
    if ba.get("owner_hint") not in VALID_OWNER_HINTS:
        ba["owner_hint"] = rule_infer_owner_hint(event)

    # tracking_metrics
    metrics = ba.get("tracking_metrics", [])
    if not isinstance(metrics, list) or not metrics:
        ba["tracking_metrics"] = rule_infer_tracking_metrics(event)

    # follow_up_questions
    questions = ba.get("follow_up_questions", [])
    if not isinstance(questions, list):
        ba["follow_up_questions"] = []

    # confidence
    if ba.get("confidence") not in VALID_ANALYSIS_CONFIDENCE:
        ba["confidence"] = rule_infer_analysis_confidence(event)

    # downgrade_reasons 初始化
    ba.setdefault("downgrade_reasons", [])

    return ba


# ===================== 硬规则降级 =====================

def apply_analysis_downgrade(ba: dict, event: dict) -> dict:
    """对 LLM/规则 输出的 business_analysis 应用硬规则降级。

    规则:
    1. priority=ARCHIVE → action_level=archive
    2. confidence=low → action_level 不得为 immediate
    3. extraction_method=rule_fallback → action_level 不得为 immediate
    4. event_type=unclear → action_level 不得为 immediate
    5. source_credibility < 2 → action_level 不得为 immediate
    6. recommended_action 为空 → action_level=watch
    7. tracking_metrics 为空 → 降级为 watch
    """
    priority = event.get("priority", "P2")
    event_confidence = event.get("confidence", "low")
    extraction_method = event.get("extraction_method", "")
    event_type = event.get("event_type", "unclear")
    sc = event.get("scores", {}).get("source_credibility", 0)

    action_level = ba.get("action_level", "watch")
    reasons = ba.get("downgrade_reasons", [])

    # 规则1: priority=ARCHIVE → action_level=archive
    if priority == "ARCHIVE":
        if action_level != "archive":
            reasons.append("硬降级: priority=ARCHIVE → action_level=archive")
            ba["action_level"] = "archive"

    # 规则2: confidence=low → action_level 不得为 immediate
    if event_confidence == "low" and action_level == "immediate":
        reasons.append("硬降级: confidence=low → action_level不可为immediate")
        ba["action_level"] = "test"

    # 规则3: extraction_method=rule_fallback → 不得为 immediate
    if extraction_method == "rule_fallback" and ba["action_level"] == "immediate":
        reasons.append("硬降级: extraction_method=rule_fallback → action_level不可为immediate")
        ba["action_level"] = "test"

    # 规则4: event_type=unclear → 不得为 immediate
    if event_type == "unclear" and ba["action_level"] == "immediate":
        reasons.append("硬降级: event_type=unclear → action_level不可为immediate")
        ba["action_level"] = "test"

    # 规则5: source_credibility < 2 → 不得为 immediate
    if sc < 2 and ba["action_level"] == "immediate":
        reasons.append(f"硬降级: source_credibility={sc}<2 → action_level不可为immediate")
        ba["action_level"] = "test"

    # 规则6: recommended_action 为空 → watch
    if not ba.get("recommended_action", "").strip():
        reasons.append("硬降级: recommended_action为空 → action_level=watch")
        ba["action_level"] = "watch"

    # 规则7: tracking_metrics 为空 → 补充 questions 或降级
    metrics = ba.get("tracking_metrics", [])
    if not metrics or metrics == ["待定"]:
        questions = ba.get("follow_up_questions", [])
        if not questions:
            ba["follow_up_questions"] = ["需要确认可追踪的经营指标"]
        reasons.append("注意: tracking_metrics不明确，已补充follow_up_questions")
        if ba["action_level"] in ("immediate", "test"):
            reasons.append("硬降级: tracking_metrics为空 → action_level=watch")
            ba["action_level"] = "watch"

    # 空话检测：recommended_action 不含具体信息
    action = ba.get("recommended_action", "")
    vague_patterns = ["加强关注", "持续优化", "持续关注", "密切关注", "保持关注",
                      "注意观察", "持续跟进", "保持警惕", "留意"]
    for vp in vague_patterns:
        if vp in action and len(action) < 20:
            reasons.append(f"硬降级: recommended_action含空话'{vp}' → action_level=watch")
            ba["action_level"] = "watch"
            ba["recommended_action"] = action.replace(vp, "").strip() or "待确认具体动作后再行推动"
            break

    ba["downgrade_reasons"] = reasons
    return ba


# ===================== 主函数 =====================

def analyze_business_impact(
    project_root: str,
    date: str,
    events_file: Optional[str] = None,
    output_file: Optional[str] = None,
    use_llm: bool = True,
    max_events: Optional[int] = None,
) -> dict:
    """经营分析主函数。

    Args:
        project_root: 项目根目录
        date: 日期字符串 (YYYY-MM-DD)
        events_file: 评分后事件文件路径
        output_file: 输出文件路径
        use_llm: 是否使用 LLM
        max_events: 最大处理事件数

    Returns:
        dict: 分析结果摘要
    """
    errors: List[str] = []

    # ── 路径 ──
    if not events_file:
        events_file = resolve_path(project_root, f"data/events/{date}/events_scored.json")
    events_dir = os.path.dirname(events_file)
    log_dir = resolve_path(project_root, f"data/logs/{date}")
    os.makedirs(events_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    if not output_file:
        output_file = os.path.join(events_dir, "events_analyzed.json")
    log_file = os.path.join(log_dir, "analyze_business_impact.log")

    # ── 日志 ──
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
    logger.addHandler(fh)

    try:
        logger.info("=" * 60)
        logger.info(f"开始经营分析: date={date}")
        logger.info(f"  events_file: {events_file}")
        logger.info(f"  output_file: {output_file}")
        logger.info(f"  use_llm: {use_llm}")

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
                "analyzed_count": 0, "llm_failed_count": 0,
                "errors": errors,
            }
            with open(output_file, "w", encoding="utf-8") as out:
                json.dump({"events": [], "metadata": result}, out,
                          ensure_ascii=False, indent=2)
            return result

        # 限制处理数量
        if max_events and max_events > 0:
            raw_events = raw_events[:max_events]
            logger.info(f"限制处理事件数: {max_events}")

        logger.info(f"加载 {len(raw_events)} 条事件")

        # ── LLM 客户端 ──
        llm_client = None
        # ── 模型路由 ──
        _analyze_route = {}  # priority -> model 映射

        if use_llm:
            try:
                llm_client = get_llm_client()
                if not llm_client.available:
                    logger.warning("LLM 客户端不可用，降级为规则分析")
                    llm_client = None
                    use_llm = False
                else:
                    logger.info(f"LLM 可用: keys={llm_client.available_keys}, "
                                f"model={llm_client.model}")
            except Exception as e:
                logger.warning(f"LLM 客户端初始化失败: {e}，降级为规则分析")
                llm_client = None
                use_llm = False

        # 加载模型路由配置
        if use_llm:
            try:
                from skills.utils.model_router import (
                    get_model_for_skill, is_rule_only, get_model_params
                )
                for _pri in ["P0", "P1", "P2"]:
                    if not is_rule_only("analyze_business_impact", priority=_pri):
                        _m, _fb = get_model_for_skill("analyze_business_impact",
                                                       priority=_pri)
                        _analyze_route[_pri] = _m
                        logger.info(f"模型路由: analyze priority={_pri} → {_m}, "
                                    f"fallback={_fb}")
                    else:
                        _analyze_route[_pri] = "rule_only"
                        logger.info(f"模型路由: analyze priority={_pri} → rule_only")
            except Exception as e:
                logger.warning(f"模型路由加载失败: {e}，使用默认模型")
                _analyze_route = {}

        # ── 加载并行配置 ──
        from skills.utils.parallel_runner import batch_parallel_map, load_checkpoint, save_checkpoint, clear_checkpoint, load_parallel_config
        _ab_yaml = load_parallel_config(project_root) if project_root else {}
        _ab_cfg = _ab_yaml.get("analyze_business_impact", {}).get("event_parallel", {})
        _parallel_enabled = _ab_cfg.get("enabled", True)
        _priority_batches_cfg = _ab_cfg.get("priority_batches", {})
        _checkpoint_filename = _ab_cfg.get("checkpoint_file", "analyze_business_checkpoint.jsonl")
        _checkpoint_path = os.path.join(events_dir, _checkpoint_filename)
        _fallback_to_rule = _ab_cfg.get("fallback_to_rule", True)

        # ── 分流事件到批次 ──
        archive_indices = []   # (original_index, event)
        p2_indices = []        # (original_index, event) — Lite
        p1_indices = []        # (original_index, event) — Chat
        p0_indices = []        # (original_index, event) — Chat, low concurrency
        other_indices = []     # 未知优先级，用规则

        for i, event in enumerate(raw_events):
            priority = event.get("priority", "P2")
            if priority == "ARCHIVE" or not (use_llm and llm_client):
                archive_indices.append((i, event))
            elif priority == "P0" and use_llm and llm_client:
                p0_indices.append((i, event))
            elif priority == "P1" and use_llm and llm_client:
                p1_indices.append((i, event))
            elif priority == "P2" and use_llm and llm_client:
                p2_indices.append((i, event))
            else:
                other_indices.append((i, event))

        logger.info(f"事件分流: ARCHIVE={len(archive_indices)}, "
                    f"P0={len(p0_indices)}, P1={len(p1_indices)}, "
                    f"P2={len(p2_indices)}, other(rule)={len(other_indices)}")

        # ── 清除旧 checkpoint ──
        clear_checkpoint(_checkpoint_path)

        # ── 各批次模型与并发配置 ──
        # 默认使用 model_router.yaml 的 priority_routing，parallel.yaml 可覆盖
        batch_workers = {}
        batch_timeouts = {}
        batch_models = {}

        # P2: Lite, 高并发
        p2_cfg = _priority_batches_cfg.get("P2", {})
        batch_workers["P2"] = p2_cfg.get("max_workers", 6)
        batch_timeouts["P2"] = p2_cfg.get("timeout", 60)
        batch_models["P2"] = p2_cfg.get("model", _analyze_route.get("P2", "LongCat-Flash-Lite"))

        # P1: Chat, 中并发
        p1_cfg = _priority_batches_cfg.get("P1", {})
        batch_workers["P1"] = p1_cfg.get("max_workers", 4)
        batch_timeouts["P1"] = p1_cfg.get("timeout", 120)
        batch_models["P1"] = p1_cfg.get("model", _analyze_route.get("P1", "LongCat-Flash-Chat"))

        # P0: Chat, 低并发
        p0_cfg = _priority_batches_cfg.get("P0", {})
        batch_workers["P0"] = p0_cfg.get("max_workers", 2)
        batch_timeouts["P0"] = p0_cfg.get("timeout", 150)
        batch_models["P0"] = p0_cfg.get("model", _analyze_route.get("P0", "LongCat-Flash-Chat"))

        # ARCHIVE: 纯规则
        batch_workers["ARCHIVE"] = 0

        llm_success_count = 0
        llm_failed_count = 0

        # ── 串行处理 ARCHIVE ──
        llm_results = {}  # {original_index: (event, ba)}
        for i, event in archive_indices:
            ba = rule_analyze_event(event)
            logger.info(f"  规则分析 [ARCHIVE]: impact={ba.get('impact_type')} "
                        f"action={ba.get('action_level')}")
            llm_results[i] = (event, ba)

        # ── 串行处理 other(rule_only) ──
        for i, event in other_indices:
            ba = rule_analyze_event(event)
            logger.info(f"  规则分析 [other]: impact={ba.get('impact_type')} "
                        f"action={ba.get('action_level')}")
            llm_results[i] = (event, ba)

        # ── 并行处理 P2/P1/P0 ──
        def _analyze_one_event(event, orig_idx, batch_name):
            """单条事件 LLM 分析，含规则兜底。

            使用对应批次模型，失败时回退到规则分析。
            """
            event_id = event.get("event_id", f"ev_{orig_idx+1}")
            model = batch_models.get(batch_name, "LongCat-Flash-Lite")

            try:
                ba = llm_analyze_event(event, llm_client, model=model)
                if ba is not None:
                    # 保存 checkpoint
                    save_checkpoint(_checkpoint_path, event_id, {
                        "status": "success",
                        "batch": batch_name,
                        "model": model,
                    })
                    return {"ba": ba, "success": True, "batch": batch_name, "model": model}
                else:
                    # LLM 返回 None，规则兜底
                    if _fallback_to_rule:
                        ba = rule_analyze_event(event)
                        logger.info(f"  事件 {event_id} LLM返回None, 规则兜底: "
                                    f"impact={ba.get('impact_type')}")
                        save_checkpoint(_checkpoint_path, event_id, {
                            "status": "fallback_rule",
                            "batch": batch_name,
                            "model": model,
                        })
                        return {"ba": ba, "success": False, "batch": batch_name, "model": model}
                    else:
                        save_checkpoint(_checkpoint_path, event_id, {
                            "status": "failed",
                            "batch": batch_name,
                            "model": model,
                        })
                        return {"ba": None, "success": False, "batch": batch_name, "model": model}
            except Exception as e:
                logger.warning(f"  事件 {event_id} LLM分析异常: {e}")
                if _fallback_to_rule:
                    ba = rule_analyze_event(event)
                    logger.info(f"  事件 {event_id} LLM异常, 规则兜底: "
                                f"impact={ba.get('impact_type')}")
                    save_checkpoint(_checkpoint_path, event_id, {
                        "status": "fallback_rule_exception",
                        "batch": batch_name,
                        "model": model,
                        "error": str(e)[:100],
                    })
                    return {"ba": ba, "success": False, "batch": batch_name, "model": model}
                else:
                    save_checkpoint(_checkpoint_path, event_id, {
                        "status": "failed_exception",
                        "batch": batch_name,
                        "model": model,
                        "error": str(e)[:100],
                    })
                    return {"ba": None, "success": False, "batch": batch_name, "model": model}

        if _parallel_enabled and (p2_indices or p1_indices or p0_indices):
            items_by_batch = {}
            if p2_indices:
                items_by_batch["P2"] = p2_indices
            if p1_indices:
                items_by_batch["P1"] = p1_indices
            if p0_indices:
                items_by_batch["P0"] = p0_indices

            logger.info(f"并行 LLM 分析: P2={len(p2_indices)}, P1={len(p1_indices)}, "
                        f"P0={len(p0_indices)}, models={batch_models}")

            _batch_results, _batch_stats = batch_parallel_map(
                items_by_batch=items_by_batch,
                process_fn=_analyze_one_event,
                batch_workers=batch_workers,
                batch_timeouts=batch_timeouts,
                desc="analyze_business_impact",
                continue_on_error=True,
            )

            logger.info(f"并行分析统计: {_batch_stats}")

            # 从 batch_results 中提取结果（按原索引）并统计
            # batch_results 是与 items_by_batch 展开顺序对应的列表，
            # 但 batch_parallel_map 返回的是 total_items 长度的列表，
            # 其中每个位置对应 (original_index, item) 中的 original_index
            # 我们需要把它映射回原索引
            # 实际上 batch_parallel_map 返回的是按 total_items 长度排的 results
            # 我们需要遍历所有 batches 的 items 来重建 index 映射
            _all_llm_items = []
            for batch_name in ["P2", "P1", "P0"]:
                if batch_name in items_by_batch:
                    _all_llm_items.extend(items_by_batch[batch_name])

            for _bi, (orig_idx, _event) in enumerate(_all_llm_items):
                # 查找结果 — batch_parallel_map 按 total_items 排列
                # 我们需要从 batch_results 中找 orig_idx 对应的位置
                pass

            # 实际上 batch_parallel_map 返回的是按 original_index 排列的结果
            # 我们需要遍历所有 batch 的 items 来找到 batch_results 的位置
            _result_idx = 0
            for batch_name in ["P2", "P1", "P0"]:
                if batch_name not in items_by_batch:
                    continue
                for orig_idx, event in items_by_batch[batch_name]:
                    if _result_idx < len(_batch_results):
                        _r = _batch_results[_result_idx]
                        _result_idx += 1
                        if _r is not None:
                            ba = _r.get("ba")
                            success = _r.get("success", False)
                            if success:
                                llm_success_count += 1
                            else:
                                llm_failed_count += 1
                            if ba is None:
                                ba = rule_analyze_event(event)
                                logger.info(f"  兜底规则分析: impact={ba.get('impact_type')} "
                                            f"action={ba.get('action_level')}")
                            llm_results[orig_idx] = (event, ba)
                        else:
                            # batch_parallel_map 返回 None（超时/异常）
                            llm_failed_count += 1
                            ba = rule_analyze_event(event)
                            logger.info(f"  超时兜底规则分析: impact={ba.get('impact_type')}")
                            llm_results[orig_idx] = (event, ba)
        else:
            # 串行模式
            logger.info("串行 LLM 分析模式")
            for _prio_name, _indices_list in [("P2", p2_indices), ("P1", p1_indices), ("P0", p0_indices)]:
                model = batch_models.get(_prio_name, "LongCat-Flash-Lite")
                for orig_idx, event in _indices_list:
                    event_id = event.get("event_id", f"ev_{orig_idx+1}")
                    try:
                        ba = llm_analyze_event(event, llm_client, model=model)
                        if ba is not None:
                            llm_success_count += 1
                            logger.info(f"  事件 {event_id} [{_prio_name}] LLM分析成功: "
                                        f"impact={ba.get('impact_type')} "
                                        f"action={ba.get('action_level')}")
                        else:
                            llm_failed_count += 1
                            ba = rule_analyze_event(event)
                            logger.info(f"  事件 {event_id} [{_prio_name}] LLM返回None, 规则兜底")
                    except Exception as e:
                        llm_failed_count += 1
                        ba = rule_analyze_event(event)
                        logger.warning(f"  事件 {event_id} [{_prio_name}] LLM异常, 规则兜底: {e}")
                    llm_results[orig_idx] = (event, ba)

        # ── 组装结果 ──
        analyzed_events = []
        for i, event in enumerate(raw_events):
            if i in llm_results:
                _, ba = llm_results[i]
            else:
                ba = None

            if ba is None:
                ba = rule_analyze_event(event)
                logger.info(f"  兜底规则分析: impact={ba.get('impact_type')} "
                            f"action={ba.get('action_level')}")

            # 硬规则降级
            ba = apply_analysis_downgrade(ba, event)
            if ba.get("downgrade_reasons"):
                for r in ba["downgrade_reasons"]:
                    logger.info(f"  ⬇️ {r}")

            analyzed_event = {**event}
            analyzed_event["business_analysis"] = ba
            analyzed_events.append(analyzed_event)

        # ── 统计 ──
        analyzed_count = len(analyzed_events)

        by_priority = Counter(e.get("priority", "unknown") for e in analyzed_events)
        by_impact_type = Counter(
            e["business_analysis"]["impact_type"] for e in analyzed_events)
        by_action_level = Counter(
            e["business_analysis"]["action_level"] for e in analyzed_events)
        by_owner_hint = Counter(
            e["business_analysis"]["owner_hint"] for e in analyzed_events)

        immediate_count = by_action_level.get("immediate", 0)
        test_count = by_action_level.get("test", 0)
        watch_count = by_action_level.get("watch", 0)
        archive_count = by_action_level.get("archive", 0)

        # 降级原因统计
        all_downgrade_reasons = []
        for e in analyzed_events:
            all_downgrade_reasons.extend(
                e["business_analysis"].get("downgrade_reasons", []))
        downgrade_stats = Counter(all_downgrade_reasons)

        # top_action_events: action_level 为 immediate 或 test 的事件
        top_action = [
            {
                "event_id": e.get("event_id", ""),
                "event_title": e.get("event_title", "")[:60],
                "priority": e.get("priority", ""),
                "impact_type": e["business_analysis"]["impact_type"],
                "action_level": e["business_analysis"]["action_level"],
                "owner_hint": e["business_analysis"]["owner_hint"],
                "recommended_action": e["business_analysis"]["recommended_action"][:80],
            }
            for e in analyzed_events
            if e["business_analysis"]["action_level"] in ("immediate", "test")
        ]

        # ── 输出 ──
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8)))

        output_data = {
            "events": analyzed_events,
            "metadata": {
                "version": "1.0",
                "date": date,
                "source_file": events_file,
                "created_at": now.isoformat(),
                "event_count": analyzed_count,
                "analyzed_count": analyzed_count,
                "llm_success_count": llm_success_count,
                "llm_failed_count": llm_failed_count,
                "by_priority": dict(by_priority),
                "by_impact_type": dict(by_impact_type),
                "by_action_level": dict(by_action_level),
                "by_owner_hint": dict(by_owner_hint),
                "immediate_count": immediate_count,
                "test_count": test_count,
                "watch_count": watch_count,
                "archive_count": archive_count,
                "downgrade_stats": dict(downgrade_stats),
                "top_action_events": top_action,
            },
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"经营分析完成: {analyzed_count} 条事件")
        logger.info(f"  LLM成功={llm_success_count}, LLM失败={llm_failed_count}")
        logger.info(f"  immediate={immediate_count} test={test_count} "
                     f"watch={watch_count} archive={archive_count}")
        logger.info(f"  by_impact_type: {dict(by_impact_type)}")
        logger.info(f"  by_action_level: {dict(by_action_level)}")
        logger.info(f"  by_owner_hint: {dict(by_owner_hint)}")
        logger.info(f"  downgrade_stats: {dict(downgrade_stats)}")

        for item in top_action:
            logger.info(f"  ACTION: [{item['action_level']}] "
                         f"{item['owner_hint']} — "
                         f"{item['recommended_action']}")

        result = {
            "ok": True,
            "date": date,
            "input_file": events_file,
            "output_file": output_file,
            "log_file": log_file,
            "event_count": analyzed_count,
            "analyzed_count": analyzed_count,
            "llm_success_count": llm_success_count,
            "llm_failed_count": llm_failed_count,
            "errors": errors,
        }

        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    except Exception as e:
        error_msg = f"经营分析失败: {e}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return {"ok": False, "date": date, "errors": errors}

    finally:
        logger.removeHandler(fh)
        fh.close()


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="经营分析技能")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--events-file", default=None, help="评分后事件文件路径")
    parser.add_argument("--output-file", default=None, help="输出文件路径")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用LLM (true/false)")
    parser.add_argument("--max-events", type=int, default=None,
                        help="最大处理事件数")
    parser.add_argument("--test-llm", action="store_true",
                        help="测试 LLM 连接")

    args = parser.parse_args()

    # ── 测试 LLM 连接 ──
    if args.test_llm:
        if not _LLM_AVAILABLE:
            print("❌ llm_client 不可用")
            sys.exit(1)
        result = test_llm_connection()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("ok") else 1)

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = analyze_business_impact(
        project_root=args.project_root,
        date=args.date,
        events_file=args.events_file,
        output_file=args.output_file,
        use_llm=use_llm,
        max_events=args.max_events,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()