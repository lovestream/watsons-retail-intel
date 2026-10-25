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
    }
    run_state.atomic_write_json(path, data)
    return path


class ClassifyTests(unittest.TestCase):
    def test_success(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": [{"name": n, "status": "success"} for n in launcher.CRITICAL_STEPS]},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 2},
        )
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["exit_code"], 0)

    def test_no_signal_empty_input_is_success_not_crash(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "no_signal": True, "steps": []},
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
            {"ok": True, "steps": [{"name": n, "status": "success"} for n in launcher.CRITICAL_STEPS]},
            {"cleaned_articles": 7, "events_raw": 0, "events_scored": 0, "events_analyzed": 0},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "empty_event_pool")

    def test_incomplete_analysis_is_failed(self) -> None:
        out = launcher.classify_outcome(
            {"ok": True, "steps": [{"name": n, "status": "success"} for n in launcher.CRITICAL_STEPS]},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 1},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "incomplete_analysis")

    def test_critical_step_failed(self) -> None:
        steps = [{"name": n, "status": "failed" if n == "score" else "success"} for n in launcher.CRITICAL_STEPS]
        out = launcher.classify_outcome(
            {"ok": True, "steps": steps},
            {"cleaned_articles": 1, "events_raw": 2, "events_scored": 2, "events_analyzed": 2},
        )
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "critical_step_failed")

    def test_exception(self) -> None:
        out = launcher.classify_outcome(None, {}, exception="RuntimeError: injected")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "pipeline_exception")


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

    def _run(self, td: str, mode: str) -> subprocess.CompletedProcess:
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


if __name__ == "__main__":
    unittest.main()
