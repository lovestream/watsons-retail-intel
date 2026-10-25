#!/usr/bin/env python3
"""R1-3: 20 frozen samples through the real prompt/schema/scoring code.

Two modes:
  sim  (default): deterministic fake transport. Validates JSON contract,
        evidence gates, 8-dimension scoring, analysis pairing, counters.
  real: uses PipelineLLMClient with environment credentials. If the existing
        credentials are encrypted/unavailable, exits at gate 1 with an exact
        blocker and makes no HTTP request.

Output: tests/r1/artifacts/20_sample_<mode>_summary.json (no secrets, no full
article bodies).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import logging  # noqa: E402
import yaml  # noqa: E402

from skills.utils.llm_pipeline_adapter import PipelineLLMClient  # noqa: E402
from skills.extract_events.extract_events import (  # noqa: E402
    _build_extraction_prompt, llm_extract_events,
)
from skills.analyze_business_impact.analyze_business_impact import (  # noqa: E402
    _build_analysis_prompt, llm_analyze_event, apply_analysis_downgrade,
)
from skills.score_events.score_events import (  # noqa: E402
    score_strategic_importance, score_watsons_relevance, score_impact_scope,
    score_source_credibility, score_data_richness, score_actionability,
    score_time_sensitivity, score_novelty, compute_weighted_score,
    classify_priority, apply_hard_downgrade,
)

FROZEN_AT = datetime(2026, 10, 25, tzinfo=timezone.utc)
DIMENSIONS = ["strategic_importance", "watsons_relevance", "impact_scope",
              "source_credibility", "data_richness", "actionability",
              "time_sensitivity", "novelty"]


class SimulatedTransport:
    """Deterministic model stand-in; records payloads and token usage."""

    def __init__(self, samples_by_id):
        self.samples = samples_by_id
        self.calls = []

    def __call__(self, url, headers=None, timeout=None, **kwargs):
        request_body = kwargs.get("json") or {}
        payload = request_body
        user = ""
        for m in payload.get("messages", []):
            if m.get("role") == "user":
                user = m.get("content", "")
        self.calls.append({"url": url, "model": payload.get("model"), "user": user})

        if "请分析以下事件" in user or "business_analysis" in user:
            # Analysis prompt contains the event title/fact; echo a distinctive
            # slice so pairing can be asserted later.
            title = _extract_field(user, "事件标题") or _extract_field(user, "event_title") or ""
            body = {
                "business_analysis": {
                    "impact_type": "opportunity",
                    "affected_channels": ["线上", "O2O"],
                    "affected_business_variables": ["品类机会", "流量"],
                    "watsons_impact": "针对「%s」：该动作会影响屈臣氏对应品类的线上曝光与转化节奏，需要按周跟踪。" % title[:24],
                    "recommended_action": "在对应渠道做小范围货盘/资源位测试，7天内复盘转化。",
                    "action_level": "test",
                    "owner_hint": "即时零售运营",
                    "tracking_metrics": ["GMV", "转化率", "客单价"],
                    "follow_up_questions": ["该动作是否覆盖屈臣氏核心品类？"],
                    "confidence": "medium",
                }
            }
            content = json.dumps(body, ensure_ascii=False)
        else:
            sid = _extract_field(user, "文章ID") or ""
            s = self.samples.get(sid)
            if not s:
                content = json.dumps({"article_reject": True, "events": []}, ensure_ascii=False)
            else:
                is_retail = s["topic"] == "即时零售"
                evidence = "" if sid == "R1S07" else s["content_excerpt"][:180]
                occurred = "2027-01-01" if sid == "R1S13" else s["published_at"][:10]
                body = {
                    "article_id": sid,
                    "events": [{
                        "event_title": s["title"][:60],
                        "event_type": "platform_move" if is_retail else "category_trend",
                        "fact": s["summary"][:200],
                        "evidence_text": evidence,
                        "entities": {
                            "platforms": ["美团"] if "美团" in s["summary"] else [],
                            "companies": [], "competitors": [], "brands": [],
                            "channels": ["即时零售"] if is_retail else ["线上"],
                            "categories": ["个护美妆"] if not is_retail else ["零售"],
                        },
                        "business_variables": ["品类机会", "流量"],
                        "time_sensitivity": "recent",
                        "novelty": "new",
                        "confidence": "medium",
                        "event_occurred_at": occurred,
                        "event_announced_at": occurred,
                        "event_effective_at": "",
                        "event_time_precision": "day",
                        "event_time_confidence": "medium",
                        "temporal_evidence": s["published_at"][:10],
                        "reject": False,
                        "reject_reason": "",
                    }],
                    "article_reject": False,
                    "article_reject_reason": "",
                }
                content = json.dumps(body, ensure_ascii=False)
        return FakeResponse(200, {
            "model": "deepseek-ai/DeepSeek-V4.1-Flash",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 260, "total_tokens": 1060},
        })


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.headers = {}
        self.text = json.dumps(body, ensure_ascii=False)

    def json(self):
        return self._body


def _extract_field(text, label):
    m = re.search(r"(?:%s)[：:]\s*(.+)" % re.escape(label), text)
    return m.group(1).strip() if m else ""


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def evidence_errors(event, sample):
    errors = []
    if not (event.get("fact") or "").strip():
        errors.append("fact_empty")
    if not (event.get("evidence_text") or "").strip():
        errors.append("evidence_text_empty")
    url = sample.get("source_url") or ""
    if not url.startswith("http"):
        errors.append("source_url_missing")
    occurred = parse_date(event.get("event_occurred_at")) or parse_date(sample.get("published_at"))
    pub = parse_date(sample.get("published_at"))
    if occurred is None:
        errors.append("event_date_missing")
    elif occurred > FROZEN_AT.date():
        errors.append("future_event_date")
    elif pub and occurred < pub.replace(year=pub.year - 2):
        errors.append("event_date_too_old")
    return errors


def score_one(event, all_events, index, cfg):
    si = score_strategic_importance(event, cfg["event_type_importance"])[0]
    wr = score_watsons_relevance(event)[0]
    sc = score_impact_scope(event)[0]
    src = score_source_credibility(event, cfg["source_tier_map"])[0]
    dr = score_data_richness(event, cfg["data_keywords"])[0]
    ac = score_actionability(event, cfg["actionable_variables"])[0]
    ts = score_time_sensitivity(event, FROZEN_AT.replace(tzinfo=None))[0]
    no = score_novelty(event, all_events, index)[0]
    scores = dict(zip(DIMENSIONS, [si, wr, sc, src, dr, ac, ts, no]))
    weighted = compute_weighted_score(scores, cfg["weights"])
    priority = classify_priority(weighted, cfg["thresholds"])
    priority, reasons = apply_hard_downgrade(event, scores, priority, [], weighted)
    return scores, round(weighted, 4), priority, reasons


def run_sim():
    fixture = json.load(open(os.path.join(ROOT, "tests/fixtures/r1_samples_20.json"), encoding="utf-8"))
    samples = fixture["samples"]
    by_id = {s["sample_id"]: s for s in samples}
    transport = SimulatedTransport(by_id)
    client = PipelineLLMClient(
        config_path=os.path.join(ROOT, "config/llm_providers.yaml"),
        http_post=transport, sleep=lambda s: None,
        credential_overrides={"siliconflow": "sim-only", "rightcode-bybt": "sim-only"},
    )
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config/scoring.yaml"), encoding="utf-8"))

    rows = []
    accepted = []
    for s in samples:
        article = {
            "article_id": s["sample_id"], "title": s["title"],
            "source_name": s["source_name"], "published_at": s["published_at"],
            "summary": s["summary"], "content": s["content_excerpt"],
        }
        extracted = llm_extract_events(article, client, logging.getLogger("r1"),
                                       model="pipeline-deepseek-flash")
        row = {"sample_id": s["sample_id"], "topic": s["topic"],
               "json_parsed": bool(extracted.get("llm_extracted")),
               "extract_failure_type": extracted.get("failure_type")}
        if not row["json_parsed"]:
            row["accepted"] = False
            row["rejection_reasons"] = ["json_parse_failed:%s" % extracted.get("failure_type")]
            rows.append(row)
            continue
        events = extracted.get("events") or []
        if not events:
            row["accepted"] = False
            row["rejection_reasons"] = ["no_events"]
            rows.append(row)
            continue
        ev = dict(events[0])
        ev.update({
            "event_id": "E20261025_%04d" % (len(accepted) + 1),
            "source_url": s["source_url"], "source_name": s["source_name"],
            "published_at": s["published_at"], "extraction_method": "llm",
        })
        errs = evidence_errors(ev, s)
        row["evidence_errors"] = errs
        if errs:
            row["accepted"] = False
            row["rejection_reasons"] = errs
            rows.append(row)
            continue
        row["accepted"] = True
        accepted.append(ev)
        row["_event"] = ev
        rows.append(row)

    # Score accepted events (novelty needs the accepted pool).
    for idx, ev in enumerate(accepted):
        scores, weighted, priority, reasons = score_one(ev, accepted, idx, cfg)
        ev["scores"] = scores
        ev["weighted_score"] = weighted
        ev["priority"] = priority
        ev["downgrade_reasons"] = reasons

    # Analyse accepted events through the real prompt/validator/downgrade code.
    pair_ok = 0
    for row in rows:
        if not row.get("accepted"):
            continue
        ev = row["_event"]
        ba = llm_analyze_event(ev, client, model="pipeline-deepseek-flash")
        ok = isinstance(ba, dict)
        if ok:
            ba = apply_analysis_downgrade(ba, ev)
            token = ev["event_title"][:12]
            matched = token and token in (ba.get("watsons_impact") or "")
            pair_ok += 1 if matched else 0
            row["analysis_ok"] = True
            row["analysis_pairing_ok"] = bool(matched)
            row["action_level"] = ba.get("action_level")
            ev["business_analysis"] = ba
        else:
            row["analysis_ok"] = False
            row["analysis_pairing_ok"] = False
            row["action_level"] = ""

    # Persist accepted-event shapes for audit (no article bodies beyond 120 chars).
    audit_events = []
    for ev in accepted:
        audit_events.append({
            "event_id": ev["event_id"], "event_title": ev["event_title"],
            "fact_preview": ev["fact"][:120], "evidence_preview": ev["evidence_text"][:80],
            "source_url": ev["source_url"], "published_at": ev["published_at"],
            "scores": ev["scores"], "weighted_score": ev["weighted_score"],
            "priority": ev["priority"],
            "analysis_pairing_ok": next(r.get("analysis_pairing_ok") for r in rows
                                       if r.get("_event") is ev),
        })

    # Integrity self-check: weighted score must equal configured weights × dims.
    weighted_errors = []
    range_errors = []
    for ev in accepted:
        expected = sum(cfg["weights"][d] * ev["scores"][d] for d in DIMENSIONS)
        if abs(expected - ev["weighted_score"]) > 0.01:
            weighted_errors.append({"event_id": ev["event_id"],
                                    "expected": round(expected, 4),
                                    "actual": ev["weighted_score"]})
        for d in DIMENSIONS:
            if not (0 <= ev["scores"][d] <= 5):
                range_errors.append({"event_id": ev["event_id"], "dim": d,
                                     "value": ev["scores"][d]})

    st = client.get_status()["stats"]
    summary = {
        "mode": "simulation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples_total": len(samples),
        "json_parsed": sum(1 for r in rows if r["json_parsed"]),
        "accepted": len(accepted),
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
        },
        "rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        "audit_events": audit_events,
        "honesty_note": ("SIMULATION ONLY: transport is deterministic, not the real "
                         "SiliconFlow model. Real-model gate is blocked by encrypted "
                         "provider credentials (see NAS-R1 report)."),
    }
    out = os.path.join(ROOT, "tests/r1/artifacts/20_sample_simulation_summary.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({k: summary[k] for k in (
        "samples_total", "json_parsed", "accepted", "rejected", "analyses_ok",
        "analysis_pairing_ok", "priority_counts", "counters")}, ensure_ascii=False, indent=2))
    print("WROTE", out)


def run_real():
    client = PipelineLLMClient(config_path=os.path.join(ROOT, "config/llm_providers.yaml"))
    status = client.get_status()
    if not status["available"]:
        blockers = {k: v.get("blocker") for k, v in status["credential_report"].items()}
        payload = {
            "mode": "real", "gate": 1, "result": "BLOCKED",
            "reason": "Python has no scoped way to read existing provider credentials",
            "credential_blockers": blockers,
            "http_requests": 0,
            "next": "Inject PIPELINE_LLM_API_KEY / PIPELINE_LLM_FALLBACK_KEY into the "
                    "pipeline process env (or approve a scoped secret file), then rerun.",
        }
        out = os.path.join(ROOT, "tests/r1/artifacts/20_sample_real_summary.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(payload, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.exit(2)
    print("Credentials available; real harness would continue. Not run in this round.")
    sys.exit(0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "real"], default="sim")
    args = ap.parse_args()
    run_sim() if args.mode == "sim" else run_real()
