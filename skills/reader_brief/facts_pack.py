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
    t = text or ""
    for kw in COMPLETED:
        if kw in t:
            return "completed"
    for kw in REPORTED_MARK:
        if kw in t:
            return "reported"
    for kw in ANNOUNCED:
        if kw in t:
            return "announced"
    for kw in PROPOSED:
        if kw in t:
            return "proposed"
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
    t = re.sub(r"【?[ABK]】?|\bP[012]\b|置信度\s*[:：]?\s*[\w.]+", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ，,；;")
    return t[:limit]


def _date_of(value: str) -> Optional[date]:
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", str(value or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _add_fact(facts: List[Dict], text: str, day: str, url: str, name: str,
              platforms=None, competitors=None, event_ids=None,
              event_type: str = "") -> None:
    clean = _clean(text)
    if not clean:
        return
    facts.append({
        "fact_id": f"F{len(facts) + 1:03d}",
        "claim_id": f"CL{len(facts) + 1:03d}",
        "text": clean,
        "date": (day or "")[:10],
        "date_role": "published",
        "status": infer_status(clean),
        "numbers": extract_numbers(clean),
        "platforms": platforms or [],
        "competitors": competitors or [],
        "event_ids": event_ids or [],
        "event_type": event_type,
        "source": {"name": name, "url": url,
                   "kind": infer_source_kind(name, url)},
    })


def build_from_synthesis(synth: Dict, period_type: str = "weekly",
                         max_facts: int = 80) -> Dict:
    facts: List[Dict] = []
    seen = set()
    for c in synth.get("all_clusters") or []:
        urls = c.get("source_urls") or []
        names = c.get("sources") or []
        texts = c.get("facts") or ([c["theme"]] if c.get("theme") else [])
        eids = c.get("event_ids") or []
        for i, ftxt in enumerate(texts):
            clean = _clean(ftxt)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            _add_fact(facts, ftxt,
                      c.get("last_seen_at") or c.get("first_seen_at") or "",
                      urls[i] if i < len(urls) else (urls[0] if urls else ""),
                      names[i] if i < len(names) else (names[0] if names else ""),
                      c.get("platforms"), c.get("competitors"), eids,
                      (c.get("event_types") or [""])[0])
            if len(facts) >= max_facts:
                break
        if len(facts) >= max_facts:
            break
    return _pack(facts, period_type, synth.get("period") or {},
                 {"facts_kept": len(facts)})


def build_from_events(events_dir: str, start: str, end: str,
                      period_type: str = "monthly", max_facts: int = 120) -> Dict:
    d0, d1 = _date_of(start), _date_of(end)
    raw = []
    days = sorted(p for p in Path(events_dir).glob("2026-*") if p.is_dir())
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
        _add_fact(facts, text, day_name, ev.get("source_url") or "",
                  ev.get("source_name") or "",
                  (ents.get("platforms") or [])[:3],
                  (ents.get("companies") or [])[:3],
                  [ev.get("event_id")] if ev.get("event_id") else [],
                  ev.get("event_type") or "")
        if len(facts) >= max_facts:
            break
    return _pack(facts, period_type, {"start": start, "end": end},
                 {"days_scanned": len(days), "facts_kept": len(facts)})


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
