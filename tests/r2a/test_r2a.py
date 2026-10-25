from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from ops.r2a import launcher, run_state, scheduler, watchdog

REPO = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def manifest(
    state: Path,
    date: str,
    run_id: str,
    status: str,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    heartbeat_at: datetime | None = None,
    cleaned: int = 1,
    raw: int = 2,
    scored: int = 2,
    analyzed: int = 2,
    reportable: int = 2,
    reason: str = "test",
    errors: list | None = None,
    collection_health: str = "ok",
    content_ready: bool | None = None,
    llm_extract_health: str = "ok",
    llm_analyze_health: str = "ok",
    analysis_mode: str = "llm",
    degraded_analysis: bool = False,
) -> Path:
    now = datetime.now(UTC)
    started_at = started_at or now
    finished_at = finished_at if finished_at is not None else (now if status in ("success", "no_signal", "failed") else None)
    heartbeat_at = heartbeat_at or (finished_at or started_at)
    path = state / "runs" / date / run_id / "manifest.json"
    data = {
        "schema_version": 1,
        "run_id": run_id,
        "date": date,
        "status": status,
        "reason": reason,
        "started_at": run_state.iso(started_at),
        "finished_at": run_state.iso(finished_at) if finished_at else None,
        "heartbeat_at": run_state.iso(heartbeat_at),
        "exit_code": 0 if status in ("success", "no_signal") else (2 if status == "failed" else None),
        "pipeline_ok": status in ("success", "no_signal"),
        "sendable": False,
        "send_email": False,
        "counts": {
            "cleaned_articles": cleaned,
            "events_raw": raw,
            "events_temporal": raw,
            "events_scored": scored,
            "events_analyzed": analyzed,
        },
        "reportable_count": reportable,
        "errors": errors or [],
        "pipeline_completed": status in ("success", "no_signal"),
        "content_ready": (status == "success") if content_ready is None else content_ready,
        "collection_health": collection_health,
        "llm_extract_health": llm_extract_health,
        "llm_analyze_health": llm_analyze_health,
        "analysis_mode": analysis_mode,
        "degraded_analysis": degraded_analysis,
    }
    run_state.atomic_write_json(path, data)
    return path


def _scheduled_steps(
    collect_status: str = "success",
    *,
    completed: bool = True,
    collection_date: str = "2026-10-25",
    source_total: int = 2,
    source_success: int = 1,
    source_failed: int = 0,
    total_collected: int = 1,
    collect_errors_count: int = 0,
) -> list:
    steps = _workflow_steps()
    steps[0] = {
        "name": "collect",
        "status": collect_status,
        "stats": {
            "total_collected": total_collected,
            "collection_completed": completed,
            "collection_date": collection_date,
            "source_total": source_total,
            "source_success": source_success,
            "source_failed": source_failed,
            "collect_errors_count": collect_errors_count,
        },
    }
    return steps


def _workflow_steps(failed: str | None = None) -> list:
    return [{
        "name": "collect",
        "status": "success",
        "stats": {
            "total_collected": 1,
            "source_total": 2,
            "source_success": 1,
            "source_failed": 0,
            "collect_errors_count": 0,
        },
    }] + [
        {"name": n, "status": "failed" if n == failed else "success", "stats": {"llm_success_count": 2} if n == "analyze" else {}}
        for n in launcher.CRITICAL_STEPS
    ]


class ClassifyTests(unittest.TestCase):
    def test_success(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _workflow_steps()},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 2},
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["exit_code"], 0)

    def test_no_signal_empty_input_is_success_not_crash(self) -> None:
        out = launcher.classify_outcome(
            {
                "ok": True,
                "no_signal": True,
                "steps": [{
                    "name": "collect",
                    "status": "success",
                    "stats": {
                        "total_collected": 0,
                        "source_total": 2,
                        "source_success": 0,
                        "source_failed": 0,
                        "collect_errors_count": 0,
                    },
                }],
            },
            {"cleaned_articles": 0, "events_raw": 0, "events_scored": 0, "events_analyzed": 0},
        )
        self.assertEqual(out["status"], "no_signal")
        self.assertEqual(out["exit_code"], 0)

    def test_no_signal_with_input_is_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "no_signal": True, "steps": []},
            {"cleaned_articles": 1, "events_raw": 0, "events_scored": 0, "events_analyzed": 0},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "no_signal_with_nonempty_input")

    def test_empty_event_pool_is_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _workflow_steps()},
            {"cleaned_articles": 7, "events_raw": 0, "events_scored": 0, "events_analyzed": 0},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "empty_event_pool")

    def test_incomplete_analysis_is_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _workflow_steps()},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 1},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "incomplete_analysis")

    def test_critical_step_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _workflow_steps(failed="score")},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 2},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "critical_step_failed")

    def test_exception(self) -> None:
        out = launcher.classify_outcome(None, {}, exception="RuntimeError: injected")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "pipeline_exception")

    def test_scheduled_requires_owned_collection_even_with_cached_pool(self) -> None:
        # Old cleaned/events files make the downstream counts look healthy.
        # The collect step was skipped, so this run proved nothing about today.
        out = launcher.classify_outcome(
            {"ok": True, "steps": _scheduled_steps("skipped")},
            {"cleaned_articles": 12, "events_raw": 8, "events_scored": 8, "events_analyzed": 8},
            mode="scheduled",
            expected_date="2026-10-25",
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "collection_not_verified")
        self.assertEqual(out["collection_health"], "skipped")
        self.assertEqual(out["exit_code"], 2)

    def test_scheduled_collect_failure_with_old_events_is_not_success(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _scheduled_steps("failed")},
            {"cleaned_articles": 12, "events_raw": 8, "events_scored": 8, "events_analyzed": 8},
            mode="scheduled",
            expected_date="2026-10-25",
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "collection_not_verified")
        self.assertEqual(out["exit_code"], 2)

    def test_scheduled_no_checkable_sources_is_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _scheduled_steps(source_total=0, source_success=0, source_failed=0)},
            {"cleaned_articles": 0, "events_raw": 0, "events_scored": 0, "events_analyzed": 0},
            mode="scheduled",
            expected_date="2026-10-25",
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "collection_not_verified")
        self.assertEqual(out["collection_health"], "no_sources")
        self.assertEqual(out["exit_code"], 2)

    def test_scheduled_collection_window_mismatch_is_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": _scheduled_steps(collection_date="2026-10-24")},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 2},
            mode="scheduled",
            expected_date="2026-10-25",
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "collection_not_verified")
        self.assertIn("collection_date_mismatch", out["errors"][0])

    def test_scheduled_verified_empty_day_is_no_signal(self) -> None:
        out = launcher.classify_outcome(
            {
                "ok": True,
                "no_signal": True,
                "steps": _scheduled_steps(
                    total_collected=0, source_success=0, source_failed=0
                ),
            },
            {"cleaned_articles": 0, "events_raw": 0, "events_scored": 0, "events_analyzed": 0},
            mode="scheduled",
            expected_date="2026-10-25",
        )
        self.assertEqual(out["status"], "no_signal")
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["collection_proof"], "verified")

    def test_llm_health_uses_failure_denominator(self) -> None:
        steps = _workflow_steps()
        for step in steps:
            if step["name"] == "analyze":
                step["stats"] = {"llm_success_count": 1, "llm_failed_count": 49}
        out = launcher.classify_outcome(
            {"ok": True, "steps": steps},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 2},
        )
        self.assertEqual(out["llm_analyze_health"], "degraded")
        self.assertEqual(out["llm_analyze_attempts"], 50)
        self.assertEqual(out["llm_analyze_success_ratio"], 0.02)


class WatchdogTests(unittest.TestCase):
    def test_missing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result = watchdog.evaluate(Path(td), now=datetime.now(UTC))
            self.assertFalse(result["ok"])
            self.assertEqual(result["alerts"][0]["code"], "missing_manifest")

    def test_agent_text_is_not_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "agent_reply.txt").write_text("已启动", encoding="utf-8")
            result = watchdog.evaluate(state, now=datetime.now(UTC))
            self.assertFalse(result["ok"])
            self.assertEqual(result["alerts"][0]["code"], "missing_manifest")

    def test_healthy_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(state, "2026-10-25", "r1", "success", finished_at=now - timedelta(hours=1))
            result = watchdog.evaluate(state, now=now)
            self.assertTrue(result["ok"], result["alerts"])
            self.assertEqual(result["latest"]["status"], "success")

    def test_stale_healthy_48h(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(state, "2026-10-20", "r1", "success", finished_at=now - timedelta(hours=49))
            result = watchdog.evaluate(state, now=now)
            self.assertFalse(result["ok"])
            self.assertIn("stale_healthy_manifest", [a["code"] for a in result["alerts"]])

    def test_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(state, "2026-10-25", "r1", "failed", finished_at=now - timedelta(minutes=5), reason="pipeline_exception")
            result = watchdog.evaluate(state, now=now)
            self.assertFalse(result["ok"])
            self.assertIn("run_failed", [a["code"] for a in result["alerts"]])

    def test_stalled_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(
                state,
                "2026-10-25",
                "r1",
                "running",
                started_at=now - timedelta(hours=3),
                heartbeat_at=now - timedelta(hours=3),
            )
            result = watchdog.evaluate(state, now=now, stale_minutes=120)
            self.assertFalse(result["ok"])
            self.assertIn("stalled_running", [a["code"] for a in result["alerts"]])

    def test_incomplete_event_pool(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(state, "2026-10-25", "r1", "success", finished_at=now - timedelta(hours=1), scored=4, analyzed=2)
            result = watchdog.evaluate(state, now=now)
            self.assertFalse(result["ok"])
            self.assertIn("incomplete_analysis", [a["code"] for a in result["alerts"]])

    def test_no_signal_empty_input_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(
                state,
                "2026-10-25",
                "r1",
                "no_signal",
                finished_at=now - timedelta(hours=1),
                cleaned=0,
                raw=0,
                scored=0,
                analyzed=0,
                reportable=0,
                content_ready=False,
            )
            result = watchdog.evaluate(state, now=now)
            self.assertTrue(result["ok"], result["alerts"])

    def test_scheduler_required_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            now = datetime.now(UTC)
            manifest(state, "2026-10-25", "r1", "success", finished_at=now - timedelta(hours=1))
            result = watchdog.evaluate(state, now=now, require_scheduler=True)
            self.assertFalse(result["ok"])
            self.assertIn("scheduler_missing", [a["code"] for a in result["alerts"]])


class SchedulerTests(unittest.TestCase):
    def test_next_run_same_day_before_slot(self) -> None:
        now = datetime(2026, 10, 25, 6, 30, tzinfo=run_state.CST)
        nxt = scheduler.next_run_at(now, 7, 0)
        self.assertEqual(nxt, datetime(2026, 10, 25, 7, 0, tzinfo=run_state.CST))

    def test_next_run_next_day_at_slot(self) -> None:
        now = datetime(2026, 10, 25, 7, 0, tzinfo=run_state.CST)
        nxt = scheduler.next_run_at(now, 7, 0)
        self.assertEqual(nxt, datetime(2026, 10, 26, 7, 0, tzinfo=run_state.CST))

    def test_skip_existing_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            manifest(state, "2026-10-25", "r1", "success")
            self.assertEqual(scheduler._skip_reason(scheduler._run_manifests(state, "2026-10-25"), False), "already_success")

    def test_skip_existing_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            manifest(state, "2026-10-25", "r1", "running", started_at=datetime.now(UTC), heartbeat_at=datetime.now(UTC))
            self.assertEqual(scheduler._skip_reason(scheduler._run_manifests(state, "2026-10-25"), False), "already_running")

    def test_retry_failed_requires_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            manifest(state, "2026-10-25", "r1", "failed")
            items = scheduler._run_manifests(state, "2026-10-25")
            self.assertEqual(scheduler._skip_reason(items, False), "existing_failed_no_retry")
            self.assertIsNone(scheduler._skip_reason(items, True))


class LauncherSubprocessTests(unittest.TestCase):
    """Exercise the real launcher process and its exit codes."""

    def _run(self, td: str, mode: str, launcher_mode: str = "shadow") -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["R2A_FAKE_MODE"] = mode
        env["PYTHONPATH"] = str(REPO) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "ops.r2a.launcher",
                "--project-root",
                td,
                "--state-dir",
                str(Path(td) / "state"),
                "--date",
                "2026-10-25",
                "--run-id",
                f"run-{mode}",
                "--mode",
                launcher_mode,
                "--pipeline-module",
                "tests.r2a.fake_pipeline",
                "--force",
            ],
            cwd=str(REPO),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    def test_success_exit_zero_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "success")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-success" / "manifest.json").read_text())
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["exit_code"], 0)
            self.assertFalse(data["send_email"])
            self.assertTrue(data["finished_at"])

    def test_empty_pool_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "empty")
            self.assertEqual(proc.returncode, 2, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-empty" / "manifest.json").read_text())
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["reason"], "empty_event_pool")
            self.assertEqual(data["exit_code"], 2)

    def test_exception_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "exception")
            self.assertEqual(proc.returncode, 2, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-exception" / "manifest.json").read_text())
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["reason"], "pipeline_exception")

    def test_no_signal_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "no_signal")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-no_signal" / "manifest.json").read_text())
            self.assertEqual(data["status"], "no_signal")

    def test_collection_failure_is_not_a_quiet_day(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "collect_failure")
            self.assertEqual(proc.returncode, 2, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-collect_failure" / "manifest.json").read_text())
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["reason"], "no_signal_without_verified_collection")

    def test_scheduled_full_run_records_collection_proof(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "success", "scheduled")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-success" / "manifest.json").read_text())
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["collection_proof"], "verified")

    def test_scheduled_collection_failure_with_cached_pool_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            # The fake writes a cached article pool, but collect failed.  The
            # scheduled launcher must not report success or no_signal.
            proc = self._run(td, "collect_failure", "scheduled")
            self.assertEqual(proc.returncode, 2, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-collect_failure" / "manifest.json").read_text())
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["reason"], "no_signal_without_verified_collection")
            self.assertEqual(data["collection_health"], "failed")

    def test_degraded_analysis_is_success_but_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proc = self._run(td, "llm_degraded")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads((Path(td) / "state" / "runs" / "2026-10-25" / "run-llm_degraded" / "manifest.json").read_text())
            self.assertEqual(data["status"], "success")
            self.assertTrue(data["degraded_analysis"])
            self.assertEqual(data["analysis_mode"], "rule_fallback_or_degraded")


if __name__ == "__main__":
    unittest.main()
