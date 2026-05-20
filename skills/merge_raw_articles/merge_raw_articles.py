#!/usr/bin/env python3
"""
merge_raw_articles.py — 合并多个采集来源的原始文章

将 RSSHub/RSS/Web Monitor/广泛搜索/XCrawl抓取/Tavily补搜 的文章
合并为统一的 raw_articles_all.json，供 filter 步骤使用。

去重规则：URL 为去重主键（normalized），先到先得。

用法:
    python merge_raw_articles.py --project-root /path/to/project --date 2026-05-02
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

# ── 时区 ──
CST = timezone(timedelta(hours=8))

# ── 合并输入源（按优先级，先到先得） ──
# 重要：有全文的 enriched 版本必须排在无全文的原始版本前面！
# 这样当同一 URL 同时存在于 enriched 和 raw 中时，优先保留有全文的版本。
MERGE_SOURCES = [
    {
        "file": "cloakbrowser_enriched_articles.json",
        "name": "cloakbrowser_content_fetch",
        "description": "CloakBrowser 正文抓取后的富文章（最高优先级：有全文）",
    },
    {
        "file": "xcrawl_enriched_articles.json",
        "name": "xcrawl_enrich",
        "description": "XCrawl 抓取正文后的富文章（高优先级：有全文）",
    },
    {
        "file": "xcrawl_cloakbrowser_fallback.json",
        "name": "cloakbrowser_enrich",
        "description": "CloakBrowser XCrawl fallback（XCrawl失败的URL用CloakBrowser重抓）",
    },
    {
        "file": "raw_articles.json",
        "name": "main_collection",
        "description": "主采集（RSSHub + XCrawl search snippets）",
    },
    {
        "file": "tophub_articles.json",
        "name": "tophub_collect",
        "description": "TopHub.today 聚合搜索（覆盖数百新闻源，精确时间戳）",
    },
    {
        "file": "newly_discovered_urls.json",
        "name": "source_url_monitor",
        "description": "Web Monitor 新发现",
    },
    {
        "file": "cloakbrowser_articles.json",
        "name": "cloakbrowser_collect",
        "description": "CloakBrowser 隐身浏览器采集（兜底：仅标题无正文时使用）",
    },
    {
        "file": "tavily_gap_articles.json",
        "name": "tavily_gap_search",
        "description": "Tavily 补搜",
    },
    {
        "file": "broad_search_urls.json",
        "name": "broad_search_discovery",
        "description": "广泛搜索发现（最低优先级：被enriched覆盖的自动去重）",
    },
]


# ═══════════════════════════════════════════
# 日期提取辅助函数 (为 broad_search 等来源补充)
# ═══════════════════════════════════════════

def _extract_url_date(url: str) -> Optional[str]:
    """从 URL 中提取日期 YYYY-MM-DD（更可靠的结构化来源）。"""
    if not url:
        return None
    # /2025/11/02/ 格式 (新浪、36氪等)
    u1 = re.search(r'/(\d{4})[/_\-](\d{2})[/_\-](\d{2})/', url)
    if u1:
        try:
            y, m, d = int(u1.group(1)), int(u1.group(2)), int(u1.group(3))
            if 2020 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    # /20251102 格式
    u2 = re.search(r'/(\d{4})(\d{2})(\d{2})', url)
    if u2:
        try:
            y, m, d = int(u2.group(1)), int(u2.group(2)), int(u2.group(3))
            if 2020 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    return None


def _parse_date_str(date_str: str) -> Optional[datetime]:
    """将日期字符串解析为 datetime（CST）。"""
    if not date_str or len(date_str) < 8:
        return None
    for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z',
                '%a, %d %b %Y %H:%M:%S']:
        try:
            dt = datetime.strptime(date_str[:25].strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt
        except (ValueError, IndexError):
            continue
    # 兜底：尝试取前10位
    try:
        return datetime.strptime(date_str[:10], '%Y-%m-%d').replace(tzinfo=CST)
    except (ValueError, IndexError):
        return None


def _extract_date_from_article(article: dict, logger=None) -> Optional[str]:
    """从文章的 description/summary/url 中提取发布日期 YYYY-MM-DD。
    
    增强版：始终提取 URL 日期作为交叉验证。如果 published_at 与 URL 日期
    差异超过 7 天，优先信任 URL 日期（URL 中的日期结构更可靠）。
    """
    url = article.get("url", "")
    url_date = _extract_url_date(url)  # 始终提取 URL 日期用于交叉验证
    
    # 0. 已有 published_at
    existing = article.get("published_at", "")
    pub_date = None
    if existing and len(existing) > 5:
        dt = _parse_date_str(existing)
        if dt:
            pub_date = dt.strftime('%Y-%m-%d')
    
    # ═══ URL 日期交叉验证 ═══
    # 如果 published_at 和 URL 日期差异 > 7 天，URL 日期更可靠
    if pub_date and url_date:
        pub_dt = _parse_date_str(pub_date)
        url_dt = _parse_date_str(url_date)
        if pub_dt and url_dt:
            diff_days = abs((pub_dt - url_dt).days)
            if diff_days > 7:
                if logger:
                    logger.warning(
                        f"  ⚠️ 日期冲突: published_at={pub_date} vs URL日期={url_date} "
                        f"(差异{diff_days}天)，使用URL日期"
                    )
                return url_date
    if pub_date:
        return pub_date
    
    # 1. raw.published_date (Tavily)
    raw = article.get("raw", {})
    if isinstance(raw, dict):
        pd = raw.get("published_date", "")
        if pd and len(pd) > 5:
            return pd[:10]
    
    # 2. 合并可能包含日期的文本
    desc = (article.get("summary", "") or article.get("description", "") or "")
    if isinstance(raw, dict):
        desc_raw = raw.get("description", "")
        if desc_raw and len(desc_raw) > len(desc):
            desc = desc_raw
    content = article.get("content", "")
    combined = (desc + " " + content[:200]) if content else desc
    
    # 3. 中文日期: "2026年4月12日 — ..."
    cn = re.search(r'(\d{4})[年/\-\.](\d{1,2})[月/\-\.](\d{1,2})[日号]?\s*—?\s*', combined[:100])
    if cn:
        try:
            return f"{int(cn.group(1)):04d}-{int(cn.group(2)):02d}-{int(cn.group(3)):02d}"
        except (ValueError, IndexError):
            pass
    
    # 4. 相对日期: "5天前 — ..."
    #    注意: 必须包含"前"字，避免误匹配"24小时营业"等非时间描述
    now = datetime.now(CST)
    rel = re.search(r'(\d+)\s*(天|小时|分钟)\s*前', combined[:40])
    if rel:
        try:
            num = int(rel.group(1))
            unit = rel.group(2)
            if '天' in unit:
                return (now - timedelta(days=num)).strftime('%Y-%m-%d')
            elif '小时' in unit:
                return (now - timedelta(hours=num)).strftime('%Y-%m-%d')
            elif '分钟' in unit:
                return now.strftime('%Y-%m-%d')
        except (ValueError, IndexError):
            pass
    
    # 昨天/今天/前天
    if combined.startswith('昨天') or '昨天 —' in combined[:20]:
        return (now - timedelta(days=1)).strftime('%Y-%m-%d')
    if combined.startswith('今天') or '今天 —' in combined[:20]:
        return now.strftime('%Y-%m-%d')
    
    # 5. URL 日期
    if url_date:
        return url_date
    
    return None


def _classify_extracted_date(date_str: str, collection_date: str) -> str:
    """根据提取的日期计算 time_status。"""
    try:
        pub_dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
        window_end = datetime.strptime(collection_date, '%Y-%m-%d').replace(hour=7)
        
        diff = (window_end - pub_dt).days
        if 0 <= diff <= 2:
            return "in_window"
        elif 3 <= diff <= 7:
            return "near_window"
        elif diff > 7:
            return "old"
        else:
            return "in_window"
    except (ValueError, IndexError):
        return "unknown_time"


# ═══════════════════════════════════════════
# URL 标准化
# ═══════════════════════════════════════════

def normalize_url(url: str) -> str:
    """标准化 URL 用于去重。"""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"
        # 去除常见追踪参数
        query = parsed.query
        if query:
            # 保留核心查询参数，去除追踪参数
            tracking_params = {"utm_source", "utm_medium", "utm_campaign",
                               "utm_content", "utm_term", "from", "share"}
            pairs = []
            for pair in query.split("&"):
                if "=" in pair:
                    key = pair.split("=")[0].lower()
                    if key not in tracking_params:
                        pairs.append(pair)
            query = "&".join(pairs)
        return urlunparse((scheme, netloc, path, parsed.params, query, ""))
    except Exception:
        return url.rstrip("/")


# ═══════════════════════════════════════════
# 来源分类
# ═══════════════════════════════════════════

# collector → 来源类别映射
COLLECTOR_CATEGORY_MAP = {
    "rsshub": "rsshub_count",
    "rss": "rss_count",
    "web_monitor": "web_count",
    "source_url_monitor": "source_url_monitor_count",
    "broad_search_discovery": "broad_search_count",
    "broad_discovery": "broad_search_count",
    "broad_tavily": "broad_search_count",
    "broad_xcrawl": "broad_search_count",
    "xcrawl_enrich": "xcrawl_enriched_count",
    "xcrawl_scrape": "xcrawl_enriched_count",
    "tavily_gap_search": "tavily_gap_count",
    "tavily_broad": "broad_search_count",
    "xcrawl_broad": "broad_search_count",
    "cloakbrowser": "cloakbrowser_count",
    "cloakbrowser_collect": "cloakbrowser_count",
    "cloakbrowser_content_fetch": "cloakbrowser_count",
    "cloakbrowser_enrich": "cloakbrowser_count",
    # 通用回退
    "main_collection": "rsshub_count",
}

# source_type → 来源类别映射
SOURCE_TYPE_CATEGORY_MAP = {
    "rsshub": "rsshub_count",
    "rss": "rss_count",
    "web_monitor": "web_count",
    "source_url_monitor": "source_url_monitor_count",
    "tavily": "tavily_gap_count",
    "tavily_broad": "broad_search_count",
    "xcrawl": "xcrawl_enriched_count",
    "xcrawl_search": "broad_search_count",
    "xcrawl_broad": "broad_search_count",
    "xcrawl_enriched": "xcrawl_enriched_count",
    "cloakbrowser": "cloakbrowser_count",
    "tophub": "tophub_count",
}


def classify_article(article: dict, file_source: str) -> str:
    """根据 article 的 collector/source_type/file_source 判断来源类别。"""
    collector = article.get("collector", "")
    source_type = article.get("source_type", "")
    role = article.get("role", "")

    # 1. 明确的 collector
    if collector in COLLECTOR_CATEGORY_MAP:
        return COLLECTOR_CATEGORY_MAP[collector]

    # 2. 明确的 source_type
    if source_type in SOURCE_TYPE_CATEGORY_MAP:
        return SOURCE_TYPE_CATEGORY_MAP[source_type]

    # 3. role 标记
    if role == "broad_discovery":
        return "broad_search_count"
    if role == "newly_discovered":
        return "source_url_monitor_count"

    # 4. 文件来源
    file_map = {
        "raw_articles.json": "rsshub_count",
        "newly_discovered_urls.json": "source_url_monitor_count",
        "broad_search_urls.json": "broad_search_count",
        "xcrawl_enriched_articles.json": "xcrawl_enriched_count",
        "tavily_gap_articles.json": "tavily_gap_count",
    }
    if file_source in file_map:
        return file_map[file_source]

    # 5. 模糊匹配
    if "rss" in collector.lower() or "rss" in source_type.lower():
        return "rss_count"
    if "tavily" in collector.lower() or "tavily" in source_type.lower():
        return "tavily_gap_count"
    if "xcrawl" in collector.lower() or "xcrawl" in source_type.lower():
        return "xcrawl_enriched_count"
    if "cloakbrowser" in collector.lower() or "cloakbrowser" in source_type.lower():
        return "cloakbrowser_count"
    if "web" in collector.lower() or "monitor" in collector.lower():
        return "web_count"

    # 6. 兜底
    return "rsshub_count"


# ═══════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════

def merge_raw_articles(
    project_root: str,
    date: str,
    verbose: bool = False,
) -> dict:
    """合并多个采集来源的原始文章。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        verbose: 是否启用详细日志
    
    Returns:
        结果 dict，包含 ok, date, total_count, 各来源统计等
    """
    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)

    # ── 日志 ──
    log_file = log_dir / "merge_raw_articles.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("merge_raw_articles")

    logger.info("=" * 60)
    logger.info(f"合并原始文章: date={date}")
    logger.info("=" * 60)

    errors: List[str] = []

    # ── 逐步合并 ──
    seen_urls: Set[str] = set()
    all_articles: List[dict] = []
    source_counts = Counter()
    file_counts = Counter()
    dedup_counts = Counter()
    date_extracted_count = 0
    time_status_filled_count = 0

    for source_info in MERGE_SOURCES:
        filename = source_info["file"]
        source_name = source_info["name"]
        filepath = raw_dir / filename

        if not filepath.exists():
            logger.info(f"  ⏭️  {filename}: 文件不存在，跳过")
            continue

        try:
            data = json.load(open(filepath, encoding="utf-8"))
        except Exception as e:
            logger.warning(f"  ❌ {filename}: JSON 解析失败 — {e}")
            errors.append(f"加载 {filename} 失败: {e}")
            continue

        # 解析文章列表
        if isinstance(data, dict):
            articles = data.get("articles", data.get("urls", []))
            metadata = data.get("metadata", {})
        elif isinstance(data, list):
            articles = data
            metadata = {}
        else:
            logger.warning(f"  ❌ {filename}: 未知格式")
            continue

        file_total = len(articles)
        file_added = 0
        file_deduped = 0

        for article in articles:
            if not isinstance(article, dict):
                continue

            url = article.get("url", "").strip()
            if not url:
                continue

            norm_url = normalize_url(url)
            if norm_url in seen_urls:
                file_deduped += 1
                continue
            seen_urls.add(norm_url)

            # 确保 article_id 存在
            if "article_id" not in article or not article["article_id"]:
                article["article_id"] = hashlib.sha256(
                    f"{url}".encode()).hexdigest()[:16]

            # 确保 collected_at 存在
            if "collected_at" not in article or not article["collected_at"]:
                article["collected_at"] = datetime.now(CST).isoformat()

            # 分类来源
            category = classify_article(article, filename)
            source_counts[category] += 1
            article["_source_category"] = category

            # ── 字段标准化: pub_date/pub_time → published_at ──
            if not article.get("published_at") and article.get("pub_date"):
                pd = article["pub_date"]
                pt = article.get("pub_time", "")
                if pt:
                    article["published_at"] = f"{pd}T{pt}:00+08:00"
                else:
                    article["published_at"] = pd
                date_extracted_count += 1

            # ── 日期补充: 为没有 published_at/time_status 的文章提取日期 ──
            if not article.get("published_at") or not article.get("time_status"):
                extracted_date = _extract_date_from_article(article, logger)
                if extracted_date:
                    if not article.get("published_at"):
                        article["published_at"] = extracted_date
                        date_extracted_count += 1
                    if not article.get("time_status"):
                        article["time_status"] = _classify_extracted_date(
                            extracted_date, date
                        )
                        time_status_filled_count += 1
                # 即使无法提取日期，也设置默认的 time_status
                if not article.get("time_status"):
                    article["time_status"] = "unknown_time"
                    time_status_filled_count += 1

            all_articles.append(article)
            file_added += 1

        file_counts[filename] = file_total
        dedup_counts[filename] = file_deduped
        logger.info(f"  ✅ {filename}: 加载 {file_total} 条, 去重后新增 {file_added} 条"
                     f" (跳过 {file_deduped} 条重复)")

    # ── 统计 ──
    total_count = len(all_articles)
    logger.info(f"\n合并完成:")
    logger.info(f"  总计: {total_count} 条文章 (去重后)")
    logger.info(f"  日期提取: 补充 {date_extracted_count} 篇 published_at, "
                f"补充 {time_status_filled_count} 篇 time_status")
    logger.info(f"  来源统计:")
    for cat, count in sorted(source_counts.items()):
        logger.info(f"    {cat}: {count}")
    logger.info(f"  文件统计:")
    for fname, count in file_counts.items():
        dedup = dedup_counts.get(fname, 0)
        logger.info(f"    {fname}: {count} 条 (去重 {dedup})")

    # ── 硬日期门：仅保留数据窗口内文章 ──
    # RSSHub 频道: 当天+昨天（日期可信，每天大量新文章）
    # RSSHub 搜索: 放宽到3天（niche查询可能前天才有新文章，但不要太旧）
    # XCrawl/CloakBrowser: 放宽到7天（搜索采集日期不精确但内容有参考价值）
    # 例外：source_url_monitor 来源的 unknown_time 文章保留
    MONITOR_SOURCES = {"source_url_monitor", "web_monitor", "source_url_monitor_count"}
    BROAD_SOURCES = {"xcrawl_enrich", "xcrawl", "cloakbrowser_collect", "broad_search_discovery", "tophub_collect", "tophub", "xcrawl_enriched_count", "broad_search_count", "broad_search", "xcrawl_broad", "tavily_broad"}
    window_start_dt = datetime.strptime(date, "%Y-%m-%d").replace(hour=7, tzinfo=CST)
    date_cutoff_strict = window_start_dt - timedelta(days=1)   # RSSHub频道: 昨天~今天
    date_cutoff_search = window_start_dt - timedelta(days=3)   # RSSHub搜索: 3天内
    date_cutoff_loose = window_start_dt - timedelta(days=7)    # XCrawl/CloakBrowser: 7天
    
    kept_articles = []
    rejected_old = 0
    rejected_no_date = 0
    kept_near = 0
    
    for a in all_articles:
        ts = a.get("time_status", "unknown_time")
        src_cat = a.get("_source_category", "")
        pub_str = a.get("published_at", "")
        collector = a.get("collector", "")
        source_type = a.get("source_type", "")
        
        # ═══ 优先检查: 可信采集源的 uncertain_date 文章直接保留 ═══
        # CloakBrowser/TopHub 采集端已做时效筛选，merge不应该用从content猜测的日期拒绝它们
        if ts == "uncertain_date":
            if "cloakbrowser" in collector:
                kept_articles.append(a)
                continue
            if "tophub" in source_type:
                kept_articles.append(a)
                continue
            if any(bs in collector or bs in a.get("_source_file", "") for bs in BROAD_SOURCES):
                kept_articles.append(a)
                continue
        
        if ts == "unknown_time" and src_cat in MONITOR_SOURCES:
            kept_articles.append(a)
            continue
        
        # 尝试解析日期
        pub_dt = _parse_date_str(pub_str) if pub_str else None
        
        # ═══ URL 日期二次校验 ═══
        url_date = _extract_url_date(a.get("url", ""))
        if pub_dt and url_date:
            url_dt = _parse_date_str(url_date)
            if url_dt:
                diff_days = abs((pub_dt - url_dt).days)
                src_file = a.get("_source_file", "")
                is_broad = any(bs in src_file or bs in collector or bs in source_type for bs in BROAD_SOURCES)
                cutoff = date_cutoff_loose if is_broad else date_cutoff_strict
                if diff_days > 7 and url_dt < cutoff:
                    logger.warning(
                        f"  🚫 硬日期门: URL日期={url_date} 与 published_at={pub_str[:10]} "
                        f"冲突({diff_days}天)，以URL日期为准 → 拒绝"
                    )
                    rejected_old += 1
                    continue
        
        if pub_dt is not None:
            # Choose cutoff based on source
            src_file = a.get("_source_file", "")
            source_name = a.get("source_name", "")
            is_broad = any(bs in src_file or bs in collector or bs in source_type for bs in BROAD_SOURCES)
            # RSSHub 搜索源使用中间 cutoff（3天）：
            # - 比频道源宽松（允许前天的重要新闻被补充采集）
            # - 比 broad 严格（不让一周前的旧分析混入日报）
            is_search_source = "search" in source_name.lower()
            if is_broad:
                cutoff = date_cutoff_loose       # 7天
            elif is_search_source:
                cutoff = date_cutoff_search      # 3天
            else:
                cutoff = date_cutoff_strict      # 1天
            
            if pub_dt >= cutoff:
                kept_articles.append(a)
                if pub_dt < window_start_dt:
                    kept_near += 1
            else:
                rejected_old += 1
        else:
            # 无日期文章处理：
            # - BROAD_SOURCES (xcrawl/cloakbrowser/tophub/broad_search) 来源的保留
            #   因为搜索引擎/采集端已做时效筛选
            # - RSSHub 搜索源的无日期文章拒绝（collect 阶段已过滤，漏网说明有问题）
            # - 其他来源的拒绝
            src_file = a.get("_source_file", "")
            is_broad = any(bs in src_file or bs in collector or bs in source_type for bs in BROAD_SOURCES)
            if is_broad:
                kept_articles.append(a)
            else:
                rejected_no_date += 1
    
    if kept_articles:
        date_reject_total = len(all_articles) - len(kept_articles)
        logger.info(f"  硬日期门: 丢弃 {date_reject_total} 篇旧闻/无日期文章")
        logger.info(f"    rejected_old={rejected_old}, rejected_no_date={rejected_no_date}, "
                    f"near_cutoff_kept={kept_near}")
        logger.info(f"    保留: {len(kept_articles)} 篇 ({len(kept_articles)/len(all_articles)*100:.1f}%)")
        all_articles = kept_articles
        total_count = len(all_articles)
    else:
        logger.warning(f"  硬日期门: 所有 {len(all_articles)} 篇文章被过滤！")
        logger.warning(f"    保留原始集合以避免空管道（但今日信号质量可能差）")
        # 不修改 all_articles，让它保持原样进入管道

    # ── 写入输出 ──
    output_file = raw_dir / "raw_articles_all.json"
    output_data = {
        "metadata": {
            "version": "1.0",
            "date": date,
            "created_at": datetime.now(CST).isoformat(),
            "total_count": total_count,
            "source_counts": dict(source_counts),
            "file_counts": dict(file_counts),
            "dedup_counts": dict(dedup_counts),
            "date_extraction": {
                "published_at_filled": date_extracted_count,
                "time_status_filled": time_status_filled_count,
            },
            "date_gate": {
                "total_before": len(all_articles) + date_reject_total if kept_articles else len(all_articles),
                "kept": total_count,
                "rejected_old": rejected_old,
                "rejected_no_date": rejected_no_date,
                "near_cutoff_kept": kept_near,
            },
            "merge_sources": [s["file"] for s in MERGE_SOURCES],
            "window_start": (datetime.strptime(date, "%Y-%m-%d").replace(
                hour=7, tzinfo=CST) - timedelta(days=1)).isoformat(),
            "window_end": datetime.strptime(date, "%Y-%m-%d").replace(
                hour=7, tzinfo=CST).isoformat(),
        },
        "articles": all_articles,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    logger.info(f"输出: {output_file}")

    # ── 结果 ──
    result = {
        "ok": True,
        "date": date,
        "output_file": str(output_file),
        "total_count": total_count,
        "rsshub_count": source_counts.get("rsshub_count", 0),
        "rss_count": source_counts.get("rss_count", 0),
        "web_count": source_counts.get("web_count", 0),
        "source_url_monitor_count": source_counts.get("source_url_monitor_count", 0),
        "broad_search_count": source_counts.get("broad_search_count", 0),
        "xcrawl_enriched_count": source_counts.get("xcrawl_enriched_count", 0),
        "tavily_gap_count": source_counts.get("tavily_gap_count", 0),
        "date_extraction": {
            "published_at_filled": date_extracted_count,
            "time_status_filled": time_status_filled_count,
        },
        "date_gate": {
            "total_before": len(all_articles) + rejected_old + rejected_no_date if kept_articles else len(all_articles),
            "kept": total_count,
            "rejected_old": rejected_old,
            "rejected_no_date": rejected_no_date,
            "near_cutoff_kept": kept_near,
        },
        "file_counts": dict(file_counts),
        "dedup_counts": dict(dedup_counts),
        "errors": errors,
    }

    logger.info("=" * 60)
    logger.info(f"合并结果:")
    logger.info(f"  total_count: {total_count}")
    logger.info(f"  rsshub_count: {result['rsshub_count']}")
    logger.info(f"  rss_count: {result['rss_count']}")
    logger.info(f"  web_count: {result['web_count']}")
    logger.info(f"  source_url_monitor_count: {result['source_url_monitor_count']}")
    logger.info(f"  broad_search_count: {result['broad_search_count']}")
    logger.info(f"  xcrawl_enriched_count: {result['xcrawl_enriched_count']}")
    logger.info(f"  tavily_gap_count: {result['tavily_gap_count']}")
    logger.info(f"  date_extraction: published_at_filled={date_extracted_count}, "
                f"time_status_filled={time_status_filled_count}")
    logger.info(f"  errors: {len(errors)}")
    logger.info("=" * 60)

    return result


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="merge_raw_articles — 合并多个采集来源的原始文章")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    date_str = args.date or datetime.now(CST).strftime("%Y-%m-%d")

    result = merge_raw_articles(
        project_root=args.project_root,
        date=date_str,
        verbose=args.verbose,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()