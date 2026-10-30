#!/usr/bin/env python3
"""R0–R3 整改验收测试（不依赖 pytest）。

运行：PYTHONPATH=".venv_packages:." python3 tests/test_pipeline_r0_r3.py
覆盖独立复审 P0-01/P0-02/P0-03/P0-04/P1-01/P1-02/P1-03 的离线可判定部分：
- P09 三态交付：不可用 → hold_for_review（不是 pass），程序门错误 → blocked；
- P14/P15 正例：5 相交周 / 12 月，render 全变量（含 send_at）走通、逐条证据校验、缓存失效；
- 覆盖门依据 manifest 封账而非文件存在；degraded/incomplete 不调 LLM；
- P05 未发布选题的 fact 不得进入 P08；
- SiliconFlow 主池 + RightCode 单独备用（各自模型名、不重复访问）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.periodic_synthesis import cross_period as cp          # noqa: E402
from skills.reader_brief import p09_reviewer, schema             # noqa: E402
from skills.reader_brief.editor import _validate_topic_plan      # noqa: E402
from skills.utils import llm_client as lc                        # noqa: E402


# ---------------- fixtures ----------------

def _week_bounds(week):
    y, w = week.split("-W")
    mon = date.fromisocalendar(int(y), int(w), 1)
    return mon, mon + timedelta(days=6)


def build_weekly_fixture(root: Path, weeks, closed=True, quality=True,
                         cov=0.9):
    for wk in weeks:
        p = root / "reports" / "weekly" / wk.split("-")[0] / f"{wk}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {wk} 周报\n\n## 01 一句话结论\n本周关键变化之一。\n",
                     encoding="utf-8")
        sha = cp._sha(p.read_text(encoding="utf-8"))
        m = root / "data" / "runs" / "periodic" / "weekly" / wk / "manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"closed": closed, "quality_pass": quality,
                                 "source_coverage": cov, "report_sha": sha}),
                     encoding="utf-8")
        lo, hi = _week_bounds(wk)
        d = lo
        while d <= hi:
            day = root / "data" / "events" / d.isoformat()
            day.mkdir(parents=True, exist_ok=True)
            fid = f"F{d.isoformat()}"
            (day / "events_analyzed.json").write_text(json.dumps([{
                "event_id": fid, "fact_id": fid, "claim_id": fid,
                "event_title": f"{d.isoformat()} 天猫即时零售调整",
                "source_url": f"https://example.com/{d.isoformat()}",
                "status": "reported", "date_role": "occurred"}],
                ensure_ascii=False), encoding="utf-8")
            d += timedelta(days=1)


def build_monthly_fixture(root: Path, year, months):
    for m in months:
        pid = f"{year}-{m:02d}"
        p = root / "reports" / "monthly" / str(year) / f"{pid}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {pid} 月报\n\n## 01 本月结论\n结构变化。\n",
                     encoding="utf-8")
        sha = cp._sha(p.read_text(encoding="utf-8"))
        mf = root / "data" / "runs" / "periodic" / "monthly" / pid / "manifest.json"
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps({"closed": True, "quality_pass": True,
                                  "source_coverage": 0.85, "report_sha": sha}),
                      encoding="utf-8")


class FakeClient:
    def __init__(self, payload_builder=None, error=""):
        self.model = "fake-model"
        self.calls = []
        self.payload_builder = payload_builder
        self.error = error

    def chat(self, messages=None, system_prompt="", response_format="json",
             temperature=0.3, max_tokens=1000, model=None):
        self.calls.append({"system": system_prompt,
                           "user": (messages or [{}])[0].get("content", "")})
        if self.error:
            return {"ok": False, "parsed": None, "content": "",
                    "model": self.model, "error": self.error}
        parsed = self.payload_builder(self.calls[-1]) if self.payload_builder else {}
        return {"ok": True, "parsed": parsed, "content": json.dumps(parsed),
                "model": self.model, "error": ""}


# ---------------- P0-01 ----------------

class P09DeliveryStateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._cache = cp.CACHE_DIR
        cp.CACHE_DIR = self.root / "cache"

    def tearDown(self):
        cp.CACHE_DIR = self._cache
        shutil.rmtree(self.root, ignore_errors=True)

    def _pack(self):
        return {"period_type": "weekly",
                "period": {"start": "2026-10-19", "end": "2026-10-25"},
                "pack_sha256": "abc",
                "facts": [{"fact_id": "F001", "claim_id": "CL001",
                           "text": "10月20日天猫双11开卖，4小时124个品牌成交破亿。",
                           "date": "2026-10-20", "date_role": "occurred",
                           "status": "announced", "numbers": ["10", "20", "4", "124"],
                           "platforms": ["天猫"], "competitors": [], "event_ids": [],
                           "event_type": "", "source": {"name": "36氪",
                                                       "url": "https://36kr.com/p/1",
                                                       "kind": "media"}}]}

    def _brief(self):
        return {"period_type": "weekly", "period_start": "2026-10-19",
                "period_end": "2026-10-25", "title": "双11改官方立减",
                "one_line_takeaway": "天猫改规则，屈臣氏需重算折扣。",
                "signals": [{"headline": "双11改规则", "fact": "天猫把凑单改成官方立减。",
                             "fact_ids": ["F001"], "claim_ids": ["CL001"],
                             "source_refs": [{"name": "36氪",
                                              "url": "https://36kr.com/p/1",
                                              "supports_claim_ids": ["CL001"]}],
                             "business_impact": "满减连带失效",
                             "is_inference": True}],
                "competitor_cards": [], "priority_actions": [],
                "watch_items": [], "omitted_fact_ids": [],
                "audit": {"human_review_status": "pending",
                          "source_pack_sha256": "abc"}}

    def test_p09_unavailable_is_hold_not_pass(self):
        with mock.patch.object(p09_reviewer, "get_llm_client",
                               return_value=FakeClient(error="请求超时(300s)")):
            gate, meta = p09_reviewer.review(self._brief(), self._pack(),
                                             "2026-10-29")
        self.assertEqual(gate["state"], "hold_for_review")
        self.assertTrue(gate["degraded"])
        self.assertIn("暂缓", p09_reviewer.summarize_gate(gate))
        self.assertTrue(meta["degraded"])

    def test_deterministic_error_is_blocked(self):
        bad = self._brief()
        bad["signals"][0]["fact_ids"] = ["F999"]
        with mock.patch.object(p09_reviewer, "get_llm_client",
                               return_value=FakeClient(error="down")):
            gate, _ = p09_reviewer.review(bad, self._pack(), "2026-10-29")
        self.assertEqual(gate["state"], "blocked")

    def test_approved_only_on_real_review(self):
        def builder(call):
            return {"decision": "approve", "issues": [],
                    "checked_claim_ids": ["CL001"], "unsupported_claims": []}
        with mock.patch.object(p09_reviewer, "get_llm_client",
                               return_value=FakeClient(builder)):
            gate, meta = p09_reviewer.review(self._brief(), self._pack(),
                                             "2026-10-29")
        self.assertEqual(gate["state"], "approved")
        self.assertEqual(len(meta.get("raw_reviews") or []), 1)


# ---------------- P0-02 / P0-03 / P0-04 ----------------

class CrossPeriodPositiveTest(unittest.TestCase):
    YEAR, MONTH = 2026, 7

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self._cache = cp.CACHE_DIR
        cp.CACHE_DIR = self.root / "data" / "cache" / "cross_period"
        self.weeks = cp.iso_weeks_of_month(self.YEAR, self.MONTH)
        build_weekly_fixture(self.root, self.weeks)
        self.synth_calls = []

    def tearDown(self):
        cp.CACHE_DIR = self._cache
        shutil.rmtree(self.root, ignore_errors=True)

    def _builder(self, call):
        user = call["user"]
        self.synth_calls.append(user)
        fid = "F2026-07-01"
        return {"headline": "7 月即时零售进入补贴换量阶段",
                "trends": [{"name": "补贴换量", "claim": "平台补贴常态化",
                            "fact_ids": [fid],
                            "source_url": "https://example.com/2026-07-01"}],
                "structural_changes": [], "data_gaps": []}

    def test_render_has_send_at_and_completes(self):
        fake = FakeClient(self._builder)
        with mock.patch.object(cp, "get_llm_client", return_value=fake):
            res = cp.monthly_synthesis(self.root, self.YEAR, self.MONTH,
                                       "2026-10-29", call_llm=True)
        self.assertEqual(res["status"], "complete", res.get("gate"))
        self.assertTrue(res["_meta"]["ok"])
        self.assertEqual(res["synthesis"]["_validation"]["ok"], True)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("2026-07-01", fake.calls[0]["user"])
        combined = fake.calls[0]["system"] + fake.calls[0]["user"]
        self.assertIn("2026-10-29", combined)
        self.assertIn("2026-07-01", combined)

    def test_cache_hit_and_invalidation(self):
        fake = FakeClient(self._builder)
        with mock.patch.object(cp, "get_llm_client", return_value=fake):
            cp.monthly_synthesis(self.root, self.YEAR, self.MONTH, "2026-10-29")
            again = cp.monthly_synthesis(self.root, self.YEAR, self.MONTH,
                                        "2026-10-29")
        self.assertTrue(again["_meta"].get("cache_hit"))
        self.assertEqual(len(fake.calls), 1)
        wk = self.weeks[0]
        p = self.root / "reports" / "weekly" / wk.split("-")[0] / f"{wk}.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n新增一段。\n",
                     encoding="utf-8")
        m = self.root / "data" / "runs" / "periodic" / "weekly" / wk / "manifest.json"
        man = json.loads(m.read_text(encoding="utf-8"))
        man["report_sha"] = cp._sha(p.read_text(encoding="utf-8"))
        m.write_text(json.dumps(man), encoding="utf-8")
        with mock.patch.object(cp, "get_llm_client", return_value=fake):
            third = cp.monthly_synthesis(self.root, self.YEAR, self.MONTH,
                                        "2026-10-29")
        self.assertFalse(third["_meta"].get("cache_hit"))
        self.assertEqual(len(fake.calls), 2)

    def test_unknown_ids_flag_hold(self):
        def bad_builder(call):
            return {"headline": "x",
                    "trends": [{"claim": "编造", "fact_ids": ["F-DOES-NOT-EXIST"],
                                "source_url": "https://fake.example/x"}]}
        with mock.patch.object(cp, "get_llm_client",
                               return_value=FakeClient(bad_builder)):
            res = cp.monthly_synthesis(self.root, self.YEAR, self.MONTH,
                                       "2026-10-29")
        self.assertEqual(res["status"], "hold_for_review")
        self.assertTrue(res["synthesis"]["_validation"]["unknown_ids"])

    def test_file_without_manifest_is_not_closed(self):
        wk = self.weeks[0]
        (self.root / "data" / "runs" / "periodic" / "weekly" / wk
         / "manifest.json").unlink()
        res = cp.monthly_synthesis(self.root, self.YEAR, self.MONTH,
                                   "2026-10-29", call_llm=False)
        self.assertEqual(res["status"], "degraded")
        self.assertIn(wk, res["gate"]["unverified_weeks"])
        self.assertFalse(res["_meta"]["llm_called"])

    def test_quality_fail_blocks(self):
        wk = self.weeks[0]
        m = self.root / "data" / "runs" / "periodic" / "weekly" / wk / "manifest.json"
        man = json.loads(m.read_text(encoding="utf-8"))
        man["quality_pass"] = False
        m.write_text(json.dumps(man), encoding="utf-8")
        res = cp.monthly_synthesis(self.root, self.YEAR, self.MONTH,
                                   "2026-10-29", call_llm=False)
        self.assertEqual(res["status"], "degraded")

    def test_yearly_requires_twelve_closed_months_and_p14(self):
        build_monthly_fixture(self.root, self.YEAR, list(range(1, 13)))
        for m in range(1, 13):
            pid = f"{self.YEAR}-{m:02d}"
            (cp.CACHE_DIR).mkdir(parents=True, exist_ok=True)
            (cp.CACHE_DIR / f"monthly_result_{pid}.json").write_text(json.dumps({
                "status": "complete", "_meta": {"prompt_version": "v1"},
                "synthesis": {"headline": f"{pid} 综合", "fact_ids": []}}),
                encoding="utf-8")
        builder = lambda call: {"headline": "全年结构判断",
                                "structural_changes": [], "data_gaps": []}
        fake = FakeClient(builder)
        with mock.patch.object(cp, "get_llm_client", return_value=fake):
            res = cp.yearly_synthesis(self.root, self.YEAR, "2027-01-05")
        self.assertEqual(res["status"], "complete", res.get("gate"))
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("12", fake.calls[0]["user"])

    def test_yearly_incomplete_skips_llm(self):
        fake = FakeClient(lambda c: {})
        with mock.patch.object(cp, "get_llm_client", return_value=fake):
            res = cp.yearly_synthesis(self.root, self.YEAR, "2026-07-15")
        self.assertEqual(res["status"], "incomplete")
        self.assertEqual(fake.calls, [])


# ---------------- P1-01 ----------------

class P05PublishGateTest(unittest.TestCase):
    def test_unpublished_topic_fact_rejected_by_schema(self):
        pack = {"period_type": "weekly",
                "period": {"start": "2026-10-19", "end": "2026-10-25"},
                "pack_sha256": "abc",
                "facts": [{"fact_id": "F001", "claim_id": "CL001",
                           "text": "天猫双11开卖，4小时124个品牌破亿。",
                           "date": "2026-10-20", "date_role": "occurred",
                           "status": "announced", "numbers": ["4", "124"],
                           "platforms": ["天猫"], "competitors": [], "event_ids": [],
                           "event_type": "", "source": {"name": "36氪",
                                                       "url": "https://36kr.com/p/1",
                                                       "kind": "media"}},
                          {"fact_id": "F002", "claim_id": "CL002",
                           "text": "丝芙兰玩妆节10月23日在上海举办。",
                           "date": "2026-10-23", "date_role": "occurred",
                           "status": "reported", "numbers": ["10"],
                           "platforms": [], "competitors": ["丝芙兰"], "event_ids": [],
                           "event_type": "", "source": {"name": "美妆头条",
                                                       "url": "https://e.com/a",
                                                       "kind": "media"}}]}
        brief = {"period_type": "weekly", "period_start": "2026-10-19",
                 "period_end": "2026-10-25", "title": "t",
                 "one_line_takeaway": "take",
                 "signals": [{"headline": "h", "fact": "天猫改规则。",
                              "fact_ids": ["F002"], "claim_ids": ["CL002"],
                              "source_refs": [{"name": "美妆头条",
                                               "url": "https://e.com/a",
                                               "supports_claim_ids": ["CL002"]}],
                              "business_impact": "x", "is_inference": True}],
                 "competitor_cards": [], "priority_actions": [],
                 "watch_items": [], "omitted_fact_ids": [],
                 "audit": {"human_review_status": "pending",
                           "source_pack_sha256": "abc"}}
        res = schema.validate_brief(brief, pack, "2026-10-29",
                                    allowed_fact_ids={"F001"})
        codes = {e["code"] for e in res["errors"]}
        self.assertIn("p05_topic_not_published", codes)

    def test_invalid_topic_records_rejection(self):
        plan = {"topics": [{"topic_id": "T1", "source_fact_ids": ["F999"]}]}
        pack = {"facts": [{"fact_id": "F001", "claim_id": "CL001", "text": "x",
                           "source": {"url": "u"}, "status": "reported",
                           "platforms": []}]}
        _validate_topic_plan(plan, pack)
        self.assertTrue(plan["rejected_topics"])
        self.assertEqual(plan["published_fact_ids"], [])


# ---------------- P1-02 ----------------

class ProviderRoutingTest(unittest.TestCase):
    class Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
            self.text = json.dumps(body) if isinstance(body, dict) else str(body)

        def json(self):
            return self._body

    def test_primary_then_single_rightcode_fallback(self):
        ok = {"choices": [{"message": {"content": "hi"}}], "usage": {}}
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append((url, (json or {}).get("model")))
            if "siliconflow" in url:
                return ProviderRoutingTest.Resp(500, {"error": "boom"})
            return ProviderRoutingTest.Resp(200, ok)

        env = {"siliconflow_api_key": "sf-key",
               "rightcode_bybt": "rc-key"}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(lc, "requests") as req:
            req.exceptions = __import__("requests").exceptions
            req.post = fake_post
            client = lc.LLMClient()
            client.max_attempts = 1
            res = client.chat([{"role": "user", "content": "x"}])
        self.assertTrue(res["ok"])
        models = [m for _, m in calls]
        self.assertEqual(calls[0][0].count("siliconflow"), 1)
        rc = [c for c in calls if "right.codes" in c[0]]
        self.assertEqual(len(rc), 1, calls)
        self.assertEqual(rc[0][1], "deepseek-flash")
        self.assertTrue(all(m == "deepseek-ai/DeepSeek-V4.1-Flash"
                            for u, m in calls if "siliconflow" in u), models)

    def test_single_primary_entry_no_rc_in_pool(self):
        with mock.patch.dict(os.environ, {"siliconflow_api_key": "sf",
                                          "rightcode_bybt": "rc"}, clear=False):
            client = lc.LLMClient()
        self.assertEqual([e["name"] for e in client.endpoints], ["siliconflow"])
        self.assertFalse(hasattr(lc, "OPENCODE_BASE_URL"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
