#!/usr/bin/env python3
"""cross_period.py — 月报 P14 / 年报 P15 的跨周、跨月分级汇总。

硬性要求（docs/16 §2）：
- 月报不是"大周报"：先按自然月取相交 ISO 周（4～5 个），逐周检查是否已封账；
  缺周/未封账/来源缺口必须在 gate 里显式反映，不因"凑够4份"就强行出月报。
- 年报只用 12 份已封账月报；12 月未封账时不得在 1 月 1 日假装完成全年。
- 每次只调用**一次** LLM（P14 或 P15），一次输出 *_synthesis.json；
  缓存键 = prompt_version + model + 输入事实/报告指纹。
- 综合结果必须保留逐层溯源链，模型不得自行计数、对齐日期或编造 ID。
"""

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from skills.utils.llm_client import get_llm_client
from skills.utils.prompt_registry import render

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/cache/cross_period")
STATUSES = ("complete", "degraded", "incomplete")


# ---------- 日期工具 ----------

def iso_weeks_of_month(year: int, month: int) -> List[str]:
    d = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    weeks = []
    while d < end:
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        if wk not in weeks:
            weeks.append(wk)
        d += timedelta(days=1)
    return weeks


def month_closed(year: int, month: int, as_of: date) -> bool:
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return as_of >= end + timedelta(days=1)


# ---------- 报告收集 ----------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def collect_weekly(root: Path, weeks: List[str]) -> Tuple[List[Dict], List[str]]:
    found, missing = [], []
    for wk in weeks:
        year = wk.split("-")[0]
        path = root / "reports" / "weekly" / year / f"{wk}.md"
        text = _read(path)
        if not text.strip():
            missing.append(wk)
            continue
        heads = re.findall(r"^#{2,3}\s+(.+)$", text, re.M)[:6]
        found.append({"week": wk, "path": str(path), "sha": _sha(text),
                      "chars": len(text), "headings": heads,
                      "excerpt": re.sub(r"\s+", " ", text)[:600]})
    return found, missing


def collect_monthly(root: Path, year: int, months: List[int]) -> Tuple[List[Dict], List[int]]:
    found, missing = [], []
    for m in months:
        path = root / "reports" / "monthly" / str(year) / f"{year}-{m:02d}.md"
        text = _read(path)
        if not text.strip():
            missing.append(m)
            continue
        found.append({"month": f"{year}-{m:02d}", "path": str(path),
                      "sha": _sha(text), "chars": len(text),
                      "excerpt": re.sub(r"\s+", " ", text)[:600]})
    return found, missing


def _event_claims(root: Path, start: date, end: date, limit: int = 60) -> List[Dict]:
    """周/月报告背后的 canonical 事件索引（溯源用，程序读取，不让模型生成）。"""
    out = []
    for day in sorted((root / "data" / "events").glob("2026-*")):
        try:
            d = date.fromisoformat(day.name)
        except ValueError:
            continue
        if not (start <= d <= end):
            continue
        f = day / "events_analyzed.json"
        if not f.exists():
            continue
        try:
            data = json.loads(_read(f))
        except Exception:
            continue
        for ev in (data if isinstance(data, list) else data.get("events", [])):
            out.append({"date": day.name, "event_id": ev.get("event_id"),
                        "title": (ev.get("event_title") or ev.get("fact") or "")[:120],
                        "source_url": ev.get("source_url") or ""})
            if len(out) >= limit:
                return out
    return out


# ---------- P14 / P15 ----------

def _cache_get(key: str) -> Optional[Dict]:
    p = CACHE_DIR / f"{key}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_put(key: str, data: Dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def monthly_synthesis(root: Path, year: int, month: int, send_at: str,
                      call_llm: bool = True) -> Dict:
    weeks = iso_weeks_of_month(year, month)
    found, missing = collect_weekly(root, weeks)
    as_of = date.fromisoformat(send_at)
    start = date(year, month, 1)
    end = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1))
    last_closed = month_closed(year, month, as_of)

    gate = {"expected_weeks": weeks, "found_weeks": [w["week"] for w in found],
            "missing_weeks": missing, "month_closed": last_closed}
    if missing or not last_closed:
        status = "incomplete" if len(found) < 3 else "degraded"
        if not last_closed and len(found) == len(weeks):
            status = "degraded"
    else:
        status = "complete"
    gate["status"] = status

    payload = {
        "period_id": f"{year}-{month:02d}",
        "month_start": start.isoformat(), "month_end": end.isoformat(),
        "as_of_period_end": send_at,
        "weekly_reports_json": found,
        "weekly_report_manifest_json": {
            "expected": weeks, "complete": not missing,
            "sha_by_week": {w["week"]: w["sha"] for w in found}},
        "monthly_canonical_fact_pack_json": _event_claims(root, start, end),
        "previous_month_comparison_json": "无同口径上月封账数据时留空，不得编造环比",
        "weekly_published_fingerprints_json": [],
        "coverage_and_integrity_json": gate,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return _run_cross("P14", payload, gate, "monthly_synthesis",
                      f"{year}-{month:02d}", call_llm)


def yearly_synthesis(root: Path, year: int, send_at: str,
                     call_llm: bool = True) -> Dict:
    as_of = date.fromisoformat(send_at)
    months = list(range(1, 13))
    found, missing = collect_monthly(root, year, months)
    closed = [m for m in months if month_closed(year, m, as_of)]
    unclosed = [m for m in months if m not in closed]
    gate = {"expected_months": months,
            "found_months": [m["month"] for m in found],
            "missing_months": missing, "unclosed_months": unclosed,
            "month_coverage": f"{len(found)}/12"}
    if missing or unclosed:
        status = "incomplete" if len(found) < 6 else "degraded"
    else:
        status = "complete"
    gate["status"] = status

    payload = {
        "year": year, "year_start": f"{year}-01-01", "year_end": f"{year}-12-31",
        "as_of_year_end": send_at,
        "twelve_monthly_reports_json": found,
        "monthly_report_manifest_json": {"missing": missing,
                                         "unclosed": unclosed,
                                         "sha_by_month": {m["month"]: m["sha"] for m in found}},
        "monthly_synthesis_and_lineage_json": [],
        "quarterly_and_year_comparable_metrics_json":
            "无同口径季度数据时留空，不得把不同统计口径相加",
        "comparison_and_coverage_json": gate,
        "verified_internal_business_context_json": {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return _run_cross("P15", payload, gate, "yearly_synthesis", str(year), call_llm)


def _run_cross(pid: str, payload: Dict, gate: Dict, out_kind: str,
               period_id: str, call_llm: bool) -> Dict:
    if gate["status"] == "incomplete":
        logger.warning("%s 来源门 incomplete：不调用 LLM，返回缺口报告", pid)
        return {"status": "incomplete", "gate": gate, "synthesis": None,
                "_meta": {"prompt": pid, "llm_called": False,
                          "reason": "缺周/缺月或未封账，禁止假装完整期间"}}
    pr = render(pid, **payload)
    import skills.reader_brief.schema as _  # noqa: F401  (保持依赖显式)
    key = f"{pid}:{pr.version}:{hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]}"
    cached = _cache_get(key)
    if cached:
        cached["_meta"]["cache_hit"] = True
        return cached
    if not call_llm:
        return {"status": gate["status"], "gate": gate, "synthesis": None,
                "_meta": {"prompt": pid, "llm_called": False}}
    client = get_llm_client()
    res = client.chat(messages=[{"role": "user", "content": pr.user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=pr.temperature, max_tokens=pr.max_tokens)
    synth = res.get("parsed") if res.get("ok") else None
    meta = {"prompt": pid, "prompt_version": pr.version,
            "model": res.get("model"), "llm_called": True,
            "cache_key": key, "ok": bool(synth)}
    if not isinstance(synth, dict):
        return {"status": "incomplete", "gate": gate, "synthesis": None,
                "_meta": dict(meta, error=(res.get("error") or "")[:200])}
    synth = _attach_lineage(synth, payload, gate)
    out = {"status": gate["status"], "gate": gate, "synthesis": synth,
           "_meta": meta}
    _cache_put(key, out)
    return out


def _attach_lineage(synth: Dict, payload: Dict, gate: Dict) -> Dict:
    """给综合包补上程序事实：（周期、门禁状态、来源报告指纹、原始claim索引）。"""
    synth["_lineage"] = {
        "source_reports": {k: v for k, v in payload.items()
                           if k.endswith("_json") and isinstance(v, list)},
        "coverage": gate,
        "generated_at": payload.get("generated_at"),
    }
    synth.setdefault("data_gaps", []).extend(gate.get("missing_weeks") or gate.get("missing_months") or [])
    return synth


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["monthly", "yearly"], required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int)
    ap.add_argument("--send-at", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--out")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.kind == "monthly":
        res = monthly_synthesis(root, a.year, a.month, a.send_at, not a.no_llm)
    else:
        res = yearly_synthesis(root, a.year, a.send_at, not a.no_llm)
    text = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({"status": res["status"], "gate": res["gate"],
                      "_meta": res["_meta"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
