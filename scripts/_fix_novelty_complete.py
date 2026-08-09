#!/usr/bin/env python3
"""修复 08-09 events_scored_novelty.json: 合并 analyzed 的完整字段。

背景: 新链路下 novelty 在 score/analyze 之前，novelty 输出不含
priority/weighted_score/business_analysis。08-09 的 novelty 文件是
测试时用 temporal 输入重跑覆盖的（缺这些字段），而 events_analyzed.json
是旧链路产物（含 ba/weighted_score/priority 但缺 novelty_status/temporal）。

目标: 生成一个完整版 novelty 文件:
  - novelty/temporal 字段: 来自当前 events_scored_novelty.json (新链路判定)
  - priority/weighted_score/scores/business_analysis: 来自 events_analyzed.json
按 event_id 合并。不调 LLM、不写账本。
"""
import json
import sys

DATE = "2026-08-09"
ROOT = "/app/working/projects/watsons-retail-intel"

novelty_path = f"{ROOT}/data/events/{DATE}/events_scored_novelty.json"
analyzed_path = f"{ROOT}/data/events/{DATE}/events_analyzed.json"
out_path = novelty_path + ".complete"
if len(sys.argv) > 1:
    out_path = sys.argv[1]

with open(novelty_path, "r", encoding="utf-8") as f:
    novelty_data = json.load(f)
with open(analyzed_path, "r", encoding="utf-8") as f:
    analyzed_data = json.load(f)

novelty_events = novelty_data.get("events", [])
analyzed_events = analyzed_data.get("events", [])

# 索引 analyzed 按 event_id
analyzed_map = {e.get("event_id"): e for e in analyzed_events if e.get("event_id")}

merged_count = 0
missing_ba = 0
for ev in novelty_events:
    eid = ev.get("event_id")
    src = analyzed_map.get(eid)
    if src:
        # 从 analyzed 补齐评分/分析字段
        for field in ("priority", "weighted_score", "scores", "score_reasons",
                      "downgrade_reasons", "business_analysis"):
            if field in src:
                ev[field] = src[field]
        # 补齐缺失的常规字段
        for field in ("source_url", "source_name", "published_at",
                      "time_sensitivity", "confidence"):
            if field in src and not ev.get(field):
                ev[field] = src[field]
        merged_count += 1
        if not ev.get("business_analysis"):
            missing_ba += 1
    else:
        print(f"WARN 无 analyzed 对应: {eid} {ev.get('event_title','')[:40]}")

# 校验
n_pr = sum(1 for e in novelty_events if e.get("priority"))
n_ws = sum(1 for e in novelty_events if "weighted_score" in e)
n_ba = sum(1 for e in novelty_events if e.get("business_analysis"))
n_nv = sum(1 for e in novelty_events if e.get("novelty_status"))
n_tf = sum(1 for e in novelty_events if e.get("fact_freshness_status"))

print(f"novelty_events={len(novelty_events)} merged={merged_count} "
      f"priority={n_pr} weighted={n_ws} ba={n_ba} novelty={n_nv} freshness={n_tf} "
      f"missing_ba={missing_ba}")

with open(out_path, "w", encoding="utf-8") as f:
    json.dump(novelty_data, f, ensure_ascii=False, indent=2)
print(f"written: {out_path}")
