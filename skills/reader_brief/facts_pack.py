#!/usr/bin/env python3
"""facts_pack.py — 构建"可信事实包"（reader brief 的唯一事实来源）。

设计原则（对应 docs/15）：
1. 编辑层只能看到事实包，看不到长报告，因此不会"改写"报告里的结论；
2. 每条事实都带原文句子、来源链接、日期、状态词，缺失即不入包；
3. 事实包不含评分/优先级/置信度（避免编辑层复述内部信号）。

两种构建方式：
- from_synthesis: 周期合成结果 periodic_synthesis.json（clusters 里已有 facts/source_urls）
- from_events:    事件池 events_analyzed.json（月度/年度按日期区间聚合）
"""

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------- 状态判定

_COMPLETED = ("已完成", "已签署", "已上线", "已开卖", "已开业", "已关停",
              "已生效", "已落地", "正式开卖", "正式上线")
_ANNOUNCED = ("宣布", "官宣", "发布", "公布", "启动")
_PROPOSED = ("拟", "计划", "将", "筹备", "传闻", "据传", "有望")


REPORTED_MARK = ("据报道", "据媒体", "消息称", "据透露", "据报道称")


def infer_status(text: str) -> str:
    """把一句事实归到 4 个状态之一：completed/announced/proposed/reported。

    顺序很重要：先看"已发生"，再看"媒体披露"前缀，再看当事方宣布、拟推进。
    否则一句"据报道…将…"会被误标成拟推进。
    """
    t = text or ""
    for kw in _COMPLETED:
        if kw in t:
            return "completed"
    for kw in REPORTED_MARK:
        if kw in t:
            return "reported"
    for kw in _ANNOUNCED:
        if kw in t:
            return "announced"
    for kw in _PROPOSED:
        if kw in t:
            return "proposed"
    return "reported"


STATUS_LABEL = {
    "completed": "已发生",
    "announced": "当事方宣布",
    "proposed": "拟推进/未落地",
    "reported": "媒体披露",
}


# ---------------------------------------------------------------- 工具函数

def _clean(text: str, limit: int = 220) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    # 去掉内部标记（event_id / 优先级 / 置信度）后再入包
    t = re.sub(r"\bE\d{8}_\d{3,4}\b", "", t)
    t = re.sub(r"【?[ABK]】?|\bP[012]\b|置信度\s*[:：]?\s*[\w.]+", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ，,；;")
    return t[:limit]


def _norm_num(s: str) -> str:
    return s.replace(",", "").replace("，", "").strip()


def _date_of(value: str) -> Optional[date]:
    if not value:
        return None
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", str(value))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------- 合成结果

def from_synthesis(synth: Dict, period_type: str = "weekly",
                   max_facts: int = 80) -> Dict:
    """从 periodic_synthesis.json 构建事实包。"""
    facts: List[Dict] = []
    clusters = synth.get("all_clusters") or []
    for c in clusters:
        urls = c.get("source_urls") or []
        src_names = c.get("sources") or []
        facts_text = c.get("facts") or []
        if not facts_text and c.get("theme"):
            facts_text = [c["theme"]]
        seen = set()
        for i, ftxt in enumerate(facts_text):
            clean = _clean(ftxt)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            url = urls[i] if i < len(urls) else (urls[0] if urls else "")
            name = src_names[i] if i < len(src_names) else (src_names[0] if src_names else "")
            facts.append({
                "fact_id": f"F{len(facts) + 1:03d}",
                "text": clean,
                "date": (c.get("last_seen_at") or c.get("first_seen_at") or "")[:10],
                "status": infer_status(clean),
                "platforms": c.get("platforms") or [],
                "competitors": c.get("competitors") or [],
                "event_type": (c.get("event_types") or [""])[0],
                "variables": c.get("business_variables") or [],
                "source": {"name": name, "url": url},
                "cluster": c.get("theme", ""),
            })
            if len(facts) >= max_facts:
                break
        if len(facts) >= max_facts:
            break

    return {
        "period_type": period_type,
        "period": synth.get("period") or {},
        "coverage": synth.get("coverage") or {},
        "facts": facts,
        "aggregates": _aggregates(synth),
    }


def _aggregates(synth: Dict) -> List[Dict]:
    """渠道/品类/竞对的聚合数字（作为背景，不带 URL，仅允许定性引用）。"""
    out = []
    for key, label in (("channel_summary", "渠道"), ("category_summary", "品类"),
                       ("competitor_summary", "竞对")):
        block = synth.get(key) or {}
        if not isinstance(block, dict):
            continue
        for k, v in list(block.items())[:12]:
            val = v[0] if isinstance(v, list) and v else v
            if isinstance(val, dict):
                val = val.get("count") or val.get("events") or val
            if isinstance(val, (int, float)):
                out.append({"dimension": label, "name": k, "value": val})
    return out


# ---------------------------------------------------------------- 事件池

def from_events(events_dir: str, start: str, end: str,
                period_type: str = "monthly", max_facts: int = 120,
                min_score: float = 0.0) -> Dict:
    """从事件池按日期区间聚合事实包。"""
    d0, d1 = _date_of(start), _date_of(end)
    facts: List[Dict] = []
    seen_text = set()
    days = sorted(p for p in Path(events_dir).glob("2026-*") if p.is_dir())
    raw_events = []
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
            score = float(ev.get("weighted_score") or 0)
            if score < min_score:
                continue
            raw_events.append((score, day.name, ev))

    raw_events.sort(key=lambda x: -x[0])
    for score, day_name, ev in raw_events:
        text = _clean(ev.get("fact") or ev.get("event_title") or "")
        if not text:
            continue
        key = re.sub(r"\W+", "", text)[:40]
        if key in seen_text:
            continue
        seen_text.add(key)
        ents = ev.get("entities") or {}
        facts.append({
            "fact_id": f"F{len(facts) + 1:03d}",
            "text": text,
            "date": day_name,
            "status": infer_status(text),
            "platforms": (ents.get("platforms") or [])[:3],
            "competitors": (ents.get("companies") or [])[:3],
            "event_type": ev.get("event_type") or "",
            "variables": (ev.get("business_variables") or [])[:3],
            "source": {"name": ev.get("source_name") or "",
                       "url": ev.get("source_url") or ""},
            "cluster": "",
        })
        if len(facts) >= max_facts:
            break

    return {
        "period_type": period_type,
        "period": {"start": start, "end": end},
        "coverage": {"days_scanned": len(days), "facts_kept": len(facts)},
        "facts": facts,
        "aggregates": [],
    }


def fact_index(pack: Dict) -> Dict[str, Dict]:
    return {f["fact_id"]: f for f in pack.get("facts", [])}


def compact_pack_for_llm(pack: Dict, max_facts: int = 60) -> Dict:
    """给 LLM 的事实包：只保留编辑需要的最小字段，控制 token。"""
    facts = pack.get("facts", [])[:max_facts]
    return {
        "period_type": pack.get("period_type"),
        "period": pack.get("period"),
        "facts": [
            {"id": f["fact_id"], "date": f["date"], "status": f["status"],
             "text": f["text"],
             "platforms": f["platforms"], "competitors": f["competitors"],
             "source": f["source"]["url"]}
            for f in facts
        ],
    }
