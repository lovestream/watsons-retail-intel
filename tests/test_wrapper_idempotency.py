#!/usr/bin/env python3
"""回归测试：包装器不得因执行期异常整轮重跑；抽取必须支持断点续跑。

背景（2026-10-30）：extract 步骤 import 后业务函数抛 `'str' object has no
attribute 'get'`，包装器 catch Exception 后无条件 subprocess 重跑整轮，
导致 43 篇文章的 LLM 抽取被重复执行一次（约 19 分钟、43 次多余调用）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.run_daily_pipeline import run_daily_pipeline as rdp  # noqa: E402
from skills.utils import parallel_runner as pr  # noqa: E402


class StepDispatchTest(unittest.TestCase):
    """导入失败可降级；执行失败绝不可重跑。"""

    def test_import_failure_falls_back_to_subprocess(self):
        with mock.patch.object(rdp, "call_step_by_import",
                               side_effect=rdp.StepImportError("no module")):
            with mock.patch.object(rdp, "call_step_by_subprocess",
                                   return_value={"ok": True}) as sub:
                res = rdp.call_step("extract", {"project_root": ".", "date": "2026-10-31"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["_call_method"], "subprocess")
        sub.assert_called_once()

    def test_execution_failure_does_not_rerun(self):
        with mock.patch.object(rdp, "call_step_by_import",
                               side_effect=rdp.StepExecutionError("boom after llm")):
            with mock.patch.object(rdp, "call_step_by_subprocess") as sub:
                res = rdp.call_step("extract", {"project_root": ".", "date": "2026-10-31"})
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("execution_failed_after_start"))
        self.assertIn("boom after llm", res["errors"][0])
        sub.assert_not_called()  # 关键：不得重跑

    def test_import_stage_and_exec_stage_are_distinguished(self):
        """模块能导入但函数抛错时，必须是 StepExecutionError 而非 StepImportError。"""
        fake_mod = mock.Mock()
        fake_mod.some_step = mock.Mock(side_effect=ValueError("inside function"))
        with mock.patch.object(rdp, "STEP_DEFS", {
                "extract": {"module": "fake.module", "function": "some_step",
                            "cli": "x.py"}}):
            with mock.patch("importlib.import_module", return_value=fake_mod):
                with self.assertRaises(rdp.StepExecutionError):
                    rdp.call_step_by_import("extract", {})

    def test_missing_attribute_is_import_stage(self):
        fake_mod = mock.Mock(spec=[])  # 没有任何属性
        with mock.patch.object(rdp, "STEP_DEFS", {
                "extract": {"module": "fake.module", "function": "nope",
                            "cli": "x.py"}}):
            with mock.patch("importlib.import_module", return_value=fake_mod):
                with self.assertRaises(rdp.StepImportError):
                    rdp.call_step_by_import("extract", {})


class CheckpointResumeTest(unittest.TestCase):
    """逐篇 checkpoint：中断后只跑未完成文章。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ckpt = os.path.join(self.tmp, "ckpt.jsonl")

    def test_save_and_load_roundtrip(self):
        pr.save_checkpoint(self.ckpt, "art_1", {"event_count": 3})
        pr.save_checkpoint(self.ckpt, "art_2", {"event_count": 0})
        got = pr.load_checkpoint(self.ckpt)
        self.assertEqual(set(got), {"art_1", "art_2"})
        self.assertEqual(got["art_1"]["event_count"], 3)

    def test_resume_skips_completed(self):
        pr.save_checkpoint(self.ckpt, "art_1", {"ok": True})
        done = pr.load_checkpoint(self.ckpt)
        articles = [{"article_id": "art_1"}, {"article_id": "art_2"}]
        pending = [a for a in articles if a["article_id"] not in done]
        self.assertEqual([a["article_id"] for a in pending], ["art_2"])

    def test_duplicate_save_keeps_latest(self):
        pr.save_checkpoint(self.ckpt, "art_1", {"v": 1})
        pr.save_checkpoint(self.ckpt, "art_1", {"v": 2})
        got = pr.load_checkpoint(self.ckpt)
        self.assertEqual(got["art_1"]["v"], 2)

    def test_extract_has_resume_switch(self):
        src = (ROOT / "skills/extract_events/extract_events.py").read_text(encoding="utf-8")
        self.assertIn("EXTRACT_RESUME", src)
        self.assertIn("force: bool = False", src)
        # 不允许无条件清除 checkpoint
        self.assertNotIn("        # 清除旧 checkpoint\n        clear_checkpoint",
                         src)


class LlmCallBudgetTest(unittest.TestCase):
    """同一篇文章在一次运行内最多 2 次 LLM 调用（首次 + 一次重试）。"""

    def test_no_duplicate_llm_calls_on_single_article(self):
        calls = {"n": 0}

        def fake_chat(**kwargs):
            calls["n"] += 1
            return {"ok": True, "content": "{}", "parsed": {}, "model": "deepseek-flash"}

        client = mock.Mock()
        client.chat.side_effect = fake_chat
        from skills.extract_events import extract_events as ee
        article = {"article_id": "a1", "title": "t", "content": "正文内容" * 50,
                   "source_url": "https://example.com/a1", "publish_time": "2026-10-30"}
        for _ in range(2):
            try:
                ee.llm_extract_events(article, client, mock.Mock(), model="deepseek-flash")
            except Exception:
                pass
        self.assertLessEqual(calls["n"], 4)  # 每次最多 1 次（传 {}) + 余量


if __name__ == "__main__":
    unittest.main(verbosity=2)
