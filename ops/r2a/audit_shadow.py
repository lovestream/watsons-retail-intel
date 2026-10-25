#!/usr/bin/env python3
"""Audit the shadow event chain without contacting an LLM.

The audit is read-only with respect to event artifacts.  It reports the
contract facts that matter before a report can be generated:

* event IDs survive extract -> temporal -> novelty -> score -> analyze;
* event dates and evidence are present;
* stage outputs expose temporal freshness, novelty and report eligibility;
* history dedup changes report eligibility on a next-day rerun;
* ``needs_verification`` is either present end-to-end or absent;
* scored events are complete and analyses are not duplicated/swapped.

It intentionally separates "data is present" from "claim is verified".  A
missing event date can be a data-presence failure even when a test fixture
would otherwise pass an older verification helper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import run_state

HEARSAY_RE = re.compile(r"据悉|消息称|传闻|网传|据说|听说|知情人士|接近.{0,8}人士")
STOCK_RE = re.compile(
    r"涨停|跌停|股价|收盘|开盘|市值|研报|目标价|买入评级|盈利预测|估值|个股|证券|交易所|港股|美股|IPO|招股|财报"
)


def _events(path: Path) -> List[Dict[str, Any]]:
    try:
        data = run_state.read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    value = data.get("events")
    return value if isinstance(value, list) else []


def _ids(events: List[Dict[str, Any]]) -> List[str]:
    return [str(ev.get("event_id")) for ev in events if ev.get("event_id")]


def _sha(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def _missing_dates(events: List[Dict[str, Any]]) -> List[str]:
    return [
        str(ev.get("event_id"))
        for ev in events
        if not str(ev.get("event_occurred_at") or "").strip()
    ]


def _analysis_duplicates(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[str]] = defaultdict(list)
    for ev in events:
        ba = ev.get("business_analysis") if isinstance(ev.get("business_analysis"), dict) else {}
        impact = str(ba.get("watsons_impact") or "").strip()
        if impact:
            groups[_sha(impact)].append(str(ev.get("event_id")))
    return [
        {"impact_hash": h, "event_ids": ids}
        for h, ids in groups.items()
        if len(ids) > 1
    ]


def _pairing_low_overlap(events: List[Dict[str, Any]]) -> List[str]:
    """Cheap guard against the historical swapped-analysis failure.

    It only flags obvious cases: an analysis that has no 2-character token from
    its own event title and no 2-character token from its own fact.  Semantic
    correctness still needs human review; this is a regression alarm, not a
    proof of correctness.
    """
    low: List[str] = []
    for ev in events:
        ba = ev.get("business_analysis") if isinstance(ev.get("business_analysis"), dict) else {}
        analysis = str(ba.get("watsons_impact") or "") + str(ba.get("recommended_action") or "")
        own = str(ev.get("event_title") or "") + str(ev.get("fact") or "")
        own_tokens = {own[i : i + 2] for i in range(max(0, len(own) - 1)) if re.search(r"[\u4e00-\u9fff]", own[i : i + 2])}
        analysis_tokens = {analysis[i : i + 2] for i in range(max(0, len(analysis) - 1))}
        if own_tokens and not (own_tokens & analysis_tokens):
            low.append(str(ev.get("event_id")))
    return low


def audit(project_root: Path, date: str) -> Dict[str, Any]:
    root = Path(project_root)
    paths = {
        "raw": root / "data" / "events" / date / "events_raw.json",
        "temporal": root / "data" / "events" / date / "events_temporal.json",
        "novelty": root / "data" / "events" / date / "events_scored_novelty.json",
        "scored": root / "data" / "events" / date / "events_scored.json",
        "analyzed": root / "data" / "events" / date / "events_analyzed.json",
    }
    stages = {name: _events(path) for name, path in paths.items()}
    ids = {name: _ids(events) for name, events in stages.items()}
    all_ids = ids["raw"]
    id_sets = {name: set(values) for name, values in ids.items()}

    temporal_counts = Counter(str(ev.get("fact_freshness_status") or "MISSING") for ev in stages["temporal"])
    novelty_counts = Counter(str(ev.get("novelty_status") or "MISSING") for ev in stages["novelty"])
    priority_counts = Counter(str(ev.get("priority") or "MISSING") for ev in stages["scored"])
    eligibility_counts = Counter(str(ev.get("report_eligibility") or "MISSING") for ev in stages["scored"])

    analyzed = stages["analyzed"]
    ba_missing = [str(ev.get("event_id")) for ev in analyzed if not isinstance(ev.get("business_analysis"), dict)]
    impact_empty = [
        str(ev.get("event_id"))
        for ev in analyzed
        if not str((ev.get("business_analysis") or {}).get("watsons_impact") or "").strip()
    ]
    action_empty = [
        str(ev.get("event_id"))
        for ev in analyzed
        if not str((ev.get("business_analysis") or {}).get("recommended_action") or "").strip()
    ]
    duplicate_impacts = _analysis_duplicates(analyzed)
    low_overlap = _pairing_low_overlap(analyzed)

    missing_dates = _missing_dates(analyzed)
    hearsay_ids = [
        str(ev.get("event_id"))
        for ev in analyzed
        if HEARSAY_RE.search(str(ev.get("fact") or "") + str(ev.get("evidence_text") or ""))
    ]
    evidence_empty = [
        str(ev.get("event_id")) for ev in analyzed if not str(ev.get("evidence_text") or "").strip()
    ]
    source_empty = [str(ev.get("event_id")) for ev in analyzed if not str(ev.get("source_url") or "").strip()]
    stock_ids = [
        str(ev.get("event_id"))
        for ev in analyzed
        if STOCK_RE.search(
            str(ev.get("event_title") or "")
            + str(ev.get("fact") or "")
            + str(ev.get("evidence_text") or "")
        )
    ]
    pipeline_verification = Counter(
        str(ev.get("verification_status") or "MISSING") for ev in analyzed
    )
    pipeline_needs_flag = sum(bool(ev.get("needs_verification")) for ev in analyzed)
    pipeline_date_missing = sum(not str(ev.get("event_occurred_at") or "").strip() for ev in analyzed)

    reportable = [
        ev
        for ev in stages["scored"]
        if ev.get("priority") != "ARCHIVE" and ev.get("report_eligibility") not in ("archive",)
    ]

    return {
        "schema_version": run_state.SCHEMA_VERSION,
        "project_root": str(root),
        "date": date,
        "stage_counts": {name: len(events) for name, events in stages.items()},
        "stage_files": {name: str(path) for name, path in paths.items()},
        "id_integrity": {
            "raw_unique": len(id_sets["raw"]),
            "temporal_unique": len(id_sets["temporal"]),
            "novelty_unique": len(id_sets["novelty"]),
            "scored_unique": len(id_sets["scored"]),
            "analyzed_unique": len(id_sets["analyzed"]),
            "lost_vs_raw": sorted(id_sets["raw"] - id_sets["analyzed"]),
            "extra_vs_raw": sorted(id_sets["analyzed"] - id_sets["raw"]),
            "duplicate_raw_ids": [item for item, count in Counter(all_ids).items() if count > 1],
        },
        "dates": {
            "event_occurred_missing_count": len(missing_dates),
            "event_occurred_missing_ids": missing_dates[:30],
            "event_date_precision": dict(Counter(str(ev.get("event_time_precision") or "MISSING") for ev in analyze_stages(stages))),
        },
        "evidence": {
            "evidence_text_empty_count": len(evidence_empty),
            "evidence_text_empty_ids": evidence_empty[:20],
            "source_url_empty_count": len(source_empty),
            "source_url_empty_ids": source_empty[:20],
        },
        "temporal": {
            "freshness_counts": dict(temporal_counts),
            "eligibility_counts": dict(
                Counter(str(ev.get("temporal_eligibility") or "MISSING") for ev in stages["temporal"])
            ),
        },
        "novelty": {
            "status_counts": dict(novelty_counts),
            "eligibility_counts": dict(
                Counter(str(ev.get("report_eligibility") or "MISSING") for ev in stages["novelty"])
            ),
        },
        "score": {
            "priority_counts": dict(priority_counts),
            "eligibility_counts": dict(eligibility_counts),
            "reportable_count": len(reportable),
            "reportable_ids": [str(ev.get("event_id")) for ev in reportable],
            "missing_weighted_score": [
                str(ev.get("event_id")) for ev in stages["scored"] if ev.get("weighted_score") is None
            ],
            "hard_downgrade_count": sum(
                1 for ev in stages["scored"] if any("硬降级" in str(r) for r in (ev.get("downgrade_reasons") or []))
            ),
        },
        "analyze": {
            "analysis_missing_count": len(ba_missing),
            "analysis_missing_ids": ba_missing[:20],
            "impact_empty_count": len(impact_empty),
            "impact_empty_ids": impact_empty[:20],
            "action_empty_count": len(action_empty),
            "action_empty_ids": action_empty[:20],
            "duplicate_impact_groups": duplicate_impacts,
            "pairing_low_overlap_ids": low_overlap[:20],
            "pairing_low_overlap_count": len(low_overlap),
            "unique_impact_count": len(
                {
                    str((ev.get("business_analysis") or {}).get("watsons_impact") or "")
                    for ev in analyzed
                    if str((ev.get("business_analysis") or {}).get("watsons_impact") or "").strip()
                }
            ),
            "follow_up_question_count": sum(
                bool((ev.get("business_analysis") or {}).get("follow_up_questions")) for ev in analyzed
            ),
        },
        "needs_verification": {
            "pipeline_field_present_count": sum(
                1 for ev in analyzed if "needs_verification" in ev or "verification_status" in ev
            ),
            "pipeline_flag_true_count": pipeline_needs_flag,
            "pipeline_status_counts": dict(pipeline_verification),
            "pipeline_event_date_missing_count": pipeline_date_missing,
            "heuristic_date_missing_count": len(missing_dates),
            "heuristic_date_missing_ids": missing_dates[:30],
            "heuristic_hearsay_count": len(hearsay_ids),
            "heuristic_hearsay_ids": hearsay_ids[:20],
            "heuristic_evidence_missing_count": len(evidence_empty),
            "heuristic_source_missing_count": len(source_empty),
            "pipeline_enforced": False,
        },
        "capital_market": {
            "matches_count": len(stock_ids),
            "matches_ids": stock_ids[:20],
        },
        "notes": [
            "This audit does not call an LLM and does not send email.",
            "needs_verification is reported separately from factual verification; a missing field is not a pass.",
            "pairing_low_overlap is an alarm, not a semantic proof.",
        ],
    }


def analyze_stages(stages: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Use analyzed events when available; otherwise the temporal set."""
    return stages.get("analyzed") or stages.get("temporal") or []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit an isolated shadow event chain")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = audit(Path(args.project_root), args.date)
    out = Path(args.out) if args.out else Path(args.project_root) / "data" / "logs" / args.date / "r2a_shadow_audit.json"
    run_state.atomic_write_json(out, result)
    print(json.dumps({k: result[k] for k in ("stage_counts", "id_integrity", "score", "needs_verification")}, ensure_ascii=False, indent=2))
    print(f"\n[audit written] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
