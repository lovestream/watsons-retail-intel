"""reader_brief 最小回归测试（与当前 schema/renderer 对齐，R3 整改）。

运行：PYTHONPATH=".venv_packages:." python3 tests/reader_brief/run_tests.py
"""

import json
import re
import shutil
import tempfile
from pathlib import Path

from skills.reader_brief import facts_pack, render_mobile, schema


def _pack():
    return {"period_type": "weekly",
            "period": {"start": "2026-10-19", "end": "2026-10-25"},
            "pack_sha256": "abc123",
            "facts": [{"fact_id": "F001", "claim_id": "CL001",
                       "text": "10月20日天猫双11开卖，4小时124个品牌成交破亿。",
                       "date": "2026-10-20", "date_role": "occurred",
                       "status": "announced", "numbers": ["10", "20", "4", "124"],
                       "platforms": ["天猫"], "competitors": [], "event_ids": [],
                       "event_type": "", "source": {"name": "36氪",
                                                   "url": "https://example.com/a",
                                                   "kind": "media"}}]}


def _brief(**over):
    b = {"period_type": "weekly", "period_start": "2026-10-19",
         "period_end": "2026-10-25", "title": "双11改官方立减",
         "one_line_takeaway": "天猫把凑单改成官方立减，屈臣氏需重算单件折扣。",
         "signals": [{"headline": "双11改官方立减",
                      "fact": "天猫把凑单改成官方立减。",
                      "fact_ids": ["F001"], "claim_ids": ["CL001"],
                      "source_refs": [{"name": "36氪",
                                       "url": "https://example.com/a",
                                       "supports_claim_ids": ["CL001"]}],
                      "business_impact": "满减连带失效", "is_inference": True}],
         "competitor_cards": [],
         "priority_actions": [{"title": "重算折扣带",
                              "precondition": "需先取得官方细则",
                              "trial": "选少量SKU做两版测算",
                              "metric": "毛利率", "linked_fact_ids": ["F001"],
                              "due_date": "2026-11-05"}],
         "watch_items": [{"trigger": "天猫公布类目细则",
                          "business_relevance": "影响美妆折扣结构",
                          "fact_ids": ["F001"]}],
         "omitted_fact_ids": [],
         "audit": {"human_review_status": "pending",
                   "source_pack_sha256": "abc123"}}
    b.update(over)
    return b


def test_status_inference_prefers_reported_marker():
    assert facts_pack.infer_status("据报道，某平台拟拓仓") == "reported"
    assert facts_pack.infer_status("某公司宣布上线新服务") == "announced"


def test_facts_pack_from_events_is_traceable():
    assert not hasattr(facts_pack, "from_synthesis"), \
        "旧 from_synthesis 已移除：事实包必须来自 events 原始记录"
    tmp = Path(tempfile.mkdtemp())
    try:
        day = tmp / "2026-10-20"
        day.mkdir(parents=True)
        (day / "events_analyzed.json").write_text(json.dumps([{
            "event_id": "E1", "fact_id": "E1", "claim_id": "E1",
            "event_title": "天猫双11开卖，4小时124个品牌成交破亿。",
            "source_url": "https://example.com/a", "source_name": "36氪",
            "status": "reported", "date_role": "occurred"}], ensure_ascii=False),
            encoding="utf-8")
        pack = facts_pack.build_from_events(str(tmp), "2026-10-19", "2026-10-25")
        assert pack["facts"], pack
        f = pack["facts"][0]
        assert f["source"]["url"] == "https://example.com/a"
        assert f["fact_id"] and f["claim_id"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_good_brief_passes():
    res = schema.validate_brief(_brief(), _pack(), "2026-10-29")
    assert res["pass"], res["errors"]


def test_unknown_fact_id_rejected():
    b = _brief()
    b["signals"][0]["fact_ids"] = ["F999"]
    codes = {e["code"] for e in schema.validate_brief(b, _pack(), "2026-10-29")["errors"]}
    assert "unknown_fact_id" in codes or "fact_not_in_pack" in codes


def test_number_not_in_fact_rejected():
    b = _brief()
    b["signals"][0]["fact"] = "天猫双11开卖，9小时999个品牌破亿。"
    codes = {e["code"] for e in schema.validate_brief(b, _pack(), "2026-10-29")["errors"]}
    assert codes & {"number_not_in_fact", "unsupported_number"}


def test_expired_deadline_rejected():
    b = _brief()
    b["priority_actions"][0]["due_date"] = "2026-10-01"
    codes = {e["code"] for e in schema.validate_brief(b, _pack(), "2026-10-29")["errors"]}
    assert codes & {"expired_deadline", "action_deadline_expired"}


def test_event_id_leak_rejected():
    b = _brief()
    b["signals"][0]["headline"] = "E20260530_0037 双11改规则"
    codes = {e["code"] for e in schema.validate_brief(b, _pack(), "2026-10-29")["errors"]}
    assert "event_id_leak" in codes


def test_render_has_no_internal_markers_and_single_column():
    html = render_mobile.render(_brief(), _pack(), "weekly",
                                "2026-10-19", "2026-10-25",
                                generated_at="2026-10-29 09:00")
    assert "<table" not in html
    assert "display:flex" not in html
    assert not re.search(r"\bE\d{8}_\d{3,4}\b", html)
    assert "max-width:600px" in html
    assert "example.com" in html
    assert "已核验" not in html


def test_render_audio_note_only_when_given():
    html = render_mobile.render(_brief(), _pack(), "weekly", "2026-10-19",
                                "2026-10-25")
    assert "口播音频" not in html
    html2 = render_mobile.render(_brief(), _pack(), "weekly", "2026-10-19",
                                 "2026-10-25", audio_note="附本期口播音频（sha256 x）")
    assert "口播音频" in html2


def test_period_labels_differ():
    w = render_mobile.render(_brief(), _pack(), "weekly", "W")
    m = render_mobile.render(_brief(period_type="monthly"), _pack(), "monthly", "M")
    y = render_mobile.render(_brief(period_type="yearly"), _pack(), "yearly", "Y")
    assert "周报" in w and "月报" in m and "年报" in y
