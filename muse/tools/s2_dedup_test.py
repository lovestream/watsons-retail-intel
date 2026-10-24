"""S2/S3-A 跨会话持久化去重验收测试（沙箱版，v3，2026-10-24 S3-A1 修复版）。

用法: python3 tools/s2_dedup_test.py <wtd_jsonl> <sandbox_out_jsonl> [merged_baseline_index_json]
  merged_baseline_index_json 由 tools/build_merged_baseline.py 从 WTD JSONL +
  LEDGER-2026-10.md 重建（只读衍生索引，非权威账本；冲突时以原账本为准）。
  若未提供该参数，脚本仅用 WTD JSONL 并明确标注合并基线未覆盖（局限）。

v3 相对 v2 的修复（PR #1 复审评论 6384013651 / 6384796719 S3-A1）：
 1. date_match() 的三项含义全部被主流程消费，不再只用 same_event：
    - same_event（事件日相同）+ subject/object/action 相同 + 无新事实 -> SAME_OCCURRENCE
    - same_announcement（双方宣布日均非空且相同）或 ann_matches_event（双向宣布/事件交叉，
      均要求非空）+ subject/object/action 相同 + 无新事实 + 事件日不同 -> SAME_STORY
      （同一宣布行动的故事链接，不是同一次事件合并；不得用 any() 粗暴并为 SAME_OCCURRENCE）
    - 事件日不同且无宣布日匹配（包括双方均无宣布日，None 不算匹配）+ 非跟进报道
      -> DATE_MISMATCH_REVIEW，不合并、不新增
    - 事件日不同 + 明确跟进报道 -> SAME_STORY
 2. 多匹配不再取第一条即 break：收集全部 subject/object/action 匹配记录，按
    SAME_OCCURRENCE > SAME_STORY 排序；若出现多个 SAME_OCCURRENCE 候选（不同 ID、
    同主体/对象/动作/日期）-> AMBIGUOUS_MULTIPLE_MATCH_REVIEW，不自动合并。
 3. 合并历史基线：WTD JSONL + LEDGER-only 衍生索引，共 9 个不同 EV（有效 8、废止 1），
    背景 LEAD 不作新事件；LEDGER-only 事件（天猫、妍丽、抖音榜等）现做全量
    subject/object/action 匹配，不再仅做 ID 存在核对。
 4. update_of 校验：必须指向合并基线中的有效历史 ID，且主体相同；不存在、废止、
    主体不符一律拒绝。
局限（不得虚报）：
  - 本脚本仍是规则单元测试，候选的 new_fact / update_of / is_followup_report 由测试
    输入指定；真实 LLM 抽取能力由盲测（新会话原文输入）另行验收，见盲测记录。
  - “真实账本哈希不变”只证明沙箱未改真实文件，不证明真实入池路径幂等；真实入池
    幂等须由 S3-A3 手动窗口重跑另行验证。
"""
import hashlib, json, sys, copy, re

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def core(text):
    return re.sub(r"[\s，。、；：！？《》“”‘’·\-/]", "", text or "")

def date_match(c, r):
    """三项日期含义，全部要求非空才算匹配（None 不算）。"""
    same_event = c["event_date"] == r["event_date"]
    c_ann = c.get("announcement_date")
    r_ann = r.get("announcement_date")
    same_announcement = bool(c_ann) and bool(r_ann) and c_ann == r_ann
    ann_matches_event = (bool(c_ann) and c_ann == r["event_date"]) or (bool(r_ann) and r_ann == c["event_date"])
    return same_event, same_announcement, ann_matches_event

def main():
    src, out = sys.argv[1], sys.argv[2]
    index_path = sys.argv[3] if len(sys.argv) > 3 else None
    before_hash = sha(src)
    rows = [json.loads(x) for x in open(src, encoding="utf-8") if x.strip()]
    # 合并基线：WTD 为权威，索引仅补 LEDGER-only 记录；冲突时 WTD 胜出并报告
    merged = {}
    for r in rows:
        merged[r["event_id"]] = r
    index_note = "WTD-only (merged baseline NOT covered - limitation)"
    if index_path:
        idx = json.load(open(index_path, encoding="utf-8"))
        for rec in idx["records"]:
            if rec["event_id"] not in merged:
                merged[rec["event_id"]] = rec
        index_note = f"merged index {index_path}: distinct={idx['counts']['distinct_ev']} valid={idx['counts']['valid']} superseded={idx['counts']['superseded']}"
    active = [r for r in merged.values() if r["status"] != "superseded"]
    superseded = {r["event_id"]: r.get("superseded_by") for r in merged.values() if r["status"] == "superseded"}
    by_id = {r["event_id"]: r for r in active}
    before_ids = sorted(by_id.keys())
    print(f"BEFORE wtd_records={len(rows)} merged_valid={len(active)} merged_superseded={len(superseded)}")
    print(f"BEFORE merged_valid_ids={before_ids}")
    print(f"BEFORE superseded_map={superseded}")
    print(f"BEFORE baseline_note={index_note}")
    print(f"BEFORE real_ledger_sha256={before_hash}")

    candidates = [
        {"label": "C1-zhejiang-repost", "subject": "浙江省药监局", "action": "披露", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-22", "new_fact": False, "url": "https://example.test/s2-c1-zhejiang-repost"},
        {"label": "C2-hunan-followup", "subject": "湖南省市场监管局", "action": "开展", "object": "化妆品“净网清源”专项行动", "event_date": "2026-10-21", "new_fact": False, "is_followup_report": True, "url": "https://example.test/s2-c2-hunan-followup"},
        {"label": "C2b-hunan-old-id", "candidate_event_id": "EV-20261021-03", "subject": "湖南省市场监管局", "action": "开展", "object": "化妆品“净网清源”专项行动", "event_date": "2026-10-21", "new_fact": False, "url": "https://example.test/s2-c2b-old-id"},
        {"label": "C3-shenzhen-repost", "subject": "深圳交警联合美团", "action": "启动", "object": "“不闯红灯，安全同行”骑手安全激励活动", "event_date": "2026-10-22", "announcement_date": "2026-10-20", "new_fact": False, "url": "https://example.test/s2-c3-shenzhen-repost"},
        {"label": "C4-hunan-fictitious-progress", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261012-01", "url": "https://example.test/s2-c4-fictitious"},
        {"label": "C5-zhejiang-fictitious-progress", "subject": "浙江省药监局", "action": "发布", "object": "《若干举措》施行细则（测试用虚构数据）", "event_date": "2026-11-15", "new_fact": True, "update_of": "EV-20261022-01", "url": "https://example.test/s2-c5-fictitious"},
        {"label": "C6-fictitious-unrelated", "subject": "某美妆品牌（测试用虚构）", "action": "开设", "object": "即时零售旗舰仓（测试用虚构数据）", "event_date": "2026-10-24", "new_fact": True, "update_of": None, "url": "https://example.test/s2-c6-fictitious"},
        {"label": "C7-zhejiang-wrong-date", "subject": "浙江省药监局", "action": "披露", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-23", "new_fact": False, "url": "https://example.test/s2-c7-wrong-date"},
        {"label": "C8-same-day-different-action", "subject": "浙江省药监局", "action": "发布", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-22", "new_fact": False, "url": "https://example.test/s2-c8-diff-action"},
        {"label": "C9-invalid-updateof-notfound", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261099-99", "url": "https://example.test/s2-c9-notfound"},
        {"label": "C9b-invalid-updateof-superseded", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261021-03", "url": "https://example.test/s2-c9b-superseded"},
        {"label": "C9c-invalid-updateof-subject-mismatch", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261022-01", "url": "https://example.test/s2-c9c-mismatch"},
        {"label": "C10-same-subject-different-event", "subject": "浙江省药监局", "action": "制定", "object": "化妆品电子商务平台检查指南（测试用，不同对象）", "event_date": "2026-10-22", "new_fact": True, "update_of": None, "url": "https://example.test/s2-c10-diff-event"},
        # v3 新增：LEDGER-only 全量匹配 + 宣布/启动交叉日期
        {"label": "C11-tmall-ledger-repeat", "subject": "天猫", "action": "开卖", "object": "双11开卖4小时", "event_date": "2026-10-20", "new_fact": False, "url": "https://example.test/s3-c11-tmall"},
        {"label": "C12-yanli-ledger-repeat", "subject": "妍丽化妆品（中国）有限公司", "action": "被处罚", "object": "广告违法罚款3000元", "event_date": "2026-10-21", "new_fact": False, "url": "https://example.test/s3-c12-yanli"},
        {"label": "C13-shenzhen-cross-date-announcement", "subject": "深圳交警联合美团", "action": "启动", "object": "“不闯红灯，安全同行”骑手安全激励活动", "event_date": "2026-10-23", "announcement_date": "2026-10-20", "new_fact": False, "url": "https://example.test/s3-c13-shenzhen-cross"},
        {"label": "C14-shenzhen-announcement-day", "subject": "深圳交警联合美团", "action": "启动", "object": "“不闯红灯，安全同行”骑手安全激励活动", "event_date": "2026-10-20", "new_fact": False, "url": "https://example.test/s3-c14-shenzhen-ann-day"},
        {"label": "C15-douyin-ledger-repeat", "subject": "抖音商城", "action": "发布", "object": "双11开门红热卖品牌榜", "event_date": "2026-10-23", "new_fact": False, "url": "https://example.test/s3-c15-douyin"},
    ]

    sandbox = copy.deepcopy(rows)
    # 沙箱中补入索引记录的副本（仅用于 supporting_url 合并演示，不改真实账本）
    sandbox_by_id = {r["event_id"]: r for r in sandbox}
    for rec in active:
        if rec["event_id"] not in sandbox_by_id:
            sandbox.append(copy.deepcopy(rec))
            sandbox_by_id[rec["event_id"]] = sandbox[-1]
    decisions = []
    test_counter = 0
    def add_test_record(c, update_of):
        nonlocal test_counter
        test_counter += 1
        tid = f"TEST-{test_counter:02d}"
        rec = {"event_id": tid, "subject": c["subject"], "action": c["action"], "object": c["object"], "event_date": c["event_date"], "fact": "TEST ONLY - fictitious, must not enter real ledger", "update_of": update_of, "status": "test_sandbox_only", "source_url": c["url"]}
        sandbox.append(rec)
        return tid

    for c in candidates:
        if c.get("candidate_event_id") in superseded:
            decisions.append({"candidate": c["label"], "decision": "BLOCKED_SUPERSEDED", "redirect_to": superseded[c["candidate_event_id"]], "new_id": None})
            continue
        # 收集全部 subject/object/action 匹配（不再第一条即 break）
        matches = [r for r in active if r["subject"] == c["subject"] and core(r["object"]) == core(c["object"]) and r["action"] == c["action"]]
        action_mismatch = any(r["subject"] == c["subject"] and core(r["object"]) == core(c["object"]) and r["action"] != c["action"] and c["event_date"] == r["event_date"] for r in active)
        if matches and not c["new_fact"]:
            occ = []
            story = []
            for r in matches:
                same_event, same_ann, ann_event = date_match(c, r)
                if same_event:
                    occ.append(r)
                elif same_ann or ann_event:
                    story.append(r)
                elif c.get("is_followup_report"):
                    story.append(r)
            if len(occ) > 1:
                decisions.append({"candidate": c["label"], "decision": "AMBIGUOUS_MULTIPLE_MATCH_REVIEW", "matched_ids": [r["event_id"] for r in occ], "new_id": None, "note": "multiple SAME_OCCURRENCE candidates; do not auto-merge"})
                continue
            if len(occ) == 1:
                r = occ[0]
                s = sandbox_by_id[r["event_id"]]
                if c["url"] not in s.get("supporting_urls", []):
                    s.setdefault("supporting_urls", []).append(c["url"])
                decisions.append({"candidate": c["label"], "decision": "SAME_OCCURRENCE", "matched_id": r["event_id"], "new_id": None})
                continue
            if len(story) >= 1:
                r = story[0]
                s = sandbox_by_id[r["event_id"]]
                if c["url"] not in s.get("supporting_urls", []):
                    s.setdefault("supporting_urls", []).append(c["url"])
                decisions.append({"candidate": c["label"], "decision": "SAME_STORY", "matched_id": r["event_id"], "new_id": None, "note": "announcement/launch cross-date or followup report; story link only, not occurrence merge"})
                continue
            # 有 subject/object/action 匹配但日期全不符
            decisions.append({"candidate": c["label"], "decision": "DATE_MISMATCH_REVIEW", "matched_id": matches[0]["event_id"], "new_id": None, "note": "date differs, no announcement match (None does not count), not a followup report; do not auto-merge"})
            continue
        if action_mismatch and not c["new_fact"] and not matches:
            decisions.append({"candidate": c["label"], "decision": "ACTION_MISMATCH_REVIEW", "matched_id": None, "new_id": None, "note": "same subject/object/date but different action; do not merge as same occurrence"})
            continue
        if c["new_fact"]:
            uo = c.get("update_of")
            if uo is not None:
                if uo in superseded:
                    decisions.append({"candidate": c["label"], "decision": "INVALID_UPDATE_OF_SUPERSEDED", "redirect_to": superseded[uo], "new_id": None})
                    continue
                if uo not in by_id:
                    decisions.append({"candidate": c["label"], "decision": "INVALID_UPDATE_OF_NOT_FOUND", "new_id": None})
                    continue
                if by_id[uo]["subject"] != c["subject"]:
                    decisions.append({"candidate": c["label"], "decision": "INVALID_UPDATE_OF_SUBJECT_MISMATCH", "matched_id": uo, "new_id": None})
                    continue
            tid = add_test_record(c, uo)
            decisions.append({"candidate": c["label"], "decision": "SANDBOX_NEW_TEST_RECORD", "matched_id": None, "new_id": tid, "update_of": uo})
            continue
        decisions.append({"candidate": c["label"], "decision": "NEEDS_REVIEW_NO_AUTO_RECORD", "matched_id": None, "new_id": None})

    with open(out, "w", encoding="utf-8") as f:
        for r in sandbox:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    active_ids_after = sorted(r["event_id"] for r in sandbox if r["status"] != "superseded" and not r["event_id"].startswith("TEST-") and r["event_id"] in by_id)
    print("DECISIONS:")
    for d in decisions:
        print(" ", d)
    print(f"AFTER sandbox_records={len(sandbox)} active_real_ids_in_baseline={active_ids_after}")
    print(f"AFTER test_records={[r['event_id'] for r in sandbox if r['event_id'].startswith('TEST-')]}")
    for s in sandbox:
        if not s["event_id"].startswith("TEST-"):
            for bad in ("s2-c7-wrong-date", "s2-c8-diff-action"):
                assert bad not in " ".join(s.get("supporting_urls", [])), f"{bad} was merged into {s['event_id']}"
    assert active_ids_after == before_ids, f"active real IDs changed: {before_ids} -> {active_ids_after}"
    assert sha(src) == before_hash, "real ledger changed during test"
    assert test_counter == 4, f"expected 4 TEST records (C4,C5,C6,C10), got {test_counter}"
    print("ASSERTIONS PASSED: no new real EV id, no reactivated superseded id, C7/C8 not merged, invalid update_of rejected, real ledger hash unchanged")
    print(f"AFTER real_ledger_sha256={sha(src)}")

if __name__ == "__main__":
    main()
