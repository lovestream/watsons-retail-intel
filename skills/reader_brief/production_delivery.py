#!/usr/bin/env python3
"""Fail-closed bridge from periodic long report to the P05/P08/P09 reader email.

Never sends SMTP. All period IDs, facts, P14/P15 gates and approved JSON are
validated before the existing sender is allowed to use the generated HTML.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

from skills.reader_brief.facts_pack import build_from_synthesis


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dates(kind, period):
    if kind == "weekly":
        return period["start"], period["end"]
    year = int(period["year"])
    if kind == "yearly":
        return f"{year}-01-01", f"{year}-12-31"
    month = int(period["month"])
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return f"{year}-{month:02d}-01", (first_next - timedelta(days=1)).isoformat()


def _period_id(kind, period):
    start, _ = _dates(kind, period)
    if kind == "weekly":
        y, w, _ = date.fromisoformat(start).isocalendar()
        return f"{y}-W{w:02d}"
    return start[:7] if kind == "monthly" else str(period["year"])


def prepare_approved_brief(kind, period, root, synthesis, cross=None,
                           audio_path="", timeout_seconds=900):
    """Build and verify a reader brief; returns approved=False on every error.

    No controlled fixture or hand-made P1 scores are loaded. The week uses
    actual periodic_synthesis; month/year use P14/P15 plus actual data/events.
    """
    root = Path(root).resolve()
    start, end = _dates(kind, period)
    pid = _period_id(kind, period)
    outdir = root / "data" / "runs" / "periodic" / kind / pid / "reader_brief"
    outdir.mkdir(parents=True, exist_ok=True)
    if kind in ("monthly", "yearly") and (
            not cross or cross.get("status") != "complete" or not cross.get("synthesis")):
        return {"approved": False, "error": "cross_period_not_complete", "period_id": pid}
    if kind == "weekly" and not synthesis:
        return {"approved": False, "error": "no_weekly_synthesis", "period_id": pid}

    cmd = [sys.executable, "-m", "skills.reader_brief.run_brief",
           "--period-type", kind, "--start", start, "--end", end,
           "--send-at", datetime.now(timezone(timedelta(hours=8))).date().isoformat(),
           "--events-dir", str(root / "data" / "events"),
           "--outdir", str(outdir)]
    if audio_path:
        cmd += ["--audio-file", str(Path(audio_path).resolve())]
    if kind == "weekly":
        pack = build_from_synthesis(synthesis, "weekly", max_facts=80)
        if not pack.get("facts"):
            return {"approved": False, "error": "no_source_linked_weekly_facts", "period_id": pid}
        # An LLM or secondary report cannot turn a headline into verified source.
        # Every exported fact must carry a real link and claim text.
        for fact in pack["facts"]:
            u = (fact.get("source") or {}).get("url") or ""
            if (not str(u).startswith(("https://", "http://")) or
                    not fact.get("text") or not fact.get("claim_id")):
                return {"approved": False, "error": "source_or_claim_missing",
                        "period_id": pid}
        pack["period"] = {"start": start, "end": end}
        pack_path = outdir / "canonical_weekly_pack.json"
        pack_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd += ["--pack-in", str(pack_path)]
    else:
        cross_path = outdir / "cross_period.json"
        cross_path.write_text(json.dumps(cross, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd += ["--synthesis-in", str(cross_path)]
    try:
        proc = subprocess.run(cmd, cwd=str(root), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              timeout=timeout_seconds, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"approved": False, "error": "reader_brief_runtime_error",
                "detail": str(exc)[:300], "period_id": pid}
    manifest_path, html_path = outdir / "manifest.json", outdir / "brief.html"
    if proc.returncode != 0 or not manifest_path.is_file() or not html_path.is_file():
        return {"approved": False, "error": "reader_brief_not_approved",
                "returncode": proc.returncode, "stderr": proc.stderr[-350:],
                "period_id": pid}
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        checks = [
            m.get("delivery_state") == "approved", m.get("sendable") is True,
            m.get("period_type") == kind, m.get("start") == start,
            m.get("end") == end, m.get("html_sha256") == _sha(html_path),
            (outdir / "p09_review.json").is_file(),
            (outdir / "reader_brief.json").is_file(),
        ]
        if audio_path:
            audio = m.get("audio") or {}
            checks.append(audio.get("exists") is True and
                          audio.get("sha256") == _sha(audio_path))
        if not all(checks):
            return {"approved": False, "error": "manifest_integrity_failure",
                    "period_id": pid}
        return {"approved": True, "period_id": pid,
                "html_path": str(html_path), "manifest_path": str(manifest_path),
                "html_sha256": m["html_sha256"],
                "pack_sha256": m.get("pack_sha256"), "outdir": str(outdir)}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"approved": False, "error": "reader_brief_parse_failure",
                "detail": str(exc)[:200], "period_id": pid}
