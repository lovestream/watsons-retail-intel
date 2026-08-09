#!/usr/bin/env python3
"""重建 event_ledger/events_seen.jsonl。

背景: 2026-08-09 排查时 git checkout 把 07-06~08-08 的账本运行期记录
(从未提交) 回滚掉了。这些记录可以从每天的 events_scored_novelty.json
(含 cluster_key/fact_hash/first_seen_at/last_seen_at/last_reported_at/
report_count/priority/source_url/date) 重建。

策略:
1. 加载现有已提交账本 (07-05 止) 为 dict {cluster_key: entry}
2. 按日期顺序 (07-06 ~ 08-09) 逐天 upsert 合并 daily novelty 输出
   - first_seen_at 取最小
   - last_seen_at 取最大
   - last_reported_at 取最近非空
   - report_count 取最大
   - fact_hash / event 元信息取最新
3. 整体写回 JSONL (保持 cluster_key 有序)
"""
import json
import os
import sys
from datetime import datetime

PROJECT_ROOT = "/app/working/projects/watsons-retail-intel"
LEDGER = os.path.join(PROJECT_ROOT, "data/event_ledger/events_seen.jsonl")

# 日期序列: 已提交账本截止 07-05, 之后到 08-09 (含)
DATES = [
    "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10",
    "2026-07-11", "2026-07-12", "2026-07-13", "2026-07-14", "2026-07-15",
    "2026-07-16", "2026-07-17", "2026-07-18", "2026-07-19", "2026-07-20",
    "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24", "2026-07-25",
    "2026-07-26", "2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30",
    "2026-07-31", "2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04",
    "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-08", "2026-08-09",
]


def load_ledger(path):
    ledger = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    key = entry.get("cluster_key", "")
                    if key:
                        ledger[key] = entry
                except json.JSONDecodeError:
                    pass
    return ledger


def merge_entry(ledger, entry):
    """按日期顺序合并: 保留最早 first_seen, 最新 last_seen/last_reported。"""
    key = entry.get("cluster_key", "")
    if not key:
        return
    existing = ledger.get(key)
    if existing is None:
        ledger[key] = dict(entry)
        return

    def _date_key(v):
        return v or "0000-00-00"

    for field in ("first_seen_at",):
        if _date_key(entry.get(field)) < _date_key(existing.get(field)):
            existing[field] = entry.get(field)
    for field in ("last_seen_at", "last_reported_at"):
        if _date_key(entry.get(field)) > _date_key(existing.get(field)):
            existing[field] = entry.get(field)
    # fact_hash 用最新的
    if entry.get("fact_hash"):
        existing["fact_hash"] = entry["fact_hash"]
    # report_count 取最大
    rc_new = entry.get("report_count", 0) or 0
    rc_old = existing.get("report_count", 0) or 0
    existing["report_count"] = max(rc_old, rc_new)
    # 其余字段更新为最新
    for field in ("event_id", "event_title", "event_type", "priority",
                  "source_url", "date", "fact_freshness_status"):
        if entry.get(field):
            existing[field] = entry[field]


def main():
    ledger = load_ledger(LEDGER)
    before = len(ledger)
    added = 0
    missing_days = []

    for date in DATES:
        fpath = os.path.join(PROJECT_ROOT, "data/events", date,
                             "events_scored_novelty.json")
        if not os.path.exists(fpath):
            missing_days.append(date)
            continue
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        events = data.get("events", []) if isinstance(data, dict) else data
        for ev in events:
            ck = ev.get("cluster_key")
            if not ck:
                continue
            entry = {
                "cluster_key": ck,
                "event_id": ev.get("event_id", ""),
                "event_title": (ev.get("event_title") or "")[:100],
                "event_type": ev.get("event_type", ""),
                "fact_hash": ev.get("fact_hash", ""),
                "first_seen_at": ev.get("first_seen_at", date),
                "last_seen_at": ev.get("last_seen_at", date),
                "last_reported_at": ev.get("last_reported_at"),
                "report_count": ev.get("report_count", 0),
                "fact_freshness_status": ev.get("fact_freshness_status", ""),
                "date": date,
                "priority": ev.get("priority", ""),
                "source_url": ev.get("source_url", ""),
            }
            merge_entry(ledger, entry)
            added += 1

    # 写回 (保持 dict 顺序, 每行一条)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "w", encoding="utf-8") as f:
        for key, entry in ledger.items():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"重建完成: before={before} entries, after={len(ledger)} entries, "
          f"merged={added} event-records")
    if missing_days:
        print(f"缺失日期(无 scored_novelty): {missing_days}")


if __name__ == "__main__":
    main()
