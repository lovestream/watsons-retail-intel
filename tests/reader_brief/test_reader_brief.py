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


# Delivery-level claim / source / time contracts (2026-10-31).
def _codes(b, p=None):
    return {e["code"] for e in schema.validate_brief(
        b, p or _pack(), "2026-10-29")["errors"]}


def test_missing_signal_claim_binding_blocks():
    b = _brief()
    del b["signals"][0]["claim_ids"]
    assert "missing_claim_binding" in _codes(b)


def test_valid_but_wrong_claim_id_blocks():
    p = _pack()
    another = dict(p["facts"][0], fact_id="F002", claim_id="CL002")
    another["source"] = {"url": "https://example.com/b", "name": "媒体", "kind": "media"}
    p["facts"].append(another)
    b = _brief()
    b["signals"][0]["claim_ids"] = ["CL002"]
    assert "claim_fact_mismatch" in _codes(b, p)


def test_missing_source_claim_binding_blocks():
    b = _brief()
    b["signals"][0]["source_refs"][0].pop("supports_claim_ids")
    assert "missing_source_claim_binding" in _codes(b)


def test_source_claim_url_cross_pair_blocks():
    p = _pack()
    p["facts"].append(dict(p["facts"][0], fact_id="F002", claim_id="CL002",
                           source={"name": "媒体", "url": "https://example.com/b", "kind": "media"}))
    b = _brief()
    b["signals"][0]["source_refs"][0]["supports_claim_ids"] = ["CL002"]
    assert "supports_unbound" in _codes(b, p)


def test_missing_competitor_claim_binding_blocks():
    b = _brief()
    b["competitor_cards"] = [{"name": "某品牌", "move": "10月20日开展线下活动",
                              "occurred_on": "2026-10-20", "why_matters": "参考",
                              "fact_ids": ["F001"],
                              "source_refs": [{"url": "https://example.com/a",
                                               "supports_claim_ids": ["CL001"]}]}]
    assert "missing_claim_binding" in _codes(b)


def test_competitor_publication_date_not_occurrence_blocks():
    p = _pack()
    p["facts"][0]["date_role"] = "published"
    p["facts"][0]["date"] = "2026-10-25"
    # A publication date is not evidence of a completed event on that date.
    b = _brief()
    b["competitor_cards"] = [{"name": "某品牌", "move": "某品牌发布预告",
                              "occurred_on": "2026-10-25", "why_matters": "观察",
                              "fact_ids": ["F001"], "claim_ids": ["CL001"],
                              "source_refs": [{"url": "https://example.com/a",
                                               "supports_claim_ids": ["CL001"]}]}]
    assert "occurred_date_unbound" in _codes(b, p)


def test_period_identity_change_blocks():
    b = _brief()
    b["period_start"] = "2026-10-12"
    assert "period_identity_mismatch" in _codes(b)


def test_mixed_completed_and_reported_overclaim_blocks():
    p = _pack()
    p["facts"][0]["status"] = "completed"
    p["facts"].append(dict(p["facts"][0], fact_id="F002", claim_id="CL002",
                           status="reported"))
    b = _brief()
    s = b["signals"][0]
    s["fact_ids"] = ["F001", "F002"]
    s["claim_ids"] = ["CL001", "CL002"]
    s["source_refs"][0]["supports_claim_ids"] = ["CL001", "CL002"]
    s["fact"] = "项目已落地，覆盖范围需后续核实。"
    assert "status_overclaim" in _codes(b, p)
