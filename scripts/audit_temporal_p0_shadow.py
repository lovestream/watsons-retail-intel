#!/usr/bin/env python3
"""Read-only pre/post temporal contract audit.

Reads existing NAS data only; writes JSON to stdout. No LLM, network, SMTP,
new article fetch, source rewrite, ledger mutation, or implicit backfill.

Example:
 PYTHONPATH=. python3 scripts/audit_temporal_p0_shadow.py --project-root .
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import json

from skills.temporal_fact_validation.date_contract import (
    exact_day, trustworthy_occurred_day, normalize_precision
)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def as_items(data, key):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get(key) or []
    return []


def audit(root: Path, max_examples=15):
    all_stats = Counter()
    by_priority = defaultdict(Counter)
    by_precision = Counter()
    examples = []
    events_root = root / "data" / "events"
    for path in sorted(events_root.glob("*/events_analyzed.json")):
        day = path.parent.name
        events = as_items(read_json(path), "events")
        articles = as_items(read_json(root / "data" / "cleaned" / day /
                                      "cleaned_articles.json"), "articles")
        article_by_id = {
            str(a.get("article_id") or a.get("id")): a
            for a in articles if isinstance(a, dict)
        }
        for ev in events:
            if not isinstance(ev, dict):
                continue
            priority = str(ev.get("priority") or "unknown").upper()
            stats = by_priority[priority]
            all_stats["total"] += 1
            stats["total"] += 1
            raw_day = ev.get("event_occurred_at") or ev.get("occurred_at") or ""
            precision = normalize_precision(ev.get("event_time_precision"))
            by_precision[precision] += 1
            if raw_day:
                all_stats["has_claimed_occurred"] += 1
                stats["has_claimed_occurred"] += 1
            if precision == "day":
                all_stats["claimed_day_precision"] += 1
                stats["claimed_day_precision"] += 1
            art_id = str(ev.get("source_article_id") or "")
            source = article_by_id.get(art_id) or {}
            original = source.get("content") or source.get("article_content") or ""
            quote = ev.get("evidence_text") or ""
            evidence_verified = bool(
                original and isinstance(quote, str) and quote
                and quote in original
            )
            if not original:
                all_stats["missing_fulltext"] += 1
                stats["missing_fulltext"] += 1
            elif not evidence_verified:
                all_stats["source_quote_mismatch"] += 1
                stats["source_quote_mismatch"] += 1
            ev_view = dict(ev, evidence_verified=evidence_verified)
            trusted = trustworthy_occurred_day(ev_view)
            if trusted:
                all_stats["trusted_occurred_day"] += 1
                stats["trusted_occurred_day"] += 1
            elif raw_day:
                all_stats["untrusted_claimed_day"] += 1
                stats["untrusted_claimed_day"] += 1
                if len(examples) < max_examples:
                    examples.append({
                        "event_id": ev.get("event_id"),
                        "period_day": day,
                        "priority": priority,
                        "claimed": str(raw_day)[:25],
                        "precision": precision,
                        "source_quote_verified": evidence_verified,
                        "reason": (
                            "bad_date" if not exact_day(str(raw_day))
                            else "precision_mismatch" if precision != "day"
                            else "unsupported_day_or_role"
                        ),
                    })
    return {
        "mode": "readonly",
        "methodology": "syntactic/evidence gate, not human truth adjudication",
        "all": dict(all_stats),
        "precision": dict(by_precision),
        "by_priority": {k: dict(v) for k, v in sorted(by_priority.items())},
        "examples": examples,
        "caveats": [
            "No rows were modified; no network/LLM called.",
            "The 487/118 and P1/P2 prior baseline must be reconciled against the exact dataset version.",
            "Trusted here means a strict machine evidence precondition; semantic correctness still needs source-level review.",
            "Missing raw article fulltext fails closed, which can lower coverage without creating a false date.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--max-examples", type=int, default=15)
    args = parser.parse_args()
    result = audit(args.project_root.resolve(), max(0, args.max_examples))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
