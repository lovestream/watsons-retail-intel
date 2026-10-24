"""生成合并历史基线衍生索引（只读、可重建，非权威账本）。

用法: python3 tools/build_merged_baseline.py <wtd_jsonl> <out_json> [ledger_md]
  ledger_md 默认为 events/LEDGER-2026-10.md（相对于仓库根目录的同级路径推断：
  本脚本位于 muse/tools/，默认 ledger 为 ../events/LEDGER-2026-10.md）。
来源：
 1. WTD JSONL（权威机器账本，优先；冲突时以 WTD 为准并报告冲突数）。
 2. LEDGER-2026-10.md 中 WTD 之外的历史事件（人工从 LEDGER 小节事实中提炼的
    subject/action/object/event_date，仅用于去重匹配，不得作为入池事实依据；
    事实原文与证据仍以 LEDGER 小节为准，索引中只存匹配键与来源文件）。
FAIL-CLOSED 一致性检查（S3-A4，PR #1 评论 6385204555 问题 B）：
  每次重建必须读取 LEDGER Markdown 的真实 EV 小节 ID（形如 ### EV-YYYYMMDD-NN），
  核对：(a) 每个硬编码 LEDGER_ONLY ID 必须真实出现在 LEDGER 小节中；
  (b) 每个 LEDGER 小节 EV ID 必须出现在合并集合（WTD 或 LEDGER_ONLY）中；
  (c) LEDGER_ONLY 的 event_date 必须与对应 LEDGER 小节中首次出现的 event_date 一致
      （小节行以 event_date：开头，日期为 YYYY-MM-DD；若小节无该行，记为未核对并告警）。
  任一检查失败，脚本以非零退出且不写出输出文件，不得无声产出滞后索引。
  本映射仍是一次性人工映射，不是自动全量重建：LEDGER 新增事件必须由人工在
  LEDGER_ONLY 中补映射并通过本检查后，索引才更新；检查失败时人工必须更新映射，
  不得绕过检查直接编辑索引。
输出：JSON，含 generated_from、records[]；records 按 event_id 排序。
本文件不是第二份权威账本：如索引与原账本冲突，以原账本为准；索引可随时
由本脚本从原账本重建，人工不得直接编辑索引作为入池依据。
"""
import json, sys, re, os

# LEDGER-only 事件（WTD JSONL 中没有的 5 条有效事件；提炼自 LEDGER 小节事实）
LEDGER_ONLY = [
    {"event_id": "EV-20261020-01", "subject": "天猫", "action": "开卖", "object": "双11开卖4小时", "event_date": "2026-10-20", "published_date": "2026-10-22", "announcement_date": None, "status": "updated", "priority": "OBSERVATION", "source_file": "events/LEDGER-2026-10.md", "note": "纯 B2C 背景，124 个品牌为全平台总数，无即时零售承接证据"},
    {"event_id": "EV-20261021-01", "subject": "WOW BEAUTY", "action": "开业", "object": "花知晓十周年全球旗舰店", "event_date": "2026-10-21", "published_date": "2026-10-21", "announcement_date": None, "status": "archived", "priority": "ARCHIVE", "source_file": "events/LEDGER-2026-10.md", "note": "线下单店动作，ARCHIVE"},
    {"event_id": "EV-20261021-02", "subject": "妍丽化妆品（中国）有限公司", "action": "被处罚", "object": "广告违法罚款3000元", "event_date": "2026-10-21", "published_date": None, "announcement_date": None, "status": "archived", "priority": "ARCHIVE", "source_file": "events/LEDGER-2026-10.md", "note": "文号深市监福处罚〔2026〕福田120号，市监局官网公示直达页仍缺"},
    {"event_id": "EV-20261022-02", "subject": "国家医保局", "action": "发布", "object": "骑手参保联合倡议书", "event_date": "2026-10-22", "published_date": None, "announcement_date": None, "status": "archived", "priority": "ARCHIVE", "source_file": "events/LEDGER-2026-10.md", "note": "倡议性质，无强制条款，与深圳安全激励为不同事件"},
    {"event_id": "EV-20261023-01", "subject": "抖音商城", "action": "发布", "object": "双11开门红热卖品牌榜", "event_date": "2026-10-23", "published_date": None, "announcement_date": None, "status": "archived", "priority": "ARCHIVE", "source_file": "events/LEDGER-2026-10.md", "note": "平台营销榜单，无绝对成交额"},
]

def parse_ledger_ids(ledger_path):
    """从 LEDGER Markdown 中提取 EV 小节 ID 与 event_date（首次出现）。"""
    text = open(ledger_path, encoding="utf-8").read()
    ids = {}
    # 小节标题形如 ### EV-20261022-01 · ... ；取标题后的 event_date 行
    sections = re.split(r"(?m)^###\s+(EV-\d{8}-\d+)\b", text)
    # sections[0] 为标题前内容，之后交替为 id、body
    for i in range(1, len(sections), 2):
        eid = sections[i]
        body = sections[i + 1] if i + 1 < len(sections) else ""
        m = re.search(r"event_date[：:]\s*(\d{4}-\d{2}-\d{2})", body)
        ids[eid] = m.group(1) if m else None
    return ids

def main():
    src, out = sys.argv[1], sys.argv[2]
    ledger_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(__file__), "..", "events", "LEDGER-2026-10.md")
    rows = [json.loads(x) for x in open(src, encoding="utf-8") if x.strip()]
    by_id = {}
    conflicts = []
    for r in rows:
        by_id[r["event_id"]] = {"event_id": r["event_id"], "subject": r["subject"], "action": r["action"], "object": r["object"], "event_date": r["event_date"], "published_date": r.get("published_date"), "announcement_date": r.get("announcement_date"), "status": r["status"], "priority": r["priority"], "source_file": "events/WTD-2026-10-19-24.jsonl"}
    for rec in LEDGER_ONLY:
        if rec["event_id"] in by_id:
            conflicts.append(rec["event_id"])
        else:
            by_id[rec["event_id"]] = rec
    # FAIL-CLOSED 检查（在写出之前执行）
    ledger_ids = parse_ledger_ids(ledger_path)
    errors = []
    hardcoded_ids = {r["event_id"] for r in LEDGER_ONLY}
    for eid in sorted(hardcoded_ids):
        if eid not in ledger_ids:
            errors.append(f"hardcoded LEDGER_ONLY id {eid} not found in LEDGER sections")
        else:
            led_date = ledger_ids[eid]
            rec_date = next(r["event_date"] for r in LEDGER_ONLY if r["event_id"] == eid)
            if led_date is not None and led_date != rec_date:
                errors.append(f"LEDGER_ONLY date mismatch {eid}: hardcoded {rec_date} vs LEDGER section {led_date}")
    for eid in sorted(ledger_ids):
        if eid not in by_id:
            errors.append(f"LEDGER section id {eid} missing from merged set (WTD + LEDGER_ONLY); add mapping or fail")
    if errors:
        print("FAIL-CLOSED: merged baseline NOT written, errors:")
        for e in errors:
            print(" -", e)
        sys.exit(2)
    print(f"FAIL-CLOSED CHECK PASSED: ledger_section_ids={sorted(ledger_ids)} hardcoded_ledger_only_ids={sorted(hardcoded_ids)}")
    records = [by_id[k] for k in sorted(by_id)]
    valid = [r for r in records if r["status"] != "superseded"]
    superseded = [r for r in records if r["status"] == "superseded"]
    data = {"generated_from": ["events/WTD-2026-10-19-24.jsonl", "events/LEDGER-2026-10.md (LEDGER-only 5 records, manually extracted from LEDGER sections)"], "note": "Derived read-only index for dedup matching only; authoritative ledgers are WTD JSONL and LEDGER MD. On conflict, authoritative ledgers win. Rebuildable by tools/build_merged_baseline.py.", "records": records, "counts": {"distinct_ev": len(records), "valid": len(valid), "superseded": len(superseded), "conflicts_wtd_wins": conflicts}}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"merged distinct={len(records)} valid={len(valid)} superseded={len(superseded)} conflicts={conflicts}")
    print(f"valid_ids={[r['event_id'] for r in valid]}")
    print(f"superseded_ids={[r['event_id'] for r in superseded]}")

if __name__ == "__main__":
    main()
