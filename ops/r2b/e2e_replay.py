#!/usr/bin/env python3
"""Isolated same-window replay: production snapshot -> periodic synthesis -> M2.

Reads a production event window into a temporary project root; never writes
production data.  It deliberately does not upgrade old events to `verified`.
The output shows what the strict R3 gate would see, and how M2 would annotate
the same raw pool.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from skills.m2.tri_state_prefilter import annotate_events
from skills.report_policy import evaluate_verification
from skills.periodic_synthesis.periodic_synthesis import (
    _is_verified_reportable_event, build_periodic_synthesis,
)


def _explicit_event_date(text: str):
    m = re.search(r"(20(?:25|26))年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _apply_explicit_date_verification(root: str, start: date, end: date) -> int:
    """Mark only events whose own fact/evidence text states an explicit in-window date."""
    marked = 0
    files = []
    for name in ("events_analyzed.json", "events_scored_novelty.json"):
        files.extend(Path(root).glob(f"data/events/*/{name}"))
    for p in files:
        d = json.loads(p.read_text())
        events = d.get("events", [])
        for e in events:
            text = " ".join(str(e.get(k) or "") for k in ("fact", "evidence_text", "event_title"))
            explicit = _explicit_event_date(text)
            if explicit and start - timedelta(days=1) <= explicit <= end + timedelta(days=1):
                e["event_occurred_at"] = explicit.isoformat()
                e["event_time_source"] = "occurred"
            v = evaluate_verification(e, as_of_date=end,
                                      article={"published_at": e.get("published_at")})
            e["verification_status"] = v["verification_status"]
            e["needs_verification"] = v["needs_verification"]
            e["verification_reasons"] = v["verification_reasons"]
            if v["verified"]:
                marked += 1
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    return marked


def _prefer_scored_pool(root: str) -> None:
    """Synthesis loader prefers analyzed; point it at the full scored pool in replay."""
    for scored in Path(root).glob("data/events/*/events_scored_novelty.json"):
        analyzed = scored.with_name("events_analyzed.json")
        if analyzed.exists():
            analyzed.write_bytes(scored.read_bytes())


def _copy_window(src: str, dst: str, start: date, end: date) -> int:
    total = 0
    cur = start
    while cur <= end:
        d = cur.isoformat()
        src_dir = Path(src) / "data/events" / d
        if src_dir.exists():
            dst_dir = Path(dst) / "data/events" / d
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dst_dir)
            total += 1
        cur += timedelta(days=1)
    cfg_src = Path(src) / "config/periodic_report.yaml"
    if cfg_src.exists():
        cfg_dst = Path(dst) / "config"
        cfg_dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg_src, cfg_dst / "periodic_report.yaml")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default="/app/working/projects/watsons-retail-intel")
    ap.add_argument("--start", default="2026-08-03")
    ap.add_argument("--end", default="2026-08-09")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    tmp = tempfile.mkdtemp(prefix="onepass_e2e_")
    try:
        copied = _copy_window(args.project_root, tmp, start, end)
        explicitly_verified = _apply_explicit_date_verification(tmp, start, end)
        _prefer_scored_pool(tmp)
        period_info = {"year": start.year, "week": start.isocalendar().week,
                       "start": args.start, "end": args.end}
        synth = build_periodic_synthesis("weekly", period_info, tmp)
        raw = []
        for d in [start + timedelta(days=i) for i in range((end - start).days + 1)]:
            base = Path(tmp) / "data/events" / d.isoformat()
            p = base / "events_scored_novelty.json"
            if not p.exists():
                p = base / "events_analyzed.json"
            if p.exists():
                raw.extend(json.loads(p.read_text()).get("events", []))
        annotated = annotate_events(raw)
        labels = Counter(a["tri_state"]["label"] for a in annotated)
        verifications = [
            evaluate_verification(e, as_of_date=end,
                                  article={"published_at": e.get("published_at")})
            for e in raw
        ]
        v_reasons = Counter(r for v in verifications for r in v.get("verification_reasons", []))
        replayed_verified = sum(1 for v in verifications if v.get("verified"))
        verified = sum(1 for e in raw if _is_verified_reportable_event(e))
        reportable = sum(1 for e in raw if _is_verified_reportable_event(e) and
                         (e.get("event_title") or e.get("fact")))
        result = {
            "date": "2026-10-26", "mode": "isolated same-window replay; no production writes",
            "window": [args.start, args.end], "days_copied": copied,
            "raw_events": len(raw), "m2_labels": dict(labels),
            "verified_reportable_events": verified,
            "verification_replay": {
                "verified": replayed_verified,
                "explicit_date_verified_before_synthesis": explicitly_verified,
                "needs_verification": len(raw) - replayed_verified,
                "top_reasons": dict(v_reasons.most_common(6)),
                "note": "Re-ran report_policy.evaluate_verification from raw fields; "
                        "published_at alone is not accepted as event occurrence proof.",
            },
            "reportable_events": reportable,
            "synthesis_success": synth.get("success"),
            "clusters": len(synth.get("synthesis", {}).get("all_clusters", [])),
            "synthesis_event_stats": synth.get("synthesis", {}).get("event_stats", {}),
            "coverage": synth.get("coverage_report", {}),
            "r3_candidate_status": "blocked_no_verified_core_events" if reportable == 0 else "candidate_review_required",
            "policy": "Old raw P1/P2 events are never bulk-marked verified; missing evidence stays review/reference.",
            "production_untouched": True,
        }
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
