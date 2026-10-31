#!/usr/bin/env python3
"""facts_pack.py — P08/P09 的输入：可信事实包（唯一事实来源）。

相较 v1，按 docs/16 §2 要求补齐每条事实的字段：
- claim_id：程序分配（CLnnn），用于"主张级"事实审稿与跨期溯源；
- source_url / source_kind（official|media|unknown）/ date_role（published|occurred）；
- numbers：从原文句子抽出的数字与单位，供数字绑定核对；
- event_ids：来自事件池的 canonical_event_id（可为空，程序不臆造）。

事实一律来自"有原文句子 + 有来源链接"的记录；缺失即不入包。
"""

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COMPLETED = ("已完成", "已签署", "已上线", "已开卖", "已开业", "已关停",
             "已生效", "已落地", "正式开卖", "正式上线", "已交割", "已完成收购")
ANNOUNCED = ("宣布", "官宣", "发布", "公布", "启动")
PROPOSED = ("拟", "计划", "将", "筹备", "传闻", "据传", "有望", "接洽", "意向")
REPORTED_MARK = ("据报道", "据媒体", "消息称", "据透露", "据报道称")

STATUS_LABEL = {"completed": "已发生", "announced": "当事方宣布",
                "proposed": "拟推进/未落地", "reported": "媒体披露"}

OFFICIAL_HINTS = ("政府", "商务部", "市场监管", "总局", "官方", "公告",
                  "gov.cn", "政府网")
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万亿|亿|万|千|%|％|家|个|款|天|小时|分钟|元|倍)?")


def infer_status(text: str) -> str:
    """从文本推断事件状态。

    2026-10-31 修正（P0）：关键词**不再直接判定 completed**。
    原因：正文出现"已上线/已开卖"可能出自：(a) 记者转述第三方口径，
    (b) 指代另一件事，(c) 引用历史，此时事件未必真的完成。
    现在规则：
      1) 出现传闻标记 → reported（最低置信）
      2) 出现"拟/计划/将/筹备"等 → proposed（未落地优先，压过完成的词）
      3) 出现"宣布/发布"类 → announced（当事方表态，不等于已执行）
      4) 才轮到"已完成/已上线"类 → completed
      5) 兜底 reported
    即：proposed > announced > completed，避免"拟上线"被读成"已上线"。
    真正的 completed 仍需来源证据（官方公告/明确完成时点），
    由 schema 的 status_overclaim 与人工复核把关。
    """
    t = text or ""
    if any(kw in t for kw in REPORTED_MARK):
        return "reported"
    if any(kw in t for kw in PROPOSED):
        return "proposed"
    if any(kw in t for kw in ANNOUNCED):
        return "announced"
    if any(kw in t for kw in COMPLETED):
        return "completed"
    return "reported"


def infer_source_kind(name: str, url: str) -> str:
    blob = f"{name} {url}".lower()
    for kw in OFFICIAL_HINTS:
        if kw.lower() in blob:
            return "official"
    return "media" if url else "unknown"


def extract_numbers(text: str) -> List[str]:
    out = []
    for m in NUM_RE.finditer(text or ""):
        num = m.group(1).replace(",", "")
        out.append(num + (m.group(2) or ""))
    return out


def _clean(text: str, limit: int = 240) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"\bE\d{8}_\d{3,4}\b", "", t)
    # 只清洗真正的标签形态：【A】/\[B\]/(K)/独立的 A B K 词，绝不拆品牌名
    t = re.sub(r"(?:【\s*[ABK][^】]{0,12}】|\[\s*[ABK]\s*\]|\(\s*[ABK]\s*\)|\b[A-BK]\b)"
               r"|\bP[012]\b|置信度\s*[:：]?\s*[\w.]+", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ，,；;")
    return t[:limit]


def _date_of(value: str) -> Optional[date]:
    """解析 ISO / 中文 / RFC822 三种日期；解析不出返回 None（绝不截断字符串当日期）。"""
    raw = str(value or "")
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt:
            return dt.date()
    except Exception:
        pass
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(20\d{2})", raw)
    if m:
        months = {name: i for i, name in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
             "oct", "nov", "dec"], start=1)}
        mon = months.get(m.group(2).lower())
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(1)))
            except ValueError:
                return None
    return None


def _participation_level(text: str) -> str:
    """判断文中主体是"被并列提及"还是"确有动作"。"""
    import re as _re
    if _re.search(r"[、,，]等[^。；]{0,12}(?:商家|企业|品牌|零售商|平台)", text or ""):
        return "listed_only"      # 出现在举报名单里，动作主体是别人
    if _re.search(r"(?:与|同)[^、。；]{1,12}、[^、。；]{1,12}等", text or ""):
        return "listed_only"
    return "direct"


def _add_fact(facts: List[Dict], text: str, day: str, url: str, name: str,
              platforms=None, competitors=None, event_ids=None,
              event_type: str = "", date_role: str = "published") -> None:
    clean = _clean(text)
    if not clean:
        return
    parsed = _date_of(day)
    facts.append({
        "fact_id": f"F{len(facts) + 1:03d}",
        "claim_id": f"CL{len(facts) + 1:03d}",
        "text": clean,
        "date": parsed.isoformat() if parsed else "",
        "date_role": date_role,
        "status": infer_status(clean),
        # 主体参与度：并列列举("与A、B、C等合作")只代表"被提及在名单中"，
        # 不代表每个主体都实际执行该动作。给编辑端一个显式字段，
        # 避免模型凭语感把"被提及"升格成"参与"（10 轮测试中 27 条 major 同源于此）。
        "mentions": [n for n in ("屈臣氏",) if n in clean],
        "participation": _participation_level(clean),
        "time_scope": _time_scope_flags(clean),
        "numbers": extract_numbers(clean),
        "platforms": platforms or [],
        "competitors": competitors or [],
        "event_ids": event_ids or [],
        "event_type": event_type,
        "source": {"name": name, "url": url,
                   "kind": infer_source_kind(name, url)},
    })


# 可当作"事件真实发生日"的时间精度白名单。
# 2026-10-31 实测：上游把研报估算写成 event_occurred_at=2026-10-30 但 precision=quarter，
# 把 2025-08 旧闻写成 2025-08-01 但 precision=unknown。
# 这类值一旦当 occurred 用，会同时造成"旧闻混入"和"竞对发生日无法绑定"两个问题。
PRECISE_DAY_LEVELS = {"day", "date", "exact", "published"}


def _occurred_index(events_dir: str) -> Dict[str, str]:
    """建立 event_id -> event_occurred_at 索引（仅收录可解析日期）。

    2026-10-31：此前 build_from_synthesis 只有 cluster 的 first_seen/last_seen
    （采集时间），导致全部事实被标 observed，竞对卡片 occurred_on 一律无法绑定，
    10 轮稳定性测试中 8 轮被 occurred_date_unbound 硬门拦下。
    真实事件库中约 60% 事件自带 event_occurred_at，应当用上。
    """
    idx: Dict[str, str] = {}
    if not events_dir:
        return idx
    for p in sorted(Path(events_dir).glob("*/events_analyzed.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ev in data.get("events") or []:
            eid = ev.get("event_id")
            when = ev.get("event_occurred_at") or ev.get("occurred_at") or ""
            precision = str(ev.get("event_time_precision") or "").lower()
            conf = str(ev.get("event_time_confidence") or "").lower()
            if not eid or not _date_of(when):
                continue
            # 精度不足（quarter/month/year/unknown）或低置信，不得当作发生日
            if precision and precision not in PRECISE_DAY_LEVELS:
                continue
            if conf == "low":
                continue
            idx[eid] = when
    return idx


def build_from_synthesis(synth: Dict, period_type: str = "weekly",
                         max_facts: int = 80,
                         events_dir: str = "") -> Dict:
    facts: List[Dict] = []
    seen = set()
    for c in synth.get("all_clusters") or []:
        urls = c.get("source_urls") or []
        names = c.get("sources") or []
        texts = c.get("facts") or ([c["theme"]] if c.get("theme") else [])
        eids = c.get("event_ids") or []
        # 事件发生时间优先：cluster 的 first/last_seen 只是采集时间
        _occ = _occurred_index(events_dir) if events_dir else {}
        _occ_when = next((_occ[e] for e in eids if e in _occ), "")
        # 周期窗口：事实的"发生日/发文日"必须落在本期内，否则是旧闻混入。
        # 2026-10-31 实测：W43 事实包曾混入 2025-08、2022-11 旧闻与 10-18 区外内容，
        # 导致竞对卡片 occurred_on 无法绑定、并污染正文。
        # 只用"真实事件发生日"做窗口判定；没有发生日时**不做过滤**
        # （采集时间 last_seen 是抓取时刻，不能当作事件日期，否则会误杀全量）。
        _p0 = _date_of(str((synth.get("period") or {}).get("start") or ""))
        _p1 = _date_of(str((synth.get("period") or {}).get("end") or ""))
        _when = _date_of(_occ_when) if _occ_when else None
        if _p0 and _p1 and _when and not (_p0 <= _when <= _p1):
            continue
        # 文本层面已明确落在周期之后（如"截止11月11日"）→ 不进本期事实包
        _first_txt = (texts[0] if texts else "") or ""
        if _time_scope_flags(_first_txt, str((synth.get("period") or {}).get("end") or "")
                             ).get("beyond_period"):
            continue
        for i, ftxt in enumerate(texts):
            clean = _clean(ftxt)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            _add_fact(facts, ftxt,
                      _occ_when or c.get("last_seen_at") or c.get("first_seen_at") or "",
                      urls[i] if i < len(urls) else (urls[0] if urls else ""),
                      names[i] if i < len(names) else (names[0] if names else ""),
                      c.get("platforms"), c.get("competitors"), eids,
                      (c.get("event_types") or [""])[0],
                      date_role="occurred" if _occ_when else "observed")
            # Cluster first_seen/last_seen are collection times, never
            # evidence of article publication or event occurrence.
            if len(facts) >= max_facts:
                break
        if len(facts) >= max_facts:
            break
    facts = _dedupe_facts(facts)
    return _pack(facts, period_type, synth.get("period") or {},
                 {"facts_kept": len(facts)})


def occurrences_ok(ev: Dict) -> bool:
    """仅当事件自带可解析的 occurred/occurred_at 时才把日期角色标成 occurred。"""
    return bool(_date_of(ev.get("event_occurred_at") or ev.get("occurred_at")
                         or ""))


def build_from_events(events_dir: str, start: str, end: str,
                      period_type: str = "monthly", max_facts: int = 120) -> Dict:
    d0, d1 = _date_of(start), _date_of(end)
    raw = []
    days = sorted((p for p in Path(events_dir).glob("*") if p.is_dir()
                   and _date_of(p.name)), key=lambda p: p.name)
    for day in days:
        dd = _date_of(day.name)
        if not dd or not d0 or not d1 or not (d0 <= dd <= d1):
            continue
        f = day / "events_analyzed.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("events", [])
        for ev in items:
            if ev.get("reject"):
                continue
            raw.append((float(ev.get("weighted_score") or 0), day.name, ev))
    raw.sort(key=lambda x: -x[0])

    facts: List[Dict] = []
    seen = set()
    for score, day_name, ev in raw:
        text = ev.get("fact") or ev.get("event_title") or ""
        key = re.sub(r"\W+", "", _clean(text))[:40]
        if not key or key in seen:
            continue
        seen.add(key)
        ents = ev.get("entities") or {}
        event_at = ev.get("event_occurred_at") or ev.get("occurred_at")
        published_at = ev.get("published_at") or ev.get("article_published_at")
        if occurrences_ok(ev):
            when, role = event_at, "occurred"
        elif _date_of(published_at or ""):
            when, role = published_at, "published"
        else:
            # Directory date only establishes when the pipeline saw the item.
            when, role = day_name, "observed"
        _add_fact(facts, text, when, ev.get("source_url") or "",
                  ev.get("source_name") or "",
                  (ents.get("platforms") or [])[:3],
                  (ents.get("companies") or [])[:3],
                  [ev.get("event_id")] if ev.get("event_id") else [],
                  ev.get("event_type") or "", date_role=role)
        if len(facts) >= max_facts:
            break
    facts = _dedupe_facts(facts)
    return _pack(facts, period_type, {"start": start, "end": end},
                 {"days_scanned": len(days), "facts_kept": len(facts)})


# 周期外/模糊时间信号。
# 2026-10-31 实测：W43 的 P09 阻断 90% 集中在"事实本身时间不属于本周期"——
#   "截止11月11日，单量超8万"（事件在本期之后）
#   "双11期间上线直播工具"（双11在11月，非10月19-25）
#   "同比去年增长1000%"（未给统计区间）
# 事实在本周被报道 ≠ 事实描述的事件发生在本周，必须显式区分。
_FUTURE_WINDOW = re.compile(r"(?:截止|截至|预计|将于)?\s*(1[12]月\d{0,2}日?)")
_VAGUE_GROWTH = re.compile(r"同比(?:去年)?(?:增长|下降|提升)\s*\d+(?:\.\d+)?%?")
_CAMPAIGN_ONLY = re.compile(r"(双11|双十一|11\.11|618|双12)")


def _time_scope_flags(text: str, period_end: str = "") -> Dict[str, bool]:
    """标记事实里的时间问题，供编辑端避坑。"""
    t = text or ""
    flags = {"has_future_date": bool(_FUTURE_WINDOW.search(t)),
             "has_vague_growth": bool(_VAGUE_GROWTH.search(t)),
             "has_campaign_ref": bool(_CAMPAIGN_ONLY.search(t))}
    # 形如"截止11月11日"且周期在 10 月 → 明确是周期外
    m = _FUTURE_WINDOW.search(t)
    if m and period_end[:7] in ("2026-10", "2026-09"):
        flags["beyond_period"] = True
    else:
        flags["beyond_period"] = flags["has_future_date"]
    return flags


def _dedupe_facts(facts: List[Dict], threshold: float = 0.92) -> List[Dict]:
    """事实级近似去重。

    2026-10-31 实测：W43 事实包 17 条中有 7 组相似度 0.83~0.99 的重复摘录
    （同一媒体段落被拆成两三条），导致 P09 反复告警"证据重复挂载"，
    10 轮稳定性中 3 轮因此被阻断。去重保留首条，重编 fact_id/claim_id。
    """
    import difflib
    kept: List[Dict] = []
    for f in facts:
        t = f.get("text") or ""
        dup = False
        for k in kept:
            if difflib.SequenceMatcher(None, t, k.get("text") or "").ratio() >= threshold:
                dup = True
                break
        if not dup:
            kept.append(dict(f))
    for i, f in enumerate(kept, 1):
        f["fact_id"] = f"F{i:03d}"
        f["claim_id"] = f"CL{i:03d}"
    return kept


def _pack(facts: List[Dict], period_type: str, period: Dict,
          coverage: Dict) -> Dict:
    blob = json.dumps([{k: f[k] for k in ("claim_id", "text", "date", "status",
                                          "source")} for f in facts],
                      ensure_ascii=False, sort_keys=True)
    return {"period_type": period_type, "period": period,
            "coverage": coverage, "facts": facts,
            "pack_sha256": hashlib.sha256(blob.encode()).hexdigest()}


def fact_index(pack: Dict) -> Dict[str, Dict]:
    return {f["fact_id"]: f for f in pack.get("facts", [])}


def claim_index(pack: Dict) -> Dict[str, Dict]:
    return {f["claim_id"]: f for f in pack.get("facts", [])}


def reader_fact_pack_json(pack: Dict, limit: int = 60) -> List[Dict]:
    """P08 的 {reader_fact_pack_json}：只给编辑需要、且都可核对的字段。"""
    out = []
    for f in pack.get("facts", [])[:limit]:
        out.append({
            "fact_id": f["fact_id"], "claim_id": f["claim_id"],
            "text": f["text"], "date": f["date"], "date_role": f["date_role"],
            "status": f["status"], "numbers": f["numbers"],
            "source_url": f["source"]["url"],
            "source_kind": f["source"]["kind"],
            "source_name": f["source"]["name"],
        })
    return out
