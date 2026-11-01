#!/usr/bin/env python3
"""Read-only AIHOT raw-material bridge -> Watsons shadow intake.

AIHOT is the source of truth for fetch status, article identity and body.
This adapter does NOT call AIHOT's public selected API, LLM, Cron or SMTP.
It does not write AIHOT PostgreSQL or the Watsons production data tree.

Run with --fixture to check the contract without a database. In production,
connect using a restricted PostgreSQL SELECT-only role via AIHOT_DSN.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = "aihot-raw-bridge-v1"
# No SQL schema names come from user input. Bind all filter values separately.
READ_SQL = """
SELECT a.id AS id, a.identity_key, a.url, a.title, a.author,
       a.published_at, a.publication_time, a.discovered_at,
       a.updated_at, a.revision, a.content_hash, a.excerpt,
       a.body_text, a.body_status, a.backfill, a.backfill_reason,
       a.source_id, s.name AS source_name, s.tier AS source_tier,
       s.participation_mode
FROM articles a
JOIN sources s ON s.id = a.source_id
WHERE a.updated_at >= %s::timestamptz
  AND a.updated_at < %s::timestamptz
  AND a.source_id = ANY(%s::text[])
ORDER BY a.updated_at, a.id
LIMIT %s
"""


def parse_aware(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("AIHOT timestamps must include an explicit timezone")
    return dt.astimezone(SHANGHAI)


def iso(value):
    v = parse_aware(value)
    return v.isoformat(timespec="seconds") if v else ""


def canonical_url(url):
    """Normalize tracking-only variants; retain material query parameters."""
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            return ""
        host = parts.hostname.lower()
        netloc = host
        if parts.port and not (parts.scheme == "https" and parts.port == 443 or
                              parts.scheme == "http" and parts.port == 80):
            netloc += ":" + str(parts.port)
        query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                                 if not (k.lower().startswith("utm_") or
                                         k.lower() in ("from", "spm", "share", "fbclid", "gclid"))))
        return urlunsplit((parts.scheme.lower(), netloc, parts.path.rstrip("/") or "/", query, ""))
    except (ValueError, TypeError):
        return ""


def normalize(row, as_of):
    """One AIHOT article material becomes one Watsons source article."""
    aid = str(row.get("id") or "")
    url = canonical_url(str(row.get("url") or ""))
    title = str(row.get("title") or "").strip()
    if not aid or not title or not url:
        return None
    published = parse_aware(row.get("published_at"))
    discovered = parse_aware(row.get("discovered_at"))
    updated = parse_aware(row.get("updated_at"))
    if not discovered or not updated:
        return None
    if published is None:
        status = "unknown_time"
    else:
        lag = (as_of - published.date()).days
        status = ("future" if lag < -1 else
                  "in_window" if lag <= 2 else
                  "near_window" if lag <= 7 else "old")
    body = row.get("body_text") if row.get("body_status") == "ok" else None
    body = body if isinstance(body, str) else ""
    excerpt = row.get("excerpt") or ""
    source_id = str(row.get("source_id") or "")
    return {
        "article_id": "AH_" + aid,
        "title": title,
        "url": url,
        "source_name": str(row.get("source_name") or source_id),
        "source_type": "aihot_raw",
        "collector": "aihot_postgres_readonly",
        "source_tier": str(row.get("source_tier") or "T2"),
        "published_at": published.isoformat(timespec="seconds") if published else "",
        "published_at_source": "aihot_source_record" if published else "unknown",
        "published_at_confidence": "high" if published else "unknown",
        "collected_at": discovered.isoformat(timespec="seconds"),
        "discovered_at": discovered.isoformat(timespec="seconds"),
        "updated_at": updated.isoformat(timespec="seconds"),
        "summary": str(excerpt)[:3000],
        "content": body,
        "time_status": status,
        "freshness_status": "newly_discovered" if status == "in_window" else (
            "week_fallback" if status == "near_window" else "bootstrap_seen"
        ),
        "matched_keywords": [],
        "raw": {
            "provider": "AIHOT",
            "aihot_article_id": aid,
            "aihot_identity_key": str(row.get("identity_key") or ""),
            "aihot_revision": int(row.get("revision") or 1),
            "aihot_content_hash": str(row.get("content_hash") or ""),
            "aihot_source_id": source_id,
            "aihot_participation_mode": row.get("participation_mode"),
            "aihot_publication_time_precision": row.get("publication_time"),
            "aihot_body_status": row.get("body_status"),
            "aihot_backfill": bool(row.get("backfill")),
            "aihot_backfill_reason": row.get("backfill_reason"),
            "article_canonical_url": url,
            "body_provided_by_original": bool(body),
        },
    }


def select_articles(rows, as_of):
    unique = {}
    invalid = 0
    for row in rows:
        result = normalize(row, as_of)
        if result is None:
            invalid += 1
            continue
        key = result["article_id"]
        old = unique.get(key)
        if old is None or (
            result["raw"]["aihot_revision"], result["updated_at"]
        ) > (old["raw"]["aihot_revision"], old["updated_at"]):
            unique[key] = result
    articles = sorted(unique.values(), key=lambda a: a["article_id"])
    return articles, invalid


def read_pg(dsn, since, until, source_ids, max_rows):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("Install optional psycopg[binary]>=3 for PostgreSQL bridge") from exc
    # Even if credentials have excess privileges, this transaction cannot write.
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=8) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            with conn.cursor() as cur:
                cur.execute(READ_SQL, (since, until, source_ids, max_rows + 1))
                rows = cur.fetchall()
    if len(rows) > max_rows:
        raise RuntimeError("Too many rows: increase --max-rows or split the time window; no output written")
    return rows


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".aihot-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def run(args):
    as_of = date.fromisoformat(args.as_of)
    start = parse_aware(args.since)
    end = parse_aware(args.until)
    if not start or not end or start >= end:
        raise ValueError("--since and --until must be increasing timezone-aware ISO timestamps")
    ids = sorted(set(args.source_id or []))
    if not ids:
        raise ValueError("At least one --source-id is mandatory; never export the AI demo/unrelated sources")
    if args.fixture:
        rows = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("articles", [])
        if not isinstance(rows, list):
            raise ValueError("Fixture must be an array or object with articles array")
        rows = [row for row in rows if isinstance(row, dict) and
                str(row.get("source_id") or "") in ids and
                start <= parse_aware(row.get("updated_at")) < end]
        if len(rows) > args.max_rows:
            raise RuntimeError("Fixture result exceeds --max-rows")
    else:
        dsn = os.environ.get("AIHOT_READONLY_DSN")
        if not dsn:
            raise RuntimeError("AIHOT_READONLY_DSN is required (read-only role; never committed)")
        rows = read_pg(dsn, start, end, ids, args.max_rows)
    articles, invalid = select_articles(rows, as_of)
    counts = Counter(a["time_status"] for a in articles)
    manifest = {
        "schema": SCHEMA_VERSION, "mode": "fixture" if args.fixture else "postgres_readonly",
        "as_of": str(as_of), "updated_since": start.isoformat(),
        "updated_until_exclusive": end.isoformat(),
        "source_ids": ids, "raw_rows": len(rows), "article_count": len(articles),
        "invalid_rows": invalid, "time_status_counts": dict(counts),
        "with_original_body": sum(bool(a["content"]) for a in articles),
        "with_source_published_time": sum(bool(a["published_at"]) for a in articles),
        "empty_or_unknown_dates": sum(not a["published_at"] for a in articles),
        "not_a_production_run": True, "llm_calls": 0,
        "warning": "All original source materials are internal-only. Do not redistribute full body text.",
    }
    if args.write_shadow:
        output_dir = Path(args.output_dir or "").resolve()
        if "shadow" not in output_dir.parts and not any("shadow" in part for part in output_dir.parts):
            raise ValueError("--output-dir must name an isolated shadow directory")
        if output_dir == Path("/").resolve():
            raise ValueError("Refusing root output")
        atomic_json(output_dir / "raw_articles_all.json",
                    {"metadata": {"provider": "AIHOT", "shadow": True,
                                  "as_of": str(as_of)}, "articles": articles})
        atomic_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of", required=True, help="Business day YYYY-MM-DD (Asia/Shanghai)")
    p.add_argument("--since", required=True, help="updated_at inclusive, ISO timestamp with timezone")
    p.add_argument("--until", required=True, help="updated_at exclusive, ISO timestamp with timezone")
    p.add_argument("--source-id", action="append", required=True, help="AIHOT source id; repeatable")
    p.add_argument("--max-rows", type=int, default=2000)
    p.add_argument("--fixture", help="Offline DB-shaped rows JSON, no network")
    p.add_argument("--write-shadow", action="store_true", help="Explicitly write staging JSON")
    p.add_argument("--output-dir", help="Mandatory when --write-shadow; must be isolated shadow")
    args = p.parse_args(argv)
    if args.max_rows < 1 or args.max_rows > 100000:
        p.error("--max-rows must be 1..100000")
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except Exception as e:
        # Never print DSN or credentials. Error messages from psycopg might
        # echo host info; redact complete connection details in caller logs.
        print("AIHOT bridge failed: " + type(e).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
