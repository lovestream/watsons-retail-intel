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
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from skills.m2.tri_state_prefilter import annotate_events
from skills.report_policy import evaluate_verification
from skills.periodic_synthesis.periodic_synthesis import (
    _is_verified_reportable_event, build_periodic_synthesis,
)


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
        period_info = {"year": start.year, "week": start.isocalendar().week,
                       "start": args.start, "end": args.end}
        synth = build_periodic_synthesis("weekly", period_info, tmp)
        raw = []
        for d in [start + timedelta(days=i) for i in range((end - start).days + 1)]:
            p = Path(tmp) / "data/events" / d.isoformat() / "events_analyzed.json"
            if p.exists():
                raw.extend(json.loads(p.read_text()).get("events", []))
        annotated = annotate_events(raw)
        labels = Counter(a["tri_state"]["label"] for a in annotated)
        verifications = [
            evaluate_verification(e, as_of_date=start,
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
                "needs_verification": len(raw) - replayed_verified,
                "top_reasons": dict(v_reasons.most_common(6)),
                "note": "Re-ran report_policy.evaluate_verification from raw fields; "
                        "published_at alone is not accepted as event occurrence proof.",
            },
            "reportable_events": reportable,
            "synthesis_success": synth.get("success"),
            "clusters": len(synth.get("synthesis", {}).get("all_clusters", [])),
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
