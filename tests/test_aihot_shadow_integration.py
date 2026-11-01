"""Offline end-to-end source-shaped fixture -> bridge -> Watsons shadow contract.

These tests never contact PostgreSQL, external sources, a model, or SMTP.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from skills.aihot_bridge.aihot_bridge import run
from scripts.import_aihot_shadow_to_watsons import prepare
from scripts.preview_aihot_scope_shadow import preview


class BridgeShadowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.stage = self.base / "aihot-shadow"
        self.project = self.base / "watsons-shadow"
        (self.project / "skills" / "filter_relevant_articles").mkdir(parents=True)
        self.fixture = Path(__file__).parent / "fixtures" / "aihot_articles.json"

    def options(self, write=True):
        return SimpleNamespace(
            as_of="2026-10-31",
            since="2026-10-30T00:00:00+08:00",
            until="2026-11-01T00:00:00+08:00",
            source_id=["wt_36kr_watsons"],
            max_rows=50,
            fixture=str(self.fixture),
            write_shadow=write,
            output_dir=str(self.stage),
        )

    def test_fixture_to_native_input_and_business_gate(self):
        manifest = run(self.options())
        self.assertEqual(manifest["article_count"], 2)
        self.assertTrue(manifest["not_a_production_run"])
        result = prepare(self.stage, self.project, "2026-10-31")
        self.assertEqual(result["article_count"], 2)
        self.assertEqual(result["scope_preview_counts"]["main"], 1)
        self.assertEqual(result["scope_preview_counts"]["reject"], 1)
        native = self.project / "data/raw/2026-10-31/raw_articles_all.json"
        articles = json.loads(native.read_text(encoding="utf-8"))
        self.assertEqual(len(articles["articles"]), 2)
        p = preview(articles)
        self.assertEqual(p["stable_articles"], 2)
        self.assertEqual(p["counts"], {"main": 1, "reject": 1})
        self.assertEqual(p["llm_calls"], 0)

    def test_native_input_is_not_silently_overwritten(self):
        run(self.options())
        prepare(self.stage, self.project, "2026-10-31")
        with self.assertRaises(FileExistsError):
            prepare(self.stage, self.project, "2026-10-31")

    def test_wrong_target_day_fails_before_writes(self):
        run(self.options())
        with self.assertRaises(ValueError):
            prepare(self.stage, self.project, "2026-10-30")
        self.assertFalse((self.project / "data").exists())

    def test_explicit_export_flag_is_required(self):
        manifest = run(self.options(write=False))
        self.assertEqual(manifest["article_count"], 2)
        self.assertFalse(self.stage.exists())

    def test_import_refuses_nonshadow_root(self):
        run(self.options())
        unsafe = self.base / "production"
        (unsafe / "skills/filter_relevant_articles").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "shadow"):
            prepare(self.stage, unsafe, "2026-10-31")


if __name__ == "__main__":
    unittest.main()
