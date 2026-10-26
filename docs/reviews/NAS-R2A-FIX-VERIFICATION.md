# NAS R2-A-FIX Verification — Truth, Dates, Scheduling, Alerts

**Status:** implemented, tested, documented and pushed to the isolated branch; awaiting ChatGPT re-review
**Branch:** `r2a/shadow-scheduler`  
**Base commit:** `5f78c1a1e8e1bc8f512ca3889d9be4945a5d53ee`  
**Production impact:** none. No Cron, SMTP, report email, supervisor install, formal ledger, Docker or production data was changed.

## 1. Scope

This round implements the R2-A review items G1–G6 only:

| Item | Required outcome | Implementation |
|---|---|---|
| G1 | Unified date/verification policy; do not infer an occurrence date and call it verified; monotonic eligibility | `skills/report_policy.py`; wired into temporal validation, novelty, scoring, reporting count |
| G2 | Truthful reportable count, not “priority ≠ ARCHIVE” | `ops/r2a/run_state.reportable_count()` delegates to `is_reportable_event()`; daily report receives the count |
| G3 | Scheduled runs must execute collection/filtering, not start from a pre-existing extract pool | launcher/scheduler reject `--mode scheduled --start-step extract`; default starts at `health_check` |
| G4 | Independent alert outlet that cannot be silenced by a shell wrapper; distinguish collection failure from a quiet day | `ops/r2a/alerts.py`; watchdog `--loop` + `status.json` + local sink |
| G5 | Distinguish “pipeline completed” from “LLM analysis really succeeded” | manifest fields and watchdog warning for degraded analysis |
| G6 | Hard-block out-of-domain verticals without killing real instant-retail/beauty events | `score_events.apply_hard_downgrade()` + shared policy; passenger-car, apparel and foreign-subject gates |

The existing collection, timeliness, novelty and scoring mechanisms were reused. No new report system was built.

## 2. What changed

### G1 — shared truth policy and date semantics

New `skills/report_policy.py` is the single source for:

- `evaluate_verification()` — produces `verification_status`, `needs_verification`, `verification_reasons`, `verification_date_source`, `verification_event_date`, `date_conflict`, `hearsay_markers`.
- date source separation: `occurred`, `announced`, `effective`, `inferred_content_date`, `published_at`, `missing`.
- `cap_eligibility()` — monotonic downgrade: review/reference cannot be promoted back to core by novelty or score.
- `is_reportable_event()` — core + verified + new/updated + fact/evidence/source_url.
- `is_out_of_domain_event()` — shared passenger-car/apparel gate.

`temporal_fact_validation.py` now:

- writes the verification fields above;
- caps eligibility after temporal classification;
- uses the closed day’s end for historical replays, not the wall clock;
- does not turn a future content date into `event_occurred_at`;
- labels missing/inferred dates and keeps them in review/reference instead of pretending they are verified.

`event_novelty_check.py` and `score_events.py` re-apply the monotonic cap. A ledger hit or high score cannot reset a review/reference event to core.

### G2 — reportable count

`run_state.reportable_count()` now counts only the shared reportable predicate. It excludes:

- cross-day repeated events;
- reference/review/tracking events;
- needs-verification and date-conflicted events;
- events without fact, evidence or source URL.

`generate_daily_report.py` also records `reportable_count` and the top reportable signals for the run manifest.

### G3 — scheduled start guard

- Launcher and scheduler default start step changed from `extract` to `health_check`.
- Scheduled mode rejects `extract` with:
  `scheduled mode must start at health_check/collect, not at a pre-existing extract pool`.
- Supervisor config starts at `health_check`; a scheduled run additionally requires a `collect` step, so skipping collection is still rejected even if the launcher starts at health_check.
- Scheduled smoke with the isolated fake pipeline: exit 0, `collection_health=ok`, `analysis_mode=llm`, `degraded_analysis=false`.

### G4 — watchdog and alert sink

`ops/r2a/alerts.py` writes, in an isolated state directory only:

- `alerts/events.jsonl` — append-only alert/recovered events;
- `alerts/current.json` — deduplicated active alerts;
- `alerts/notification_preview.txt` — local preview, never emailed.

Watchdog now writes the real exit code to `watchdog/status.json`, supports `--loop`, and deduplicates repeated failures. Recovery is appended when the condition clears. No real alert email is sent.

Collection health is now explicit:

- `ok` — collection step succeeded and was not an all-sources-down result;
- `failed` / `no_source_success` — zero articles is an operational failure, not a quiet news day;
- `not_run` — missing collection step.

### G5 — LLM/pipeline distinction

Manifest fields:

- `pipeline_completed`
- `content_ready`
- `collection_health`
- `llm_extract_health`
- `llm_analyze_health`
- `analysis_mode`
- `degraded_analysis`

A rule-only fallback can still complete the pipeline, but it is marked `degraded_analysis` and emits `llm_analysis_degraded` / `llm_extraction_degraded` warnings into the sink. It is never described as full LLM success.

### G6 — domain gate

Hard ARCHIVE when the event has no strong instant-retail/beauty signal and contains:

- passenger-car retail: 乘用车, 乘联会, 乘联分会, 汽车销量, 新能源车销量, 车企销量, 汽车零售;
- apparel/footwear: 波司登, 羽绒服, 服饰品牌, 服装品牌, 鞋服品牌, 安踏, 李宁, 优衣库, 海澜之家, 太平鸟;
- foreign subject/market: 印度, 英国, 美国, 日本, 韩国, 法国, 德国, 澳大利亚, 加拿大, 土耳其, 巴西, 欧洲市场, 北美市场, 东南亚市场, 中东市场, plus existing HK/TW/overseas signals.

A title that also clearly names the mainland market is not filtered by the foreign-title rule. Strong instant-retail/beauty signals protect genuine cross-domain items (for example “美团闪购汽车用品增长”).

## 3. Frozen-window verification

Window: closed **2026-10-11** data, isolated from production. Input was 7 cleaned articles and the original 50-event raw pool. The original ledger snapshot was used for the before/after comparison.

Commands:

```sh
cd /tmp/r2a_wt
PYTHONPATH=".:$PYTHONPATH" /app/venv/bin/python3 -m ops.r2a.launcher \
  --project-root /tmp/r2a_fix_after \
  --state-dir /tmp/r2a_fix_after/state \
  --date 2026-10-11 --run-id r2a-fix-after \
  --mode shadow --start-step temporal_fact_validation \
  --end-step analyze --force

PYTHONPATH=".:$PYTHONPATH" /app/venv/bin/python3 -m ops.r2a.audit_shadow \
  --project-root /tmp/r2a_fix_after --date 2026-10-11 \
  --out /tmp/r2a_fix_after_audit.json
```

### 3.1 Before / after

| Metric | R2-A baseline | R2-A-FIX |
|---|---:|---:|
| Cleaned articles | 7 | 7 |
| Raw events | 50 | 50 |
| Temporal events | 50 | 50 |
| Event ID lost / added / duplicated | 0 / 0 / 0 | 0 / 0 / 0 |
| P2+ events (old priority view) | 11 | 10 (P1 1 + P2 9) |
| Eligibility distribution | 39 ARCHIVE | **archive 40 / reference 1 / review 3 / tracking 1 / core 5** |
| Reportable count (new truthful predicate) | 11 (old definition) | **5** |
| Domain-false-positive reportable | 2 (0044, 0018) | **0** |
| Events with no usable date at all | 21 occurred missing (some had announced) | **2** (0038, 0039) |
| Verified / needs-verification | not systematically enforced | **32 / 18** |
| Analysis pairing low-overlap | 0 | **0** |

The old “11” was `priority ≠ ARCHIVE`, not a verified-new-fact count. The new count intentionally separates priority from verification.

### 3.2 Reportable set

Only five events pass the new predicate:

- `E20261011_0026` — 多平台双11简化促销玩法、降低参与门槛
- `E20261011_0023` — 即时零售巨头配送卡在最后100米门禁
- `E20261011_0036` — 北京“十五五”国际消费中心城市规划，引导即时零售/直播电商
- `E20261011_0024` — 微信小店首次参加双11
- `E20261011_0035` — 富力海珠城美妆快闪节招募启动

All five are mainland, in-domain, have a usable announced/effective date, verified evidence and fresh/updated novelty.

### 3.3 G6 negative and positive cases

| Event | Result | Reason |
|---|---|---|
| `E20261011_0044` 乘用车零售 | ARCHIVE | `域外垂直[passenger_vehicle_retail]` |
| `E20261011_0018` 波司登门店 | ARCHIVE | `域外垂直[apparel_vertical]` |
| `E20261011_0021` / `0022` / `0019` / `0020` apparel | ARCHIVE | `域外垂直[apparel_vertical]` |
| `E20261011_0047` 7-Eleven India | ARCHIVE | `事件主体/市场为港台海外` |
| `E20261011_0048` 7-Eleven India | ARCHIVE | `事件主体/市场为港台海外` |
| `E20261011_0050` Boots UK | ARCHIVE | `事件主体/市场为港台海外` |

True-domain positive tests and the five reportable IDs above confirm the gate is not a blanket “retail = reject” rule. The automated test includes “乘用车车主在美团闪购购买汽车用品，即时零售订单增长” as a positive instant-retail case.

### 3.4 Cross-day repeat

The same 50-event pool was replayed as 2026-10-12 against the ledger updated by the 10-11 run.

| Metric | Result |
|---|---|
| Novelty status | 50 / 50 `repeated` |
| Final eligibility | 50 / 50 `reference` |
| Reportable count | **0** |
| Priority leakage | P2/P1 priority values remain, but eligibility is reference; no event is reportable |

This verifies G2 does not count cross-day duplicates merely because their priority is still P1/P2.

## 4. Test and fault-injection evidence

Command:

```sh
cd /tmp/r2a_wt
PYTHONPATH=".:$PYTHONPATH" /app/venv/bin/python3 -m unittest discover -s tests/r2a -v
```

Result: **43 tests, OK**.

Coverage includes:

- announced date is verified without inventing `event_occurred_at`;
- missing date, future occurrence, inferred date conflict, old announcement, hearsay;
- monotonic cap cannot promote review/reference;
- reportable predicate rejects reference, repeated, needs-verification, missing evidence;
- domain positive/negative cases and foreign-subject case;
- reportable count from actual JSON file;
- watchdog failed run → exit 1, sink event, `status.json`;
- duplicate failed checks → no duplicate active alert;
- recovery → `recovered` event and empty active list;
- zero articles with failed collection → critical `no_signal_without_verified_collection`;
- degraded LLM analysis → warning in sink without failing exit;
- real subprocess launcher: `collect_failure` → exit 2, status failed, reason `no_signal_without_verified_collection`;
- real subprocess launcher: `llm_degraded` → exit 0, success but `degraded_analysis=true`;
- scheduled extract-only launch rejected;
- scheduled full-workflow smoke requires and records the collect step;
- supervisor config starts at `health_check`, keeps watchdog exit status.

A separate CLI check produced:

```
$ ... launcher --mode scheduled --start-step extract ...
scheduled mode must start at health_check/collect, not at a pre-existing extract pool
```

## 5. Artifacts

Sanitized evidence:

- `docs/reviews/artifacts/NAS-R2A-FIX-shadow-audit-2026-10-11.json`
- `docs/reviews/artifacts/NAS-R2A-FIX-repeat-audit-2026-10-12.json`

The artifacts contain counts, IDs, reasons and stage integrity only. They contain no credentials, article bodies, email addresses or production paths.

## 6. Constraints respected

- No production configuration changed.
- No Cron switched; no supervisor installed.
- No SMTP or report email sent.
- No formal event ledger modified.
- No Docker or formal data volume modified.
- No keys copied, printed or embedded.
- No merge to `main`.
- No R2-B work started.

## 7. Residual findings and limits

- 0038 and 0039 genuinely have no usable date and remain `needs_verification`; they are excluded from the reportable set, not deleted.
- 18 of 50 events are `needs_verification` for a mix of missing, inferred, old or hearsay reasons. This is a review queue, not a reportable queue.
- The frozen window is an old/closed pool; five reportable events is a strict quality result, not a production-volume benchmark.
- Supervisor config remains a draft. Installation requires separate authorization after re-review.
- The 7-Eleven/Boots foreign gate was added as a safety extension of G6; it is covered by tests and the 10-11 audit.
- No real billable LLM sample run was performed in this round.

## 8. Review readiness

R2-A-FIX is code-complete, tested, documented and pushed to the isolated branch `r2a/shadow-scheduler`. No production switch, supervisor install or Cron change should occur before ChatGPT re-review.
