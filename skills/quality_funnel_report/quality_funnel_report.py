#!/usr/bin/env python3
"""
quality_funnel_report.py — Pipeline V3 采集质量漏斗诊断报告

诊断 Pipeline V3 为什么采集量大但高质量信息少。
统计从 raw → cleaned/reference/rejected 的漏斗转化，
按 collector / source / page_type / freshness / time_status 等维度
拆解，找出瓶颈和可疑遗漏。

输入:
  - data/raw/{date}/raw_articles_all.json
  - data/cleaned/{date}/cleaned_articles.json
  - data/cleaned/{date}/reference_articles.json
  - data/rejected/{date}/rejected_articles.json

输出:
  - data/logs/{date}/quality_funnel_report.json
  - data/logs/{date}/quality_funnel_report.md
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ── logging ──────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(format=LOG_FORMAT, level=logging.INFO)
logger = logging.getLogger("quality_funnel_report")


# ── helpers ──────────────────────────────────────────────────────────────

def _load_json(path: str) -> Any:
    """Load JSON, return empty structure on missing file."""
    if not os.path.exists(path):
        logger.warning(f"File not found: {path}")
        return {"metadata": {}, "articles": []}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"metadata": {}, "articles": data}
    return data


def _safe_get_nested(d: dict, *keys, default="") -> Any:
    """Safely get nested dict values."""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
        else:
            return default
    return current


def _rule_score_bucket(score: Any) -> str:
    """Classify rule_score into a bucket label."""
    if score is None or score == "" or score == "NONE":
        return "未评分"
    try:
        s = int(score)
    except (ValueError, TypeError):
        try:
            s = float(score)
        except (ValueError, TypeError):
            return "未评分"
    if s >= 10:
        return "≥10"
    if s >= 8:
        return "8-9"
    if s >= 6:
        return "6-7"
    if s >= 3:
        return "3-5"
    return "<3"


def _get_collector(article: dict) -> str:
    """Get the most specific collector identifier."""
    # Prefer _source_category (set by merge), then raw_collector, then source_type
    cat = article.get("_source_category", "")
    if cat and cat != "MISSING":
        # Map merge-level categories to friendly names
        mapping = {
            "rsshub_count": "rsshub",
            "source_url_monitor_count": "web_monitor",
            "broad_search_count": "broad_search",
            "xcrawl_enriched_count": "xcrawl_enriched",
            "tavily_gap_count": "tavily_gap",
            "newly_discovered_count": "web_monitor",
        }
        return mapping.get(cat, cat)
    rc = article.get("raw_collector", "")
    if rc:
        return rc
    st = article.get("source_type", "")
    if st:
        return st
    return "unknown"


def _get_freshness(article: dict) -> str:
    """Get freshness_status with fallback."""
    fs = article.get("freshness_status", "")
    if fs:
        return fs
    # Infer from collector / source_type
    st = article.get("source_type", "")
    if st == "web_monitor":
        return "newly_discovered"
    coll = _get_collector(article)
    if coll == "broad_search":
        return "broad_discovery"
    if coll == "tavily_gap":
        return "tavily_gap"
    return "rsshub_collect"


def _get_content_status(article: dict) -> str:
    """Classify content availability."""
    content = article.get("content", "") or ""
    summary = article.get("summary", "") or ""
    if len(content) > 500:
        return "rich"
    if len(content) > 100:
        return "moderate"
    if len(content) > 0:
        return "thin"
    if len(summary) > 50:
        return "summary_only"
    return "empty"


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup matching."""
    try:
        parsed = urlparse(url.lower().rstrip("/"))
        return f"{parsed.netloc}{parsed.path}"
    except Exception:
        return url.lower().rstrip("/")


# ── main analysis ──────────────────────────────────────────────────────

def quality_funnel_report(
    project_root: str,
    date: str,
    verbose: bool = False,
) -> dict:
    """
    Generate the quality funnel report.

    Returns:
        dict with keys: ok, date, report, output_json, output_md
    """
    logger.info("=" * 60)
    logger.info(f"质量漏斗诊断: date={date}")
    logger.info("=" * 60)

    # ── Load data ────────────────────────────────────────────────────
    raw_path = os.path.join(project_root, f"data/raw/{date}/raw_articles_all.json")
    cleaned_path = os.path.join(project_root, f"data/cleaned/{date}/cleaned_articles.json")
    reference_path = os.path.join(project_root, f"data/cleaned/{date}/reference_articles.json")
    rejected_path = os.path.join(project_root, f"data/rejected/{date}/rejected_articles.json")

    raw_data = _load_json(raw_path)
    cleaned_data = _load_json(cleaned_path)
    reference_data = _load_json(reference_path)
    rejected_data = _load_json(rejected_path)

    raw_articles = raw_data.get("articles", [])
    cleaned_articles = cleaned_data.get("articles", [])
    reference_articles = reference_data.get("articles", [])
    rejected_articles = rejected_data.get("articles", [])

    raw_total = len(raw_articles)
    cleaned_total = len(cleaned_articles)
    reference_total = len(reference_articles)
    rejected_total = len(rejected_articles)
    processed_total = cleaned_total + reference_total + rejected_total

    logger.info(f"  raw: {raw_total}  |  cleaned: {cleaned_total}  |  reference: {reference_total}  |  rejected: {rejected_total}")

    # ── Build URL → final_pool map from ALL processed articles ──────
    url_to_pool: Dict[str, str] = {}
    url_to_article: Dict[str, dict] = {}

    for a in cleaned_articles:
        u = _normalize_url(a.get("url", ""))
        url_to_pool[u] = "cleaned"
        url_to_article[u] = a

    for a in reference_articles:
        u = _normalize_url(a.get("url", ""))
        if u not in url_to_pool:  # cleaned takes priority
            url_to_pool[u] = "reference"
            url_to_article[u] = a

    for a in rejected_articles:
        u = _normalize_url(a.get("url", ""))
        if u not in url_to_pool:
            url_to_pool[u] = "rejected"
            url_to_article[u] = a

    # For raw articles not found in processed ones
    raw_unprocessed_urls = set()

    # ── Attach pool info to raw articles ─────────────────────────────
    for a in raw_articles:
        u = _normalize_url(a.get("url", ""))
        if u in url_to_pool:
            a["_final_pool"] = url_to_pool[u]
            # Merge filter data from processed article
            processed = url_to_article.get(u, {})
            flt = processed.get("filter", {})
            if flt:
                if "filter" not in a:
                    a["filter"] = {}
                for key in ("rule_score", "rule_decision", "final_pool",
                           "final_reason", "page_type", "region_tag",
                           "noise_flags", "reject_reason"):
                    if key in flt and key not in a.get("filter", {}):
                        a["filter"][key] = flt[key]
                # Also copy time_status from processed if raw doesn't have it
                for key in ("time_status", "freshness_status"):
                    if not a.get(key) and processed.get(key):
                        a[key] = processed[key]
        else:
            a["_final_pool"] = "unprocessed"
            raw_unprocessed_urls.add(u)

    if raw_unprocessed_urls:
        logger.warning(f"  {len(raw_unprocessed_urls)} raw articles not found in any processed pool")

    # ── Build unified article list ────────────────────────────────────
    all_articles = list(raw_articles)
    # Deduplicate: add processed articles not in raw
    raw_urls = {_normalize_url(a.get("url", "")) for a in raw_articles}
    for a in cleaned_articles + reference_articles + rejected_articles:
        u = _normalize_url(a.get("url", ""))
        if u not in raw_urls:
            a["_final_pool"] = a.get("filter", {}).get("final_pool", "unknown")
            all_articles.append(a)

    # ── Statistics ────────────────────────────────────────────────────
    # Compute pool counts from the unified article list
    pool_counter = Counter(a.get("_final_pool", "unknown") for a in all_articles)
    funnel_cleaned = pool_counter.get("cleaned", 0)
    funnel_reference = pool_counter.get("reference", 0)
    funnel_rejected = pool_counter.get("rejected", 0)
    funnel_unprocessed = pool_counter.get("unprocessed", 0)
    funnel_total = len(all_articles)

    report: Dict[str, Any] = {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "funnel_summary": {
            "raw_total": raw_total,
            "processed_total": processed_total,
            "cleaned_total": funnel_cleaned,
            "reference_total": funnel_reference,
            "rejected_total": funnel_rejected,
            "unprocessed_total": funnel_unprocessed,
            "total_in_funnel": funnel_total,
            "cleaned_rate": round(funnel_cleaned / max(funnel_total, 1) * 100, 2),
            "reference_rate": round(funnel_reference / max(funnel_total, 1) * 100, 2),
            "reject_rate": round(funnel_rejected / max(funnel_total, 1) * 100, 2),
            "unprocessed_rate": round(funnel_unprocessed / max(funnel_total, 1) * 100, 2),
        },
    }

    # ─────────────────────────────────────────────────────────────────
    # 1-10: Simple dimension counters
    # ─────────────────────────────────────────────────────────────────

    # 1. by_collector
    by_collector = Counter(_get_collector(a) for a in all_articles)
    report["by_collector"] = dict(by_collector.most_common())

    # 2. by_source_name
    by_source_name = Counter(a.get("source_name", "unknown") for a in all_articles)
    report["by_source_name"] = dict(by_source_name.most_common(50))

    # 3. by_source_type
    by_source_type = Counter(a.get("source_type", "unknown") for a in all_articles)
    report["by_source_type"] = dict(by_source_type.most_common())

    # 4. by_freshness_status
    by_freshness = Counter(_get_freshness(a) for a in all_articles)
    report["by_freshness_status"] = dict(by_freshness.most_common())

    # 5. by_time_status
    by_time_status = Counter(a.get("time_status", "unknown") for a in all_articles)
    report["by_time_status"] = dict(by_time_status.most_common())

    # 6. by_page_type
    by_page_type = Counter()
    for a in all_articles:
        pt = _safe_get_nested(a, "filter", "page_type", default="未分类")
        by_page_type[pt] += 1
    report["by_page_type"] = dict(by_page_type.most_common())

    # 7. by_region_tag
    by_region = Counter()
    for a in all_articles:
        rt = _safe_get_nested(a, "filter", "region_tag", default="未分类")
        by_region[rt] += 1
    report["by_region_tag"] = dict(by_region.most_common())

    # 8. by_content_status
    by_content = Counter(_get_content_status(a) for a in all_articles)
    report["by_content_status"] = dict(by_content.most_common())

    # 9. by_rule_score_bucket
    by_score_bucket = Counter()
    for a in all_articles:
        score = _safe_get_nested(a, "filter", "rule_score", default=None)
        by_score_bucket[_rule_score_bucket(score)] += 1
    report["by_rule_score_bucket"] = dict(
        sorted(by_score_bucket.items(),
               key=lambda x: ["≥10", "8-9", "6-7", "3-5", "<3", "未评分"].index(x[0])
               if x[0] in ["≥10", "8-9", "6-7", "3-5", "<3", "未评分"] else 99)
    )

    # 10. by_final_pool
    by_pool = Counter(a.get("_final_pool", "unprocessed") for a in all_articles)
    report["by_final_pool"] = dict(by_pool.most_common())

    # ─────────────────────────────────────────────────────────────────
    # 11-13: Cross-dimension counters
    # ─────────────────────────────────────────────────────────────────

    # 11. collector × final_pool
    coll_pool: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in all_articles:
        coll = _get_collector(a)
        pool = a.get("_final_pool", "unprocessed")
        coll_pool[coll][pool] += 1
    report["collector_x_pool"] = {k: dict(v) for k, v in sorted(coll_pool.items())}

    # 12. collector × page_type
    coll_ptype: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in all_articles:
        coll = _get_collector(a)
        pt = _safe_get_nested(a, "filter", "page_type", default="未分类")
        coll_ptype[coll][pt] += 1
    report["collector_x_page_type"] = {k: dict(v) for k, v in sorted(coll_ptype.items())}

    # 13. collector × freshness_status
    coll_fresh: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in all_articles:
        coll = _get_collector(a)
        fs = _get_freshness(a)
        coll_fresh[coll][fs] += 1
    report["collector_x_freshness"] = {k: dict(v) for k, v in sorted(coll_fresh.items())}

    # ─────────────────────────────────────────────────────────────────
    # 14-15: Per-source pool breakdown
    # ─────────────────────────────────────────────────────────────────

    source_pool: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in all_articles:
        sn = a.get("source_name", "unknown")
        pool = a.get("_final_pool", "unprocessed")
        source_pool[sn][pool] += 1

    # 14. source_name × cleaned_count
    report["source_x_cleaned"] = {
        k: dict(v) for k, v in sorted(source_pool.items())
    }

    # 15. source_name × reject_count
    report["source_x_reject"] = {
        k: dict(v) for k, v in sorted(source_pool.items())
    }

    # ─────────────────────────────────────────────────────────────────
    # 16-17: Top N by rule_score in reference/rejected
    # ─────────────────────────────────────────────────────────────────

    def _article_summary(a: dict) -> dict:
        return {
            "article_id": a.get("article_id", ""),
            "title": a.get("title", "")[:80],
            "url": a.get("url", ""),
            "source_name": a.get("source_name", ""),
            "collector": _get_collector(a),
            "rule_score": _safe_get_nested(a, "filter", "rule_score", default=None),
            "page_type": _safe_get_nested(a, "filter", "page_type", default=""),
            "region_tag": _safe_get_nested(a, "filter", "region_tag", default=""),
            "time_status": a.get("time_status", ""),
            "freshness_status": _get_freshness(a),
            "final_pool": a.get("_final_pool", ""),
            "final_reason": _safe_get_nested(a, "filter", "final_reason", default=""),
            "noise_flags": _safe_get_nested(a, "filter", "noise_flags", default=[]),
            "keywords_matched": a.get("keywords_matched", []),
        }

    # 16. top 50 reference by rule_score
    ref_with_score = [(a, _safe_get_nested(a, "filter", "rule_score", default=0)) for a in all_articles
                      if a.get("_final_pool") == "reference"]
    ref_with_score.sort(key=lambda x: float(x[1]) if x[1] is not None else 0, reverse=True)
    report["top50_reference_by_score"] = [_article_summary(a) for a, _ in ref_with_score[:50]]

    # 17. top 50 rejected by rule_score
    rej_with_score = [(a, _safe_get_nested(a, "filter", "rule_score", default=0)) for a in all_articles
                      if a.get("_final_pool") == "rejected"]
    rej_with_score.sort(key=lambda x: float(x[1]) if x[1] is not None else 0, reverse=True)
    report["top50_rejected_by_score"] = [_article_summary(a) for a, _ in rej_with_score[:50]]

    # ─────────────────────────────────────────────────────────────────
    # 18. Cleaned full list
    # ─────────────────────────────────────────────────────────────────
    cleaned_list = [_article_summary(a) for a in all_articles
                    if a.get("_final_pool") == "cleaned"]
    report["cleaned_full_list"] = cleaned_list

    # ─────────────────────────────────────────────────────────────────
    # 19. suspected_missed_candidates
    # ─────────────────────────────────────────────────────────────────
    ALLOWED_FRESHNESS = {"newly_discovered", "day_primary", "tavily_day",
                         "search_day", "published_in_window", "broad_discovery",
                         "rsshub_collect", "in_window"}
    ALLOWED_PAGE_TYPES = {"news", "article", "report", "official_notice", "unknown"}

    suspected = []
    for a in all_articles:
        pool = a.get("_final_pool", "")
        if pool == "cleaned":
            continue

        score = _safe_get_nested(a, "filter", "rule_score", default=None)
        if score is None:
            continue
        try:
            s = int(score)
        except (ValueError, TypeError):
            try:
                s = float(score)
            except (ValueError, TypeError):
                continue

        if s < 6:
            continue

        freshness = _get_freshness(a)
        if freshness not in ALLOWED_FRESHNESS:
            # Also allow articles with time_status=in_window/near_window
            ts = a.get("time_status", "")
            if ts not in ("in_window", "near_window"):
                continue

        pt = _safe_get_nested(a, "filter", "page_type", default="")
        if pt and pt not in ALLOWED_PAGE_TYPES:
            continue

        suspected.append(_article_summary(a))

    suspected.sort(key=lambda x: float(x.get("rule_score", 0) or 0), reverse=True)
    report["suspected_missed_candidates"] = suspected

    # ── Diagnostic analysis ──────────────────────────────────────────
    diagnostics = []

    # Funnel conversion rates
    cr = report["funnel_summary"]["cleaned_rate"]
    rr = report["funnel_summary"]["reference_rate"]
    jr = report["funnel_summary"]["reject_rate"]
    if cr < 5:
        diagnostics.append(
            f"⚠️ cleaned率极低({cr}%)，<5%意味着漏斗严重堵塞。"
            f"reference占{rr}%，rejected占{jr}%。"
        )
    elif cr < 15:
        diagnostics.append(
            f"⚡ cleaned率偏低({cr}%)，大部分文章滞留在reference({rr}%)和rejected({jr}%)。"
        )

    # Old time_status dominance
    old_ratio = report["by_time_status"].get("old", 0)
    if raw_total > 0 and old_ratio / raw_total > 0.4:
        diagnostics.append(
            f"⏰ old文章占{old_ratio}/{raw_total}({round(old_ratio/raw_total*100,1)}%)，"
            "时效性是最大瓶颈。建议增加filterout_days或丢弃更多旧文章。"
        )

    # Unknown time_status
    unknown_ratio = report["by_time_status"].get("unknown_time", 0)
    if unknown_ratio > raw_total * 0.15:
        diagnostics.append(
            f"🔍 unknown_time文章占{unknown_ratio}/{raw_total}({round(unknown_ratio/raw_total*100,1)}%)，"
            "日期缺失导致无法确认时效性。建议增强日期提取或降低unknown_time准入门槛。"
        )

    # page_type noise
    noise_page_types = {"product_page", "homepage", "social_post", "promotion", "job"}
    noise_total = sum(report["by_page_type"].get(pt, 0) for pt in noise_page_types)
    if noise_total > raw_total * 0.15:
        diagnostics.append(
            f"🔇 噪音page_type(product/homepage/social/promotion/job)占"
            f"{noise_total}/{raw_total}({round(noise_total/raw_total*100,1)}%)，"
            "这些文章几乎不可能成为高质量内容。"
        )

    # Content availability
    empty_content = report["by_content_status"].get("empty", 0)
    summary_only = report["by_content_status"].get("summary_only", 0)
    thin_content = report["by_content_status"].get("thin", 0)
    no_real_content = empty_content + summary_only + thin_content
    if no_real_content > raw_total * 0.3:
        diagnostics.append(
            f"📭 缺少正文的文章(empty+summary_only+thin={no_real_content}/{raw_total}"
            f"={round(no_real_content/raw_total*100,1)}%)，"
            "影响关键词匹配和LLM筛选质量。XCrawl富化覆盖率待提升。"
        )

    # Suspected missed
    missed_count = len(suspected)
    if missed_count > 0:
        diagnostics.append(
            f"🔍 发现{missed_count}个suspected_missed_candidates（rule_score≥6，但未进cleaned）。"
            "可能是噪音降级或freshness_status限制，请检查是否需要调整准入规则。"
        )

    # Collector quality
    for coll, pools in report["collector_x_pool"].items():
        total = sum(pools.values())
        cleaned_n = pools.get("cleaned", 0)
        if total >= 5 and cleaned_n == 0:
            reject_n = pools.get("rejected", 0)
            ref_n = pools.get("reference", 0)
            diagnostics.append(
                f"📊 {coll}采集{total}篇，0篇进cleaned"
                f"(rejected={reject_n}, reference={ref_n})。"
                "该采集渠道可能需要优化关键词或过滤源配置。"
            )

    report["diagnostics"] = diagnostics

    # ── Save outputs ──────────────────────────────────────────────────
    logs_dir = os.path.join(project_root, f"data/logs/{date}")
    os.makedirs(logs_dir, exist_ok=True)

    # JSON output
    json_path = os.path.join(logs_dir, "quality_funnel_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"  JSON saved: {json_path}")

    # MD output
    md = _generate_markdown(report)
    md_path = os.path.join(logs_dir, "quality_funnel_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(f"  MD saved: {md_path}")

    logger.info("=" * 60)
    logger.info("质量漏斗诊断完成")
    logger.info(f"  raw: {raw_total}  →  cleaned: {cleaned_total}  |  reference: {reference_total}  |  rejected: {rejected_total}")
    logger.info(f"  suspected_missed: {len(suspected)}")
    logger.info("=" * 60)

    return {
        "ok": True,
        "date": date,
        "report": report,
        "output_json": json_path,
        "output_md": md_path,
    }


def _generate_markdown(report: dict) -> str:
    """Generate a readable Markdown diagnostic report."""
    lines = []

    def header(text, level=2):
        lines.append(f"\n{'#' * level} {text}\n")

    def table_row(cells, widths=None):
        if widths:
            padded = [str(c).ljust(w) for c, w in zip(cells, widths)]
        else:
            padded = [str(c) for c in cells]
        lines.append("| " + " | ".join(padded) + " |")

    def table_sep(count, widths=None):
        if widths:
            lines.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
        else:
            lines.append("|" + "|".join(["---"] * count) + "|")

    s = report["funnel_summary"]
    header(f"质量漏斗诊断报告 — {report['date']}", 1)

    # ── Funnel Summary ──────────────────────────────────────────────
    header("1. 漏斗概览")
    lines.append(f"- **采集总量**: {s['raw_total']}")
    lines.append(f"- **处理总量**: {s['processed_total']}")
    lines.append(f"- **cleaned**: {s['cleaned_total']} ({s['cleaned_rate']}%)")
    lines.append(f"- **reference**: {s['reference_total']} ({s['reference_rate']}%)")
    lines.append(f"- **rejected**: {s['rejected_total']} ({s['reject_rate']}%)")
    lines.append(f"- **未处理**: {s['unprocessed_total']}")
    lines.append("")

    # ── Funnel bar ──────────────────────────────────────────────────
    total = max(s['total_in_funnel'], 1)
    bar_len = 50
    c_len = max(int(s['cleaned_total'] / total * bar_len), 1) if s['cleaned_total'] > 0 else 0
    r_len = max(int(s['reference_total'] / total * bar_len), 1) if s['reference_total'] > 0 else 0
    j_len = max(int(s['rejected_total'] / total * bar_len), 1) if s['rejected_total'] > 0 else 0
    u_len = max(bar_len - c_len - r_len - j_len, 0)
    lines.append(f"```\n{'█' * c_len}{'░' * r_len}{'·' * j_len}{'⁓' * u_len}\n```")
    lines.append(f"`█` cleaned  `░` reference  `·` rejected  `⁓` unprocessed\n")

    # ── Simple dimension tables ─────────────────────────────────────
    dimension_configs = [
        ("by_collector", "2. 按采集源(collector)", "Collector", "Count"),
        ("by_source_type", "3. 按源类型(source_type)", "Source Type", "Count"),
        ("by_freshness_status", "4. 按时效标记(freshness_status)", "Freshness", "Count"),
        ("by_time_status", "5. 按时间状态(time_status)", "Time Status", "Count"),
        ("by_page_type", "6. 按页面类型(page_type)", "Page Type", "Count"),
        ("by_region_tag", "7. 按地区标签(region_tag)", "Region", "Count"),
        ("by_content_status", "8. 按内容丰富度(content_status)", "Content", "Count"),
        ("by_rule_score_bucket", "9. 按规则评分(rule_score_bucket)", "Score Bucket", "Count"),
        ("by_final_pool", "10. 按最终池(final_pool)", "Pool", "Count"),
    ]

    for key, title, col1, col2 in dimension_configs:
        header(title)
        data = report.get(key, {})
        if not data:
            lines.append("*无数据*\n")
            continue
        items = list(data.items())
        lines.append(f"| {col1} | {col2} |")
        lines.append(f"| --- | --- |")
        for k, v in items:
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # ── Cross-dimension tables ───────────────────────────────────────
    cross_configs = [
        ("collector_x_pool", "11. Collector × Final Pool", "Collector"),
        ("collector_x_page_type", "12. Collector × Page Type", "Collector"),
        ("collector_x_freshness", "13. Collector × Freshness", "Collector"),
    ]

    for key, title, label in cross_configs:
        header(title)
        data = report.get(key, {})
        if not data:
            lines.append("*无数据*\n")
            continue
        # Collect all sub-keys
        all_subkeys = sorted(set(sk for v in data.values() for sk in v.keys()))
        lines.append(f"| {label} | " + " | ".join(all_subkeys) + " |")
        lines.append(f"| --- | " + " | ".join(["---"] * len(all_subkeys)) + " |")
        for coll, pools in sorted(data.items()):
            vals = [str(pools.get(sk, 0)) for sk in all_subkeys]
            lines.append(f"| {coll} | " + " | ".join(vals) + " |")
        # Total row
        totals = [sum(data[coll].get(sk, 0) for coll in data) for sk in all_subkeys]
        lines.append(f"| **Total** | " + " | ".join(f"**{t}**" for t in totals) + " |")
        lines.append("")

    # ── Source Name breakdown ────────────────────────────────────────
    header("14-15. 源名(Source Name) × Pool 分布")
    source_data = report.get("source_x_cleaned", report.get("source_x_reject", {}))
    if source_data:
        all_pools = sorted(set(sk for v in source_data.values() for sk in v.keys()))
        lines.append(f"| Source | " + " | ".join(all_pools) + " | Total |")
        lines.append(f"| --- | " + " | ".join(["---"] * len(all_pools)) + " | --- |")
        # Sort by total articles desc
        sorted_sources = sorted(source_data.items(),
                                key=lambda x: sum(x[1].values()), reverse=True)
        for sn, pools in sorted_sources:
            vals = [str(pools.get(sk, 0)) for sk in all_pools]
            total = sum(pools.values())
            lines.append(f"| {sn} | " + " | ".join(vals) + f" | {total} |")
        lines.append("")

    # ── Top 50 Reference ────────────────────────────────────────────
    header("16. Top 50 Reference (by rule_score)")
    top_ref = report.get("top50_reference_by_score", [])
    if top_ref:
        lines.append("| # | Score | Title | Source | Type | Time | Freshness | Reason |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for i, a in enumerate(top_ref[:50], 1):
            score = a.get("rule_score", "")
            title = a.get("title", "")[:50]
            src = a.get("source_name", "")
            pt = a.get("page_type", "")
            ts = a.get("time_status", "")
            fs = a.get("freshness_status", "")
            reason = a.get("final_reason", "")[:60]
            lines.append(f"| {i} | {score} | {title} | {src} | {pt} | {ts} | {fs} | {reason} |")
        lines.append("")
    else:
        lines.append("*无reference文章*\n")

    # ── Top 50 Rejected ─────────────────────────────────────────────
    header("17. Top 50 Rejected (by rule_score)")
    top_rej = report.get("top50_rejected_by_score", [])
    if top_rej:
        lines.append("| # | Score | Title | Source | Type | Time | Reason |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for i, a in enumerate(top_rej[:50], 1):
            score = a.get("rule_score", "")
            title = a.get("title", "")[:50]
            src = a.get("source_name", "")
            pt = a.get("page_type", "")
            ts = a.get("time_status", "")
            reason = a.get("final_reason", "")[:60] or a.get("reject_reason", "")[:60]
            lines.append(f"| {i} | {score} | {title} | {src} | {pt} | {ts} | {reason} |")
        lines.append("")
    else:
        lines.append("*无rejected文章*\n")

    # ── Cleaned Full List ────────────────────────────────────────────
    header("18. Cleaned 全量清单")
    cleaned_list = report.get("cleaned_full_list", [])
    if cleaned_list:
        lines.append("| # | Score | Title | Source | Collector | Page Type | Freshness |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for i, a in enumerate(cleaned_list, 1):
            score = a.get("rule_score", "")
            title = a.get("title", "")[:60]
            src = a.get("source_name", "")
            coll = a.get("collector", "")
            pt = a.get("page_type", "")
            fs = a.get("freshness_status", "")
            lines.append(f"| {i} | {score} | {title} | {src} | {coll} | {pt} | {fs} |")
        lines.append("")
    else:
        lines.append("*⚠️ 无cleaned文章*\n")

    # ── Suspected Missed Candidates ──────────────────────────────────
    header("19. Suspected Missed Candidates (rule_score≥6, !cleaned)")
    missed = report.get("suspected_missed_candidates", [])
    if missed:
        lines.append(f"共 **{len(missed)}** 篇疑似遗漏（高分但未进cleaned）：\n")
        lines.append("| # | Score | Title | Source | Collector | Page Type | Time | Pool | Reason |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for i, a in enumerate(missed[:50], 1):
            score = a.get("rule_score", "")
            title = a.get("title", "")[:45]
            src = a.get("source_name", "")
            coll = a.get("collector", "")
            pt = a.get("page_type", "")
            ts = a.get("time_status", "")
            pool = a.get("final_pool", "")
            reason = a.get("final_reason", "")[:50]
            lines.append(f"| {i} | {score} | {title} | {src} | {coll} | {pt} | {ts} | {pool} | {reason} |")
        lines.append("")
    else:
        lines.append("*无疑似遗漏*\n")

    # ── Diagnostics ──────────────────────────────────────────────────
    header("诊断建议")
    diagnostics = report.get("diagnostics", [])
    if diagnostics:
        for d in diagnostics:
            lines.append(f"- {d}")
        lines.append("")
    else:
        lines.append("*无明显问题*\n")

    return "\n".join(lines)


# ── CLI entry point ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Quality Funnel Report — 采集质量漏斗诊断")
    parser.add_argument("--project-root", required=True, help="Project root directory")
    parser.add_argument("--date", required=True, help="Date string YYYY-MM-DD")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    result = quality_funnel_report(
        project_root=args.project_root,
        date=args.date,
        verbose=args.verbose,
    )

    if result["ok"]:
        print(f"\n✅ 质量漏斗诊断完成: {result['output_md']}")
        print(f"   JSON: {result['output_json']}")
        s = result["report"]["funnel_summary"]
        print(f"   raw={s['raw_total']} → cleaned={s['cleaned_total']} | reference={s['reference_total']} | rejected={s['rejected_total']}")
    else:
        print(f"❌ 诊断失败")
        sys.exit(1)


if __name__ == "__main__":
    main()