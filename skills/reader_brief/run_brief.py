#!/usr/bin/env python3
"""run_brief.py — 周期读者简报流水线：事实包 → P05 → P08 → P09 → HTML。

交付状态只有三态（docs/reviews/NAS-LLM-PROMPT-INTEGRATION-INDEPENDENT-REVIEW §P0-01）：
- approved        ：P09 完成主张级复核且确定性门通过 → 才允许 sendable=true
- hold_for_review ：P09 未完成/降级 → 可出预览 HTML，禁止自动发送
- blocked         ：程序门/P09 存在未解决问题 → 不产出可发送物

月/年报必须携带 P14/P15 综合包（status=complete）；degraded/incomplete 一律 hold，
除非显式 --draft（仅内部草稿，永远不可发送）。
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from skills.reader_brief.facts_pack import build_from_events
from skills.reader_brief.editor import build_topic_plan, write_brief
from skills.reader_brief.p09_reviewer import review, summarize_gate
from skills.reader_brief.render_mobile import render

logger = logging.getLogger(__name__)


def _load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(path: Path, data) -> str:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                               default=str), encoding="utf-8")
    return path.name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period-type", required=True,
                    choices=["weekly", "monthly", "yearly"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--send-at", default=date.today().isoformat())
    ap.add_argument("--events-dir", default="data/events")
    ap.add_argument("--pack-in", default="")
    ap.add_argument("--synthesis-in", default="")
    ap.add_argument("--topic-plan-in", default="")
    ap.add_argument("--previous-fingerprints", default="")
    ap.add_argument("--audio-file", default="")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args(argv)

    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    synth = _load(a.synthesis_in)
    draft_note = ""

    if a.period_type in ("monthly", "yearly"):
        if a.pack_in:
            print("ERROR: 月/年报禁止 --pack-in 绕过 P14/P15 来源门", file=sys.stderr)
            return 3
        if not synth:
            print("ERROR: 月/年报必须提供 --synthesis-in（P14/P15 结果）",
                  file=sys.stderr)
            return 3
        if synth.get("status") != "complete" or not synth.get("synthesis"):
            if not a.draft:
                print(f"BLOCKED: {a.period_type} 来源门 {synth.get('status')}："
                      f"{synth.get('gate')}", file=sys.stderr)
                return 4
            draft_note = (f"来源门 {synth.get('status')}：本文件仅为内部草稿，"
                          "不得发送")
            logger.warning(draft_note)
        pack = build_from_events(a.events_dir, a.start, a.end,
                                 period_type=a.period_type,
                                 max_facts=140 if a.period_type != "yearly" else 160)
        pack["synthesis"] = synth.get("synthesis")
        pack["synthesis_meta"] = {"status": synth.get("status"),
                                  "gate": synth.get("gate")}
    else:
        if synth:
            print("WARN: 周报忽略 --synthesis-in", file=sys.stderr)
        pack = _load(a.pack_in) if a.pack_in else build_from_events(
            a.events_dir, a.start, a.end, period_type="weekly", max_facts=80)

    if not pack.get("facts"):
        print("BLOCKED: 事实包为空（无可核验事实），不生成简报", file=sys.stderr)
        return 4

    plan = _load(a.topic_plan_in)
    if plan is None:
        plan = build_topic_plan(
            pack, a.send_at,
            previous=(synth or {}).get("comparison"),
            published_fingerprints=_load(a.previous_fingerprints) or [])
    _dump(outdir / "topic_plan.json", plan)

    if plan.get("topics") and not plan.get("published_fact_ids"):
        print("BLOCKED: P05 没有任何带合法 fact_id 的可发布选题", file=sys.stderr)
        return 2

    brief, emeta = write_brief(pack, a.send_at, topic_plan=plan)
    _dump(outdir / "reader_brief.json", brief)

    gate, rmeta = review(brief, pack, a.send_at, emeta.get("final_verdict"))
    _dump(outdir / "p09_review.json", {"gate": gate, "meta": rmeta})
    for i, raw in enumerate(rmeta.get("raw_reviews") or [], start=1):
        _dump(outdir / f"p09_round{i}.json", raw)
    if gate.get("brief"):
        _dump(outdir / "reader_brief_after_revise.json", gate["brief"])
    if gate.get("det_errors"):
        _dump(outdir / "p09_det_errors.json", gate["det_errors"])

    state = gate.get("state") or gate.get("status")
    if state == "blocked":
        print(f"BLOCKED: {summarize_gate(gate)}", file=sys.stderr)
        return 2
    if draft_note:
        state = "hold_for_review"

    audio_note, audio_meta = "", {"path": "", "exists": False}
    if a.audio_file:
        p = Path(a.audio_file)
        if p.exists() and p.stat().st_size > 0:
            sha = _sha256_file(str(p))
            audio_meta = {"path": str(p), "exists": True, "sha256": sha,
                          "bytes": p.stat().st_size}
            audio_note = f"附本期口播音频（sha256 {sha[:12]}）"
        else:
            audio_meta = {"path": str(p), "exists": False}
            logger.warning("音频文件不存在或为空，HTML 不写附音频声明")

    html = render(brief, pack, brief.get("period_type"), brief.get("period_start"),
                  brief.get("period_end"), audio_note=audio_note,
                  generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
                  basis_note=(draft_note or None))
    (outdir / "brief.html").write_text(html, encoding="utf-8")

    manifest = {
        "schema_version": 2,
        "period_type": a.period_type, "start": a.start, "end": a.end,
        "send_at": a.send_at,
        "delivery_state": state,
        "sendable": state == "approved",
        "deliverable": state == "approved",
        "gate_summary": summarize_gate(gate),
        "pack_sha256": pack.get("pack_sha256"),
        "facts": len(pack.get("facts") or []),
        "synthesis_status": (synth or {}).get("status"),
        "notes": [draft_note] if draft_note else [],
        "p05": plan.get("_meta"),
        "p05_rejected_topics": plan.get("rejected_topics") or [],
        "p08": {k: v for k, v in emeta.items() if k != "first_pass"},
        "p09": {"state": state, "decision": gate.get("decision"),
                "rounds": rmeta.get("rounds"),
                "degraded": bool(rmeta.get("degraded")),
                "prompt_version": rmeta.get("prompt_version"),
                "model": rmeta.get("model")},
        "audio": audio_meta,
        "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _dump(outdir / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=1, default=str))
    return 0 if state == "approved" else 7


if __name__ == "__main__":
    raise SystemExit(main())
