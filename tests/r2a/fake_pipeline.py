"""Fault-injection pipeline used only by R2-A subprocess tests.

It has the same callable contract as the production pipeline but never makes a
network request and never writes a report.  The mode is selected with
``R2A_FAKE_MODE`` so the real launcher's exit-code and manifest paths can be
tested end to end.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

CRITICAL = ("extract", "temporal_fact_validation", "novelty_check", "score", "analyze")


def _write(path: Path, key: str, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({key: [{"event_id": f"T{i}"} for i in range(count)]}), encoding="utf-8")


def run_daily_pipeline(
    project_root: str,
    date: str,
    start_step: str = "extract",
    end_step: str = "analyze",
    use_llm: bool = True,
    send_email: bool = False,
    recipient: str = None,
    resume: bool = True,
    force: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
) -> Dict[str, Any]:
    mode = os.environ.get("R2A_FAKE_MODE", "success")
    root = Path(project_root) / "data"
    _write(root / "cleaned" / date / "cleaned_articles.json", "articles", 0 if mode in ("no_signal", "collect_failure") else 1)

    if mode == "exception":
        raise RuntimeError("injected pipeline exception")

    if mode in ("no_signal", "collect_failure"):
        for name, count in (("events_raw", 0), ("events_temporal", 0), ("events_scored", 0), ("events_analyzed", 0)):
            _write(root / "events" / date / f"{name}.json", "events", count)
        failed = mode == "collect_failure"
        return {
            "ok": not failed,
            "sendable": False,
            "no_signal": True,
            "steps": [
                {
                    "name": "collect",
                    "status": "failed" if failed else "success",
                    "stats": {
                        "total_collected": 0,
                        "collection_completed": not failed,
                        "collection_date": date,
                        "source_total": 2,
                        "source_success": 0,
                        "source_failed": 2 if failed else 0,
                        "collect_errors_count": 2 if failed else 0,
                    },
                }
            ] + [{"name": n, "status": "skipped" if n == "extract" else "success", "stats": {}} for n in CRITICAL],
        }

    raw = 0 if mode == "empty" else 2
    _write(root / "events" / date / "events_raw.json", "events", raw)
    _write(root / "events" / date / "events_temporal.json", "events", raw)
    _write(root / "events" / date / "events_scored.json", "events", 2)
    _write(root / "events" / date / "events_analyzed.json", "events", 1 if mode == "incomplete" else 2)
    analyze_stats = (
        {"llm_success_count": 0, "llm_failed_count": 2}
        if mode == "llm_degraded"
        else {"llm_success_count": 2, "llm_failed_count": 0}
    )
    steps = [{
        "name": "collect",
        "status": "success",
        "stats": {
            "total_collected": 1,
            "collection_completed": True,
            "collection_date": date,
            "source_total": 2,
            "source_success": 1,
            "source_failed": 0,
            "collect_errors_count": 0,
        },
    }] + [
        {
            "name": n,
            "status": "failed" if (mode == "step_failed" and n == "score") else "success",
            "stats": analyze_stats if n == "analyze" else {},
        }
        for n in CRITICAL
    ]
    return {
        "ok": mode != "step_failed",
        "sendable": False,
        "no_signal": False,
        "steps": steps,
    }
