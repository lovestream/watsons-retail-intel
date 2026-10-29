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
CAPS = {"signals": 3, "competitor_cards": 2, "priority_actions": 3,
        "watch_items": 2, "omitted_fact_ids": 12}
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


def validate_brief(brief: Dict, pack: Dict, send_at: str) -> Dict:
    facts = {f["fact_id"]: f for f in pack.get("facts", [])}
    claims = {f["claim_id"]: f for f in pack.get("facts", [])}
    pack_urls = {f["source"]["url"] for f in pack.get("facts", [])}
    today = date.fromisoformat(send_at)
    errors: List[Dict] = []
    warnings: List[Dict] = []

    for key in REQUIRED:
        if key not in brief:
            errors.append(_issue("missing_field", "error", key, "缺少必填字段"))
    for key, cap in CAPS.items():
        items = brief.get(key) or []
        if not isinstance(items, list):
            errors.append(_issue("bad_type", "error", key, "应为数组"))
            continue
        if len(items) > cap:
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
        cids = item.get("claim_ids") or []
        if cids:
            badc = [x for x in cids if x not in claims]
            if badc:
                errors.append(_issue("unknown_claim_id", "error", where, f"不存在: {badc}"))
        # 来源链接必须真实存在且属于所引用事实
        own_urls = {facts[x]["source"]["url"] for x in ids}
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
            if sup and any(x not in (item.get("claim_ids") or []) for x in sup):
                warnings.append(_issue("supports_unbound", "warning",
                                       f"{where}.source_refs[{j}]",
                                       "supports_claim_ids 不在本条 claim_ids 中"))

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
        if statuses <= {"proposed", "reported"} and re.search(OVERCLAIM, joined):
            errors.append(_issue("status_overclaim", "error", where,
                                 "事实为拟推进/媒体披露，正文却写成已落地"))
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
    for i, it in enumerate(brief.get("watch_items") or []):
        check_item("watch_items", i, it, "fact_ids",
                   ("trigger", "business_relevance"), True)

    for i, it in enumerate(brief.get("omitted_fact_ids") or []):
        fid = it.get("fact_id") or ""
        if fid and fid not in facts:
            errors.append(_issue("unknown_fact_id", "error",
                                 f"omitted_fact_ids[{i}]", f"不存在: {fid}"))
        if not it.get("reason"):
            warnings.append(_issue("missing_reason", "warning",
                                   f"omitted_fact_ids[{i}]", "缺少删除理由"))

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
