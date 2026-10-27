from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ops.r2b import e2e_replay as replay
from skills.periodic_synthesis import periodic_synthesis as ps


class E1DateSemanticsAndMergeTest(unittest.TestCase):
    def test_date_context_requires_action_and_rejects_report_date(self):
        text = "2026年4月发布研报估算，某平台全年亏损。"
        m = re.search(r"(20(?:25|26))年\s*(\d{1,2})月\s*(\d{1,2})日", text)
        self.assertTrue(m is None or not replay._date_context_is_event(text, m))
        text2 = "2026年10月20日晚八点，天猫双11正式开卖。"
        m2 = re.search(r"(20(?:25|26))年\s*(\d{1,2})月\s*(\d{1,2})日", text2)
        self.assertTrue(m2 and replay._date_context_is_event(text2, m2))

    def test_event_occurrence_wins_over_later_publication(self):
        event = {
            "effective_event_time": "2026-10-23",
            "published_at": "Mon, 26 Oct 2026 11:54:09 GMT",
            "date_semantics": "event_occurrence",
        }
        dt, source = ps.get_effective_event_time(event)
        self.assertEqual(dt.strftime("%Y-%m-%d"), "2026-10-23")
        self.assertEqual(source, "effective_event_time")

    def test_merge_keeps_business_analysis_and_adds_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "data/events" / "2026-10-20"
            d.mkdir(parents=True)
            analyzed = {"events": [{"event_id": "E1", "event_title": "x", "business_analysis": {"watsons_impact": "impact"}}]}
            scored = {"events": [{"event_id": "E1", "event_title": "x", "verification_status": "verified"}]}
            (d / "events_analyzed.json").write_text(json.dumps(analyzed))
            (d / "events_scored_novelty.json").write_text(json.dumps(scored))
            replay._prefer_scored_pool(tmp)
            merged = json.loads((d / "events_analyzed.json").read_text())["events"][0]
            self.assertEqual(merged["business_analysis"]["watsons_impact"], "impact")
            self.assertEqual(merged["verification_status"], "verified")

    def test_old_explicit_date_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "data/events" / "2026-10-20"
            d.mkdir(parents=True)
            e = {
                "event_id": "E2", "event_title": "2026年4月发布研报",
                "fact": "2026年4月发布研报估算，某平台全年亏损。",
                "evidence_text": "2026年4月发布研报估算，某平台全年亏损。",
                "published_at": "Thu, 22 Oct 2026 00:00:00 +0800",
                "verification_status": "verified",
            }
            (d / "events_analyzed.json").write_text(json.dumps({"events": [e]}))
            replay._apply_explicit_date_verification(tmp, date(2026, 10, 19), date(2026, 10, 25))
            out = json.loads((d / "events_analyzed.json").read_text())["events"][0]
            self.assertEqual(out.get("event_occurred_at", ""), "")
            self.assertNotEqual(out["verification_status"], "verified")


if __name__ == "__main__":
    unittest.main()
