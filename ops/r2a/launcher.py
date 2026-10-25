#!/usr/bin/env python3
"""Direct Python launcher for the daily pipeline.

The launcher is the executable boundary between a scheduler and the pipeline.
It writes a run manifest *before* invoking the pipeline, updates a heartbeat
while the work is running, and writes a terminal manifest with an explicit
status and exit code afterwards.

Invariants:
* It never sends email.  The pipeline call always receives ``send_email=False``.
* It runs the pipeline in-process; no QwenPaw agent is woken to "start" a job.
* A successful exit code is written only after stage artifacts are counted.
* A run holds a non-blocking per-date lock, so a scheduler retry cannot run two
  copies of the same date concurrently.

Statuses:
* ``success``   -- every critical stage succeeded and the event pool is complete.
* ``no_signal`` -- pipeline completed with no input/cleaned articles (not a crash).
* ``failed``    -- exception, non-zero pipeline status, missing step, empty or
  incomplete event pool, or a no-signal result with non-empty input.
"""
from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import run_state

CRITICAL_STEPS = ("extract", "temporal_fact_validation", "novelty_check", "score", "analyze")
TERMINAL_OK = ("success", "no_signal")


def _safe_error(exc: BaseException) -> str:
    """Keep diagnostics useful without dumping article or credential data."""
    text = f"{type(exc).__name__}: {exc}"
    # Credentials are never put in manifests, but avoid accidental long traces.
    return text.replace("\n", " ")[:800]


def _step_view(steps: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(steps, list):
        return out
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "")
        if not name:
            continue
        stats = step.get("stats") if isinstance(step.get("stats"), dict) else {}
        out[name] = {
            "status": str(step.get("status") or "unknown"),
            "duration_seconds": step.get("duration_seconds"),
            "error": str(step.get("error") or "")[:500],
            "stats": {
                k: stats.get(k)
                for k in (
                    "event_count",
                    "article_count",
                    "scored_count",
                    "analyzed_count",
                    "llm_success_count",
                    "llm_failed_count",
                    "llm_fallback_count",
                    "total_collected",
                    "source_total",
                    "source_success",
                    "source_failed",
                    "collector_count",
                    "collector_nonzero_count",
                    "collect_errors_count",
                )
                if k in stats
            },
        }
    return out


def _stat_int(stats: Dict[str, Any], key: str) -> Optional[int]:
    value = stats.get(key) if isinstance(stats, dict) else None
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _llm_health(steps: Dict[str, Dict[str, Any]], name: str) -> str:
    """Return ok/degraded/unknown for a step's real LLM usage.

    ``success`` on a step only means the step did not crash.  A rule-only
    fallback is operational, but it must not be reported as a full LLM success.
    """
    step = steps.get(name)
    if not step:
        return "not_run"
    success = _stat_int(step.get("stats", {}), "llm_success_count")
    if success is None:
        return "unknown"
    return "ok" if success > 0 else "degraded"


def _collection_health(steps: Dict[str, Dict[str, Any]]) -> str:
    """Classify whether collection itself succeeded.

    Legitimate empty days and an all-sources-down day both produce zero cleaned
    articles.  They are only distinguishable by the collection step's status,
    source counts and collector errors.
    """
    step = steps.get("collect")
    if not step:
        return "not_run"
    if step.get("status") not in ("success", "warning"):
        return "failed"
    stats = step.get("stats", {})
    collected = _stat_int(stats, "total_collected")
    errors = _stat_int(stats, "collect_errors_count") or 0
    source_total = _stat_int(stats, "source_total")
    source_success = _stat_int(stats, "source_success")
    source_failed = _stat_int(stats, "source_failed")
    if errors > 0 and (collected or 0) == 0:
        return "failed"
    if (
        source_total is not None
        and source_total > 0
        and (source_success or 0) == 0
        and (source_failed or 0) >= source_total
    ):
        # Every tried source failed.  Do not call this a quiet news day.
        return "no_source_success"
    return "ok"


def classify_outcome(
    result: Optional[Dict[str, Any]],
    counts: Dict[str, int],
    exception: Optional[str] = None,
    start_step: str = "health_check",
) -> Dict[str, Any]:
    """Classify a pipeline result into a truthful terminal status.

    This function is pure so fault-injection tests can exercise the exact
    production decision table without making network calls.
    """
    if exception:
        return {
            "status": "failed",
            "reason": "pipeline_exception",
            "errors": [exception],
            "exit_code": 2,
        }

    result = result if isinstance(result, dict) else {}
    pipeline_ok = result.get("ok") is True
    no_signal_flag = result.get("no_signal") is True
    steps = _step_view(result.get("steps"))

    cleaned = int(counts.get("cleaned_articles", 0) or 0)

    if not pipeline_ok:
        # A collection failure that leaves no articles is more specific than a
        # generic pipeline error; keep the operational cause visible.
        if no_signal_flag and cleaned == 0:
            return {
                "status": "failed",
                "reason": "no_signal_without_verified_collection",
                "errors": ["pipeline returned ok=false with no signal and zero articles"],
                "exit_code": 2,
                "pipeline_completed": False,
                "content_ready": False,
                "collection_health": _collection_health(steps),
                "llm_extract_health": _llm_health(steps, "extract"),
                "llm_analyze_health": _llm_health(steps, "analyze"),
                "analysis_mode": "not_run",
            }
        return {
            "status": "failed",
            "reason": "pipeline_not_ok",
            "errors": ["pipeline returned ok=false"],
            "exit_code": 2,
        }
    raw = int(counts.get("events_raw", 0) or 0)
    scored = int(counts.get("events_scored", 0) or 0)
    analyzed = int(counts.get("events_analyzed", 0) or 0)

    if start_step == "health_check":
        # A scheduled full workflow must prove collection happened; starting at
        # health_check while skipping collect is still an extract-only run.
        required_critical = ["collect"] + list(CRITICAL_STEPS)
    elif start_step == "collect":
        required_critical = list(CRITICAL_STEPS)
    elif start_step in CRITICAL_STEPS:
        required_critical = list(CRITICAL_STEPS[CRITICAL_STEPS.index(start_step):])
    else:
        required_critical = list(CRITICAL_STEPS)

    missing_steps = [name for name in required_critical if name not in steps]
    bad_steps = [
        name
        for name in required_critical
        if name in steps and steps[name].get("status") not in ("success", "warning", "skipped")
    ]

    collection_health = _collection_health(steps)
    llm_extract_health = _llm_health(steps, "extract")
    llm_analyze_health = _llm_health(steps, "analyze")

    if no_signal_flag:
        if cleaned == 0:
            if collection_health == "ok":
                return {
                    "status": "no_signal",
                    "reason": "no_input_articles_after_successful_collection",
                    "errors": [],
                    "exit_code": 0,
                    "pipeline_completed": True,
                    "content_ready": False,
                    "collection_health": collection_health,
                    "llm_extract_health": llm_extract_health,
                    "llm_analyze_health": llm_analyze_health,
                    "analysis_mode": "not_run",
                }
            # Zero articles without a verified collection run is not a quiet
            # news day; it is an unverified/failed collection result.
            return {
                "status": "failed",
                "reason": "no_signal_without_verified_collection",
                "errors": [
                    f"no_signal with cleaned_articles=0 but collection_health={collection_health}"
                ],
                "exit_code": 2,
                "pipeline_completed": bool(pipeline_ok),
                "content_ready": False,
                "collection_health": collection_health,
                "llm_extract_health": llm_extract_health,
                "llm_analyze_health": llm_analyze_health,
                "analysis_mode": "not_run",
            }
        return {
            "status": "failed",
            "reason": "no_signal_with_nonempty_input",
            "errors": [f"no_signal reported but cleaned_articles={cleaned}"],
            "exit_code": 2,
            "pipeline_completed": bool(pipeline_ok),
            "content_ready": False,
            "collection_health": collection_health,
            "llm_extract_health": llm_extract_health,
            "llm_analyze_health": llm_analyze_health,
            "analysis_mode": "not_run",
        }

    if missing_steps:
        return {
            "status": "failed",
            "reason": "missing_critical_step",
            "errors": [f"missing steps: {','.join(missing_steps)}"],
            "exit_code": 2,
        }
    if bad_steps:
        return {
            "status": "failed",
            "reason": "critical_step_failed",
            "errors": [f"non-success steps: {','.join(bad_steps)}"],
            "exit_code": 2,
        }
    if cleaned > 0 and raw == 0:
        return {
            "status": "failed",
            "reason": "empty_event_pool",
            "errors": [f"cleaned_articles={cleaned} but events_raw=0"],
            "exit_code": 2,
        }
    if scored == 0:
        return {
            "status": "failed",
            "reason": "empty_scored_pool",
            "errors": ["events_scored=0"],
            "exit_code": 2,
        }
    if analyzed < scored:
        return {
            "status": "failed",
            "reason": "incomplete_analysis",
            "errors": [f"events_scored={scored} but events_analyzed={analyzed}"],
            "exit_code": 2,
        }
    if analyzed == 0:
        return {
            "status": "failed",
            "reason": "empty_analyzed_pool",
            "errors": ["events_analyzed=0"],
            "exit_code": 2,
        }

    analysis_mode = "llm" if llm_analyze_health == "ok" else (
        "rule_fallback_or_degraded" if llm_analyze_health == "degraded" else "unknown"
    )
    reason = "complete_event_pool"
    if analysis_mode != "llm":
        reason = "complete_event_pool_degraded_analysis"
    return {
        "status": "success",
        "reason": reason,
        "errors": [],
        "exit_code": 0,
        "pipeline_completed": True,
        "content_ready": analyzed > 0,
        "collection_health": collection_health,
        "llm_extract_health": llm_extract_health,
        "llm_analyze_health": llm_analyze_health,
        "analysis_mode": analysis_mode,
        "degraded_analysis": analysis_mode != "llm",
    }


class ManifestWriter:
    """Thread-safe manifest updates for the heartbeat and final result."""

    def __init__(self, path: Path, initial: Dict[str, Any], heartbeat_seconds: int = 15):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = dict(initial)
        self._heartbeat_seconds = max(1, int(heartbeat_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        run_state.atomic_write_json(self.path, self._data)

    def update(self, **fields: Any) -> None:
        with self._lock:
            self._data.update(fields)
            self._data["updated_at"] = run_state.iso()
            run_state.atomic_write_json(self.path, self._data)

    def start_heartbeat(self) -> None:
        def beat() -> None:
            while not self._stop.wait(self._heartbeat_seconds):
                try:
                    self.update(heartbeat_at=run_state.iso())
                except OSError:
                    # A transient filesystem error must not abort the pipeline.
                    continue

        self._thread = threading.Thread(target=beat, name="r2a-heartbeat", daemon=True)
        self._thread.start()

    def finish(self, **fields: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._lock:
            self._data.update(fields)
            self._data["finished_at"] = run_state.iso()
            self._data["heartbeat_at"] = run_state.iso()
            self._data["updated_at"] = run_state.iso()
            run_state.atomic_write_json(self.path, self._data)


def _acquire_date_lock(state_dir: Path, date: str):
    lock_dir = Path(state_dir) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{date}.lock"
    fh = lock_path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()} started={run_state.iso()}\n")
    fh.flush()
    return fh


def _load_pipeline(module_name: str) -> Callable[..., Dict[str, Any]]:
    module = importlib.import_module(module_name)
    fn = getattr(module, "run_daily_pipeline", None)
    if not callable(fn):
        raise AttributeError(f"{module_name} has no callable run_daily_pipeline")
    return fn


def execute(args: argparse.Namespace) -> int:
    date = args.date or run_state.cst_now().date().isoformat()
    state_dir = Path(args.state_dir)
    run_id = args.run_id or f"{date}T{run_state.cst_now().strftime('%H%M%S')}-{os.getpid()}"
    run_dir = state_dir / "runs" / date / run_id
    manifest_path = run_dir / "manifest.json"
    run_dir.mkdir(parents=True, exist_ok=True)

    lock = _acquire_date_lock(state_dir, date)
    if lock is None:
        initial = {
            "schema_version": run_state.SCHEMA_VERSION,
            "run_id": run_id,
            "date": date,
            "mode": args.mode,
            "status": "failed",
            "reason": "already_running",
            "pid": os.getpid(),
            "started_at": run_state.iso(),
            "finished_at": run_state.iso(),
            "heartbeat_at": run_state.iso(),
            "exit_code": 4,
            "errors": [f"date lock is held for {date}"],
            "send_email": False,
        }
        run_state.atomic_write_json(manifest_path, initial)
        print(json.dumps({"status": "failed", "reason": "already_running", "manifest": str(manifest_path)}))
        return 4

    initial = {
        "schema_version": run_state.SCHEMA_VERSION,
        "run_id": run_id,
        "date": date,
        "mode": args.mode,
        "status": "running",
        "reason": "started",
        "pid": os.getpid(),
        "started_at": run_state.iso(),
        "finished_at": None,
        "heartbeat_at": run_state.iso(),
        "updated_at": run_state.iso(),
        "exit_code": None,
        "pipeline_ok": None,
        "sendable": None,
        "no_signal": None,
        "send_email": False,
        "project_root": str(Path(args.project_root).resolve()),
        "start_step": args.start_step,
        "end_step": args.end_step,
        "force": bool(args.force),
        "pipeline_module": args.pipeline_module,
        "counts": {},
        "reportable_count": 0,
        "steps": {},
        "errors": [],
    }
    writer = ManifestWriter(manifest_path, initial, args.heartbeat_seconds)
    writer.start_heartbeat()

    result: Optional[Dict[str, Any]] = None
    exception_text: Optional[str] = None
    try:
        counts_before = run_state.event_counts(Path(args.project_root), date)
        writer.update(counts=counts_before)
        pipeline = _load_pipeline(args.pipeline_module)
        # Keep the pipeline call explicit: no recipient, no email, no agent.
        result = pipeline(
            project_root=str(Path(args.project_root).resolve()),
            date=date,
            start_step=args.start_step,
            end_step=args.end_step,
            use_llm=True,
            send_email=False,
            recipient=None,
            resume=not bool(args.force),
            force=bool(args.force),
            dry_run=False,
            continue_on_error=False,
        )
    except BaseException as exc:  # noqa: BLE001 - terminal manifest must cover all failures
        exception_text = _safe_error(exc)
        traceback.print_exc(file=sys.stderr)
        result = None
    finally:
        try:
            lock.close()
        except OSError:
            pass

    counts = run_state.event_counts(Path(args.project_root), date)
    outcome = classify_outcome(
        result, counts, exception=exception_text, start_step=args.start_step
    )
    step_view = _step_view((result or {}).get("steps"))
    reportable = run_state.reportable_count(
        Path(args.project_root) / "data" / "events" / date / "events_scored.json"
    )
    errors = list(outcome.get("errors") or [])
    if exception_text:
        errors.insert(0, exception_text)
    writer.finish(
        status=outcome["status"],
        reason=outcome["reason"],
        exit_code=outcome["exit_code"],
        pipeline_ok=(result or {}).get("ok") if result else False,
        sendable=(result or {}).get("sendable") if result else False,
        no_signal=(result or {}).get("no_signal") if result else None,
        counts=counts,
        reportable_count=reportable,
        steps=step_view,
        errors=errors,
        pipeline_completed=outcome.get("pipeline_completed"),
        content_ready=outcome.get("content_ready"),
        collection_health=outcome.get("collection_health", "unknown"),
        llm_extract_health=outcome.get("llm_extract_health", "unknown"),
        llm_analyze_health=outcome.get("llm_analyze_health", "unknown"),
        analysis_mode=outcome.get("analysis_mode", "unknown"),
        degraded_analysis=bool(outcome.get("degraded_analysis", False)),
    )
    print(
        json.dumps(
            {
                "status": outcome["status"],
                "reason": outcome["reason"],
                "exit_code": outcome["exit_code"],
                "manifest": str(manifest_path),
                "counts": counts,
                "reportable_count": reportable,
            },
            ensure_ascii=False,
        )
    )
    return int(outcome["exit_code"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="R2-A direct daily pipeline launcher")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--mode", default="shadow", choices=("shadow", "scheduled"))
    parser.add_argument("--start-step", default="health_check")
    parser.add_argument("--end-step", default="analyze")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pipeline-module", default="skills.run_daily_pipeline.run_daily_pipeline")
    parser.add_argument("--heartbeat-seconds", type=int, default=15)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "shadow" and args.end_step not in ("analyze", "score", "novelty_check", "temporal_fact_validation"):
        raise SystemExit("shadow mode must end at or before analyze")
    if args.mode == "scheduled" and args.start_step not in ("health_check", "collect"):
        raise SystemExit(
            "scheduled mode must start at health_check/collect, not at a pre-existing extract pool"
        )
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
