#!/usr/bin/env python3
"""prompt_bindings.py — 旧模块 <-> 独立提示词库(P01/P02/P04/P06/P07/P10)的兼容层。

原则（docs/16 §3 重要契约）：
- 语义规则以 docs/prompts/*.md 为准；
- 但旧模块的解析器依赖具体字段名，**不得改名或改 enum**，只允许新增字段；
- 因此这里把 registry 的 System 文本与旧接口字段白名单拼在一起发给模型。
"""

from typing import Dict, Optional, Tuple

from skills.utils.prompt_registry import get_prompt, render


def _compat_block(fields: Dict[str, str]) -> str:
    lines = ["", "【程序接口兼容（不得改名，可新增字段）】"]
    for k, v in fields.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


P01_FIELDS = {
    "llm_relevance": "high|medium|low|none",
    "business_relevance_type": "direct|indirect|background|irrelevant",
    "related_channels": "数组",
    "related_categories": "数组",
    "business_variables": "数组",
    "reason": "一句话理由",
    "recommended_pool": "main|reference|reject",
    "confidence": "high|medium|low（仅表示模型自评，不是人工核验）",
}

P02_FIELDS = {
    "events": "数组；每条含 event_title/fact/evidence_text/summary",
    "events[].entities": "companies/brands/platforms/regions 数组",
    "events[].business_variables": "数组",
    "events[].occurred_at": "事件发生日 YYYY-MM-DD，未知留空",
    "events[].announced_at": "宣布日 YYYY-MM-DD，未知留空",
    "events[].effective_at": "生效日 YYYY-MM-DD，未知留空",
    "events[].time_evidence": "原文中支持上述时间的句子，不得编造",
    "events[].confidence": "high|medium|low（模型自评）",
    "events[].source_articles": "原文标题或URL数组",
    "events[].reject": "布尔；无法核实时为 true",
    "events[].reject_reason": "字符串",
}

P04_FIELDS = {
    "business_analysis": "对象",
    "impact_type": "positive|negative|neutral|mixed|uncertain",
    "affected_channels": "数组",
    "affected_business_variables": "数组",
    "watsons_impact": "一句话经营影响，必须基于事件事实",
    "recommended_action": "有条件的具体动作，不得虚构内部库存/授权/预算",
    "action_level": "watch|test|act",
    "owner_hint": "ecommerce|merchandising|marketing|supply_chain|store_ops|management",
    "tracking_metrics": "数组",
    "follow_up_questions": "数组",
    "confidence": "high|medium|low",
    "downgrade_reasons": "数组",
}


def p01_prompt(article: Dict) -> str:
    """筛选模块：返回单段 prompt（旧接口是单条 user 文本）。"""
    pr = render("P01", article_json={
        "title": article.get("title", ""),
        "source_name": article.get("source_name", ""),
        "summary": article.get("summary", ""),
        "content": (article.get("content") or "")[:1500],
        "matched_keywords": article.get("matched_keywords") or [],
        "published_at": article.get("published_at", ""),
    })
    return pr.system + "\n\n" + pr.user + _compat_block(P01_FIELDS)


def p02_system() -> str:
    return get_prompt("P02").system + _compat_block(P02_FIELDS)


def p02_user(article: Dict) -> str:
    pr = render("P02", article_json=article)
    return pr.user


def p04_system() -> str:
    return get_prompt("P04").system + _compat_block(P04_FIELDS)


def p06_system() -> str:
    return get_prompt("P06").system


def p07_system() -> str:
    return get_prompt("P07").system


def p10_system() -> str:
    return get_prompt("P10").system


def prompt_versions() -> Dict[str, str]:
    out = {}
    for pid in ("P01", "P02", "P04", "P06", "P07", "P08", "P09", "P10",
                "P05", "P14", "P15"):
        try:
            out[pid] = get_prompt(pid).version
        except Exception as exc:  # pragma: no cover
            out[pid] = f"error:{exc}"
    return out
