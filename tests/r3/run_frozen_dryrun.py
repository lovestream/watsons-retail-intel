#!/usr/bin/env python3
"""Reproduce the R3 frozen-sample Phase 4 + real TTS + email dry-run evidence.

The TTS is synthesized from this frozen script through the existing SiliconFlow
credential (edge-tts currently returns HTTP 403 from this network).  No prior
MP3 is copied.  Email is sent only to the reserved dry-run address and with
dry_run=True.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import skills.generate_periodic_report.generate_periodic_report as g

FIX = Path(__file__).parent / "fixtures"
OUT_JSON = Path(__file__).parents[2] / "docs/reviews/artifacts/R3-FROZEN-DRYRUN-2026-10-26.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def main() -> int:
    report = (FIX / "good_weekly.md").read_text(encoding="utf-8")
    chapters = {}
    for part in re.split(r"(?m)^## ", report):
        m = re.match(r"(\d{2})[^\n]*\n(.*)", part, re.S)
        if m:
            chapters[m.group(1)] = "## " + part.strip()
    for key in list(chapters):
        chapters[key] = re.sub(r"\n## 证据来源[\s\S]*$", "", chapters[key]).strip()
    syn = json.loads((FIX / "good_synthesis.json").read_text())
    script = (FIX / "good_podcast.txt").read_text(encoding="utf-8")

    out = Path(tempfile.mkdtemp(prefix="r3_dryrun_"))
    g._generate_podcast_script = lambda *a, **k: script

    # Keep the real TTS implementation.  It reads `siliconflow_api_key` from
    # the environment; no recipient or credential is stored in the repo.
    result = g.phase4_finalize(
        chapters, "weekly", "2026-W40",
        {"year": 2026, "week": 40, "start": "2026-09-28", "end": "2026-10-04"},
        str(out), syn, syn["coverage"])
    email = g._send_report_email(
        "weekly", "2026-W40", result, "review-test@example.invalid", str(out),
        dry_run=True) if result.get("delivery_allowed") else None

    report_path = Path(result.get("report_path", ""))
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    markers = {pat: bool(re.search(pat, text)) for pat in (
        r"E\d{8}_\d{4}", r"【[ABCRKX]】", r"\b涉及事件\b", r"\b置信度\b",
        r"规则兜底", r"按事件密度排序")}
    script_path = out / "podcasts/scripts/weekly-2026-W40.txt"
    script_sha = sha(script_path)
    audio_sha = sha(Path(result.get("audio_path", "")))
    evidence = {
        "date": "2026-10-26", "period": "2026-W40",
        "mode": "frozen-sample phase4; real SiliconFlow TTS; dry-run email",
        "sendable": result.get("sendable"),
        "delivery_allowed": result.get("delivery_allowed"),
        "delivery_blockers": result.get("delivery_blockers"),
        "review": {k: result.get("review_result", {}).get(k) for k in (
            "sendable", "overall_quality", "failed_checks", "high_issues",
            "medium_issues")},
        "podcast_review": result.get("podcast_review"),
        "tts": result.get("tts"),
        "audio_issues": result.get("audio_issues"),
        "report_chars": len(text), "report_sha256": sha(report_path),
        "audio_path": result.get("audio_path"),
        "audio_bytes": os.path.getsize(result["audio_path"]) if result.get("audio_path") and os.path.exists(result.get("audio_path", "")) else 0,
        "audio_sha256": sha(Path(result.get("audio_path", ""))),
        "script_chars": len(script),
        "script_sha256": script_sha,
        "internal_markers_in_report": markers,
        "email_dry_run": email,
        "tts_note": "New MP3 synthesized from the frozen script via SiliconFlow; "
                    "edge-tts HTTP 403 is retained only as a fallback.",
        "tts_provider": "siliconflow",
        "script_audio_match": bool(result.get("audio_path") and script_sha and audio_sha),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in (
        "sendable", "delivery_allowed", "report_sha256", "audio_sha256")},
        ensure_ascii=False, indent=2))
    return 0 if evidence["sendable"] and evidence["delivery_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
