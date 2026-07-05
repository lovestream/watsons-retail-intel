#!/usr/bin/env python3
"""
quota_tracker.py — API 用量追踪

从存量 pipeline 输出文件中统计本月 API 调用次数，
对比月度预算判断是否超标。

数据源：
- broad_search_urls.json → queries_executed.{tavily,xcrawl}
- xcrawl_enriched_articles.json → total_enriched
- tavily_gap_search_articles.json → metadata gap queries

月度预算 (total across all keys):
- XCrawl:  7000 credits/month (search=2 credits, scrape=1 credit)
- Tavily:  7000 searches/month

用法:
    from skills.health_check.quota_tracker import track_quota
    result = track_quota(project_root)
"""

import json
import os
import logging
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

logger = logging.getLogger(__name__)

XCRAWL_MONTHLY_BUDGET = 7000  # total credits across all keys
TAVILY_MONTHLY_BUDGET = 7000  # total searches across all keys
XCRAWL_SEARCH_COST = 2       # credits per search
XCRAWL_SCRAPE_COST = 1       # credits per scrape


def _days_in_month(year: int, month: int) -> int:
    """Return number of days in given month."""
    if month == 12:
        return 31
    next_month = date(year, month, 1) + timedelta(days=32)
    return (next_month.replace(day=1) - timedelta(days=1)).day


def track_quota(project_root: str) -> dict:
    """统计本月 API 用量，返回健康度报告。

    Returns:
        {"ok": bool, "xcrawl": {...}, "tavily": {...}, "alerts": [...]}
    """
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).date()
    month = today.month
    year = today.year
    days_in_month_val = _days_in_month(year, month)
    days_elapsed = (today - date(year, month, 1)).days + 1

    root = Path(project_root)
    data_raw = root / "data" / "raw"

    # ── 统计 ──
    xcrawl_searches = 0
    xcrawl_scrapes = 0
    tavily_searches = 0
    days_with_data = 0
    daily_breakdown = {}

    for d in range(1, today.day + 1):
        day_str = f"{year}-{month:02d}-{d:02d}"
        daily_breakdown[day_str] = {"xcrawl_search": 0, "xcrawl_scrape": 0, "tavily": 0}

        day_dir = data_raw / day_str

        # ── broad_search_urls.json → search counts ──
        broad_file = day_dir / "broad_search_urls.json"
        if broad_file.exists():
            try:
                with open(broad_file, encoding="utf-8") as f:
                    broad = json.load(f)
                meta = broad.get("metadata", {})
                qe = meta.get("queries_executed", {})
                xs = qe.get("xcrawl", 0)
                ts = qe.get("tavily", 0)
                xcrawl_searches += xs
                tavily_searches += ts
                daily_breakdown[day_str]["xcrawl_search"] = xs
                daily_breakdown[day_str]["tavily"] += ts
            except Exception as e:
                logger.debug(f"Failed to read {broad_file}: {e}")

        # ── xcrawl_enriched_articles.json → scrape count ──
        enrich_file = day_dir / "xcrawl_enriched_articles.json"
        if enrich_file.exists():
            try:
                with open(enrich_file, encoding="utf-8") as f:
                    enrich = json.load(f)
                meta = enrich.get("metadata", {}) if isinstance(enrich, dict) else {}
                scraped = meta.get("total_enriched", 0)
                xcrawl_scrapes += scraped
                daily_breakdown[day_str]["xcrawl_scrape"] = scraped
            except Exception as e:
                logger.debug(f"Failed to read {enrich_file}: {e}")

        # ── tavily_gap_search_articles.json → additional Tavily searches ──
        gap_file = day_dir / "tavily_gap_search_articles.json"
        if gap_file.exists():
            try:
                with open(gap_file, encoding="utf-8") as f:
                    gap = json.load(f)
                meta = gap.get("metadata", {}) if isinstance(gap, dict) else {}
                gap_queries = meta.get("queries_executed", meta.get("gap_count", 0))
                tavily_searches += gap_queries
                daily_breakdown[day_str]["tavily"] += gap_queries
            except Exception as e:
                logger.debug(f"Failed to read {gap_file}: {e}")

        if broad_file.exists() or enrich_file.exists():
            days_with_data += 1

    # ── 计算消耗 ──
    xcrawl_credits = xcrawl_searches * XCRAWL_SEARCH_COST + xcrawl_scrapes * XCRAWL_SCRAPE_COST
    tavily_credits = tavily_searches  # 1 credit per search

    # ── 按天数比例的预期用量 ──
    expected_xcrawl = XCRAWL_MONTHLY_BUDGET * days_elapsed / days_in_month_val
    expected_tavily = TAVILY_MONTHLY_BUDGET * days_elapsed / days_in_month_val

    # ── 判定 ──
    xcrawl_ratio = xcrawl_credits / max(expected_xcrawl, 1)
    tavily_ratio = tavily_credits / max(expected_tavily, 1)

    xcrawl_status = "healthy"
    if xcrawl_ratio > 1.5:
        xcrawl_status = "critical"
    elif xcrawl_ratio > 1.2:
        xcrawl_status = "degraded"
    elif xcrawl_ratio > 1.0:
        xcrawl_status = "warning"

    tavily_status = "healthy"
    if tavily_ratio > 1.5:
        tavily_status = "critical"
    elif tavily_ratio > 1.2:
        tavily_status = "degraded"
    elif tavily_ratio > 1.0:
        tavily_status = "warning"

    # ── 构建结果 ──
    result = {
        "ok": xcrawl_status != "critical" and tavily_status != "critical",
        "date": today.isoformat(),
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month_val,
        "days_with_data": days_with_data,
        "xcrawl": {
            "searches": xcrawl_searches,
            "scrapes": xcrawl_scrapes,
            "credits_used": xcrawl_credits,
            "monthly_budget": XCRAWL_MONTHLY_BUDGET,
            "expected_by_now": round(expected_xcrawl),
            "usage_ratio": round(xcrawl_ratio, 2),
            "status": xcrawl_status,
        },
        "tavily": {
            "searches": tavily_searches,
            "credits_used": tavily_credits,
            "monthly_budget": TAVILY_MONTHLY_BUDGET,
            "expected_by_now": round(expected_tavily),
            "usage_ratio": round(tavily_ratio, 2),
            "status": tavily_status,
        },
        "alerts": [],
        "daily_breakdown": daily_breakdown,
    }

    # ── 生成告警 ──
    if xcrawl_status in ("degraded", "critical"):
        result["alerts"].append(
            f"[{'CRITICAL' if xcrawl_status == 'critical' else 'WARNING'}] "
            f"XCrawl 用量偏高: {xcrawl_credits}/{XCRAWL_MONTHLY_BUDGET} credits "
            f"({xcrawl_ratio*100:.0f}% 预期), 剩余 {XCRAWL_MONTHLY_BUDGET - xcrawl_credits} credits"
        )
    if tavily_status in ("degraded", "critical"):
        result["alerts"].append(
            f"[{'CRITICAL' if tavily_status == 'critical' else 'WARNING'}] "
            f"Tavily 用量偏高: {tavily_credits}/{TAVILY_MONTHLY_BUDGET} searches "
            f"({tavily_ratio*100:.0f}% 预期), 剩余 {TAVILY_MONTHLY_BUDGET - tavily_credits} searches"
        )

    return result


def format_quota_report(result: dict) -> str:
    """格式化配额报告。"""
    x = result["xcrawl"]
    t = result["tavily"]
    lines = [
        f"## API 用量监控",
        f"统计: {result['days_elapsed']}/{result['days_in_month']} 天 ({result['days_with_data']} 天有数据)",
        "",
        f"### XCrawl ({x['status'].upper()})",
        f"- 搜索: {x['searches']} 次 × 2 credits = {x['searches'] * 2}",
        f"- 抓取: {x['scrapes']} 次 × 1 credit  = {x['scrapes']}",
        f"- 已用: {x['credits_used']} / {x['monthly_budget']} credits ({x['usage_ratio']*100:.0f}% 预期)",
        f"- 预期: ~{x['expected_by_now']} credits",
        f"- 剩余: {x['monthly_budget'] - x['credits_used']} credits",
        "",
        f"### Tavily ({t['status'].upper()})",
        f"- 搜索: {t['searches']} 次",
        f"- 已用: {t['credits_used']} / {t['monthly_budget']} searches ({t['usage_ratio']*100:.0f}% 预期)",
        f"- 预期: ~{t['expected_by_now']} searches",
        f"- 剩余: {t['monthly_budget'] - t['credits_used']} searches",
        "",
    ]
    if result["alerts"]:
        lines.append("### ⚠️ 配额告警")
        for a in result["alerts"]:
            lines.append(f"- {a}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    project_root = sys.argv[1] if len(sys.argv) > 1 else "."
    r = track_quota(project_root)
    print(format_quota_report(r))
    if not r["ok"]:
        sys.exit(1)
