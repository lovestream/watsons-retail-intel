#!/usr/bin/env python3
"""Small explainable SelectBench for the Watsons tri-state prefilter.

Inspired by AIHOT `admin/selectbench.ts`: every case has a human label and an
evidence URL, so a precision/recall change can be traced to a concrete source
rather than a vibe.  This is intentionally a fixture harness, not a claim of
production accuracy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from skills.m2.tri_state_prefilter import BLOCK, PASS, UNKNOWN, classify_candidate


def load_cases(path: str) -> List[Dict[str, Any]]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def evaluate(cases: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for c in cases:
        got = classify_candidate(c)
        expected = str(c.get("expected") or "").upper()
        rows.append({
            "id": c.get("id"),
            "stratum": c.get("stratum"),
            "expected": expected,
            "predicted": got["label"],
            "reason": got["reason"],
            "evidence_url": c.get("evidence_url"),
            "correct": got["label"] == expected,
            "false_block": expected in (PASS, UNKNOWN) and got["label"] == BLOCK,
            "false_pass": expected == BLOCK and got["label"] == PASS,
        })
    expected_pass = [r for r in rows if r["expected"] == PASS]
    expected_block = [r for r in rows if r["expected"] == BLOCK]
    pass_pred = [r for r in rows if r["predicted"] == PASS]
    block_pred = [r for r in rows if r["predicted"] == BLOCK]
    unknown = [r for r in rows if r["predicted"] == UNKNOWN]
    precision = (sum(r["correct"] for r in pass_pred) / len(pass_pred)
                 if pass_pred else 0.0)
    recall = (sum(r["correct"] for r in expected_pass) / len(expected_pass)
              if expected_pass else 0.0)
    block_precision = (sum(r["correct"] for r in block_pred) / len(block_pred)
                       if block_pred else 0.0)
    return {
        "n": len(rows),
        "precision_pass": round(precision, 4),
        "recall_pass": round(recall, 4),
        "precision_block": round(block_precision, 4),
        "unknown_rate": round(len(unknown) / len(rows), 4) if rows else 0.0,
        "false_block": sum(r["false_block"] for r in rows),
        "false_pass": sum(r["false_pass"] for r in rows),
        "correct": sum(r["correct"] for r in rows),
        "by_stratum": _by_stratum(rows),
        "rows": rows,
        "safe_to_enable": bool(
            len(rows) >= 40 and precision >= 0.90 and recall >= 0.80 and
            block_precision >= 0.95 and
            sum(r["false_block"] for r in rows) == 0),
        "note": "Advisory fixture only; production remains on hard gates until "
                "a larger human-labeled set clears the same thresholds.",
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
