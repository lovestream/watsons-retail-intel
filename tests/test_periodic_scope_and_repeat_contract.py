"""P0: business scope cannot be bypassed by periodic facts or repeat counts.

Offline regression fixtures; no NAS, LLM, internet or history modifications.
"""
import json
import tempfile
from pathlib import Path

from skills.periodic_synthesis.periodic_synthesis import (
    _is_archive_event, cluster_events, detect_trend_direction,
)
from skills.reader_brief.facts_pack import build_from_events, build_from_synthesis


def good_event(**over):
    ev = {
        "event_id": "E1",
        "event_title": "屈臣氏携手美团闪购试点个护到家",
        "fact": "屈臣氏携手美团闪购试点个护到家",
        "event_type": "platform_move",
        "source_url": "https://www.36kr.com/p/12345?utm_source=abc",
        "source_name": "36kr",
        "entities": {"platforms": ["美团闪购"], "companies": ["屈臣氏"], "brands": []},
        "effective_event_time": "2026-10-27",
        "collected_at": "2026-10-27T08:00:00+08:00",
        "weighted_score": 4.3,
        "priority": "P1",
        "published_at": "2026-10-27",
    }
    ev.update(over)
    return ev


def test_icecream_archive_never_reenter_periodic():
    e = good_event(
        event_title="野人先生冰淇淋品牌开业",
        fact="野人先生开冰激凌门店",
        priority="ARCHIVE",
        downgrade_reasons=["republished_old_fact"],
        scores={"watsons_relevance": 5},
    )
    assert _is_archive_event(e)


def test_recruitment_and_stocks_never_reenter_periodic():
    for title in ("美妆公司高薪招聘CHO", "美妆上市公司股价涨停"):
        e = good_event(event_title=title, fact=title,
                       priority="ARCHIVE",
                       downgrade_reasons=["republished_old_fact"],
                       scores={"watsons_relevance": 5})
        assert _is_archive_event(e)


def test_verified_watsons_move_is_not_scope_rejected():
    assert not _is_archive_event(good_event())


def test_repeated_23_same_source_does_not_create_23_independent_sources():
    same = [
        good_event(event_id=f"E{i}", collected_at=f"2026-10-{(i % 23) + 1:02d}T09:00:00+08:00")
        for i in range(23)
    ]
    groups = cluster_events(same, {"clustering": {"title_jaccard_threshold": 0.65,
                                               "entity_overlap_threshold": 0.6}})
    assert len(groups) == 1
    g = groups[0]
    assert g["observation_count"] == 23
    assert g["distinct_source_article_count"] == 1
    assert g["independent_source_count"] == 1
    assert detect_trend_direction(g, [], {"trend": {"min_days_active": 3,
                                                  "min_source_count": 3}}) == "unclear"


def test_monthly_fact_pack_drops_out_of_scope_legacy_events():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "2026-10-27"
        d.mkdir()
        entries = [
            good_event(event_id="E1"),
            good_event(event_id="E2", event_title="野人先生冰淇淋开店",
                       fact="野人先生冰淇淋开店", priority="P1"),
        ]
        (d / "events_analyzed.json").write_text(
            json.dumps(entries, ensure_ascii=False), encoding="utf-8"
        )
        pack = build_from_events(tmp, "2026-10-01", "2026-10-31")
        titles = [f.get("text", "") for f in pack["facts"]]
        assert any("屈臣氏" in x for x in titles)
        assert not any("野人先生" in x for x in titles)


def test_weekly_fact_pack_drops_out_of_scope_synthesis():
    synth = {
        "period": {"start": "2026-10-26", "end": "2026-11-01"},
        "all_clusters": [
            {"theme": "野人先生冰淇淋开店",
             "facts": ["野人先生冰淇淋开店"],
             "source_urls": ["https://example.com/icecream"],
             "sources": ["example"], "event_ids": ["junk"]},
            {"theme": "屈臣氏携手美团闪购试点个护到家",
             "facts": ["屈臣氏携手美团闪购试点个护到家"],
             "source_urls": ["https://example.com/watsons"],
             "sources": ["example"], "event_ids": ["E1"]},
        ],
    }
    pack = build_from_synthesis(synth, "weekly")
    titles = [f.get("text", "") for f in pack["facts"]]
    assert any("屈臣氏" in x for x in titles)
    assert not any("野人先生" in x for x in titles)
