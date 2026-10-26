#!/usr/bin/env python3
"""F4 deterministic checks for the two negative R1 fixtures.

No model calls, no credentials. Demonstrates that R1S07 (hearsay-only growth
claim) and R1S13 (article's own 9月13日 marker vs 9月26日 fixture date) are
returned as `needs_verification`, not silently accepted as verified facts.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tests.r1.run_20_samples import assess_verification  # noqa: E402


def main():
    fixture = json.load(open(os.path.join(ROOT, "tests/fixtures/r1_samples_20.json"), encoding="utf-8"))
    by_id = {s["sample_id"]: s for s in fixture["samples"]}
    cases = [
        {
            "sample_id": "R1S07",
            "event": {
                "event_title": "得物男士护肤品类环比增长150%-200%",
                "fact": "得物平台男士护肤品类出现环比150%-200%的增长，吸引品牌进入该品类寻找增量。",
                "evidence_text": "听说得物男士护肤非常猛，“环比增长150%-200%”。",
                "event_occurred_at": "2026-09-03",
            },
        },
        {
            "sample_id": "R1S13",
            "event": {
                "event_title": "永辉超市福州奥体中心店健康场景改造带动多品类增长",
                "fact": "永辉超市福州奥体中心店十大健康场景落地一个月，多品类销售额环比提升。",
                "evidence_text": "以福州奥体中心店为例，十大健康场景落地仅一个月，销售额环比提升。",
                # This is exactly the value run_20_real.py used to overwrite:
                # the fixture collection date, while the article says 9月13日.
                "event_occurred_at": "2026-09-26",
            },
        },
    ]
    rows = []
    for case in cases:
        sample = by_id[case["sample_id"]]
        verification = assess_verification(case["event"], sample)
        rows.append({
            "sample_id": case["sample_id"],
            "event_title": case["event"]["event_title"],
            "verification": verification,
            "pass": verification["verification_status"] == "needs_verification",
        })
    out = {
        "mode": "deterministic_fixture_check",
        "description": "F4 negative cases; no real model call, 0 tokens, 0 HTTP.",
        "cases": rows,
        "all_pass": all(r["pass"] for r in rows),
    }
    path = os.path.join(ROOT, "tests/r1/artifacts/fact_verification_negative_cases.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
