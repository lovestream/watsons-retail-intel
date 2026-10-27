#!/usr/bin/env python3
"""Read-only source coverage/yield audit (AIHOT "source coverage" analogue).

Counts, per source: unique event URLs observed, events extracted, verified,
reportable, and how many distinct report periods used them.  It never writes to
the production event ledger.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List

try:
    from skills.report_policy import is_reportable_event, is_verified_event
except Exception:  # pragma: no cover
    is_reportable_event = None
    is_verified_event = None


def _source_name(e: Dict[str, Any]) -> str:
    return str(e.get("source_name") or e.get("source") or e.get("site_name") or
               "unknown")


def _domain(url: str) -> str:
    u = str(url or "")
    if "://" in u:
        u = u.split("://", 1)[1]
    return u.split("/", 1)[0].lower()


def audit(project_root: str, date_glob: str = "*") -> Dict[str, Any]:
    rows: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"source": "", "urls": set(), "events": 0, "verified": 0,
                 "reportable": 0, "dates": set(), "domains": set()})
    for path in sorted(glob.glob(os.path.join(
            project_root, "data", "events", date_glob, "events_analyzed.json"))):
        date = os.path.basename(os.path.dirname(path))
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        events = data.get("events", []) if isinstance(data, dict) else data
        for e in events:
            if not isinstance(e, dict):
                continue
            name = _source_name(e)
            r = rows[name]
            r["source"] = name
            r["events"] += 1
            r["dates"].add(date)
            url = str(e.get("source_url") or "")
            if url:
                r["urls"].add(url)
                d = _domain(url)
                if d:
                    r["domains"].add(d)
            if is_verified_event and is_verified_event(e):
                r["verified"] += 1
            if is_reportable_event and is_reportable_event(e):
                r["reportable"] += 1
    out = []
    for r in rows.values():
        out.append({
            "source": r["source"],
            "events": r["events"],
            "unique_urls": len(r["urls"]),
            "verified": r["verified"],
            "reportable": r["reportable"],
            "reportable_rate": round(r["reportable"] / r["events"], 4)
            if r["events"] else 0.0,
            "active_dates": len(r["dates"]),
            "domains": len(r["domains"]),
        })
    out.sort(key=lambda x: (-x["reportable"], -x["verified"], x["source"]))
    return {"sources": out, "total_sources": len(out),
            "total_events": sum(x["events"] for x in out),
            "total_reportable": sum(x["reportable"] for x in out),
            "note": "read-only audit; no ledger mutation"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--date-glob", default="*")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    result = audit(args.project_root, args.date_glob)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(text)
    print(json.dumps({k: result[k] for k in
                      ("total_sources", "total_events", "total_reportable")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
