# NAS-R1 — Pipeline LLM Recovery Test

- **Branch**: `r1/pipeline-deepseek-flash-recovery`
- **Base**: `e32d2e6` (`origin/main`, 2026-10-25)
- **Code/test commit**: `e49d131c557772f141ece7d8d7648e21eb64580c`
- **Status**: Gate 1 PASS; Gate 2 PASS on 20 frozen samples; **not merged, not wired into production cron**
- **Scope**: isolated worktree `/tmp/r1_worktree`; production model configuration, cron, event ledger, and SMTP were not modified

## 0. Conclusion

The Python report pipeline can now call a real model directly, without importing QwenPaw's
private credential/decryption code. The operator supplied two process environment variables:

- `siliconflow_api_key` → primary `https://api.siliconflow.cn/v1`, model `deepseek-ai/DeepSeek-V4.1-Flash`
- `rightcode_bybt` → fallback `https://rightapi.ai/v1`, model `deepseek-flash`

Both returned HTTP 200 with valid JSON on real calls. A 20-sample frozen run completed
40/40 model calls with 0 retries, 0 fallback calls, 20/20 JSON parses, 20/20 accepted events,
20/20 business analyses, and 0 score-integrity errors.

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

## 3. Credential and safety properties

- Real keys exist only in the process environment; no key was copied into the repo,
  config, test fixture, artifact, or report.
- Artifact scan for `sk-...` / `ENC:` patterns: 0 matches.
- Policy test `test_secret_never_appears_in_error_or_key_used` passes.
- `check_llm_config()` prints channel labels, never values.
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
| Accepted after evidence gate | 20 / 20 |
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
| HTTP 401/403 auth | No retry; provider disabled for the run |
| HTTP 402 quota | No retry; provider disabled for the run |
| HTTP 429 | At most 1 retry if retry budget remains |
| HTTP 5xx | Up to 2 transient retries |
| Timeout / connection error | Up to 2 transient attempts, bounded by wall clock |
| HTTP 200 empty content | `invalid_json`; bounded empty-content retry, then fallback |
| Invalid JSON | No blind re-request loop; classified and fallback-eligible |
| Missing / `ENC:` credential | Structured blocker; no request attempted |
| Per-task / per-process cap | Hard stop; no request emitted |
| Disabled provider | Never selected, including as fallback |
| All providers fail | One sanitized terminal error; no expensive GPT auto-call |

## 8. Tests

- `python3 -m unittest tests.r1.test_llm_adapter_policy -v` → **16 tests, OK**.
- Simulation harness `--mode sim` → 20 samples, 18 accepted; rejects are the intentional
  negative fixtures (`R1S07` empty evidence, `R1S13` future event date). This confirms the
  evidence gate is not bypassed.
- Real harness `tests/r1/run_20_real.py` → 20 accepted; the real extractor supplied valid
  evidence and normalized dates for those two fixtures rather than bypassing the gate.

## 9. Rollback

The branch is isolated; production `main` and production runtime are untouched.

To abandon R1:

```bash
cd /app/working/projects/watsons-retail-intel
git worktree remove /tmp/r1_worktree
git branch -D r1/pipeline-deepseek-flash-recovery
```

To roll back only the code commit while keeping the branch for review:

```bash
git revert e49d131c557772f141ece7d8d7648e21eb64580c
```

Rollback restores the previous `llm_client.py`; `config/llm_providers.yaml` and the adapter
are additions and can be removed independently. No data migration is involved.

## 10. Limits / not covered

- The 20 samples are frozen fixtures, not a production day's full article pool.
- Only one model per provider was exercised.
- The fallback path was probed directly, but not exercised by a forced primary outage in
  the final 20-sample run.
- The pairing metric is deterministic and can produce false positives.
- No shadow run against production collection/scoring, no cron change, no ledger write,
  no SMTP send, no R2 work.
- Exact billed cost is unresolved; only token counts from API responses are recorded.

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
  tests/r1/run_20_real.py      # makes real, billable calls
```
