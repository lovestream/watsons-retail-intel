"""Manifest and filesystem helpers shared by the R2-A scheduler tools.

All writes are atomic within the same directory.  Manifests intentionally
contain only operational metadata: no API keys, no SMTP credentials, no
article bodies, and no report text.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from skills.report_policy import is_reportable_event
except ImportError:  # direct invocation from the ops/r2a directory
    try:
        from report_policy import is_reportable_event  # type: ignore
    except ImportError:  # pragma: no cover - only when PYTHONPATH is absent
        is_reportable_event = None  # type: ignore

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore

CST = ZoneInfo("Asia/Shanghai") if ZoneInfo else timezone.utc
UTC = timezone.utc
SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(UTC)


def cst_now() -> datetime:
    return datetime.now(CST)


def iso(dt: Optional[datetime] = None) -> str:
    """Return an ISO-8601 timestamp with an explicit offset."""
    if dt is None:
        dt = utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def iso_cst(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = cst_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST).isoformat()


def parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON to ``path`` atomically (temp file + replace + fsync).

    The temp file is created in the destination directory so ``os.replace``
    cannot cross filesystems.  The file mode is restricted because manifests
    can contain run IDs and error messages, but never secrets.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a JSON object")
    return data


def count_list(path: Path, key: str) -> int:
    """Count entries in ``{"<key>": [...]}``; missing/invalid file -> 0."""
    try:
        data = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    value = data.get(key)
    return len(value) if isinstance(value, list) else 0


def event_counts(project_root: Path, date: str) -> Dict[str, int]:
    """Collect stage counts from the actual pipeline artifacts.

    These counts are deliberately independent from an agent's claim that the
    pipeline ran.  A manifest with ``agent_started=true`` is not proof of a
    complete event pool.
    """
    root = Path(project_root)
    return {
        "cleaned_articles": count_list(root / "data" / "cleaned" / date / "cleaned_articles.json", "articles"),
        "events_raw": count_list(root / "data" / "events" / date / "events_raw.json", "events"),
        "events_temporal": count_list(root / "data" / "events" / date / "events_temporal.json", "events"),
        "events_scored": count_list(root / "data" / "events" / date / "events_scored.json", "events"),
        "events_analyzed": count_list(root / "data" / "events" / date / "events_analyzed.json", "events"),
    }


def reportable_count(scored_path: Path) -> int:
    """Count events that could enter a report, using the display contract."""
    try:
        data = read_json(scored_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    events = data.get("events")
    if not isinstance(events, list):
        return 0
    if is_reportable_event is None:
        # Conservative fallback: without the shared policy, do not count
        # anything as a verified new fact.
        return 0
    return sum(1 for ev in events if is_reportable_event(ev))
