#!/usr/bin/env python3
"""editor.py — P05 选题策划 + P08 独立经营邮件编辑（reader_brief.json）。

P05：每个周期一次，输入已核验事实包，输出选题优先级（不写长报告）。
P08：把 P05 计划 + 事实包写成独立手机简报 JSON，不是长报告的机械摘句。

两者都只通过 prompt_registry 取提示词；输出经 schema.py 确定性验收。
"""

import json
import logging
from typing import Dict, Optional, Tuple

from skills.utils.llm_client import get_llm_client
from skills.utils.prompt_registry import render
from skills.reader_brief import schema
from skills.reader_brief.facts_pack import fact_index, reader_fact_pack_json

logger = logging.getLogger(__name__)


def _clusters(pack: Dict) -> list:
    groups: Dict[str, Dict] = {}
    for f in pack.get("facts", []):
        key = (f.get("platforms") or ["行业"])[0] + "|" + f["text"][:18]
        g = groups.setdefault(key, {"cluster_id": f"C{len(groups) + 1:03d}",
                                    "theme": f["text"][:60], "facts": [],
                                    "claims": [], "source": f["source"]["url"],
                                    "status": f["status"],
                                    "fact_ids": [], "claim_ids": [],
                                    "canonical_event_ids": []})
        g["facts"].append(f["text"])
        g["claims"].append(f["claim_id"])
        g["fact_ids"].append(f["fact_id"])
        g["claim_ids"].append(f["claim_id"])
        g["canonical_event_ids"].extend(f.get("event_ids") or [])
    return list(groups.values())


def build_topic_plan(pack: Dict, send_at: str, previous=None,
                     published_fingerprints=None) -> Dict:
    payload = {
        "period_type": pack.get("period_type", "weekly"),
        "period_start": (pack.get("period") or {}).get("start", ""),
        "period_end": (pack.get("period") or {}).get("end", ""),
        "news_clusters": _clusters(pack),
        # The current P14/P15 result is not a previous-period comparison.
        "current_cross_period_synthesis": pack.get("synthesis") or {},
        "previous_comparison": previous or "本期无同口径上一周期数据",
        "published_fingerprints": published_fingerprints or [],
    }
    pr = render("P05", periodic_fact_pack_json=payload)
    client = get_llm_client()
    res = client.chat(messages=[{"role": "user", "content": pr.user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=pr.temperature, max_tokens=pr.max_tokens)
    plan = res.get("parsed") if res.get("ok") else None
    if not isinstance(plan, dict):
        logger.warning("P05 失败(%s)，回退规则排序", (res.get("error") or "")[:120])
        return _rule_topic_plan(pack, fallback_reason="llm_unavailable")
    plan["_meta"] = {"prompt": "P05", "prompt_version": pr.version,
                     "model": res.get("model"), "fallback": False}
    plan = _validate_topic_plan(plan, pack)
    if not plan["published_fact_ids"]:
        logger.warning("P05 无任何可发布选题（全部缺合法 fact_id）")
    return plan


def _rule_topic_plan(pack: Dict, fallback_reason: str) -> Dict:
    topics = []
    for i, f in enumerate(pack.get("facts", [])[:4]):
        topics.append({"topic_id": f"T{i + 1}", "headline": f["text"][:24],
                       "source_fact_ids": [f["fact_id"]],
                       "new_information": f["text"][:80],
                       "what_it_changes": "规则排序：按事实新鲜度与来源权重",
                       "affected_channels": f.get("platforms") or [],
                       "business_variable": "", "impact_hypothesis": "",
                       "reader_priority": "must_read" if i == 0 else "useful",
                       "should_publish": True, "reason": "规则回退排序"})
    plan = {"period_type": pack.get("period_type", "weekly"),
            "period_start": (pack.get("period") or {}).get("start", ""),
            "period_end": (pack.get("period") or {}).get("end", ""),
            "single_biggest_change": topics[0]["headline"] if topics else "",
            "topics": topics, "competitor_fact_ids": [], "not_selected": [],
            "genuine_watch_triggers": [], "missing_coverage": [],
            "_meta": {"prompt": "P05", "fallback": True,
                      "reason": fallback_reason}}
    return _validate_topic_plan(plan, pack)


def _validate_topic_plan(plan: Dict, pack: Dict) -> Dict:
    ids = fact_index(pack)
    rejected = plan.get("rejected_topics") or []
    for t in plan.get("topics") or []:
        bad = [x for x in (t.get("source_fact_ids") or []) if x not in ids]
        if bad:
            t["source_fact_ids"] = [x for x in (t.get("source_fact_ids") or [])
                                    if x in ids]
            t["invalid_fact_ids"] = bad
            logger.warning("P05 引用了不存在的事实 %s", bad)
        if not t.get("source_fact_ids"):
            t["should_publish"] = False
            t["rejected_reason"] = "no_valid_fact_ids"
            rejected.append({"topic_id": t.get("topic_id"),
                             "headline": t.get("headline", ""),
                             "reason": "no_valid_fact_ids"})
    must = [t for t in plan.get("topics") or []
            if t.get("reader_priority") == "must_read"]
    if len(must) > 3:
        for t in must[3:]:
            t["reader_priority"] = "useful"
    plan["rejected_topics"] = rejected
    published = sorted({fid for t in plan.get("topics") or []
                        if t.get("should_publish")
                        for fid in (t.get("source_fact_ids") or [])})
    plan["published_fact_ids"] = published
    return plan


def write_brief(pack: Dict, send_at: str, topic_plan: Optional[Dict] = None,
                verified_internal: Optional[Dict] = None,
                previous_fingerprints: Optional[list] = None) -> Tuple[Dict, Dict]:
    """调用 P08，返回 (brief, meta)。schema 不通过时最多一次定点修复。"""
    pr = render("P08",
                period_type=pack.get("period_type", "weekly"),
                period_start=(pack.get("period") or {}).get("start", ""),
                period_end=(pack.get("period") or {}).get("end", ""),
                send_at=send_at,
                reader_fact_pack_json=(
                    {"facts": reader_fact_pack_json(pack),
                     "current_cross_period_synthesis": pack["synthesis"],
                     "synthesis_status": (pack.get("synthesis_meta") or {}).get("status")}
                    if pack.get("synthesis") else reader_fact_pack_json(pack)
                ),
                topic_plan_json=topic_plan or {"topics": []},
                verified_internal_capabilities_json=verified_internal or {},
                previous_digest_fingerprints_json=previous_fingerprints or [])
    client = get_llm_client()
    res = client.chat(messages=[{"role": "user", "content": pr.user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=pr.temperature, max_tokens=pr.max_tokens)
    brief = res.get("parsed") if res.get("ok") else None
    if not isinstance(brief, dict):
        raise RuntimeError("P08 未返回 JSON: " + (res.get("error") or "")[:200])
    brief.setdefault("audit", {})["source_pack_sha256"] = pack["pack_sha256"]

    allowed = None
    if topic_plan and topic_plan.get("topics"):
        allowed = set(topic_plan.get("published_fact_ids") or [])
    verdict = schema.validate_brief(brief, pack, send_at,
                                    allowed_fact_ids=allowed)
    meta = {"prompt": "P08", "prompt_version": pr.version,
            "model": res.get("model"), "first_pass": verdict,
            "repair_rounds": 0}
    if not verdict["pass"]:
        brief = _repair(pr, pack, brief, verdict, client, meta, send_at,
                        allowed)
        verdict = meta["final_verdict"]
    meta["final_verdict"] = verdict
    if not verdict["pass"]:
        raise ValueError("reader_brief 未通过确定性验收: " +
                         json.dumps(verdict["errors"][:4], ensure_ascii=False))
    return brief, meta


def _repair(pr, pack, brief, verdict, client, meta, send_at,
            allowed=None) -> Dict:
    meta["repair_rounds"] = 1
    errs = json.dumps(verdict["errors"][:12], ensure_ascii=False, indent=1)
    user = (pr.user + "\n\n上一版输出被程序验收拒绝，错误如下（只修正这些，"
            "不得新增事实ID/链接/数字）：\n" + errs +
            "\n上一版输出：\n" + json.dumps(brief, ensure_ascii=False))
    res = client.chat(messages=[{"role": "user", "content": user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=0.1, max_tokens=pr.max_tokens)
    fixed = res.get("parsed") if res.get("ok") else None
    if not isinstance(fixed, dict):
        meta["final_verdict"] = verdict
        return brief
    fixed.setdefault("audit", {})["source_pack_sha256"] = pack["pack_sha256"]
    meta["final_verdict"] = schema.validate_brief(fixed, pack, send_at,
                                                  allowed_fact_ids=allowed)
    return fixed
