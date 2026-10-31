#!/usr/bin/env python3
"""schema.py — reader_brief.json（P08 输出）的字段白名单 + 确定性验收。

P09 只做"主张级"语义复核；字段、枚举、长度、fact_id/claim_id、URL、数字、
日期、状态这些**确定性**检查必须先在这里跑完，不消耗模型。
"""

import re
from datetime import date
from typing import Dict, List, Tuple

REQUIRED = ("period_type", "period_start", "period_end", "title",
            "one_line_takeaway", "signals", "competitor_cards",
            "priority_actions", "watch_items", "omitted_fact_ids", "audit")
# 2026-10-31：真实周数据（586 事件）下，未采用事实常达 20+ 条。
# omitted_fact_ids 是"编辑取舍说明"，不是全量清单 —— 列代表性条目即可，
# 全量清单由程序侧 coverage_report 统计，不应让模型逐条枚举（会挤占正文预算）。
# 因此上限提升到 24 并改为 warning 级：超限不阻断，只提示精简。
CAPS = {"signals": 3, "competitor_cards": 2, "priority_actions": 3,
        "watch_items": 2, "omitted_fact_ids": 24}
SOFT_CAPS = {"omitted_fact_ids"}   # 超限只 warning，不阻断
TEXT_LIMITS = {"title": 32, "one_line_takeaway": 90,
               "signals.headline": 24, "signals.fact": 110,
               "signals.business_impact": 100,
               "competitor_cards.move": 110, "competitor_cards.why_matters": 90,
               "priority_actions.title": 20, "priority_actions.trial": 90,
               "priority_actions.metric": 60, "watch_items.trigger": 60,
               "watch_items.business_relevance": 80}
FORBIDDEN = [
    (r"\bE\d{8}_\d{3,4}\b", "event_id_leak"),
    (r"\bP[0-2]\b", "priority_leak"),
    (r"置信度|八维|加权分|评分|附录|来源清单|证据清单|verified|human_reviewed",
     "internal_leak"),
    (r"【[ABK]】|\['[ABK]'\]|\[\"[ABK]\"\]", "tag_leak"),
    (r"<html|<table|<div", "html_leak"),
]
OVERCLAIM = r"已上线|已完成|已落地|已开卖|正式上线|正式开卖|已签署|已交割|已完成收购"
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万亿|亿|万|千|%|％|家|个|款|天|小时|分钟|元|倍)?")
PROMO = {"双11": "大促", "双12": "大促", "双十一": "大促", "618": "大促", "38节": "大促"}


def _issue(code, severity, where, message):
    return {"code": code, "severity": severity, "where": where,
            "message": message}


def mask_promo(text: str) -> str:
    t = text or ""
    for k, v in PROMO.items():
        t = t.replace(k, v)
    return t


def numbers(text: str) -> List[str]:
    return [(m.group(1).replace(",", "") + (m.group(2) or ""))
            for m in NUM_RE.finditer(mask_promo(text or ""))]


def parse_date(text: str, year: int):
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text or "")
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text or "")
    if m:
        try:
            return date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _texts(item: Dict, fields) -> str:
    return " ".join(str(item.get(f, "")) for f in fields)


def validate_brief(brief: Dict, pack: Dict, send_at: str,
                   allowed_fact_ids=None) -> Dict:
    facts = {f["fact_id"]: f for f in pack.get("facts", [])}
    claims = {f["claim_id"]: f for f in pack.get("facts", [])}
    pack_urls = {f["source"]["url"] for f in pack.get("facts", [])}
    today = date.fromisoformat(send_at)
    errors: List[Dict] = []
    warnings: List[Dict] = []

    # Report identity is a program-owned invariant, not a value that P08/P09
    # may change during editing or targeted corrections.
    period = pack.get("period") or {}
    for key, expected in (("period_type", pack.get("period_type")),
                          ("period_start", period.get("start")),
                          ("period_end", period.get("end"))):
        if expected and brief.get(key) != expected:
            errors.append(_issue("period_identity_mismatch", "error", key,
                                 f"期别与来源包不一致，应为 {expected}"))

    for key in REQUIRED:
        if key not in brief:
            errors.append(_issue("missing_field", "error", key, "缺少必填字段"))
    for key, cap in CAPS.items():
        items = brief.get(key) or []
        if not isinstance(items, list):
            errors.append(_issue("bad_type", "error", key, "应为数组"))
            continue
        if len(items) > cap:
            if key in SOFT_CAPS:
                warnings.append(_issue("too_many_items", "warning", key,
                                       f"{len(items)} 条 > 建议上限 {cap}（已截断显示）"))
                brief[key] = items[:cap]
            else:
                errors.append(_issue("too_many_items", "error", key,
                                     f"{len(items)} 条 > 上限 {cap}"))
    for key, limit in TEXT_LIMITS.items():
        section = key.split(".")[0]
        field = key.split(".")[1] if "." in key else key
        if "." in key:
            for i, it in enumerate(brief.get(section) or []):
                if len(str(it.get(field, ""))) > limit:
                    warnings.append(_issue("too_long", "warning",
                                           f"{section}[{i}].{field}",
                                           f"超过 {limit} 字"))
        elif len(str(brief.get(key, ""))) > limit:
            warnings.append(_issue("too_long", "warning", key, f"超过 {limit} 字"))

    def check_item(section: str, i: int, item: Dict, id_field: str,
                   text_fields: Tuple[str, ...], check_dates: bool):
        where = f"{section}[{i}]"
        ids = item.get(id_field) or []
        if not ids:
            errors.append(_issue("no_fact_ids", "error", where, "条目没有绑定事实"))
            return
        bad = [x for x in ids if x not in facts]
        if bad:
            errors.append(_issue("unknown_fact_id", "error", where, f"不存在: {bad}"))
            return
        if allowed_fact_ids is not None:
            outside = [x for x in ids if x not in allowed_fact_ids]
            if outside:
                errors.append(_issue("p05_topic_not_published", "error", where,
                                     f"引用了 P05 未发布选题的事实: {outside}"))
        cids = item.get("claim_ids") or []
        # P08's mandatory claim_ids/source_refs cannot be a prompt-only rule:
        # an omitted or empty list is a delivery-blocking error.
        if section in ("signals", "competitor_cards"):
            if "claim_ids" not in item or not isinstance(item.get("claim_ids"), list) or not cids:
                errors.append(_issue("missing_claim_binding", "error", where,
                                     "公开事实卡必须显式绑定非空 claim_ids"))
            if not isinstance(item.get("source_refs"), list) or not item.get("source_refs"):
                errors.append(_issue("missing_source_binding", "error", where,
                                     "公开事实卡必须显式绑定来源链接和主张"))
        if cids:
            badc = [x for x in cids if x not in claims]
            if badc:
                errors.append(_issue("unknown_claim_id", "error", where, f"不存在: {badc}"))
            elif section in ("signals", "competitor_cards"):
                # Every CLxxx is program-assigned to exactly one Fxxx.
                # Merely referring to another valid claim in the pack is not proof.
                required_cids = {facts[x]["claim_id"] for x in ids}
                if set(cids) != required_cids:
                    errors.append(_issue("claim_fact_mismatch", "error", where,
                                         "claim_ids 与 fact_ids 必须精确一一对应"))
        # 来源链接必须真实存在且属于所引用事实
        own_urls = {facts[x]["source"]["url"] for x in ids}
        bound = set()
        for j, ref in enumerate(item.get("source_refs") or []):
            url = ref.get("url") or ""
            if not url:
                errors.append(_issue("missing_source_url", "error",
                                     f"{where}.source_refs[{j}]", "缺少来源链接"))
                continue
            if url not in pack_urls:
                errors.append(_issue("unbound_source_url", "error",
                                     f"{where}.source_refs[{j}]",
                                     "链接不在事实包中"))
            elif url not in own_urls:
                errors.append(_issue("source_not_of_fact", "error",
                                     f"{where}.source_refs[{j}]",
                                     "链接与所引用事实不匹配"))
            sup = ref.get("supports_claim_ids") or []
            if section in ("signals", "competitor_cards"):
                if not isinstance(ref.get("supports_claim_ids"), list) or not sup:
                    errors.append(_issue("missing_source_claim_binding", "error",
                                         f"{where}.source_refs[{j}]",
                                         "来源必须显式绑定支持的 claim_ids"))
            if any(x not in (item.get("claim_ids") or []) for x in sup):
                errors.append(_issue("supports_unbound", "error",
                                     f"{where}.source_refs[{j}]",
                                     "来源所支持的 claim_ids 不在本条中"))
            for cid in sup:
                if cid in claims:
                    cf = claims[cid]
                    if cf["source"]["url"] != url or cf["fact_id"] not in ids:
                        errors.append(_issue("source_claim_mismatch", "error",
                                             f"{where}.source_refs[{j}]",
                                             f"来源链接不能支持主张 {cid}"))
                    else:
                        bound.add(cid)
        if section in ("signals", "competitor_cards") and cids and set(cids) != bound:
            errors.append(_issue("uncovered_claim_ids", "error", where,
                                 "至少一条主张未获得所属原始来源的明确绑定"))

        joined = _texts(item, text_fields)
        fact_texts = " ".join(facts[x]["text"] for x in ids)
        fact_nums = {n for x in ids for n in facts[x].get("numbers", [])}
        for n in numbers(joined):
            bare = re.sub(r"[^\d.]", "", n)
            if len(bare) <= 1 and float(bare or 0) <= 5:
                continue
            if n in fact_nums or (bare and bare in re.sub(r"[^\d.]", "", fact_texts)):
                continue
            errors.append(_issue("number_not_in_fact", "error", where,
                                 f"数字 {n} 未出现在所引用事实中"))
        statuses = {facts[x]["status"] for x in ids}
        # A mixed completed+proposed bundle must not launder the proposed
        # event into a completed one merely because another cited fact occurred.
        #
        # 2026-10-31 收紧：原来对整段文本一刀切匹配"已上线/已完成"，
        # 导致"平台确实已上线某功能"这类正常转述也被判过度声明（10 轮中 3 次）。
        # 现在按"句"归属：只有含 OVERCLAIM 的那一句，且该句同时出现
        # 未落地事实的关键实体（主体/平台）时才判错，避免跨句连坐。
        if statuses & {"proposed", "reported"}:
            _soft = [x for x in ids
                     if facts[x]["status"] in ("proposed", "reported")]
            _all_ents = set()
            for x in ids:
                _all_ents |= {e for e in (facts[x].get("platforms") or []) if e}
                _all_ents |= {e for e in (facts[x].get("competitors") or []) if e}
            _ents = set()
            for x in _soft:
                _ents |= {e for e in (facts[x].get("platforms") or []) if e}
                _ents |= {e for e in (facts[x].get("competitors") or []) if e}
            _bad = []
            for _sent in re.split(r"[。！？!?；;\n]", joined):
                if not re.search(OVERCLAIM, _sent):
                    continue
                # 只有"能明确判定该句在讲别的主体"时才豁免；
                # 句子不含任何主体名时无法排除，保守判错（安全优先）。
                if _ents and not any(e in _sent for e in _ents) and \
                        any(_e in _sent for _e in _all_ents):
                    continue          # 明确在讲另一个主体，不连坐
                _bad.append(_sent.strip()[:40])
            if _bad:
                errors.append(_issue("status_overclaim", "error", where,
                                     f"未落地事项被写成已落地：{_bad[0]}"))
        if section == "competitor_cards":
            occurred_text = str(item.get("occurred_on") or "").strip()
            # 2026-10-31：当引用事实本身没有"日级发生时间"（date_role!=occurred）时，
            # 允许省略 occurred_on —— 宁可留空，也不许用发文日/抓取日冒充发生日。
            # 若填写则必须能被引用事实的日级发生日支撑。
            _has_day = any(facts[x].get("date_role") == "occurred" for x in ids)
            if occurred_text:
                event_date = parse_date(occurred_text, today.year)
                if not event_date:
                    errors.append(_issue("invalid_occurred_on", "error", where,
                                         "事件发生日无法解析；不得以发文日或抓取日替代"))
                else:
                    # A reported/published date is not an event occurrence date.
                    # Only an original fact with occurrence semantics or an
                    # explicit matching date within its factual text can support it.
                    supported = any(
                        (facts[x].get("date_role") == "occurred" and
                         facts[x].get("date") == event_date.isoformat()) or
                        parse_date(facts[x].get("text", ""), today.year) == event_date
                        for x in ids
                    )
                    if not supported:
                        errors.append(_issue("occurred_date_unbound", "error", where,
                                             "事件发生日期未在主张事实中得到支持"))
            elif _has_day:
                pass  # 有日级发生日却留空，可接受（不强制补写）
        if check_dates:
            d = parse_date(joined, today.year)
            if d and d < today:
                errors.append(_issue("expired_deadline", "error", where,
                                     f"期限 {d.isoformat()} 已过期"))
        for pat, code in FORBIDDEN:
            if re.search(pat, joined):
                errors.append(_issue(code, "error", where, f"匹配 {pat}"))

    for i, it in enumerate(brief.get("signals") or []):
        check_item("signals", i, it, "fact_ids",
                   ("headline", "fact", "business_impact"), False)
    for i, it in enumerate(brief.get("competitor_cards") or []):
        check_item("competitor_cards", i, it, "fact_ids",
                   ("name", "move", "why_matters"), False)
    for i, it in enumerate(brief.get("priority_actions") or []):
        check_item("priority_actions", i, it, "linked_fact_ids",
                   ("title", "precondition", "trial", "metric"), True)
        _due = parse_date(str(it.get("due_date") or ""), today.year)
        if _due and _due < today:
            errors.append(_issue("expired_deadline", "error",
                                 f"priority_actions[{i}]",
                                 f"期限 {_due.isoformat()} 已过期"))
    for i, it in enumerate(brief.get("watch_items") or []):
        check_item("watch_items", i, it, "fact_ids",
                   ("trigger", "business_relevance"), True)

    # 自相矛盾：同一事实既被标为"未采用"，又出现在正文引用里
    _used_ids = set()
    for _sec, _key in (("signals", "fact_ids"), ("competitor_cards", "fact_ids"),
                       ("priority_actions", "linked_fact_ids"),
                       ("watch_items", "fact_ids")):
        for _it in brief.get(_sec) or []:
            for _f in (_it.get(_key) or []):
                _used_ids.add(_f)
    for i, it in enumerate(brief.get("omitted_fact_ids") or []):
        fid = it.get("fact_id") or ""
        if fid and fid not in facts:
            errors.append(_issue("unknown_fact_id", "error",
                                 f"omitted_fact_ids[{i}]", f"不存在: {fid}"))
        if not it.get("reason"):
            warnings.append(_issue("missing_reason", "warning",
                                   f"omitted_fact_ids[{i}]", "缺少删除理由"))
        if fid and fid in _used_ids:
            errors.append(_issue("omitted_but_used", "error",
                                 f"omitted_fact_ids[{i}]",
                                 f"{fid} 已标未采用，却仍被正文引用"))

    # 2026-10-31：10 轮稳定性中 27 条 major 全部指向同一个问题——
    # 事实只把屈臣氏列为"并列提及的商家名单"，正文却写成屈臣氏"参与/试点/合作"。
    # 这是最高频的语义升格，必须程序化拦截（P09 每轮都要重复指出的显然问题）。
    # 只拦"把屈臣氏当动作主语"的升格，例如：
    #   "屈臣氏参与直播工具试点" / "屈臣氏首批接入"
    # 不拦：
    #   "与丝芙兰、屈臣氏等商家试点合作"（原文照抄，屈臣氏是并列宾语）
    #   "先核实屈臣氏在试点中的角色"（谨慎表述）
    _watsons_bad = re.compile(
        r"屈臣氏(?:也|将|已|则)?(?:正式)?(?:参与|接入|上线|首批|独家|独家参与)")
    _watsons_ok = re.compile(
        r"(?:核实|确认|未明确|并未|名单|被提及|等(?:美妆)?(?:零售)?商家)")
    for _sec, _fields in (("title", ("title",)),
                          ("one_line_takeaway", ("one_line_takeaway",)),
                          ("signals", ("headline", "fact", "business_impact")),
                          ("competitor_cards", ("move", "why_matters")),
                          ("priority_actions", ("title", "precondition", "trial", "metric")),
                          ("watch_items", ("trigger", "business_relevance"))):
        for _i, _it in enumerate(brief.get(_sec) or []):
            if _sec in ("title", "one_line_takeaway"):
                _it, _i = {"text": brief.get(_sec, "")}, None
                _where = _sec
            else:
                _where = f"{_sec}[{_i}]"
            for _fld in _fields:
                _txt = str(_it.get(_fld, "") if isinstance(_it, dict) else "")
                if "屈臣氏" in _txt and _watsons_bad.search(_txt) and not _watsons_ok.search(_txt):
                    errors.append(_issue("watsons_role_upgraded", "error", _where,
                                         f"屈臣氏被写成参与方，原文仅并列提及：{_txt[:30]}"))

    lead = _texts(brief, ("title", "one_line_takeaway"))
    for pat, code in FORBIDDEN:
        if re.search(pat, lead):
            errors.append(_issue(code, "error", "title/takeaway", f"匹配 {pat}"))
    if len(numbers(brief.get("one_line_takeaway", ""))) > 1:
        warnings.append(_issue("too_many_numbers", "warning", "one_line_takeaway",
                               "结论里数字过多，可读性差"))

    audit = brief.get("audit") or {}
    if audit.get("source_pack_sha256") != pack.get("pack_sha256"):
        errors.append(_issue("audit_sha_mismatch", "error", "audit.source_pack_sha256",
                             "事实包指纹不匹配（模型不得编造）"))
    if str(audit.get("human_review_status")) in ("approved", "verified", "human_verified"):
        errors.append(_issue("fake_human_approval", "error",
                             "audit.human_review_status",
                             "模型/程序不得声称人工已核"))

    return {"pass": not errors, "errors": errors, "warnings": warnings,
            "stats": {"signals": len(brief.get("signals") or []),
                      "competitors": len(brief.get("competitor_cards") or []),
                      "actions": len(brief.get("priority_actions") or []),
                      "watch": len(brief.get("watch_items") or []),
                      "omitted": len(brief.get("omitted_fact_ids") or [])}}


def apply_replacements(brief: Dict, replacements: List[Dict]) -> Tuple[Dict, List[str]]:
    """按 P09 的 suggested_replacements 做最小定点替换（路径如 signals[0].fact）。"""
    import copy

    out = copy.deepcopy(brief)
    applied, failed = [], []
    for rep in replacements or []:
        path = str(rep.get("path") or "")
        text = rep.get("replacement_text")
        top = re.match(r"^([a-zA-Z_]+)$", path)
        if top and isinstance(out.get(top.group(1)), str) and text is not None:
            out[top.group(1)] = text
            applied.append(path)
            continue
        m = re.match(r"([a-zA-Z_]+)\[(\d+)\]\.([a-zA-Z_]+)$", path)
        if not m or text is None:
            failed.append(path)
            continue
        section, idx, field = m.group(1), int(m.group(2)), m.group(3)
        items = out.get(section)
        if not isinstance(items, list) or idx >= len(items):
            failed.append(path)
            continue
        items[idx][field] = text
        applied.append(path)
    return out, applied
