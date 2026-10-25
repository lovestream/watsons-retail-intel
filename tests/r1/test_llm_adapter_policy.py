#!/usr/bin/env python3
"""R1 adapter policy tests — deterministic, no network, no real credentials."""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from skills.utils.llm_pipeline_adapter import (  # noqa: E402
    CredentialRef, LLMConfigError, PipelineLLMClient,
)


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.text = text if text is not None else json.dumps(body or {}, ensure_ascii=False)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def ok_body(content, model="deepseek-ai/DeepSeek-V4.1-Flash", prompt=100, completion=50):
    return {
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion},
    }


def make_config(primary="siliconflow", fallback=None, allow_fallback=True,
                max_transient_retries=2, max_process=200, max_task=6):
    return {
        "policy": {
            "primary": primary,
            "fallback": fallback or [],
            "allow_fallback": allow_fallback,
            "max_transient_retries": max_transient_retries,
            "max_empty_content_retries": 1,
            "max_http_requests_per_task": max_task,
            "max_http_requests_per_process": max_process,
            "wall_clock_seconds": 60,
            "request_timeout_seconds": 5,
            "disabled_providers": ["opencode", "gpt-5.4-xhigh"],
        },
        "providers": {
            "siliconflow": {
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "deepseek-ai/DeepSeek-V4.1-Flash",
                "credential": {"env": "R1_SF_KEY"},
                "json_mode": True,
            },
            "rightcode-bybt": {
                "base_url": "https://rightapi.ai/v1",
                "model": "deepseek-flash",
                "credential": {"env": "R1_RC_KEY"},
                "json_mode": True,
            },
        },
        "model_aliases": {"pipeline-deepseek-flash": "siliconflow"},
    }


def client(config=None, responses=None, **kwargs):
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        item = responses.pop(0) if responses else FakeResponse(500, {"error": "empty queue"})
        if isinstance(item, Exception):
            raise item
        return item

    cfg = config or make_config()
    c = PipelineLLMClient(
        config=cfg, http_post=post, sleep=lambda s: None,
        credential_overrides=kwargs.pop("credential_overrides", {
            "siliconflow": "test-sf-secret", "rightcode-bybt": "test-rc-secret"}),
        **kwargs)
    c._test_calls = calls
    return c


class AdapterPolicyTests(unittest.TestCase):
    def test_400_model_unavailable_fails_fast_no_retry(self):
        # Primary 400; fallback also 400. Exactly one request per provider.
        c = client(responses=[
            FakeResponse(400, {"error": {"message": "Model does not exist"}}),
            FakeResponse(400, {"error": {"message": "model unsupported"}}),
        ])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["http_status"], 400)
        self.assertEqual(r["error_type"], "model_unavailable")
        st = c.get_status()["stats"]
        # No fallback configured in this fixture: fail-fast means exactly one
        # HTTP request, not a retry storm.
        self.assertEqual(st["http_requests"], 1)
        self.assertEqual(st["provider_attempts"], 1)
        self.assertEqual(st["retries"], 0)
        self.assertEqual(st["error_events"], 1)
        self.assertEqual(st["failed"], 1)
        # Repeated call must be circuit-broken: 0 new HTTP.
        before = c.get_status()["stats"]["http_requests"]
        r2 = c.chat([{"role": "user", "content": "x"}], response_format="json")
        after = c.get_status()["stats"]["http_requests"]
        self.assertFalse(r2["ok"])
        self.assertEqual(before, after)
        self.assertEqual(c.total_failures, 2)


    def test_400_does_not_retry_even_with_fallback(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]), responses=[
            FakeResponse(400, {"error": {"message": "Model does not exist"}}),
            FakeResponse(400, {"error": {"message": "model unsupported"}}),
        ])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        st = c.get_status()["stats"]
        self.assertEqual(st["http_requests"], 2)   # one per provider
        self.assertEqual(st["retries"], 0)         # zero retries
        self.assertEqual(st["error_events"], 2)

    def test_primary_success_never_calls_fallback(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(200, ok_body('{"ok": true}'))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(len(c._test_calls), 1)
        self.assertEqual(c.get_status()["stats"]["fallback_calls"], 0)

    def test_429_then_success_counts_one_retry(self):
        c = client(responses=[
            FakeResponse(429, {"error": "rate limit"}, headers={"Retry-After": "0"}),
            FakeResponse(200, ok_body('{"ok": true}')),
        ])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 2)
        st = c.get_status()["stats"]
        self.assertEqual(st["http_requests"], 2)
        self.assertEqual(st["retries"], 1)
        self.assertEqual(st["error_events"], 1)
        self.assertEqual(st["tokens"]["total"], 150)

    def test_5xx_retries_then_fallback(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]), responses=[
            FakeResponse(500, {"error": "upstream"}),
            FakeResponse(502, {"error": "bad gateway"}),
            FakeResponse(503, {"error": "unavailable"}),
            FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash")),
        ])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(r["model"], "deepseek-flash")
        st = c.get_status()["stats"]
        self.assertEqual(st["http_requests"], 4)
        self.assertEqual(st["provider_attempts"], 4)
        self.assertEqual(st["retries"], 2)
        self.assertEqual(st["fallback_calls"], 1)
        self.assertEqual(st["fallback_success"], 1)
        # Fallback payload must use RightCode's own model, not SiliconFlow's id.
        self.assertEqual(c._test_calls[-1]["json"]["model"], "deepseek-flash")

    def test_timeout_then_success(self):
        import requests
        c = client(responses=[requests.exceptions.Timeout("slow"),
                              FakeResponse(200, ok_body('{"ok": true}'))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(c.get_status()["stats"]["http_requests"], 2)
        self.assertEqual(c.get_status()["stats"]["retries"], 1)

    def test_empty_content_retry_once(self):
        c = client(responses=[FakeResponse(200, ok_body("")),
                              FakeResponse(200, ok_body('{"ok": true}'))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        st = c.get_status()["stats"]
        self.assertEqual(st["http_requests"], 2)
        self.assertEqual(st["retries"], 1)
        self.assertEqual(st["error_events"], 1)

    def test_invalid_json_is_reported_not_silently_ok(self):
        c = client(config=make_config(fallback=[], allow_fallback=False),
                   responses=[FakeResponse(200, ok_body("这不是JSON"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_type"], "invalid_json")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 1)

    def test_missing_credential_makes_zero_http_requests(self):
        c = client(config=make_config(fallback=[], allow_fallback=False),
                   responses=[], credential_overrides={"siliconflow": "", "rightcode-bybt": ""})
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_type"], "credential_missing")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 0)
        self.assertEqual(c.total_failures, 1)

    def test_encrypted_credential_is_explicit_blocker(self):
        c = client(config=make_config(fallback=[], allow_fallback=False),
                   responses=[],
                   credential_overrides={"siliconflow": "ENC:abc123", "rightcode-bybt": ""})
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_type"], "credential_encrypted_unsupported")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 0)

    def test_process_budget_hard_cap(self):
        c = client(config=make_config(max_process=2), responses=[
            FakeResponse(200, ok_body('{"n": 1}')),
            FakeResponse(200, ok_body('{"n": 2}')),
        ])
        self.assertTrue(c.chat([{"role": "user", "content": "1"}], response_format="json")["ok"])
        self.assertTrue(c.chat([{"role": "user", "content": "2"}], response_format="json")["ok"])
        r = c.chat([{"role": "user", "content": "3"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_type"], "budget_exhausted")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 2)
        self.assertEqual(c.get_status()["stats"]["budget_rejections"], 1)

    def test_legacy_model_alias_is_never_sent_verbatim(self):
        c = client(responses=[FakeResponse(200, ok_body('{"ok": true}'))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json",
                   model="deepseek-v4-flash-free")
        self.assertTrue(r["ok"])
        self.assertEqual(c._test_calls[0]["json"]["model"], "deepseek-ai/DeepSeek-V4.1-Flash")

    def test_disabled_provider_cannot_be_selected_or_fallback(self):
        with self.assertRaises(LLMConfigError):
            PipelineLLMClient(config=make_config(primary="opencode"))
        cfg = make_config(fallback=["gpt-5.4-xhigh"])
        with self.assertRaises(LLMConfigError):
            PipelineLLMClient(config=cfg)

    def test_gpt_model_is_rejected(self):
        cfg = make_config()
        cfg["providers"]["siliconflow"]["model"] = "gpt-5.4-xhigh"
        with self.assertRaises(LLMConfigError):
            PipelineLLMClient(config=cfg)

    def test_delta_snapshot_prevents_double_counting(self):
        c = client(responses=[
            FakeResponse(200, ok_body('{"n": 1}')),
            FakeResponse(200, ok_body('{"n": 2}')),
        ])
        c.chat([{"role": "user", "content": "1"}], response_format="json")
        first = c.stats_delta()  # establish baseline (first call returns cumulative)
        self.assertTrue(first["do_not_sum_repeated_snapshots"])
        c.chat([{"role": "user", "content": "2"}], response_format="json")
        delta = c.stats_delta()
        self.assertEqual(delta["snapshot_scope"], "delta_since_last_call")
        self.assertEqual(delta["stats"]["http_requests"], 1)
        self.assertEqual(delta["stats"]["task_calls"], 1)

    def test_secret_never_appears_in_error_or_key_used(self):
        c = client(responses=[FakeResponse(500, {"error": "upstream"})])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertNotIn("test-sf-secret", json.dumps(r, ensure_ascii=False))
        self.assertNotIn("test-sf-secret", json.dumps(c.get_status(), ensure_ascii=False))


    # ── F2: availability / fallback policy ──────────────────────────────
    def test_availability_separates_primary_and_fallback(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   credential_overrides={"siliconflow": "", "rightcode-bybt": "test-rc"})
        st = c.get_status()
        self.assertFalse(st["primary_available"])
        self.assertTrue(st["fallback_available"])
        self.assertTrue(st["any_usable"])
        self.assertTrue(st["degraded_using_paid_fallback"])
        self.assertTrue(st["paid_fallback_allowed"])
        self.assertFalse(c.primary_available)
        self.assertTrue(c.available)

    def test_missing_primary_credential_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))],
                   credential_overrides={"siliconflow": "", "rightcode-bybt": "test-rc"})
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(c.get_status()["stats"]["fallback_calls"], 1)
        self.assertEqual(c.get_status()["stats"]["http_requests"], 1)

    def test_quota_exhausted_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(402, {"error": "quota"}), 
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 2)
        self.assertEqual(c.get_status()["stats"]["retries"], 0)

    def test_bad_request_is_not_fallback_reason(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(400, {"error": "bad prompt"})])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_type"], "bad_request")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 1)
        self.assertEqual(c.get_status()["stats"]["fallback_calls"], 0)

    def test_cross_provider_model_id_is_mapped_not_forwarded(self):
        c = client(config=make_config(fallback=["rightcode-bybt"], max_transient_retries=0),
                   responses=[FakeResponse(500, {"error": "upstream"}),
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json",
                   model="deepseek-ai/DeepSeek-V4.1-Flash")
        self.assertTrue(r["ok"])
        self.assertEqual(c._test_calls[-1]["json"]["model"], "deepseek-flash")

    def test_unknown_model_override_never_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]))
        r = c.chat([{"role": "user", "content": "x"}], response_format="json",
                   model="totally-unknown-model")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_type"], "unknown_model_override")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 0)

    def test_paid_fallback_cap_is_enforced(self):
        c = client(config=make_config(fallback=["rightcode-bybt"], max_transient_retries=0),
                   responses=[FakeResponse(500, {"error": "upstream"}),
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash")),
                              FakeResponse(500, {"error": "upstream"})])
        c.set_max_paid_fallback_calls(1)
        self.assertTrue(c.chat([{"role": "user", "content": "1"}], response_format="json")["ok"])
        r2 = c.chat([{"role": "user", "content": "2"}], response_format="json")
        self.assertFalse(r2["ok"])
        self.assertEqual(c.get_paid_fallback_calls(), 1)
        self.assertEqual(c.get_status()["stats"]["http_requests"], 3)
        self.assertEqual(len(c._test_calls), 3)

    def test_rate_limit_exhaustion_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"], max_transient_retries=0),
                   responses=[FakeResponse(429, {"error": "rate limit"}),
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 2)

    def test_auth_error_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(401, {"error": "invalid key"}),
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 2)

    def test_empty_content_exhaustion_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(200, ok_body("")),
                              FakeResponse(200, ok_body("")),
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 3)

    def test_invalid_json_falls_back(self):
        c = client(config=make_config(fallback=["rightcode-bybt"]),
                   responses=[FakeResponse(200, ok_body("这不是JSON")),
                              FakeResponse(200, ok_body('{"ok": true}', model="deepseek-flash"))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["provider"], "rightcode-bybt")
        self.assertEqual(c.get_status()["stats"]["http_requests"], 2)

    def test_fallback_attempt_budget_zero_blocks_fallback(self):
        cfg = make_config(fallback=["rightcode-bybt"], max_transient_retries=0)
        cfg["policy"]["max_fallback_attempts_per_task"] = 0
        c = client(config=cfg, responses=[FakeResponse(500, {"error": "upstream"})])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertFalse(r["ok"])
        self.assertEqual(c.get_status()["stats"]["http_requests"], 1)
        self.assertEqual(c.get_status()["stats"]["fallback_calls"], 0)

    def test_concurrent_process_budget_is_atomic(self):
        c = client(config=make_config(max_process=1),
                   responses=[FakeResponse(200, ok_body('{"ok": true}')) for _ in range(8)])
        barrier = threading.Barrier(8)
        def run(i):
            barrier.wait(timeout=2)
            return c.chat([{"role": "user", "content": str(i)}], response_format="json")
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(run, range(8)))
        self.assertEqual(sum(1 for r in results if r["ok"]), 1)
        self.assertEqual(c.get_status()["stats"]["http_requests"], 1)
        self.assertEqual(len(c._test_calls), 1)

    # ── F3: secret handling ─────────────────────────────────────────────
    def test_key_used_never_contains_secret_bytes(self):
        c = client(responses=[FakeResponse(200, ok_body('{"ok": true}'))])
        r = c.chat([{"role": "user", "content": "x"}], response_format="json")
        self.assertTrue(r["ok"])
        self.assertEqual(r["key_used"], "siliconflow:override")
        self.assertNotIn("test-sf", r["key_used"])
        self.assertNotIn("test-sf", json.dumps(c.get_status(), ensure_ascii=False))

    def _file_ref(self, path, allowed):
        return CredentialRef(file_env="R1_TEST_KEY_FILE", allowed_dirs=[allowed])

    def test_secret_file_0600_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "key")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, b"secret-value")
            os.close(fd)
            with patch.dict(os.environ, {"R1_TEST_KEY_FILE": path}, clear=False):
                secret, blocker, channel = self._file_ref(path, d).resolve_with_channel()
            self.assertEqual(secret, "secret-value")
            self.assertEqual(blocker, "")
            self.assertTrue(channel.startswith("file_env:"))

    def test_secret_file_0644_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "key")
            with open(path, "w") as fh:
                fh.write("secret-value")
            os.chmod(path, 0o644)
            with patch.dict(os.environ, {"R1_TEST_KEY_FILE": path}, clear=False):
                secret, blocker, _ = self._file_ref(path, d).resolve_with_channel()
            self.assertIsNone(secret)
            self.assertEqual(blocker, "credential_file_permissions")

    def test_secret_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "real-key")
            with open(target, "w") as fh:
                fh.write("secret-value")
            os.chmod(target, 0o600)
            link = os.path.join(d, "link-key")
            os.symlink(target, link)
            with patch.dict(os.environ, {"R1_TEST_KEY_FILE": link}, clear=False):
                secret, blocker, _ = self._file_ref(link, d).resolve_with_channel()
            self.assertIsNone(secret)
            self.assertEqual(blocker, "credential_file_symlink")

    def test_secret_file_outside_allowed_dir_is_rejected(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as other:
            path = os.path.join(other, "key")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.write(fd, b"secret-value")
            os.close(fd)
            with patch.dict(os.environ, {"R1_TEST_KEY_FILE": path}, clear=False):
                secret, blocker, _ = self._file_ref(path, d).resolve_with_channel()
            self.assertIsNone(secret)
            self.assertEqual(blocker, "credential_file_outside_allowed_dir")


if __name__ == "__main__":
    unittest.main(verbosity=2)
