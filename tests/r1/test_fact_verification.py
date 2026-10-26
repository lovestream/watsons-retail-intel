#!/usr/bin/env python3
"""F4 regression tests: no false `verified` on date conflict / hearsay."""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tests.r1.run_20_samples import assess_verification  # noqa: E402


def samples():
    with open(os.path.join(ROOT, "tests/fixtures/r1_samples_20.json"),
              encoding="utf-8") as fh:
        data = json.load(fh)
    return {s["sample_id"]: s for s in data["samples"]}


class FactVerificationTests(unittest.TestCase):
    def test_r1s07_hearsay_is_not_verified(self):
        v = assess_verification({
            "fact": "得物男士护肤环比增长150%-200%。",
            "evidence_text": "听说得物男士护肤非常猛，“环比增长150%-200%”。",
            "event_occurred_at": "2026-09-03",
        }, samples()["R1S07"])
        self.assertEqual(v["verification_status"], "needs_verification")
        self.assertIn("hearsay_source:听说", v["reasons"])
        self.assertFalse(v["verified"])

    def test_r1s13_date_conflict_is_not_verified(self):
        v = assess_verification({
            "fact": "永辉超市门店健康场景改造带动增长。",
            "evidence_text": "十大健康场景落地仅一个月，销售额环比提升。",
            "event_occurred_at": "2026-09-26",
        }, samples()["R1S13"])
        self.assertEqual(v["verification_status"], "needs_verification")
        self.assertTrue(v["date_conflict"])
        self.assertIn("date_conflict:2026-09-13!=2026-09-26", v["reasons"])

    def test_missing_event_date_is_flagged_not_verified(self):
        v = assess_verification({"fact": "某平台调整即时零售履约规则。"},
                                samples()["R1S07"])
        self.assertEqual(v["verification_status"], "needs_verification")
        self.assertIn("event_date_missing_fixture_fallback", v["reasons"])

    def test_clean_event_is_verified(self):
        v = assess_verification({
            "fact": "美团调整即时零售履约规则。",
            "evidence_text": "美团宣布调整即时零售履约规则。",
            "event_occurred_at": "2026-09-03",
        }, samples()["R1S07"])
        self.assertEqual(v["verification_status"], "verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
