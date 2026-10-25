#!/usr/bin/env python3
"""Independent health check for R2-A launcher manifests.

The watchdog reads only on-disk run manifests and scheduler heartbeats.  It
does not inspect a chat transcript, does not call an agent, and therefore
cannot mistake "已启动" for a completed run.

Alert conditions:
* no manifest exists at all                         -> missing_manifest
* latest run failed / exception / incomplete pool   -> run_failed / empty_pool
* latest run has a stale heartbeat                  -> stalled_running
* latest healthy run is older than 48 hours         -> stale_healthy_manifest
* scheduler heartbeat is absent when required       -> scheduler_missing
* scheduler heartbeat is stale                      -> scheduler_stalled
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import run_state

CST = run_state.CST


def _manifest_paths(state_dir: Path) -> List[Path]:
    root = Path(state_dir) / "runs"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*/manifest.json"))


def _age_hours(now: datetime, value: Any) -> Optional[float]:
    dt = run_state.parse_iso(value)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def _age_minutes(now: datetime, value: Any) -> Optional[float]:
    hours = _age_hours(now, value)
    return hours * 60.0 if hours is not None else None


def _alert(code: str, severity: str, message: str, manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if manifest:
        item["run_id"] = manifest.get("run_id")
        item["date"] = manifest.get("date")
        item["manifest"] = manifest.get("_path")
    return item


def _latest(manifests: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not manifests:
        return None
    return max(manifests, key=lambda m: str(m.get("started_at") or ""))


def evaluate(
    state_dir: Path,
    now: Optional[datetime] = None,
    max_age_hours: float = 48.0,
    stale_minutes: float = 120.0,
    require_scheduler: bool = False,
) -> Dict[str, Any]:
    now = now or run_state.utc_now()
    alerts: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    manifests: List[Dict[str, Any]] = []

    for path in _manifest_paths(Path(state_dir)):
        try:
            data = run_state.read_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            alerts.append(_alert("invalid_manifest", "critical", f"cannot read {path}: {exc}"))
            continue
        data["_path"] = str(path)
        if not data.get("run_id") or not data.get("status"):
            alerts.append(_alert("invalid_manifest", "critical", "manifest missing run_id/status", data))
            continue
        manifests.append(data)

    latest = _latest(manifests)
    latest_view: Optional[Dict[str, Any]] = None
    last_healthy_view: Optional[Dict[str, Any]] = None

    if latest is None:
        alerts.append(
            _alert(
                "missing_manifest",
                "critical",
                "no launcher manifest: the task may never have started (an agent reply is not evidence)",
            )
        )
    else:
        latest_view = {
            "run_id": latest.get("run_id"),
            "date": latest.get("date"),
            "status": latest.get("status"),
            "reason": latest.get("reason"),
            "exit_code": latest.get("exit_code"),
            "started_at": latest.get("started_at"),
            "finished_at": latest.get("finished_at"),
            "heartbeat_at": latest.get("heartbeat_at"),
            "counts": latest.get("counts") or {},
            "reportable_count": latest.get("reportable_count"),
            "errors": (latest.get("errors") or [])[:3],
            "manifest": latest.get("_path"),
        }
        status = str(latest.get("status") or "")
        counts = latest.get("counts") if isinstance(latest.get("counts"), dict) else {}
        cleaned = int(counts.get("cleaned_articles", 0) or 0)
        raw = int(counts.get("events_raw", 0) or 0)
        scored = int(counts.get("events_scored", 0) or 0)
        analyzed = int(counts.get("events_analyzed", 0) or 0)

        if status == "running":
            hb_age = _age_minutes(now, latest.get("heartbeat_at"))
            if hb_age is None or hb_age > stale_minutes:
                alerts.append(_alert("stalled_running", "critical", f"running heartbeat age={hb_age} minutes", latest))
            else:
                warnings.append(_alert("run_in_progress", "warning", f"run is active, heartbeat age={hb_age:.1f} minutes", latest))
        elif status == "failed":
            alerts.append(_alert("run_failed", "critical", f"latest run failed: {latest.get('reason')}", latest))
        elif status in ("success", "no_signal"):
            if status == "no_signal" and cleaned > 0:
                alerts.append(_alert("no_signal_with_input", "critical", "no_signal but cleaned_articles>0", latest))
            if cleaned > 0 and raw == 0:
                alerts.append(_alert("empty_event_pool", "critical", f"cleaned={cleaned} but events_raw=0", latest))
            if raw > 0 and scored == 0:
                alerts.append(_alert("empty_scored_pool", "critical", "events_raw>0 but events_scored=0", latest))
            if scored > 0 and analyzed < scored:
                alerts.append(
                    _alert("incomplete_analysis", "critical", f"scored={scored} but analyzed={analyzed}", latest)
                )
            finished_age = _age_hours(now, latest.get("finished_at"))
            if finished_age is None:
                alerts.append(_alert("missing_finished_at", "critical", "terminal manifest has no finished_at", latest))
            elif finished_age > max_age_hours:
                alerts.append(
                    _alert("stale_healthy_manifest", "critical", f"latest terminal run is {finished_age:.1f}h old", latest)
                )
        else:
            alerts.append(_alert("unknown_status", "critical", f"unknown status={status!r}", latest))

    healthy = [m for m in manifests if m.get("status") in ("success", "no_signal")]
    if healthy:
        last_healthy = max(healthy, key=lambda m: str(m.get("finished_at") or m.get("started_at") or ""))
        last_healthy_view = {
            "run_id": last_healthy.get("run_id"),
            "date": last_healthy.get("date"),
            "status": last_healthy.get("status"),
            "finished_at": last_healthy.get("finished_at"),
            "manifest": last_healthy.get("_path"),
        }
        healthy_age = _age_hours(now, last_healthy.get("finished_at"))
        if latest is not None and str(latest.get("status")) not in ("success", "no_signal"):
            if healthy_age is None or healthy_age > max_age_hours:
                alerts.append(
                    _alert(
                        "stale_healthy_manifest",
                        "critical",
                        f"no healthy terminal run within {max_age_hours:g}h (last healthy age={healthy_age})",
                        last_healthy,
                    )
                )
        elif latest is not None and latest.get("run_id") == last_healthy.get("run_id"):
            pass  # already checked above

    scheduler_path = Path(state_dir) / "scheduler" / "heartbeat.json"
    scheduler_view: Optional[Dict[str, Any]] = None
    if scheduler_path.exists():
        try:
            sched = run_state.read_json(scheduler_path)
            scheduler_view = {
                "updated_at": sched.get("updated_at"),
                "last_tick_at": sched.get("last_tick_at"),
                "next_run_at": sched.get("next_run_at"),
                "last_launch_at": sched.get("last_launch_at"),
                "last_launch_pid": sched.get("last_launch_pid"),
                "pid": sched.get("pid"),
            }
            age = _age_minutes(now, sched.get("updated_at"))
            if age is None or age > stale_minutes:
                alerts.append(_alert("scheduler_stalled", "critical", f"scheduler heartbeat age={age} minutes"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            alerts.append(_alert("scheduler_invalid", "critical", f"cannot read scheduler heartbeat: {exc}"))
    elif require_scheduler:
        alerts.append(_alert("scheduler_missing", "critical", "scheduler heartbeat is required but absent"))

    ok = not alerts
    return {
        "ok": ok,
        "status": "healthy" if ok else "critical",
        "checked_at": run_state.iso(now),
        "max_age_hours": max_age_hours,
        "stale_minutes": stale_minutes,
        "manifest_count": len(manifests),
        "latest": latest_view,
        "last_healthy": last_healthy_view,
        "scheduler": scheduler_view,
        "alerts": alerts,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2-A independent run health watchdog")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--now", default=None)
    parser.add_argument("--max-age-hours", type=float, default=48.0)
    parser.add_argument("--stale-minutes", type=float, default=120.0)
    parser.add_argument("--require-scheduler", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    now = run_state.parse_iso(args.now) if args.now else run_state.utc_now()
    result = evaluate(
        Path(args.state_dir),
        now=now,
        max_age_hours=args.max_age_hours,
        stale_minutes=args.stale_minutes,
        require_scheduler=args.require_scheduler,
    )
    if not args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
