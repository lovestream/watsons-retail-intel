# R2-A: shadow runner, scheduler, and watchdog

This directory is an **isolated R2-A implementation**. It is not wired into
production Cron, QwenPaw, Docker, SMTP, or the event ledger.

## Why

The previous schedule depended on a QwenPaw agent receiving a prompt and
replying "已启动". That reply is not evidence that Python started, that the
event chain completed, or that the event pool is non-empty. R2-A moves the
boundary to a normal Python process and records the truth on disk.

## Components

| File | Purpose |
|---|---|
| `launcher.py` | Starts the pipeline in-process, takes a per-date lock, writes `running` and terminal manifests, counts stage artifacts, and returns a real exit code. Never sends email. |
| `scheduler.py` | Computes the next 07:00 Asia/Shanghai slot and starts the launcher detached. `--once` is testable; `--loop` is the supervised production mode. |
| `watchdog.py` | Reads manifests only. Alerts when no run exists, a run failed, a heartbeat is stale, the pool is empty/incomplete, or the last healthy run is older than 48 hours. |
| `audit_shadow.py` | Audits event-ID/date/evidence/score/novelty/analysis continuity in a shadow data directory. |
| `supervisor/watsons-retail-intel-r2a.conf` | Draft supervisor configuration. **Not installed.** |

## Manifest contract

`<state-dir>/runs/<date>/<run-id>/manifest.json`

- `status`: `running` -> `success` | `no_signal` | `failed`
- `exit_code`: `0` success/no_signal, `2` pipeline failure, `4` date lock held
- `heartbeat_at`: updated while running
- `counts`: cleaned / raw / temporal / scored / analyzed
- `reportable_count`: priority not ARCHIVE and eligibility not archive
- `steps`: status-only step view; no article bodies, prompts, or keys
- `send_email` is always `false`

## Direct invocation

```sh
PYTHONPATH=/app/working/projects/watsons-retail-intel \
/app/venv/bin/python3 -m ops.r2a.launcher \
  --project-root /app/working/projects/watsons-retail-intel \
  --state-dir /app/working/projects/watsons-retail-intel/data/ops \
  --date 2026-10-25 \
  --mode shadow \
  --start-step extract --end-step analyze
```

The launcher calls `skills.run_daily_pipeline.run_daily_pipeline` directly with
`send_email=False`, `recipient=None`.

## Scheduler

```sh
PYTHONPATH=/app/working/projects/watsons-retail-intel \
/app/venv/bin/python3 -m ops.r2a.scheduler \
  --project-root /app/working/projects/watsons-retail-intel \
  --state-dir /app/working/projects/watsons-retail-intel/data/ops \
  --once --ignore-window --now 2026-10-25T07:00:00+08:00 \
  --dry-run
```

`--loop` is kept alive by a supervisor. The scheduler does not parse chat
messages and does not ask an agent to start anything. It skips a duplicate date
if a terminal manifest already exists, and skips a running date to prevent
overlap.

## Watchdog

```sh
PYTHONPATH=/app/working/projects/watsons-retail-intel \
/app/venv/bin/python3 -m ops.r2a.watchdog \
  --state-dir /app/working/projects/watsons-retail-intel/data/ops \
  --max-age-hours 48 --stale-minutes 120 --require-scheduler
```

Exit `0` = healthy; exit `1` = at least one critical alert. An agent reply
"已启动" is not read and cannot make the watchdog green.

## Tests

```sh
PYTHONPATH=/app/working/projects/watsons-retail-intel \
/app/venv/bin/python3 -m unittest discover -s tests/r2a -v
```

The subprocess tests use `tests/r2a/fake_pipeline.py` only to exercise real
launcher exit codes and manifests without network calls. They do not claim the
production pipeline itself passed.

## Rollout / rollback

1. Keep this branch unmerged until ChatGPT review.
2. Install the supervisor unit only after approval; do not modify the existing
   production Cron.
3. Roll back by stopping/removing the supervisor programs; no event ledger,
   Docker, or SMTP state is touched by these tools.
4. Once the scheduler is trusted, the old agent cron can be disabled in a
   separate, explicitly approved change.
