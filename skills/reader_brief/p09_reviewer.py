#!/usr/bin/env python3
"""p09_reviewer.py — 邮件成稿的主张级事实一致性复核（P09）+ 程序门。

边界：
- 只复查已入选的少量主张（signals/competitor_cards/priority_actions/watch_items）；
- 先跑 schema.py 的确定性检查，模型不做字段/数字/链接的重复审计；
- 模型 decision 不是人工签字：approve 只表示"可以进入渲染"，发送仍受程序门控制；
- critical 未解决 → block，不发 SMTP。
"""

import json
import logging
from typing import Dict, List, Optional, Tuple

from skills.utils.llm_client import get_llm_client
from skills.utils.prompt_registry import render
from skills.reader_brief import schema
from skills.reader_brief.facts_pack import fact_index

logger = logging.getLogger(__name__)

CRITICAL_CODES = {"unknown_fact_id", "unknown_claim_id", "unbound_source_url",
                  "source_not_of_fact", "number_not_in_fact", "status_overclaim",
                  "expired_deadline", "missing_source_url", "event_id_leak",
                  "priority_leak", "internal_leak", "html_leak",
                  "audit_sha_mismatch", "fake_human_approval", "no_fact_ids"}


def _evidence(brief: Dict, pack: Dict) -> List[Dict]:
    idx = fact_index(pack)
    out: Dict[str, Dict] = {}

    def add(where: str, ids: List[str]):
        for fid in ids or []:
            f = idx.get(fid)
            if not f:
                continue
            item = out.setdefault(fid, {"fact_id": fid,
                                        "claim_id": f["claim_id"],
                                        "locations": [],
                                        "evidence": f["text"],
                                        "source_url": f["source"]["url"],
                                        "source_kind": f["source"]["kind"],
                                        "date": f["date"],
                                        "status": f["status"],
                                        "numbers": f["numbers"]})
            item["locations"].append(where)

    for i, s in enumerate(brief.get("signals") or []):
        add(f"signals[{i}]", s.get("fact_ids"))
    for i, c in enumerate(brief.get("competitor_cards") or []):
        add(f"competitor_cards[{i}]", c.get("fact_ids"))
    for i, a in enumerate(brief.get("priority_actions") or []):
        add(f"priority_actions[{i}]", a.get("linked_fact_ids"))
    for i, w in enumerate(brief.get("watch_items") or []):
        add(f"watch_items[{i}]", w.get("fact_ids"))
    return list(out.values())


def review(brief: Dict, pack: Dict, send_at: str,
           deterministic: Optional[Dict] = None) -> Tuple[Dict, Dict]:
    """返回 (gate, meta)。gate: {status: pass|revise_applied|block, ...}"""
    det = deterministic or schema.validate_brief(brief, pack, send_at)
    det_errors = det.get("errors") or []
    pr = render("P09",
                reader_brief_json=brief,
                selected_claim_evidence_json=_evidence(brief, pack),
                deterministic_validation_findings_json={
                    "errors": det["errors"], "warnings": det["warnings"]})
    client = get_llm_client()
    client.timeout = max(int(getattr(client, "timeout", 180)), 300)
    res = client.chat(messages=[{"role": "user", "content": pr.user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=pr.temperature, max_tokens=pr.max_tokens)
    review_json = res.get("parsed") if res.get("ok") else None
    meta = {"prompt": "P09", "prompt_version": pr.version,
            "model": res.get("model"), "rounds": 1,
            "deterministic": det}
    if not isinstance(review_json, dict):
        # 模型不可用 → fail-closed：任何程序门错误 blocked；否则 hold_for_review。
        # 绝不把"没审"当成"已审"。
        meta["degraded"] = True
        meta["error"] = (res.get("error") or "")[:200]
        if det_errors:
            return {"state": "blocked", "status": "blocked",
                    "decision": "deterministic_errors",
                    "review": None, "degraded": True, "issues": [],
                    "det_errors": det_errors,
                    "reason": (res.get("error") or "")[:160]}, meta
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "p09_unavailable", "review": None,
                "degraded": True, "issues": [],
                "reason": (res.get("error") or "")[:160]}, meta

    decision = str(review_json.get("decision") or "").lower()
    issues = review_json.get("issues") or []
    meta["review"] = review_json
    meta["raw_reviews"] = [review_json]

    critical = [i for i in issues if str(i.get("severity")).lower() == "critical"]
    hard_errors = [e for e in det["errors"] if e["code"] in CRITICAL_CODES]
    if decision == "block" or critical or hard_errors or det_errors:
        return {"state": "blocked", "status": "blocked",
                "decision": decision or "block",
                "review": review_json, "issues": critical or issues,
                "det_errors": hard_errors or det_errors}, meta

    if decision == "revise" or review_json.get("suggested_replacements"):
        fixed, applied = schema.apply_replacements(
            brief, review_json.get("suggested_replacements") or [])
        after = schema.validate_brief(fixed, pack, send_at)
        meta["applied_replacements"] = applied
        meta["post_revise"] = after
        if not after["pass"]:
            return {"state": "blocked", "status": "blocked",
                    "decision": "revise_rejected",
                    "review": review_json, "issues": issues,
                    "det_errors": after["errors"]}, meta
        unresolved = [i for i in issues
                      if str(i.get("severity")).lower() in ("critical", "major")
                      and str(i.get("location") or "") not in applied]
        meta["unresolved_major"] = unresolved
        if unresolved:
            return {"state": "blocked", "status": "blocked",
                    "decision": "revise_incomplete",
                    "review": review_json, "issues": unresolved,
                    "applied": applied}, meta
        if applied and _still_major(issues):
            gate2, meta2 = _second_round(fixed, pack, send_at, issues, client, pr)
            meta["rounds"] = 2
            meta["second_round"] = meta2
            if gate2.get("status") != "approved":
                return gate2, meta
        return {"state": "approved", "status": "approved",
                "decision": "revise", "review": review_json,
                "issues": issues, "applied": applied, "brief": fixed}, meta

    return {"state": "approved", "status": "approved",
            "decision": "approve", "review": review_json,
            "issues": issues}, meta


def _still_major(issues) -> bool:
    return any(str(i.get("severity")).lower() in ("critical", "major")
               for i in issues)


def _second_round(brief, pack, send_at, issues, client, pr) -> Tuple[Dict, Dict]:
    user = (pr.user + "\n\n上一轮修改后仍存在以下问题，请重新给出最小更正：\n" +
            json.dumps(issues, ensure_ascii=False))
    res = client.chat(messages=[{"role": "user", "content": user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=0.0, max_tokens=pr.max_tokens)
    rj = res.get("parsed") if res.get("ok") else None
    if not isinstance(rj, dict):
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "second_round_failed"}, {}
    crit = [i for i in (rj.get("issues") or [])
            if str(i.get("severity")).lower() == "critical"]
    if str(rj.get("decision")).lower() == "block" or crit:
        return {"state": "blocked", "status": "blocked",
                "decision": "block", "review": rj}, {}
    return {"state": "approved", "status": "approved",
            "decision": str(rj.get("decision")), "review": rj}, {}


STATE_ZH = {"approved": "已通过（本批次可发送）",
            "hold_for_review": "暂缓（P09 未完成，需人工复核后放行）",
            "blocked": "已阻断（存在未解决事实问题）"}


def summarize_gate(gate: Dict) -> str:
    state = gate.get("state") or gate.get("status")
    if state == "blocked":
        n = len(gate.get("issues") or gate.get("det_errors") or [])
        return f"已阻断：{n} 个未解决问题（禁止发送）"
    if state == "hold_for_review":
        return "暂缓：P09 模型未完成主张级复核（禁止自动发送，需人工放行）"
    if gate.get("degraded"):
        return "暂缓：P09 降级执行（禁止自动发送）"
    if gate.get("decision") == "revise":
        return f"已通过：P09 定点更正 {len(gate.get('applied') or [])} 处后复核通过"
    return "已通过：P09 approve（程序确定性检查 + 主张级复核）"
