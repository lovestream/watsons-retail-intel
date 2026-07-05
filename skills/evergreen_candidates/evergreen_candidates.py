#!/usr/bin/env python3
"""
evergreen_candidates.py — 高价值旧文转知识库

从 reference_articles.json 和 rejected_articles.json 中识别高价值历史文章，
存入 evergreen 知识库。

入选条件:
  - time_status = old 或 unknown_time
  - rule_score >= 8
  - page_type in [article, report, news, unknown]
  - 非 noise 类型 (非促销/产品页)

用途:
  1. 行业背景知识库
  2. 周报/月报素材
  3. 低信号日解释背景
  4. 经营分析Agent补充上下文

输出:
  data/evergreen/YYYY-MM-DD/evergreen_candidates.json
  data/logs/YYYY-MM-DD/evergreen_candidates.log

用法:
    python evergreen_candidates.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-05-02 \
        [--verbose]
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── 时区 ──
CST = timezone(timedelta(hours=8))

# ── 入选条件 ──
EVERGREEN_MIN_SCORE = 8
OLD_TIME_STATUSES = {"old", "unknown_time", "week_fallback"}
VALID_PAGE_TYPES = {"article", "report", "news", "unknown"}
NOISE_ROLES = {"promo", "product_listing", "coupon", "deal"}
NOISE_PAGE_TYPES = {"product", "listing", "promo", "coupon", "store_page"}

# ── 噪音关键词 (标题/摘要中) ──
TITLE_NOISE_KEYWORDS = [
    "降价", "打折", "促销", "特价", "优惠券", "秒杀", "拼团",
    "领券", "满减", "红包", "抽奖", "返现", "福利",
    "招聘", "求职", "简历", "面试",
]


def load_json_safe(filepath: str) -> Optional[dict]:
    """安全加载 JSON 文件。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_articles_from_file(filepath: str) -> List[dict]:
    """从 JSON 文件加载文章列表。"""
    data = load_json_safe(filepath)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("articles", data.get("data", []))
    return []


def is_noise_article(article: dict) -> bool:
    """判断文章是否为噪音类型（不应该进入 evergreen）。"""
    # 检查 role
    role = article.get("role", "")
    if role in NOISE_ROLES:
        return True
    
    # 检查 page_type
    page_type = article.get("page_type", "unknown")
    if page_type in NOISE_PAGE_TYPES:
        return True
    
    # 检查标题中的噪音关键词
    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    for kw in TITLE_NOISE_KEYWORDS:
        if kw in title:
            return True
    
    # 检查内容状态
    content_status = article.get("content_status", "")
    if content_status in ("thin", "empty", "paywall_only"):
        return True
    
    return False


def get_rule_score(article: dict) -> int:
    """获取文章的 rule_score，兼容嵌套和顶层格式。"""
    # 顶层 rule_score
    if "rule_score" in article and isinstance(article["rule_score"], (int, float)):
        return int(article["rule_score"])
    
    # 嵌套在 filter 字段中
    filter_info = article.get("filter", {})
    if isinstance(filter_info, dict) and "rule_score" in filter_info:
        return int(filter_info["rule_score"])
    
    return 0


def get_page_type(article: dict) -> str:
    """获取文章的 page_type，兼容嵌套和顶层格式。"""
    if "page_type" in article:
        return article["page_type"]
    
    filter_info = article.get("filter", {})
    if isinstance(filter_info, dict) and "page_type" in filter_info:
        return filter_info["page_type"]
    
    # 从 URL 推断
    url = article.get("url", "")
    if any(p in url for p in ["/product/", "/item/", "/buy/", "/cart/"]):
        return "product"
    if any(p in url for p in ["/tag/", "/category/"]):
        return "listing"
    
    return "unknown"


def compute_evergreen_score(article: dict) -> float:
    """计算 evergreen 评分 (0-10)。
    
    基于 rule_score + 新鲜度调整 + 内容质量调整。
    """
    base_score = get_rule_score(article)
    
    # 新鲜度调整: old 扣分较少（本来就是 old），unknown_time 稍微扣分
    time_status = article.get("time_status", "unknown_time")
    if time_status == "unknown_time":
        base_score -= 1
    elif time_status == "week_fallback":
        base_score += 0.5  # week_fallback 可能是本周的文章，价值略高
    
    # 内容质量调整
    page_type = get_page_type(article)
    if page_type == "report":
        base_score += 1  # 报告类价值更高
    elif page_type == "article":
        base_score += 0.5  # 文章类价值略高
    
    # watsons_relevance 加成
    wr = article.get("watsons_relevance", 0)
    if isinstance(wr, (int, float)) and wr >= 4:
        base_score += 0.5
    
    # core_hit 加成 (可能在 filter 子字典中)
    filter_info = article.get("filter", {})
    core_hit = article.get("core_hit", filter_info.get("core_hit", False) if isinstance(filter_info, dict) else False)
    if core_hit:
        base_score += 1
    
    return round(base_score, 2)


def extract_evergreen_candidates(
    project_root: str,
    date: str,
    verbose: bool = False,
) -> dict:
    """提取高价值旧文转知识库。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        verbose: 是否启用详细日志
    
    Returns:
        dict: 包含 evergreen_candidates 数量和路径
    """
    # ── 路径 ──
    raw_dir = os.path.join(project_root, "data", "raw", date)
    log_dir = os.path.join(project_root, "data", "logs", date)
    evergreen_dir = os.path.join(project_root, "data", "evergreen", date)
    
    os.makedirs(evergreen_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    
    # ── 日志 ──
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(log_dir, "evergreen_candidates.log"),
                encoding="utf-8",
            ),
        ],
    )
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info(f"Evergreen 候选提取: date={date}")
    logger.info("=" * 60)
    
    output_file = os.path.join(evergreen_dir, "evergreen_candidates.json")
    
    try:
        return _extract_evergreen_impl(project_root, date, logger, raw_dir, evergreen_dir, output_file)
    except Exception as e:
        logger.error(f"Evergreen 候选提取异常: {e}", exc_info=True)
        return {
            "ok": False,
            "status": "error",
            "date": date,
            "total_candidates": 0,
            "error": str(e),
        }


def _extract_evergreen_impl(project_root, date, logger, raw_dir, evergreen_dir, output_file):
    
    # ── 加载文章 ──
    # reference_articles 可能在 data/raw/ 或 data/cleaned/ 下
    reference_paths = [
        os.path.join(project_root, "data", "raw", date, "reference_articles.json"),
        os.path.join(project_root, "data", "cleaned", date, "reference_articles.json"),
    ]
    # rejected_articles 可能在 data/raw/ 或 data/rejected/ 下
    rejected_paths = [
        os.path.join(project_root, "data", "raw", date, "rejected_articles.json"),
        os.path.join(project_root, "data", "rejected", date, "rejected_articles.json"),
    ]
    
    reference_articles = []
    for ref_path in reference_paths:
        if os.path.exists(ref_path):
            ref_data = load_articles_from_file(ref_path)
            if ref_data:
                reference_articles = ref_data
                logger.info(f"加载 reference_articles: {ref_path} ({len(reference_articles)} 条)")
                break
    
    rejected_articles = []
    for rej_path in rejected_paths:
        if os.path.exists(rej_path):
            rej_data = load_articles_from_file(rej_path)
            if rej_data:
                rejected_articles = rej_data
                logger.info(f"加载 rejected_articles: {rej_path} ({len(rejected_articles)} 条)")
                break
    
    if not reference_articles and not rejected_articles:
        logger.warning("未找到 reference_articles 或 rejected_articles，输出0条 evergreen 候选")
    
    logger.info(f"加载 reference_articles: {len(reference_articles)} 条")
    logger.info(f"加载 rejected_articles: {len(rejected_articles)} 条")
    
    # ── 筛选 evergreen 候选 ──
    evergreen_candidates = []
    stats = Counter()
    
    all_candidates = []
    for a in reference_articles:
        a["_source_pool"] = "reference"
        all_candidates.append(a)
    for a in rejected_articles:
        a["_source_pool"] = "rejected"
        all_candidates.append(a)
    
    for article in all_candidates:
        time_status = article.get("time_status", "unknown_time")
        rule_score = get_rule_score(article)
        page_type = get_page_type(article)
        source_pool = article.get("_source_pool", "unknown")
        
        # 条件1: 时间状态 = old / unknown_time / week_fallback
        if time_status not in OLD_TIME_STATUSES:
            stats["rejected_not_old"] += 1
            continue
        
        # 条件2: rule_score >= 8
        if rule_score < EVERGREEN_MIN_SCORE:
            stats["rejected_low_score"] += 1
            continue
        
        # 条件3: page_type 有效
        if page_type not in VALID_PAGE_TYPES:
            stats["rejected_page_type"] += 1
            continue
        
        # 条件4: 非噪音
        if is_noise_article(article):
            stats["rejected_noise"] += 1
            continue
        
        # 计算 evergreen 评分
        evergreen_score = compute_evergreen_score(article)
        
        # 获取 rule_score 和 page_type (兼容嵌套格式)
        actual_rule_score = get_rule_score(article)
        actual_page_type = get_page_type(article)
        
        # 创建 evergreen 候选记录
        candidate = {
            "article_id": article.get("article_id", ""),
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "source_name": article.get("source_name", ""),
            "source_type": article.get("source_type", ""),
            "source_tier": article.get("source_tier", article.get("tier", "")),
            "page_type": actual_page_type,
            "time_status": time_status,
            "rule_score": actual_rule_score,
            "evergreen_score": evergreen_score,
            "watsons_relevance": article.get("watsons_relevance", 0),
            "core_hit": article.get("core_hit", article.get("filter", {}).get("core_hit", False) if isinstance(article.get("filter"), dict) else False),
            "original_pool": source_pool,
            "category": article.get("category", ""),
            "summary": article.get("summary", "")[:200] if article.get("summary") else "",
            "published_at": article.get("published_at", ""),
            "collected_at": article.get("collected_at", ""),
            "reason": article.get("_reject_reason", article.get("_filter_reason", "")),
            "selected_at": datetime.now(CST).isoformat(),
        }
        evergreen_candidates.append(candidate)
        stats[f"accepted_from_{source_pool}"] += 1
    
    # ── 按 evergreen_score 排序 ──
    evergreen_candidates.sort(key=lambda x: x["evergreen_score"], reverse=True)
    
    # ── 保存结果 ──
    output_data = {
        "metadata": {
            "date": date,
            "window_start": f"{date}T07:00:00+08:00" if not date else f"{date}T07:00:00+08:00",
            "window_end": datetime.now(CST).isoformat(),
            "total_candidates": len(evergreen_candidates),
            "from_reference": stats.get("accepted_from_reference", 0),
            "from_rejected": stats.get("accepted_from_rejected", 0),
            "total_reference_input": len(reference_articles),
            "total_rejected_input": len(rejected_articles),
            "rejection_stats": {
                "not_old": stats.get("rejected_not_old", 0),
                "low_score": stats.get("rejected_low_score", 0),
                "invalid_page_type": stats.get("rejected_page_type", 0),
                "noise": stats.get("rejected_noise", 0),
            },
            "selection_criteria": {
                "min_rule_score": EVERGREEN_MIN_SCORE,
                "time_statuses": list(OLD_TIME_STATUSES),
                "valid_page_types": list(VALID_PAGE_TYPES),
            },
            "created_at": datetime.now(CST).isoformat(),
        },
        "articles": evergreen_candidates,
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Evergreen 候选提取完成:")
    logger.info(f"  输入: reference={len(reference_articles)}, rejected={len(rejected_articles)}")
    logger.info(f"  过滤: not_old={stats.get('rejected_not_old',0)}, "
                f"low_score={stats.get('rejected_low_score',0)}, "
                f"page_type={stats.get('rejected_page_type',0)}, "
                f"noise={stats.get('rejected_noise',0)}")
    logger.info(f"  输出: {len(evergreen_candidates)} 篇 evergreen 候选")
    logger.info(f"  来源: reference={stats.get('accepted_from_reference',0)}, "
                f"rejected={stats.get('accepted_from_rejected',0)}")
    if evergreen_candidates:
        top = evergreen_candidates[0]
        logger.info(f"  最高分: {top['evergreen_score']} - {top['title'][:40]}")
        avg = sum(c["evergreen_score"] for c in evergreen_candidates) / len(evergreen_candidates)
        logger.info(f"  平均分: {avg:.1f}")
    logger.info(f"  输出: {output_file}")
    logger.info("=" * 60)
    
    return {
        "ok": True,
        "status": "success",
        "date": date,
        "total_candidates": len(evergreen_candidates),
        "from_reference": stats.get("accepted_from_reference", 0),
        "from_rejected": stats.get("accepted_from_rejected", 0),
        "output_file": output_file,
        "rejection_stats": {
            "not_old": stats.get("rejected_not_old", 0),
            "low_score": stats.get("rejected_low_score", 0),
            "page_type": stats.get("rejected_page_type", 0),
            "noise": stats.get("rejected_noise", 0),
        },
    }


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="高价值旧文转知识库")
    parser.add_argument("--project-root", required=True, help="项目根目录路径")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--verbose", action="store_true", help="启用详细日志")
    args = parser.parse_args()
    
    result = extract_evergreen_candidates(
        project_root=args.project_root,
        date=args.date,
        verbose=args.verbose,
    )
    
    print(f"\n📚 Evergreen 候选提取结果:")
    print(f"  总候选: {result['total_candidates']} 篇")
    print(f"  来自 reference: {result['from_reference']} 篇")
    print(f"  来自 rejected: {result['from_rejected']} 篇")
    print(f"  过滤: not_old={result['rejection_stats']['not_old']}, "
          f"low_score={result['rejection_stats']['low_score']}, "
          f"page_type={result['rejection_stats']['page_type']}, "
          f"noise={result['rejection_stats']['noise']}")


if __name__ == "__main__":
    main()