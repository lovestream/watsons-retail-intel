import json
import tempfile
import unittest
from pathlib import Path

from skills.m2.tri_state_prefilter import BLOCK, PASS, UNKNOWN, annotate_events, classify_candidate
from skills.m2.selectbench import evaluate, load_cases
from skills.m2.source_yield import audit
from skills.m2.event_relationship import relation, independent_sources

ROOT = Path(__file__).parent


class TriStateTest(unittest.TestCase):
    def test_stock_mentioning_instant_retail_is_blocked(self):
        r = classify_candidate({
            "title": "即时零售龙头股价续创历史新高",
            "summary": "券商研报上调目标价和盈利预测，提到美团闪购。",
        })
        self.assertEqual(r["label"], BLOCK)

    def test_job_ad_is_blocked_even_with_domain_signal(self):
        r = classify_candidate({
            "title": "即时零售运营经理招聘",
            "summary": "负责美团闪购运营，要求三年经验。",
        })
        self.assertEqual(r["label"], BLOCK)

    def test_hk_only_is_blocked(self):
        r = classify_candidate({
            "title": "香港屈臣氏独家优惠",
            "summary": "香港门店限时优惠，适用于香港本地。",
        })
        self.assertEqual(r["label"], BLOCK)

    def test_specific_business_action_passes(self):
        r = classify_candidate({
            "title": "京东秒送在重点城市上线美妆专区",
            "summary": "京东秒送联合个护美妆品牌开城，前置仓覆盖防晒和洗护品类。",
            "source_url": "https://www.jd.com/",
        })
        self.assertEqual(r["label"], PASS)

    def test_negated_signal_does_not_pass(self):
        r = classify_candidate({
            "title": "沃尔玛调整自有品牌",
            "summary": "沃尔玛调整SKU结构，未涉及即时零售或美妆品类。",
        })
        self.assertEqual(r["label"], UNKNOWN)

    def test_insufficient_text_is_unknown_not_silent_reject(self):
        r = classify_candidate({"title": "美妆渠道变化", "summary": ""})
        self.assertEqual(r["label"], UNKNOWN)
        self.assertTrue(r["review_queue"])

    def test_event_pool_rows_use_event_title_and_fact(self):
        rows = annotate_events([{
            "event_id": "E1",
            "event_title": "美团闪购在重点城市上线美妆专区",
            "fact": "美团闪购联合美妆品牌上线专区，覆盖30分钟履约。",
            "source_url": "https://example.invalid/a",
        }])
        self.assertEqual(rows[0]["tri_state"]["label"], PASS)


class SelectBenchTest(unittest.TestCase):
    def test_fixture_has_no_false_block_and_is_not_auto_enabled(self):
        result = evaluate(load_cases(str(ROOT / "fixtures/selectbench.jsonl")))
        self.assertEqual(result["n"], 59)
        self.assertEqual(result["false_block"], 0)
        self.assertEqual(result["false_pass"], 0)
        self.assertTrue(result["traceability_ok"])
        self.assertEqual(result["real_evidence_n"], 40)
        self.assertEqual(result["verified_real_evidence_n"], 0)
        self.assertEqual(result["synthetic_n"], 19)
        self.assertTrue(all(r["evidence_url"] for r in result["rows"]))
        # 59 cases include 19 synthetic/homepage regressions; the 40 real URLs are
        # captured but not all re-verified, so this is not a production gate.
        self.assertFalse(result["safe_to_enable"])


class SourceYieldTest(unittest.TestCase):
    def test_counts_verified_and_reportable(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "data/events/2026-10-01"
            d.mkdir(parents=True)
            (d / "events_analyzed.json").write_text(json.dumps({"events": [
                {"source_name": "A", "source_url": "https://a.example/1",
                 "event_title": "即时零售", "report_eligibility": "core",
                 "verification_status": "verified", "novelty_status": "new_today",
                 "priority": "P1", "fact": "f", "evidence_text": "e"},
                {"source_name": "A", "source_url": "https://a.example/2",
                 "event_title": "旧闻", "report_eligibility": "archive",
                 "verification_status": "rejected", "novelty_status": "old",
                 "priority": "P2", "fact": "f", "evidence_text": "e"},
            ]}, ensure_ascii=False))
            result = audit(td)
        self.assertEqual(result["total_events"], 2)
        self.assertEqual(result["total_reportable"], 1)
        self.assertEqual(result["sources"][0]["source"], "A")


class EventRelationshipTest(unittest.TestCase):
    def test_same_event_then_update(self):
        a = {"event_id": "E1", "fact_hash": "h1", "event_title": "美团加码即时零售"}
        b = {"event_id": "E2", "fact_hash": "h1", "event_title": "美团加码即时零售"}
        self.assertEqual(relation(a, b), "same_event")
        c = {"event_id": "E3", "fact_hash": "h2",
             "event_title": "美团加码即时零售新城市", "published_at": "2026-10-02"}
        self.assertEqual(relation(a, c), "update")
        self.assertEqual(len(independent_sources([
            {"source_url": "https://a.example/x"},
            {"source_url": "https://a.example/y"},
            {"source_url": "https://b.example/z"}])), 2)


if __name__ == "__main__":
    unittest.main()
