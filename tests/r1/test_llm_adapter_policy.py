#!/usr/bin/env python3
"""R1 adapter policy tests — deterministic, no network, no real credentials."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from skills.utils.llm_pipeline_adapter import (  # noqa: E402
    LLMConfigError, PipelineLLMClient,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
