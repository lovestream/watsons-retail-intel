"""Offline AIHOT PostgreSQL schema -> Watsons shadow intake contract tests."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from skills.aihot_bridge.aihot_bridge import canonical_url, select_articles, run, parse_aware


def row(**changes):
    base = {
        "id": "mat001", "identity_key": "url:https://36kr.com/p/123",
        "source_id": "watsons_36kr", "source_name": "36氪",
        "source_tier": "T2", "participation_mode": "isolated",
        "url": "https://36kr.com/p/123?utm_source=rss&from=share",
        "title": "屈臣氏与美团闪购开展个护合作",
        "published_at": "2026-10-30T13:00:00+08:00",
        "publication_time": "exact",
        "discovered_at": "2026-10-30T14:00:00+08:00",
        "updated_at": "2026-10-30T14:01:00+08:00",
        "revision": 1,
        "content_hash": "content1", "excerpt": "双方宣布合作",
        "body_text": "2026年10月30日，屈臣氏与美团闪购宣布开展个护合作。",
        "body_status": "ok", "backfill": False,
        "backfill_reason": None, "author": None,
    }
    base.update(changes)
    return base


class BridgeOfflineTest(unittest.TestCase):
    def test_url_tracking_normalizes_but_material_query_kept(self):
        self.assertEqual(canonical_url("https://EXAMPLE.com/a/?utm_id=x&share=1&id=5"),
                         "https://example.com/a?id=5")

    def test_duplicate_observation_same_material_one_article(self):
        items, invalid = select_articles([
            row(), row(updated_at="2026-10-30T15:00:00+08:00"),
        ], date(2026, 10, 30))
        self.assertEqual((len(items), invalid), (1, 0))
        self.assertEqual(items[0]["article_id"], "AH_mat001")
        self.assertEqual(items[0]["raw"]["aihot_revision"], 1)

    def test_revision_updates_content_without_new_article_id(self):
        items, invalid = select_articles([
            row(), row(revision=2, body_text="修订后的原文",
                       updated_at="2026-10-31T08:00:00+08:00"),
        ], date(2026, 10, 31))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "修订后的原文")
        self.assertEqual(items[0]["raw"]["aihot_revision"], 2)

    def test_missing_fulltext_never_invented(self):
        items, _ = select_articles([
            row(body_text=None, body_status="pending", excerpt="摘要提供的信息"),
        ], date(2026, 10, 30))
        self.assertEqual(items[0]["summary"], "摘要提供的信息")
        self.assertEqual(items[0]["content"], "")
        self.assertFalse(items[0]["raw"]["body_provided_by_original"])

    def test_missing_published_time_remains_unknown(self):
        items, _ = select_articles([
            row(published_at=None, publication_time=None),
        ], date(2026, 10, 30))
        self.assertEqual(items[0]["published_at"], "")
        self.assertEqual(items[0]["time_status"], "unknown_time")

    def test_article_published_old_not_renewed_by_recent_discovery(self):
        items, _ = select_articles([
            row(published_at="2026-10-01T10:00:00+08:00",
                discovered_at="2026-10-30T10:00:00+08:00"),
        ], date(2026, 10, 30))
        self.assertEqual(items[0]["time_status"], "old")
        self.assertEqual(items[0]["freshness_status"], "bootstrap_seen")

    def test_invalid_url_skips(self):
        _, invalid = select_articles([row(url="file:///etc/passwd")],
                                     date(2026, 10, 30))
        self.assertEqual(invalid, 1)

    def test_fixture_export_scoped_and_shadow_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "data.json"
            p.write_text(json.dumps([row(), row(id="ignored", source_id="ai_demo")]),
                         encoding="utf-8")
            class Opt:
                as_of = "2026-10-30"
                since = "2026-10-30T00:00:00+08:00"
                until = "2026-11-01T00:00:00+08:00"
                source_id = ["watsons_36kr"]
                max_rows = 100
                fixture = str(p)
                write_shadow = True
                output_dir = str(Path(d) / "watsons-shadow")
            m = run(Opt)
            self.assertEqual(m["article_count"], 1)
            out = json.loads((Path(Opt.output_dir) / "raw_articles_all.json").read_text())
            self.assertEqual(len(out["articles"]), 1)
            self.assertEqual(out["articles"][0]["source_type"], "aihot_raw")
            self.assertFalse((Path(d) / "data" / "raw").exists())

    def test_unsafe_export_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            p.write_text(json.dumps([row()]), encoding="utf-8")
            class Opt:
                as_of = "2026-10-30"
                since = "2026-10-30T00:00:00+08:00"
                until = "2026-11-01T00:00:00+08:00"
                source_id = ["watsons_36kr"]
                max_rows = 100
                fixture = str(p)
                write_shadow = True
                output_dir = str(Path(d) / "production")
            with self.assertRaisesRegex(ValueError, "shadow"):
                run(Opt)

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            parse_aware("2026-10-30 12:00:00")


if __name__ == "__main__":
    unittest.main()
