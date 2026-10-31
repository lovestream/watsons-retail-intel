#!/usr/bin/env python3
"""Final-reader-brief -> deterministic spoken script -> MP3.

Uses the SAME P09-approved structured brief as the HTML. No second editorial
LLM can introduce new claims, figures, or sources after P09 approval.
Audio generation is synchronous and fail-closed; no SMTP is performed here.
"""
import hashlib
import json
import re
from pathlib import Path


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _voice_text(value):
    # Input is final structured plain text. Strip formatting, not numbers.
    value = re.sub(r"https?://\S+", "", str(value or ""))
    value = re.sub(r"[#*_`]+", "", value)
    return " ".join(value.split()).strip()


def build_script(brief):
    kind = {"weekly": "周", "monthly": "月", "yearly": "年"}.get(
        brief.get("period_type"), "周期")
    lines = [f"这里是即时零售与个护美妆经营{kind}报。"]
    for f in ("title", "one_line_takeaway"):
        if _voice_text(brief.get(f)):
            lines.append(_voice_text(brief[f]))
    for i, s in enumerate(brief.get("signals") or [], 1):
        lines.append(f"第{i}个变化。")
        for key in ("headline", "fact", "business_impact"):
            if _voice_text(s.get(key)):
                lines.append(_voice_text(s[key]))
    for c in brief.get("competitor_cards") or []:
        lines.append("再看竞对。")
        for key in ("name", "move", "why_matters"):
            if _voice_text(c.get(key)):
                lines.append(_voice_text(c[key]))
    acts = brief.get("priority_actions") or []
    if acts:
        lines.append("最后是有条件的经营动作。")
        for i, act in enumerate(acts, 1):
            lines.append(f"第{i}项。")
            for key in ("title", "precondition", "trial", "metric"):
                if _voice_text(act.get(key)):
                    lines.append(_voice_text(act[key]))
    watch = brief.get("watch_items") or []
    if watch:
        lines.append("最后留意这些触发条件。")
        for w in watch:
            for key in ("trigger", "business_relevance"):
                if _voice_text(w.get(key)):
                    lines.append(_voice_text(w[key]))
    lines.append("本期简报到这里。")
    return "\n".join(lines)


def generate(brief, outdir, root):
    """Generate audio from final P09-approved JSON; returns provenance or error."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    brief_path = outdir / "reader_brief.json"
    script_path = outdir / "podcast-script.txt"
    audio_path = outdir / "podcast-audio.mp3"
    result = {"exists": False, "generation": "from_p09_final_brief"}
    try:
        if not brief_path.is_file():
            return dict(result, error="final_brief_missing")
        persisted = json.loads(brief_path.read_text(encoding="utf-8"))
        if persisted != brief:
            return dict(result, error="final_brief_changed")
        script = build_script(brief)
        if not script or len(script) < 100:
            return dict(result, error="script_too_short")
        script_path.write_text(script, encoding="utf-8")

        # Use the project's existing free edge-tts generator and duration checks.
        from skills.generate_periodic_report.generate_periodic_report import _generate_tts
        tts = _generate_tts(script, str(audio_path), target_seconds=None,
                            project_root=str(root))
        if not tts.get("ok") or not audio_path.is_file() or audio_path.stat().st_size <= 1000:
            return dict(result, error="tts_failed", tts=tts)
        duration = tts.get("duration_seconds")
        if duration is None or duration <= 0:
            return dict(result, error="invalid_duration")
        return {
            "exists": True, "path": str(audio_path),
            "sha256": _digest(audio_path), "bytes": audio_path.stat().st_size,
            "source_brief_sha256": _digest(brief_path),
            "script_path": str(script_path), "script_sha256": _digest(script_path),
            "duration_seconds": duration, "generation": "from_p09_final_brief",
            "tts": tts,
        }
    except Exception as exc:
        return dict(result, error="audio_pipeline_exception",
                    detail=str(exc)[:180])
