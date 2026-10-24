"""S3-A4 沙箱新增→重跑测试（纯沙箱，非正式账本，2026-10-24）。

用法: python3 tools/s3a4_sandbox_new_rerun.py <merged_baseline_index_json> <formal_wtd_jsonl>
输入候选（真实已核验列表项，非虚构）：
  subject 美团闪购，action 发布，object 官旗闪电仓售后处理规范（试运行），
  event_date 2026-10-22，published_date 2026-10-22（列表日期），
  announcement_date 无（不得用空值匹配），primary_url 为规则中心列表 URL
  （单篇全文未核，列表级证据，正式入池前须打开全文复核）。
预期：
  第 1 次运行：合并基线中无匹配 → 沙箱新增 SANDBOX-EV-20261022-01，新 = 1，
    沙箱不同 ID 9 → 10，有效 8 → 9。
  第 2 次运行（同输入，中断重试模拟）：匹配到沙箱记录 → SAME_OCCURRENCE，
    新 = 0，沙箱不同 ID 仍为 10，同一 ID，supporting_url 合并不重复。
  正式账本：全程只读，哈希不变，正式 ID 不变，SANDBOX- 在正式账本中 0 次。
局限：本测试证明沙箱路径的写入幂等，不证明真实入池路径的生产幂等或并发幂等。
"""
import hashlib, json, sys, copy

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def main():
    index_path, formal_path = sys.argv[1], sys.argv[2]
    formal_hash_before = sha(formal_path)
    idx = json.load(open(index_path, encoding="utf-8"))
    baseline = {r["event_id"]: r for r in idx["records"]}
    candidate = {"subject": "美团闪购", "action": "发布", "object": "官旗闪电仓售后处理规范（试运行）", "event_date": "2026-10-22", "published_date": "2026-10-22", "announcement_date": None, "url": "https://rules-center.meituan.com/rule-overview?commonType=25", "priority": "P2", "note": "title-only list evidence; full article NOT verified"}
    sandbox = copy.deepcopy(baseline)
    print(f"BEFORE sandbox_distinct={len(sandbox)} valid={sum(1 for r in sandbox.values() if r['status']!='superseded')}")
    print(f"BEFORE formal_sha256={formal_hash_before}")
    # 第 1 次
    matched = [r for r in sandbox.values() if r["subject"] == candidate["subject"] and r["action"] == candidate["action"] and r["object"] == candidate["object"] and r["event_date"] == candidate["event_date"]]
    assert not matched, f"unexpected match on first run: {matched}"
    new_id = "SANDBOX-EV-20261022-01"
    sandbox[new_id] = {"event_id": new_id, "subject": candidate["subject"], "action": candidate["action"], "object": candidate["object"], "event_date": candidate["event_date"], "published_date": candidate["published_date"], "announcement_date": None, "status": "sandbox_only", "priority": "P2", "source_file": "sandbox (not formal ledger)", "supporting_urls": [candidate["url"]]}
    print(f"RUN1 decision=NEW_SANDBOX new_id={new_id} new_count=1 sandbox_distinct={len(sandbox)} valid={sum(1 for r in sandbox.values() if r['status'] not in ('superseded','sandbox_only') or r['event_id']==new_id)}")
    # 第 2 次（同输入，中断重试）
    matched2 = [r for r in sandbox.values() if r["subject"] == candidate["subject"] and r["action"] == candidate["action"] and r["object"] == candidate["object"] and r["event_date"] == candidate["event_date"]]
    assert len(matched2) == 1 and matched2[0]["event_id"] == new_id, f"rerun match failed: {matched2}"
    if candidate["url"] not in matched2[0].get("supporting_urls", []):
        matched2[0].setdefault("supporting_urls", []).append(candidate["url"])
    print(f"RUN2 decision=SAME_OCCURRENCE matched_id={new_id} new_count=0 sandbox_distinct={len(sandbox)} supporting_urls={matched2[0]['supporting_urls']}")
    assert len(sandbox) == 10, f"sandbox distinct should be 10, got {len(sandbox)}"
    assert sha(formal_path) == formal_hash_before, "formal ledger changed"
    formal_text = open(formal_path, encoding="utf-8").read()
    assert "SANDBOX-" not in formal_text, "sandbox id leaked into formal ledger"
    print("ASSERTIONS PASSED: 1->0 new, same id, formal hash unchanged, sandbox id not in formal ledger")
    print(f"AFTER formal_sha256={sha(formal_path)}")

if __name__ == "__main__":
    main()
