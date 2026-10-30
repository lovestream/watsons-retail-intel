#!/usr/bin/env python3
"""cross_period.py — 月报 P14 / 年报 P15 的跨周、跨月分级汇总（R0/R1 整改版）。

整改要点（docs/reviews/NAS-LLM-PROMPT-INTEGRATION-INDEPENDENT-REVIEW-2026-10-29）：
P0-02 模板变量齐全（含 `send_at`），正例可走完 render；
P0-03 覆盖门读 `data/runs/periodic/{type}/{period_id}/manifest.json` 判定"已封账 +
      质量门通过 + 报告 sha 匹配"，文件存在≠已封账；degraded/incomplete 一律不调 LLM；
P0-04 输入是可追溯结构：周报全文要点 + 本月日级事实切片（含 claim/status/date_role/
      来源），年报带 12 份月报 + 12 份 P14 结果；不硬编码年份；LLM 输出逐条校验 ID/URL，
      未知引用不得静默通过；缓存键含来源 sha + prompt 版本 + 模型。
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
MIN_SOURCE_COVERAGE = 0.6
REPORT_CHARS = 4000
FACT_LIMIT_MONTH = 200
FACT_LIMIT_YEAR = 400


# ---------- 日期工具 ----------

def iso_weeks_of_month(year: int, month: int) -> List[str]:
    d = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    weeks: List[str] = []
    while d < end:
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        if wk not in weeks:
            weeks.append(wk)
        d += timedelta(days=1)
    return weeks


def iso_week_range(week: str) -> Tuple[date, date]:
    y, w = week.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    return monday, monday + timedelta(days=6)


def month_bounds(year: int, month: int) -> Tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return start, end


def month_closed(year: int, month: int, as_of: date) -> bool:
    _, end = month_bounds(year, month)
    return as_of > end


# ---------- 基础 IO ----------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _period_manifest(root: Path, kind: str, period_id: str) -> Tuple[Optional[Dict], str]:
    for rel in (f"data/runs/periodic/{kind}/{period_id}/manifest.json",
                f"data/runs/{kind}/{period_id}/manifest.json"):
        p = root / rel
        if p.exists():
            try:
                return json.loads(_read(p)), str(p)
            except Exception as e:
                return None, f"{p} (解析失败: {e})"
    return None, ""


def _closure(root: Path, kind: str, period_id: str, report_path: Path,
             report_text: str) -> Dict:
    """判定某周期是否真正"已封账"。返回 closure dict，不允许用文件存在冒充封账。"""
    man, man_path = _period_manifest(root, kind, period_id)
    sha = _sha(report_text) if report_text else ""
    out = {"period_id": period_id, "manifest_path": man_path,
           "report_sha": sha, "closed": False, "quality_pass": False,
           "source_coverage": None, "reasons": []}
    if not report_text.strip():
        out["reasons"].append("report_missing")
        out["verified"] = False
        return out
    if not man:
        out["reasons"].append("manifest_missing")
        out["verified"] = False
        return out
    out.setdefault("verified", False)
    out["closed"] = bool(man.get("closed") or man.get("sealed"))
    q = man.get("quality") or {}
    out["quality_pass"] = bool(man.get("quality_pass") or q.get("pass"))
    cov = man.get("source_coverage")
    if isinstance(cov, dict):
        cov = cov.get("ratio") if cov.get("ratio") is not None else cov.get("value")
    try:
        out["source_coverage"] = float(cov) if cov is not None else None
    except (TypeError, ValueError):
        out["source_coverage"] = None
    man_sha = man.get("report_sha") or (man.get("report") or {}).get("sha256") or ""
    if man_sha and man_sha != sha:
        out["reasons"].append("report_sha_mismatch")
    if not out["closed"]:
        out["reasons"].append("not_closed")
    if not out["quality_pass"]:
        out["reasons"].append("quality_gate_failed")
    if out["source_coverage"] is not None and out["source_coverage"] < MIN_SOURCE_COVERAGE:
        out["reasons"].append("source_coverage_low")
    out["verified"] = not out["reasons"]
    return out


# ---------- 日级事实切片 ----------

def _day_dirs(root: Path):
    base = root / "data" / "events"
    if not base.exists():
        return []
    out = []
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        try:
            out.append((date.fromisoformat(p.name), p))
        except ValueError:
            continue
    return out


def _event_facts(root: Path, start: date, end: date, limit: int = 200) -> List[Dict]:
    """本月/本年日级事实切片：带 claim 文本、状态、日期角色、来源链接。"""
    out: List[Dict] = []
    for d, p in _day_dirs(root):
        if not (start <= d <= end):
            continue
        f = p / "events_analyzed.json"
        if not f.exists():
            f = p / "events_scored_novelty.json"
        if not f.exists():
            continue
        try:
            data = json.loads(_read(f))
        except Exception:
            continue
        events = data if isinstance(data, list) else data.get("events", [])
        for ev in events or []:
            text = _norm(ev.get("event_title") or ev.get("fact") or "")
            if not text:
                continue
            out.append({
                "fact_id": ev.get("fact_id") or ev.get("event_id") or "",
                "event_id": ev.get("event_id") or "",
                "claim_id": ev.get("claim_id") or ev.get("event_id") or "",
                "date": d.isoformat(),
                "date_role": ev.get("date_role") or "occurred",
                "status": ev.get("status") or "reported",
                "text": text[:300],
                "source_url": ev.get("source_url") or "",
                "source_kind": ev.get("source_kind") or "media",
                "event_ids": ev.get("event_ids") or [],
            })
            if len(out) >= limit:
                return out
    return out


# ---------- 报告收集 ----------

def collect_weekly(root: Path, weeks: List[str],
                   month_range: Optional[Tuple[date, date]] = None
                   ) -> Tuple[List[Dict], List[str], List[Dict]]:
    found, missing, closures = [], [], []
    for wk in weeks:
        year = wk.split("-")[0]
        path = root / "reports" / "weekly" / year / f"{wk}.md"
        text = _read(path)
        closure = _closure(root, "weekly", wk, path, text)
        if month_range:
            ws = iso_week_range(wk)
            lo = max(ws[0], month_range[0])
            hi = min(ws[1], month_range[1])
            fact_slice = _event_facts(root, lo, hi, 60) if lo <= hi else []
        else:
            fact_slice = _event_facts(root, *iso_week_range(wk), 60)
        closure["days_in_scope"] = (
            None if not month_range else
            [lo.isoformat(), hi.isoformat()] if lo <= hi else [])
        closures.append(closure)
        if not text.strip():
            missing.append(wk)
            continue
        found.append({
            "week": wk, "path": str(path), "sha": closure["report_sha"],
            "chars": len(text), "verified": closure["verified"],
            "headings": re.findall(r"^#{2,3}\s+(.+)$", text, re.M)[:8],
            "report_body": text[:REPORT_CHARS],
            "fact_slice": fact_slice,
        })
    return found, missing, closures


def collect_monthly(root: Path, year: int, months: List[int],
                    p14_by_month: Optional[Dict[str, Dict]] = None,
                    full_body: bool = True
                    ) -> Tuple[List[Dict], List[int], List[Dict]]:
    found, missing, closures = [], [], []
    for m in months:
        pid = f"{year}-{m:02d}"
        path = root / "reports" / "monthly" / str(year) / f"{pid}.md"
        text = _read(path)
        closure = _closure(root, "monthly", pid, path, text)
        rec = dict(closure)
        if p14_by_month is not None:
            p14 = p14_by_month.get(pid) or {}
            rec["p14_status"] = p14.get("status") or "missing"
            rec["p14_sha"] = (p14.get("_meta") or {}).get("prompt_version")
            rec["p14_synthesis"] = p14.get("synthesis")
            if rec["p14_status"] != "complete":
                rec["reasons"] = list(rec["reasons"]) + ["p14_not_complete"]
                rec["verified"] = False
        closures.append(rec)
        if not text.strip():
            missing.append(m)
            continue
        found.append({
            "month": pid, "path": str(path), "sha": closure["report_sha"],
            "chars": len(text), "verified": rec.get("verified", False),
            "headings": re.findall(r"^#{2,3}\s+(.+)$", text, re.M)[:8],
            "report_body": text[:REPORT_CHARS] if full_body else _norm(text)[:600],
            "fact_slice": _event_facts(root, *month_bounds(year, m), 40),
        })
    return found, missing, closures


# ---------- P14 / P15 缓存 ----------

def _cache_key(pid: str, version: str, payload: Dict, model: str) -> str:
    src = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return f"{pid}:{version}:{model}:{hashlib.sha256(src.encode()).hexdigest()[:24]}"


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
        json.dumps(data, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")


def _prev_month_synthesis(root: Path, year: int, month: int) -> Tuple[Dict, str]:
    py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
    pid = f"{py}-{pm:02d}"
    path = CACHE_DIR / f"monthly_result_{pid}.json"
    if path.exists():
        try:
            data = json.loads(_read(path))
            if data.get("status") == "complete" and data.get("synthesis"):
                return data.get("synthesis"), pid
        except Exception:
            pass
    return {}, ""


def _load_p14_results(root: Path, year: int, months: List[int]) -> Dict[str, Dict]:
    out = {}
    for m in months:
        pid = f"{year}-{m:02d}"
        p = CACHE_DIR / f"monthly_result_{pid}.json"
        if p.exists():
            try:
                out[pid] = json.loads(_read(p))
            except Exception:
                out[pid] = {"status": "incomplete", "_meta": {}}
        else:
            out[pid] = {"status": "missing", "_meta": {}}
    return out


# ---------- 综合输出校验 ----------

def _evidence_index(payload: Dict) -> Dict[str, set]:
    ids: set = set()
    urls: set = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("fact_id", "claim_id", "event_id") and isinstance(v, str):
                    ids.add(v)
                elif k in ("event_ids", "fact_ids", "claim_ids") and isinstance(v, list):
                    ids.update(x for x in v if isinstance(x, str))
                elif k == "source_url" and isinstance(v, str):
                    urls.add(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return {"ids": ids, "urls": urls}


def _validate_synthesis(synth: Dict, payload: Dict) -> Dict:
    idx = _evidence_index(payload)
    unknown_ids, unknown_urls, refs = [], [], 0

    def walk(node, path=""):
        nonlocal refs
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("fact_id", "claim_id", "event_id") and isinstance(v, str):
                    refs += 1
                    if v not in idx["ids"]:
                        unknown_ids.append({"path": f"{path}.{k}", "value": v})
                elif k in ("event_ids", "fact_ids", "claim_ids") and isinstance(v, list):
                    for x in v:
                        refs += 1
                        if isinstance(x, str) and x not in idx["ids"]:
                            unknown_ids.append({"path": f"{path}.{k}", "value": x})
                elif k == "source_url" and isinstance(v, str):
                    refs += 1
                    if v not in idx["urls"]:
                        unknown_urls.append({"path": f"{path}.{k}", "value": v})
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(synth)
    ok = not unknown_ids and not unknown_urls
    return {"ok": ok, "refs_checked": refs,
            "unknown_ids": unknown_ids[:40],
            "unknown_urls": unknown_urls[:40],
            "evidence_ids": len(idx["ids"]), "evidence_urls": len(idx["urls"])}


# ---------- P14 / P15 ----------

def monthly_synthesis(root: Path, year: int, month: int, send_at: str,
                      call_llm: bool = True) -> Dict:
    as_of = date.fromisoformat(send_at)
    start, end = month_bounds(year, month)
    weeks = iso_weeks_of_month(year, month)
    found, missing, closures = collect_weekly(root, weeks, (start, end))
    closed_ok = [c for c in closures if c.get("verified")]
    last_closed = month_closed(year, month, as_of)

    gate = {"expected_weeks": weeks,
            "found_weeks": [w["week"] for w in found],
            "missing_weeks": missing,
            "unverified_weeks": [c["period_id"] for c in closures
                                 if not c.get("verified")],
            "month_closed": last_closed,
            "closure_evidence": closures,
            "fact_slice_days": sum(len(w.get("fact_slice") or []) for w in found)}
    if not last_closed:
        status = "incomplete"
    elif len(closed_ok) == len(weeks):
        status = "complete"
    elif closed_ok:
        status = "degraded"
    else:
        status = "incomplete"
    gate["status"] = status

    prev_synth, prev_pid = _prev_month_synthesis(root, year, month)
    payload = {
        "period_id": f"{year}-{month:02d}",
        "month_start": start.isoformat(), "month_end": end.isoformat(),
        "as_of_period_end": send_at,
        "send_at": send_at,
        "weekly_reports_json": found,
        "weekly_report_manifest_json": {
            "expected": weeks, "complete": not missing,
            "verified": [c["period_id"] for c in closed_ok],
            "sha_by_week": {w["week"]: w["sha"] for w in found},
            "closure": [{k: c.get(k) for k in
                         ("period_id", "closed", "quality_pass", "source_coverage",
                          "report_sha", "reasons", "days_in_scope")}
                        for c in closures]},
        "monthly_canonical_fact_pack_json": _event_facts(root, start, end,
                                                         FACT_LIMIT_MONTH),
        "previous_month_comparison_json": (
            {"period_id": prev_pid, "synthesis": prev_synth} if prev_synth else {}),
        "weekly_published_fingerprints_json": sorted(
            {f["fact_id"] for w in found for f in (w.get("fact_slice") or [])
             if f.get("fact_id")})[:200],
        "coverage_and_integrity_json": gate,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return _run_cross("P14", payload, gate, f"{year}-{month:02d}", call_llm)


def yearly_synthesis(root: Path, year: int, send_at: str,
                     call_llm: bool = True) -> Dict:
    as_of = date.fromisoformat(send_at)
    months = list(range(1, 13))
    p14 = _load_p14_results(root, year, months)
    found, missing, closures = collect_monthly(root, year, months, p14, True)
    unclosed = [f"{year}-{m:02d}" for m in months
                if not month_closed(year, m, as_of)]
    p14_complete = [pid for pid, r in p14.items() if r.get("status") == "complete"]
    verified = [c["period_id"] for c in closures if c.get("verified")]

    gate = {"expected_months": months,
            "found_months": [m["month"] for m in found],
            "missing_months": missing, "unclosed_months": unclosed,
            "p14_complete_months": p14_complete,
            "verified_months": verified,
            "month_coverage": f"{len(found)}/12",
            "closure_evidence": closures,
            "fact_slice_days": sum(len(m.get("fact_slice") or []) for m in found)}
    if unclosed:
        status = "incomplete"
    elif len(verified) == 12:
        status = "complete"
    elif verified:
        status = "degraded"
    else:
        status = "incomplete"
    gate["status"] = status

    payload = {
        "year": year, "year_start": f"{year}-01-01", "year_end": f"{year}-12-31",
        "as_of_year_end": send_at,
        "send_at": send_at,
        "twelve_monthly_reports_json": found,
        "monthly_report_manifest_json": {
            "missing": missing, "unclosed": unclosed,
            "p14_complete": p14_complete,
            "sha_by_month": {m["month"]: m["sha"] for m in found},
            "closure": [{k: c.get(k) for k in
                         ("period_id", "closed", "quality_pass", "source_coverage",
                          "report_sha", "reasons", "p14_status")}
                        for c in closures]},
        "monthly_synthesis_and_lineage_json": [
            {"month": pid, "status": r.get("status"),
             "synthesis": r.get("synthesis"),
             "gate": (r.get("gate") or {}).get("status")}
            for pid, r in sorted(p14.items())],
        "quarterly_and_year_comparable_metrics_json":
            "无同口径季度数据时留空，不得把不同统计口径相加",
        "comparison_and_coverage_json": gate,
        "verified_internal_business_context_json": {},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    return _run_cross("P15", payload, gate, str(year), call_llm)


def _run_cross(pid: str, payload: Dict, gate: Dict, period_id: str,
               call_llm: bool) -> Dict:
    if gate["status"] != "complete":
        logger.warning("%s 来源门 %s：不调用 LLM（缺周/缺月/未封账/质量门未过）",
                       pid, gate["status"])
        return {"status": gate["status"], "gate": gate, "synthesis": None,
                "_meta": {"prompt": pid, "llm_called": False,
                          "reason": "来源门非 complete，禁止假装完整期间"}}
    pr = render(pid, **payload)
    model = getattr(get_llm_client(), "model", "")
    key = _cache_key(pid, pr.version, payload, model)
    cached = _cache_get(key)
    if cached:
        cached["_meta"]["cache_hit"] = True
        return cached
    if not call_llm:
        return {"status": "complete", "gate": gate, "synthesis": None,
                "_meta": {"prompt": pid, "llm_called": False,
                          "cache_key": key}}
    client = get_llm_client()
    res = client.chat(messages=[{"role": "user", "content": pr.user}],
                      system_prompt=pr.system, response_format="json",
                      temperature=pr.temperature, max_tokens=pr.max_tokens)
    synth = res.get("parsed") if res.get("ok") else None
    meta = {"prompt": pid, "prompt_version": pr.version,
            "model": res.get("model"), "llm_called": True,
            "cache_key": key, "ok": bool(synth)}
    if not isinstance(synth, dict):
        return {"status": "hold_for_review", "gate": gate, "synthesis": None,
                "_meta": dict(meta, error=(res.get("error") or "")[:200])}
    validation = _validate_synthesis(synth, payload)
    synth["_lineage"] = {
        "period_id": period_id,
        "coverage": {k: gate.get(k) for k in
                     ("status", "missing_weeks", "missing_months",
                      "unverified_weeks", "verified_months", "month_closed")},
        "source_shas": {r.get("week") or r.get("month"): r.get("sha")
                        for r in (payload.get("weekly_reports_json")
                                  or payload.get("twelve_monthly_reports_json")
                                  or [])},
        "evidence_refs": validation["refs_checked"],
        "generated_at": payload.get("generated_at"),
    }
    synth["_validation"] = validation
    synth.setdefault("data_gaps", []).extend(
        gate.get("missing_weeks") or gate.get("missing_months") or [])
    if not validation["ok"]:
        meta["validation_failed"] = True
        logger.warning("%s 综合包存在 %d 个未知 ID / %d 个未知 URL，标记 hold",
                       pid, len(validation["unknown_ids"]),
                       len(validation["unknown_urls"]))
        out = {"status": "hold_for_review", "gate": gate, "synthesis": synth,
               "_meta": meta}
        _cache_put(key, out)
        return out
    out = {"status": "complete", "gate": gate, "synthesis": synth,
           "_meta": meta}
    _cache_put(key, out)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"monthly_result_{period_id}.json" if pid == "P14"
     else CACHE_DIR / f"yearly_result_{period_id}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    return out


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
    text = json.dumps(res, ensure_ascii=False, indent=1, default=str)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(json.dumps({"status": res["status"], "gate": res["gate"],
                      "_meta": res["_meta"]}, ensure_ascii=False, indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
