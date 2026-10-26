#!/usr/bin/env python3
"""R1 real-model 20-sample run (actual SiliconFlow + RightCode fallback).

Requires siliconflow_api_key / rightcode_bybt in the process env. Does not
print or persist either key. Output: tests/r1/artifacts/20_sample_real_summary.json
"""
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import yaml  # noqa: E402

from skills.utils.llm_pipeline_adapter import PipelineLLMClient  # noqa: E402
from skills.extract_events.extract_events import llm_extract_events  # noqa: E402
from skills.analyze_business_impact.analyze_business_impact import (  # noqa: E402
    llm_analyze_event, apply_analysis_downgrade,
)
from tests.r1.run_20_samples import (  # noqa: E402
    DIMENSIONS, assess_verification, evidence_errors, score_one,
)

log = logging.getLogger("r1-real")
logging.basicConfig(level=logging.WARNING)


def main():
    fixture = json.load(open(os.path.join(ROOT, "tests/fixtures/r1_samples_20.json"), encoding="utf-8"))
    samples = fixture["samples"]
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config/scoring.yaml"), encoding="utf-8"))
    client = PipelineLLMClient(config_path=os.path.join(ROOT, "config/llm_providers.yaml"))
    if not client.available:
        print(json.dumps({"result": "BLOCKED", "credential_report": client.credential_report()},
                         ensure_ascii=False, indent=2))
        sys.exit(2)

    rows, accepted = [], []
    def run_extract(s):
        article = {
            "article_id": s["sample_id"], "title": s["title"],
            "source_name": s["source_name"], "published_at": s["published_at"],
            "summary": s["summary"], "content": s["content_excerpt"],
        }
        extracted = llm_extract_events(article, client, log, model="pipeline-deepseek-flash")
        row = {"sample_id": s["sample_id"], "topic": s["topic"],
               "json_parsed": bool(extracted.get("llm_extracted")),
               "extract_failure_type": extracted.get("failure_type")}
        if not row["json_parsed"]:
            row["accepted"] = False
            row["rejection_reasons"] = ["json_parse_failed:%s" % extracted.get("failure_type")]
            return row
        events = extracted.get("events") or []
        if not events:
            row["accepted"] = False
            row["rejection_reasons"] = ["no_events"]
            return row
        ev = dict(events[0])
        # F4: never overwrite an extractor-provided date with the fixture date.
        # The fixture date is only a labelled fallback when extraction is silent.
        extracted_pub = str(ev.get("published_at") or "").strip()
        ev.update({
            "event_id": "E20261025_%04d" % int(re.sub(r"\D", "", s["sample_id"]) or "0"),
            "source_url": s["source_url"], "source_name": s["source_name"],
            "published_at": extracted_pub or s["published_at"],
            "published_at_source": "extracted" if extracted_pub else "fixture_fallback",
            "extraction_method": "llm",
        })
        row["verification"] = assess_verification(ev, s)
        errs = evidence_errors(ev, s)
        row["evidence_errors"] = errs
        if errs:
            row["accepted"] = False
            row["rejection_reasons"] = errs
            return row
        row["accepted"] = True
        row["_event"] = ev
        return row

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(run_extract, samples))
    accepted = [r["_event"] for r in rows if r.get("accepted")]

    for idx, ev in enumerate(accepted):
        scores, weighted, priority, reasons = score_one(ev, accepted, idx, cfg)
        ev.update({"scores": scores, "weighted_score": weighted,
                   "priority": priority, "downgrade_reasons": reasons})

    pair_ok = 0
    def run_analysis(row):
        if not row.get("accepted"):
            return row
        ev = row["_event"]
        ba = llm_analyze_event(ev, client, model="pipeline-deepseek-flash")
        ok = isinstance(ba, dict)
        if ok:
            ba = apply_analysis_downgrade(ba, ev)
            impact = ba.get("watsons_impact") or ""
            fact = ev.get("fact") or ""
            nums = set(re.findall(r"\d+(?:\.\d+)?%?", fact + " " + ev["event_title"]))
            num_hit = sorted(n for n in nums if n in impact)
            generic = {"即时", "时零", "零售", "美妆", "电商", "市场", "增长",
                       "上半", "半年", "亿元", "同比", "公司", "平台", "中国",
                       "行业", "品牌", "规模", "预计", "达到", "突破", "超过"}
            title = re.sub(r"[\s，。；：、“”（）()《》%]", "", ev["event_title"])
            grams = {title[i:i+2] for i in range(len(title) - 1)} - generic
            gram_hit = sorted(g for g in grams if g in impact)
            matched = bool(num_hit) or len(gram_hit) >= 2
            row["analysis_ok"] = True
            row["analysis_pairing_ok"] = matched
            row["pairing_evidence"] = {"numbers": num_hit, "title_bigrams": gram_hit[:8]}
            row["action_level"] = ba.get("action_level")
            row["watsons_impact"] = impact
            ev["business_analysis"] = ba
        else:
            row["analysis_ok"] = False
            row["analysis_pairing_ok"] = False
            row["action_level"] = ""
        return row

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(run_analysis, rows))
    pair_ok = sum(1 for r in rows if r.get("analysis_pairing_ok"))

    weighted_errors, range_errors = [], []
    for ev in accepted:
        expected = sum(cfg["weights"][d] * ev["scores"][d] for d in DIMENSIONS)
        if abs(expected - ev["weighted_score"]) > 0.01:
            weighted_errors.append({"event_id": ev["event_id"], "expected": round(expected, 4),
                                    "actual": ev["weighted_score"]})
        for d in DIMENSIONS:
            if not (0 <= ev["scores"][d] <= 5):
                range_errors.append({"event_id": ev["event_id"], "dim": d,
                                     "value": ev["scores"][d]})

    st = client.get_status()["stats"]
    summary = {
        "mode": "real",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": "siliconflow deepseek-ai/DeepSeek-V4.1-Flash; fallback rightcode-bybt deepseek-flash",
        "samples_total": len(samples),
        "json_parsed": sum(1 for r in rows if r["json_parsed"]),
        "accepted_structural": len(accepted),
        "verified": sum(1 for r in rows if r.get("accepted") and
                        (r.get("verification") or {}).get("verified")),
        "needs_verification": sum(1 for r in rows if r.get("accepted") and
                                  not (r.get("verification") or {}).get("verified")),
        "verification_cases": [
            {"sample_id": r["sample_id"],
             "status": (r.get("verification") or {}).get("verification_status"),
             "reasons": (r.get("verification") or {}).get("reasons", [])}
            for r in rows if (r.get("verification") or {}).get("reasons")
        ],
        "rejected": len(rows) - len(accepted),
        "analyses_ok": sum(1 for r in rows if r.get("analysis_ok")),
        "analysis_pairing_ok": pair_ok,
        "score_dimensions": DIMENSIONS,
        "score_integrity": {
            "weighted_math_errors": weighted_errors,
            "score_range_errors": range_errors,
            "all_events_have_8_dims": all(set(e["scores"]) == set(DIMENSIONS) for e in accepted),
        },
        "priority_counts": {p: sum(1 for e in accepted if e["priority"] == p)
                            for p in ("P0", "P1", "P2", "ARCHIVE")},
        "counters": {
            "task_calls": st["task_calls"],
            "http_requests": st["http_requests"],
            "provider_attempts": st["provider_attempts"],
            "retries": st["retries"],
            "error_events": st["error_events"],
            "fallback_calls": st["fallback_calls"],
            "tokens": st["tokens"],
            "usage_by_provider": st["usage_by_provider"],
            "latency_ms": st["latency_ms"],
        },
        "rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        "audit_events": [{
            "event_id": e["event_id"], "event_title": e["event_title"],
            "fact_preview": e["fact"][:120], "evidence_preview": e["evidence_text"][:80],
            "source_url": e["source_url"], "published_at": e["published_at"],
            "scores": e["scores"], "weighted_score": e["weighted_score"],
            "priority": e["priority"],
            "analysis_pairing_ok": next(r.get("analysis_pairing_ok") for r in rows
                                       if r.get("_event") is e),
            "pairing_evidence": next(r.get("pairing_evidence") for r in rows
                                     if r.get("_event") is e),
            "action_level": next(r.get("action_level") for r in rows
                                 if r.get("_event") is e),
            "watsons_impact": next(r.get("watsons_impact") for r in rows
                                   if r.get("_event") is e),
            "verification": next(r.get("verification") for r in rows
                                 if r.get("_event") is e),
        } for e in accepted],
        "honesty_note": ("Real model calls; credentials came from process env only. "
                         "`accepted_structural` means JSON/evidence shape passed; "
                         "`verified` excludes date conflicts and hearsay-only claims."),
    }
    out = os.path.join(ROOT, "tests/r1/artifacts/20_sample_real_summary.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in (
        "samples_total", "json_parsed", "accepted", "rejected", "analyses_ok",
        "analysis_pairing_ok", "priority_counts", "counters")}, ensure_ascii=False, indent=2))
    print("WROTE", out)


if __name__ == "__main__":
    main()
