"""用同一份 brief JSON 重建事实包（不重跑 LLM），复检 + 重渲染三份邮件。"""
import json
from pathlib import Path

from skills.reader_brief import claim_checker, facts_pack, render_mobile

TODAY = "2026-10-29"

JOBS = [
    dict(name="W43", ptype="weekly", out="/tmp/rb_out/W43",
         label="2026年第43周（10/19–10/25）",
         src=("synthesis", "/tmp/e1_report/data/periodic/weekly/2026-W43/periodic_synthesis.json"),
         max_facts=80),
    dict(name="M2026-07", ptype="monthly", out="/tmp/rb_out/M2026-07",
         label="2026年7月（7/1–7/31）", src=("events", "2026-07-01", "2026-07-31"),
         max_facts=100),
    dict(name="Y2026", ptype="yearly", out="/tmp/rb_out/Y2026",
         label="2026年年初至今（1/1–9/30）", src=("events", "2026-01-01", "2026-09-30"),
         max_facts=60),
]

for job in JOBS:
    data = json.loads(Path(job["out"] + ".json").read_text(encoding="utf-8"))
    brief = data["brief"]
    if job["src"][0] == "synthesis":
        synth = json.loads(Path(job["src"][1]).read_text(encoding="utf-8"))
        pack = facts_pack.from_synthesis(synth, job["ptype"], max_facts=job["max_facts"])
    else:
        pack = facts_pack.from_events(
            "/app/working/projects/watsons-retail-intel/data/events",
            job["src"][1], job["src"][2], job["ptype"], max_facts=job["max_facts"])
    report = claim_checker.check(brief, pack, job["ptype"], TODAY)
    html = render_mobile.render(brief, pack, job["ptype"], job["label"], TODAY)
    Path(job["out"] + ".html").write_text(html, encoding="utf-8")
    data.update({"facts": pack["facts"], "check": report,
                 "pack_meta": {k: pack[k] for k in ("period_type", "period", "coverage")}})
    Path(job["out"] + ".json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(job["name"], "facts", len(pack["facts"]), "pass", report["pass"],
          "errors", len(report["errors"]), "warn", len(report["warnings"]), len(html))
