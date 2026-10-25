#!/usr/bin/env python3
"""Local alert sink for the R2-A watchdog.

The production notification channel is intentionally *not* wired here.  This
module only records alerts in an isolated state directory so the watchdog's
exit code cannot be swallowed by a shell pipeline and so a later reviewer can
inspect what would have been notified.

Files (all under ``<state-dir>/alerts``):
* ``events.jsonl`` -- append-only alert/recovery events
* ``current.json`` -- currently active alerts (deduplicated)
* ``notification_preview.txt`` -- a human-readable local preview; never sent

No email, webhook, credential or article body is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import run_state

SCHEMA_VERSION = 1


def _alert_key(alert: Dict[str, Any]) -> str:
    """Stable identity for deduplication across repeated watchdog runs."""
    parts = [
        str(alert.get("code") or ""),
        str(alert.get("run_id") or ""),
        str(alert.get("date") or ""),
        str(alert.get("severity") or ""),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def _atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".alert-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_event(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load_current(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "active": []}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("active"), list):
            raise ValueError("invalid alert snapshot")
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "active": []}


def emit(result: Dict[str, Any], state_dir: Path, *, source: str = "watchdog",
         alert_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Persist new, still-active and recovered alerts.

    Repeated checks of the same broken run do not create duplicate active
    events.  When the condition disappears, a ``recovered`` event is appended.
    """
    alert_dir = Path(alert_dir) if alert_dir else Path(state_dir) / "alerts"
    event_path = alert_dir / "events.jsonl"
    current_path = alert_dir / "current.json"
    preview_path = alert_dir / "notification_preview.txt"

    previous = _load_current(current_path)
    previous_by_key: Dict[str, Dict[str, Any]] = {
        str(a.get("key")): a for a in previous.get("active", []) if isinstance(a, dict)
    }

    now = run_state.iso()
    new_alerts: List[Dict[str, Any]] = []
    active: List[Dict[str, Any]] = []
    recovered: List[Dict[str, Any]] = []

    all_alerts = list(result.get("alerts", []) or [])
    # Operational warnings (for example degraded LLM analysis) must also reach
    # the same sink; they are not silently dropped just because they are not
    # critical exit-code failures.
    all_alerts.extend(result.get("warnings", []) or [])
    for alert in all_alerts:
        if not isinstance(alert, dict):
            continue
        key = _alert_key(alert)
        item = {
            "key": key,
            "code": alert.get("code"),
            "severity": alert.get("severity"),
            "message": alert.get("message"),
            "run_id": alert.get("run_id"),
            "date": alert.get("date"),
            "manifest": alert.get("manifest"),
        }
        active.append(item)
        if key not in previous_by_key:
            record = {"ts": now, "event": "alert", "source": source, **item}
            _append_event(event_path, record)
            new_alerts.append(item)

    active_keys = {str(a.get("key")) for a in active}
    for key, old in previous_by_key.items():
        if key not in active_keys:
            record = {
                "ts": now,
                "event": "recovered",
                "source": source,
                "key": key,
                "code": old.get("code"),
                "run_id": old.get("run_id"),
                "date": old.get("date"),
                "severity": old.get("severity"),
                "message": old.get("message"),
            }
            _append_event(event_path, record)
            recovered.append(record)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now,
        "source": source,
        "status": result.get("status"),
        "ok": bool(result.get("ok")),
        "checked_at": result.get("checked_at"),
        "active": active,
        "new_count": len(new_alerts),
        "recovered_count": len(recovered),
    }
    _atomic_text(current_path, json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    lines = [
        "R2-A watchdog local notification preview (NOT SENT)",
        f"checked_at: {result.get('checked_at')}",
        f"status: {result.get('status')}",
        "",
    ]
    if active:
        lines.append("ACTIVE")
        for a in active:
            lines.append(
                f"- [{a.get('severity')}] {a.get('code')}: {a.get('message')} "
                f"(run={a.get('run_id') or '-'})"
            )
    else:
        lines.append("ACTIVE: none")
    if recovered:
        lines.append("")
        lines.append("RECOVERED")
        for r in recovered:
            lines.append(f"- {r.get('code')} (run={r.get('run_id') or '-'})")
    _atomic_text(preview_path, "\n".join(lines) + "\n")

    return {
        "sink_dir": str(alert_dir),
        "events_file": str(event_path),
        "current_file": str(current_path),
        "preview_file": str(preview_path),
        "new_count": len(new_alerts),
        "active_count": len(active),
        "recovered_count": len(recovered),
        "new_alerts": new_alerts,
        "recovered": recovered,
    }
