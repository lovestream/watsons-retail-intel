from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ops.r2a import alerts, launcher, run_state, scheduler, watchdog
from skills import report_policy
from skills.score_events import score_events

UTC = timezone.utc


def event(**overrides):
    base = {
        "event_id": "E1",
        "event_title": "美团闪购美妆品类即时零售增长",
        "fact": "美团闪购宣布美妆个护品类即时零售订单同比增长。",
        "evidence_text": "美团闪购官方发布，美妆个护品类即时零售订单增长。",
        "source_url": "https://example.com/a",
        "source_title": "美团闪购美妆即时零售",
        "priority": "P2",
        "report_eligibility": "core",
        "temporal_eligibility": "core",
        "fact_freshness_status": "new_fact",
        "novelty_status": "new_today",
        "verification_status": "verified",
        "needs_verification": False,
        "event_announced_at": "2026-10-10T09:00:00+08:00",
        "event_time_source": "announced",
    }
    base.update(overrides)
    return base


class PolicyTests(unittest.TestCase):
    def test_announced_date_is_verified_without_faking_occurred(self) -> None:
        ev = event()
        result = report_policy.evaluate_verification(ev, as_of_date="2026-10-11")
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["verification_date_source"], "announced")
        self.assertEqual(result["verification_event_date"], "2026-10-10")
        self.assertNotIn("event_occurred_at", result)

    def test_missing_event_date_needs_verification(self) -> None:
        ev = event(event_announced_at="", event_time_source="missing")
        result = report_policy.evaluate_verification(ev, as_of_date="2026-10-11")
        self.assertFalse(result["verified"])
        self.assertIn("event_date_missing", result["verification_reasons"])

    def test_inferred_date_after_window_is_date_conflict(self) -> None:
        ev = event(
            event_occurred_at="2026-10-25T10:00:00+08:00",
            event_announced_at="",
            event_time_source="inferred_content_date",
        )
        result = report_policy.evaluate_verification(ev, as_of_date="2026-10-11")
        self.assertFalse(result["verified"])
        self.assertTrue(result["date_conflict"])
        self.assertIn("event_date_inferred_from_content", result["verification_reasons"])

    def test_future_occurred_date_is_not_hidden_by_announced_anchor(self) -> None:
        ev = event(
            event_occurred_at="2026-10-25T10:00:00+08:00",
            event_announced_at="2026-07-15T09:00:00+08:00",
            event_time_source="announced",
            fact_freshness_status="new_fact",
        )
        result = report_policy.evaluate_verification(ev, as_of_date="2026-10-11")
        self.assertFalse(result["verified"])
        self.assertTrue(result["date_conflict"])

    def test_very_old_announcement_is_a_recap(self) -> None:
        ev = event(
            event_announced_at="2025-10-22T09:00:00+08:00",
            event_time_source="announced",
            fact_freshness_status="updated_fact",
        )
        result = report_policy.evaluate_verification(ev, as_of_date="2026-10-11")
        self.assertFalse(result["verified"])
        self.assertIn("event_date_before_window:announced:2025-10-22<2026-10-11", result["verification_reasons"])

    def test_hearsay_needs_verification(self) -> None:
        ev = event(fact="据知情人士透露，某平台或将进入即时零售。")
        result = report_policy.evaluate_verification(ev, as_of_date="2026-10-11")
        self.assertFalse(result["verified"])
        self.assertTrue(result["hearsay_markers"])

    def test_eligibility_never_promoted(self) -> None:
        ev = event(verification_status="verified")
        self.assertEqual(report_policy.cap_eligibility(ev, "core"), "core")
        ev_bad = event(verification_status="needs_verification", needs_verification=True)
        self.assertEqual(report_policy.cap_eligibility(ev_bad, "core"), "review")
        ev_ref = event(temporal_eligibility="reference")
        self.assertEqual(report_policy.cap_eligibility(ev_ref, "core"), "reference")

    def test_reportable_requires_core_verified_new_evidence(self) -> None:
        ev = event()
        self.assertTrue(report_policy.is_reportable_event(ev))
        self.assertFalse(report_policy.is_reportable_event(event(report_eligibility="reference")))
        self.assertFalse(report_policy.is_reportable_event(event(novelty_status="repeated")))
        self.assertFalse(report_policy.is_reportable_event(event(verification_status="needs_verification")))
        self.assertFalse(report_policy.is_reportable_event(event(evidence_text="")))
        self.assertTrue(
            report_policy.is_reportable_event(event(novelty_status="updated_today", fact="更新后的新事实"))
        )

    def test_reportable_count_does_not_count_reference_or_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.json"
            path.write_text(
                json.dumps({"events": [
                    event(event_id="new"),
                    event(event_id="ref", report_eligibility="reference"),
                    event(event_id="rep", novelty_status="repeated"),
                    event(event_id="needs", verification_status="needs_verification"),
                ]}),
                encoding="utf-8",
            )
            self.assertEqual(run_state.reportable_count(path), 1)


class DomainGateTests(unittest.TestCase):
    def test_passenger_vehicle_and_apparel_are_archived(self) -> None:
        auto = event(
            event_title="乘联分会：9月乘用车零售同比增长",
            fact="乘联分会发布乘用车零售数据。",
            evidence_text="乘用车零售数据发布。",
        )
        apparel = event(
            event_title="波司登关闭多家门店",
            fact="波司登调整线下门店。",
            evidence_text="波司登门店调整。",
        )
        foreign = event(
            event_title="英国零售商Boots再度易主",
            fact="Boots在英国市场更换股东。",
            evidence_text="Boots UK ownership change.",
        )
        for ev in (auto, apparel, foreign):
            priority, reasons = score_events.apply_hard_downgrade(ev, {}, "P2", [])
            self.assertEqual(priority, "ARCHIVE")
            self.assertTrue(any(("域外垂直" in r or "港台海外" in r) for r in reasons), reasons)

    def test_true_instant_retail_beauty_is_not_false_positive(self) -> None:
        for text in (
            "美团闪购美妆个护品类即时零售增长",
            "屈臣氏联合京东秒送拓展美妆即时零售",
            "乘用车车主在美团闪购购买汽车用品，即时零售订单增长",
        ):
            ev = event(event_title=text, fact=text, evidence_text=text)
            out, _ = report_policy.is_out_of_domain_event(ev)
            self.assertFalse(out, text)


class AlertSinkTests(unittest.TestCase):
    def _manifest(self, state: Path, status: str, **extra) -> Path:
        now = run_state.iso(datetime.now(UTC))
        path = state / "runs" / "2026-10-25" / "r1" / "manifest.json"
        data = {
            "schema_version": 1,
            "run_id": "r1",
            "date": "2026-10-25",
            "status": status,
            "reason": extra.get("reason", "test"),
            "started_at": now,
            "finished_at": now,
            "heartbeat_at": now,
            "exit_code": 0 if status in ("success", "no_signal") else 1,
            "pipeline_ok": status in ("success", "no_signal"),
            "counts": {
                "cleaned_articles": extra.get("cleaned", 1),
                "events_raw": extra.get("raw", 2),
                "events_temporal": extra.get("raw", 2),
                "events_scored": extra.get("scored", 2),
                "events_analyzed": extra.get("analyzed", 2),
            },
            "reportable_count": 1,
            "pipeline_completed": status in ("success", "no_signal"),
            "content_ready": extra.get("content_ready", True),
            "collection_health": extra.get("collection_health", "ok"),
            "llm_extract_health": extra.get("llm_extract_health", "ok"),
            "llm_analyze_health": extra.get("llm_analyze_health", "ok"),
            "analysis_mode": "llm",
            "degraded_analysis": extra.get("degraded_analysis", False),
        }
        run_state.atomic_write_json(path, data)
        return path

    def _events(self, state: Path) -> list:
        p = state / "alerts" / "events.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_alert_exit_code_and_sink_dedup_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._manifest(state, "failed", reason="pipeline_exception", content_ready=False)
            code = watchdog.main(["--state-dir", td, "--quiet"])
            self.assertEqual(code, 1)
            events = self._events(state)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event"], "alert")
            status = json.loads((state / "watchdog" / "status.json").read_text())
            self.assertEqual(status["exit_code"], 1)
            current = json.loads((state / "alerts" / "current.json").read_text())
            self.assertEqual(current["active"][0]["code"], "run_failed")

            # Same broken manifest: no duplicate active alert.
            self.assertEqual(watchdog.main(["--state-dir", td, "--quiet"]), 1)
            self.assertEqual(len(self._events(state)), 1)

            # Recovery is emitted and the active list clears.
            self._manifest(state, "success", content_ready=True)
            self.assertEqual(watchdog.main(["--state-dir", td, "--quiet"]), 0)
            events = self._events(state)
            self.assertEqual(events[-1]["event"], "recovered")
            current = json.loads((state / "alerts" / "current.json").read_text())
            self.assertEqual(current["active"], [])

    def test_no_signal_without_collection_is_critical(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._manifest(
                state, "no_signal", cleaned=0, raw=0, scored=0, analyzed=0,
                collection_health="failed", content_ready=False,
            )
            code = watchdog.main(["--state-dir", td, "--quiet"])
            self.assertEqual(code, 1)
            codes = [e["code"] for e in self._events(state)]
            self.assertIn("no_signal_without_verified_collection", codes)

    def test_degraded_llm_warning_reaches_sink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            self._manifest(
                state, "success", degraded_analysis=True,
                llm_analyze_health="degraded", content_ready=True,
            )
            self.assertEqual(watchdog.main(["--state-dir", td, "--quiet"]), 0)
            events = self._events(state)
            self.assertIn("llm_analysis_degraded", [e["code"] for e in events])


class SchedulingEntryTests(unittest.TestCase):
    def test_scheduler_rejects_extract_only_scheduled_run(self) -> None:
        with self.assertRaises(SystemExit):
            scheduler.main([
                "--project-root", "/tmp/x",
                "--state-dir", "/tmp/x/state",
                "--mode", "scheduled",
                "--start-step", "extract",
            ])

    def test_supervisor_starts_at_collection_and_keeps_watchdog_exit(self) -> None:
        conf = Path(__file__).resolve().parents[2] / "ops/r2a/supervisor/watsons-retail-intel-r2a.conf"
        text = conf.read_text(encoding="utf-8")
        self.assertIn("--start-step health_check", text)
        self.assertNotIn("--start-step extract", text)
        self.assertNotIn("|| true", text)
        self.assertIn("watchdog --state-dir", text)
        self.assertIn("--loop --interval", text)


if __name__ == "__main__":
    unittest.main()
