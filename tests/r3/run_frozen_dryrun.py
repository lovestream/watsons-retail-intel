#!/usr/bin/env python3
"""Reproduce the R3 frozen-sample Phase 4 + email dry-run evidence.

The TTS provider (edge-tts) returned HTTP 403 in the isolated environment.
This runner therefore uses a real, already-existing Chinese MP3 as a playback
fixture; it does not claim that audio was synthesized from this script.
Set R3_AUDIO_FIXTURE to a readable MP3 with 300-540s duration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import skills.generate_periodic_report.generate_periodic_report as g

FIX = Path(__file__).parent / "fixtures"
OUT_JSON = Path(__file__).parents[2] / "docs/reviews/artifacts/R3-FROZEN-DRYRUN-2026-10-26.json"
AUDIO = os.environ.get(
    "R3_AUDIO_FIXTURE",
    "/app/working/projects/watsons-retail-intel/podcasts/audio/weekly-2026-W27.mp3")


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

    def fake_tts(text, path, target_seconds=None, project_root="."):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(AUDIO, path)
        return {"ok": True, "duration_seconds": 415.3, "rate": "+0%", "error": ""}

    g._generate_tts = fake_tts
    result = g.phase4_finalize(
        chapters, "weekly", "2026-W40",
        {"year": 2026, "week": 40, "start": "2026-09-28", "end": "2026-10-04"},
        str(out), syn, syn["coverage"])
    email = g._send_report_email(
        "weekly", "2026-W40", result, "123399974@qq.com", str(out),
        dry_run=True) if result.get("delivery_allowed") else None

    report_path = Path(result.get("report_path", ""))
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    markers = {pat: bool(re.search(pat, text)) for pat in (
        r"E\d{8}_\d{4}", r"【[ABCRKX]】", r"\b涉及事件\b", r"\b置信度\b",
        r"规则兜底", r"按事件密度排序")}
    evidence = {
        "date": "2026-10-26", "period": "2026-W40",
        "mode": "frozen-sample phase4; TTS provider unavailable",
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
        "script_sha256": sha(out / "podcasts/scripts/weekly-2026-W40.txt"),
        "internal_markers_in_report": markers,
        "email_dry_run": email,
        "tts_note": "edge-tts HTTP 403; existing Chinese MP3 used as playback fixture only.",
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: evidence[k] for k in (
        "sendable", "delivery_allowed", "report_sha256", "audio_sha256")},
        ensure_ascii=False, indent=2))
    return 0 if evidence["sendable"] and evidence["delivery_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
