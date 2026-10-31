#!/usr/bin/env python3
"""Repeat P05/P08/P09 on the SAME frozen real-source fact pack, no SMTP/TTS.

Strictly a text-editing repeatability experiment, NOT a production-release
certificate. Use isolated worktree and frozen canonical_weekly_pack.json.
Example:
  PYTHONPATH=.venv_packages:. python3 scripts/measure_reader_brief_stability.py \
    --pack-in data/shadow/w43/canonical_weekly_pack.json \
    --start 2026-10-19 --end 2026-10-25 --runs 10 \
    --outdir data/shadow/w43/stability
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description="Isolated W43 reader-brief stability suite")
    p.add_argument("--pack-in", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--send-at", default=date.today().isoformat())
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--outdir", required=True)
    p.add_argument("--timeout", type=int, default=1200)
    a = p.parse_args()
    if not (3 <= a.runs <= 20):
        p.error("runs must be 3..20 to bound costs; production threshold needs 10 or 20")
    pack_path = Path(a.pack_in).resolve()
    if not pack_path.is_file():
        p.error("frozen fact pack not found")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    if (pack.get("period_type") != "weekly" or
            (pack.get("period") or {}).get("start") != a.start or
            (pack.get("period") or {}).get("end") != a.end):
        p.error("pack period does not match requested dates")
    frozen_hash = sha(pack_path)
    dst = Path(a.outdir).resolve()
    dst.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(1, a.runs + 1):
        if sha(pack_path) != frozen_hash:
            raise RuntimeError("immutable source pack changed; stop replay")
        rd = dst / f"run_{i:02d}"
        rd.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "skills.reader_brief.run_brief",
               "--period-type", "weekly", "--start", a.start, "--end", a.end,
               "--send-at", a.send_at, "--pack-in", str(pack_path),
               "--outdir", str(rd)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=a.timeout, check=False)
            rc, tail = proc.returncode, proc.stderr[-500:]
        except subprocess.TimeoutExpired:
            rc, tail = 124, "timeout"
        mpath = rd / "manifest.json"
        jpath = rd / "p09_review.json"
        m, j = {}, {}
        try:
            if mpath.exists():
                m = json.loads(mpath.read_text(encoding="utf-8"))
            if jpath.exists():
                j = json.loads(jpath.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
        state = (m.get("delivery_state") or (j.get("gate") or {}).get("state")
                 or ("runtime_error" if rc else "unverifiable"))
        approved = (
            rc == 0 and state == "approved" and
            m.get("text_review_approved") is True and
            m.get("pack_sha256") == pack.get("pack_sha256") and
            (j.get("gate") or {}).get("state") == "approved" and
            (rd / "brief.html").is_file() and
            m.get("html_sha256") == sha(rd / "brief.html")
        )
        row = {"run": i, "returncode": rc, "state": state,
               "verified_text_approved": approved,
               "p09_rounds": (m.get("p09") or {}).get("rounds"),
               "p05_model": (m.get("p05") or {}).get("model"),
               "p08_model": (m.get("p08") or {}).get("model"),
               "p09_model": (m.get("p09") or {}).get("model"),
               "brief_sha256": sha(rd / "reader_brief.json")
               if (rd / "reader_brief.json").is_file() else None,
               "p09_issue_count": len((j.get("gate") or {}).get("issues") or []),
               "stderr_tail": tail}
        rows.append(row)
        (dst / "progress.json").write_text(
            json.dumps({"pack_file_sha256": frozen_hash, "runs": rows},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    approved = sum(x["verified_text_approved"] for x in rows)
    failures = [x for x in rows if not x["verified_text_approved"]]
    summary = {
        "kind": "text_only_10_20_replay_not_delivery_certificate",
        "pack_file_sha256": frozen_hash,
        "pack_claim_sha256": pack.get("pack_sha256"),
        "prompt_runs": len(rows), "verified_text_approved": approved,
        "approval_rate": round(approved / len(rows), 3),
        "failure_count": len(failures),
        "content_variants": len({x["brief_sha256"] for x in rows if x["brief_sha256"]}),
        "models": sorted({str(x[k]) for x in rows for k in
                          ("p05_model", "p08_model", "p09_model") if x[k]}),
        "runs": rows,
        "requires_independent_semantic_review": True,
        "smtp_sent": False, "audio_generated": False,
        "release_candidate_threshold": len(rows) >= 10 and approved / len(rows) >= 0.9,
        "note": "通过率不是事实正确率；须另测日期/跨主体/状态反例的零误放行"
    }
    (dst / "stability_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"},
                     ensure_ascii=False, indent=2))
    return 0 if summary["release_candidate_threshold"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
