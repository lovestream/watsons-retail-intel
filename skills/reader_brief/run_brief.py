#!/usr/bin/env python3
"""run_brief.py — 周期读者简报流水线：事实包 → P05 → P08 → P09 → HTML。

链路（docs/15、docs/16）：
- weekly：事件事实包（本周增量）→ P05 → P08 → P09 → HTML(+MP3 附件由发送层负责)
- monthly：P14 月度跨周综合（程序门）→ P05（月度选题）→ P08 → P09
- yearly：P15 年度跨月综合 → P05 → P08 → P09

来源门 incomplete 时不发送：只允许 --draft 产出内部草稿并显式标记。
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from skills.reader_brief.facts_pack import build_from_events, build_from_synthesis
from skills.reader_brief.editor import build_topic_plan, write_brief
from skills.reader_brief.p09_reviewer import review, summarize_gate
from skills.reader_brief.render_mobile import render

logger = logging.getLogger(__name__)


def _load(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


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
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--draft", action="store_true",
                    help="来源门不完整时仍产出内部草稿（不得发送）")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)

    outdir = Path(a.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    synth = _load(Path(a.synthesis_in)) if a.synthesis_in else None
    deliverable = True
    notes = []

    if a.pack_in:
        pack = _load(Path(a.pack_in))
        if synth:
            pack["synthesis"] = synth.get("synthesis")
    elif a.period_type in ("monthly", "yearly"):
        if not synth:
            print("ERROR: 月/年报必须先有 P14/P15 综合包（--synthesis-in）",
                  file=sys.stderr)
            return 3
        if synth.get("status") == "incomplete" or not synth.get("synthesis"):
            if not a.draft:
                print(f"BLOCKED: {a.period_type} 来源门 incomplete "
                      f"{synth.get('gate')}", file=sys.stderr)
                return 4
            deliverable = False
            notes.append("来源门 incomplete：本文件仅为内部草稿，不得发送")
        pack = build_from_events(a.events_dir, a.start, a.end,
                                 period_type=a.period_type,
                                 max_facts=140 if a.period_type != "yearly" else 160)
        pack["synthesis"] = synth.get("synthesis")
    else:
        pack = build_from_events(a.events_dir, a.start, a.end,
                                 period_type="weekly", max_facts=80)

    if not pack["facts"]:
        print("BLOCKED: 事实包为空（无可核验事实），不生成简报", file=sys.stderr)
        return 4

    plan = _load(Path(a.topic_plan_in)) if a.topic_plan_in else None
    if plan is None:
        if a.no_llm:
            plan = {"topics": [], "_meta": {"prompt": "P05", "skipped": True}}
        else:
            plan = build_topic_plan(
                pack, a.send_at,
                previous=synth.get("synthesis") if synth else None,
                published_fingerprints=_load(Path(a.previous_fingerprints))
                if a.previous_fingerprints else [])
    (outdir / "topic_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    if a.no_llm:
        print("ERROR: --no-llm 只用于规则自检，不能产出简报", file=sys.stderr)
        return 5

    brief, emeta = write_brief(pack, a.send_at, topic_plan=plan)
    (outdir / "reader_brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")

    gate, rmeta = review(brief, pack, a.send_at, emeta.get("final_verdict"))
    (outdir / "p09_review.json").write_text(
        json.dumps({"gate": gate, "meta": rmeta}, ensure_ascii=False, indent=1,
                   default=str), encoding="utf-8")

    if gate["status"] == "block":
        print(f"BLOCKED by P09: {summarize_gate(gate)}", file=sys.stderr)
        return 2
    if gate.get("brief"):
        brief = gate["brief"]
        (outdir / "reader_brief.json").write_text(
            json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")

    html = render(brief, pack, brief.get("period_type"), brief.get("period_start"),
                  brief.get("period_end"),
                  audio_note="附本期口播音频" if deliverable else "",
                  generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    (outdir / "brief.html").write_text(html, encoding="utf-8")

    manifest = {"period_type": a.period_type, "start": a.start, "end": a.end,
                "send_at": a.send_at, "deliverable": deliverable,
                "notes": notes, "gate": gate["status"],
                "gate_summary": summarize_gate(gate),
                "pack_sha256": pack["pack_sha256"],
                "facts": len(pack["facts"]),
                "p05": plan.get("_meta"), "p08": {k: v for k, v in emeta.items()
                                                  if k != "first_pass"},
                "p09": {"prompt": rmeta.get("prompt"),
                        "version": rmeta.get("prompt_version"),
                        "rounds": rmeta.get("rounds")},
                "stats": emeta.get("final_verdict", {}).get("stats")}
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=1, default=str))
    return 0 if deliverable else 6


if __name__ == "__main__":
    raise SystemExit(main())
