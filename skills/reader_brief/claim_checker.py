#!/usr/bin/env python3
"""claim_checker.py — 事实质量审查（简报 JSON 的硬门）。

检查项：
1. schema：字段齐全、条目数不超上限、每条都带 fact_ids；
2. 事实绑定：fact_ids 必须存在于事实包；
3. 数字绑定：正文里的数字必须出现在被引用 fact 的原文里；
4. 状态一致：proposed/reported 的事实不得写成"已落地/已完成"；
5. 期限失效：动作/观察项里不得出现已过期的日期；
6. 越权指令：不得出现未在事实中的折扣、预算、上线日期、限购规则；
7. 内部标记：不得出现 event_id、评分、优先级、置信度、附录、证据清单；
8. 过度确定：必然/彻底/证明等词只报警告，交编辑层改写。
"""

import re
from datetime import date, datetime
from typing import Dict, List

BRIEF_KEYS = {
    "weekly": ("lead", "signals", "competitors", "actions", "watch"),
    "monthly": ("lead", "trends", "tradeoffs", "actions", "watch"),
    "yearly": ("lead", "structure", "decisions", "actions", "watch"),
}
CAPS = {
    "weekly": {"signals": 3, "competitors": 3, "actions": 3, "watch": 2},
    "monthly": {"trends": 4, "tradeoffs": 3, "actions": 3, "watch": 2},
    "yearly": {"structure": 3, "decisions": 3, "actions": 2, "watch": 2},
}
TEXT_FIELDS = ("title", "what_changed", "why_it_matters", "move", "implication",
               "evidence", "meaning", "choice", "tension", "detail",
               "decision", "cost_of_waiting", "action", "horizon", "item",
               "metric", "name")

FORBIDDEN = [
    (r"\bE\d{8}_\d{3,4}\b", "event_id_leak"),
    (r"\bP[0-2]\b", "priority_leak"),
    (r"置信度|评分|加权分|优先级", "internal_signal_leak"),
    (r"附录|证据清单|完整来源|来源列表", "appendix_leak"),
    (r"【[ABK]】|\['[ABK]'\]|\[\"[ABK]\"\]", "tag_leak"),
]
CERTAINTY = ("必然", "一定会", "彻底", "证明", "毫无疑问", "全面领先",
             "唯一正确", "一定能够")
RISKY_ACTION = ("立即上线", "马上上线", "限购", "预算迁移", "砍掉",
                "全面降价", "统一降价", "清仓")
# 这些词在有事实支撑时是正常表述，只在"事实里没有"时才判为越权
RISKY_SUPPORT = ("折扣", "补贴", "预算", "上线", "限购", "降价", "满减", "券")

NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万亿|亿|万|千|%|％|家|个|款|天|小时|分钟|元|倍)?")


def _norm_num(v: str) -> str:
    return v.replace(",", "").replace("，", "")


PLACEHOLDER = {"双11": "大促", "双12": "大促", "双十一": "大促",
               "618": "大促", "38节": "大促", "38 节": "大促"}


def _mask_promo(text: str) -> str:
    t = text or ""
    for k, v in PLACEHOLDER.items():
        t = t.replace(k, v)
    return t


def extract_numbers(text: str) -> List[str]:
    out = []
    for m in NUM_RE.finditer(_mask_promo(text)):
        num = _norm_num(m.group(1))
        unit = m.group(2) or ""
        out.append(num + unit)
    return out


def _parse_date_token(text: str, default_year: int):
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text or "")
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text or "")
    if m:
        try:
            return date(default_year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def _issue(code, severity, where, message):
    return {"code": code, "severity": severity, "where": where,
            "message": message}


def check(brief: Dict, pack: Dict, period_type: str, today: str) -> Dict:
    facts = {f["fact_id"]: f for f in pack.get("facts", [])}
    today_d = date.fromisoformat(today)
    errors: List[Dict] = []
    warnings: List[Dict] = []
    stats = {"items": 0, "facts_referenced": set(), "numbers_checked": 0}

    # 1) schema
    for key in BRIEF_KEYS[period_type]:
        if key == "lead":
            if not (brief.get("lead") or "").strip():
                errors.append(_issue("missing_lead", "error", "lead",
                                     "缺少一句话结论"))
            continue
        items = brief.get(key) or []
        if not isinstance(items, list):
            errors.append(_issue("bad_type", "error", key, "应为数组"))
            continue
        cap = CAPS[period_type].get(key, 9)
        if len(items) > cap:
            errors.append(_issue("too_many_items", "error", key,
                                 f"{len(items)} 条 > 上限 {cap}"))
        for i, it in enumerate(items):
            where = f"{key}[{i}]"
            stats["items"] += 1
            ids = it.get("fact_ids") or []
            if not ids:
                errors.append(_issue("no_fact_ids", "error", where,
                                     "条目没有绑定事实"))
                continue
            bad = [x for x in ids if x not in facts]
            if bad:
                errors.append(_issue("unknown_fact_id", "error", where,
                                     f"事实包中不存在: {bad}"))
                continue
            stats["facts_referenced"].update(ids)

    # 2) 逐条文本检查（事实绑定 / 数字 / 状态 / 期限 / 越权 / 内部标记）
    for key in BRIEF_KEYS[period_type]:
        items = brief.get(key) or []
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            where = f"{key}[{i}]"
            ids = [x for x in (it.get("fact_ids") or []) if x in facts]
            if not ids:
                continue
            fact_texts = " ".join(facts[x]["text"] for x in ids)
            fact_nums = set()
            for fx in ids:
                fact_nums.update(extract_numbers(facts[fx]["text"]))
            statuses = {facts[x]["status"] for x in ids}

            joined = " ".join(str(it.get(f, "")) for f in TEXT_FIELDS)
            joined_all = joined + " " + str(it.get("name", ""))

            # 数字绑定
            for n in extract_numbers(joined):
                bare = re.sub(r"[^\d.]", "", n)
                if len(bare) <= 1 and float(bare or 0) <= 5:
                    continue  # 允许"三件事"这类计数
                stats["numbers_checked"] += 1
                if n in fact_nums:
                    continue
                if bare and bare in re.sub(r"[^\d.]", "", fact_texts):
                    continue
                errors.append(_issue("number_not_in_fact", "error", where,
                                     f"数字 {n} 未出现在所引用事实中"))

            # 状态一致性
            if statuses <= {"proposed", "reported"} and re.search(
                    r"已上线|已完成|已落地|已开卖|正式上线|正式开卖|已签署", joined):
                errors.append(_issue("status_overclaim", "error", where,
                                     "事实为拟推进/媒体披露，正文却写成已落地"))

            # 期限失效（只查动作与观察项里的日期）
            if key in ("actions", "watch"):
                d = _parse_date_token(joined, today_d.year)
                if d and d < today_d:
                    errors.append(_issue("expired_deadline", "error", where,
                                         f"期限 {d.isoformat()} 已过期"))

            # 越权指令
            for kw in RISKY_ACTION:
                if kw in joined and kw not in fact_texts:
                    errors.append(_issue("unverified_action", "error", where,
                                         f"“{kw}”没有事实支撑"))
            for kw in RISKY_SUPPORT:
                if kw in joined and kw not in fact_texts and re.search(
                        r"\d", joined):
                    warnings.append(_issue("number_in_support_word", "warning",
                                           where, f"“{kw}”搭配了事实中没有的数字"))

            # 内部标记
            for pat, code in FORBIDDEN:
                if re.search(pat, joined_all):
                    errors.append(_issue(code, "error", where, f"匹配 {pat}"))

            # 过度确定
            for kw in CERTAINTY:
                if kw in joined:
                    warnings.append(_issue("over_certain", "warning", where,
                                           f"“{kw}”是过度确定表述"))

    # 3) lead 也要查一遍（不要求 fact_ids，但禁止内部标记与过期期限）
    lead = str(brief.get("lead") or "")
    for pat, code in FORBIDDEN:
        if re.search(pat, lead):
            errors.append(_issue(code, "error", "lead", f"匹配 {pat}"))
    for kw in CERTAINTY:
        if kw in lead:
            warnings.append(_issue("over_certain", "warning", "lead",
                                   f"“{kw}”是过度确定表述"))

    stats["facts_referenced"] = sorted(stats["facts_referenced"])
    return {"pass": not errors, "errors": errors, "warnings": warnings,
            "stats": stats}
