#!/usr/bin/env python3
"""Explainable SelectBench for the Watsons tri-state prefilter.

Each case carries a human label plus an evidence URL.  The report separates
real article URLs from synthetic regression cases/home pages; 24 hand-written
cases are not evidence of production precision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


def load_cases(path: str) -> List[Dict[str, Any]]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _url_strength(url: str) -> str:
    u = str(url or "").strip()
    if not u.startswith("http"):
        return "missing"
    path = u.split("://", 1)[-1].split("/", 1)
    tail = path[1] if len(path) > 1 else ""
    if any(x in u for x in ("/detail/", "/information/", "/article", "/news/",
                            ".html", "/p/", "/articles/", "/content/")):
        return "article_url"
    if tail in ("", "/"):
        return "homepage"
    return "listing_or_unknown"


def _normalise(c: Dict[str, Any]) -> Dict[str, Any]:
    url = str(c.get("evidence_url") or c.get("source_url") or "").strip()
    strength = _url_strength(url)
    etype = str(c.get("evidence_type") or "").strip()
    if not etype:
        etype = "fixture_synthetic" if strength != "article_url" else "real_source"
    return {
        "id": c.get("id"),
        "stratum": str(c.get("stratum") or "other"),
        "expected": str(c.get("expected") or "").upper(),
        "source_url": c.get("source_url") or "",
        "evidence_url": url,
        "evidence_type": etype,
        "evidence_strength": strength,
        "evidence_verified": bool(c.get("evidence_verified", False)),
        "evidence_note": str(c.get("evidence_note") or ""),
        "text": c,
    }


def _metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    expected_pass = [r for r in rows if r["expected"] == "PASS"]
    pass_pred = [r for r in rows if r["predicted"] == "PASS"]
    block_pred = [r for r in rows if r["predicted"] == "BLOCK"]
    return {
        "n": len(rows),
        "precision_pass": round(
            sum(r["correct"] for r in pass_pred) / len(pass_pred), 4
        ) if pass_pred else 0.0,
        "recall_pass": round(
            sum(r["correct"] for r in expected_pass) / len(expected_pass), 4
        ) if expected_pass else 0.0,
        "precision_block": round(
            sum(r["correct"] for r in block_pred) / len(block_pred), 4
        ) if block_pred else 0.0,
        "correct": sum(r["correct"] for r in rows),
        "false_block": sum(r["false_block"] for r in rows),
        "false_pass": sum(r["false_pass"] for r in rows),
    }


def evaluate(cases: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    from skills.m2.tri_state_prefilter import BLOCK, PASS, UNKNOWN, classify_candidate

    rows: List[Dict[str, Any]] = []
    for raw in cases:
        c = _normalise(raw)
        got = classify_candidate(c["text"])
        expected = c["expected"]
        complete = bool(c["evidence_url"]) and (
            c["evidence_type"] == "real_source" and c["evidence_strength"] == "article_url"
            or c["evidence_type"] == "fixture_synthetic" and c["evidence_strength"] in ("article_url", "homepage")
        )
        rows.append({
            "id": c["id"],
            "stratum": c["stratum"],
            "expected": expected,
            "predicted": got["label"],
            "reason": got["reason"],
            "source_url": c["source_url"],
            "evidence_url": c["evidence_url"],
            "evidence_type": c["evidence_type"],
            "evidence_strength": c["evidence_strength"],
            "evidence_verified": c["evidence_verified"],
            "evidence_complete": complete,
            "correct": got["label"] == expected,
            "false_block": expected in (PASS, UNKNOWN) and got["label"] == BLOCK,
            "false_pass": expected == BLOCK and got["label"] == PASS,
        })

    real = [r for r in rows if r["evidence_type"] == "real_source" and
            r["evidence_strength"] == "article_url"]
    synthetic = [r for r in rows if r not in real]
    overall = _metrics(rows)
    real_m = _metrics(real)
    synthetic_m = _metrics(synthetic)
    traceability_ok = all(bool(r["evidence_url"]) for r in rows)
    verified_real = [r for r in real if r["evidence_verified"]]
    safe = bool(
        len(verified_real) >= 40 and traceability_ok and
        real_m["precision_pass"] >= 0.90 and real_m["recall_pass"] >= 0.80 and
        real_m["precision_block"] >= 0.95 and real_m["false_block"] == 0)
    return {
        **overall,
        "traceability_ok": traceability_ok,
        "real_evidence_n": len(real),
        "verified_real_evidence_n": len(verified_real),
        "synthetic_n": len(synthetic),
        "real_evidence": real_m,
        "synthetic": synthetic_m,
        "by_stratum": _by_stratum(rows),
        "rows": rows,
        "safe_to_enable": safe,
        "note": "Advisory only. Real article URLs and synthetic/homepage cases are "
                "reported separately; 24 hand-built cases never establish production "
                "precision, and evidence_verified is false unless article text was "
                "re-read and checked manually.",
    }


def _by_stratum(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for r in rows:
        s = str(r.get("stratum") or "other")
        d = out.setdefault(s, {"n": 0, "correct": 0, "false_block": 0})
        d["n"] += 1
        d["correct"] += int(bool(r["correct"]))
        d["false_block"] += int(bool(r["false_block"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="tests/m2/fixtures/selectbench.jsonl")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    result = evaluate(load_cases(args.cases))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
