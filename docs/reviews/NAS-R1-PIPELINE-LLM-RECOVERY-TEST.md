# NAS-R1 — Pipeline LLM Recovery Test

- **Branch**: `r1/pipeline-deepseek-flash-recovery`
- **Base**: `e32d2e6` (`origin/main`, 2026-10-25)
- **Base code/test commit**: `e49d131c557772f141ece7d8d7648e21eb64580c`
- **R1-FIX code/test commit**: `beb4e9e475f994358ede115e31249f895b4463e4`
- **Status**: Gate 1 PASS; 20-sample structural run PASS; R1-FIX F2–F5 implemented; **not merged, not wired into production cron**
- **Scope**: isolated worktree `/tmp/r1_worktree`; production model configuration, cron, event ledger, and SMTP were not modified

## 0. Conclusion

The Python report pipeline can now call a real model directly, without importing QwenPaw's
private credential/decryption code. The operator supplied two process environment variables:

- `siliconflow_api_key` → primary `https://api.siliconflow.cn/v1`, model `deepseek-ai/DeepSeek-V4.1-Flash`
- `rightcode_bybt` → fallback `https://rightapi.ai/v1`, model `deepseek-flash`

Both returned HTTP 200 with valid JSON on real calls. A 20-sample frozen run completed
40/40 model calls with 0 retries, 0 fallback calls, 20/20 JSON parses, 20/20 structural
evidence-gate acceptances, 20/20 business analyses, and 0 score-integrity errors.

That old artifact did **not** persist a separate factual-verification status. It must not be
read as "20/20 facts verified". The new harness now records `verified` separately from
`accepted_structural`, and the two known negatives (R1S07 hearsay, R1S13 date conflict) are
explicitly `needs_verification`. The 20/20 number is therefore a structural/JSON/evidence
result only.

## 1. Why "the system model" was not directly callable before

QwenPaw's provider registry is an **agent-runtime** facility. Its provider credentials are
stored encrypted (`ENC:`) and are intended to be used by QwenPaw itself, not by arbitrary
Python scripts running in a separate process. The report pipeline is a Python program; it
cannot inherit the agent's provider session or decrypt that store with least privilege.

Before R1, the pipeline used a plaintext 4-entry OpenCode key pool. That pool returned
HTTP 400 for every entry from 2026-10 and is now permanently disabled as a provider and as
a fallback. The correct minimal bridge is therefore an operator-injected environment
variable (or a 0600 key file), not reuse of QwenPaw's encrypted registry.

This change does **not** reconfigure the user's existing QwenPaw models or keys. It adds a
separate, explicit credential channel for the Python pipeline.

## 2. Code changes (commit `e49d131c557772f141ece7d8d7648e21eb64580c`)

### 2.1 New adapter — `skills/utils/llm_pipeline_adapter.py`

- `PipelineLLMClient` with provider specs, logical model aliases, and a hard policy layer.
- Credentials resolve from, in order: primary env name, alias env names, then a 0600 file
  named by `*_FILE`. The adapter never writes a secret to disk, logs, traces, or errors.
- `CredentialRef` supports `env_aliases` so both the operator's current names
  (`siliconflow_api_key`, `rightcode_bybt`) and the documented production names
  (`PIPELINE_LLM_API_KEY`, `PIPELINE_LLM_FALLBACK_KEY`) work.
- Error classification: `http_400`, `auth`, `rate_limit`, `server`, `timeout`,
  `empty_content`, `invalid_json`, `credential_*`, `budget_*`, `provider_disabled`.
- Per-task and per-process HTTP caps, wall-clock cap, request timeout, retry counts,
  provider-attempt counts, token usage, and sanitized trace records.
- JSON-mode probe requires a parsed object; empty HTTP 200 is `invalid_json`, not success.
- `enable_thinking: false` is set **only for SiliconFlow** after a verified probe. The same
  flag makes RightCode return HTTP 200 with empty content, so it is not set there.

### 2.2 Active client — `skills/utils/llm_client.py`

- Old OpenCode implementation renamed to `LegacyOpenCodeClient` and marked deprecated.
- `LLMClient` is now an alias for `PipelineLLMClient`, so existing callers
  (`get_llm_client()`, `client.chat(...)`) keep working.
- `check_llm_config()` now reports the provider-agnostic status, credential channels, and
  blockers without exposing secret values.
- `test_llm_connection()` works unchanged against the adapter.

### 2.3 Config — `config/llm_providers.yaml`

- Primary `siliconflow`, fallback `rightcode-bybt`, fallback allowed only when the primary
  is unavailable.
- Disabled: `opencode`, `opencode-keyless`, `gpt-5.4-xhigh`.
- No secret is stored in this file. Pricing is explicitly marked `verified: false`; no
  currency amount is asserted.

### 2.4 R1-FIX — F2–F5 implementation

F2–F5 are implemented below. The user explicitly waived F1's dedicated paid-fallback
cost statistics and daily monetary budget ("正常运行一两天就知道了，没必要专门去统计").
No daily cost ledger, daily token budget, or monetary projection was added. The adapter keeps
only the counters required for the F2 health/degraded signal and the existing optional
per-process cap API (unset by default); it does not claim a billed total.

### F2 — availability and fallback boundaries

- `available` is no longer a synonym for "primary key exists". The client now exposes
  `primary_available`, `fallback_available`, `any_usable`, and
  `degraded_using_paid_fallback`. `available` remains for old callers and means "any usable
  channel"; health checks can no longer report a fallback-only system as fully primary-normal.
- `check_llm_config()` returns `primary_available`, `fallback_available`, `any_usable`,
  `degraded_using_paid_fallback`, `primary_blocker`, `paid_fallback_allowed`, and
  `paid_fallback_calls`. Extraction and filtering health logs print the degraded fields.
- Fallback is controlled by an explicit `fallback_on` whitelist, not "every error". Allowed:
  model 400/unavailable, 401/403, 402 quota, 404 endpoint, 429, 5xx, timeout, connection,
  invalid/empty response, invalid JSON, and credential/file failures. **Not** allowed:
  `bad_request` (bad prompt), `unknown_model_override` (caller bug), budget/wall-clock
  exhaustion, or provider-not-configured. A bad prompt now fails instead of silently paying
  the fallback.
- A model id belonging to another provider is mapped to the current provider's model before
  sending. This fixes the class of bug where a fallback received SiliconFlow's model id.
- Fallback is capped per task (`max_fallback_attempts_per_task: 1`). The old paid-call cap
  API remains optional; setting it to 0 means no extra monetary cap (per user direction).

### F3 — credential-file hardening and no secret bytes

- `CredentialRef.resolve_with_channel()` requires a **regular file**, rejects symlinks,
  requires owner-only `0400`/`0600` permissions, and rejects files outside the configured
  allowed directories (project root, `/run/secrets`, `PIPELINE_LLM_SECRET_DIR`, or an explicit
  `allowed_dirs` list). The file is not read until all checks pass.
- `key_used` and the trace now contain only `provider:channel` (e.g.
  `siliconflow:env:...`), never a masked or partial secret. `credential_report` reports the
  channel/blocker, not key fragments.
- New tests cover 0600 accepted, 0644 rejected, symlink rejected, and outside-directory
  rejected.

### F4 — verification state, not a false "verified" claim

- `run_20_real.py` no longer overwrites `published_at` with the fixture date. It keeps the
  extractor's date when present; otherwise it labels the fixture date
  `published_at_source=fixture_fallback`.
- `assess_verification()` keeps the event's own date, detects an explicit `M月D日` marker in
  the article that conflicts with the event/collection date, and detects hearsay markers in
  the event's own fact/evidence. Either condition yields `needs_verification`, not `verified`.
- R1S07 (听说/传闻 growth claim) and R1S13 (article says 9月13日; fixture date 9月26日) now
  produce `needs_verification`. Deterministic evidence:
  `tests/r1/artifacts/fact_verification_negative_cases.json`; 0 HTTP, 0 tokens.
- The old real summary is not retroactively relabelled. Its `accepted` field is documented
  as structural only; future `run_20_real.py` output adds `accepted_structural`, `verified`,
  `needs_verification`, and per-event verification reasons.

### F5 — fault-injection and concurrency

- HTTP process budget is now reserved atomically under the same lock that counts requests.
  Previously the check and increment were separate critical sections, so concurrent tasks
  could overrun `max_http_requests_per_process`.
- Tests exercise: model 400 (no retry, fallback), 401 fallback, 402 fallback, 429 retry,
  5xx retry, timeout, empty content exhaustion → fallback, invalid JSON → fallback, missing
  credential → fallback, bad request suppression, unknown model suppression, fallback attempt
  cap, paid-call cap, cross-provider model mapping, and concurrent process-budget atomicity.
- No paid model call was made in R1-FIX. All new tests use a simulated transport.

## 3. Credential and safety properties

- Real keys exist only in the process environment; no key was copied into the repo,
  config, test fixture, artifact, or report.
- Artifact scan for `sk-...` / `ENC:` patterns: 0 matches.
- Policy test `test_secret_never_appears_in_error_or_key_used` passes and additionally
  asserts `key_used == "siliconflow:override"` with no secret bytes in the full status.
- `check_llm_config()` prints channel labels, never values.
- File credentials are checked before read: regular file, non-symlink, owner-only
  0400/0600, inside an allowed directory.
- If credentials are absent, the adapter returns a structured blocker and does not retry.

## 4. Gate 1 — real connectivity

| Probe | Provider | Model | HTTP | Parsed | Latency | Tokens |
|---|---|---|---|---|---:|---:|
| Primary JSON | siliconflow | `deepseek-ai/DeepSeek-V4.1-Flash` | 200 | yes | 1,445 ms | 130 |
| Fallback JSON | rightcode-bybt | `deepseek-flash` | 200 | yes | 2,549 ms | 47 |
| Primary, thinking off | siliconflow | `deepseek-ai/DeepSeek-V4.1-Flash` | 200 | yes | ~800 ms | 35 |
| Fallback, thinking off | rightcode-bybt | `deepseek-flash` | 200 | **no** (empty content) | ~3,000 ms | 0 |

Integration smoke through the real production import path:

- `get_llm_client().available == True`, 2 usable providers
- `check_llm_config()` → `keys_found=True`, `key_count=2`, provider mode
  `provider_agnostic_r1`, `credential_blockers=[]`
- `test_llm_connection()` → `llm_config_ok=True`, `api_reachable=True`,
  `parsed_json_ok=True`, no error

**Gate 1: PASS.**

## 5. Gate 2 — 20 frozen samples, real models

Fixture: `tests/fixtures/r1_samples_20.json` (20 samples across instant retail, beauty,
platform, brand, and macro topics). Summary:
`tests/r1/artifacts/20_sample_real_summary.json`.

| Metric | Result |
|---|---:|
| Samples | 20 |
| JSON parsed | 20 / 20 |
| Accepted after structural evidence gate | 20 / 20 (pre-F4 artifact) |
| Verified facts (separate F4 status) | not measured in this old artifact |
| Rejected | 0 |
| Business analyses returned | 20 / 20 |
| Analysis-to-event pairing check | 20 / 20 |
| Events with all 8 dimensions | 20 / 20 |
| Weighted-score math errors | 0 |
| Score-range errors | 0 |
| Priority split | P0 0 / P1 5 / P2 5 / ARCHIVE 10 |
| Action levels | watch 8 / test 2 / archive 10 |

Examples of coherent pairing:

- “阿里为即时零售设定占电商GMV 30%的新目标” → analysis discusses Taobao Shangou resource
  tilt, shelf/subsidy opportunity, and fulfillment-density risk for Watsons.
- “美团闪购宣布未来三年投入300亿元支持即时零售行业伙伴” → P1, watch.
- “珀莱雅2026年上半年营收53.75亿元蝉联国货美妆榜首” → ARCHIVE, but the analysis still
  explains the indirect assortment/bargaining implication rather than inventing a direct one.

**Pairing check definition**: at least one exact number from the event fact appears in
`watsons_impact`, or at least two non-generic title bigrams appear. This is a deterministic
guard, not a human semantic review; a false positive is possible when generic industry
numbers recur. Human review of the 20 outputs is still required before R2.

## 6. Counters, retries, and cost

Final run (`20_sample_real_summary.json`):

| Counter | Value |
|---|---:|
| Task calls | 40 |
| HTTP requests | 40 |
| Provider attempts | 40 |
| Retries | 0 |
| Error events | 0 |
| Fallback calls | 0 |
| Prompt tokens | 45,311 |
| Completion tokens | 29,827 |
| Total tokens | 75,138 |
| Wall time (sum of latencies) | 283,948 ms |
| Providers used | siliconflow only |

Additional billed calls outside the final summary:

- Gate 1 probes: 130 + 47 + 35 + 0 = 212 tokens.
- Integration smoke: 1 call; usage was not captured.
- Development attempts before the final run: one 600 s timeout, one exception after
  analysis start, and one complete 40-call run before the pairing metric was corrected.
  Approximate total development calls across all attempts: ~136. Exact per-attempt token
  totals were not persisted and **must be reconciled in the provider console**.

No monetary cost is claimed. `pricing.verified` is `false`; billing must come from the
SiliconFlow/RightCode console.

## 7. Retry / error policy (tested)

| Condition | Behavior |
|---|---|
| HTTP 400 model unavailable | No retry for that provider; circuit-break; try fallback if allowed |
| HTTP 400 bad prompt / caller bug | No retry, **no fallback** (explicitly suppressed) |
| Other provider's model id | Mapped to current provider's model; never forwarded |
| HTTP 401/403 auth | No retry; provider disabled for the run |
| HTTP 402 quota | No retry; provider disabled for the run |
| HTTP 429 | At most 1 retry if retry budget remains |
| HTTP 5xx | Up to 2 transient retries |
| Timeout / connection error | Up to 2 transient attempts, bounded by wall clock |
| HTTP 200 empty content | `invalid_json`; bounded empty-content retry, then fallback |
| Invalid JSON | No blind re-request loop; classified and fallback-eligible |
| Missing / `ENC:` credential | Structured blocker; fallback may be used if available |
| Secret file symlink / 0644 / outside dir | Structured blocker before read; no request attempted |
| Per-task / per-process cap | Hard stop; no request emitted |
| Disabled provider | Never selected, including as fallback |
| All providers fail | One sanitized terminal error; no expensive GPT auto-call |

## 8. Tests

- `python3 -m unittest discover -s tests/r1 -p 'test_*.py' -v` → **38 tests, OK**
  (34 adapter/policy + 4 fact-verification).
- Simulation harness `--mode sim` → 20 samples, 18 structurally accepted; rejects are the
  intentional negative fixtures (`R1S07` empty evidence, `R1S13` future event date). The
  new verification field reports `needs_verification` separately; this confirms the evidence
  gate is not bypassed.
- `tests/r1/check_negative_cases.py` → both R1S07 and R1S13 `needs_verification`, reasons
  persisted in `fact_verification_negative_cases.json`; 0 HTTP, 0 tokens.
- Real harness `tests/r1/run_20_real.py` was not re-run in R1-FIX (no billable calls). Its
  old 20/20 result remains structural only, not a 20/20 factual verification.

## 9. Rollback

The branch is isolated; production `main` and production runtime are untouched.

To abandon R1:

```bash
cd /app/working/projects/watsons-retail-intel
git worktree remove /tmp/r1_worktree
git branch -D r1/pipeline-deepseek-flash-recovery
```

To roll back only the R1-FIX commit while keeping the branch for review:

```bash
git revert beb4e9e475f994358ede115e31249f895b4463e4
```

The base R1 commit is `e49d131c557772f141ece7d8d7648e21eb64580c`; revert it only if the
whole adapter is being abandoned.

Rollback restores the previous `llm_client.py`; `config/llm_providers.yaml` and the adapter
are additions and can be removed independently. No data migration is involved.

## 10. Limits / not covered

- The 20 samples are frozen fixtures, not a production day's full article pool.
- Only one model per provider was exercised.
- The fallback path was probed directly but was **not** exercised by a forced primary
  outage in the final 20-sample run. R1-FIX exercises fallback only with simulated transport.
- F1 paid-fallback cost statistics and a daily monetary budget were explicitly waived by the
  user; no billed-cost total is claimed. The optional `set_max_paid_fallback_calls` API is
  unset by default (0 = no extra cap).
- The pairing metric is deterministic and can produce false positives.
- No shadow run against production collection/scoring, no cron change, no ledger write,
  no SMTP send, no R2 work.
- Exact billed cost is unresolved; only token counts from API responses are recorded.
- The 20/20 structural acceptance in the old real artifact is **not** a 20/20 factual
  verification. R1-FIX adds the missing verification state but does not retroactively rerun
  the paid 20-sample test.

## 11. Next gate (requires review)

R2 should be a **shadow run on one production day**: collect and process the same day with
the adapter, compare event count/quality against the current pipeline, and still avoid
switching production. Do not merge or change cron before ChatGPT review of this document.

## 12. Reproduction

```bash
# Env vars are supplied by the operator; never echo or write them.
cd /tmp/r1_worktree
PYTHONPATH=".venv_packages:.:$PYTHONPATH" /app/venv/bin/python3 \
  -m unittest tests.r1.test_llm_adapter_policy -v
PYTHONPATH=".venv_packages:.:$PYTHONPATH" /app/venv/bin/python3 \
  tests/r1/run_20_samples.py --mode sim
PYTHONPATH=".venv_packages:.:$PYTHONPATH" /app/venv/bin/python3 \
  tests/r1/check_negative_cases.py   # 0 HTTP, 0 tokens
PYTHONPATH=".venv_packages:.:$PYTHONPATH" /app/venv/bin/python3 \
  tests/r1/run_20_real.py      # makes real, billable calls
```
