"""S2 跨会话持久化去重验收测试（沙箱版，v2，2026-10-24 修复版）。

用法: python3 tools/s2_dedup_test.py <ledger_jsonl> <sandbox_out_jsonl> [ledger_md]
  ledger_md 可选：LEDGER-2026-10.md，用于合并历史 ID 基线核对（仅 ID 层，见局限）。

v1 缺陷（PR #1 复审评论 6383696372，P0，必须修复）：
  旧条件 `c.get("announcement_date") == r.get("announcement_date")` 在双方都缺失时
  返回 None == None == True，导致同主体、同对象、日期不同也被判为匹配。
  复现（审查方构造）：浙江 EV-20261022-01 的 subject/object/action 不变，仅
  event_date 改为 2026-10-23、new_fact=False，旧代码日期谓词返回 True，走 SAME_STORY 合并。
  v2 修法：所有可选日期匹配都要求双方实际非空（见 date_match()），空值不再算匹配；
  并要求 SAME_OCCURRENCE 的 action 也相同；同主体/对象但日期不符且无新事实时
  不自动合并，改为 DATE_MISMATCH_REVIEW；同日不同动作改为 ACTION_MISMATCH_REVIEW；
  湖南跟进不再硬编码 ID/日期，改为通用规则：subject/object/action 相同、无新事实、
  且候选明确标记为跟进报道（is_followup_report=True）时判 SAME_STORY（故事链接，
  不是同一次事件合并）；无效 update_of 一律拒绝。

行为：
 1. 只读真实账本，绝不写入它；所有测试写入只发生在沙箱副本。
 2. 测试前快照：真实账本记录数、有效 event_id、废止 event_id、真实账本 SHA-256。
 3. 候选分三类：重复报道（C1-C3）、虚构新进展/无关（C4-C6，仅沙箱 TEST-）、
    反例（C7-C11，修复后必须不合并/不新增真实 ID，其中 C11 为合并基线局限的显式标记）。
 4. 断言：有效真实 ID 集合不变；被拒绝的反例产生 0 条新记录；C7/C8 的 URL 未被
    合并进任何真实记录的 supporting_urls；真实账本哈希不变；沙箱新增记录全部为 TEST- 前缀。
局限（不得虚报）：
  - 本脚本是规则单元测试，不是端到端采集→抽取→去重的集成验收；候选的 new_fact /
    update_of / is_followup_report 仍由测试输入指定，真实 LLM 抽取能力须由盲测（新会话
    原文输入，不预告 ID/判断）另行验收，见 reviews/S2-BLIND-TEST-PROTOCOL-2026-10-24.md。
  - 脚本主匹配只针对 WTD JSONL 的 4 条记录；LEDGER-2026-10.md 的其他历史事件
    （如天猫 EV-20261020-01、妍丽 EV-20261021-02）在本脚本中仅做 ID 基线核对，
    未做 subject/object/action 全量匹配，因此合并基线匹配仍为部分通过，见 C11 与验收记录。
"""
import hashlib, json, sys, copy, re

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

def core(text):
    return re.sub(r"[\s，。、；：！？《》“”‘’·\-/]", "", text or "")

def date_match(c, r):
    """v2 日期匹配：每个可选日期比较都要求双方非空，空值绝不算匹配。"""
    same_event = c["event_date"] == r["event_date"]
    c_ann = c.get("announcement_date")
    r_ann = r.get("announcement_date")
    same_announcement = bool(c_ann) and bool(r_ann) and c_ann == r_ann
    # 双向：候选宣布日 == 记录事件日，或记录宣布日 == 候选事件日（均要求非空）
    ann_matches_event = (bool(c_ann) and c_ann == r["event_date"]) or (bool(r_ann) and r_ann == c["event_date"])
    return same_event, same_announcement, ann_matches_event

def main():
    src, out = sys.argv[1], sys.argv[2]
    ledger_md = sys.argv[3] if len(sys.argv) > 3 else None
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
    merged_ids = set(before_ids) | set(superseded.keys())
    if ledger_md:
        md_text = open(ledger_md, encoding="utf-8").read()
        md_ids = sorted(set(re.findall(r"EV-\d{8}-\d{2}", md_text)))
        merged_ids |= set(md_ids)
        print(f"MERGED_BASELINE ledger_md_ids={md_ids} merged_distinct_ev={sorted(merged_ids)}")
    else:
        print("MERGED_BASELINE ledger_md not supplied; merged set is WTD-only (limitation, see C11)")

    candidates = [
        {"label": "C1-zhejiang-repost", "subject": "浙江省药监局", "action": "披露", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-22", "new_fact": False, "url": "https://example.test/s2-c1-zhejiang-repost"},
        {"label": "C2-hunan-followup", "subject": "湖南省市场监管局", "action": "开展", "object": "化妆品“净网清源”专项行动", "event_date": "2026-10-21", "new_fact": False, "is_followup_report": True, "url": "https://example.test/s2-c2-hunan-followup"},
        {"label": "C2b-hunan-old-id", "candidate_event_id": "EV-20261021-03", "subject": "湖南省市场监管局", "action": "开展", "object": "化妆品“净网清源”专项行动", "event_date": "2026-10-21", "new_fact": False, "url": "https://example.test/s2-c2b-old-id"},
        {"label": "C3-shenzhen-repost", "subject": "深圳交警联合美团", "action": "启动", "object": "“不闯红灯，安全同行”骑手安全激励活动", "event_date": "2026-10-22", "announcement_date": "2026-10-20", "new_fact": False, "url": "https://example.test/s2-c3-shenzhen-repost"},
        {"label": "C4-hunan-fictitious-progress", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261012-01", "url": "https://example.test/s2-c4-fictitious"},
        {"label": "C5-zhejiang-fictitious-progress", "subject": "浙江省药监局", "action": "发布", "object": "《若干举措》施行细则（测试用虚构数据）", "event_date": "2026-11-15", "new_fact": True, "update_of": "EV-20261022-01", "url": "https://example.test/s2-c5-fictitious"},
        {"label": "C6-fictitious-unrelated", "subject": "某美妆品牌（测试用虚构）", "action": "开设", "object": "即时零售旗舰仓（测试用虚构数据）", "event_date": "2026-10-24", "new_fact": True, "update_of": None, "url": "https://example.test/s2-c6-fictitious"},
        # --- v2 反例：修复前会错误合并，修复后必须不合并 ---
        # C7：审查方 exact 反例。浙江 subject/object/action 不变，仅 event_date 2026-10-23，无宣布日，无新事实。
        {"label": "C7-zhejiang-wrong-date", "subject": "浙江省药监局", "action": "披露", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-23", "new_fact": False, "url": "https://example.test/s2-c7-wrong-date"},
        # C8：同日不同动作。浙江 subject/object/date 相同，action 由“披露”改为“发布”。
        {"label": "C8-same-day-different-action", "subject": "浙江省药监局", "action": "发布", "object": "《若干举措》（20 条）执行进展", "event_date": "2026-10-22", "new_fact": False, "url": "https://example.test/s2-c8-diff-action"},
        # C9：无效 update_of 三种。
        {"label": "C9-invalid-updateof-notfound", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261099-99", "url": "https://example.test/s2-c9-notfound"},
        {"label": "C9b-invalid-updateof-superseded", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261021-03", "url": "https://example.test/s2-c9b-superseded"},
        {"label": "C9c-invalid-updateof-subject-mismatch", "subject": "湖南省市场监管局", "action": "通报", "object": "化妆品“净网清源”专项行动首批查处案例（测试用虚构数据）", "event_date": "2026-10-25", "new_fact": True, "update_of": "EV-20261022-01", "url": "https://example.test/s2-c9c-mismatch"},
        # C10：同主体不同事件。浙江 subject 相同，object 不同，新事实 True，应新增 TEST- 而非合并。
        {"label": "C10-same-subject-different-event", "subject": "浙江省药监局", "action": "制定", "object": "化妆品电子商务平台检查指南（测试用，不同对象）", "event_date": "2026-10-22", "new_fact": True, "update_of": None, "url": "https://example.test/s2-c10-diff-event"},
        # C11：合并基线局限显式标记。天猫事件只在 LEDGER 中，不在 WTD JSONL 主匹配中。
        {"label": "C11-tmall-ledger-only", "subject": "天猫", "action": "开卖", "object": "双11开卖4小时（LEDGER 事件 EV-20261020-01，仅 ID 基线核对）", "event_date": "2026-10-20", "new_fact": False, "ledger_only_check": True, "url": "https://example.test/s2-c11-tmall"},
    ]

    sandbox = copy.deepcopy(rows)
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
        # 废止拦截优先（候选自带 ID 或 update_of 指向废止 ID 均处理，update_of 在后段校验）
        if c.get("candidate_event_id") in superseded:
            decisions.append({"candidate": c["label"], "decision": "BLOCKED_SUPERSEDED", "redirect_to": superseded[c["candidate_event_id"]], "new_id": None})
            continue
        if c.get("ledger_only_check"):
            exists = "EV-20261020-01" in merged_ids
            decisions.append({"candidate": c["label"], "decision": "MERGED_BASELINE_ID_EXISTS_BUT_NOT_MATCHED_BY_WTD_SCRIPT" if exists else "MERGED_BASELINE_ID_MISSING", "matched_id": "EV-20261020-01" if exists else None, "new_id": None, "note": "WTD-only script does not subject/object-match LEDGER-only events; real intake must check LEDGER first - this part is NOT passed by script alone"})
            continue
        # 主匹配：subject + object + action 相同，再看日期（v2，非空才算）
        same_subject_object_action = None
        for r in active:
            if r["subject"] == c["subject"] and core(r["object"]) == core(c["object"]) and r["action"] == c["action"]:
                same_subject_object_action = r
                break
        # 同主体/对象但动作不同 -> 动作不符，不合并
        action_mismatch = False
        if not same_subject_object_action:
            for r in active:
                if r["subject"] == c["subject"] and core(r["object"]) == core(c["object"]) and r["action"] != c["action"] and c["event_date"] == r["event_date"]:
                    action_mismatch = True
                    break
        # 统一处理同主体/对象/动作匹配（v2 修正：SAME_STORY 不依赖日期谓词短路）：
        #  - 日期相同 -> SAME_OCCURRENCE
        #  - 日期不同 + 明确标记为跟进报道 -> SAME_STORY（故事链接，不是同一次事件合并）
        #  - 日期不同 + 非跟进报道 -> DATE_MISMATCH_REVIEW（不合并、不新增）
        if same_subject_object_action and not c["new_fact"]:
            same_event, _, _ = date_match(c, same_subject_object_action)
            if same_event:
                for s in sandbox:
                    if s["event_id"] == same_subject_object_action["event_id"] and c["url"] not in s.get("supporting_urls", []):
                        s.setdefault("supporting_urls", []).append(c["url"])
                decisions.append({"candidate": c["label"], "decision": "SAME_OCCURRENCE", "matched_id": same_subject_object_action["event_id"], "new_id": None})
                continue
            if c.get("is_followup_report"):
                for s in sandbox:
                    if s["event_id"] == same_subject_object_action["event_id"] and c["url"] not in s.get("supporting_urls", []):
                        s.setdefault("supporting_urls", []).append(c["url"])
                decisions.append({"candidate": c["label"], "decision": "SAME_STORY", "matched_id": same_subject_object_action["event_id"], "new_id": None})
                continue
            decisions.append({"candidate": c["label"], "decision": "DATE_MISMATCH_REVIEW", "matched_id": same_subject_object_action["event_id"], "new_id": None, "note": "date differs, no announcement match (None does not count), no new fact, not a followup report; do not auto-merge"})
            continue
        if action_mismatch and not c["new_fact"]:
            decisions.append({"candidate": c["label"], "decision": "ACTION_MISMATCH_REVIEW", "matched_id": None, "new_id": None, "note": "same subject/object/date but different action; do not merge as same occurrence"})
            continue
        # 新事实：校验 update_of
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
        # 兜底：无匹配、无新事实、非上述 REVIEW 情形 -> 不自动新增，标记人工复核（防止静默合并或静默新增）
        decisions.append({"candidate": c["label"], "decision": "NEEDS_REVIEW_NO_AUTO_RECORD", "matched_id": None, "new_id": None})

    with open(out, "w", encoding="utf-8") as f:
        for r in sandbox:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    active_ids_after = sorted(r["event_id"] for r in sandbox if r["status"] != "superseded" and not r["event_id"].startswith("TEST-"))
    print("DECISIONS:")
    for d in decisions:
        print(" ", d)
    print(f"AFTER sandbox_records={len(sandbox)} active_real_ids={active_ids_after}")
    print(f"AFTER test_records={[r['event_id'] for r in sandbox if r['event_id'].startswith('TEST-')]}")
    # 强断言：C7/C8 URL 未被合并进真实记录
    for s in sandbox:
        if not s["event_id"].startswith("TEST-"):
            for bad in ("s2-c7-wrong-date", "s2-c8-diff-action"):
                assert bad not in " ".join(s.get("supporting_urls", [])), f"{bad} was merged into {s['event_id']}"
    assert active_ids_after == before_ids, f"active real IDs changed: {before_ids} -> {active_ids_after}"
    assert sha(src) == before_hash, "real ledger changed during test"
    # 被拒绝的反例必须产生 0 条新记录：C7/C8/C9/C9b/C9c/C11 不在 TEST 记录中（TEST 记录只应来自 C4/C5/C6/C10）
    assert test_counter == 4, f"expected 4 TEST records (C4,C5,C6,C10), got {test_counter}"
    print("ASSERTIONS PASSED: no new real EV id, no reactivated superseded id, C7/C8 not merged, invalid update_of rejected, real ledger hash unchanged")
    print(f"AFTER real_ledger_sha256={sha(src)}")

if __name__ == "__main__":
    main()
