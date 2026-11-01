"""P0 temporal semantics: tests must run without LLM, network, NAS or SMTP."""
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from skills.temporal_fact_validation.date_contract import (
    exact_day, day_candidates, trustworthy_occurred_day
)
from skills.temporal_fact_validation.temporal_fact_validation import (
    extract_content_dates, validate_event_times, article_date_sanity_check
)
from skills.reader_brief import facts_pack

CST = timezone(timedelta(hours=8))
RUN = datetime(2026, 11, 2, 10, 0, tzinfo=CST)


def event(day="", evidence="", precision="unknown", confidence="unknown",
          published="2026-10-25", **kw):
    result = {
        "event_occurred_at": day,
        "event_time_precision": precision,
        "event_time_confidence": confidence,
        "evidence_text": evidence,
        "published_at": published,
    }
    result.update(kw)
    return result


def check(ev):
    return validate_event_times(ev, {"published_at_parsed": ev.get("published_at", "")},
                                extract_content_dates(ev.get("evidence_text", ""), RUN),
                                RUN)


def test_T01_yesterday_without_original_published_anchor_is_unknown():
    out = extract_content_dates("昨天宣布合作", RUN)
    assert not out["relative_dates"]
    assert out["relative_expressions"][0]["resolved"] == ""


def test_T02_yesterday_anchored_to_article_not_run_date():
    a = extract_content_dates("昨天宣布合作", RUN, "2026-10-25")
    b = extract_content_dates("昨天宣布合作", RUN + timedelta(days=25), "2026-10-25")
    assert a["relative_dates"] == b["relative_dates"]
    assert a["relative_expressions"][0]["resolved"] == "2026-10-24"


def test_T03_today_is_publication_date_not_execution_date():
    x = extract_content_dates("今天发布新规", RUN, "2026-10-25")
    assert x["relative_expressions"][0]["resolved"] == "2026-10-25"


def test_T04_hours_ago_with_day_only_anchor_not_day_precision():
    x = extract_content_dates("3小时前发布", RUN, "2026-10-25")
    assert x["relative_expressions"][0]["precision"] == "unknown"
    assert not x["relative_dates"]


def test_T05_no_latest_date_becomes_occurred():
    x = event(evidence="10月20日宣布，11月11日计划实施", published="2026-10-25")
    out = check(x)
    assert out["event_occurred_at"] == ""
    assert out["event_announced_at"] == ""


def test_T06_published_does_not_automatically_become_announced():
    x = event(published="2026-10-25", evidence="某平台计划新政策")
    assert check(x)["event_announced_at"] == ""


def test_T07_future_effective_role_kept_out_of_occurred():
    x = event(day="2026-11-11", precision="day", confidence="high",
              evidence="11月11日计划生效", event_effective_at="2026-11-11")
    out = check(x)
    assert out["event_occurred_at"] == ""
    assert out["event_effective_at"] == "2026-11-11"


def test_T08_quarter_precision_with_fake_day_is_not_occurred():
    x = event(day="2026-10-01", precision="quarter", confidence="high",
              evidence="2026年第四季度销售额")
    out = check(x)
    assert out["event_occurred_at"] == ""
    assert out["event_time_precision"] == "quarter"
    assert "occurred_precision_mismatch" in out["temporal_issues"]


def test_T09_month_is_not_month_first():
    x = extract_content_dates("2026年10月销售情况", RUN)
    assert not x["absolute_dates"]
    assert x["month_mentions"][0]["value"] == "2026-10"
    assert x["month_mentions"][0]["precision"] == "month"


def test_T10_unknown_precision_does_not_promote_day():
    x = event(day="2026-10-20", confidence="high", evidence="2026年10月20日平台新店开业")
    assert trustworthy_occurred_day(x) is None
    assert check(x)["event_occurred_at"] == ""


def test_T11_low_confidence_does_not_promote_day():
    x = event(day="2026-10-20", precision="day", confidence="low",
              evidence="2026年10月20日平台新店开业")
    assert trustworthy_occurred_day(x) is None


def test_T12_precise_source_evidence_can_be_retained():
    x = event(day="2026-10-20", precision="day", confidence="high",
              evidence="2026年10月20日，平台新店开业")
    assert trustworthy_occurred_day(x) == "2026-10-20"
    assert check(x)["event_occurred_at"] == "2026-10-20"


def test_T13_wrong_source_date_is_not_evidence():
    x = event(day="2026-10-21", precision="day", confidence="high",
              evidence="2026年10月20日新店开业")
    assert trustworthy_occurred_day(x) is None


def test_T14_invalid_calendar_day_rejected():
    assert exact_day("2026-02-30") is None
    x = event(day="2026-02-30", precision="day", confidence="high",
              evidence="2026年2月30日开业")
    assert "occurred_not_exact_day" in check(x)["temporal_issues"]


def test_T15_spoofed_model_temporal_quote_not_original_evidence():
    x = event(day="2026-10-20", precision="day", confidence="high",
              evidence="某企业开业", temporal_evidence="2026年10月20日已开业")
    assert trustworthy_occurred_day(x) is None


def test_T16_multi_date_quote_needs_claim_disambiguation():
    x = event(day="2026-10-20", precision="day", confidence="high",
              evidence="10月20日宣布、11月11日执行")
    assert trustworthy_occurred_day(x) is None


def test_T17_published_is_not_day_precision():
    x = event(day="2026-10-20", precision="published", confidence="high",
              evidence="2026年10月20日平台开业")
    assert not facts_pack.occurrences_ok(x)
    assert "published" not in facts_pack.PRECISE_DAY_LEVELS


def test_T18_legacy_occurrence_without_evidence_is_not_trusted():
    x = event(day="2026-10-20", precision="day", confidence="medium")
    assert not facts_pack.occurrences_ok(x)


def test_T19_monthly_facts_pack_does_not_relabel_bad_precision():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "2026-10-20"
        d.mkdir()
        ev = event(day="2026-10-20", precision="quarter", confidence="high",
                   evidence="第四季度计划", source_url="https://example.com/a",
                   source_name="官网", fact="某平台第四季度拟开店", event_id="E1")
        (d / "events_analyzed.json").write_text(
            json.dumps({"events": [ev]}, ensure_ascii=False), encoding="utf-8"
        )
        pack = facts_pack.build_from_events(tmp, "2026-10-19", "2026-10-25")
        assert pack["facts"]
        assert all(f["date_role"] != "occurred" for f in pack["facts"])


def test_T20_cross_event_cluster_has_no_arbitrary_first_occurred_date():
    with tempfile.TemporaryDirectory() as tmp:
        for i, when in enumerate(("2026-10-20", "2026-10-21"), 1):
            d = Path(tmp) / f"2026-10-2{i-1}"
            d.mkdir()
            ev = event(day=when, precision="day", confidence="high",
                       evidence=f"{when}平台新店开业", event_id=f"E{i}")
            (d / "events_analyzed.json").write_text(
                json.dumps({"events": [ev]}, ensure_ascii=False), encoding="utf-8"
            )
        synth = {
            "period": {"start": "2026-10-19", "end": "2026-10-25"},
            "all_clusters": [{
                "event_ids": ["E1", "E2"],
                "facts": ["平台本周拓展门店"],
                "source_urls": ["https://example.com/a"],
                "sources": ["官方"]
            }]
        }
        pack = facts_pack.build_from_synthesis(synth, "weekly", events_dir=tmp)
        assert pack["facts"]
        assert all(f["date_role"] != "occurred" for f in pack["facts"])


def test_T21_cross_year_yesterday_is_stable():
    x = extract_content_dates("昨天发布", RUN, "2027-01-01")
    assert x["relative_expressions"][0]["resolved"] == "2026-12-31"


def test_T22_unverified_source_quote_never_accepted():
    x = event(day="2026-10-20", precision="day", confidence="high",
              evidence="2026年10月20日新店开业", evidence_verified=False)
    assert trustworthy_occurred_day(x) is None


def test_T23_multi_date_original_with_scoped_occurrence_can_pass():
    quote = "2026年10月20日新店开业，同时公告11月11日促销计划"
    x = event(day="2026-10-20", precision="day", confidence="high",
              evidence=quote, temporal_role_evidence=[
                  {"role": "occurred", "value": "2026-10-20",
                   "quote": "2026年10月20日新店开业"}])
    assert trustworthy_occurred_day(x) == "2026-10-20"


def test_T24_prompt_builds_without_format_interpolation_or_early_truncation():
    from skills.extract_events.extract_events import (
        _build_extraction_prompt, _context_with_late_date_evidence
    )
    content = "市场背景。" * 1000 + "2026年10月20日平台新店开业。" + "其他内容。" * 100
    selected = _context_with_late_date_evidence(content)
    assert "2026年10月20日平台新店开业" in selected
    assert len(selected) <= 4200
    system, user_prompt = _build_extraction_prompt({
        "article_id": "test-p0", "title": "开业", "content": content
    })
    assert "temporal_role_evidence" in user_prompt
    assert "2026年10月20日平台新店开业" in user_prompt
    assert isinstance(system, str)
