#!/usr/bin/env python3
"""
xcrawl_enrich_articles.py — 为搜索发现的 URL 抓取正文内容

支持多输入源（newly_discovered_urls.json, broad_search_urls.json,
tavily_gap_articles.json），为每个 URL 抓取正文，
输出兼容 raw_articles.json 格式的富文章数据。

用法:
    python xcrawl_enrich_articles.py --project-root /path/to/project --date 2026-05-02
    python xcrawl_enrich_articles.py --project-root /path/to/project --date 2026-05-02 --max-articles 80
    python xcrawl_enrich_articles.py --project-root /path/to/project --date 2026-05-02 --skip-keyword-filter
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections import Counter, OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ── 常量 ──
CST = timezone(timedelta(hours=8))
REQUEST_DELAY_MIN = 0.8   # 每次scrape之间的最小延迟(秒)
REQUEST_DELAY_MAX = 1.8   # 每次scrape之间的最大延迟(秒)
SCRAPE_TIMEOUT = 30        # 单次scrape超时(秒)
MAX_RETRIES_PER_URL = 1    # 单个URL最大重试次数(不含首次)

# 输入文件优先级（按顺序加载，后加载的 URL 被去重跳过）
INPUT_FILES = [
    ("newly_discovered_urls.json", "source_url_monitor"),
    ("broad_search_urls.json", "broad_search_discovery"),
    ("tophub_articles.json", "tophub_collect"),  # TopHub 聚合搜索 → 抓全文
    ("tavily_gap_articles.json", "tavily_gap_search"),
    ("raw_articles.json", "main_collection"),  # RSSHub/XCrawl snippet → 抓全文
]

# 这些输入源的文章不受 dedup_set 限制
# raw_articles.json: dedup_set 本身就从它加载，必须豁免
# tophub_articles.json: TopHub 线索 URL 可能已存在于 raw_articles_all 但没有全文
DEDUP_EXEMPT_FILES = {"raw_articles.json", "tophub_articles.json"}


# ═══════════════════════════════════════════
# XCrawl Key Rotator
# ═══════════════════════════════════════════

class XCrawlKeyRotator:
    """XCrawl API Key 轮换器，支持 7 key round-robin + 用量追踪（线程安全）"""

    def __init__(self, keys: List[str], monthly_limit: int = 1000):
        self.keys = [k for k in keys if k]
        self.monthly_limit = monthly_limit
        self.current_index = 0
        self.call_counts: Dict[str, int] = {k: 0 for k in self.keys}
        self.exhausted_keys: Set[str] = set()
        self.error_counts: Dict[str, int] = {k: 0 for k in self.keys}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        with self._lock:
            return len(self.keys) > 0 and len(self.exhausted_keys) < len(self.keys)

    def get_key(self) -> Optional[str]:
        """获取下一个可用 key（round-robin，线程安全）"""
        if not self.keys:
            return None
        with self._lock:
            for _ in range(len(self.keys)):
                idx = self.current_index % len(self.keys)
                self.current_index += 1
                key = self.keys[idx]
                if key in self.exhausted_keys:
                    continue
                if self.call_counts.get(key, 0) >= self.monthly_limit:
                    self.exhausted_keys.add(key)
                    logging.warning(f"[XCrawl Enrich] Key {_mask_key(key)} 达到月度上限 {self.monthly_limit}")
                    continue
                return key
        return None

    def record_call(self, key: str, credits: int = 1):
        """记录一次调用（线程安全）"""
        with self._lock:
            self.call_counts[key] = self.call_counts.get(key, 0) + credits

    def record_error(self, key: str, is_api_error: bool = False):
        """记录一次错误（线程安全）。只有 is_api_error=True 才计入 exhaustion 阈值。"""
        if not is_api_error:
            return  # 内容级错误不计入 key 健康度
        with self._lock:
            self.error_counts[key] = self.error_counts.get(key, 0) + 1
            # 连续3次 API 错误则标记为exhausted
            if self.error_counts.get(key, 0) >= 3:
                self.exhausted_keys.add(key)
                logging.warning(f"[XCrawl Enrich] Key {_mask_key(key)} 连续 API 错误 ≥3 次，标记为exhausted")

    def status(self) -> dict:
        with self._lock:
            return {
                "total_keys": len(self.keys),
                "exhausted_keys": len(self.exhausted_keys),
                "call_counts": {_mask_key(k): v for k, v in self.call_counts.items()},
                "error_counts": {_mask_key(k): v for k, v in self.error_counts.items()},
            }


def _mask_key(key: str) -> str:
    """遮蔽 API key，只显示前8位和后4位"""
    if not key or len(key) < 12:
        return "***"
    return f"{key[:8]}...{key[-4:]}"


def load_xcrawl_keys(project_root: Path) -> List[str]:
    """从配置文件和环境变量加载 XCrawl keys"""
    import yaml

    config_path = project_root / "config" / "sources.yaml"
    keys = []

    # 优先从配置文件读取环境变量名
    if config_path.exists():
        config = yaml.safe_load(open(config_path, encoding="utf-8"))
        xcrawl_config = config.get("xcrawl", {})
        env_names = xcrawl_config.get(
            "keys_env_vars",
            # 减半到4 key（xcrawl_key1~4，xcrawl_key已耗尽自动跳过）
            ["xcrawl_key1", "xcrawl_key2", "xcrawl_key3", "xcrawl_key4"],
        )
        for name in env_names:
            val = os.environ.get(name, "")
            if val:
                keys.append(val)

    # Fallback: 直接检查环境变量（4 key，预算控制）
    if not keys:
        for name in ["xcrawl_key1", "xcrawl_key2", "xcrawl_key3", "xcrawl_key4"]:
            val = os.environ.get(name, "")
            if val:
                keys.append(val)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return unique


# ═══════════════════════════════════════════
# URL 去重
# ═══════════════════════════════════════════

def normalize_url_for_dedup(url: str) -> str:
    """标准化 URL 用于去重（去除尾部 / 和查询参数）"""
    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"
        return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"
    except Exception:
        return url.rstrip("/")


def load_dedup_set(project_root: Path, date_str: str) -> Set[str]:
    """加载已有文章 URL 用于去重"""
    seen = set()
    raw_dir = project_root / "data" / "raw" / date_str

    # 从 raw_articles.json 去重
    raw_file = raw_dir / "raw_articles.json"
    if raw_file.exists():
        try:
            data = json.load(open(raw_file, encoding="utf-8"))
            articles = data.get("articles", data) if isinstance(data, dict) else data
            for a in articles:
                if isinstance(a, dict) and a.get("url"):
                    seen.add(normalize_url_for_dedup(a["url"]))
        except Exception:
            pass

    # 从 raw_articles_all.json 去重
    all_file = raw_dir / "raw_articles_all.json"
    if all_file.exists():
        try:
            data = json.load(open(all_file, encoding="utf-8"))
            articles = data.get("articles", data) if isinstance(data, dict) else data
            for a in articles:
                if isinstance(a, dict) and a.get("url"):
                    seen.add(normalize_url_for_dedup(a["url"]))
        except Exception:
            pass

    return seen


# ═══════════════════════════════════════════
# URL 筛选
# ═══════════════════════════════════════════

def load_keywords(project_root: Path) -> Set[str]:
    """从 config/keywords.yaml 加载关键词"""
    kw_path = project_root / "config" / "keywords.yaml"
    if not kw_path.exists():
        return set()
    try:
        import yaml
        kw = yaml.safe_load(open(kw_path, encoding="utf-8"))
    except Exception:
        return set()

    keywords = set()
    def _flatten(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                _flatten(v)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, str):
                    keywords.add(item)
                    keywords.add(item.lower())
                elif isinstance(item, dict):
                    _flatten(item)
        elif isinstance(obj, str):
            keywords.add(obj)
            keywords.add(obj.lower())
    _flatten(kw)
    return keywords


def should_enrich(article: dict, skip_keyword_filter: bool = False) -> bool:
    """判断是否值得抓取正文。"""
    title = article.get("title", "").strip()
    url = article.get("url", "").strip()

    # URL 必须有效
    if not url or not url.startswith(("http://", "https://")):
        return False

    # 标题至少 4 个字符
    if len(title) < 4:
        return False

    # ── 跳过分类/列表/导航/产品页面 ──
    # 明显的非文章URL模式
    noise_patterns = [
        r"/login", r"/register", r"/signup", r"/cart", r"/checkout",
        r"/search\?", r"/tag/", r"/page/\d+", r"\.pdf$", r"\.zip$",
    ]
    for pat in noise_patterns:
        if re.search(pat, url, re.IGNORECASE):
            return False

    # 分类/列表页URL模式（不含具体文章ID或日期）
    category_patterns = [
        # 电商分类页
        r"/category/", r"/categories/", r"/cat-\d+", r"/list\?",
        r"/products\?", r"/product-list/", r"/goods\?",
        # 新闻门户分类页
        r"/channel/", r"/subject/", r"/topic/", r"/topics/",
        r"/column/", r"/columns/",
        # 标签聚合页
        r"/tags/", r"/tag/\?", r"/label/",
        # 分页
        r"[\?&]page=\d+", r"/p/\d+$", r"_\d+\.html?$",
        # 搜索结果页
        r"/so\?", r"/search\?", r"/s\?",
        # 首页/Section首页
        r"/(news|article|content|info|finance|tech|digital|beauty|retail)/?$",
        # 纯数字路径（非日期格式）
        r"/\d{3,6}(/|\?)",  # 3-6位纯数字 (不是8位日期)
    ]
    for pat in category_patterns:
        if re.search(pat, url, re.IGNORECASE):
            return False

    # 特例：8位数字路径通常是文章ID，跳过3-6位规则
    if re.search(r"/\d{8}(\.html?|/|$)", url):
        pass  # 合法文章ID，不跳过

    # 合法文章URL指示符（含这些模式不跳过）
    article_indicators = [
        r"/\d{4}/\d{2}/\d{2}/",         # 日期路径 /2026/05/03/
        r"/\d{8}-",                       # 日期前缀 /20260503-
        r"/\d{4}[/_-]\d{1,2}[/_-]\d{1,2}", # 各种日期格式
        r"/article/", r"/post/", r"/blog/",
        r"/news/\d{4}",                   # /news/2026...
        r"/content/", r"/detail/", r"/story/",
        r"/\d{6,}",                        # 6位以上数字 (文章ID)
        r"[?&]id=\d{4,}",                 # ?id=xxx 文章ID
        r"\.html$",                        # .html 结尾常见文章页
        r"/p/\d{6,}",                      # 长数字路径
    ]
    has_article_indicator = any(
        re.search(pat, url) for pat in article_indicators
    )

    # 如果URL看起来像分类页且没有文章指示符，跳过
    # 这个检查很保守：只对明确不含文章模式的URL进行跳过

    # 如果已有 content 且长度 > 200，可以不抓
    existing_content = article.get("content", "") or article.get("summary", "")
    if len(existing_content) > 200:
        return False  # 已有足够内容

    return True


# ═══════════════════════════════════════════
# Scrape
# ═══════════════════════════════════════════

def scrape_url(url: str, api_key: str, timeout: int = SCRAPE_TIMEOUT) -> Optional[dict]:
    """调用 XCrawl scrape API 获取页面正文。
    
    Returns:
        dict with content on success, or dict with _error_type on failure:
        - _error_type="api_error": API key/auth/rate-limit/server issue → count toward key exhaustion
        - _error_type="content_error": page not accessible/timeout → don't count toward key exhaustion
    """
    try:
        from xcrawl import XcrawlClient
        from xcrawl.types import ScrapeOptions

        client = XcrawlClient(api_key=api_key, timeout=timeout)
        response = client.scrape(url, ScrapeOptions(output={"formats": ["markdown", "html"]}))

        result = {
            "url": url,
            "content": "",
            "summary": "",
            "title": "",
            "published_at": None,
            "metadata": {},
            "credits_used": response.get("total_credits_used", 1),
        }

        data = response.get("data", {})
        if isinstance(data, dict):
            # 正文：优先 markdown，其次 html
            markdown_content = data.get("markdown", "")
            html_content = data.get("html", "")
            if markdown_content:
                result["content"] = markdown_content
            elif html_content:
                result["content"] = html_content

            # 元数据
            meta = data.get("metadata", {})
            if isinstance(meta, dict):
                result["metadata"] = {
                    "title": meta.get("title", ""),
                    "description": meta.get("description", ""),
                    "keywords": meta.get("keywords", ""),
                    "author": meta.get("author", ""),
                    "final_url": meta.get("final_url", url),
                    "status_code": meta.get("status_code", 0),
                }
                if meta.get("title") and not result["title"]:
                    result["title"] = meta["title"]
                if meta.get("description") and not result["summary"]:
                    result["summary"] = meta["description"]

        return result

    except Exception as e:
        import requests as _requests
        err_str = str(e)
        err_type = type(e).__name__
        
        # 判断是否为 API 级别错误（而非目标页面问题）
        is_api_error = False
        if hasattr(e, 'response') and e.response is not None:
            status = e.response.status_code
            # API 认证/速率/服务器错误 → 真·API故障
            if status in (401, 403, 429) or status >= 500:
                is_api_error = True
        elif isinstance(e, _requests.exceptions.ConnectionError):
            # 连接不上 XCrawl API 服务器
            is_api_error = True
        elif "auth" in err_str.lower() or "unauthorized" in err_str.lower() or "forbidden" in err_str.lower():
            is_api_error = True
        
        logging.debug(f"Scrape {'API' if is_api_error else '内容'} 异常 {url}: {err_type}: {err_str[:120]}")
        
        return {
            "_error": True,
            "_error_type": "api_error" if is_api_error else "content_error",
            "_error_msg": f"{err_type}: {err_str[:200]}",
        }


def extract_publish_date(content: str, url: str, metadata: dict) -> Optional[str]:
    """从正文、URL、metadata 中提取发布日期"""
    # 1. 从 URL 提取日期
    url_date = _extract_date_from_url(url)
    if url_date:
        return url_date

    # 2. 从正文开头提取日期
    if content:
        patterns = [
            r"(\d{4}年\d{1,2}月\d{1,2}日)",
            r"(\d{4}-\d{1,2}-\d{1,2})",
            r"(\d{4}/\d{1,2}/\d{1,2})",
            r"(\d{4}\.\d{1,2}\.\d{1,2})",
            r"Published[:\s]*(\w+ \d{1,2},? \d{4})",
        ]
        head = content[:500]
        for pat in patterns:
            m = re.search(pat, head)
            if m:
                return m.group(1)

    return None


def _extract_date_from_url(url: str) -> Optional[str]:
    """从 URL path 中提取日期"""
    patterns = [
        r"/(\d{4})/(\d{2})/(\d{2})/",
        r"/(\d{4})(\d{2})(\d{2})",
        r"/(\d{4})-(\d{2})-(\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2000 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y}-{mo:02d}-{d:02d}"
    return None


def compute_time_status(published_at: Optional[str], window_start: datetime,
                        window_end: datetime) -> str:
    """计算文章的时间状态"""
    if not published_at:
        return "unknown_time"
    try:
        dt_str = published_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        elif dt.tzinfo != CST:
            dt = dt.astimezone(CST)

        margin = timedelta(days=2)
        if window_start - margin <= dt <= window_end + margin:
            if window_start <= dt <= window_end:
                return "in_window"
            return "near_window"
        elif dt < window_start - margin:
            return "old"
        else:
            return "near_window"
    except Exception:
        return "unknown_time"


# ═══════════════════════════════════════════
# 构建输出
# ═══════════════════════════════════════════

def _build_enriched_article(
    original: dict,
    content: str,
    summary: str,
    published_at: Optional[str],
    metadata: dict,
    credits_used: int,
    window_start: datetime,
    window_end: datetime,
    enriched_title: Optional[str] = None,
) -> dict:
    """构建兼容 raw_articles.json 格式的富文章"""
    time_status = compute_time_status(published_at, window_start, window_end)
    final_title = enriched_title or original.get("title", "")

    # 保留原始 source 信息
    article = {
        "article_id": original.get("article_id", hashlib.sha256(
            f"{original.get('url', '')}".encode()).hexdigest()[:16]),
        "title": final_title,
        "url": original.get("url", ""),
        "source_name": original.get("source_name", "unknown"),
        "source_type": original.get("source_type", "xcrawl_enriched"),
        "source_tier": original.get("source_tier", "tier3_anchor"),
        "role": original.get("role", "enriched"),
        "category": original.get("category", ""),
        "collector": original.get("collector", "xcrawl_enrich"),
        "published_at": published_at or original.get("published_at", ""),
        "collected_at": original.get("collected_at", datetime.now(CST).isoformat()),
        "discovered_at": original.get("discovered_at", ""),
        "freshness_status": original.get("freshness_status", "enriched"),
        "time_status": time_status,
        "summary": summary or original.get("summary", "") or original.get("content", "")[:500],
        "content": content or original.get("content", ""),
        "matched_keywords": original.get("matched_keywords", []),
        "search_query": original.get("search_query", ""),
        "enrichment": {
            "enriched_at": datetime.now(CST).isoformat(),
            "credits_used": credits_used,
            "content_length": len(content) if content else 0,
            "source_file": original.get("_source_file", ""),
        },
    }

    # 抓取成功时更新 content
    if content:
        article["content"] = content
        article["summary"] = summary or content[:500]

    # 保留原始数据
    if original.get("raw"):
        article["raw"] = original["raw"]

    return article


# ═══════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════

def xcrawl_enrich_articles(
    project_root: str,
    date: str,
    max_articles: int = 50,  # 4 valid keys × ~12 calls/key = budget for ~50 articles
    skip_keyword_filter: bool = False,
    timeout: int = SCRAPE_TIMEOUT,
    verbose: bool = False,
    input_files: Optional[List[str]] = None,
) -> dict:
    """XCrawl Enrich Articles 主函数。
    
    支持多输入源（newly_discovered_urls.json, broad_search_urls.json,
    tavily_gap_articles.json），为每个 URL 抓取正文，
    输出兼容 raw_articles.json 格式的富文章数据。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        max_articles: 最大抓取文章数（控制预算）
        skip_keyword_filter: 跳过关键词筛选
        timeout: 单次 scrape 超时(秒)
        verbose: 详细日志
        input_files: 自定义输入文件列表（None 则使用默认）
    
    Returns:
        结果 dict: ok, date, total_urls, success_count, by_source, by_domain, by_key, ...
    """
    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)

    window_start = datetime.strptime(date, "%Y-%m-%d").replace(hour=7, tzinfo=CST) - timedelta(days=1)
    window_end = datetime.strptime(date, "%Y-%m-%d").replace(hour=7, tzinfo=CST)

    # ── 日志 ──
    log_file = log_dir / "xcrawl_enrich_articles.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    # 移除已有 handlers
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
    logger = logging.getLogger("xcrawl_enrich")
    logger.info(f"XCrawl Enrich Articles 启动 — date={date}, max={max_articles}")

    errors: List[str] = []

    # ── 加载 XCrawl Keys ──
    keys = load_xcrawl_keys(project_root_path)
    if not keys:
        logger.warning("未找到 XCrawl API Keys")
        return {
            "ok": False, "date": date, "error": "无 XCrawl API Keys",
            "total_urls": 0, "success_count": 0,
        }
    rotator = XCrawlKeyRotator(keys)
    logger.info(f"XCrawl Keys: {len(keys)} 个已加载")

    # ── 确定输入文件 ──
    if input_files is None:
        input_files = [f[0] for f in INPUT_FILES]  # 使用默认列表

    # ── 加载已有 URL 用于去重 ──
    dedup_set = load_dedup_set(project_root_path, date)
    logger.info(f"已存在文章 URL 去重集合: {len(dedup_set)} 条")

    # ── 加载多个输入源并去重 ──
    all_candidates = []
    source_counts = Counter()
    seen_urls = set()

    for filename, default_source in INPUT_FILES:
        if filename not in input_files:
            continue
        filepath = raw_dir / filename
        if not filepath.exists():
            logger.info(f"输入文件不存在，跳过: {filename}")
            continue

        try:
            data = json.load(open(filepath, encoding="utf-8"))
            # 支持两种格式: {"articles": [...]} 或 [...]
            if isinstance(data, dict):
                articles = data.get("articles", data.get("urls", []))
                metadata = data.get("metadata", {})
            else:
                articles = data
                metadata = {}
        except Exception as e:
            logger.warning(f"加载 {filename} 失败: {e}")
            continue

        count_before_dedup = len(articles)
        is_dedup_exempt = filename in DEDUP_EXEMPT_FILES
        for a in articles:
            if not isinstance(a, dict):
                continue
            url = a.get("url", "").strip()
            if not url:
                continue
            norm_url = normalize_url_for_dedup(url)
            # 跳过已存在于 raw_articles 的（但 DEDUP_EXEMPT 文件本身不受此限制）
            if not is_dedup_exempt and norm_url in dedup_set:
                continue
            # 跳过同一输入批次内的重复
            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)

            # 标记来源文件
            a["_source_file"] = filename
            # 保留原始 collector 信息
            if "collector" not in a:
                a["collector"] = default_source
            if "source_type" not in a:
                a["source_type"] = default_source

            all_candidates.append(a)
            source_counts[filename] += 1

        logger.info(f"  {filename}: 加载 {count_before_dedup} 条, "
                     f"去重后新增 {source_counts.get(filename, 0)} 条")

    logger.info(f"总候选 URL: {len(all_candidates)} 条 (来源: {dict(source_counts)})")

    if not all_candidates:
        logger.info("无候选 URL，跳过抓取")
        output_data = {
            "metadata": {
                "date": date,
                "total_candidates": 0,
                "total_enriched": 0,
                "success_count": 0,
                "failed_count": 0,
                "skipped_count": 0,
                "source_counts": dict(source_counts),
                "by_domain": {},
                "by_key": {},
            },
            "articles": [],
        }
        output_file = raw_dir / "xcrawl_enriched_articles.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"输出空文件: {output_file}")
        return {
            "ok": True, "date": date, "total_urls": 0,
            "success_count": 0, "failed_count": 0, "skipped_count": 0,
            "enriched_count": 0, "output_file": str(output_file),
            "source_counts": dict(source_counts),
        }

    # ── 筛选值得抓取的 URL ──
    candidates = [a for a in all_candidates if should_enrich(a, skip_keyword_filter=skip_keyword_filter)]
    logger.info(f"筛选后 {len(candidates)} 条值得抓取 "
                 f"(跳过 {len(all_candidates) - len(candidates)} 条已有内容/短标题/无效URL)")

    # 如果超过 max_articles，按优先级排序：
    # 1. tophub/raw_articles 来源（已通过采集端筛选，高相关性）
    # 2. 有关键词匹配的
    # 3. 其他
    if len(candidates) > max_articles:
        def _priority_score(a):
            score = 0
            src_file = a.get("_source_file", "")
            # TopHub 线索最高优先级（已经过搜索筛选）
            if "tophub" in src_file:
                score += 100
            # raw_articles (RSSHub) 次高优先级
            elif "raw_articles" in src_file:
                score += 50
            # 关键词匹配数
            score += len(a.get("matched_keywords", [])) * 10
            return score
        candidates.sort(key=_priority_score, reverse=True)
        logger.info(f"截取前 {max_articles} 条（优先 tophub/rsshub + 关键词匹配）")
        candidates = candidates[:max_articles]

    # ── 并发抓取 ──
    MAX_WORKERS = int(os.environ.get("XCRAWL_ENRICH_WORKERS", "6"))
    PER_SCRAPE_TIMEOUT = timeout  # 复用配置的超时
    
    enriched_results: Dict[int, dict] = {}  # 按索引有序存储
    success_count = 0
    failed_count = 0
    skipped_count = 0
    key_usage: Dict[str, int] = {}
    _key_usage_lock = threading.Lock()
    domain_counts: Counter = Counter()
    _domain_lock = threading.Lock()
    
    def _scrape_one(i: int, article: dict):
        """单篇文章抓取（线程安全），返回 (i, enriched_article, status)"""
        url = article.get("url", "")
        title = article.get("title", "")
        source_name = article.get("source_name", "unknown")
        domain = urlparse(url).netloc
        
        with _domain_lock:
            domain_counts[domain] += 1
        
        logger.info(f"[{i+1}/{len(candidates)}] 抓取: {title[:50]}... ({url[:80]})")
        
        # 获取 API Key（线程安全）
        api_key = rotator.get_key()
        if not api_key:
            logger.warning("所有 XCrawl Keys 已耗尽，停止抓取")
            # 标记所有剩余为跳过
            return i, None, "skipped_no_key"
        
        key_short = _mask_key(api_key)
        with _key_usage_lock:
            key_usage[key_short] = key_usage.get(key_short, 0) + 1
        
        # 抓取
        result = scrape_url(url, api_key=str(api_key), timeout=PER_SCRAPE_TIMEOUT)
        
        if result is None or result.get("_error"):
            is_api = result.get("_error_type") == "api_error" if result else False
            err_msg = result.get("_error_msg", "unknown") if result else "None returned"
            if is_api:
                logger.warning(f"  API错误: {url[:80]} - {err_msg}")
            else:
                logger.debug(f"  内容错误(不计入key): {url[:80]} - {err_msg}")
            rotator.record_error(api_key, is_api_error=is_api)
            # 构建输出（保留原文，content 为空）
            enriched_article = _build_enriched_article(
                article, content="", summary="",
                published_at=None, metadata={},
                credits_used=0, window_start=window_start,
                window_end=window_end,
            )
            return i, enriched_article, "failed"
        
        rotator.record_call(api_key, credits=result.get("credits_used", 1))
        
        # 提取发布日期
        published_at = article.get("published_at", "")
        if not published_at:
            published_at = result.get("published_at")
            if not published_at:
                published_date_str = extract_publish_date(
                    result.get("content", ""), url, result.get("metadata", {}))
                published_at = published_date_str or ""
        
        # 关键词额外匹配
        content_text = result.get("content", "")
        matched_kw = list(article.get("matched_keywords", []))
        if content_text and matched_kw:
            try:
                kw_path = project_root_path / "config" / "keywords.yaml"
                if kw_path.exists():
                    import yaml
                    kw_data = yaml.safe_load(open(kw_path, encoding="utf-8"))
                    all_kw = set()
                    def _flatten(obj):
                        if isinstance(obj, dict):
                            for v in obj.values(): _flatten(v)
                        elif isinstance(obj, list):
                            for item in obj:
                                if isinstance(item, str):
                                    all_kw.add(item.lower())
                    _flatten(kw_data)
                    lower_content = content_text.lower()
                    for kw in all_kw:
                        if kw in lower_content and kw not in [k.lower() for k in matched_kw]:
                            matched_kw.append(kw)
            except Exception:
                pass
        
        # 构建富文章
        enriched_article = _build_enriched_article(
            article,
            content=content_text,
            summary=result.get("summary", ""),
            published_at=published_at,
            metadata=result.get("metadata", {}),
            credits_used=result.get("credits_used", 1),
            window_start=window_start,
            window_end=window_end,
            enriched_title=result.get("title"),
        )
        enriched_article["matched_keywords"] = matched_kw
        
        logger.info(f"  ✓ 抓取成功: {len(content_text)} chars")
        return i, enriched_article, "success"
    
    # ── 执行并发抓取 ──
    n_workers = min(MAX_WORKERS, len(candidates)) if candidates else 1
    logger.info(f"并发抓取 {len(candidates)} 篇 (workers={n_workers}, timeout={PER_SCRAPE_TIMEOUT}s)...")
    
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {}
        for i, article in enumerate(candidates):
            fut = executor.submit(_scrape_one, i, article)
            futures[fut] = i
        
        completed = 0
        for fut in as_completed(futures):
            try:
                i, enriched_article, status = fut.result(timeout=PER_SCRAPE_TIMEOUT + 10)
                completed += 1
                if status == "success":
                    success_count += 1
                    enriched_results[i] = enriched_article
                elif status == "failed":
                    failed_count += 1
                    if enriched_article:
                        enriched_results[i] = enriched_article
                elif status == "skipped_no_key":
                    skipped_count = len(candidates) - completed
                    # 取消未完成的 future
                    for remaining_fut in futures:
                        if remaining_fut not in [f for f in futures if futures[f] <= i]:
                            continue
                        if not remaining_fut.done():
                            remaining_fut.cancel()
                    break
            except Exception as e:
                logger.warning(f"Query {futures[fut]} 异常: {e}")
                failed_count += 1
                completed += 1
            
            if completed % 10 == 0:
                logger.info(f"  进度: {completed}/{len(candidates)} | 成功={success_count} 失败={failed_count}")
    
    # 按原始顺序重建 enriched 列表
    enriched = [enriched_results[i] for i in sorted(enriched_results.keys())]
    
    # skipped: 完全未处理的（不在 enriched_results 中的 candidates 索引）
    processed_indices = set(enriched_results.keys())
    skipped_count = len(candidates) - len(processed_indices)

    # ── 写入输出 ──
    output_file = raw_dir / "xcrawl_enriched_articles.json"

    output_data = {
        "metadata": {
            "date": date,
            "total_candidates": len(all_candidates),
            "candidates_after_filter": len(candidates),
            "total_enriched": len(enriched),
            "success_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "source_counts": dict(source_counts),
            "by_domain": dict(domain_counts),
            "by_key": key_usage,
            "rotator_status": rotator.status(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "created_at": datetime.now(CST).isoformat(),
        },
        "articles": enriched,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # ── 写入统计日志 ──
    stats_file = raw_dir / "xcrawl_enrich_stats.json"
    stats = {
        "ok": True,
        "date": date,
        "total_urls": len(candidates),
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "enriched_count": len(enriched),
        "source_counts": dict(source_counts),
        "by_domain": dict(domain_counts),
        "by_key": key_usage,
        "rotator_status": rotator.status(),
        "output_file": str(output_file),
    }

    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    logger.info("=" * 60)
    logger.info(f"XCrawl Enrich Articles 完成")
    logger.info(f"  候选 URL: {len(candidates)}")
    logger.info(f"  成功: {success_count}")
    logger.info(f"  失败: {failed_count}")
    logger.info(f"  跳过(钥匙耗尽): {skipped_count}")
    logger.info(f"  来源统计: {dict(source_counts)}")
    logger.info(f"  域名统计: {dict(domain_counts)}")
    logger.info(f"  Key 使用: {key_usage}")
    logger.info(f"  输出: {output_file}")
    logger.info("=" * 60)

    return stats


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="XCrawl Enrich Articles — 为搜索发现的 URL 抓取正文")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--max-articles", type=int, default=50,
                        help="最大抓取文章数（控制预算）")
    parser.add_argument("--skip-keyword-filter", action="store_true",
                        help="跳过关键词筛选，对所有URL抓取")
    parser.add_argument("--timeout", type=int, default=SCRAPE_TIMEOUT,
                        help="单次scrape超时(秒)")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    date_str = args.date or datetime.now(CST).strftime("%Y-%m-%d")

    result = xcrawl_enrich_articles(
        project_root=args.project_root,
        date=date_str,
        max_articles=args.max_articles,
        skip_keyword_filter=args.skip_keyword_filter,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()