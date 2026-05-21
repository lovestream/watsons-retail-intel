#!/usr/bin/env python3
"""
extract_events.py — 从清洗后的文章中抽取结构化事件

三类输出：
1. events_raw.json        — 成功抽取的事件
2. events_rejected_articles.json — 业务拒绝（文章本身无明确事件）
3. events_failed_articles.json  — 技术失败（LLM错误、JSON截断等）

用法:
    python extract_events.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-04-26 \
        --use-llm true \
        --max-articles 20

    # LLM 连通性测试
    python extract_events.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --test-llm
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ===================== 依赖检查 =====================
_MISSING = []
try:
    import yaml
except ImportError:
    _MISSING.append("PyYAML")

if _MISSING:
    print(f"ERROR: 缺少必要依赖: {', '.join(_MISSING)}\n"
          f"请运行: pip install {' '.join(_MISSING)}", file=sys.stderr)
    sys.exit(1)

# ===================== 常量 =====================

EVENT_TYPES = [
    "platform_move", "platform_rule", "competitor_move", "brand_move",
    "category_trend", "channel_shift", "consumer_scene", "data_signal",
    "policy_signal", "background_only", "unclear",
]

BUSINESS_VARIABLES = [
    "流量", "转化率", "客单价", "复购", "价格", "补贴", "毛利",
    "货盘", "SKU", "履约", "会员", "私域", "活动", "投流",
    "门店覆盖", "平台资源位", "组织执行", "竞争格局", "品类机会", "风险",
]

TIME_SENSITIVITY = ["today", "recent", "background"]
NOVELTY = ["new", "follow_up", "old_background"]
CONFIDENCE_LEVELS = ["high", "medium", "low"]

# 失败类型枚举
FAILURE_TYPES = [
    "llm_api_error",       # LLM API 调用失败
    "empty_content",       # LLM 返回 content 为空
    "json_parse_failed",   # JSON 解析失败（含截断）
    "truncated_json",      # JSON 截断
    "invalid_schema",      # LLM 输出格式不合规
    "timeout",             # 超时
    "unknown",             # 未知错误
]

# 规则兜底触发关键词
RULE_FALLBACK_TITLE_KEYWORDS = [
    "屈臣氏",
    "美团闪购", "京东秒送", "京东到家", "淘宝闪购", "抖音小时达",
    "GMV", "增长", "突破", "合作", "上线", "入驻", "加码", "布局",
]

# ===================== LLM 提示词 =====================

LLM_SYSTEM_PROMPT = (
    "你是即时零售与个护美妆行业事件抽取Agent。\n"
    "你的唯一任务是从输入文章中抽取\"明确发生的事实事件\"，并输出一个JSON对象。\n\n"
    "严格禁止：\n"
    "- 写日报或经营建议\n"
    "- 过度推断或虚构事实\n"
    "- 输出任何JSON之外的文字（包括思考过程、解释、注释）\n\n"
    "规则：\n"
    "1. 文章包含明确新增事实 → 抽取1个或多个事件\n"
    "2. 没有新增事实 → article_reject=true\n"
    "3. 每个事件必须有 fact 和 evidence_text\n"
    "4. 事件服务于屈臣氏电商经营分析\n\n"
    "confidence评定标准（重要）：\n"
    "- high: 事实明确、有具体数据或官方来源、时新性强（今天/昨天发生或明确标注日期）\n"
    "- medium: 事实较明确但缺少关键数据细节，或时效为近期但非当天\n"
    "- low: 事实模糊、无法验证、或仅为趋势/观点而非具体事件\n"
    "注意：不要默认打low！如果文章明确报道了某个事件且有具体信息，就打high或medium。\n\n"
    "事件类型枚举：\n"
    + ", ".join(EVENT_TYPES) + "\n\n"
    "业务变量枚举：\n"
    + ", ".join(BUSINESS_VARIABLES) + "\n\n"
    "重要：只输出一个JSON对象，不要输出任何其他文字。"
)

LLM_USER_PROMPT_TEMPLATE = """从下面文章中抽取事件，只输出JSON，不要输出任何其他文字。

文章ID：{article_id}
标题：{title}
来源：{source_name}
时间：{published_at}
摘要：{summary}

正文：{content}

输出格式：

{{
  "article_id": "{article_id}",
  "events": [
    {{
      "event_title": "",
      "event_type": "",
      "fact": "",
      "evidence_text": "",
      "entities": {{ "platforms": [], "companies": [], "competitors": [], "brands": [], "channels": [], "categories": [] }},
      "business_variables": [],
      "time_sensitivity": "",
      "novelty": "",
      "confidence": "",
      "reject": false,
      "reject_reason": ""
    }}
  ],
  "article_reject": false,
  "article_reject_reason": ""
}}"""

# 极简重试提示词
SIMPLIFIED_SYSTEM_PROMPT = (
    "你是事件抽取器。只输出严格JSON，不要输出推理过程、不要Markdown、不要解释。"
    "事件类型枚举: " + ", ".join(EVENT_TYPES) + "。"
    "业务变量枚举: " + ", ".join(BUSINESS_VARIABLES) + "。"
    "最多抽取3个事件。"
    "confidence: high=事实明确+有数据/官方来源; medium=事实较明确但少数据; low=模糊/无法验证。不要默认打low。"
)

SIMPLIFIED_USER_TEMPLATE = """请只从文章中抽取明确事实事件，不要解释，不要建议。

标题：{title}
摘要：{summary}
正文：{content}

输出严格JSON：
{{
  "events": [
    {{
      "event_title": "",
      "fact": "",
      "evidence_text": "",
      "event_type": "",
      "business_variables": [],
      "confidence": "high|medium|low"
    }}
  ],
  "article_reject": false,
  "article_reject_reason": ""
}}

如果文章没有明确事实：
{{
  "events": [],
  "article_reject": true,
  "article_reject_reason": "没有明确新增事实"
}}"""


# ===================== 工具函数 =====================


def _title_char_set(title: str) -> set:
    """提取标题中的中文字符集合（用于 Jaccard 相似度）。"""
    return set(c for c in title if '\u4e00' <= c <= '\u9fff')


def _jaccard_similarity(s1: set, s2: set) -> float:
    """计算两个集合的 Jaccard 相似度。"""
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)


def _event_richness_score(event: dict) -> float:
    """评估事件信息丰富度（用于去重时选择保留哪个）。"""
    score = 0.0
    # 有 summary 且较长
    summary = event.get("summary", "")
    score += min(len(summary) / 200, 2.0)
    # 有 business_analysis
    ba = event.get("business_analysis", {})
    if ba:
        score += 1.0
        if ba.get("watson_impact"):
            score += 0.5
        if ba.get("action_level") == "immediate":
            score += 0.5
    # 有 entities
    entities = event.get("entities", {})
    if entities:
        score += 0.3 * min(len(str(entities)), 5)
    # confidence
    if event.get("confidence") == "high":
        score += 1.0
    elif event.get("confidence") == "medium":
        score += 0.5
    # source_articles 数量
    score += 0.2 * len(event.get("source_articles", []))
    return score


def deduplicate_events(events: list, similarity_threshold: float = 0.55) -> tuple:
    """对事件列表进行去重。

    策略：
    1. 基于标题中文字符的 Jaccard 相似度
    2. 相似度 > threshold 且主体实体有交集 → 视为重复
    3. 保留信息最丰富的版本，合并 source_articles

    Returns:
        (deduplicated_events, merge_log)
    """
    if not events:
        return events, []

    # 预计算标题字符集
    char_sets = [_title_char_set(e.get("event_title", "")) for e in events]

    # 标记哪些事件被合并掉了
    merged_into = {}  # index → merged_into_index
    merge_log = []

    for i in range(len(events)):
        if i in merged_into:
            continue
        for j in range(i + 1, len(events)):
            if j in merged_into:
                continue

            sim = _jaccard_similarity(char_sets[i], char_sets[j])
            if sim < similarity_threshold:
                continue

            # 检查主体实体是否有交集
            ent_i = set()
            ent_j = set()
            for key in ("companies", "brands", "platforms"):
                ent_i.update(events[i].get("entities", {}).get(key, []))
                ent_j.update(events[j].get("entities", {}).get(key, []))

            # 如果两个都有实体但无交集，不合并
            if ent_i and ent_j and not (ent_i & ent_j):
                continue

            # 决定保留哪个
            score_i = _event_richness_score(events[i])
            score_j = _event_richness_score(events[j])

            if score_j > score_i:
                # j 更丰富，合并 i 到 j
                keeper, absorbed = j, i
            else:
                keeper, absorbed = i, j

            merged_into[absorbed] = keeper

            # 合并 source_articles
            keeper_sources = events[keeper].get("source_articles", [])
            absorbed_sources = events[absorbed].get("source_articles", [])
            existing_urls = {s.get("url") for s in keeper_sources}
            for src in absorbed_sources:
                if src.get("url") not in existing_urls:
                    keeper_sources.append(src)
            events[keeper]["source_articles"] = keeper_sources

            merge_log.append({
                "kept": events[keeper].get("event_title", "")[:60],
                "absorbed": events[absorbed].get("event_title", "")[:60],
                "similarity": round(sim, 3),
            })

    # 构建去重后的列表
    deduped = [e for idx, e in enumerate(events) if idx not in merged_into]
    return deduped, merge_log


def load_yaml(filepath: str) -> dict:
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(project_root: str, rel_path: str) -> str:
    return str(Path(project_root) / rel_path)


def generate_event_id(date: str, index: int) -> str:
    date_compact = date.replace("-", "")
    return f"E{date_compact}_{index:04d}"


def _classify_failure(result: dict, content: str = "") -> str:
    """根据 LLM 返回结果判断失败类型。"""
    if not result.get("ok"):
        error = result.get("error", "")
        if "timeout" in error.lower() or "超时" in error:
            return "timeout"
        if "connection" in error.lower() or "连接" in error:
            return "llm_api_error"
        return "llm_api_error"

    parsed = result.get("parsed")
    if not parsed and not content:
        return "empty_content"
    if not parsed:
        # 检查是否截断
        if content and content.rstrip().endswith((",", '"', "[", "{")):
            return "truncated_json"
        return "json_parse_failed"

    # parsed 成功但格式不对
    if not isinstance(parsed, dict):
        return "invalid_schema"

    return "unknown"


# ===================== LLM 事件抽取 =====================


def _build_extraction_prompt(article: dict) -> tuple:
    article_id = article.get("article_id", "")
    if not article_id:
        title = article.get("title", "") or ""
        source = article.get("source_name", "") or ""
        article_id = f"{source}_{hash(title) % 100000:05d}"

    user_prompt = LLM_USER_PROMPT_TEMPLATE.format(
        article_id=article_id,
        title=article.get("title", "") or "",
        source_name=article.get("source_name", "") or "",
        published_at=article.get("published_at", "") or "",
        summary=article.get("summary", "") or "",
        content=(article.get("content", "") or "")[:2000],
    )
    return LLM_SYSTEM_PROMPT, user_prompt


def _build_simplified_prompt(article: dict) -> tuple:
    """极简重试 prompt。"""
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    content = (article.get("content", "") or "")[:800]
    user_prompt = SIMPLIFIED_USER_TEMPLATE.format(
        title=title, summary=summary, content=content,
    )
    return SIMPLIFIED_SYSTEM_PROMPT, user_prompt


def llm_extract_events(
    article: dict,
    llm_client,
    logger: logging.Logger,
    model: str = None,
) -> dict:
    """对单篇文章进行 LLM 事件抽取（含二次重试）。

    Args:
        article: 清洗后文章
        llm_client: LLM 客户端
        logger: 日志器
        model: 覆盖默认模型（None 使用 llm_client 默认模型）

    Returns:
        dict with keys:
        - article_id: str
        - events: list
        - article_reject: bool
        - article_reject_reason: str
        - llm_extracted: bool
        - failure_type: str|None   (技术失败时有值)
        - failure_reason: str|None
        - raw_llm_preview: str      (失败时保留预览)
        - attempt_count: int
    """
    article_id = article.get("article_id", "")
    default_result = {
        "article_id": article_id,
        "events": [],
        "article_reject": True,
        "article_reject_reason": "LLM不可用",
        "llm_extracted": False,
        "failure_type": None,
        "failure_reason": None,
        "raw_llm_preview": "",
        "attempt_count": 0,
    }

    if llm_client is None or not llm_client.available:
        logger.warning("LLM 不可用，跳过事件抽取")
        default_result["failure_type"] = "llm_api_error"
        default_result["failure_reason"] = "LLM不可用"
        return default_result

    # ── 第一次尝试：完整 prompt ──
    system_prompt, user_prompt = _build_extraction_prompt(article)

    # ── fallback 模型列表（用于二次重试时可切换模型） ──
    _fallback_models = []
    try:
        from skills.utils.model_router import get_model_for_skill
        _, _fallback_models = get_model_for_skill("extract_events")
    except Exception:
        pass

    result = llm_client.chat(
        messages=[{"role": "user", "content": user_prompt}],
        system_prompt=system_prompt,
        response_format="json",
        temperature=0.15,
        max_tokens=4096,
        model=model,
    )

    content = result.get("content", "")
    parsed = result.get("parsed")
    _used_model = result.get("model", model or "")
    attempt_count = 1

    # 辅助：从 parsed 结果中提取标准格式
    def _extract_from_parsed(p: dict) -> Optional[dict]:
        """从 LLM parsed 结果中提取并校验。"""
        if not p or not isinstance(p, dict):
            return None
        p.setdefault("article_id", article_id)
        p.setdefault("article_reject", False)
        p.setdefault("article_reject_reason", "")
        p["llm_extracted"] = True

        events = p.get("events", [])
        if not isinstance(events, list):
            events = []

        validated = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            # setdefault 所有必要字段
            for field, default in [
                ("event_title", ""), ("event_type", "unclear"),
                ("fact", ""), ("evidence_text", ""),
                ("business_variables", []), ("time_sensitivity", "background"),
                ("novelty", "unclear"), ("confidence", "low"),
                ("reject", False), ("reject_reason", ""),
            ]:
                ev.setdefault(field, default)
            ev.setdefault("entities", {
                "platforms": [], "companies": [], "competitors": [],
                "brands": [], "channels": [], "categories": [],
            })
            # 校验枚举
            if ev["event_type"] not in EVENT_TYPES:
                ev["event_type"] = "unclear"
            bvs = ev["business_variables"]
            if isinstance(bvs, list):
                ev["business_variables"] = [v for v in bvs if v in BUSINESS_VARIABLES]
            else:
                ev["business_variables"] = []
            if ev["time_sensitivity"] not in TIME_SENSITIVITY:
                ev["time_sensitivity"] = "background"
            if ev["novelty"] not in NOVELTY:
                ev["novelty"] = "unclear"
            if ev["confidence"] not in CONFIDENCE_LEVELS:
                ev["confidence"] = "low"
            # fact 为空标记
            if not ev.get("fact", "").strip():
                ev["reject"] = True
                ev["reject_reason"] = ev.get("reject_reason") or "fact为空"
            validated.append(ev)

        p["events"] = validated
        # events 为空：区分业务拒绝 vs 疑似技术问题
        if not validated:
            if p.get("article_reject"):
                # LLM 明确说 article_reject=True → 业务拒绝
                pass
            elif p.get("article_reject_reason"):
                # 有拒绝理由但 article_reject 为 False → 视为业务拒绝
                p["article_reject"] = True
            else:
                # article_reject=False 且无理由 → 疑似技术问题
                # 内容相关但 LLM 未能输出事件，标记为技术失败
                # (常见于 thinking 模型将答案放在 reasoning 中)
                title = article.get("title", "") or ""
                # 简单检查：有没有疑似屈臣氏/GMV等关键词
                suspect_words = ["屈臣氏", "GMV", "突破", "增长", "发布", "推出",
                                 "上线", "合作", "签约", "开店", "关闭", "退出"]
                has_suspect = any(w in title for w in suspect_words)
                if has_suspect:
                    # 标题疑似包含事件 → LLM 输出丢失，判定技术失败
                    p["article_reject"] = False
                    p["article_reject_reason"] = ""
                    p["_empty_events_suspect_failure"] = True
                    logger.warning(
                        f"  文章 {article_id} events 为空且 article_reject 未设置，"
                        f"但标题含疑似事件关键词 → 疑似技术失败"
                    )
                else:
                    # 无法判断，保守归为业务拒绝
                    p["article_reject"] = True
                    p["article_reject_reason"] = "LLM未输出事件(无明确reject标记)"
        return p

    # ── 第一次尝试成功 ──
    if parsed and isinstance(parsed, dict):
        extracted = _extract_from_parsed(parsed)
        if extracted is not None:
            extracted["attempt_count"] = attempt_count
            extracted["failure_type"] = None
            extracted["failure_reason"] = None
            extracted["raw_llm_preview"] = ""
            return extracted

    # ── 第一次失败：分类失败原因 ──
    failure_type = _classify_failure(result, content)
    content_preview = content[:300] if content else "(空)"
    logger.warning(f"文章 {article_id} 首次抽取失败: {failure_type}, "
                   f"content长度={len(content)}")

    # ── 第二次尝试：极简 prompt，使用 fallback 模型 ──
    # 如果第一次用的不是 fallback 模型，切换到 fallback 模型
    _retry_model = model  # 第一次用的模型
    if _fallback_models and (_retry_model is None or _retry_model not in _fallback_models):
        _retry_model = _fallback_models[0]  # 使用第一个 fallback 模型

    logger.info(f"文章 {article_id} 用极简 prompt 重试, 模型={_retry_model or '默认'}...")
    simpl_system, simpl_user = _build_simplified_prompt(article)
    result2 = llm_client.chat(
        messages=[{"role": "user", "content": simpl_user}],
        system_prompt=simpl_system,
        response_format="json",
        temperature=0.1,
        max_tokens=2048,
        model=_retry_model,
    )
    attempt_count = 2
    content2 = result2.get("content", "")
    parsed2 = result2.get("parsed")

    if parsed2 and isinstance(parsed2, dict):
        extracted = _extract_from_parsed(parsed2)
        if extracted is not None:
            extracted["attempt_count"] = attempt_count
            extracted["failure_type"] = None
            extracted["failure_reason"] = None
            extracted["raw_llm_preview"] = ""
            logger.info(f"文章 {article_id} 二次重试成功")
            return extracted

    # ── 两次都失败：返回失败信息 ──
    failure_type2 = _classify_failure(result2, content2)
    content2_preview = content2[:300] if content2 else "(空)"
    logger.warning(f"文章 {article_id} 二次重试也失败: {failure_type2}")

    # 使用更具体的失败类型
    final_failure = failure_type2 if failure_type2 != "unknown" else failure_type

    return {
        "article_id": article_id,
        "events": [],
        "article_reject": True,
        "article_reject_reason": f"LLM二次抽取均失败: {final_failure}",
        "llm_extracted": False,
        "failure_type": final_failure,
        "failure_reason": f"首次:{failure_type}, 二次:{failure_type2}",
        "raw_llm_preview": content_preview[:200],
        "attempt_count": attempt_count,
    }


# ===================== 规则兜底抽取 =====================

_KEYWORD_EVENT_MAP = {
    "即时零售": "platform_move", "美团闪购": "platform_move", "京东到家": "platform_move",
    "京东秒送": "platform_move", "淘宝闪购": "platform_move", "饿了么": "platform_move",
    "抖音小时达": "platform_move", "前置仓": "platform_move", "闪电仓": "platform_move",
    "平台规则": "platform_rule", "平台政策": "platform_rule", "入驻": "platform_rule",
    "扣点": "platform_rule", "佣金": "platform_rule",
    "丝芙兰": "competitor_move", "万宁": "competitor_move", "妍丽": "competitor_move",
    "调色师": "competitor_move", "话梅": "competitor_move", "WOW COLOUR": "competitor_move",
    "名创优品": "competitor_move", "KK集团": "competitor_move",
    "品牌": "brand_move", "新品": "brand_move", "联名": "brand_move",
    "美妆": "category_trend", "个护": "category_trend", "护肤": "category_trend",
    "彩妆": "category_trend", "防晒": "category_trend", "面膜": "category_trend",
    "渠道": "channel_shift", "线下": "channel_shift", "线上": "channel_shift",
    "私域": "channel_shift",
    "消费者": "consumer_scene", "场景": "consumer_scene",
    "GMV": "data_signal", "增速": "data_signal", "增长": "data_signal",
    "下滑": "data_signal", "占比": "data_signal",
    "监管": "policy_signal", "法规": "policy_signal", "备案": "policy_signal",
}

_KEYWORD_BV_MAP = {
    "流量": "流量", "转化率": "转化率", "客单价": "客单价", "复购": "复购",
    "价格": "价格", "补贴": "补贴", "毛利": "毛利", "货盘": "货盘",
    "SKU": "SKU", "履约": "履约", "会员": "会员", "私域": "私域",
    "活动": "活动", "投流": "投流", "资源位": "平台资源位",
}


def _should_rule_fallback(article: dict) -> bool:
    """判断文章是否满足规则兜底条件。"""
    title = (article.get("title", "") or "").lower()
    for kw in RULE_FALLBACK_TITLE_KEYWORDS:
        if kw.lower() in title:
            return True
    return False


def rule_fallback_extract(article: dict, date: str, event_index: int) -> tuple:
    """规则兜底抽取：为满足条件的高价值标题生成一条 low confidence 事件。

    Returns:
        (events: list, next_event_index: int)
    """
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    source_name = article.get("source_name", "") or ""
    combined = f"{title} {summary}".lower()

    # 匹配事件类型和业务变量
    matched_types = {}
    for kw, etype in _KEYWORD_EVENT_MAP.items():
        if kw.lower() in combined:
            matched_types.setdefault(etype, []).append(kw)

    matched_bvs = []
    for kw, bv in _KEYWORD_BV_MAP.items():
        if kw.lower() in combined:
            matched_bvs.append(bv)

    # 默认类型
    if not matched_types:
        matched_types = {"unclear": ["标题关键词匹配"]}

    # 取第一个匹配的事件类型
    etype = list(matched_types.keys())[0]
    primary_kw = matched_types[etype][0]

    # 事实陈述：将标题改写为事实
    fact = f"[规则兜底] {source_name}报道：{title}"

    # 时间敏感性
    ts = article.get("time_status", "")
    time_sensitivity = "background"
    if ts == "in_window":
        time_sensitivity = "today"
    elif ts == "near_window":
        time_sensitivity = "recent"

    event = {
        "event_id": generate_event_id(date, event_index),
        "event_title": title[:60] if len(title) > 60 else title,
        "event_type": etype,
        "fact": fact,
        "evidence_text": f"{title}。{summary}"[:200],
        "entities": _extract_entities(article, combined),
        "business_variables": list(set(matched_bvs)),
        "time_sensitivity": time_sensitivity,
        "novelty": "unclear",
        "confidence": "low",
        "extraction_method": "rule_fallback",
        "needs_verification": True,   # 规则兜底事件需验证
        "reject": False,
        "reject_reason": "",
        "source_article_id": article.get("article_id", ""),
        "source_title": title,
        "source_name": source_name,
        "source_url": article.get("url", ""),
        "published_at": article.get("published_at", ""),
    }

    return [event], event_index + 1


def rule_extract_events(article: dict, date: str, event_index: int) -> tuple:
    """轻量规则抽取（LLM 完全不可用时）。

    Returns:
        (events: list, reject: bool, reject_reason: str, next_index: int)
    """
    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    content = (article.get("content", "") or "").lower()
    combined = f"{title} {summary} {content[:1500]}"

    has_watsons = any(kw.lower() in combined for kw in ["屈臣氏", "watsons"])

    if not title.strip() and len(summary) < 20 and len(content) < 20:
        return [], True, "文章内容不足", event_index

    matched_types = {}
    for kw, etype in _KEYWORD_EVENT_MAP.items():
        if kw.lower() in combined:
            matched_types.setdefault(etype, []).append(kw)

    matched_bvs = []
    for kw, bv in _KEYWORD_BV_MAP.items():
        if kw.lower() in combined:
            matched_bvs.append(bv)

    if not matched_types:
        if has_watsons or any(kw in combined for kw in ["即时零售", "小时达", "同城零售"]):
            matched_types = {"background_only": ["屈臣氏相关"]}
        else:
            return [], True, "规则无法匹配明确事件", event_index

    events = []
    for etype, keywords in matched_types.items():
        primary_kw = keywords[0] if keywords else ""
        evidence = _find_evidence(article, primary_kw)
        event = {
            "event_id": generate_event_id(date, event_index),
            "event_title": _build_event_title(article, primary_kw, etype),
            "event_type": etype,
            "fact": _build_fact(article, primary_kw, etype),
            "evidence_text": evidence,
            "entities": _extract_entities(article, combined),
            "business_variables": list(set(matched_bvs)),
            "time_sensitivity": "background",
            "novelty": "unclear",
            "confidence": "low",
            "extraction_method": "rule_extraction",
            "reject": False,
            "reject_reason": "",
        }
        event_index += 1
        events.append(event)

    return events[:2], False, "", event_index


def _find_evidence(article: dict, keyword: str) -> str:
    content = article.get("content", "") or ""
    summary = article.get("summary", "") or ""
    title = article.get("title", "") or ""

    for text in [content, summary, title]:
        sentences = re.split(r'[。！？\n]', text)
        for s in sentences:
            if keyword.lower() in s.lower() and len(s.strip()) > 10:
                return s.strip()[:200]
    if summary:
        return summary[:200]
    return title[:200]


def _build_fact(article: dict, keyword: str, event_type: str) -> str:
    title = article.get("title", "") or ""
    source = article.get("source_name", "") or ""
    return f"[规则抽取] {source}报道：涉及{keyword}的{event_type}类事件 — {title[:60]}"


def _build_event_title(article: dict, keyword: str, event_type: str) -> str:
    title = article.get("title", "") or ""
    if len(title) > 60:
        return title[:57] + "..."
    return title


def _extract_entities(article: dict, combined: str) -> dict:
    entities = {"platforms": [], "companies": [], "competitors": [],
                "brands": [], "channels": [], "categories": []}
    for kw in ["美团闪购", "京东到家", "京东秒送", "淘宝闪购", "饿了么",
               "抖音小时达", "天猫", "京东"]:
        if kw.lower() in combined:
            entities["platforms"].append(kw)
    for kw in ["丝芙兰", "万宁", "妍丽", "调色师", "话梅", "WOW COLOUR",
               "名创优品", "KK集团"]:
        if kw.lower() in combined:
            entities["competitors"].append(kw)
    if "屈臣氏" in combined or "watsons" in combined:
        entities["companies"].append("屈臣氏")
    for kw in ["美妆", "个护", "护肤", "彩妆", "防晒", "面膜", "洗护", "香氛", "口腔护理"]:
        if kw in combined:
            entities["categories"].append(kw)
    for k in entities:
        entities[k] = list(set(entities[k]))
    return entities


# ===================== 主函数 =====================


def extract_events(
    project_root: str,
    date: str,
    cleaned_file: Optional[str] = None,
    output_file: Optional[str] = None,
    use_llm: bool = True,
    max_articles: Optional[int] = None,
) -> dict:
    """事件抽取主函数。"""
    errors: List[str] = []

    # ── 路径 ──
    if not cleaned_file:
        cleaned_file = resolve_path(project_root, f"data/cleaned/{date}/cleaned_articles.json")
    events_dir = resolve_path(project_root, f"data/events/{date}")
    log_dir = resolve_path(project_root, f"data/logs/{date}")

    os.makedirs(events_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    if not output_file:
        output_file = os.path.join(events_dir, "events_raw.json")
    rejected_file = os.path.join(events_dir, "events_rejected_articles.json")
    failed_file = os.path.join(events_dir, "events_failed_articles.json")
    log_file = os.path.join(log_dir, "extract_events.log")

    # ── 日志 ──
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger = logging.getLogger("extract_events")
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info(f"开始事件抽取: date={date}")
    logger.info(f"use_llm={use_llm}, max_articles={max_articles}")
    logger.info(f"输入文件: {cleaned_file}")
    logger.info("=" * 60)

    # ── 加载清洗后的文章（main池）──
    try:
        with open(cleaned_file, "r", encoding="utf-8") as f:
            cleaned_data = json.load(f)
    except Exception as e:
        error_msg = f"无法加载清洗文章: {e}"
        logger.error(error_msg)
        logger.removeHandler(file_handler)
        file_handler.close()
        return {
            "ok": False, "date": date, "input_file": cleaned_file,
            "output_file": output_file, "log_file": log_file,
            "article_count": 0, "event_count": 0,
            "rejected_article_count": 0, "failed_article_count": 0,
            "rule_fallback_count": 0, "errors": [error_msg],
        }

    articles = cleaned_data.get("articles", [])
    article_count = len(articles)

    # ── V2: 同时加载 reference 池文章（仅 in_window / near_window）──
    # reference 池的文章虽然不在时间窗口内或分数较低，但可能包含有价值的行业分析
    # 标记为 reference=True，便于后续区分优先级
    # ⚠️ 重要：只纳入 in_window / near_window 的 reference 文章，
    #          排除 old / unknown_time 以防止旧闻（如去年双11）污染日报
    reference_file = resolve_path(project_root, f"data/cleaned/{date}/reference_articles.json")
    ref_count = 0
    ref_skipped_count = 0
    try:
        if os.path.exists(reference_file):
            with open(reference_file, "r", encoding="utf-8") as f:
                ref_data = json.load(f)
            all_ref_articles = ref_data if isinstance(ref_data, list) else ref_data.get("articles", [])
            # 仅保留时效性合适的 reference 文章：
            #   - in_window / near_window (时间窗口内)
            #   - uncertain_date (CloakBrowser/XCrawl 搜索采集，日期不精确但内容为近期)
            #   - unknown_time 但 campaign_temporality 为 current/upcoming (当年大促内容)
            ref_articles = []
            for a in all_ref_articles:
                ts = a.get("time_status", "")
                ct = (a.get("filter") or {}).get("campaign_temporality", "not_campaign")
                collector = a.get("collector", "") or a.get("source_type", "")
                if ts in ("in_window", "near_window"):
                    a["is_reference"] = True
                    ref_articles.append(a)
                elif ts == "uncertain_date":
                    # CloakBrowser/XCrawl 搜索采集的近期内容，日期提取失败但值得纳入
                    a["is_reference"] = True
                    ref_articles.append(a)
                elif ts in ("unknown_time", "old") and ct in ("current_campaign", "upcoming_campaign"):
                    # 当年大促内容即使 time_status 不明也纳入
                    a["is_reference"] = True
                    ref_articles.append(a)
                else:
                    ref_skipped_count += 1
            ref_count = len(ref_articles)
            articles.extend(ref_articles)
            logger.info(f"加载 reference 文章: {ref_count} 篇 (跳过 {ref_skipped_count} 篇 old/unknown_time)")
    except Exception as e:
        logger.warning(f"加载 reference 文章失败（非阻塞）: {e}")

    # main 池文章标记为非 reference
    for a in articles[:article_count]:
        if "is_reference" not in a:
            a["is_reference"] = False

    if max_articles and max_articles > 0:
        total_before_limit = len(articles)
        # 优先保留 main 池文章，然后按 rule_score 排序 reference 文章
        main_articles = [a for a in articles if not a.get("is_reference", False)]
        ref_articles = [a for a in articles if a.get("is_reference", False)]
        # reference 文章按分数排序，保留高分
        ref_articles.sort(key=lambda a: a.get("filter", {}).get("rule_score", 0), reverse=True)
        remaining = max_articles - len(main_articles)
        if remaining > 0:
            articles = main_articles + ref_articles[:remaining]
        else:
            articles = main_articles[:max_articles]
        logger.info(f"限制处理文章数: {max_articles} (main={len(main_articles)}, ref={min(remaining, len(ref_articles))})")

    logger.info(f"加载文章总计: {len(articles)} 篇 (main={article_count}, reference={ref_count})")

    # ── LLM 客户端 ──
    llm_client = None
    _extract_default_model = None  # 模型路由默认模型

    if use_llm:
        try:
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from skills.utils.llm_client import get_llm_client
            llm_client = get_llm_client()
            if llm_client.available:
                logger.info(f"LLM 客户端就绪: {llm_client.available_keys} Key, "
                            f"模型={llm_client.model}")
            else:
                logger.warning("LLM 客户端无可用 Key，降级为规则抽取")
                llm_client = None
                use_llm = False
        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}，降级为规则抽取")
            llm_client = None
            use_llm = False

    # ── 模型路由 ──
    if use_llm:
        try:
            from skills.utils.model_router import get_model_for_skill
            _extract_default_model, _extract_fallback_models = get_model_for_skill("extract_events")
            logger.info(f"模型路由: extract_events 默认={_extract_default_model}, "
                        f"fallback={_extract_fallback_models}")
        except Exception as e:
            logger.warning(f"模型路由加载失败: {e}，使用默认模型")
            _extract_default_model = None
            _extract_fallback_models = []

    # ── 逐篇抽取（并行 LLM → 合并结果）──
    all_events: List[dict] = []
    rejected_articles: List[dict] = []  # 业务拒绝
    failed_articles: List[dict] = []    # 技术失败
    event_index = 1

    llm_success_count = 0
    llm_failed_count = 0
    retry_success_count = 0
    retry_failed_count = 0
    rule_fallback_count = 0

    by_event_type = Counter()
    by_confidence = Counter()
    by_source = Counter()
    by_failure_type = Counter()
    top_business_variables = Counter()

    # ── Phase 1: 并行 LLM 调用 ──
    if use_llm and llm_client and len(articles) > 0:
        # ── 加载并行配置与 checkpoint ──
        from skills.utils.parallel_runner import parallel_map, load_checkpoint, save_checkpoint, clear_checkpoint, load_parallel_config
        _ee_yaml = load_parallel_config(project_root) if project_root else {}
        _ee_cfg = _ee_yaml.get("extract_events", {}).get("article_parallel", {})
        _parallel_enabled = _ee_cfg.get("enabled", True)
        _max_workers = _ee_cfg.get("max_workers", 4)
        _article_timeout = _ee_cfg.get("article_timeout", 180)
        _retry_on_failure = _ee_cfg.get("retry_on_failure", True)
        _retry_model_name = _ee_cfg.get("retry_model", "LongCat-Flash-Chat")
        _checkpoint_filename = _ee_cfg.get("checkpoint_file", "extract_events_checkpoint.jsonl")
        _checkpoint_path = os.path.join(events_dir, _checkpoint_filename)

        # 模型策略: Lite 默认, Chat 重试
        _model_strategy = _ee_cfg.get("model_strategy", {})
        _default_model = _model_strategy.get("default", _extract_default_model or "LongCat-Flash-Lite")
        _fallback_model = _model_strategy.get("fallback", "LongCat-Flash-Chat")
        _skip_thinking = _model_strategy.get("skip_thinking", True)

        # 清除旧 checkpoint
        clear_checkpoint(_checkpoint_path)

        # ── 加载 checkpoint 恢复 ──
        _completed_checkpoints = load_checkpoint(_checkpoint_path)
        if _completed_checkpoints:
            logger.info(f"从 checkpoint 恢复: {len(_completed_checkpoints)} 篇已完成")

        # ── 筛选未完成文章 ──
        _pending_indices = []
        _checkpoint_results = {}  # {article_id: result}
        for i, article in enumerate(articles):
            _aid = article.get("article_id", f"art_{i+1:04d}")
            if _aid in _completed_checkpoints:
                _checkpoint_results[i] = (article, _completed_checkpoints[_aid])
            else:
                _pending_indices.append(i)

        skip_count = len(articles) - len(_pending_indices)
        if skip_count > 0:
            logger.info(f"Checkpoint 恢复: 跳过 {skip_count} 篇已完成文章, "
                        f"{len(_pending_indices)} 篇待处理")

        def _extract_one_article(article, idx):
            """单篇文章 LLM 抽取 + 失败重试

            使用 Lite 默认模型，失败后用 Chat 重试。
            不使用 Thinking 模型做 JSON 抽取。
            """
            _aid = article.get("article_id", f"art_{idx+1:04d}")
            # 第一次尝试: 默认模型(Lite)
            result = llm_extract_events(article, llm_client, logger, model=_default_model)

            # 检查失败且允许重试
            failure_type = result.get("failure_type") if result else None
            if failure_type and _retry_on_failure:
                logger.info(f"  文章 {_aid} 首次抽取失败({failure_type}), 用 {_fallback_model} 重试...")
                try:
                    retry_result = llm_extract_events(
                        article, llm_client, logger, model=_fallback_model
                    )
                    # 重试成功(无 failure_type)
                    if not retry_result.get("failure_type"):
                        retry_result["retry_used"] = True
                        retry_result["retry_model"] = _fallback_model
                        result = retry_result
                        logger.info(f"  文章 {_aid} 重试成功")
                    else:
                        # 重试也失败，保留重试结果
                        logger.warning(f"  文章 {_aid} 重试仍失败: "
                                       f"{retry_result.get('failure_type')}")
                except Exception as e:
                    logger.warning(f"  文章 {_aid} 重试异常: {e}")

            # 保存 checkpoint
            save_checkpoint(_checkpoint_path, _aid, result)
            return result

        # ── 执行并行/串行抽取 ──
        if _parallel_enabled and len(_pending_indices) > 2:
            logger.info(f"并行 LLM 抽取 {len(_pending_indices)} 篇文章 "
                        f"(max_workers={_max_workers}, model={_default_model})...")
            _pending_articles = [articles[i] for i in _pending_indices]
            _llm_parallel_results, _parallel_stats = parallel_map(
                items=_pending_articles,
                process_fn=_extract_one_article,
                max_workers=_max_workers,
                timeout=_article_timeout,
                desc="extract_events_parallel",
                continue_on_error=True,
            )
            logger.info(f"并行抽取统计: {_parallel_stats}")

            # 组装 llm_results: 恢复的 + 新抽取的
            llm_results = [None] * len(articles)
            # 先填 checkpoint 恢复的
            for idx, (article, result) in _checkpoint_results.items():
                llm_results[idx] = (article, result)
            # 再填并行抽取的
            for _pi, _orig_idx in enumerate(_pending_indices):
                _result = _llm_parallel_results[_pi]
                llm_results[_orig_idx] = (articles[_orig_idx], _result)
        else:
            # 串行模式
            logger.info(f"串行 LLM 抽取 {len(_pending_indices)} 篇文章 "
                        f"(model={_default_model})...")
            llm_results = [None] * len(articles)
            # 先填 checkpoint 恢复的
            for idx, (article, result) in _checkpoint_results.items():
                llm_results[idx] = (article, result)
            # 串行处理未完成的
            for _orig_idx in _pending_indices:
                article = articles[_orig_idx]
                try:
                    result = _extract_one_article(article, _orig_idx)
                    llm_results[_orig_idx] = (article, result)
                except Exception as e:
                    logger.warning(f"  文章 {_orig_idx+1} 抽取异常: {e}")
                    llm_results[_orig_idx] = (article, {"failure_type": "llm_exception",
                                                          "failure_reason": str(e)})

        # ── Phase 2: 串行合并 LLM 结果 ──
        logger.info(f"LLM 抽取完成，开始合并结果...")
        for idx, (article, result) in enumerate(llm_results):
            if result is None:
                result = {"failure_type": "llm_exception", "failure_reason": "timeout or unknown error"}

            source_name = article.get("source_name", "unknown")
            article_id = article.get("article_id", f"art_{idx+1:04d}")
            by_source[source_name] += 1
            title = article.get("title", "") or ""

            # ── 分类处理（与原逻辑一致）──
            failure_type = result.get("failure_type")

            if failure_type:
                llm_failed_count += 1
                by_failure_type[failure_type] += 1

                if _should_rule_fallback(article):
                    logger.info(f"  文章 {article_id} LLM失败但满足规则兜底条件，生成兜底事件")
                    fallback_events, event_index = rule_fallback_extract(
                        article, date, event_index
                    )
                    all_events.extend(fallback_events)
                    rule_fallback_count += 1
                    for ev in fallback_events:
                        by_event_type[ev["event_type"]] += 1
                        by_confidence[ev["confidence"]] += 1
                        for bv in ev.get("business_variables", []):
                            top_business_variables[bv] += 1
                else:
                    logger.warning(f"  文章 {article_id} 技术失败({failure_type})，记入 failed")
                    failed_articles.append({
                        "source_article_id": article_id,
                        "source_title": title,
                        "source_url": article.get("url", ""),
                        "source_name": source_name,
                        "published_at": article.get("published_at", ""),
                        "failure_type": failure_type,
                        "failure_reason": result.get("failure_reason", ""),
                        "retry_required": True,
                        "raw_llm_preview": result.get("raw_llm_preview", "")[:200],
                        "attempt_count": result.get("attempt_count", 0),
                    })
                continue

            # 疑似技术失败
            if result.get("_empty_events_suspect_failure"):
                llm_failed_count += 1
                failure_type = "suspect_empty_events"
                by_failure_type[failure_type] += 1

                if _should_rule_fallback(article):
                    logger.info(f"  文章 {article_id} 疑似失败但满足规则兜底，生成兜底事件")
                    fallback_events, event_index = rule_fallback_extract(
                        article, date, event_index
                    )
                    all_events.extend(fallback_events)
                    rule_fallback_count += 1
                    for ev in fallback_events:
                        by_event_type[ev["event_type"]] += 1
                        by_confidence[ev["confidence"]] += 1
                        for bv in ev.get("business_variables", []):
                            top_business_variables[bv] += 1
                else:
                    logger.warning(f"  文章 {article_id} 疑似失败(空事件+标题含关键词)，记入 failed")
                    failed_articles.append({
                        "source_article_id": article_id,
                        "source_title": title,
                        "source_url": article.get("url", ""),
                        "source_name": source_name,
                        "published_at": article.get("published_at", ""),
                        "failure_type": failure_type,
                        "failure_reason": "LLM返回空事件且未标reject，但标题含疑似事件关键词",
                        "retry_required": True,
                        "raw_llm_preview": result.get("raw_llm_preview", "")[:200],
                        "attempt_count": result.get("attempt_count", 0),
                    })
                continue

            # LLM 成功
            llm_success_count += 1
            if result.get("retry_used") or result.get("attempt_count", 1) > 1:
                retry_success_count += 1

            if result.get("article_reject"):
                reason = result.get("article_reject_reason", "无明确新增事实")
                rejected_articles.append({
                    **article,
                    "reject_reason": f"LLM拒绝: {reason}",
                })
                logger.info(f"  文章 {article_id} 被LLM拒绝(业务): {reason}")
                continue

            # LLM 成功事件 — 分配 event_id
            events = result.get("events", [])
            for ev in events:
                if ev.get("reject"):
                    logger.info(f"  事件被拒绝: {ev.get('reject_reason', '未知原因')}")
                    continue

                ev["event_id"] = generate_event_id(date, event_index)
                event_index += 1

                # 确保所有必要字段
                ev.setdefault("event_title", "")
                ev.setdefault("event_type", "unclear")
                ev.setdefault("fact", "")
                ev.setdefault("evidence_text", "")
                ev.setdefault("business_variables", [])
                ev.setdefault("time_sensitivity", "background")
                ev.setdefault("novelty", "unclear")
                ev.setdefault("confidence", "low")
                ev.setdefault("extraction_method", "llm")
                ev.setdefault("entities", {
                    "platforms": [], "companies": [], "competitors": [],
                    "brands": [], "channels": [], "categories": [],
                })
                ev.setdefault("source_article_id", article_id)
                ev.setdefault("source_title", title)
                ev.setdefault("source_name", source_name)
                ev.setdefault("source_url", article.get("url", ""))
                ev.setdefault("published_at", article.get("published_at", ""))

                if not ev.get("fact", "").strip():
                    logger.warning(f"  事件 fact 为空，跳过")
                    continue
                if not ev.get("evidence_text", "").strip():
                    ev["evidence_text"] = ev.get("fact", "")[:200]
                if not ev.get("source_url"):
                    ev["source_url"] = article.get("url", "")

                all_events.append(ev)
                by_event_type[ev.get("event_type", "unclear")] += 1
                by_confidence[ev.get("confidence", "low")] += 1
                for bv in ev.get("business_variables", []):
                    top_business_variables[bv] += 1

            if not events:
                logger.info(f"  文章 {article_id} 未抽取到事件")

        else:
            # ── 无 LLM 结果（降级模式或并行失败）── 串行处理
            for idx_r, (article_r, result_r) in enumerate(llm_results):
                if result_r is not None:
                    continue  # 已在上面处理
                source_name_r = article_r.get("source_name", "unknown")
                article_id_r = article_r.get("article_id", f"art_{idx_r+1:04d}")
                by_source[source_name_r] += 1
                events, reject, reason, event_index = rule_extract_events(
                    article_r, date, event_index
                )
                if reject:
                    rejected_articles.append({
                        **article_r,
                        "reject_reason": f"规则拒绝: {reason}",
                    })
                    continue
                for ev in events:
                    all_events.append(ev)
                    by_event_type[ev["event_type"]] += 1
                    by_confidence[ev["confidence"]] += 1
                    for bv in ev.get("business_variables", []):
                        top_business_variables[bv] += 1

        logger.info(f"  合并完成: {len(all_events)} 个事件, "
                    f"{llm_success_count} LLM成功, {llm_failed_count} LLM失败")

    else:
        # ── 非LLM模式: 纯规则抽取（降级模式）──
        for i, article in enumerate(articles):
            source_name = article.get("source_name", "unknown")
            article_id = article.get("article_id", f"art_{i+1:04d}")
            by_source[source_name] += 1

    # ── 事件去重 ──
    pre_dedup_count = len(all_events)
    all_events, dedup_log = deduplicate_events(all_events, similarity_threshold=0.50)
    dedup_count = pre_dedup_count - len(all_events)
    if dedup_count > 0:
        logger.info(f"  事件去重: {pre_dedup_count} → {len(all_events)} (合并 {dedup_count} 个重复事件)")
        for entry in dedup_log[:10]:
            logger.info(f"    合并: '{entry['absorbed']}' → '{entry['kept']}' (sim={entry['similarity']})")
    else:
        logger.info(f"  事件去重: 无重复事件")

    # ── 保存输出 ──
    events_data = {
        "metadata": {
            "version": "3.0",
            "date": date,
            "source_file": cleaned_file,
            "created_at": datetime.now().isoformat(),
            "total_articles": len(articles),
            "total_events": len(all_events),
            "rejected_articles": len(rejected_articles),
            "failed_articles": len(failed_articles),
            "llm_success_count": llm_success_count,
            "llm_failed_count": llm_failed_count,
            "retry_success_count": retry_success_count,
            "retry_failed_count": retry_failed_count,
            "rule_fallback_count": rule_fallback_count,
            "by_event_type": dict(by_event_type),
            "by_confidence": dict(by_confidence),
            "by_source": dict(by_source),
            "by_failure_type": dict(by_failure_type),
            "top_business_variables": dict(top_business_variables.most_common(15)),
        },
        "events": all_events,
    }

    rejected_data = {
        "metadata": {
            "version": "3.0",
            "date": date,
            "total_rejected": len(rejected_articles),
            "reject_reason_stats": dict(Counter(
                a.get("reject_reason", "unknown") for a in rejected_articles
            )),
        },
        "articles": rejected_articles,
    }

    failed_data = {
        "metadata": {
            "version": "3.0",
            "date": date,
            "total_failed": len(failed_articles),
            "failure_type_stats": dict(by_failure_type),
        },
        "articles": failed_articles,
    }

    for filepath, data in [
        (output_file, events_data),
        (rejected_file, rejected_data),
        (failed_file, failed_data),
    ]:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 日志汇总 ──
    logger.info("=" * 60)
    logger.info("事件抽取完成汇总:")
    logger.info(f"  article_count: {len(articles)}")
    logger.info(f"  event_count: {len(all_events)}")
    logger.info(f"  rejected_article_count: {len(rejected_articles)}")
    logger.info(f"  failed_article_count: {len(failed_articles)}")
    logger.info(f"  llm_success_count: {llm_success_count}")
    logger.info(f"  llm_failed_count: {llm_failed_count}")
    logger.info(f"  retry_success_count: {retry_success_count}")
    logger.info(f"  retry_failed_count: {retry_failed_count}")
    logger.info(f"  rule_fallback_count: {rule_fallback_count}")
    logger.info(f"  by_event_type: {dict(by_event_type)}")
    logger.info(f"  by_confidence: {dict(by_confidence)}")
    logger.info(f"  by_source: {dict(by_source)}")
    logger.info(f"  by_failure_type: {dict(by_failure_type)}")
    logger.info(f"  top_business_variables: {dict(top_business_variables.most_common(10))}")
    if llm_client:
        status = llm_client.get_status()
        logger.info(f"  LLM status: available_keys={status['available_keys']}, "
                     f"calls={status['total_calls']}, failures={status['total_failures']}")
    logger.info("=" * 60)

    logger.removeHandler(file_handler)
    file_handler.close()

    return {
        "ok": True,
        "date": date,
        "input_file": cleaned_file,
        "output_file": output_file,
        "log_file": log_file,
        "article_count": len(articles),
        "event_count": len(all_events),
        "rejected_article_count": len(rejected_articles),
        "failed_article_count": len(failed_articles),
        "rule_fallback_count": rule_fallback_count,
        "llm_success_count": llm_success_count,
        "llm_failed_count": llm_failed_count,
        "retry_success_count": retry_success_count,
        "retry_failed_count": retry_failed_count,
        "by_failure_type": dict(by_failure_type),
        "errors": errors,
    }


# ===================== LLM 连通性测试 =====================


def run_test_llm():
    project_root = "/app/working/projects/watsons-retail-intel"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from skills.utils.llm_client import check_llm_config, test_llm_connection

    print("=" * 60)
    print("Event Extractor — LongCat LLM 连通性测试")
    print("=" * 60)

    config = check_llm_config()
    print()
    print("📋 LLM 配置:")
    print(f"  keys_found:    {config['keys_found']}")
    print(f"  key_count:     {config['key_count']}")
    print(f"  base_url:      {config['base_url']}")
    print(f"  model:         {config['model']}")
    print(f"  endpoint:      {config['endpoint']}")

    if not config["keys_found"]:
        print("\n❌ 未找到任何 API Key。")
        return

    print("\n🔄 正在发送测试请求...")
    result = test_llm_connection()
    print()
    print("📊 测试结果:")
    print(f"  llm_config_ok:   {result['llm_config_ok']}")
    print(f"  api_reachable:   {result['api_reachable']}")
    print(f"  model:           {result['model']}")
    print(f"  parsed_json_ok:  {result['parsed_json_ok']}")
    print(f"  error_message:   {result['error_message'] or '(无)'}")
    print()
    if result["api_reachable"] and result["parsed_json_ok"]:
        print("✅ LLM 连通性测试通过！")
    elif result["api_reachable"]:
        print("⚠️  API 可达但 JSON 解析异常。")
    else:
        print("❌ API 不可达或配置不完整。")
    print("=" * 60)


# ===================== CLI =====================


def main():
    parser = argparse.ArgumentParser(
        description="事件抽取 — 从清洗文章中提取结构化事件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", required=False, help="项目根目录")
    parser.add_argument("--date", help="日期 YYYY-MM-DD")
    parser.add_argument("--cleaned-file", default=None, help="覆盖输入文件路径")
    parser.add_argument("--output-file", default=None, help="覆盖输出文件路径")
    parser.add_argument("--use-llm", default="true", help="是否使用LLM (true/false)")
    parser.add_argument("--max-articles", type=int, default=None, help="最大处理文章数")
    parser.add_argument("--verbose", action="store_true", help="启用详细日志")
    parser.add_argument("--test-llm", action="store_true", help="运行 LLM 连通性测试")

    args = parser.parse_args()

    if args.test_llm:
        run_test_llm()
        sys.exit(0)

    if not args.project_root:
        parser.error("正常模式需要 --project-root 参数")
    if not args.date:
        parser.error("正常模式需要 --date 参数")

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = extract_events(
        project_root=args.project_root,
        date=args.date,
        cleaned_file=args.cleaned_file,
        output_file=args.output_file,
        use_llm=use_llm,
        max_articles=args.max_articles,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()