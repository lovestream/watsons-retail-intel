import json
import tempfile
import unittest
from pathlib import Path

from skills.periodic_editor_review.periodic_editor_review import review_final_draft
from skills.generate_periodic_report.generate_periodic_report import phase4_finalize
from skills.utils.report_sanitizer import sanitize_periodic_report

ROOT = Path(__file__).parent


class PeriodicQualityGateTest(unittest.TestCase):
    def setUp(self):
        self.w40 = (ROOT / "fixtures/w40_degraded.md").read_text(encoding="utf-8")
        self.w40_syn = json.loads((ROOT / "fixtures/w40_synthesis.json").read_text())
        self.good = (ROOT / "fixtures/good_weekly.md").read_text(encoding="utf-8")
        self.good_syn = json.loads((ROOT / "fixtures/good_synthesis.json").read_text())

    def test_w40_water_report_is_blocked(self):
        r = review_final_draft(
            "weekly", self.w40, self.w40_syn, self.w40_syn.get("coverage"),
            chapter_ids=["01", "02", "03", "04", "05", "06"],
            fallback_chapters=["01", "02", "03", "04", "05", "06"],
            generation_stats={"expected_chapters": 6})
        self.assertFalse(r["sendable"])
        self.assertEqual(r["overall_quality"], "needs_revision")
        self.assertIn("fallback_or_template", r["failed_checks"])
        self.assertIn("repeated_content", r["failed_checks"])
        self.assertIn("insufficient_evidence", r["failed_checks"])
        self.assertIn("chapter_too_short", r["failed_checks"])

    def test_good_weekly_passes_clean_gate(self):
        r = review_final_draft(
            "weekly", self.good, self.good_syn, self.good_syn["coverage"],
            chapter_ids=["01", "02", "03", "04", "05", "06"],
            generation_stats={"expected_chapters": 6})
        self.assertTrue(r["sendable"], r)
        self.assertEqual(r["overall_quality"], "good")

    def test_missing_chapter_is_detected(self):
        # The historical code passed actual chapter ids as expected ids, which
        # made a truncated report look complete.  Expected ids must be fixed.
        report = self.good.replace("## 06 风险与观察", "## 风险与观察")
        r = review_final_draft(
            "weekly", report, self.good_syn, self.good_syn["coverage"],
            chapter_ids=["01", "02", "03", "04", "05", "06"],
            generation_stats={"expected_chapters": 6})
        self.assertFalse(r["sendable"], r)
        self.assertIn("章节缺失", " ".join(r["high_issues"]))

    def test_single_cluster_evidence_is_blocked(self):
        syn = {"all_clusters": self.good_syn["all_clusters"][:1],
               "selected_clusters": self.good_syn["selected_clusters"][:1]}
        issues = []
        from skills.periodic_editor_review.periodic_editor_review import (
            _check_evidence_sufficiency)
        issues = _check_evidence_sufficiency("weekly", syn)
        self.assertTrue(any("证据簇不足" in x for x in issues), issues)

    def test_display_cleaning_happens_before_gate(self):
        dirty = self.good.replace(
            "## 01 本周一句话结论",
            "## 01 本周一句话结论\n判断标签：A  E20261026_0001")
        clean = sanitize_periodic_report(dirty, mode="final")
        self.assertNotIn("E20261026_0001", clean)
        self.assertNotIn("判断标签", clean)
        r = review_final_draft(
            "weekly", clean, self.good_syn, self.good_syn["coverage"],
            chapter_ids=["01", "02", "03", "04", "05", "06"],
            generation_stats={"expected_chapters": 6})
        self.assertTrue(r["sendable"], r)

    def test_phase4_blocks_rule_fallback_before_audio(self):
        chapters = {f"{i:02d}": "规则兜底：按事件密度排序\n- 重复内容" for i in range(1, 7)}
        with tempfile.TemporaryDirectory() as td:
            r = phase4_finalize(
                chapters, "weekly", "2026-W40",
                {"year": 2026, "week": 40, "start": "2026-09-28",
                 "end": "2026-10-04"},
                td, self.w40_syn, {"status": "passing"})
        self.assertFalse(r["sendable"])
        self.assertIn("quality_gate", r["delivery_blockers"])
        self.assertFalse(r.get("tts_success", False))


if __name__ == "__main__":
    unittest.main()
