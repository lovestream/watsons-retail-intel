#!/usr/bin/env python3
"""No-network tests for bounded P09 decision policy and review-call cap.

Run: PYTHONPATH=".venv_packages:." python3 tests/test_p09_bounded_review.py -v
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from skills.reader_brief.issue_policy import classify_issue
from skills.reader_brief import p09_reviewer
from tests.reader_brief.test_reader_brief import _pack, _brief


def example_material():
    return {
        "location": "signals[0].fact",
        "category": "unsupported_material_claim",
        "severity": "major",
        "brief_quote": "天猫把凑单改成官方立减",
        "claim_id": "CL001",
        "source_excerpt": "10月20日天猫双11开卖",
        "evidence_url": "https://example.com/a",
        "problem": "当前原始主张只证明开卖，不支持具体规则变更",
        "correction": "10月20日天猫双11开卖",
    }


class FakeLLM:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.timeout = 60

    def chat(self, **kwargs):
        self.calls += 1
        if self.calls > len(self.answers):
            raise AssertionError("P09 made a prohibited third review call")
        return {"ok": True, "parsed": self.answers[self.calls - 1],
                "model": "test-model"}


class BoundedP09Test(unittest.TestCase):
    def _run(self, answers):
        fake = FakeLLM(answers)
        with mock.patch.object(p09_reviewer, "get_llm_client", return_value=fake):
            with mock.patch.object(p09_reviewer, "render", return_value=SimpleNamespace(
                    system="frozen system", user="frozen user",
                    version="test", temperature=0, max_tokens=2048)):
                gate, meta = p09_reviewer.review(_brief(), _pack(), "2026-10-29")
        return gate, meta, fake.calls

    def test_concrete_material_issue_has_source_and_quote(self):
        obj = classify_issue(example_material(), _brief(), _pack())
        self.assertEqual(obj["kind"], "material", obj)

    def test_missing_source_quote_is_hold_not_advisory(self):
        item = dict(example_material(), source_excerpt="原文并不存在的精确句子")
        self.assertEqual(classify_issue(item, _brief(), _pack())["kind"],
                         "ungrounded_material")
        g, _, n = self._run([{"decision": "block", "issues": [item]}])
        self.assertEqual(g["state"], "hold_for_review")
        self.assertEqual(n, 1)

    def test_wording_only_must_not_block_even_if_model_says_block(self):
        issue = {"location": "signals[0].fact", "category": "wording_preference",
                 "severity": "minor", "problem": "原文没有‘名单’一词"}
        g, m, n = self._run([{"decision": "block", "issues": [issue]}])
        self.assertEqual(g["state"], "approved")
        self.assertTrue(g["advisory_only"])
        self.assertEqual(n, 1)

    def test_real_material_no_fix_blocks(self):
        g, _, n = self._run([{"decision": "block",
                              "issues": [example_material()]}])
        self.assertEqual(g["state"], "blocked")
        self.assertEqual(n, 1)

    def test_material_but_model_approves_is_hold(self):
        g, _, n = self._run([{"decision": "approve",
                              "issues": [example_material()]}])
        self.assertEqual(g["state"], "hold_for_review")
        self.assertEqual(n, 1)

    def test_bounded_fix_then_approve_only_two_calls(self):
        first = {"decision": "revise", "issues": [example_material()],
                 "suggested_replacements": [{"path": "signals[0].fact",
                                            "replacement_text": "10月20日天猫双11开卖。"}]}
        second = {"decision": "approve", "issues": []}
        g, meta, n = self._run([first, second])
        self.assertEqual(g["state"], "approved", g)
        self.assertEqual(n, 2)
        self.assertEqual(meta["rounds"], 2)
        self.assertEqual(g["brief"]["signals"][0]["fact"],
                         "10月20日天猫双11开卖。")

    def test_second_review_material_stops_at_two(self):
        first = {"decision": "revise", "issues": [example_material()],
                 "suggested_replacements": [{"path": "signals[0].fact",
                                            "replacement_text": "10月20日天猫双11开卖。"}]}
        new_issue = dict(example_material(), brief_quote="天猫双11开卖")
        g, _, n = self._run([first, {"decision": "block", "issues": [new_issue]}])
        self.assertEqual(g["state"], "blocked")
        self.assertEqual(n, 2)

    def test_second_review_unsupported_major_is_hold(self):
        first = {"decision": "revise", "issues": [example_material()],
                 "suggested_replacements": [{"path": "signals[0].fact",
                                            "replacement_text": "10月20日天猫双11开卖。"}]}
        new_issue = dict(example_material(), source_excerpt="编造的引用")
        g, _, n = self._run([first, {"decision": "block", "issues": [new_issue]}])
        self.assertEqual(g["state"], "hold_for_review")
        self.assertEqual(n, 2)

    def test_critical_style_conflict_held_not_silently_approved(self):
        item = {"category": "wording_preference", "severity": "critical",
                "location": "signals[0].fact", "problem": "再精确一点"}
        g, _, n = self._run([{"decision": "block", "issues": [item]}])
        self.assertEqual(g["state"], "hold_for_review")
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
