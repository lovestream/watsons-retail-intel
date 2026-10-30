#!/usr/bin/env python3
"""P01–P15 独立提示词接入的功能测试（不依赖 pytest）。

运行：PYTHONPATH=".venv_packages:." python3 tests/test_prompt_integration.py
覆盖 docs/16 §4 中可离线判定的部分：
- registry 变量/章节校验；
- P08 JSON schema 与确定性门（fact_id/数字/状态/期限/链接/泄漏）；
- P09 门禁语义（critical → block）；
- P05 选题引用校验；
- P14/P15 周/月覆盖门（自然月 5 个 ISO 周、12 月未封账 incomplete）；
- P01/P02/P04 旧字段兼容块。
"""

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.utils import prompt_registry as reg          # noqa: E402
from skills.utils.prompt_bindings import (p01_prompt, p02_system,      # noqa: E402
                                          p04_system, prompt_versions)
from skills.reader_brief import schema                   # noqa: E402
from skills.reader_brief.editor import _validate_topic_plan             # noqa: E402
from skills.reader_brief.p09_reviewer import summarize_gate             # noqa: E402
from skills.periodic_synthesis import cross_period as cp                # noqa: E402


def pack():
    return {"period_type": "weekly",
            "period": {"start": "2026-10-19", "end": "2026-10-25"},
            "coverage": {}, "pack_sha256": "abc123",
            "facts": [{
                "fact_id": "F001", "claim_id": "CL001",
                "text": "10月20日天猫双11开卖，4小时124个品牌成交破亿。",
                "date": "2026-10-20", "date_role": "occurred",
                "status": "announced", "numbers": ["10", "20", "4", "124"],
                "platforms": ["天猫"], "competitors": [], "event_ids": [],
                "event_type": "", "source": {"name": "36氪",
                                            "url": "https://36kr.com/p/1",
                                            "kind": "media"}},
                     {"fact_id": "F002", "claim_id": "CL002",
                      "text": "丝芙兰玩妆节于10月23日至25日在上海张园举办。",
                      "date": "2026-10-23", "date_role": "occurred",
                      "status": "reported", "numbers": ["10", "23", "25"],
                      "platforms": [], "competitors": ["丝芙兰"], "event_ids": [],
                      "event_type": "", "source": {"name": "美妆头条",
                                                  "url": "https://example.com/a",
                                                  "kind": "media"}}]}


def brief(**over):
    b = {"period_type": "weekly", "period_start": "2026-10-19",
         "period_end": "2026-10-25", "title": "双11改官方立减",
         "one_line_takeaway": "天猫把凑单改成官方立减，屈臣氏需重算单件折扣。",
         "signals": [{"headline": "双11改官方立减", "fact": "天猫把凑单改成官方立减。",
                      "fact_ids": ["F001"], "claim_ids": ["CL001"],
                      "source_refs": [{"name": "36氪", "url": "https://36kr.com/p/1",
                                       "supports_claim_ids": ["CL001"]}],
                      "business_impact": "满减连带失效", "is_inference": True}],
         "competitor_cards": [],
         "priority_actions": [{"title": "重算折扣带",
                              "precondition": "需先取得官方细则",
                              "trial": "选少量SKU做两版测算",
                              "metric": "毛利率", "linked_fact_ids": ["F001"]}],
         "watch_items": [], "omitted_fact_ids": [{"fact_id": "F002",
                                                 "reason": "弱关联"}],
         "audit": {"human_review_status": "pending",
                   "source_pack_sha256": "abc123"}}
    b.update(over)
    return b


class RegistryTest(unittest.TestCase):
    def test_all_15_prompts_valid(self):
        self.assertEqual(reg.check_all(), [])

    def test_versions_stable_and_distinct(self):
        v = {k: reg.get_prompt(k).version for k in ("P01", "P02", "P04", "P08",
                                                    "P09", "P14", "P15")}
        self.assertEqual(len(set(v.values())), len(v))
        self.assertEqual(v, {k: reg.get_prompt(k).version for k in v})

    def test_render_requires_and_fills_vars(self):
        with self.assertRaises(ValueError):
            reg.render("P08", period_type="weekly")
        r = reg.render("P08", period_type="weekly", period_start="a",
                       period_end="b", send_at="2026-10-29",
                       reader_fact_pack_json=[{"fact_id": "F001"}],
                       topic_plan_json={"topics": []},
                       verified_internal_capabilities_json={},
                       previous_digest_fingerprints_json=[])
        self.assertNotIn("{reader_fact_pack_json}", r.user)  # 无残留占位符
        self.assertIn("F001", r.user)

    def test_no_opencode_in_provider_layer(self):
        src = (Path(__file__).resolve().parents[1] /
               "skills/utils/llm_client.py").read_text(encoding="utf-8")
        # 只允许历史追溯函数名，不允许实际 base_url/model 常量
        self.assertNotIn('OPENCODE_BASE_URL = "https://opencode.ai', src)
        self.assertNotIn('OPENCODE_MODEL = "deepseek-v4-flash-free"', src)
        self.assertIn("SILICONFLOW_BASE_URL", src)
        self.assertIn("rightcode_bybt", src)


class BindingTest(unittest.TestCase):
    def test_p01_has_legacy_fields(self):
        text = p01_prompt({"title": "t", "summary": "s", "content": "c"})
        for f in ("recommended_pool", "llm_relevance", "confidence"):
            self.assertIn(f, text)

    def test_p02_p04_keep_enums(self):
        self.assertIn("effective_at", p02_system())
        self.assertIn("action_level", p04_system())
        self.assertIn("watch|test|act", p04_system())

    def test_versions_cover_active_prompts(self):
        v = prompt_versions()
        self.assertEqual(v["P08"], reg.get_prompt("P08").version)


class SchemaGateTest(unittest.TestCase):
    def test_valid_brief_passes(self):
        v = schema.validate_brief(brief(), pack(), "2026-10-29")
        self.assertTrue(v["pass"], v["errors"])

    def test_unknown_fact_id(self):
        b = brief(signals=[dict(brief()["signals"][0], fact_ids=["F999"])])
        v = schema.validate_brief(b, pack(), "2026-10-29")
        self.assertIn("unknown_fact_id", [e["code"] for e in v["errors"]])

    def test_number_not_in_fact(self):
        b = brief(priority_actions=[dict(brief()["priority_actions"][0],
                                          metric="提升37%毛利")])
        v = schema.validate_brief(b, pack(), "2026-10-29")
        self.assertIn("number_not_in_fact", [e["code"] for e in v["errors"]])

    def test_status_overclaim(self):
        b = brief(signals=[dict(brief()["signals"][0],
                                fact="丝芙兰已上线新专区。", fact_ids=["F002"],
                                claim_ids=["CL002"],
                                source_refs=[{"name": "美妆头条",
                                              "url": "https://example.com/a"}])])
        codes = [e["code"] for e in schema.validate_brief(b, pack(), "2026-10-29")["errors"]]
        self.assertIn("status_overclaim", codes)

    def test_expired_deadline_action(self):
        b = brief(priority_actions=[dict(brief()["priority_actions"][0],
                                          trial="在2026年8月3日前完成改价")])
        codes = [e["code"] for e in schema.validate_brief(b, pack(), "2026-10-29")["errors"]]
        self.assertIn("expired_deadline", codes)

    def test_unbound_source_url(self):
        b = brief(signals=[dict(brief()["signals"][0],
                                source_refs=[{"name": "假来源",
                                              "url": "https://fake.example/x"}])])
        codes = [e["code"] for e in schema.validate_brief(b, pack(), "2026-10-29")["errors"]]
        self.assertIn("unbound_source_url", codes)

    def test_internal_marker_leak(self):
        b = brief(title="E20261029_0037 双11改节奏")
        codes = [e["code"] for e in schema.validate_brief(b, pack(), "2026-10-29")["errors"]]
        self.assertIn("event_id_leak", codes)

    def test_fake_human_approval(self):
        b = brief(audit={"human_review_status": "verified",
                         "source_pack_sha256": "abc123"})
        codes = [e["code"] for e in schema.validate_brief(b, pack(), "2026-10-29")["errors"]]
        self.assertIn("fake_human_approval", codes)

    def test_too_many_signals(self):
        s = brief()["signals"][0]
        b = brief(signals=[dict(s) for _ in range(4)])
        codes = [e["code"] for e in schema.validate_brief(b, pack(), "2026-10-29")["errors"]]
        self.assertIn("too_many_items", codes)

    def test_gate_summary_language(self):
        self.assertIn("阻断", summarize_gate({"state": "blocked", "issues": [1]}))
        self.assertIn("暂缓", summarize_gate({"state": "hold_for_review"}))
        self.assertIn("已通过", summarize_gate({"state": "approved",
                                              "decision": "approve"}))


class TopicPlanTest(unittest.TestCase):
    def test_unknown_ids_removed_and_demoted(self):
        plan = {"topics": [{"source_fact_ids": ["F001", "F999"],
                            "reader_priority": "must_read"},
                           {"source_fact_ids": ["F404"], "reader_priority": "must_read"}]}
        _validate_topic_plan(plan, pack())
        self.assertEqual(plan["topics"][0]["source_fact_ids"], ["F001"])
        self.assertFalse(plan["topics"][1]["should_publish"])

    def test_must_read_cap(self):
        plan = {"topics": [{"source_fact_ids": ["F001"], "reader_priority": "must_read"}
                           for _ in range(5)]}
        _validate_topic_plan(plan, pack())
        self.assertEqual(sum(1 for t in plan["topics"]
                             if t["reader_priority"] == "must_read"), 3)


class CrossPeriodGateTest(unittest.TestCase):
    def test_july_2026_has_five_iso_weeks(self):
        self.assertEqual(len(cp.iso_weeks_of_month(2026, 7)), 5)

    def test_month_not_closed_on_first_day(self):
        self.assertFalse(cp.month_closed(2026, 12, date(2026, 12, 31)))
        self.assertTrue(cp.month_closed(2026, 11, date(2027, 1, 1)))

    def test_monthly_incomplete_without_weekly_reports(self):
        res = cp.monthly_synthesis(Path("/tmp/_empty_root"), 2026, 7,
                                   "2026-10-29", call_llm=False)
        self.assertEqual(res["status"], "incomplete")
        self.assertFalse(res["_meta"]["llm_called"])
        self.assertEqual(len(res["gate"]["missing_weeks"]), 5)

    def test_yearly_incomplete_without_monthly_reports(self):
        res = cp.yearly_synthesis(Path("/tmp/_empty_root"), 2026, "2026-10-29",
                                  call_llm=False)
        self.assertEqual(res["status"], "incomplete")
        self.assertEqual(len(res["gate"]["missing_months"]), 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)
