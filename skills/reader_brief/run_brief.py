#!/usr/bin/env python3
"""run_brief.py — reader brief 编排：事实包 → 编辑 → 事实审查 → 手机 HTML。

用法示例：
  python -m skills.reader_brief.run_brief \
    --period-type weekly --source synthesis \
    --synthesis data/periodic/weekly/2026-W43/periodic_synthesis.json \
    --period-label "2026 年第 43 周（10/19–10/25）" \
    --out-html /tmp/out/W43.html --out-json /tmp/out/W43.json
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from skills.reader_brief import claim_checker, editor, facts_pack, render_mobile

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reader_brief")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--period-type", required=True,
                    choices=["weekly", "monthly", "yearly"])
    ap.add_argument("--source", default="synthesis",
                    choices=["synthesis", "events"])
    ap.add_argument("--synthesis", help="periodic_synthesis.json 路径")
    ap.add_argument("--events-dir", default="data/events")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--max-facts", type=int, default=80)
    ap.add_argument("--period-label", default="")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--out-html", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--allow-fallback", action="store_true",
                    help="LLM 失败时允许规则兜底（默认拒绝交付）")
    args = ap.parse_args(argv)

    if args.source == "synthesis":
        if not args.synthesis:
            logger.error("--synthesis 必填")
            return 2
        synth = json.loads(Path(args.synthesis).read_text(encoding="utf-8"))
        pack = facts_pack.from_synthesis(synth, args.period_type,
                                        max_facts=args.max_facts)
    else:
        if not (args.start and args.end):
            logger.error("--start/--end 必填")
            return 2
        pack = facts_pack.from_events(args.events_dir, args.start, args.end,
                                      args.period_type,
                                      max_facts=args.max_facts)

    if not pack["facts"]:
        logger.error("事实包为空，拒绝生成")
        return 3
    logger.info("事实包: %d 条事实, 周期 %s", len(pack["facts"]), args.period_type)

    try:
        brief = editor.compose(pack, args.period_type, args.today)
    except Exception as e:
        logger.error("编辑层失败: %s", e)
        if not args.allow_fallback:
            logger.error("未加 --allow-fallback，拒绝交付")
            return 4
        brief = editor.fallback_brief(pack, args.period_type)

    report = claim_checker.check(brief, pack, args.period_type, args.today)
    rounds = []
    for round_no in range(1, 3):
        if report["pass"]:
            break
        issues = [e for e in report["errors"] if e.get("severity") == "error"]
        logger.info("第 %s 轮事实修订：%d 个错误", round_no, len(issues))
        brief = editor.revise(brief, issues, pack, args.period_type, args.today)
        report = claim_checker.check(brief, pack, args.period_type, args.today)
        rounds.append({"round": round_no,
                       "errors_after": len(report["errors"])})
    html = render_mobile.render(brief, pack, args.period_type,
                               args.period_label, args.today)

    out_html, out_json = Path(args.out_html), Path(args.out_json)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    out_json.write_text(json.dumps({"brief": brief, "check": report, "revision_rounds": rounds,
                                    "pack_meta": {k: pack[k] for k in
                                                  ("period_type", "period",
                                                   "coverage")},
                                    "facts": pack["facts"]},
                                   ensure_ascii=False, indent=1),
                        encoding="utf-8")

    print(json.dumps({
        "html": str(out_html), "json": str(out_json),
        "html_chars": len(html),
        "pass": report["pass"],
        "errors": len(report["errors"]), "warnings": len(report["warnings"]),
        "editor_mode": brief.get("_meta", {}).get("editor_mode"),
        "stats": report["stats"],
    }, ensure_ascii=False))
    for e in report["errors"][:12]:
        print("  ERROR", e["code"], e["where"], e["message"])
    for w in report["warnings"][:8]:
        print("  WARN ", w["code"], w["where"], w["message"])
    return 0 if report["pass"] else 5


if __name__ == "__main__":
    sys.exit(main())
