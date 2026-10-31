"""P09 semantic issue admissibility: bounded, provenance-linked review.

We do NOT decide semantic truth with regex. A material LLM finding must
provide a traceable claim+source excerpt; if it cannot, hold for human review
rather than silently ignore it or retry the model until it says approve.
"""
import re
from typing import Dict, List


MATERIAL = {
    "wrong_subject", "wrong_date", "wrong_status", "incorrect_number",
    "wrong_source_attribution", "unsupported_material_claim",
    "cross_subject_causality", "assumed_internal_capability",
    "wrong_region_or_scope", "contradicted_source",
}
ADVISORY = {
    "wording_preference", "nonmaterial_precision", "stylistic",
    "brevity", "readability",
}
LOCATION = re.compile(r"^([a-zA-Z_]+)(?:\[(\d+)\])?(?:\.([a-zA-Z_]+))?$")


def _get_item(brief: Dict, location: str):
    m = LOCATION.fullmatch(str(location or ""))
    if not m:
        return None
    val = brief.get(m.group(1))
    if m.group(2) is not None:
        if not isinstance(val, list) or int(m.group(2)) >= len(val):
            return None
        val = val[int(m.group(2))]
    if m.group(3) is not None:
        if not isinstance(val, dict):
            return None
        val = val.get(m.group(3))
    return val


def _contains(item, quote: str) -> bool:
    if isinstance(item, str):
        return quote in item
    if isinstance(item, dict):
        # IDs and links are not sufficient to prove the quoted claim occurred.
        return any(_contains(v, quote) for k, v in item.items()
                   if k not in ("source_refs", "fact_ids", "claim_ids", "audit"))
    if isinstance(item, list):
        return any(_contains(v, quote) for v in item)
    return False


def classify_issue(issue: Dict, brief: Dict, pack: Dict) -> Dict:
    """Classify an issue as material, advisory, or ungrounded_material.

    Model severity and decision alone never determine admissibility.
    """
    if not isinstance(issue, dict):
        return {"kind": "ungrounded_material", "reason": "bad_issue_type"}
    cat = str(issue.get("category") or "").strip().lower()
    sev = str(issue.get("severity") or "").strip().lower()
    if cat in ADVISORY:
        # A reported critical/major error must not be downgraded by relabeling
        # it a style note. Hold for a human when the reviewer contradicts itself.
        if sev in ("major", "critical"):
            return {"kind": "ungrounded_material", "reason": "severity_category_conflict"}
        return {"kind": "advisory", "reason": cat}
    if cat not in MATERIAL:
        if sev in ("minor", "info") and not cat:
            return {"kind": "advisory", "reason": "legacy_minor"}
        return {"kind": "ungrounded_material", "reason": "missing_or_unknown_category"}
    location = str(issue.get("location") or "")
    snippet = str(issue.get("brief_quote") or "")
    source = str(issue.get("source_excerpt") or "")
    cid = str(issue.get("claim_id") or "")
    url = str(issue.get("evidence_url") or "")
    item = _get_item(brief, location)
    if not item or not snippet or not _contains(item, snippet):
        return {"kind": "ungrounded_material", "reason": "brief_quote_not_at_location"}
    facts = [f for f in pack.get("facts") or []
             if f.get("claim_id") == cid and (f.get("source") or {}).get("url") == url]
    if not cid or not url or not facts:
        return {"kind": "ungrounded_material", "reason": "claim_or_source_unbound"}
    if not source or not any(source in (f.get("text") or "") for f in facts):
        return {"kind": "ungrounded_material", "reason": "source_excerpt_unbound"}
    # A field-level issue (e.g. signals[0].fact) also needs the card's own
    # claim bindings. Without this, a valid source from a DIFFERENT card could
    # give a bogus reviewer finding an apparently admissible anchor.
    m = LOCATION.fullmatch(location)
    card = item
    if m and m.group(2) is not None and m.group(3) is not None:
        card = _get_item(brief, f"{m.group(1)}[{m.group(2)}]")
    if isinstance(card, dict):
        fids = set(card.get("fact_ids") or card.get("linked_fact_ids") or [])
        cids = set(card.get("claim_ids") or [])
        if fids and not any(f.get("fact_id") in fids for f in facts):
            return {"kind": "ungrounded_material", "reason": "source_not_of_location"}
        if cids and cid not in cids:
            return {"kind": "ungrounded_material", "reason": "claim_not_of_location"}
    return {"kind": "material", "reason": cat}


def partition_issues(issues: List[Dict], brief: Dict, pack: Dict) -> Dict:
    out = {"material": [], "advisory": [], "ungrounded_material": []}
    for i in issues:
        info = classify_issue(i, brief, pack)
        out[info["kind"]].append(dict(issue=i, reason=info["reason"]))
    return out
