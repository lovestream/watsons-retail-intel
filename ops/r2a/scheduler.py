#!/usr/bin/env python3
"""Deterministic daily scheduler for the R2-A launcher.

This module is intentionally not an agent task and not a QwenPaw cron prompt.
A supervisor (supervisord/systemd, outside the agent) keeps this process alive;
the process computes the next 07:00 Asia/Shanghai slot and starts
``launcher.py`` as a detached child.  The child owns the run manifest.

The scheduler is idempotent per date:
* an existing ``success``/``no_signal`` manifest for the date -> skip;
* an existing ``running`` manifest -> skip (avoid overlapping runs);
* an existing ``failed`` manifest -> skip unless ``--retry-failed``.

``--once`` is the testable tick primitive.  ``--loop`` is the production mode.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import run_state

DEFAULT_HOUR = 7
DEFAULT_MINUTE = 0
DEFAULT_WINDOW_MINUTES = 90


def next_run_at(now: datetime, hour: int = DEFAULT_HOUR, minute: int = DEFAULT_MINUTE) -> datetime:
    """Return the next scheduled slot in Asia/Shanghai, never in the past."""
    local = now.astimezone(run_state.CST)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local >= candidate:
        candidate += timedelta(days=1)
    return candidate


def _run_manifests(state_dir: Path, date: str) -> List[Dict[str, Any]]:
    manifests: List[Dict[str, Any]] = []
    root = Path(state_dir) / "runs" / date
    if not root.is_dir():
        return manifests
    for path in sorted(root.glob("*/manifest.json")):
        try:
            data = run_state.read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        data["_path"] = str(path)
        manifests.append(data)
    return manifests


def _latest(manifests: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not manifests:
        return None
    return max(manifests, key=lambda m: str(m.get("started_at") or ""))


def _skip_reason(manifests: List[Dict[str, Any]], retry_failed: bool) -> Optional[str]:
    latest = _latest(manifests)
    if latest is None:
        return None
    status = str(latest.get("status") or "")
    if status in ("success", "no_signal"):
        return f"already_{status}"
    if status == "running":
        return "already_running"
    if status == "failed" and not retry_failed:
        return "existing_failed_no_retry"
    return None


def _write_heartbeat(state_dir: Path, **fields: Any) -> None:
    path = Path(state_dir) / "scheduler" / "heartbeat.json"
    current: Dict[str, Any] = {"pid": os.getpid(), "updated_at": run_state.iso()}
    try:
        old = run_state.read_json(path)
        if isinstance(old, dict):
            current.update(old)
    except (OSError, ValueError):
        pass
    current.update(fields)
    current["updated_at"] = run_state.iso()
    run_state.atomic_write_json(path, current)


def launch_once(args: argparse.Namespace, now: Optional[datetime] = None) -> int:
    now = now or run_state.cst_now()
    date = args.date or now.astimezone(run_state.CST).date().isoformat()
    state_dir = Path(args.state_dir)
    manifests = _run_manifests(state_dir, date)
    skip = _skip_reason(manifests, args.retry_failed)

    next_slot = next_run_at(now, args.hour, args.minute)
    _write_heartbeat(
        state_dir,
        next_run_at=run_state.iso(next_slot),
        last_tick_at=run_state.iso(now),
        date=date,
    )

    if skip:
        print(json.dumps({"action": "skip", "reason": skip, "date": date}, ensure_ascii=False))
        return 0

    if not args.ignore_window:
        local = now.astimezone(run_state.CST)
        scheduled = local.replace(hour=args.hour, minute=args.minute, second=0, microsecond=0)
        delta_min = abs((local - scheduled).total_seconds()) / 60.0
        if delta_min > args.window_minutes:
            print(
                json.dumps(
                    {
                        "action": "skip",
                        "reason": "outside_schedule_window",
                        "date": date,
                        "minutes_from_slot": round(delta_min, 1),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

    cmd = [
        sys.executable,
        "-m",
        "ops.r2a.launcher",
        "--project-root",
        str(Path(args.project_root).resolve()),
        "--state-dir",
        str(state_dir.resolve()),
        "--date",
        date,
        "--mode",
        args.mode,
        "--start-step",
        args.start_step,
        "--end-step",
        args.end_step,
        "--pipeline-module",
        args.pipeline_module,
    ]
    if args.force:
        cmd.append("--force")

    if args.dry_run:
        print(json.dumps({"action": "would_launch", "date": date, "command": cmd}, ensure_ascii=False))
        return 0

    log_dir = state_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{date}.out"
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    with log_path.open("a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(args.project_root).resolve()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    _write_heartbeat(
        state_dir,
        last_launch_at=run_state.iso(),
        last_launch_pid=proc.pid,
        last_launch_date=date,
        last_launch_command=cmd,
    )
    print(
        json.dumps(
            {
                "action": "launched",
                "date": date,
                "pid": proc.pid,
                "log": str(log_path),
                "next_run_at": run_state.iso(next_slot),
            },
            ensure_ascii=False,
        )
    )
    return 0


def run_loop(args: argparse.Namespace) -> int:
    while True:
        now = run_state.cst_now()
        slot = now.astimezone(run_state.CST).replace(hour=args.hour, minute=args.minute, second=0, microsecond=0)
        if now >= slot and (now - slot).total_seconds() <= args.window_minutes * 60:
            launch_once(args, now=now)
        else:
            _write_heartbeat(Path(args.state_dir), next_run_at=run_state.iso(next_run_at(now, args.hour, args.minute)))
        time.sleep(max(30, args.poll_seconds))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2-A deterministic daily scheduler")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--hour", type=int, default=DEFAULT_HOUR)
    parser.add_argument("--minute", type=int, default=DEFAULT_MINUTE)
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--mode", default="scheduled", choices=("shadow", "scheduled"))
    parser.add_argument("--start-step", default="health_check")
    parser.add_argument("--end-step", default="analyze")
    parser.add_argument("--pipeline-module", default="skills.run_daily_pipeline.run_daily_pipeline")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ignore-window", action="store_true")
    parser.add_argument("--now", default=None, help="ISO timestamp for deterministic tests")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "scheduled" and args.start_step not in ("health_check", "collect"):
        raise SystemExit(
            "scheduled mode must start at health_check/collect; extract-only launches are not a full workflow"
        )
    now = run_state.parse_iso(args.now) if args.now else run_state.cst_now()
    if args.loop:
        return run_loop(args)
    return launch_once(args, now=now)


if __name__ == "__main__":
    raise SystemExit(main())
