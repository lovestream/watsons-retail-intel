# -*- coding: utf-8 -*-
"""R1: provider-agnostic Python LLM adapter for the intelligence pipeline.

Design constraints (from NAS-R0 review / R1):
- No credentials are stored in source, logs, git, or reports.
- Production code only reads a credential *reference*: an environment variable
  (PIPELINE_LLM_API_KEY) or a file path supplied by that environment. It never
  parses QwenPaw's private provider registry.
- The legacy OpenCode pool is disabled and is never used as a fallback.
- Expensive GPT fallback is disabled; only an explicitly configured fallback
  provider (rightcode-bybt / deepseek-flash) may be used.
- HTTP 400 "model unavailable" fails fast: no retry storm.
- Counters distinguish task calls, provider attempts, real HTTP requests,
  retries, error events, and token usage. `get_status()` is a cumulative
  snapshot; repeated snapshots must not be summed by log readers.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

try:  # PyYAML is already a pipeline dependency
    import yaml
except Exception:  # pragma: no cover
    yaml = None

logger = logging.getLogger(__name__)

_ENC_PREFIX = "ENC:"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_DEFAULT_CONFIG = os.path.join(_PROJECT_ROOT, "config", "llm_providers.yaml")

# Model IDs that used to be passed by model_router.yaml / callers. They are
# logical aliases, never sent to a provider verbatim.
LEGACY_MODEL_ALIASES = {
    "pipeline-deepseek-flash": "primary",
    "deepseek-v4-flash-free": "primary",
    "deepseek-v4-flash": "primary",
    "deepseek-flash": "primary",
}

# Never allowed, even if a stale config still names them.
HARD_DISABLED_PROVIDERS = {
    "opencode", "opencode-keyless", "opencode-custom", "opencode-custom-new",
    "opencode-custom-new-new", "gpt-5.4-xhigh", "gpt", "right.codes-legacy",
}

_TRANSIENT_TYPES = {
    "rate_limited", "server_error", "timeout", "connection_error",
    "invalid_response_body",
}
_PERMANENT_PROVIDER_TYPES = {
    "auth_error", "model_unavailable", "endpoint_not_found", "quota_exhausted",
}

# F2: paid fallback is allowed only for genuine primary-unavailability or
# transport/format failure. A bad prompt (`bad_request`) or a caller bug
# (`unknown_model_override`) must fail, not silently spend on the fallback.
FALLBACK_ALLOWED_ERROR_TYPES = {
    "model_unavailable", "auth_error", "quota_exhausted",
    "endpoint_not_found", "rate_limited", "server_error",
    "timeout", "connection_error", "invalid_response_body",
    "empty_content", "invalid_json",
    "credential_missing", "credential_encrypted_unsupported",
    "credential_file_missing", "credential_file_unreadable",
    "credential_file_invalid_json", "credential_file_permissions",
    "credential_file_symlink", "credential_file_not_regular",
    "credential_file_outside_allowed_dir",
}


class LLMConfigError(Exception):
    """Configuration or credential problem detected before any HTTP call."""


def mask_secret(value: str) -> str:
    if not value:
        return "****"
    if len(value) <= 4:
        return "****"
    return value[:2] + "*" * max(1, len(value) - 4) + value[-2:]


def _redact(text: str, secret: str = "") -> str:
    text = str(text or "").replace("\n", " ")
    if secret:
        text = text.replace(secret, "***")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", text, flags=re.I)
    return text[:240]


@dataclass
class CredentialRef:
    env: str = ""
    file_env: str = ""
    env_aliases: List[str] = field(default_factory=list)
    allowed_dirs: List[str] = field(default_factory=list)
    last_channel: str = field(default="", init=False, repr=False)

    def _allowed_dir(self, real_path: str) -> bool:
        dirs = list(self.allowed_dirs) or [
            _PROJECT_ROOT,
            "/run/secrets",
            os.environ.get("PIPELINE_LLM_SECRET_DIR", "").strip(),
        ]
        for d in dirs:
            if not d:
                continue
            root = os.path.realpath(d)
            if real_path == root or real_path.startswith(root + os.sep):
                return True
        return False

    def resolve_with_channel(self) -> Tuple[Optional[str], str, str]:
        """Return (secret, blocker, channel). Never logs/persists the secret.

        File credentials must be owner-only regular files (0400/0600), not
        symlinks, and inside an allowed secret directory. This prevents a
        world-readable or redirected key from being silently accepted.
        """
        value = ""
        for name in [self.env] + list(self.env_aliases):
            if name and os.environ.get(name, "").strip():
                value = os.environ[name].strip()
                self.last_channel = "env:" + name
                break
        file_path = os.environ.get(self.file_env, "").strip() if self.file_env else ""
        if not value and file_path:
            if not os.path.exists(file_path):
                return None, "credential_file_missing", "file_env:" + self.file_env
            try:
                st = os.lstat(file_path)
            except OSError:
                return None, "credential_file_unreadable", "file_env:" + self.file_env
            if stat.S_ISLNK(st.st_mode):
                return None, "credential_file_symlink", "file_env:" + self.file_env
            if not stat.S_ISREG(st.st_mode):
                return None, "credential_file_not_regular", "file_env:" + self.file_env
            mode = stat.S_IMODE(st.st_mode)
            if (mode & 0o777) not in (0o400, 0o600):
                return None, "credential_file_permissions", "file_env:" + self.file_env
            real_path = os.path.realpath(file_path)
            if not self._allowed_dir(real_path):
                return None, "credential_file_outside_allowed_dir", "file_env:" + self.file_env
            try:
                with open(real_path, "r", encoding="utf-8") as fh:
                    raw = fh.read().strip()
            except Exception:
                return None, "credential_file_unreadable", "file_env:" + self.file_env
            if raw.startswith("{"):
                try:
                    obj = json.loads(raw)
                    value = str(obj.get("api_key") or "").strip()
                except Exception:
                    return None, "credential_file_invalid_json", "file_env:" + self.file_env
            else:
                value = raw
            self.last_channel = "file_env:" + self.file_env
        if not value:
            return None, "credential_missing", self.last_channel or ""
        if value.startswith(_ENC_PREFIX):
            return None, "credential_encrypted_unsupported", self.last_channel or ""
        return value, "", self.last_channel

    def resolve(self) -> Tuple[Optional[str], str]:
        secret, blocker, _ = self.resolve_with_channel()
        return secret, blocker


@dataclass
class ProviderSpec:
    name: str
    base_url: str
    model: str
    credential: CredentialRef
    timeout: int = 90
    json_mode: bool = True
    extra_body: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    pricing: Dict[str, Any] = field(default_factory=dict)

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"


@dataclass
class Policy:
    primary: str = "siliconflow"
    fallback: List[str] = field(default_factory=lambda: ["rightcode-bybt"])
    allow_fallback: bool = True
    fallback_on: List[str] = field(default_factory=lambda: sorted(FALLBACK_ALLOWED_ERROR_TYPES))
    max_fallback_attempts_per_task: int = 1
    max_transient_retries: int = 2
    max_empty_content_retries: int = 1
    max_http_requests_per_task: int = 6
    max_http_requests_per_process: int = 200
    wall_clock_seconds: int = 120
    request_timeout_seconds: int = 90
    disabled_providers: List[str] = field(default_factory=list)


def _load_config(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or os.environ.get("PIPELINE_LLM_CONFIG", "").strip() or _DEFAULT_CONFIG
    if not os.path.exists(path):
        raise LLMConfigError("provider_config_missing:%s" % os.path.basename(path))
    if yaml is None:
        raise LLMConfigError("pyyaml_missing")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise LLMConfigError("provider_config_invalid")
    return data


def _build_specs(config: Dict[str, Any]) -> Dict[str, ProviderSpec]:
    specs: Dict[str, ProviderSpec] = {}
    for name, raw in (config.get("providers") or {}).items():
        if name in HARD_DISABLED_PROVIDERS:
            continue
        cred = raw.get("credential") or {}
        specs[name] = ProviderSpec(
            name=name,
            base_url=str(raw.get("base_url") or "").rstrip("/"),
            model=str(raw.get("model") or ""),
            credential=CredentialRef(
                env=str(cred.get("env") or ""),
                file_env=str(cred.get("file_env") or ""),
                env_aliases=[str(x) for x in (cred.get("env_aliases") or [])],
                allowed_dirs=[str(x) for x in (cred.get("allowed_dirs") or [])],
            ),
            timeout=int(raw.get("timeout") or 90),
            json_mode=bool(raw.get("json_mode", True)),
            extra_body=dict(raw.get("extra_body") or {}),
            headers=dict(raw.get("headers") or {}),
            pricing=dict(raw.get("pricing") or {}),
        )
    if not specs:
        raise LLMConfigError("no_enabled_providers")
    return specs


def _build_policy(config: Dict[str, Any]) -> Policy:
    raw = config.get("policy") or {}
    return Policy(
        primary=str(raw.get("primary") or "siliconflow"),
        fallback=[str(x) for x in (raw.get("fallback") or [])],
        allow_fallback=bool(raw.get("allow_fallback", True)),
        fallback_on=[str(x) for x in (raw.get("fallback_on") or sorted(FALLBACK_ALLOWED_ERROR_TYPES))],
        max_fallback_attempts_per_task=max(0, int(raw.get("max_fallback_attempts_per_task", 1))),
        max_transient_retries=max(0, int(raw.get("max_transient_retries", 2))),
        max_empty_content_retries=max(0, int(raw.get("max_empty_content_retries", 1))),
        max_http_requests_per_task=max(1, int(raw.get("max_http_requests_per_task", 6))),
        max_http_requests_per_process=max(1, int(raw.get("max_http_requests_per_process", 200))),
        wall_clock_seconds=max(5, int(raw.get("wall_clock_seconds", 120))),
        request_timeout_seconds=max(5, int(raw.get("request_timeout_seconds", 90))),
        disabled_providers=[str(x) for x in (raw.get("disabled_providers") or [])],
    )


class PipelineLLMClient:
    """Small, auditable client used by the Python pipeline.

    `chat()` keeps the legacy return contract (`ok/content/parsed/model/usage/
    key_used/error`) and adds trace/counter fields. The legacy OpenCode class
    remains in llm_client.py only as `LegacyOpenCodeClient` for archaeology.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        http_post: Optional[Callable[..., Any]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        credential_overrides: Optional[Dict[str, str]] = None,
    ):
        self.config = copy.deepcopy(config) if config is not None else _load_config(config_path)
        self.specs = _build_specs(self.config)
        self.policy = _build_policy(self.config)
        self.aliases = dict(LEGACY_MODEL_ALIASES)
        self.aliases.update(self.config.get("model_aliases") or {})
        self._http_post = http_post or requests.post
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._credential_overrides = dict(credential_overrides or {})
        self._max_paid_fallback_calls = 0
        self._lock = threading.Lock()
        self._disabled: Dict[str, str] = {}
        self._last_status: Optional[Dict[str, Any]] = None
        self._trace: deque = deque(maxlen=100)
        self._stats: Dict[str, Any] = {
            "task_calls": 0,
            "succeeded": 0,
            "failed": 0,
            "provider_attempts": 0,
            "http_requests": 0,
            "retries": 0,
            "error_events": 0,
            "errors_by_type": {},
            "fallback_calls": 0,
            "fallback_success": 0,
            "budget_rejections": 0,
            "reasoning_fallbacks": 0,
            "sleep_seconds": 0.0,
            "latency_ms": 0.0,
            "tokens": {"prompt": 0, "completion": 0, "total": 0},
            "usage_by_provider": {},
        }
        if self.policy.primary in HARD_DISABLED_PROVIDERS or self.policy.primary in self.policy.disabled_providers:
            raise LLMConfigError("primary_provider_disabled:%s" % self.policy.primary)
        if self.policy.primary not in self.specs:
            raise LLMConfigError("primary_provider_not_configured:%s" % self.policy.primary)
        for name in self.policy.fallback:
            if name in HARD_DISABLED_PROVIDERS:
                raise LLMConfigError("fallback_provider_hard_disabled:%s" % name)
        # A paid GPT channel must never be silently wired as fallback.
        for spec in self.specs.values():
            if "gpt" in spec.model.lower():
                raise LLMConfigError("expensive_gpt_model_rejected:%s" % spec.model)

    # ── compatibility surface ───────────────────────────────────────────
    @property
    def model(self) -> str:
        return self.specs[self.policy.primary].model

    @property
    def base_url(self) -> str:
        return self.specs[self.policy.primary].base_url

    @property
    def timeout(self) -> int:
        return self.policy.request_timeout_seconds

    @property
    def max_retries(self) -> int:
        return self.policy.max_transient_retries

    @property
    def keys(self) -> List[str]:
        """Credential labels only (never secret values)."""
        return ["%s:%s" % (name, spec.credential.env or spec.credential.file_env or "unset")
                for name, spec in self.specs.items()]

    def _provider_state(self, name: str) -> Dict[str, Any]:
        spec = self.specs.get(name)
        if not spec:
            return {"configured": False, "available": False, "blocker": "provider_not_configured",
                    "channel": ""}
        disabled = self._disabled.get(name, "")
        secret, blocker, channel = self._resolve_secret_info(spec)
        usable = bool(secret) and not blocker and not disabled
        return {"configured": True, "available": usable,
                "blocker": disabled or blocker, "channel": channel,
                "model": spec.model, "base_url": spec.base_url}

    @property
    def primary_available(self) -> bool:
        return bool(self._provider_state(self.policy.primary)["available"])

    @property
    def fallback_available(self) -> bool:
        return any(self._provider_state(n)["available"]
                   for n in self.policy.fallback if n != self.policy.primary)

    @property
    def any_usable(self) -> bool:
        return self.primary_available or self.fallback_available

    @property
    def available(self) -> bool:
        """Backward-compatible: any configured channel is usable, not 'all green'."""
        return self.any_usable

    @property
    def degraded_using_paid_fallback(self) -> bool:
        return (not self.primary_available and self.fallback_available) or \
            int(self._stats.get("fallback_calls", 0)) > 0

    @property
    def available_keys(self) -> int:
        return sum(1 for name in self.specs if self._provider_state(name)["available"])

    @property
    def total_calls(self) -> int:
        return int(self._stats["task_calls"])

    @property
    def total_failures(self) -> int:
        return int(self._stats["failed"])

    @property
    def token_usage(self) -> Dict[str, int]:
        return dict(self._stats["tokens"])

    @property
    def error_keys(self) -> Dict[str, str]:
        return dict(self._disabled)

    def _get_endpoint(self) -> str:
        return self.specs[self.policy.primary].endpoint

    def get_paid_fallback_calls(self) -> int:
        return int(self._stats.get("fallback_calls", 0))

    def reset_paid_fallback_calls(self) -> None:
        with self._lock:
            self._stats["fallback_calls"] = 0
            self._stats["fallback_success"] = 0

    def set_max_paid_fallback_calls(self, limit: int) -> None:
        self._max_paid_fallback_calls = max(0, int(limit or 0))

    # ── credentials ────────────────────────────────────────────────────
    def _resolve_secret_info(self, spec: ProviderSpec) -> Tuple[Optional[str], str, str]:
        if spec.name in self._credential_overrides:
            value = str(self._credential_overrides[spec.name] or "").strip()
            if not value:
                return None, "credential_missing", "override"
            if value.startswith(_ENC_PREFIX):
                return None, "credential_encrypted_unsupported", "override"
            return value, "", "override"
        return spec.credential.resolve_with_channel()

    def _resolve_secret(self, spec: ProviderSpec) -> Tuple[Optional[str], str]:
        secret, blocker, _ = self._resolve_secret_info(spec)
        return secret, blocker

    def credential_report(self) -> Dict[str, Any]:
        """Non-secret credential/blocker state for health checks and reports."""
        out = {}
        for name, spec in self.specs.items():
            secret, blocker, channel = self._resolve_secret_info(spec)
            out[name] = {
                "channel": channel,
                "present": bool(secret),
                "blocker": blocker,
                "disabled": self._disabled.get(name, ""),
            }
        return out

    # ── counters ───────────────────────────────────────────────────────
    def _bump(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._stats[key] = int(self._stats.get(key, 0)) + amount

    def _bump_error(self, error_type: str) -> None:
        with self._lock:
            self._stats["error_events"] += 1
            counts = self._stats.setdefault("errors_by_type", {})
            counts[error_type] = int(counts.get(error_type, 0)) + 1

    def _accumulate_usage(self, provider: str, usage: Dict[str, int]) -> None:
        with self._lock:
            for k in ("prompt", "completion", "total"):
                self._stats["tokens"][k] += int(usage.get(k, 0))
            by = self._stats["usage_by_provider"].setdefault(
                provider, {"prompt": 0, "completion": 0, "total": 0, "requests": 0})
            for k in ("prompt", "completion", "total"):
                by[k] += int(usage.get(k, 0))
            by["requests"] += 1

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            stats = copy.deepcopy(self._stats)
        primary = self._provider_state(self.policy.primary)
        fallback_states = {n: self._provider_state(n) for n in self.policy.fallback
                           if n != self.policy.primary}
        fallback_available = any(v["available"] for v in fallback_states.values())
        paid_tokens = {"prompt": 0, "completion": 0, "total": 0}
        for state_name in fallback_states:
            usage = stats.get("usage_by_provider", {}).get(state_name) or {}
            for k in paid_tokens:
                paid_tokens[k] += int(usage.get(k, 0))
        return {
            "counter_version": 3,
            "snapshot_scope": "cumulative_process",
            "do_not_sum_repeated_snapshots": True,
            # available is kept for old callers; it means "any usable channel".
            "available": bool(primary["available"] or fallback_available),
            "primary_available": bool(primary["available"]),
            "fallback_available": fallback_available,
            "any_usable": bool(primary["available"] or fallback_available),
            "degraded_using_paid_fallback": bool(
                (not primary["available"] and fallback_available) or
                stats.get("fallback_calls", 0) > 0),
            "primary_blocker": primary["blocker"],
            "fallback_blockers": {n: v["blocker"] for n, v in fallback_states.items()},
            "available_keys": self.available_keys,
            "total_keys": len(self.specs),
            "model": self.model,
            "base_url": self.base_url,
            "endpoint": self._get_endpoint(),
            "total_calls": stats["task_calls"],
            "total_failures": stats["failed"],
            "token_usage": dict(stats["tokens"]),
            "stats": stats,
            "credential_report": self.credential_report(),
            "disabled_providers": dict(self._disabled),
            "paid_fallback_allowed": bool(self.policy.allow_fallback and fallback_available),
            "paid_fallback_calls": int(stats.get("fallback_calls", 0)),
            "paid_fallback_success": int(stats.get("fallback_success", 0)),
            "paid_fallback_tokens": paid_tokens,
            "error_keys": dict(self._disabled),
            "recent_trace": list(self._trace)[-10:],
        }

    def stats_delta(self) -> Dict[str, Any]:
        """Return counters since the previous call; safe to log per step."""
        current = self.get_status()
        previous = self._last_status
        self._last_status = current
        if previous is None:
            return current
        delta = copy.deepcopy(current)
        delta["stats"] = {}
        for key, value in current["stats"].items():
            old = previous["stats"].get(key, 0)
            if isinstance(value, dict):
                old_map = old if isinstance(old, dict) else {}
                if all(isinstance(v, (int, float)) for v in value.values()):
                    delta["stats"][key] = {
                        k: int(v) - int(old_map.get(k, 0))
                        for k, v in value.items()
                    }
                else:
                    nested = {}
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, dict):
                            old_sub = old_map.get(sub_key) or {}
                            nested[sub_key] = {
                                k: int(v) - int(old_sub.get(k, 0))
                                for k, v in sub_val.items()
                            }
                        elif isinstance(sub_val, (int, float)):
                            nested[sub_key] = sub_val - int(old_map.get(sub_key, 0))
                    delta["stats"][key] = nested
            elif isinstance(value, (int, float)):
                delta["stats"][key] = value - old
        delta["snapshot_scope"] = "delta_since_last_call"
        delta["do_not_sum_repeated_snapshots"] = False
        return delta

    def reset_stats(self) -> None:
        with self._lock:
            for key in ("task_calls", "succeeded", "failed", "provider_attempts",
                        "http_requests", "retries", "error_events", "fallback_calls",
                        "fallback_success", "budget_rejections", "reasoning_fallbacks"):
                self._stats[key] = 0
            self._stats["errors_by_type"] = {}
            self._stats["sleep_seconds"] = 0.0
            self._stats["latency_ms"] = 0.0
            self._stats["tokens"] = {"prompt": 0, "completion": 0, "total": 0}
            self._stats["usage_by_provider"] = {}
        self._last_status = None

    # ── helpers ────────────────────────────────────────────────────────
    def _resolve_model(self, spec: ProviderSpec, requested: Optional[str]) -> Tuple[str, str]:
        if not requested:
            return spec.model, ""
        requested = str(requested).strip()
        if requested in ("rule_only", "none", ""):
            return spec.model, ""
        if requested == spec.model:
            return requested, ""
        if requested in self.aliases:
            # A logical pipeline alias always resolves to this provider's own
            # model. This is what lets the same caller string work on primary
            # and fallback without sending SiliconFlow's model id to RightCode.
            return spec.model, ""
        if requested in {s.model for s in self.specs.values()}:
            # F2: a model id belonging to another provider is a cross-channel
            # request. Map it to this provider's own model rather than sending
            # an invalid foreign id (e.g. SiliconFlow id to RightCode).
            return spec.model, ""
        return "", "unknown_model_override:%s" % requested

    def _parse_json(self, text: str) -> Optional[dict]:
        if not text:
            return None
        try:
            try:
                from .llm_client import robust_json_extract  # lazy: avoid import cycle
            except ImportError:
                from llm_client import robust_json_extract
            parsed = robust_json_extract(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        try:
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _normalise_usage(raw: Any) -> Dict[str, int]:
        if not isinstance(raw, dict):
            return {"prompt": 0, "completion": 0, "total": 0}
        prompt = raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0
        completion = raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0
        total = raw.get("total_tokens", 0) or (int(prompt) + int(completion))
        return {"prompt": int(prompt), "completion": int(completion), "total": int(total)}

    @staticmethod
    def _classify_error(status: int, body: str) -> str:
        low = (body or "").lower()
        if status == 400:
            model_hints = ("model", "unsupported", "not found", "does not exist",
                           "unavailable", "invalid model", "no available channel")
            if any(h in low for h in model_hints):
                return "model_unavailable"
            return "bad_request"
        if status in (401, 403):
            return "auth_error"
        if status == 402:
            return "quota_exhausted"
        if status == 404:
            return "endpoint_not_found"
        if status == 408:
            return "timeout"
        if status == 429:
            return "rate_limited"
        if 500 <= status <= 599:
            return "server_error"
        return "http_error"

    @staticmethod
    def _retry_after(resp: Any, default: float) -> float:
        try:
            value = resp.headers.get("Retry-After")
            if value:
                return min(10.0, max(0.0, float(value)))
        except Exception:
            pass
        return default

    def _reserve_http_slot(self, task_http: int) -> Tuple[bool, str]:
        """Atomically reserve one process-wide HTTP slot.

        F5: checking then incrementing in separate critical sections allowed
        concurrent tasks to overrun `max_http_requests_per_process`.
        """
        with self._lock:
            if task_http >= self.policy.max_http_requests_per_task:
                return False, "task_request_budget_exhausted"
            if self._stats["http_requests"] >= self.policy.max_http_requests_per_process:
                return False, "process_request_budget_exhausted"
            self._stats["http_requests"] += 1
            return True, ""

    def _fallback_allowed(self, error_type: str) -> bool:
        if not self.policy.allow_fallback:
            return False
        return str(error_type or "") in set(self.policy.fallback_on)

    def _record_trace(self, **kwargs: Any) -> None:
        self._trace.append(kwargs)

    def _result(self, base: Dict[str, Any], **updates: Any) -> Dict[str, Any]:
        out = dict(base)
        out.update(updates)
        return out

    # ── main call ──────────────────────────────────────────────────────
    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        response_format: str = "text",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex
        self._bump("task_calls")
        started = self._clock()
        result_base: Dict[str, Any] = {
            "ok": False, "content": "", "parsed": None, "model": "",
            "usage": {}, "key_used": "", "error": "", "provider": "",
            "request_id": request_id, "attempts": 0, "retries": 0,
            "http_status": None, "error_type": "", "latency_ms": 0,
            "cost_estimate": None, "pricing_verified": False,
        }

        full_messages: List[Dict[str, str]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages or [])
        if response_format == "json" and not system_prompt:
            full_messages.insert(0, {
                "role": "system",
                "content": "你是严格的JSON分类器，只输出JSON，不要输出任何其他文字。"})

        deadline = started + self.policy.wall_clock_seconds
        task_http = 0
        total_retries = 0
        last_error = ""
        last_type = ""
        last_status = None
        last_provider = ""
        last_model = ""
        fallback_attempts = 0
        fallback_reason = ""

        providers: List[str] = [self.policy.primary]
        if self.policy.allow_fallback:
            providers += [p for p in self.policy.fallback if p != self.policy.primary]

        for p_index, provider_name in enumerate(providers):
            spec = self.specs.get(provider_name)
            if spec is None:
                last_error = "provider_not_configured:%s" % provider_name
                last_type = "provider_not_configured"
                self._bump_error(last_type)
                continue
            if provider_name in self._disabled:
                last_error = "provider_disabled:%s:%s" % (provider_name, self._disabled[provider_name])
                last_type = self._disabled[provider_name]
                continue
            if p_index > 0:
                if not self._fallback_allowed(last_type):
                    last_error = "fallback_suppressed:%s" % (last_type or "unknown")
                    fallback_reason = last_error
                    break
                if fallback_attempts >= self.policy.max_fallback_attempts_per_task:
                    last_error = "fallback_attempt_budget_exhausted"
                    fallback_reason = last_error
                    break
                if (self._max_paid_fallback_calls > 0 and
                        int(self._stats.get("fallback_calls", 0)) >= self._max_paid_fallback_calls):
                    last_error = "paid_fallback_budget_exhausted"
                    fallback_reason = last_error
                    break
            if self._clock() > deadline:
                last_error = "wall_clock_exceeded"
                last_type = "wall_clock_exceeded"
                break
            secret, blocker = self._resolve_secret(spec)
            if blocker or not secret:
                last_error = "%s:%s" % (blocker or "credential_missing", provider_name)
                last_type = blocker or "credential_missing"
                self._bump_error(last_type)
                self._disabled[provider_name] = last_type
                result_base["error"] = last_error
                continue
            use_model, model_error = self._resolve_model(spec, model)
            last_provider, last_model = provider_name, use_model
            if model_error:
                last_error = model_error
                last_type = "unknown_model_override"
                self._bump_error(last_type)
                break

            if p_index > 0:
                fallback_attempts += 1
                fallback_reason = last_type
                self._bump("fallback_calls")

            attempt = 0
            empty_attempt = 0
            retries_used = 0
            while True:
                if self._clock() > deadline:
                    last_error = "wall_clock_exceeded"
                    last_type = "wall_clock_exceeded"
                    break
                reserved, budget_reason = self._reserve_http_slot(task_http)
                if not reserved:
                    last_error = budget_reason
                    last_type = "budget_exhausted"
                    self._bump("budget_rejections")
                    self._bump_error(last_type)
                    break
                task_http += 1

                payload: Dict[str, Any] = {
                    "model": use_model,
                    "messages": full_messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if response_format == "json" and spec.json_mode:
                    payload["response_format"] = {"type": "json_object"}
                payload.update(spec.extra_body)
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "watsons-retail-intel/python-pipeline-r1",
                }
                headers.update(spec.headers)
                headers["Authorization"] = "Bearer " + secret

                attempt += 1
                self._bump("provider_attempts")
                req_started = self._clock()
                resp = None
                try:
                    resp = self._http_post(
                        spec.endpoint, headers=headers, json=payload,
                        timeout=spec.timeout or self.policy.request_timeout_seconds)
                except requests.exceptions.Timeout:
                    last_error, last_type = "timeout", "timeout"
                except requests.exceptions.ConnectionError as exc:
                    last_error, last_type = _redact(str(exc), secret), "connection_error"
                except Exception as exc:
                    last_error, last_type = _redact(str(exc), secret), "connection_error"
                latency_ms = int((self._clock() - req_started) * 1000)
                with self._lock:
                    self._stats["latency_ms"] += latency_ms

                if resp is None:
                    self._bump_error(last_type)
                    self._record_trace(request_id=request_id, provider=provider_name,
                                       attempt=attempt, status=None, error_type=last_type,
                                       latency_ms=latency_ms)
                    if last_type in _TRANSIENT_TYPES and retries_used < self.policy.max_transient_retries:
                        retries_used += 1
                        total_retries += 1
                        self._bump("retries")
                        delay = min(8.0, 1.0 * (2 ** (retries_used - 1)))
                        self._sleep(delay)
                        with self._lock:
                            self._stats["sleep_seconds"] += delay
                        continue
                    break

                status = int(getattr(resp, "status_code", 0) or 0)
                last_status = status
                body = _redact(getattr(resp, "text", "") or "", secret)
                self._record_trace(request_id=request_id, provider=provider_name,
                                   attempt=attempt, status=status,
                                   error_type="" if 200 <= status < 300 else
                                   self._classify_error(status, body),
                                   latency_ms=latency_ms)

                if status < 200 or status >= 300:
                    last_type = self._classify_error(status, body)
                    last_error = "%s:%s:%s" % (provider_name, last_type, body)
                    self._bump_error(last_type)
                    if last_type in _PERMANENT_PROVIDER_TYPES:
                        # Permanent for this provider in this process: fail fast.
                        self._disabled[provider_name] = last_type
                        break
                    if last_type in _TRANSIENT_TYPES and retries_used < self.policy.max_transient_retries:
                        retries_used += 1
                        total_retries += 1
                        self._bump("retries")
                        delay = self._retry_after(resp, min(8.0, 1.0 * (2 ** (retries_used - 1))))
                        self._sleep(delay)
                        with self._lock:
                            self._stats["sleep_seconds"] += delay
                        continue
                    # bad_request / http_error / exhausted transient: no retry
                    break

                # 2xx
                try:
                    body_json = resp.json()
                except Exception:
                    last_error, last_type = "invalid_response_body", "invalid_response_body"
                    self._bump_error(last_type)
                    break
                if isinstance(body_json, dict) and body_json.get("error"):
                    err_text = _redact(json.dumps(body_json.get("error"), ensure_ascii=False), secret)
                    last_type = self._classify_error(200, err_text)
                    last_error = "%s:%s:%s" % (provider_name, last_type, err_text)
                    self._bump_error(last_type)
                    if last_type in _PERMANENT_PROVIDER_TYPES:
                        self._disabled[provider_name] = last_type
                    break

                choices = body_json.get("choices") or []
                msg = (choices[0].get("message") or {}) if choices else {}
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                usage = self._normalise_usage(body_json.get("usage"))
                self._accumulate_usage(provider_name, usage)
                content_source = "content"
                if not str(content).strip() and reasoning:
                    content = reasoning
                    content_source = "reasoning"
                    self._bump("reasoning_fallbacks")

                if not str(content).strip():
                    last_error, last_type = "empty_content", "empty_content"
                    self._bump_error(last_type)
                    if empty_attempt < self.policy.max_empty_content_retries:
                        empty_attempt += 1
                        retries_used += 1
                        total_retries += 1
                        self._bump("retries")
                        delay = min(4.0, 1.0 * (2 ** (empty_attempt - 1)))
                        self._sleep(delay)
                        with self._lock:
                            self._stats["sleep_seconds"] += delay
                        continue
                    break

                parsed = self._parse_json(content) if response_format == "json" else None
                if response_format == "json" and parsed is None:
                    last_error, last_type = "invalid_json", "invalid_json"
                    self._bump_error(last_type)
                    # Different provider may comply; do not retry same model blindly.
                    break

                elapsed_ms = int((self._clock() - started) * 1000)
                cost = self._estimate_cost(spec, usage)
                if p_index > 0:
                    self._bump("fallback_success")
                with self._lock:
                    self._stats["succeeded"] += 1
                return self._result(
                    result_base,
                    ok=True, content=content, parsed=parsed,
                    model=str(body_json.get("model") or use_model),
                    usage=usage, key_used="%s:%s" % (
                        provider_name, self._resolve_secret_info(spec)[2] or "credential"),
                    error="", provider=provider_name, attempts=task_http,
                    provider_attempts=attempt, retries=total_retries, http_status=status,
                    error_type="", latency_ms=elapsed_ms,
                    content_source=content_source,
                    fallback_used=bool(p_index > 0),
                    fallback_reason=fallback_reason,
                    credential_channel=self._resolve_secret_info(spec)[2],
                    cost_estimate=cost["amount"], pricing_verified=cost["verified"],
                    cost_currency=cost["currency"],
                )

        with self._lock:
            self._stats["failed"] += 1
        result_base.update({
            "error": last_error or "all_providers_failed",
            "error_type": last_type or "all_providers_failed",
            "http_status": last_status,
            "provider": last_provider,
            "model": last_model,
            "attempts": task_http,
            "retries": total_retries,
            "latency_ms": int((self._clock() - started) * 1000),
            "fallback_used": bool(fallback_attempts > 0),
            "fallback_reason": fallback_reason,
            "credential_channel": self.credential_report().get(last_provider, {}).get("channel", ""),
        })
        return result_base

    def _estimate_cost(self, spec: ProviderSpec, usage: Dict[str, int]) -> Dict[str, Any]:
        pricing = spec.pricing or {}
        inp = pricing.get("input_per_million")
        out = pricing.get("output_per_million")
        if inp is None or out is None:
            return {"amount": None, "verified": False, "currency": pricing.get("currency", "CNY")}
        amount = (usage["prompt"] / 1_000_000.0) * float(inp) + \
                 (usage["completion"] / 1_000_000.0) * float(out)
        return {"amount": round(amount, 6), "verified": bool(pricing.get("verified", False)),
                "currency": pricing.get("currency", "CNY")}
