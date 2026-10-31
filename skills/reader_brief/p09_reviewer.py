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
from skills.reader_brief.issue_policy import partition_issues

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
    """Bounded P09: only material, source-anchored errors block delivery.

    No more than two P09 calls per brief. Unverifiable major issues are HOLD,
    not blindly accepted or retried until the model happens to approve.
    """
    det = deterministic or schema.validate_brief(brief, pack, send_at)
    det_errors = det.get("errors") or []
    if det_errors:
        return {"state": "blocked", "status": "blocked",
                "decision": "deterministic_errors", "issues": [],
                "det_errors": det_errors}, {
                    "prompt": "P09", "rounds": 0, "deterministic": det}
    pr = render(
        "P09",
        reader_brief_json=brief,
        selected_claim_evidence_json=_evidence(brief, pack),
        deterministic_validation_findings_json={
            "errors": det.get("errors") or [],
            "warnings": det.get("warnings") or [],
        })
    client = get_llm_client()
    client.timeout = max(int(getattr(client, "timeout", 180)), 300)
    res = client.chat(messages=[{"role": "user", "content": pr.user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=pr.temperature, max_tokens=pr.max_tokens)
    rj = res.get("parsed") if res.get("ok") else None
    meta = {"prompt": "P09", "prompt_version": pr.version,
            "model": res.get("model"), "rounds": 1,
            "deterministic": det}
    if not isinstance(rj, dict):
        meta.update({"degraded": True,
                     "error": (res.get("error") or "")[:200]})
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "p09_unavailable", "review": None, "issues": [],
                "degraded": True}, meta

    decision = str(rj.get("decision") or "").lower()
    issues = rj.get("issues")
    if decision not in ("approve", "revise", "block") or not isinstance(issues, list):
        meta["degraded"] = True
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "invalid_p09_response", "review": rj}, meta

    split = partition_issues(issues, brief, pack)
    meta.update({"review": rj, "raw_reviews": [rj],
                 "issue_policy": {"material": len(split["material"]),
                                  "advisory": len(split["advisory"]),
                                  "ungrounded_material": len(split["ungrounded_material"])}})
    if split["ungrounded_material"]:
        # A major claim without exact brief+source grounding may be a real
        # factual error OR an overzealous reviewer. Without proof, no auto-send.
        meta["unverifiable_findings"] = split["ungrounded_material"]
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "unverifiable_material_findings",
                "review": rj, "issues": issues}, meta

    if not split["material"]:
        # Wording, nonmaterial precision and stylistic requests are advisory
        # even when the model mistakenly chooses revise/block.
        if decision == "block" and not issues:
            return {"state": "hold_for_review", "status": "hold_for_review",
                    "decision": "unexplained_block", "review": rj}, meta
        return {"state": "approved", "status": "approved",
                "decision": "approve", "review": rj, "issues": issues,
                "advisory_only": bool(issues)}, meta

    # There is at least one material, traceable factual claim defect.
    if decision == "approve":
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "review_conflicts_with_material_findings",
                "review": rj, "issues": issues}, meta

    replacements = rj.get("suggested_replacements") or []
    if not isinstance(replacements, list) or not replacements:
        return {"state": "blocked", "status": "blocked",
                "decision": "material_errors_no_replacement",
                "review": rj, "issues": issues}, meta

    fixed, applied = schema.apply_replacements(brief, replacements)
    meta["applied_replacements"] = applied
    after = schema.validate_brief(fixed, pack, send_at)
    meta["post_revise"] = after
    if not after["pass"]:
        return {"state": "blocked", "status": "blocked",
                "decision": "revised_deterministic_errors",
                "review": rj, "det_errors": after["errors"]}, meta
    unresolved = [
        obj["issue"] for obj in split["material"]
        if str(obj["issue"].get("location") or "") not in applied
    ]
    if unresolved:
        return {"state": "blocked", "status": "blocked",
                "decision": "material_issue_without_applied_fix",
                "review": rj, "issues": unresolved}, meta

    # Exactly one revision pass, then a second P09 call. No third review.
    gate2, detail2 = _second_round(fixed, pack, send_at, split["material"],
                                    client, pr)
    meta.update({"rounds": 2, "second_round": detail2})
    if detail2.get("review"):
        meta.setdefault("raw_reviews", []).append(detail2["review"])
    if gate2["state"] != "approved":
        return gate2, meta
    return {"state": "approved", "status": "approved",
            "decision": "revise", "review": rj,
            "issues": issues, "applied": applied, "brief": fixed}, meta


def _second_round(brief, pack, send_at, prior_material, client, pr) -> Tuple[Dict, Dict]:
    """Review the final edited JSON, not the old draft; stop after this call."""
    det = schema.validate_brief(brief, pack, send_at)
    if not det["pass"]:
        return {"state": "blocked", "status": "blocked",
                "decision": "second_round_deterministic_errors",
                "det_errors": det["errors"]}, {"deterministic": det}
    user = (
        "这是第2次，也是最后一次事实审稿。不得引入第三轮修改。"
        "只核对前轮已指出的实质问题是否消除，不重复提出非实质措辞意见。"
        "若仍有主体、状态、日期、数字、来源或跨主体因果的实质错误，"
        "请以可定位的brief_quote/source_excerpt/claim_id/evidence_url指出；"
        "其他问题标minor，审稿结论approve。\n"
        "已修订完整JSON:\n" + json.dumps(brief, ensure_ascii=False) +
        "\n原始证据:\n" + json.dumps(_evidence(brief, pack), ensure_ascii=False) +
        "\n需复核的前轮实质问题:\n" +
        json.dumps(prior_material, ensure_ascii=False) +
        "\n确定性门:\n" + json.dumps(det, ensure_ascii=False)
    )
    res = client.chat(messages=[{"role": "user", "content": user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=0.0, max_tokens=pr.max_tokens)
    rj = res.get("parsed") if res.get("ok") else None
    if not isinstance(rj, dict) or not isinstance(rj.get("issues"), list) or str(
            rj.get("decision") or "").lower() not in ("approve", "block", "revise"):
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "second_round_unavailable_or_invalid"}, {}
    split = partition_issues(rj["issues"], brief, pack)
    detail = {"review": rj, "issue_policy": {
        "material": len(split["material"]),
        "advisory": len(split["advisory"]),
        "ungrounded_material": len(split["ungrounded_material"]),
    }}
    if split["ungrounded_material"]:
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "second_round_unverifiable",
                "issues": rj["issues"], "review": rj}, detail
    if split["material"]:
        return {"state": "blocked", "status": "blocked",
                "decision": "second_round_material_unresolved",
                "issues": rj["issues"], "review": rj}, detail
    if rj["decision"] == "block" and not rj["issues"]:
        return {"state": "hold_for_review", "status": "hold_for_review",
                "decision": "second_round_unexplained_block"}, detail
    return {"state": "approved", "status": "approved",
            "decision": "approve", "review": rj,
            "advisory_only": bool(rj["issues"])}, detail

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
