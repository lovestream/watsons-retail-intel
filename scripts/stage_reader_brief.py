#!/usr/bin/env python3
"""Generate an approved weekly/monthly/yearly reader HTML without SMTP.

Requires real source data and source coverage. Produces a manifest + approved
brief in data/runs/periodic/<kind>/<period_id>/reader_brief.
Use an isolated copy of the NAS project/data for this staging rehearsal.
"""
import argparse
import json
import sys
from pathlib import Path

from skills.reader_brief.production_delivery import prepare_approved_brief
from skills.generate_periodic_report.generate_periodic_report import (
    _load_or_build_synthesis, _load_cross_period)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--period-type", required=True,
                   choices=["weekly", "monthly", "yearly"])
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--year", default="")
    p.add_argument("--month", default="")
    p.add_argument("--audio-file", default="")
    a = p.parse_args()
    root = str(Path(a.root).resolve())
    if a.period_type == "weekly":
        if not a.start or not a.end:
            p.error("weekly requires --start and --end")
        period = {"start": a.start, "end": a.end}
    elif a.period_type == "monthly":
        if not a.year or not a.month:
            p.error("monthly requires --year and --month")
        period = {"year": a.year, "month": f"{int(a.month):02d}"}
    else:
        if not a.year:
            p.error("yearly requires --year")
        period = {"year": a.year}

    cross = None
    if a.period_type in ("monthly", "yearly"):
        cross = _load_cross_period(a.period_type, period, root)
        if not cross or cross.get("status") != "complete":
            print(json.dumps({"approved": False, "error": "cross_period_not_complete",
                              "status": (cross or {}).get("status")},
                             ensure_ascii=False))
            return 2
    synthesis, coverage, ok = _load_or_build_synthesis(a.period_type, period, root)
    if not ok or not synthesis or (coverage or {}).get("status") == "failing":
        print(json.dumps({"approved": False, "error": "source_coverage_unavailable",
                          "coverage": coverage}, ensure_ascii=False))
        return 2
    result = prepare_approved_brief(a.period_type, period, root, synthesis,
                                    cross=cross, audio_path=a.audio_file)
    result["smtp_sent"] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("approved") else 2


if __name__ == "__main__":
    sys.exit(main())
