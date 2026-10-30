"""Production delivery gates: run in NAS venv, no real SMTP or LLM calls.

PYTHONPATH=".venv_packages:." python3 -m unittest -v tests.test_production_delivery_gate
"""
import hashlib
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.reader_brief.production_delivery import (
    _dates, _period_id, prepare_approved_brief,
)
from skills.send_daily_report_email import send_daily_report_email as sender


class DateAndSourceGates(unittest.TestCase):
    def test_month_five_week_boundary(self):
        self.assertEqual(_dates("monthly", {"year": "2026", "month": "07"}),
                         ("2026-07-01", "2026-07-31"))

    def test_december_and_leap_february(self):
        self.assertEqual(_dates("monthly", {"year": "2028", "month": "02"})[1],
                         "2028-02-29")
        self.assertEqual(_dates("monthly", {"year": "2026", "month": "12"})[1],
                         "2026-12-31")

    def test_week_id_and_year_range(self):
        w = {"start": "2026-10-19", "end": "2026-10-25"}
        self.assertEqual(_period_id("weekly", w), "2026-W43")
        self.assertEqual(_dates("yearly", {"year": "2026"}),
                         ("2026-01-01", "2026-12-31"))

    def test_month_no_complete_p14_never_runs_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("subprocess.run", side_effect=AssertionError("should not execute")):
                result = prepare_approved_brief(
                    "monthly", {"year": "2026", "month": "7"}, tmp,
                    synthesis={}, cross={"status": "incomplete"})
        self.assertFalse(result["approved"])
        self.assertEqual(result["error"], "cross_period_not_complete")

    def test_year_no_p15_never_runs_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = prepare_approved_brief("yearly", {"year": "2026"}, tmp,
                                            synthesis={}, cross=None)
        self.assertFalse(result["approved"])


class MailSenderGate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        self.report = self.root / "research.md"
        self.report.write_text("SHOULD_NOT_APPEAR_IN_SENT_HTML", encoding="utf-8")
        self.html = self.root / "approved.html"
        self.html.write_text("<html>ONLY_APPROVED_P09_HTML</html>", encoding="utf-8")
        self.manifest = self.root / "manifest.json"
        self._write_manifest()
        self.args = dict(
            report_type="weekly", period_label="2026-W43",
            report_path=str(self.report), audio_path="",
            recipient="sandbox-test@example.invalid",
            project_root=str(self.root),
            approved_brief_html_path=str(self.html),
            approved_brief_manifest_path=str(self.manifest),
        )
        self.logger = logging.getLogger("test-prod-gate")
        patch = mock.patch.object(sender, "_setup_logger", return_value=self.logger)
        patch.start()
        self.addCleanup(patch.stop)

    def _write_manifest(self, **changes):
        m = {"period_type": "weekly", "delivery_state": "approved",
             "sendable": True,
             "html_sha256": hashlib.sha256(self.html.read_bytes()).hexdigest()}
        m.update(changes)
        self.manifest.write_text(json.dumps(m), encoding="utf-8")

    def test_default_hold_prevents_smtp_even_if_approved(self):
        with mock.patch.dict(os.environ, {"WATSONS_PERIODIC_MAIL_MODE": "hold"}):
            with mock.patch.object(sender, "_send_email", side_effect=AssertionError("SMTP forbidden")):
                r = sender.send_periodic_report_email(**self.args)
        self.assertEqual(r["error"], "periodic_delivery_disabled")

    def test_approved_source_required(self):
        self.args["approved_brief_manifest_path"] = ""
        with mock.patch.dict(os.environ, {"WATSONS_PERIODIC_MAIL_MODE": "reader_brief"}):
            r = sender.send_periodic_report_email(**self.args)
        self.assertEqual(r["error"], "approved_reader_brief_missing")

    def test_html_tamper_detected(self):
        self.html.write_text("<html>TAMPERED</html>", encoding="utf-8")
        with mock.patch.dict(os.environ, {"WATSONS_PERIODIC_MAIL_MODE": "reader_brief"}):
            r = sender.send_periodic_report_email(**self.args)
        self.assertEqual(r["error"], "reader_brief_approval_or_hash_mismatch")

    def test_unapproved_state_detected(self):
        self._write_manifest(delivery_state="hold_for_review", sendable=False)
        with mock.patch.dict(os.environ, {"WATSONS_PERIODIC_MAIL_MODE": "reader_brief"}):
            r = sender.send_periodic_report_email(**self.args)
        self.assertEqual(r["error"], "reader_brief_approval_or_hash_mismatch")

    def test_real_send_rejects_absent_mp3(self):
        with mock.patch.dict(os.environ, {"WATSONS_PERIODIC_MAIL_MODE": "reader_brief"}):
            with mock.patch.object(sender, "_send_email", side_effect=AssertionError("SMTP forbidden")):
                r = sender.send_periodic_report_email(**self.args)
        self.assertEqual(r["error"], "required_audio_missing_or_invalid")

    def test_dry_run_uses_approved_html_never_legacy_markdown(self):
        observed = {}
        def fake_send_email(**kw):
            observed.update(kw)
            return True, "dryrun"
        with mock.patch.dict(os.environ, {"WATSONS_PERIODIC_MAIL_MODE": "reader_brief"}):
            with mock.patch.object(sender, "_check_delivery_duplicate", return_value=False):
                with mock.patch.object(sender, "_send_email", side_effect=fake_send_email):
                    r = sender.send_periodic_report_email(**dict(self.args, dry_run=True))
        self.assertTrue(r["ok"])
        self.assertIn("ONLY_APPROVED_P09_HTML", observed["html_body"])
        self.assertNotIn("SHOULD_NOT_APPEAR_IN_SENT_HTML", observed["html_body"])


if __name__ == "__main__":
    unittest.main()
