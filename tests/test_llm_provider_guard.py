#!/usr/bin/env python3
"""回归测试：2026-10 采集失败真因 + 2026-10-30 起只允许 RightCode。

约束：
- 不得出现已下线模型名（deepseek-v4-flash-free 等）
- 主 Provider 唯一为 RightCode；SiliconFlow 已停用（余额耗尽、key 移除）
- 抽取默认关闭 thinking
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEAD = ("deepseek-v4-flash-free", "claude-opus-4-8")
MODEL = "deepseek-flash"


class LlmProviderGuardTest(unittest.TestCase):
    def test_no_dead_model_in_config_and_skills(self):
        offenders = []
        for base in ("config", "skills"):
            for p in (ROOT / base).rglob("*"):
                if p.suffix not in (".py", ".yaml", ".yml", ".json") or not p.is_file():
                    continue
                if "venv" in str(p) or "__pycache__" in str(p):
                    continue
                if p.name == "llm_client.py":
                    continue  # DEAD_MODEL_NAMES 白名单常量必须包含这些名字
                txt = p.read_text(encoding="utf-8", errors="ignore")
                for d in DEAD:
                    if d in txt:
                        offenders.append(f"{p.relative_to(ROOT)}:{d}")
        self.assertEqual(offenders, [], offenders)

    def test_provider_is_rightcode_only(self):
        from skills.utils import llm_client
        self.assertEqual(llm_client.DEFAULT_MODEL, MODEL)
        self.assertIn("api.site", llm_client.DEFAULT_BASE_URL)
        self.assertNotIn("siliconflow", llm_client.DEFAULT_BASE_URL.lower())

    def test_no_siliconflow_dependency(self):
        """停用 SF 后不得再要求 siliconflow_api_key，否则无 Key 就静默降级。"""
        src = (ROOT / "skills/utils/llm_client.py").read_text(encoding="utf-8")
        start = src.index("def _load_provider_endpoints")
        end = src.index("def _load_opencode_keys")
        loader = src[start:end]
        self.assertIn("FALLBACK_KEY_ENV", loader)
        self.assertNotIn("SILICONFLOW_KEY_ENV", loader)

    def test_extract_defaults_thinking_off(self):
        src = (ROOT / "skills/extract_events/extract_events.py").read_text(encoding="utf-8")
        self.assertIn("_ENABLE_THINKING", src)
        self.assertIn("enable_thinking=_ENABLE_THINKING", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
