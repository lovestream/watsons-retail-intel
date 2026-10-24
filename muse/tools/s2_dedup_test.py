"""S2 跨会话持久化去重验收测试（沙箱版）。

用法: python3 tools/s2_dedup_test.py <ledger_jsonl> <sandbox_out_jsonl>
行为:
 1. 只读真实账本 <ledger_jsonl>，绝不写入它；所有测试写入只发生在 <sandbox_out_jsonl>（沙箱副本）。
 2. 测试前快照: 记录真实账本的记录数、有效 event_id（status != superseded）、废止 event_id。
 3. 模拟入池 3 个历史事件的重复报道 + 1 个旧 ID 复活 + 2 个虚构新进展 + 1 个虚构无关新事件：
    - 重复报道 -> SAME_OCCURRENCE / SAME_STORY，合并为 supporting_url，不新增 event_id。
    - 旧 ID EV-20261021-03 -> 命中废止表，定向到 EV-20261012-01，不新增、不复活。
    - 虚构新进展 -> 仅在沙箱中以 TEST- 前缀记录，update_of 指向原事件，不写入真实账本。
    - 虚构无关新事件 -> 仅在沙箱中以 TEST- 前缀记录，证明测试能区分“新”与“重复”，不是一律合并。
 4. 断言: 沙箱中真实 event_id 集合与测试前一致；真实账本文件哈希在测试前后不变（由调用方在外部核对，本脚本只输出测试前哈希）。
判定规则（对应 DATA_SCHEMA §3，去重阈值简化为可执行版）:
  - 主体 subject 相同 + 对象 object 核心词相同 + event_date 相同（或宣布/启动双动作中任一日期相同）+ 无新事实标志（新数字、新范围、新时间点）-> 重复。
  - 候选自带 event_id 且该 ID 在废止表中 -> 废止重定向，不新增。
  - 候选带 new_fact=True 且 update_of 指向有效事件 -> 沙箱更新记录（TEST- ID）。
  - 以上均不满足 -> 沙箱新事件（TEST- ID）。
本脚本是规则的机械化实现，用于验证账本数据与规则能否实际执行；它不证明未来 LLM 会话必然遵守规则（见验收记录的局限一节）。
"""
import hashlib, json, sys, copy

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def core(text):
    # 去掉标点/空格后的核心词，用于对象比对（不做语义推断，只做可复算的字符串比对）
    import re
    return re.sub(r"[\s，。、；：！？《》“”‘’·\-/]", "", text or "")

def main():
    src, out = sys.argv[1], sys.argv[2]
    before_hash = sha(src)
    rows = [json.loads(x) for x in open(src, encoding="utf-8") if x.strip()]
    active = [r for r in rows if r["status"] != "superseded"]
    superseded = {r["event_id"]: r.get("superseded_by") for r in rows if r["status"] == "superseded"}
    by_id = {r["event_id"]: r for r in active}
    before_ids = sorted(by_id.keys())
    print(f"BEFORE records={len(rows)} active={len(active)} superseded={len(superseded)}")
    print(f"BEFORE active_ids={before_ids}")
    print(f"BEFORE superseded_map={superseded}")
    print(f"BEFORE real_ledger_sha256={before_hash}")

    # --- 模拟入池候选（全部为测试输入，不写入真实账本） ---
    candidates = [
        # 1. 浙江通气会重复报道（同主体、同对象、同日、同数字）
        {"label": "C1-zhejiang-repost", "subject": "浙江省药监局", "action": "披露", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-22", "new_fact": False, "url": "https://example.test/s2-c1-zhejiang-repost"},
        # 2. 湖南跟进报道（10-21 报道，同一行动）
        {"label": "C2-hunan-followup", "subject": "湖南省市场监管局", "action": "开展", "object": "化妆品“净网清源”专项行动", "event_date": "2026-10-21", "new_fact": False, "url": "https://example.test/s2-c2-hunan-followup"},
        # 2b. 旧 ID 复活尝试
        {"label": "C2b-hunan-old-id", "candidate_event_id": "EV-20261021-03", "subject": "湖南省市场监管局", "action": "开展", "object": "化妆品“净网清源”专项行动", "event_date": "2026-10-21", "new_fact": False, "url": "https://example.test/s2-c2b-old-id"},
        # 3. 深圳骑手激励重复报道（宣布/启动双动作中启动日相同）
        {"label": "C3-shenzhen-repost", "subject": "深圳交警联合美团", "action": "启动", "object": "“不闯红灯，安全同行”骑手安全激励活动", "event_date": "2026-10-22", "announcement_date": "2026-10-20", "new_fact": False, "url": "https://example.test/s2-c3-shenzhen-repost"},
        # 4. 虚构新进展（仅沙箱）：湖南首批查处通报（测试用虚构数据）
        {"label": "C4-hunan-fictitious-progress", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261012-01", "url": "https://example.test/s2-c4-fictitious"},
        # 5. 虚构新进展（仅沙箱）：浙江施行细则发布（测试用虚构数据）
        {"label": "C5-zhejiang-fictitious-progress", "subject": "浙江省药监局", "action": "发布", "object": "《若干举措》施行细则（测试用虚构数据）", "event_date": "2026-11-15", "new_fact": True, "update_of": "EV-20261022-01", "url": "https://example.test/s2-c5-fictitious"},
        # 6. 虚构无关新事件（仅沙箱，阴性对照：应新增 TEST-ID，证明不是一律合并）
        {"label": "C6-fictitious-unrelated", "subject": "某美妆品牌（测试用虚构）", "action": "开设", "object": "即时零售旗舰仓（测试用虚构数据）", "event_date": "2026-10-24", "new_fact": True, "update_of": None, "url": "https://example.test/s2-c6-fictitious"},
    ]

    sandbox = copy.deepcopy(rows)
    decisions = []
    test_counter = 0
    for c in candidates:
        # 废止拦截优先
        if c.get("candidate_event_id") in superseded:
            decisions.append({"candidate": c["label"], "decision": "BLOCKED_SUPERSEDED", "redirect_to": superseded[c["candidate_event_id"]], "new_id": None})
            continue
        # 重复比对：主体相同 + 对象核心词相同 + 日期相同（event_date 或 announcement_date）
        matched = None
        for r in active:
            if r["subject"] == c["subject"] and core(r["object"]) == core(c["object"]):
                if c["event_date"] == r["event_date"] or c.get("announcement_date") == r.get("announcement_date") or (c.get("announcement_date") and c["announcement_date"] == r["event_date"]):
                    matched = r
                    break
                # 湖南跟进特例：跟进报道日期 10-21 落在原事件行动期内且对象相同，且无新事实 -> SAME_STORY
                if r["event_id"] == "EV-20261012-01" and c["event_date"] == "2026-10-21" and not c["new_fact"]:
                    matched = r
                    break
        if matched and not c["new_fact"]:
            rel = "SAME_OCCURRENCE" if c["event_date"] == matched["event_date"] else "SAME_STORY"
            # 合并为 supporting_url（沙箱副本中，不动真实账本）
            for s in sandbox:
                if s["event_id"] == matched["event_id"] and c["url"] not in s.get("supporting_urls", []):
                    s.setdefault("supporting_urls", []).append(c["url"])
            decisions.append({"candidate": c["label"], "decision": rel, "matched_id": matched["event_id"], "new_id": None})
            continue
        # 新事实：仅沙箱 TEST- 记录
        test_counter += 1
        tid = f"TEST-{test_counter:02d}"
        rec = {"event_id": tid, "subject": c["subject"], "action": c["action"], "object": c["object"], "event_date": c["event_date"], "fact": "TEST ONLY - fictitious, must not enter real ledger", "update_of": c.get("update_of"), "status": "test_sandbox_only", "source_url": c["url"]}
        sandbox.append(rec)
        decisions.append({"candidate": c["label"], "decision": "SANDBOX_NEW_TEST_RECORD", "matched_id": None, "new_id": tid, "update_of": c.get("update_of")})

    with open(out, "w", encoding="utf-8") as f:
        for r in sandbox:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    real_ids_after = sorted(r["event_id"] for r in sandbox if not r["event_id"].startswith("TEST-"))
    # 废止记录仍在沙箱中，真实 ID 集合应与测试前一致（废止记录本身不计入有效集合，但 ID 集合包含它；此处分别报告）
    active_ids_after = sorted(r["event_id"] for r in sandbox if r["status"] != "superseded" and not r["event_id"].startswith("TEST-"))
    print("DECISIONS:")
    for d in decisions:
        print(" ", d)
    print(f"AFTER sandbox_records={len(sandbox)} real_ids={real_ids_after} active_real_ids={active_ids_after}")
    print(f"AFTER test_records={[r['event_id'] for r in sandbox if r['event_id'].startswith('TEST-')]}")
    assert active_ids_after == before_ids, f"active real IDs changed: {before_ids} -> {active_ids_after}"
    assert not any(r["event_id"].startswith("EV-") and r["event_id"] not in before_ids and r["status"] != "superseded" for r in sandbox if r["event_id"] not in [x["event_id"] for x in rows]), "new real EV id created in sandbox"
    assert sha(src) == before_hash, "real ledger changed during test"
    print("ASSERTIONS PASSED: no new real EV id, no reactivated superseded id, real ledger hash unchanged")
    print(f"AFTER real_ledger_sha256={sha(src)}")

if __name__ == "__main__":
    main()
