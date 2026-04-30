#!/usr/bin/env python3
"""
source_health_report.py — 信息源健康度评估与分级

在 collect + filter 完成后运行，统计每个信息源的数量、时间窗口分布、
清洗产出率等指标，并按 A/B/C/D 四级分类，给出 keep/limit/reference/disable 建议。

输入：
  data/raw/YYYY-MM-DD/raw_articles.json
  data/cleaned/YYYY-MM-DD/cleaned_articles.json
  data/cleaned/YYYY-MM-DD/reference_articles.json
  data/rejected/YYYY-MM-DD/rejected_articles.json  (可选)

输出：
  data/logs/YYYY-MM-DD/source_health_report.json
  data/logs/YYYY-MM-DD/source_health_report.md

用法：
  python skills/source_health_report/source_health_report.py \
      --project-root . --date 2026-04-26
"""

import argparse
import json
import os
import sys
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 日志 ──
logger = logging.getLogger("source_health_report")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ══════════════════════════════════════════════════════════
#  分级规则
# ══════════════════════════════════════════════════════════

def classify_source(stats: Dict[str, Any]) -> Tuple[str, str]:
    """根据统计指标判断源的分级和推荐动作。

    Returns:
        (grade, recommendation)
        grade: "A" | "B" | "C" | "D"
        recommendation: "keep_primary" | "keep_secondary_limit" | "reference_only" | "disable_candidate"
    """
    recent_ratio = stats.get("recent_ratio", 0) or 0
    old_ratio = stats.get("old_ratio", 0) or 0
    cleaned_yield = stats.get("cleaned_yield_rate", 0) or 0
    matched_kw = stats.get("matched_keyword_count", 0) or 0

    # A类: recent_ratio >= 0.3 且 cleaned_yield_rate >= 0.1
    if recent_ratio >= 0.3 and cleaned_yield >= 0.1:
        return "A", "keep_primary"

    # B类: recent_ratio >= 0.3 且 cleaned_yield_rate < 0.1
    if recent_ratio >= 0.3 and cleaned_yield < 0.1:
        return "B", "keep_secondary_limit"

    # C类: old_ratio >= 0.8 但 matched_keyword_count > 0
    if old_ratio >= 0.8 and matched_kw > 0:
        return "C", "reference_only"

    # D类: old_ratio >= 0.8 且 matched_keyword_count = 0
    if old_ratio >= 0.8 and matched_kw == 0:
        return "D", "disable_candidate"

    # 默认: recent_ratio 介于 0~0.3，old_ratio < 0.8 的是中等归入 B 或 C
    if old_ratio >= 0.5:
        return "C", "reference_only"

    return "B", "keep_secondary_limit"


# ══════════════════════════════════════════════════════════
#  数据加载
# ══════════════════════════════════════════════════════════

def _load_articles(filepath: str) -> List[Dict]:
    """加载文章列表，兼容 dict 和 list 两种格式。"""
    if not os.path.exists(filepath):
        logger.warning(f"文件不存在: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("articles", [])
    elif isinstance(data, list):
        return data
    return []


def _build_article_index(articles: List[Dict], key: str = "article_id") -> Dict[str, Dict]:
    """用 article_id 建索引，方便 cross-reference。"""
    return {a.get(key, ""): a for a in articles if a.get(key)}


# ══════════════════════════════════════════════════════════
#  per-source 统计
# ══════════════════════════════════════════════════════════

def compute_source_stats(
    source_name: str,
    raw_articles: List[Dict],
    cleaned_index: Dict[str, Dict],
    reference_index: Dict[str, Dict],
    rejected_index: Dict[str, Dict],
) -> Dict[str, Any]:
    """计算单个 source 的健康度统计。"""
    total = len(raw_articles)
    if total == 0:
        return {
            "source_name": source_name,
            "total_count": 0,
            "grade": "D",
            "recommendation": "disable_candidate",
        }

    # 时间状态分布
    time_counter = Counter(a.get("time_status", "unknown") for a in raw_articles)
    in_window = time_counter.get("in_window", 0)
    near_window = time_counter.get("near_window", 0)
    old = time_counter.get("old", 0)
    unknown_time = time_counter.get("unknown", 0)

    # 匹配关键词
    matched_kw = sum(1 for a in raw_articles if a.get("matched_keywords"))

    # 清洗结果交叉
    c_count = sum(1 for a in raw_articles if a.get("article_id") in cleaned_index)
    r_count = sum(1 for a in raw_articles if a.get("article_id") in reference_index)
    rej_count = sum(1 for a in raw_articles if a.get("article_id") in rejected_index)

    # 比率
    recent_ratio = (in_window + near_window) / total if total else 0
    old_ratio = old / total if total else 0
    cleaned_yield_rate = c_count / total if total else 0

    # top titles (来自 in_window/near_window 优先, 否则取高 rule_score)
    scored = []
    for a in raw_articles:
        ts = a.get("time_status", "unknown")
        ts_priority = 2 if ts == "in_window" else (1 if ts == "near_window" else 0)
        rule_score = (a.get("filter") or {}).get("rule_score", 0)
        scored.append((ts_priority, rule_score, a.get("title", "")))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top_titles = [t for _, _, t in scored[:5]]

    stats = {
        "source_name": source_name,
        "total_count": total,
        "in_window_count": in_window,
        "near_window_count": near_window,
        "old_count": old,
        "unknown_time_count": unknown_time,
        "matched_keyword_count": matched_kw,
        "cleaned_count": c_count,
        "reference_count": r_count,
        "rejected_count": rej_count,
        "old_ratio": round(old_ratio, 4),
        "recent_ratio": round(recent_ratio, 4),
        "cleaned_yield_rate": round(cleaned_yield_rate, 4),
        "top_titles": top_titles,
    }

    grade, rec = classify_source(stats)
    stats["grade"] = grade
    stats["recommendation"] = rec
    return stats


# ══════════════════════════════════════════════════════════
#  主函数
# ══════════════════════════════════════════════════════════

def source_health_report(
    date: str,
    project_root: str = ".",
) -> Dict[str, Any]:
    """生成信息源健康度报告。

    Args:
        date: 日期字符串，如 "2026-04-26"
        project_root: 项目根目录

    Returns:
        结果字典，包含 ok, report_file, md_file, stats 等
    """
    root = Path(project_root)
    date_str = date

    # ── 加载数据 ──
    raw_path = root / f"data/raw/{date_str}/raw_articles.json"
    cleaned_path = root / f"data/cleaned/{date_str}/cleaned_articles.json"
    ref_path = root / f"data/cleaned/{date_str}/reference_articles.json"
    rej_path = root / f"data/rejected/{date_str}/rejected_articles.json"

    raw_articles = _load_articles(str(raw_path))
    cleaned_articles = _load_articles(str(cleaned_path))
    ref_articles = _load_articles(str(ref_path))
    rej_articles = _load_articles(str(rej_path))

    logger.info(f"加载数据: raw={len(raw_articles)}, cleaned={len(cleaned_articles)}, "
                f"reference={len(ref_articles)}, rejected={len(rej_articles)}")

    if not raw_articles:
        logger.warning("原始文章为空，无法生成健康度报告")
        return {"ok": False, "error": "no_raw_articles"}

    # ── 建索引 ──
    cleaned_index = _build_article_index(cleaned_articles)
    reference_index = _build_article_index(ref_articles)
    rejected_index = _build_article_index(rej_articles)

    # ── 按 source_name 分组 ──
    by_source = defaultdict(list)
    for a in raw_articles:
        src = a.get("source_name", a.get("source_id", "unknown"))
        by_source[src].append(a)

    logger.info(f"发现 {len(by_source)} 个信息源")

    # ── 计算各源统计 ──
    source_stats = []
    for src_name, articles in sorted(by_source.items()):
        stats = compute_source_stats(
            src_name, articles, cleaned_index, reference_index, rejected_index
        )
        source_stats.append(stats)

    # ── 总览统计 ──
    totals = {
        "raw_count": len(raw_articles),
        "cleaned_count": len(cleaned_articles),
        "reference_count": len(ref_articles),
        "rejected_count": len(rej_articles),
        "source_count": len(by_source),
        "grade_distribution": Counter(s["grade"] for s in source_stats),
        "recommendation_distribution": Counter(s["recommendation"] for s in source_stats),
        "total_in_window": sum(s["in_window_count"] for s in source_stats),
        "total_near_window": sum(s["near_window_count"] for s in source_stats),
        "total_old": sum(s["old_count"] for s in source_stats),
    }

    # ── 输出 JSON ──
    log_dir = root / f"data/logs/{date_str}"
    log_dir.mkdir(parents=True, exist_ok=True)

    json_path = log_dir / "source_health_report.json"
    md_path = log_dir / "source_health_report.md"

    report_data = {
        "date": date_str,
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "totals": totals,
        "sources": source_stats,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    logger.info(f"JSON 报告已保存: {json_path}")

    # ── 生成 Markdown 报告 ──
    md_content = _generate_markdown(report_data)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    logger.info(f"Markdown 报告已保存: {md_path}")

    return {
        "ok": True,
        "date": date_str,
        "json_file": str(json_path),
        "md_file": str(md_path),
        "source_count": len(source_stats),
        "grade_distribution": dict(totals["grade_distribution"]),
        "recommendation_distribution": dict(totals["recommendation_distribution"]),
        "total_raw": totals["raw_count"],
        "total_cleaned": totals["cleaned_count"],
        "total_reference": totals["reference_count"],
        "total_rejected": totals["rejected_count"],
    }


# ══════════════════════════════════════════════════════════
#  Markdown 生成
# ══════════════════════════════════════════════════════════

def _generate_markdown(data: Dict) -> str:
    """生成可读的 Markdown 健康度报告。"""
    lines = []
    date_str = data["date"]
    totals = data["totals"]
    sources = data["sources"]

    lines.append(f"# 信息源健康度报告 ｜ {date_str}")
    lines.append("")
    lines.append(f"**生成时间**: {data['generated_at']}")
    lines.append("")

    # ── 总览 ──
    lines.append("## 总览")
    lines.append("")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 信息源数 | {totals['source_count']} |")
    lines.append(f"| 原始文章 | {totals['raw_count']} |")
    lines.append(f"| 清洗文章 | {totals['cleaned_count']} |")
    lines.append(f"| 参考文章 | {totals['reference_count']} |")
    lines.append(f"| 拒绝文章 | {totals['rejected_count']} |")
    lines.append(f"| 时间窗口内 | {totals['total_in_window']} |")
    lines.append(f"| 近窗口 | {totals['total_near_window']} |")
    lines.append(f"| 旧文章 | {totals['total_old']} |")
    lines.append("")

    # ── 分级分布 ──
    grade_dist = totals["grade_distribution"]
    grade_emoji = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}
    lines.append("## 分级分布")
    lines.append("")
    lines.append("| 级别 | 数量 | 含义 |")
    lines.append("|------|------|------|")
    for grade in ["A", "B", "C", "D"]:
        count = grade_dist.get(grade, 0)
        meaning = {"A": "时效高+产出高", "B": "时效高但产出低", "C": "过时但有匹配关键词", "D": "过时且无匹配"}[grade]
        emoji = grade_emoji[grade]
        lines.append(f"| {emoji} {grade} | {count} | {meaning} |")
    lines.append("")

    # ── 1. A类源 ──
    a_sources = [s for s in sources if s["grade"] == "A"]
    lines.append("## 1. A类源 (keep_primary)")
    lines.append("")
    if a_sources:
        lines.append(_source_table(a_sources))
    else:
        lines.append("> 无 A类源")
    lines.append("")

    # ── 2. B类源 ──
    b_sources = [s for s in sources if s["grade"] == "B"]
    lines.append("## 2. B类源 (keep_secondary_limit)")
    lines.append("")
    if b_sources:
        lines.append(_source_table(b_sources))
    else:
        lines.append("> 无 B类源")
    lines.append("")

    # ── 3. C类源 ──
    c_sources = [s for s in sources if s["grade"] == "C"]
    lines.append("## 3. C类源 (reference_only)")
    lines.append("")
    if c_sources:
        lines.append(_source_table(c_sources))
    else:
        lines.append("> 无 C类源")
    lines.append("")

    # ── 4. D类源 ──
    d_sources = [s for s in sources if s["grade"] == "D"]
    lines.append("## 4. D类源 (disable_candidate)")
    lines.append("")
    if d_sources:
        lines.append(_source_table(d_sources))
    else:
        lines.append("> 无 D类源")
    lines.append("")

    # ── 5. old_ratio 最高的前10 ──
    by_old = sorted(sources, key=lambda s: s.get("old_ratio", 0), reverse=True)[:10]
    lines.append("## 5. old_ratio 最高的前10个源")
    lines.append("")
    lines.append("| # | 源 | total | old_ratio | recent_ratio | grade |")
    lines.append("|---|-----|-------|-----------|--------------|-------|")
    for i, s in enumerate(by_old, 1):
        lines.append(f"| {i} | {s['source_name']} | {s['total_count']} | "
                     f"{s.get('old_ratio', 0):.1%} | {s.get('recent_ratio', 0):.1%} | "
                     f"{grade_emoji.get(s['grade'], '')} {s['grade']} |")
    lines.append("")

    # ── 6. cleaned_yield 最高的前10 ──
    by_yield = sorted(sources, key=lambda s: s.get("cleaned_yield_rate", 0), reverse=True)[:10]
    lines.append("## 6. cleaned_yield 最高的前10个源")
    lines.append("")
    lines.append("| # | 源 | total | cleaned | yield_rate | grade |")
    lines.append("|---|-----|-------|---------|------------|-------|")
    for i, s in enumerate(by_yield, 1):
        lines.append(f"| {i} | {s['source_name']} | {s['total_count']} | "
                     f"{s.get('cleaned_count', 0)} | {s.get('cleaned_yield_rate', 0):.1%} | "
                     f"{grade_emoji.get(s['grade'], '')} {s['grade']} |")
    lines.append("")

    # ── 7. 建议降级为 reference_only 的源 ──
    ref_only = [s for s in sources if s["recommendation"] == "reference_only"]
    lines.append("## 7. 建议降级为 reference_only 的源")
    lines.append("")
    if ref_only:
        for s in ref_only:
            lines.append(f"- **{s['source_name']}**: old_ratio={s.get('old_ratio', 0):.1%}, "
                         f"matched_kw={s.get('matched_keyword_count', 0)}, "
                         f"total={s['total_count']}")
    else:
        lines.append("> 无需降级")
    lines.append("")

    # ── 8. 建议禁用的源 ──
    disable = [s for s in sources if s["recommendation"] == "disable_candidate"]
    lines.append("## 8. 建议禁用的源")
    lines.append("")
    if disable:
        for s in disable:
            lines.append(f"- **{s['source_name']}**: old_ratio={s.get('old_ratio', 0):.1%}, "
                         f"matched_kw={s.get('matched_keyword_count', 0)}, "
                         f"total={s['total_count']}")
    else:
        lines.append("> 无需禁用")
    lines.append("")

    # ── 全源明细 ──
    lines.append("## 附录：全源明细")
    lines.append("")
    lines.append("| source_name | total | in_win | near_win | old | matched_kw | "
                 "cleaned | ref | rejected | old_r | recent_r | yield | grade | rec |")
    lines.append("|-------------|-------|--------|----------|-----|------------|---------|-----|---------|-------|----------|-------|-------|-----|")
    for s in sources:
        lines.append(
            f"| {s['source_name']} | {s['total_count']} | {s['in_window_count']} | "
            f"{s['near_window_count']} | {s['old_count']} | {s['matched_keyword_count']} | "
            f"{s['cleaned_count']} | {s['reference_count']} | {s['rejected_count']} | "
            f"{s.get('old_ratio', 0):.0%} | {s.get('recent_ratio', 0):.0%} | "
            f"{s.get('cleaned_yield_rate', 0):.1%} | "
            f"{grade_emoji.get(s['grade'], '')} {s['grade']} | {s['recommendation']} |"
        )
    lines.append("")

    return "\n".join(lines)


def _source_table(sources: List[Dict]) -> str:
    """生成源列表的 Markdown 表格。"""
    lines = []
    lines.append("| source_name | total | in_win | near_win | old | matched_kw | "
                 "cleaned | yield | old_ratio | recent_ratio |")
    lines.append("|-------------|-------|--------|----------|-----|------------|---------|-------|-----------|--------------|")
    for s in sources:
        lines.append(
            f"| {s['source_name']} | {s['total_count']} | {s['in_window_count']} | "
            f"{s['near_window_count']} | {s['old_count']} | {s['matched_keyword_count']} | "
            f"{s['cleaned_count']} | {s.get('cleaned_yield_rate', 0):.1%} | "
            f"{s.get('old_ratio', 0):.0%} | {s.get('recent_ratio', 0):.0%} |"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="信息源健康度评估")
    parser.add_argument("--project-root", default=".", help="项目根目录")
    parser.add_argument("--date", required=True, help="日期，如 2026-04-26")
    args = parser.parse_args()

    result = source_health_report(
        date=args.date,
        project_root=args.project_root,
    )

    if result.get("ok"):
        logger.info(f"✅ 报告生成成功")
        logger.info(f"  sources: {result['source_count']}")
        logger.info(f"  grades: {result['grade_distribution']}")
        logger.info(f"  recommendations: {result['recommendation_distribution']}")
        logger.info(f"  raw/cleaned/ref/rej: {result['total_raw']}/{result['total_cleaned']}"
                     f"/{result['total_reference']}/{result['total_rejected']}")
    else:
        logger.error(f"❌ 报告生成失败: {result.get('error', 'unknown')}")

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()