#!/usr/bin/env python3
"""Read-only business scope audit against a frozen NAS discovery file.

Run:
 PYTHONPATH=. python3 scripts/audit_business_scope_shadow.py \
 --input data/raw/2026-10-30/raw_articles_all.json

Never fetches URLs, calls LLM/TTS, changes event ledgers or sends mail.
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from skills.filter_relevant_articles.business_scope_gate import classify_business_scope


def review_file(path: Path):
    obj = json.loads(path.read_text(encoding="utf-8"))
    arr = obj if isinstance(obj, list) else obj.get("articles", obj.get("events", []))
    results = []
    counts = Counter()
    for item in arr:
        if not isinstance(item, dict):
            continue
        is_event = "event_title" in item and not item.get("title")
        result = classify_business_scope(item, event_level=is_event)
        counts[result["decision"]] += 1
        counts["reason:" + result["reason_code"]] += 1
        if result["decision"] != "REJECT":
            results.append({
                "id": item.get("article_id") or item.get("event_id") or "",
                "title": item.get("title") or item.get("event_title") or "",
                "source": item.get("source_name") or "",
                "reported_at": item.get("published_at") or "",
                "url": item.get("url") or item.get("source_url") or "",
                "decision": result["decision"],
                "reason": result["reason_code"],
                "scope": result["scope"],
                "action": result["action"],
            })
    return {
        "source_file": str(path),
        "total": len(arr),
        "counts": dict(sorted(counts.items())),
        "admitted_or_review": results,
        "limitations": [
            "Rules infer primary article scope; accepted is not independently verified fact.",
            "This script is deterministic and read-only; no LLM and no source refetch.",
            "Compare full retained 25 manually, grouped by reason, before production.",
            "Do not derive an event occurrence date from reported_at.",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(review_file(args.input), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
