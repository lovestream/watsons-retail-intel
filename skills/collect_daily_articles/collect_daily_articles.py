#!/usr/bin/env python3
"""
collect_daily_articles.py — 即时零售 × 个护美妆经营情报采集技能 V2

读取 config/sources.yaml（V2 结构）和 config/keywords.yaml，
通过 RSSHub / 原生RSS / 网页抓取 / Tavily搜索 四种方式采集，
统一输出到 data/raw/YYYY-MM-DD/raw_articles.json
日志输出到 data/logs/YYYY-MM-DD/collect_daily_articles.log

支持:
- V2 sources.yaml: rsshub_sources + other_sources
- Tavily Key 轮换（round_robin / failover）
- 采集数量控制（max_total_items_before_dedup 等）

用法:
    python collect_daily_articles.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-04-26 \
        --rsshub-base http://192.168.2.100:1200
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time as time_mod
from collections import OrderedDict

# 确保项目根目录在 sys.path 中，支持 from skills.xxx 导入
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

# ===================== 依赖检查 =====================
_MISSING = []
try:
    import yaml
except ImportError:
    _MISSING.append("PyYAML")

try:
    import requests
except ImportError:
    _MISSING.append("requests")

try:
    import feedparser
except ImportError:
    _MISSING.append("feedparser")

try:
    from bs4 import BeautifulSoup
except ImportError:
    _MISSING.append("beautifulsoup4")

try:
    from dateutil import parser as dateutil_parser
    from dateutil import tz as dateutil_tz
except ImportError:
    _MISSING.append("python-dateutil")

if _MISSING:
    print(
        f"ERROR: 缺少必要依赖: {', '.join(_MISSING)}\n"
        f"请运行: pip install {' '.join(_MISSING)}",
        file=sys.stderr,
    )
    sys.exit(1)

# ===================== 常量 =====================

DEFAULT_RSSHUB_BASE = "http://192.168.2.100:1200"
DEFAULT_TIMEZONE_STR = "Asia/Shanghai"
WINDOW_START_HOUR = 7

MAX_WEB_ARTICLES_PER_SOURCE = 20
DEFAULT_REQUEST_TIMEOUT = 20
DEFAULT_FILTEROUT_DAYS = 90    # RSSHub filterout_time 默认天数：排除90天外的旧文章
USER_AGENT = "WatsonRetailIntelBot/0.2"

CST = dateutil_tz.gettz("Asia/Shanghai")

_METHOD_ALIASES = {
    "web_scrape": "web",
    "scraper": "web",
    "rsshub": "rsshub",
    "rss": "rss",
    "web": "web",
    "tavily": "tavily",
    "xcrawl": "xcrawl",
}


# ===================== Tavily Key 轮换器 =====================


class TavilyKeyRotator:
    """Tavily API Key 轮换器。
    
    支持从多个环境变量名读取 Key，按 round_robin / failover 策略轮换。
    每个 Key 有月度调用上限，超出后自动切换下一个。
    """

    def __init__(self, config: dict):
        """从 tavily 配置段初始化。
        
        config 格式:
          rotation: round_robin
          keys_env_vars: [tavily_key, tavily_key1, tavily_key2]
          monthly_limit_per_key: 1000
        """
        self.rotation = config.get("rotation", "round_robin")
        self.monthly_limit = config.get("monthly_limit_per_key", 1000)
        self.timeout = config.get("timeout_seconds", 30)
        self.max_results = config.get("max_results_per_query", 10)
        self.search_depth = config.get("search_depth", "basic")

        env_var_names = config.get("keys_env_vars", [])
        self.keys: List[str] = []
        self.key_sources: List[str] = []  # 记录每个 key 来自哪个环境变量

        for var_name in env_var_names:
            val = os.environ.get(var_name, "").strip()
            if val:
                self.keys.append(val)
                self.key_sources.append(var_name)

        # 兼容旧的 TAVILY_API_KEY / TAVILY_KEY
        if not self.keys:
            for env_name in ["TAVILY_API_KEY", "TAVILY_KEY"]:
                val = os.environ.get(env_name, "").strip()
                if val:
                    self.keys.append(val)
                    self.key_sources.append(env_name)

        self.current_index = 0
        self.call_counts: Dict[str, int] = {k: 0 for k in self.keys}
        self.exhausted_keys: set = set()

    @property
    def available(self) -> bool:
        return len(self.keys) > 0

    def get_key(self) -> Optional[str]:
        """获取下一个可用 Key。
        
        round_robin: 按 Key 列表顺序轮换
        failover: 当前 Key 未耗尽前一直使用，耗尽后切换
        
        Returns:
            API Key 字符串，或 None（所有 Key 已耗尽）
        """
        if not self.keys:
            return None

        if self.rotation == "failover":
            # failover: 用完当前 Key 再切换
            if self.current_index < len(self.keys):
                key = self.keys[self.current_index]
                if self.call_counts.get(key, 0) < self.monthly_limit:
                    return key
                else:
                    self.exhausted_keys.add(key)
                    # 尝试下一个 Key
                    self.current_index += 1
                    return self.get_key()
            return None

        # round_robin: 按 Key 列表轮换
        tried = 0
        while tried < len(self.keys):
            key = self.keys[self.current_index % len(self.keys)]
            self.current_index = (self.current_index + 1) % len(self.keys)
            if key not in self.exhausted_keys:
                return key
            tried += 1
        return None

    def record_call(self, key: str):
        """记录一次调用。"""
        if key in self.call_counts:
            self.call_counts[key] += 1
        # 检查是否到达月度上限
        if self.call_counts.get(key, 0) >= self.monthly_limit:
            self.exhausted_keys.add(key)

    def mark_exhausted(self, key: str):
        """标记某个 Key 为已耗尽（如 401/403 错误）。"""
        self.exhausted_keys.add(key)

    def get_status(self) -> dict:
        """获取 Key 使用状态摘要。"""
        status = {}
        for i, (key, source) in enumerate(zip(self.keys, self.key_sources)):
            # 只显示 key 的前8位和后4位
            masked = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "***"
            count = self.call_counts.get(key, 0)
            exhausted = key in self.exhausted_keys
            status[f"key_{i}_{source}"] = {
                "masked_key": masked,
                "calls_this_month": count,
                "remaining": max(0, self.monthly_limit - count),
                "exhausted": exhausted,
            }
        return status


# ===================== 配置加载 =====================


def load_yaml(filepath: str) -> dict:
    """加载 YAML 文件，返回字典。"""
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(project_root: str, rel_path: str) -> str:
    """将相对路径解析为绝对路径。"""
    return str(Path(project_root) / rel_path)


def get_rsshub_base(source_config: dict, rsshub_base_arg: Optional[str] = None) -> str:
    """按优先级获取 RSSHub 基础地址。"""
    if rsshub_base_arg:
        return rsshub_base_arg.rstrip("/")
    env_val = os.environ.get("RSSHUB_BASE_URL")
    if env_val:
        return env_val.rstrip("/")
    config_val = source_config.get("rsshub_base")
    if config_val:
        return str(config_val).rstrip("/")
    return DEFAULT_RSSHUB_BASE


def normalize_method(raw_method: str) -> str:
    """标准化采集方式名称。"""
    return _METHOD_ALIASES.get(raw_method, raw_method)


# ===================== 时间窗口 =====================


def compute_time_window(
    date: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    extend_to_now: bool = False,
) -> Tuple[datetime, datetime]:
    """计算采集时间窗口。
    
    默认窗口: (date-1天) 07:00 ~ date 07:00 (Asia/Shanghai)
    绝不跨天——window_end 硬上限为 date 23:59:59。
    extend_to_now 仅在同一天内扩展（如 08:00 运行 → 扩展到 08:00）。
    """
    if start_time and end_time:
        ws = dateutil_parser.parse(start_time)
        we = dateutil_parser.parse(end_time)
        if ws.tzinfo is None:
            ws = ws.replace(tzinfo=CST)
        if we.tzinfo is None:
            we = we.replace(tzinfo=CST)
        return ws, we

    target_date = datetime.strptime(date, "%Y-%m-%d")
    window_start = datetime(
        target_date.year, target_date.month, target_date.day,
        WINDOW_START_HOUR, 0, 0, tzinfo=CST,
    ) - timedelta(days=1)
    window_end = datetime(
        target_date.year, target_date.month, target_date.day,
        WINDOW_START_HOUR, 0, 0, tzinfo=CST,
    )
    # 硬上限: 绝不跨天
    _max_end = datetime(
        target_date.year, target_date.month, target_date.day,
        23, 59, 59, tzinfo=CST,
    )
    
    if extend_to_now:
        now = datetime.now(CST)
        if now > window_end:
            capped = min(now, _max_end)
            if capped != window_end:
                logging.info(f"窗口扩展: window_end 从 {window_end.isoformat()} 扩展到 {capped.isoformat()}")
                window_end = capped
    
    return window_start, window_end


def classify_time_status(
    published_at: Optional[str],
    window_start: datetime,
    window_end: datetime,
) -> str:
    """判断文章发布时间相对于采集窗口的状态。
    
    新增 near_window 状态：窗口前后12小时内。
    """
    if not published_at:
        return "unknown_time"
    try:
        dt = dateutil_parser.parse(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        if dt < window_start:
            # 窗口前12小时内 → near_window，否则 → old
            if dt >= window_start - timedelta(hours=12):
                return "near_window"
            return "old"
        elif dt <= window_end:
            return "in_window"
        else:
            # 窗口后12小时内 → near_window
            if dt <= window_end + timedelta(hours=12):
                return "near_window"
            return "old"
    except (ValueError, TypeError, OverflowError):
        return "unknown_time"


# ===================== URL、ID、关键词 =====================


def normalize_url(url: str) -> str:
    """标准化 URL 用于去重。"""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"
        return urlunparse((scheme, netloc, path, parsed.params, parsed.query, ""))
    except Exception:
        return url


def generate_article_id(url: str, source_name: str, idx: int = 0) -> str:
    """基于 URL + 来源 + 索引生成唯一文章 ID。"""
    base = f"{url or ''}|{source_name}|{idx}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def load_all_keywords(keywords_config: dict) -> List[str]:
    """从 keywords.yaml 提取所有关键词为扁平列表（去重保序）。
    
    注意：YAML 可能将纯数字关键词（如 618）解析为 int，
    此处统一转为 str。
    """
    all_kw = []
    for _category, sub in keywords_config.items():
        if isinstance(sub, dict):
            for _sub_category, words in sub.items():
                if isinstance(words, list):
                    for w in words:
                        all_kw.append(str(w))
        elif isinstance(sub, list):
            for w in sub:
                all_kw.append(str(w))
    return list(dict.fromkeys(all_kw))


def match_keywords(article: dict, all_keywords: List[str]) -> List[str]:
    """在 title + summary + content 中匹配关键词。
    
    关键词统一转为 str 后匹配。
    """
    combined = " ".join([
        article.get("title", "") or "",
        article.get("summary", "") or "",
        article.get("content", "") or "",
    ]).lower()
    matched = []
    for kw in all_keywords:
        kw_str = str(kw)
        if kw_str.lower() in combined:
            matched.append(kw_str)
    return matched


# ===================== HTTP 工具 =====================


def _make_session() -> requests.Session:
    """创建带默认 headers 的 HTTP session。"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    return s


def _safe_get(
    session: requests.Session,
    url: str,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> Optional[requests.Response]:
    """安全的 HTTP GET。"""
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logging.warning(f"HTTP GET 失败: {url} — {e}")
        return None


# ===================== 采集器：RSSHub =====================


def collect_rsshub(
    source: dict,
    rsshub_base: str,
    window_start: datetime,
    window_end: datetime,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    filterout_days: int = DEFAULT_FILTEROUT_DAYS,
) -> Tuple[List[dict], dict]:
    """通过 RSSHub 采集文章。
    
    V2: 支持 source.route（单一路由）和 source.routes（多路由）。
    
    V2.1: 支持 filterout_time 参数 — 自动在 RSSHub URL 中添加
    filterout_time=<seconds>，排除超过 N 天的旧文章。
    源的 filterout_days 配置可覆盖全局默认值；设为 0 则不加此参数。
    
    V2.3: 客户端日期过滤 — RSSHub 的 filterout_time 对部分路由（如 huxiu/search）
    不生效，因此在此处增加客户端二次校验。若 pub_date 早于 filterout_days 
    天前，直接跳过。特别处理：对搜索路由（route 含 "search"），若 pub_date 
    无法解析（uncertain_date/unknown_time），也直接跳过（搜索路由不应返回
    无日期条目，说明 RSSHub 已不稳定或路由本身有问题）。
    
    V2.2: 支持 collection_keywords 参数 — 在采集时就过滤文章。
    如果源配置了 collection_keywords，只有标题或摘要中包含至少一个关键词的文章才会被保留。
    用于全站文章路由等返回大量文章但只需要特定主题的场景。
    """
    articles: List[dict] = []
    stats = {
        "source": source.get("id", source.get("name", "unknown")),
        "collector": "rsshub",
        "success": 0,
        "failed": 0,
        "error": None,
    }

    # V2 格式：route 单路由，routes 多路由
    routes = source.get("routes", [])
    if not routes:
        single_route = source.get("route", "")
        if not single_route:
            stats["error"] = "no route/routes defined"
            logging.warning(f"[RSSHub] {source.get('id', '?')}: 无 route 配置")
            return articles, stats
        routes = [single_route]

    # ── filterout_time 参数 ──
    # 源级配置可覆盖全局默认值，0 表示不加此参数
    source_filterout_days = source.get("filterout_days", filterout_days)
    filterout_seconds = int(source_filterout_days * 86400) if source_filterout_days > 0 else None
    if filterout_seconds and filterout_seconds > 0:
        logging.info(f"[RSSHub] {source.get('id', '?')}: filterout_time={filterout_seconds}s "
                     f"(排除 >{source_filterout_days}天的旧文章)")

    session = _make_session()

    for route in routes:
        feed_url = f"{rsshub_base}{route}"
        # ── 追加 filterout_time 参数 ──
        if filterout_seconds and filterout_seconds > 0:
            sep = "&" if "?" in route else "?"
            feed_url = f"{feed_url}{sep}filterout_time={filterout_seconds}"
        logging.info(f"[RSSHub] {source.get('id', '?')}: 请求 {feed_url}")

        resp = _safe_get(session, feed_url, timeout)
        if not resp:
            stats["failed"] += 1
            continue

        try:
            feed = feedparser.parse(resp.text)
        except Exception as e:
            logging.warning(f"[RSSHub] {source.get('id', '?')}: 解析 RSS 失败 — {e}")
            stats["failed"] += 1
            continue

        for entry in feed.entries:
            try:
                pub_date = entry.get("published", entry.get("updated", ""))
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", entry.get("description", ""))

                content = ""
                if hasattr(entry, "content") and entry.content:
                    if isinstance(entry.content, list) and entry.content:
                        content = entry.content[0].get("value", "")
                    else:
                        content = str(entry.content)
                elif summary:
                    content = summary

                article = {
                    "article_id": generate_article_id(link, source.get("id", source.get("name", ""))),
                    "title": title,
                    "url": link,
                    "source_name": source.get("id", source.get("name", "")),
                    "source_type": "rsshub",
                    "source_tier": _parse_tier(source.get("tier", "tier3_anchor")),
                    "collector": "rsshub",
                    "published_at": pub_date,
                    "collected_at": datetime.now(CST).isoformat(),
                    "time_status": classify_time_status(pub_date, window_start, window_end),
                    "summary": summary or "",
                    "content": content[:50000] if content else "",
                    "matched_keywords": [],
                    "raw": {
                        "feed_url": feed_url,
                        "route": route,
                        "entry_id": getattr(entry, "id", ""),
                    },
                }
                
                # V2.2: 采集时关键词过滤 — 如果源配置了 collection_keywords，
                # 只保留标题或摘要中包含至少一个关键词的文章
                collection_kws = source.get("collection_keywords", [])
                if collection_kws:
                    text_to_check = (title + " " + (summary or "")).lower()
                    if not any(kw.lower() in text_to_check for kw in collection_kws):
                        continue  # 不匹配任何关键词，跳过
                
                # V2.3 客户端日期过滤: RSSHub 的 filterout_time 对 huxiu/search 
                # 等路由不生效，在此二次校验
                if source_filterout_days and source_filterout_days > 0:
                    cutoff_dt = window_start - timedelta(days=source_filterout_days)
                    try:
                        pub_dt = dateutil_parser.parse(pub_date) if pub_date else None
                    except Exception:
                        pub_dt = None
                    is_search_route = "search" in route.lower()
                    if pub_dt is not None:
                        if pub_dt < cutoff_dt:
                            continue  # 超过 filterout_days，丢弃
                    elif is_search_route:
                        # 搜索路由不应返回无日期条目 → 跳过
                        continue
                
                articles.append(article)
                stats["success"] += 1
            except Exception as e:
                logging.warning(f"[RSSHub] {source.get('id', '?')}: 处理条目失败 — {e}")
                stats["failed"] += 1

    return articles, stats


# ===================== 采集器：原生 RSS =====================


def collect_rss(
    source: dict,
    window_start: datetime,
    window_end: datetime,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
) -> Tuple[List[dict], dict]:
    """通过原生 RSS 采集文章。"""
    articles: List[dict] = []
    stats = {
        "source": source.get("id", source.get("name", "unknown")),
        "collector": "rss",
        "success": 0,
        "failed": 0,
        "error": None,
    }

    url = source.get("url", "")
    if not url:
        stats["error"] = "no url defined"
        return articles, stats

    logging.info(f"[RSS] {source.get('id', '?')}: 请求 {url}")
    session = _make_session()
    resp = _safe_get(session, url, timeout)
    if not resp:
        stats["error"] = f"HTTP GET failed for {url}"
        return articles, stats

    try:
        feed = feedparser.parse(resp.text)
    except Exception as e:
        stats["error"] = f"RSS parse failed: {e}"
        logging.warning(f"[RSS] {source.get('id', '?')}: 解析失败 — {e}")
        return articles, stats

    for entry in feed.entries:
        try:
            pub_date = entry.get("published", entry.get("updated", ""))
            title = entry.get("title", "")
            link = entry.get("link", "")
            summary = entry.get("summary", entry.get("description", ""))

            content = ""
            if hasattr(entry, "content") and entry.content:
                if isinstance(entry.content, list) and entry.content:
                    content = entry.content[0].get("value", "")
                else:
                    content = str(entry.content)
            elif summary:
                content = summary

            article = {
                "article_id": generate_article_id(link, source.get("id", source.get("name", ""))),
                "title": title,
                "url": link,
                "source_name": source.get("id", source.get("name", "")),
                "source_type": "rss",
                "source_tier": _parse_tier(source.get("tier", "tier3_anchor")),
                "collector": "rss",
                "published_at": pub_date,
                "collected_at": datetime.now(CST).isoformat(),
                "time_status": classify_time_status(pub_date, window_start, window_end),
                "summary": summary or "",
                "content": content[:50000] if content else "",
                "matched_keywords": [],
                "raw": {
                    "feed_url": url,
                    "entry_id": getattr(entry, "id", ""),
                },
            }
            articles.append(article)
            stats["success"] += 1
        except Exception as e:
            logging.warning(f"[RSS] {source.get('id', '?')}: 处理条目失败 — {e}")
            stats["failed"] += 1

    return articles, stats


# ===================== 采集器：网页抓取 =====================


def _extract_article_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    """从页面中提取候选文章链接。"""
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower()

    links: List[str] = []
    seen: set = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc.lower() != base_domain:
            continue

        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) < 2:
            continue

        norm = normalize_url(full_url)
        if norm in seen:
            continue
        seen.add(norm)
        links.append(norm)

    return links


def _extract_article_content(html: str, url: str) -> Tuple[str, str]:
    """从文章页面提取标题和正文。"""
    try:
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        tag_h1 = soup.find("h1")
        if tag_h1:
            title = tag_h1.get_text(strip=True)
        elif soup.find("title"):
            title = soup.find("title").get_text(strip=True)

        content_parts: List[str] = []
        for selector in ["article", "main", ".content", ".article-content",
                          ".post-content", "#content", ".detail"]:
            container = soup.select_one(selector)
            if container:
                for p in container.find_all(["p", "h2", "h3", "h4"]):
                    text = p.get_text(strip=True)
                    if text and len(text) > 10:
                        content_parts.append(text)
                break

        if not content_parts:
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 10:
                    content_parts.append(text)

        content = "\n\n".join(content_parts)
        return title, content[:50000]
    except Exception as e:
        logging.debug(f"提取正文失败 {url} — {e}")
        return "", ""


def _extract_published_time(soup: BeautifulSoup) -> str:
    """尝试从页面提取发布时间。"""
    for prop in ["article:published_time", "og:article:published_time",
                  "datePublished", "publish_time", "pubdate"]:
        meta = (soup.find("meta", property=prop)
                or soup.find("meta", attrs={"name": prop}))
        if meta and meta.get("content"):
            return meta["content"]

    time_tag = soup.find("time")
    if time_tag:
        dt_val = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if dt_val:
            return dt_val

    for cls in ["date", "time", "publish-time", "pub-time", "article-date",
                "post-date", "article-time"]:
        el = soup.find(class_=cls)
        if el:
            return el.get_text(strip=True)

    return ""


def collect_web(
    source: dict,
    window_start: datetime,
    window_end: datetime,
    timeout: int = DEFAULT_REQUEST_TIMEOUT,
    max_articles: int = MAX_WEB_ARTICLES_PER_SOURCE,
) -> Tuple[List[dict], dict]:
    """通过网页抓取采集文章。"""
    articles: List[dict] = []
    stats = {
        "source": source.get("id", source.get("name", "unknown")),
        "collector": "web",
        "success": 0,
        "failed": 0,
        "error": None,
        "source_url": source.get("url", ""),
    }

    url = source.get("url", "")
    if not url:
        stats["error"] = "no url defined"
        return articles, stats

    logging.info(f"[Web] {source.get('id', '?')}: 抓取首页 {url}")
    session = _make_session()

    resp = _safe_get(session, url, timeout)
    if not resp:
        stats["error"] = f"Failed to fetch index: {url}"
        return articles, stats

    try:
        encoding = resp.encoding or "utf-8"
        html = resp.content.decode(encoding, errors="replace")
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        stats["error"] = f"Failed to parse index: {e}"
        return articles, stats

    article_links = _extract_article_links(soup, url)
    logging.info(f"[Web] {source.get('id', '?')}: 提取到 {len(article_links)} 个候选链接")
    article_links = article_links[:max_articles]

    for i, link in enumerate(article_links):
        try:
            article_resp = _safe_get(session, link, timeout)
            if not article_resp:
                stats["failed"] += 1
                continue

            enc = article_resp.encoding or "utf-8"
            article_html = article_resp.content.decode(enc, errors="replace")
            article_soup = BeautifulSoup(article_html, "html.parser")

            title, content = _extract_article_content(article_html, link)
            pub_date = _extract_published_time(article_soup)
            summary = content[:500] if content else ""

            article = {
                "article_id": generate_article_id(link, source.get("id", source.get("name", "")), i),
                "title": title,
                "url": link,
                "source_name": source.get("id", source.get("name", "")),
                "source_type": "web",
                "source_tier": _parse_tier(source.get("tier", "tier3_anchor")),
                "collector": "web",
                "published_at": pub_date,
                "collected_at": datetime.now(CST).isoformat(),
                "time_status": classify_time_status(pub_date, window_start, window_end),
                "summary": summary,
                "content": content,
                "matched_keywords": [],
                "raw": {
                    "source_url": url,
                    "final_url": link,
                    "content_length": len(content),
                },
            }
            articles.append(article)
            stats["success"] += 1
        except Exception as e:
            logging.warning(f"[Web] {source.get('id', '?')}: 抓取文章失败 {link} — {e}")
            stats["failed"] += 1

    return articles, stats


# ===================== 采集器：百度资讯搜索 =====================

def collect_baidu_news(
    source: dict,
    window_start: datetime,
    window_end: datetime,
    timeout: int = 30,
    max_articles: int = 20,
) -> Tuple[List[dict], dict]:
    """通过百度资讯搜索采集最新新闻（按时间排序）。

    百度资讯搜索(rtt=4)返回按时间排序的最新新闻结果，
    是获取当天即时零售/美妆个控行业新闻的关键来源。

    Args:
        source: 配置字典，需含 search_queries 或 url
        window_start: 时间窗口开始
        window_end: 时间窗口结束
        timeout: 请求超时秒数
        max_articles: 最大文章数
    """
    articles: List[dict] = []
    stats = {
        "source": source.get("id", source.get("name", "unknown")),
        "collector": "baidu_news",
        "success": 0,
        "failed": 0,
        "error": None,
    }

    search_queries = source.get("search_queries", [])
    if not search_queries:
        # 如果没有配置 search_queries，从 url 参数提取
        url = source.get("url", "")
        if "word=" in url:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            word = params.get("word", [""])[0]
            if word:
                search_queries = [word]
        else:
            stats["error"] = "no search_queries and no word in url"
            return articles, stats

    session = _make_session()
    # 百度资讯需要伪装浏览器 UA
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })

    source_id = source.get("id", source.get("name", ""))
    seen_urls: set = set()

    for query in search_queries:
        # 百度资讯搜索 URL: rtt=4表示按时间排序
        from urllib.parse import quote
        baidu_url = (
            f"https://www.baidu.com/s?rtt=4&rn=20&ie=utf-8"
            f"&tn=news&word={quote(query)}"
        )
        logging.info(f"[BaiduNews] {source_id}: 搜索 '{query}'")

        try:
            resp = _safe_get(session, baidu_url, timeout)
            if not resp:
                stats["failed"] += 1
                continue

            # 百度资讯搜索结果页面解析
            enc = resp.encoding or "utf-8"
            html = resp.content.decode(enc, errors="replace")
            soup = BeautifulSoup(html, "html.parser")

            # 百度资讯结果在 div.result 中
            result_divs = soup.find_all("div", class_="result")
            if not result_divs:
                # 备用选择器
                result_divs = soup.find_all("div", attrs={"class": re.compile(r"result|c-container")})

            query_count = 0
            for div in result_divs[:max_articles]:
                try:
                    # 标题
                    title_tag = div.find("h3")
                    if not title_tag:
                        continue
                    title_link = title_tag.find("a")
                    if not title_link:
                        continue
                    title = title_link.get_text(strip=True)
                    link = title_link.get("href", "")
                    if not title or not link:
                        continue

                    # 百度跳转链接需要保留，后续会重定向
                    # 有时是真实链接，有时是 baidu.com/link?url=...
                    norm = normalize_url(link)
                    if norm in seen_urls:
                        continue
                    seen_urls.add(norm)

                    # 摘要
                    content_parts = []
                    # 百度资讯的摘要通常在 class="c-span-last" 或 class="c-abstract" 中
                    abstract = div.find(class_="c-abstract") or div.find(class_="c-span-last")
                    if abstract:
                        content_parts.append(abstract.get_text(strip=True))
                    # 备用：取所有 <span> 中的文本
                    if not content_parts:
                        for span in div.find_all("span"):
                            text = span.get_text(strip=True)
                            if len(text) > 20:
                                content_parts.append(text)

                    content = "\n".join(content_parts) if content_parts else ""

                    # 发布时间：百度在 <span class="c-color-gray"> 或 <p class="c-author"> 中
                    pub_date = ""
                    time_tag = (
                        div.find("span", class_="c-color-gray")
                        or div.find("span", class_="c-font-normal")
                        or div.find("p", class_="c-author")
                        or div.find("span", class_="news-date")
                    )
                    if time_tag:
                        date_text = time_tag.get_text(strip=True)
                        # 百度资讯常见格式: "1小时前", "6小时前", "2026年04月29日 13:50"
                        pub_date = _parse_baidu_date(date_text, window_end)

                    # 来源
                    source_tag = div.find("span", class_="c-color-gray") or div.find("span", class_="c-gap-right")
                    source_name = ""
                    if source_tag:
                        source_text = source_tag.get_text(strip=True)
                        # 格式通常为 "全景网  6小时前" 或 "来源  时间"
                        parts = re.split(r'\s{2,}', source_text)
                        if parts:
                            source_name = parts[0].strip()

                    # 分类时间状态
                    time_status = classify_time_status(pub_date, window_start, window_end)

                    article = {
                        "article_id": generate_article_id(link, f"baidu_news_{query}", len(articles)),
                        "title": title,
                        "url": link,
                        "content": content[:5000] if content else "",
                        "summary": content[:500] if content else "",
                        "published_at": pub_date,
                        "source_name": f"baidu_news_{query}",
                        "source_type": "baidu_news",
                        "time_status": time_status,
                        "collected_at": window_end.isoformat(),
                        "keywords_matched": [],
                        "tier": _parse_tier(source.get("tier", source.get("source_tier", 3))),
                        "role": source.get("role", "direct_signal"),
                        "category": source.get("category", ""),
                        "raw_collector": "baidu_news",
                    }

                    # 关键词匹配
                    all_keywords = load_all_keywords(
                        load_yaml(os.path.join(
                            os.path.dirname(os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__)))),
                            "config", "keywords.yaml"))
                    ) if os.path.exists(os.path.join(
                        os.path.dirname(os.path.dirname(os.path.dirname(
                            os.path.abspath(__file__)))),
                        "config", "keywords.yaml")) else []
                    if all_keywords:
                        article["keywords_matched"] = match_keywords(article, all_keywords)

                    articles.append(article)
                    query_count += 1
                except Exception as e:
                    logging.debug(f"[BaiduNews] 解析结果条目失败: {e}")
                    continue

            logging.info(f"[BaiduNews] {source_id}: '{query}' 搜索到 {query_count} 条结果")
            stats["success"] += query_count

        except Exception as e:
            logging.warning(f"[BaiduNews] {source_id}: 搜索 '{query}' 失败: {e}")
            stats["failed"] += 1

    # 控制总量
    if len(articles) > max_articles:
        # 优先保留时间窗口内的
        in_window = [a for a in articles if a.get("time_status") == "in_window"]
        near_window = [a for a in articles if a.get("time_status") == "near_window"]
        old = [a for a in articles if a.get("time_status") == "old"]
        no_time = [a for a in articles if a.get("time_status") == "unknown_time"]
        articles = (in_window + near_window + no_time + old)[:max_articles]

    logging.info(f"[BaiduNews] {source_id}: 共采集 {len(articles)} 篇文章")
    return articles, stats


def _parse_baidu_date(date_text: str, now: datetime) -> str:
    """解析百度资讯搜索中的时间文本。

    常见格式:
    - "1小时前", "6小时前", "30分钟前"
    - "2026年04月29日 13:50"
    - "4月29日"
    - "昨天 18:30"
    """
    date_text = date_text.strip()
    cst = tz(timedelta(hours=8))

    # 相对时间: X小时前
    m = re.match(r"(\d+)\s*小时前", date_text)
    if m:
        hours = int(m.group(1))
        dt = now.astimezone(cst) - timedelta(hours=hours)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    # 相对时间: X分钟前
    m = re.match(r"(\d+)\s*分钟前", date_text)
    if m:
        minutes = int(m.group(1))
        dt = now.astimezone(cst) - timedelta(minutes=minutes)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    # 相对时间: 昨天 HH:MM
    m = re.match(r"昨天\s+(\d{1,2}):(\d{2})", date_text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        dt = (now.astimezone(cst) - timedelta(days=1)).replace(hour=h, minute=mi, second=0)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    # 绝对时间: 2026年04月29日 13:50
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", date_text)
    if m:
        y, mo, d, h, mi = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
        dt = datetime(y, mo, d, h, mi, tzinfo=cst)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    # 日期: 4月29日
    m = re.match(r"(\d{1,2})月(\d{1,2})日", date_text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        dt = datetime(now.year, mo, d, tzinfo=cst)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    # 尝试 dateutil 解析
    try:
        dt = dateutil_parser.parse(date_text)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return date_text


def tz(offset):
    """创建时区对象。"""
    from datetime import timezone as _tz
    return _tz(offset)


# ===================== 采集器：XCrawl 搜索 =====================

class XCrawlKeyRotator:
    """XCrawl API Key 轮换器。"""

    def __init__(self, keys: List[str], monthly_limit: int = 1000):
        self.keys = [k for k in keys if k]
        self.monthly_limit = monthly_limit
        self.current_index = 0
        self.call_counts: Dict[str, int] = {k: 0 for k in self.keys}
        self.exhausted_keys: set = set()
        self.lock = __import__('threading').Lock()

    @property
    def available(self) -> bool:
        return len(self.keys) > 0 and len(self.exhausted_keys) < len(self.keys)

    def get_key(self) -> Optional[str]:
        with self.lock:
            if not self.keys:
                return None
            for _ in range(len(self.keys)):
                idx = self.current_index % len(self.keys)
                self.current_index += 1
                key = self.keys[idx]
                if key in self.exhausted_keys:
                    continue
                if self.call_counts.get(key, 0) >= self.monthly_limit:
                    self.exhausted_keys.add(key)
                    logging.warning(f"[XCrawl] Key {_mask_key(key)} 已达到月度上限 {self.monthly_limit}")
                    continue
                return key
            return None

    def record_call(self, key: str, count: int = 1):
        self.call_counts[key] = self.call_counts.get(key, 0) + count

    def mark_dead(self, key: str):
        """标记 key 已永久失效（如 auth_failed 401）"""
        with self.lock:
            self.exhausted_keys.add(key)
            logging.warning(f"[XCrawl] Key {_mask_key(key)} 已永久失效，标记跳过")

    def status(self) -> dict:
        return {
            "total_keys": len(self.keys),
            "exhausted": len(self.exhausted_keys),
            "available": len(self.keys) - len(self.exhausted_keys),
            "calls": {_mask_key(k): v for k, v in self.call_counts.items()},
        }


def _mask_key(key: str) -> str:
    """脱敏显示 key 前缀。"""
    if not key or len(key) < 10:
        return key or "?"
    return key[:8] + "..." + key[-4:]


def _extract_date_from_url(url: str) -> Optional[str]:
    """从 URL 路径中提取发布日期。

    常见模式：
    - /2026/05/01/ 或 /2026/05/01
    - /20260501/ 或 /20260501
    - /2026-05-01 或 -2026-05-01-
    - /doc-inxxxyyyzzz (sina style, embedded timestamp)
    """
    import re as _re
    cst = __import__('datetime').timezone(__import__('datetime').timedelta(hours=8))
    dt_mod = __import__('datetime')

    def _fmt(dt):
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    if not url:
        return None

    # /YYYY/MM/DD/ or /YYYY/MM/DD
    m = _re.search(r'/(\d{4})/(\d{1,2})/(\d{1,2})(?:/|$)', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2020 <= y <= 2027 and 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return _fmt(dt_mod.datetime(y, mo, d, tzinfo=cst))
            except ValueError:
                pass

    # /YYYYMMDD/ or /YYYYMMDD (6 consecutive digits that form valid date)
    m = _re.search(r'/(\d{4})(\d{2})(\d{2})(?:/|$|-)', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2020 <= y <= 2027 and 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return _fmt(dt_mod.datetime(y, mo, d, tzinfo=cst))
            except ValueError:
                pass

    # -YYYY-MM-DD- or /YYYY-MM-DD (hyphen-separated date)
    m = _re.search(r'[/-](\d{4})-(\d{1,2})-(\d{1,2})(?:[/-]|$)', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 2020 <= y <= 2027 and 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                return _fmt(dt_mod.datetime(y, mo, d, tzinfo=cst))
            except ValueError:
                pass

    return None


def _extract_date_from_snippet(snippet: str, default_dt: Optional[datetime] = None) -> str:
    """从 XCrawl snippet / description 中提取发布日期。

    XCrawl 不返回标准的 publishedDate 字段，
    但 snippet 开头通常包含 "X hours ago — ..." 或 "X天前 — ..." 或 "2026年4月29日 — ..."。
    若无法提取且提供了 default_dt，则使用默认时间。
    """
    import re
    cst = __import__('datetime').timezone(__import__('datetime').timedelta(hours=8))
    now = __import__('datetime').datetime.now(cst)
    dt_mod = __import__('datetime')

    def _fmt(dt):
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

    if not snippet:
        return _fmt(default_dt) if default_dt else ""

    # ── 英文相对时间 ──
    m = re.match(r"(\d+)\s*hours?\s*ago", snippet, re.IGNORECASE)
    if m:
        hours = int(m.group(1))
        return _fmt(now - dt_mod.timedelta(hours=hours))
    m = re.match(r"(\d+)\s*minutes?\s*ago", snippet, re.IGNORECASE)
    if m:
        minutes = int(m.group(1))
        return _fmt(now - dt_mod.timedelta(minutes=minutes))
    m = re.match(r"(\d+)\s*days?\s*ago", snippet, re.IGNORECASE)
    if m:
        days = int(m.group(1))
        return _fmt(now - dt_mod.timedelta(days=days))

    # ── 中文相对时间 ──
    m = re.search(r"(\d+)\s*天前", snippet)
    if m:
        days = int(m.group(1))
        return _fmt(now - dt_mod.timedelta(days=days))
    m = re.search(r"(\d+)\s*小时前", snippet)
    if m:
        hours = int(m.group(1))
        return _fmt(now - dt_mod.timedelta(hours=hours))
    m = re.search(r"(\d+)\s*分钟前", snippet)
    if m:
        minutes = int(m.group(1))
        return _fmt(now - dt_mod.timedelta(minutes=minutes))

    # ── 绝对日期 ──
    # "2026年4月29日 — ..." / "2026年4月 — ..." / "4月29日 — ..."
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[日号]", snippet)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _fmt(dt_mod.datetime(y, mo, d, tzinfo=cst))
        except ValueError:
            pass
    # "2026年4月" (无日，默认1号)
    m = re.search(r"(\d{4})年(\d{1,2})月(?!\d)", snippet)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        try:
            return _fmt(dt_mod.datetime(y, mo, 1, tzinfo=cst))
        except ValueError:
            pass
    # "2024年" (无月，默认1月1号) — 用于抓取如"2024年上半年营收下滑"等旧年数据
    m = re.search(r"(\d{4})年(?!\d)", snippet)
    if m:
        y = int(m.group(1))
        if 2020 <= y <= 2026:
            try:
                return _fmt(dt_mod.datetime(y, 1, 1, tzinfo=cst))
            except ValueError:
                pass
    # "4月29日" / "4月29号" (无年，用今年)
    m = re.search(r"(?:^|[^\d])(\d{1,2})月(\d{1,2})[日号]", snippet)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            return _fmt(dt_mod.datetime(now.year, mo, d, tzinfo=cst))
        except ValueError:
            pass
    # "2026-04-29"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", snippet)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return _fmt(dt_mod.datetime(y, mo, d, tzinfo=cst))
        except ValueError:
            pass

    # ── 英文日期格式 ──
    # "April 29, 2026" / "Apr 29, 2026" / "29 April 2026"
    en_months = {
        'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
        'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
        'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,
        'sep':9,'oct':10,'nov':11,'dec':12
    }
    # "Month DD, YYYY" or "Month DD YYYY"
    m = re.search(r'(' + '|'.join(en_months.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})', snippet, re.IGNORECASE)
    if m:
        mon = en_months.get(m.group(1).lower())
        d, y = int(m.group(2)), int(m.group(3))
        if mon:
            try:
                return _fmt(dt_mod.datetime(y, mon, d, tzinfo=cst))
            except ValueError:
                pass
    # "DD Month YYYY"
    m = re.search(r'(\d{1,2})\s+(' + '|'.join(en_months.keys()) + r')\s+(\d{4})', snippet, re.IGNORECASE)
    if m:
        d = int(m.group(1))
        mon = en_months.get(m.group(2).lower())
        y = int(m.group(3))
        if mon:
            try:
                return _fmt(dt_mod.datetime(y, mon, d, tzinfo=cst))
            except ValueError:
                pass

    # ── 兜底：无日期信息 → 使用默认时间 ──
    if default_dt:
        return _fmt(default_dt)
    return ""


def collect_xcrawl(
    source: dict,
    key_rotator: "XCrawlKeyRotator",
    window_start: datetime,
    window_end: datetime,
    timeout: int = 30,
) -> Tuple[List[dict], dict]:
    """通过 XCrawl Search API (Python SDK) 采集最新新闻。

    XCrawl 是专门针对中文新闻优化的搜索引擎，
    对百度资讯、新浪、网易等中文新闻源有极好的时效性覆盖。

    Args:
        source: 配置字典，需含 search_queries
        key_rotator: XCrawl Key 轮换器
        window_start: 时间窗口开始
        window_end: 时间窗口结束
        timeout: 单次请求超时秒数
    """
    articles: List[dict] = []
    stats = {
        "source": source.get("id", source.get("name", "unknown")),
        "collector": "xcrawl",
        "success": 0,
        "failed": 0,
        "error": None,
    }

    if not key_rotator.available:
        stats["error"] = "No available XCrawl API Key"
        return articles, stats

    search_queries = source.get("search_queries", [])
    # 兼容单个 query 字段
    if not search_queries and source.get("query"):
        search_queries = [source["query"]]
    if not search_queries:
        stats["error"] = "no search_queries defined"
        return articles, stats

    max_results = source.get("max_items", 10)
    source_id = source.get("id", source.get("name", ""))
    all_keywords = _try_load_keywords()

    seen_urls: set = set()
    global_r_idx = 0  # 跨 query 全局索引

    for query in search_queries:
        api_key = key_rotator.get_key()
        if not api_key:
            stats["error"] = "All XCrawl API Keys exhausted"
            break

        # ── 搜索关键词：不再追加日期后缀 ──
        # XCrawl 作为搜索引擎，短查询 + 不带日期更能命中当天热点新闻
        # 之前把 "2026年5月2日" 追加到 query 会导致搜索结果变窄，反而搜不到当天突发新闻
        enhanced_query = query

        logging.info(f"[XCrawl] {source_id}: 搜索 '{enhanced_query}'")

        try:
            from xcrawl import XcrawlClient
            from xcrawl.types import SearchOptions

            client = XcrawlClient(api_key=api_key, timeout=timeout)
            response = client.search(SearchOptions(
                query=enhanced_query,
                limit=max_results,
                language="zh",
            ))

            key_rotator.record_call(api_key)

            # XCrawl SDK 返回结构: response["data"]["data"] 是结果数组
            data_block = response.get("data", {})
            results = data_block.get("data", [])

            logging.info(f"[XCrawl] {source_id}: '{enhanced_query}' 返回 {len(results)} 条")

            for result in results:
                title = result.get("title", "")
                link = result.get("url", "")
                snippet = result.get("description", "")

                if not title or not link:
                    continue

                # ── 日期提取策略：优先从 description 提取，回退到 URL ──
                # XCrawl 的 description 开头常有 "2026年4月29日 — ..." 或 "3天前 — ..."
                # URL 中也经常包含日期如 /2026/05/01/ 或 /20260501/
                # 不使用 window_end 兜底：搜索返回大量旧文，兜底会让旧文冒充今日新闻
                pub_date = _extract_date_from_snippet(snippet, default_dt=None)
                if not pub_date:
                    pub_date_from_url = _extract_date_from_url(link)
                    if pub_date_from_url:
                        pub_date = pub_date_from_url
                        logging.debug(f"[XCrawl] {source_id}: snippet无日期, 从URL提取: {pub_date[:30]} url={link[:60]}")

                # 去重
                norm = normalize_url(link)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)

                # 时间状态
                time_status = classify_time_status(pub_date, window_start, window_end)

                article = {
                    "article_id": generate_article_id(link, source_id, global_r_idx),
                    "title": title,
                    "url": link,
                    "content": snippet[:5000] if snippet else "",
                    "summary": snippet[:500] if snippet else "",
                    "published_at": pub_date or "",
                    "source_name": source_id,
                    "source_type": "xcrawl",
                    "time_status": time_status,
                    "collected_at": window_end.isoformat(),
                    "keywords_matched": [],
                    "tier": _parse_tier(source.get("tier", source.get("source_tier", 1))),
                    "role": source.get("role", "direct_signal"),
                    "category": source.get("category", ""),
                    "raw_collector": "xcrawl",
                }

                if all_keywords:
                    article["keywords_matched"] = match_keywords(article, all_keywords)

                articles.append(article)
                global_r_idx += 1

            stats["success"] += len(results)

        except Exception as e:
            err_str = str(e).lower()
            if "401" in err_str or "auth" in err_str or "invalid credentials" in err_str:
                key_rotator.mark_dead(api_key)
                logging.warning(f"[XCrawl] {source_id}: Key 已失效 (auth_failed)，标记跳过")
            else:
                logging.warning(f"[XCrawl] {source_id}: 搜索异常 '{query}': {e}")
            stats["failed"] += 1
            continue

    logging.info(f"[XCrawl] {source_id}: 共采集 {len(articles)} 篇文章 ({stats['success']} 成功, {stats['failed']} 失败)")
    return articles, stats

def _try_load_keywords() -> Optional[List[str]]:
    """尝试加载关键词配置。支持嵌套结构（platforms/categories/competitors等）。"""
    try:
        import yaml, os as _os
        config_dir = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
            "config"
        )
        kw_file = _os.path.join(config_dir, "keywords.yaml")
        if _os.path.exists(kw_file):
            with open(kw_file, "r", encoding="utf-8") as f:
                kw_config = yaml.safe_load(f)
            
            # 递归提取所有关键词
            def _flatten(obj):
                if isinstance(obj, str):
                    return [obj]
                if isinstance(obj, list):
                    result = []
                    for item in obj:
                        result.extend(_flatten(item))
                    return result
                if isinstance(obj, dict):
                    result = []
                    for v in obj.values():
                        result.extend(_flatten(v))
                    return result
                return []
            
            all_kw = _flatten(kw_config)
            # 去重并保留顺序
            seen = set()
            unique = []
            for k in all_kw:
                if k not in seen:
                    seen.add(k)
                    unique.append(k)
            return unique
    except Exception:
        pass
    return None


# ===================== 采集器：Tavily 搜索（Key 轮换 + 两级时间范围） =====================

# Tavily 垂直站点映射 — domain-specific query 用
TAVILY_VERTICAL_DOMAINS = {
    "retail": ["36kr.com", "huxiu.com", "jiemian.com", "ebrun.com", "latepost.com",
               "dx.diit.cn", "www.dsb.cn", "www.ls watchdog.com"],
    "beauty": ["ebrun.com", "36kr.com", "huxiu.com", "jiemian.com"],
    "platform": ["36kr.com", "jiemian.com", "ebrun.com"],
}

# 默认 include_domains / exclude_domains
TAVILY_DEFAULT_INCLUDE_DOMAINS = []
TAVILY_DEFAULT_EXCLUDE_DOMAINS = [
    "zhihu.com", "baidu.com", "weibo.com", "douban.com",
    "taobao.com", "jd.com", "tmall.com", "pinduoduo.com",
]


def collect_tavily(
    source: dict,
    key_rotator: TavilyKeyRotator,
    window_start: datetime,
    window_end: datetime,
    timeout: int = 30,
) -> Tuple[List[dict], dict]:
    """通过 Tavily 搜索 API 采集文章，支持 Key 轮换 + 两级时间范围。

    搜索策略:
      1. 首先以 time_range="day" 搜索（默认）
      2. 如果 day 结果不足 min_day_results，再以 time_range="week" 补搜
      3. week 补搜结果标记为 freshness_status="week_fallback"，
         进入 reference 或待验证，不作为今日强信号。

    每条搜索结果都会写入:
      - search_query: 使用的查询词
      - time_range: "day" 或 "week"
      - include_domains / exclude_domains: 使用的域名过滤
      - freshness_status: "day_primary" | "week_fallback"

    Args:
        source: 来源配置字典，需含 search_queries
        key_rotator: Tavily Key 轮换器
        window_start: 时间窗口起始
        window_end: 时间窗口终止
        timeout: 单次请求超时秒数
    """
    from skills.utils.parallel_runner import keyed_parallel_map

    articles: List[dict] = []
    stats = {
        "source": source.get("id", source.get("name", "unknown")),
        "collector": "tavily",
        "success": 0,
        "failed": 0,
        "day_queries": 0,
        "week_queries": 0,
        "day_results": 0,
        "week_results": 0,
        "error": None,
    }

    if not key_rotator.available:
        stats["error"] = "No available Tavily API Key"
        logging.warning(f"[Tavily] {source.get('id', '?')}: 无可用 API Key，跳过")
        return articles, stats

    search_queries = source.get("search_queries", [])
    # 兼容单个 query 字段
    if not search_queries and source.get("query"):
        search_queries = [source["query"]]
    if not search_queries:
        stats["error"] = "no search_queries defined"
        logging.warning(f"[Tavily] {source.get('id', '?')}: 无 search_queries 配置")
        return articles, stats

    # ── 搜索参数 ──
    min_day_results = source.get("min_day_results", 3)  # day 不足此数才降级到 week
    include_domains = source.get("include_domains", TAVILY_DEFAULT_INCLUDE_DOMAINS)
    exclude_domains = source.get("exclude_domains", TAVILY_DEFAULT_EXCLUDE_DOMAINS)
    # 支持 domain_scope 关键词映射到 include_domains
    domain_scope = source.get("domain_scope", "")
    if domain_scope and domain_scope in TAVILY_VERTICAL_DOMAINS and not include_domains:
        include_domains = TAVILY_VERTICAL_DOMAINS[domain_scope]

    # 预分配 key
    tavily_keys = key_rotator.keys if key_rotator.keys else []
    key_list: List[str] = []
    if tavily_keys:
        for i in range(len(search_queries)):
            key_list.append(tavily_keys[i % len(tavily_keys)])
    else:
        key_list = ["_no_key_"] * len(search_queries)

    source_id = source.get("id", source.get("name", ""))

    def _build_tavily_payload(query: str, api_key: str, time_range: str = "day") -> dict:
        """构建 Tavily API 请求体"""
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": key_rotator.search_depth,
            "include_raw_content": "text",   # 使用 "text" 获取纯文本正文
            "include_images": False,
            "max_results": key_rotator.max_results,
            "time_range": time_range,        # "day" | "week" | "month" | "year"
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        return payload

    def _search_one(item, idx, api_key, time_range="day"):
        """单条 Tavily 搜索，用于 keyed_parallel_map"""
        query = item
        if not api_key or api_key == "_no_key_":
            api_key = key_rotator.get_key()
        if not api_key:
            return {"articles": [], "count": 0, "error": "All Tavily API Keys exhausted"}

        try:
            payload = _build_tavily_payload(query, api_key, time_range=time_range)
            resp = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            key_rotator.record_call(api_key)

            results = data.get("results", [])
            query_articles = []
            for r_idx, result in enumerate(results):
                try:
                    title = result.get("title", "")
                    link = result.get("url", "")
                    content = result.get("content", "")
                    raw_content = result.get("raw_content", "")
                    pub_date = result.get("published_date", "")

                    # 日期提取：raw_content → content → URL
                    if not pub_date:
                        pub_date = _extract_date_from_snippet(raw_content or content, default_dt=None)
                    if not pub_date:
                        pub_date_from_url = _extract_date_from_url(link)
                        if pub_date_from_url:
                            pub_date = pub_date_from_url

                    # freshness_status 标记
                    freshness = "day_primary" if time_range == "day" else "week_fallback"

                    article = {
                        "article_id": generate_article_id(link, source_id, r_idx),
                        "title": title,
                        "url": link,
                        "source_name": source_id,
                        "source_type": "tavily",
                        "source_tier": _parse_tier(source.get("tier", "tier3_anchor")),
                        "collector": "tavily",
                        "published_at": pub_date,
                        "collected_at": datetime.now(CST).isoformat(),
                        "time_status": classify_time_status(pub_date, window_start, window_end),
                        "freshness_status": freshness,
                        "summary": content[:500] if content else "",
                        "content": (raw_content or content)[:50000] if (raw_content or content) else "",
                        "matched_keywords": [],
                        "raw": {
                            "query": query,
                            "time_range": time_range,
                            "search_query": query,
                            "include_domains": include_domains,
                            "exclude_domains": exclude_domains,
                            "tavily_score": result.get("score", 0),
                            "tavily_key_used": f"{api_key[:8]}...",
                        },
                    }
                    query_articles.append(article)
                except Exception as e:
                    logging.warning(f"[Tavily] {source_id}: 处理结果失败 — {e}")

            return {"articles": query_articles, "count": len(query_articles), "error": None}

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "N/A"
            if status_code in (401, 403):
                key_rotator.mark_exhausted(api_key)
            return {"articles": [], "count": 0, "error": f"HTTP {status_code}: {e}"}
        except Exception as e:
            return {"articles": [], "count": 0, "error": str(e)}

    # ── 第一轮: time_range="day" ──
    logging.info(f"[Tavily] {source_id}: 第一轮搜索 (time_range=day), "
                f"{len(search_queries)} 条查询")

    if tavily_keys and len(search_queries) > 1:
        day_results, _ = keyed_parallel_map(
            items=search_queries,
            process_fn=lambda item, idx, key: _search_one(item, idx, key, time_range="day"),
            key_list=key_list,
            max_workers=min(6, len(search_queries)),
            max_concurrent_per_key=1,
            timeout=timeout + 15,
            desc=f"Tavily-day {source_id}",
        )
    else:
        day_results = []
        for i, query in enumerate(search_queries):
            r = _search_one(query, i, key_list[i] if i < len(key_list) else "", time_range="day")
            day_results.append(r)

    # 汇总 day 结果
    day_article_count = 0
    for i, result in enumerate(day_results):
        if result is None:
            stats["failed"] += 1
            continue
        query_articles = result.get("articles", [])
        error = result.get("error")
        if error:
            stats["failed"] += 1
            logging.warning(f"[Tavily] {source_id}: day query {i} 失败 — {error}")
        else:
            articles.extend(query_articles)
            day_article_count += len(query_articles)
            stats["day_results"] += len(query_articles)
            stats["success"] += result.get("count", len(query_articles))

    stats["day_queries"] = len(search_queries)
    logging.info(f"[Tavily] {source_id}: day 轮完成, 获得 {day_article_count} 条结果")

    # ── 第二轮: 如果 day 不足, 降级到 time_range="week" ──
    if day_article_count < min_day_results and len(search_queries) > 0:
        logging.info(f"[Tavily] {source_id}: day 仅 {day_article_count} 条 "
                    f"(不足 {min_day_results}), 降级到 week 补搜")

        # 为 week 补搜重新分配 keys
        week_key_list = []
        if tavily_keys:
            for i in range(len(search_queries)):
                key_idx = (len(search_queries) + i) % len(tavily_keys)
                week_key_list.append(tavily_keys[key_idx])
        else:
            week_key_list = ["_no_key_"] * len(search_queries)

        if tavily_keys and len(search_queries) > 1:
            week_results, _ = keyed_parallel_map(
                items=search_queries,
                process_fn=lambda item, idx, key: _search_one(item, idx, key, time_range="week"),
                key_list=week_key_list,
                max_workers=min(6, len(search_queries)),
                max_concurrent_per_key=1,
                timeout=timeout + 15,
                desc=f"Tavily-week {source_id}",
            )
        else:
            week_results = []
            for i, query in enumerate(search_queries):
                r = _search_one(query, i, week_key_list[i] if i < len(week_key_list) else "", time_range="week")
                week_results.append(r)

        # 汇总 week 结果 — 去重 (URL 不能与 day 重复)
        day_urls = {a["url"] for a in articles}
        week_article_count = 0
        for i, result in enumerate(week_results):
            if result is None:
                stats["failed"] += 1
                continue
            query_articles = result.get("articles", [])
            error = result.get("error")
            if error:
                stats["failed"] += 1
                logging.warning(f"[Tavily] {source_id}: week query {i} 失败 — {error}")
            else:
                # 去重: 跳过 day 轮已有的 URL
                new_articles = [a for a in query_articles if a["url"] not in day_urls]
                deduped = len(query_articles) - len(new_articles)
                if deduped > 0:
                    logging.debug(f"[Tavily] {source_id}: week query {i} 去重 {deduped} 条 (day 已有)")
                articles.extend(new_articles)
                week_article_count += len(new_articles)
                stats["week_results"] += len(new_articles)
                stats["success"] += len(new_articles)
                # 将去重后的 URL 加入 day_urls 防止 week 内部也重复
                for a in new_articles:
                    day_urls.add(a["url"])

        stats["week_queries"] = len(search_queries)
        logging.info(f"[Tavily] {source_id}: week 轮完成, 补充 {week_article_count} 条新结果")
    else:
        logging.info(f"[Tavily] {source_id}: day 轮已满足最低要求 ({day_article_count} >= {min_day_results}), 无需 week 补搜")

    logging.info(f"[Tavily] {source_id}: 总计 {len(articles)} 条, "
                f"day={stats['day_results']}, week={stats['week_results']}, "
                f"成功 {stats['success']}, 失败 {stats['failed']}")

    # ── 关键词匹配 ──
    all_keywords = _try_load_keywords()
    if all_keywords:
        for a in articles:
            a["matched_keywords"] = match_keywords(a, all_keywords)

    return articles, stats


# ===================== 辅助：tier 解析 =====================


def _parse_tier(tier_value) -> int:
    """将 tier 字符串/数字统一解析为整数。
    
    tier1 > tier2 > tier3 > tier4
    tier1_direct_signal → 1
    tier2_analysis → 2
    tier3_anchor → 3
    tier4_clue → 4
    数字直传
    """
    if isinstance(tier_value, (int, float)):
        return int(tier_value)
    if isinstance(tier_value, str):
        tier_map = {
            "tier1_direct_signal": 1, "tier1": 1, "direct_signal": 1,
            "tier2_analysis": 2, "tier2": 2, "analysis_signal": 2,
            "tier3_anchor": 3, "tier3": 3, "anchor": 3,
            "tier4_clue": 4, "tier4": 4, "clue_discovery": 4,
            "competitor_signal": 3, "brand_signal": 3,
            "compliance_signal": 3, "platform_signal": 2,
        }
        return tier_map.get(tier_value, 3)
    return 3  # 默认 tier3


# ===================== 去重 =====================


def deduplicate(articles: List[dict]) -> List[dict]:
    """去重：URL 标准化后去重 + 标题非空时标题去重。
    
    同一 URL/tITLE 被多个来源抓到时，保留 source_tier 更低（数值更小=更可信）的一条，
    并在 raw.duplicate_sources 记录被合并的源名称。
    """
    # ---- Pass 1: URL 去重 ----
    by_url: OrderedDict[str, dict] = OrderedDict()

    for article in articles:
        url = article.get("url", "")
        new_tier = article.get("source_tier", 3)

        if not url:
            key = article.get("article_id", "")
            if key in by_url:
                existing = by_url[key]
                existing_tier = existing.get("source_tier", 3)
                if new_tier < existing_tier:
                    _merge_duplicate(article, existing, by_url, key)
                else:
                    _keep_existing(existing, article)
            else:
                by_url[key] = article
            continue

        norm_url = normalize_url(url)

        if norm_url in by_url:
            existing = by_url[norm_url]
            existing_tier = existing.get("source_tier", 3)
            if new_tier < existing_tier:
                _merge_to_new(article, existing, by_url, norm_url)
            else:
                _keep_existing(existing, article)
        else:
            by_url[norm_url] = article

    # ---- Pass 2: 标题去重（仅标题非空时）----
    by_title: Dict[str, dict] = {}
    result: List[dict] = []

    for _norm_url, article in by_url.items():
        title = (article.get("title") or "").strip()
        if not title:
            result.append(article)
            continue

        norm_title = re.sub(r"\s+", "", title.lower())
        if norm_title in by_title:
            existing = by_title[norm_title]
            new_tier = article.get("source_tier", 3)
            existing_tier = existing.get("source_tier", 3)
            if new_tier < existing_tier:
                raw = article.setdefault("raw", {})
                dup_src = existing.get("source_name", "")
                if dup_src:
                    raw.setdefault("duplicate_sources", []).append(dup_src)
                by_title[norm_title] = article
                result = [a for a in result if a.get("article_id") != existing.get("article_id")]
                result.append(article)
            else:
                raw = existing.setdefault("raw", {})
                dup_src = article.get("source_name", "")
                if dup_src:
                    raw.setdefault("duplicate_sources", []).append(dup_src)
        else:
            by_title[norm_title] = article
            result.append(article)

    return result


def _merge_to_new(new_article, old_article, by_url, key):
    """新文章优先级更高，替换旧的。"""
    raw = new_article.setdefault("raw", {})
    dup_src = old_article.get("source_name", "")
    if dup_src:
        raw.setdefault("duplicate_sources", []).append(dup_src)
    by_url[key] = new_article


def _merge_duplicate(new_article, old_article, by_url, key):
    """同 _merge_to_new，只是语义更清晰。"""
    raw = new_article.setdefault("raw", {})
    dup_src = old_article.get("source_name", "")
    if dup_src:
        raw.setdefault("duplicate_sources", []).append(dup_src)
    by_url[key] = new_article


def _keep_existing(existing_article, new_article):
    """保留已有文章，将新文章的来源名记入 duplicate_sources。"""
    raw = existing_article.setdefault("raw", {})
    dup_src = new_article.get("source_name", "")
    if dup_src:
        raw.setdefault("duplicate_sources", []).append(dup_src)


# ===================== 主函数 =====================


def collect_daily_articles(
    project_root: str,
    date: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    rsshub_base: Optional[str] = None,
    sources_file: str = "config/sources.yaml",
    keywords_file: str = "config/keywords.yaml",
) -> dict:
    """采集主函数（V2）。
    
    读取 V2 结构的 sources.yaml（rsshub_sources + other_sources），
    支持 Tavily Key 轮换、采集数量控制。
    """
    errors: List[str] = []

    if not date:
        date = datetime.now(CST).strftime("%Y-%m-%d")

    window_start, window_end = compute_time_window(date, start_time, end_time)
    logging.info(f"采集窗口: {window_start.isoformat()} ~ {window_end.isoformat()}")

    sources_path = resolve_path(project_root, sources_file)
    keywords_path = resolve_path(project_root, keywords_file)
    output_dir = resolve_path(project_root, f"data/raw/{date}")
    log_dir = resolve_path(project_root, f"data/logs/{date}")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    output_file = os.path.join(output_dir, "raw_articles.json")
    log_file = os.path.join(log_dir, "collect_daily_articles.log")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(file_handler)

    logging.info("=" * 60)
    logging.info(f"开始采集 (V2): date={date}")
    logging.info(f"时间窗口: {window_start} ~ {window_end}")
    logging.info(f"项目根目录: {project_root}")
    logging.info("=" * 60)

    # ---- 加载配置 ----
    try:
        sources_config = load_yaml(sources_path)
    except Exception as e:
        error_msg = f"无法加载 sources.yaml: {e}"
        logging.error(error_msg)
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        return {"ok": False, "date": date, "output_file": output_file,
                "log_file": log_file, "total_collected": 0, "total_saved": 0,
                "by_collector": {}, "errors": [error_msg]}

    try:
        keywords_config = load_yaml(keywords_path)
    except Exception as e:
        error_msg = f"无法加载 keywords.yaml: {e}"
        logging.error(error_msg)
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        return {"ok": False, "date": date, "output_file": output_file,
                "log_file": log_file, "total_collected": 0, "total_saved": 0,
                "by_collector": {}, "errors": [error_msg]}

    all_keywords = load_all_keywords(keywords_config)
    logging.info(f"加载 {len(all_keywords)} 个关键词")

    # ---- 采集数量控制 ----
    collection_limits = sources_config.get("collection_limits", {})
    max_items_before_dedup = collection_limits.get("max_total_items_before_dedup", 800)
    max_items_after_dedup = collection_limits.get("max_items_after_dedup", 300)

    # ---- RSSHub 基础地址 ----
    resolved_rsshub_base = get_rsshub_base(sources_config, rsshub_base)
    logging.info(f"RSSHub 基础地址: {resolved_rsshub_base}")

    # ---- 默认超时 ----
    defaults = sources_config.get("defaults", {})
    default_timeout = defaults.get("timeout_seconds", DEFAULT_REQUEST_TIMEOUT)
    global_filterout_days = defaults.get("filterout_days", DEFAULT_FILTEROUT_DAYS)

    # ---- Tavily Key 轮换器 ----
    tavily_config = sources_config.get("tavily", {})
    key_rotator = TavilyKeyRotator(tavily_config)
    if key_rotator.available:
        logging.info(f"Tavily: {len(key_rotator.keys)} 个 Key 已加载，轮换策略: {key_rotator.rotation}")
    else:
        logging.warning("Tavily: 无可用 API Key，Tavily 源将被跳过")

    # ---- XCrawl Key 轮换器 ----
    xcrawl_config = sources_config.get("xcrawl", {})
    xcrawl_keys = []
    for env_name in xcrawl_config.get("keys_env_vars", ["xcrawl_key", "xcrawl_key1", "xcrawl_key2", "xcrawl_key3", "xcrawl_key4", "xcrawl_key5", "xcrawl_key6"]):
        k = os.environ.get(env_name, "")
        if k:
            xcrawl_keys.append(k)
    if not xcrawl_keys:
        # 兼容直接从环境变量读取
        for env_name in ["xcrawl_key", "xcrawl_key1", "xcrawl_key2", "xcrawl_key3", "xcrawl_key4", "xcrawl_key5", "xcrawl_key6"]:
            k = os.environ.get(env_name, "")
            if k:
                xcrawl_keys.append(k)
    xcrawl_rotator = XCrawlKeyRotator(xcrawl_keys, xcrawl_config.get("monthly_limit_per_key", 1000))
    if xcrawl_rotator.available:
        logging.info(f"XCrawl: {xcrawl_rotator.status()['total_keys']} 个 Key 已加载")
    else:
        logging.warning("XCrawl: 无可用 API Key，XCrawl 源将被跳过")

    # ---- 合并所有源 ----
    rsshub_sources = sources_config.get("rsshub_sources", [])
    other_sources = sources_config.get("other_sources", [])

    # 兼容旧版 sources 格式
    legacy_sources = sources_config.get("sources", [])

    all_sources = []
    for s in rsshub_sources:
        # 根据源的 method 字段决定采集方式，而不是硬编码为 rsshub
        src_method = s.get("method", "")
        if src_method in ("xcrawl", "tavily", "rss", "web"):
            s["_method_resolved"] = src_method
        else:
            s["_method_resolved"] = "rsshub"
        all_sources.append(s)
    for s in other_sources:
        s["_method_resolved"] = normalize_method(s.get("method", ""))
        all_sources.append(s)
    for s in legacy_sources:
        if "_method_resolved" not in s:
            s["_method_resolved"] = normalize_method(s.get("method", s.get("type", "")))
        all_sources.append(s)

    enabled_sources = [s for s in all_sources if s.get("enabled", True)]
    logging.info(f"启用源数量: {len(enabled_sources)} (rsshub: {len(rsshub_sources)}, other: {len(other_sources)}, legacy: {len(legacy_sources)})")
    logging.info(f"RSSHub filterout_days 全局默认: {global_filterout_days} 天")

    # ---- 按采集优先级排序（daily_priority 降序）----
    enabled_sources.sort(key=lambda s: s.get("daily_priority", 50), reverse=True)

    # ---- 加载并行配置 ----
    from skills.utils.parallel_runner import load_parallel_config
    _parallel_cfg = load_parallel_config(project_root)
    _source_parallel_cfg = _parallel_cfg.get("collect_daily_articles", {}).get(
        "source_parallel", {})
    _parallel_enabled = _source_parallel_cfg.get("enabled", True)

    # 不同采集方式的并发数
    _workers_map = {
        "rsshub": _source_parallel_cfg.get("rsshub_workers", 6),
        "rss": _source_parallel_cfg.get("rss_workers", 4),
        "web": _source_parallel_cfg.get("web_workers", 2),
        "tavily": _source_parallel_cfg.get("tavily_workers", 6),
        "xcrawl": _source_parallel_cfg.get("xcrawl_workers", 4),
    }
    _source_timeout = _source_parallel_cfg.get("source_timeout", 120)
    _continue_on_error = _source_parallel_cfg.get("continue_on_source_error", True)

    if _parallel_enabled:
        logging.info(f"源级并行采集已启用, workers: {_workers_map}")
    else:
        logging.info("源级并行采集未启用, 使用串行模式")

    # ---- 并行/串行源采集 ----
    all_articles: List[dict] = []
    source_stats: Dict[str, dict] = {}
    by_collector: Dict[str, int] = {}

    # 按 method 分组同类型源，同组内并行
    from collections import defaultdict
    _method_groups: Dict[str, List[Tuple[int, dict]]] = defaultdict(list)
    for _idx, source in enumerate(enabled_sources):
        method = source.get("_method_resolved",
                            normalize_method(source.get("method", source.get("type", ""))))
        _method_groups[method].append((_idx, source))

    # 按 method 组顺序采集（保持 source 配置顺序），组内并行
    _method_order = ["xcrawl", "tavily", "rsshub", "rss", "web", "baidu_news"]
    # 保留不在预期顺序中的 method
    for m in _method_groups:
        if m not in _method_order:
            _method_order.append(m)

    for method in _method_order:
        if method not in _method_groups:
            continue
        group = _method_groups[method]
        if _parallel_enabled and len(group) > 1:
            # ── 并行采集同 method 组 ──
            max_w = min(_workers_map.get(method, 4), len(group))
            logging.info(f"并行采集 [{method}] 组: {len(group)} 个源, max_workers={max_w}")

            def _collect_one(item, idx, _method=method):
                """单源采集函数"""
                _orig_idx, source = item
                source_id = source.get("id", source.get("name", "unknown"))
                timeout = source.get("fetch", {}).get("timeout_seconds", default_timeout)

                start_ts = time_mod.time()
                try:
                    if _method == "rsshub":
                        articles, stats = collect_rsshub(
                            source, resolved_rsshub_base, window_start, window_end, timeout,
                            filterout_days=global_filterout_days,
                        )
                    elif _method == "rss":
                        articles, stats = collect_rss(source, window_start, window_end, timeout)
                    elif _method == "web":
                        max_art = source.get("max_items", MAX_WEB_ARTICLES_PER_SOURCE)
                        articles, stats = collect_web(source, window_start, window_end, timeout, max_art)
                    elif _method == "baidu_news":
                        max_art = source.get("max_items", 20)
                        articles, stats = collect_baidu_news(source, window_start, window_end, timeout, max_art)
                    elif _method == "xcrawl":
                        articles, stats = collect_xcrawl(source, xcrawl_rotator, window_start, window_end, timeout)
                    elif _method == "tavily":
                        tavily_timeout = tavily_config.get("timeout_seconds", timeout)
                        articles, stats = collect_tavily(
                            source, key_rotator, window_start, window_end, tavily_timeout
                        )
                    else:
                        articles = []
                        stats = {"source": source_id, "collector": _method,
                                 "success": 0, "failed": 0, "error": f"unknown method: {_method}"}
                except Exception as e:
                    logging.error(f"采集异常: [{_method}] {source_id} — {e}")
                    articles = []
                    stats = {"source": source_id, "collector": _method,
                             "success": 0, "failed": 0, "error": str(e)}

                elapsed = round(time_mod.time() - start_ts, 2)
                stats["elapsed_seconds"] = elapsed
                logging.info(
                    f"完成采集: [{_method}] {source_id} — "
                    f"成功 {stats.get('success', 0)}, 失败 {stats.get('failed', 0)}, "
                    f"耗时 {elapsed}s"
                )
                return (source_id, _method, articles, stats)

            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=max_w) as executor:
                future_map = {}
                for item in group:
                    future = executor.submit(_collect_one, item, item[0])
                    future_map[future] = item[0]

                for future in as_completed(future_map):
                    idx = future_map[future]
                    try:
                        source_id, m, articles, stats = future.result(timeout=_source_timeout)
                        all_articles.extend(articles)
                        source_stats[source_id] = stats
                        by_collector[m] = by_collector.get(m, 0) + len(articles)
                    except Exception as e:
                        logging.error(f"源采集超时 idx={idx}: {e}")
                        if not _continue_on_error:
                            raise

            # 采集数量控制（组间检查）
            if len(all_articles) >= max_items_before_dedup:
                logging.warning(f"采集数量达到上限 {max_items_before_dedup}，停止采集")
                break

        else:
            # ── 串行采集（单源或不启用并行）──
            for _orig_idx, source in group:
                source_id = source.get("id", source.get("name", "unknown"))
                timeout = source.get("fetch", {}).get("timeout_seconds", default_timeout)

                logging.info(f"开始采集: [{method}] {source_id}")
                start_ts = time_mod.time()

                try:
                    if method == "rsshub":
                        articles, stats = collect_rsshub(
                            source, resolved_rsshub_base, window_start, window_end, timeout,
                            filterout_days=global_filterout_days,
                        )
                    elif method == "rss":
                        articles, stats = collect_rss(source, window_start, window_end, timeout)
                    elif method == "web":
                        max_art = source.get("max_items", MAX_WEB_ARTICLES_PER_SOURCE)
                        articles, stats = collect_web(source, window_start, window_end, timeout, max_art)
                    elif method == "baidu_news":
                        max_art = source.get("max_items", 20)
                        articles, stats = collect_baidu_news(source, window_start, window_end, timeout, max_art)
                    elif method == "xcrawl":
                        articles, stats = collect_xcrawl(source, xcrawl_rotator, window_start, window_end, timeout)
                    elif method == "tavily":
                        tavily_timeout = tavily_config.get("timeout_seconds", timeout)
                        articles, stats = collect_tavily(
                            source, key_rotator, window_start, window_end, tavily_timeout
                        )
                    else:
                        logging.warning(f"未知采集方式: {method} (source: {source_id})")
                        articles = []
                        stats = {"source": source_id, "collector": method,
                                 "success": 0, "failed": 0, "error": f"unknown method: {method}"}
                except Exception as e:
                    logging.error(f"采集异常: [{method}] {source_id} — {e}")
                    articles = []
                    stats = {"source": source_id, "collector": method,
                             "success": 0, "failed": 0, "error": str(e)}

                elapsed = round(time_mod.time() - start_ts, 2)
                stats["elapsed_seconds"] = elapsed
                logging.info(
                    f"完成采集: [{method}] {source_id} — "
                    f"成功 {stats.get('success', 0)}, 失败 {stats.get('failed', 0)}, "
                    f"耗时 {elapsed}s"
                )

                all_articles.extend(articles)
                source_stats[source_id] = stats
                by_collector[method] = by_collector.get(method, 0) + len(articles)

                # 采集数量控制
                if len(all_articles) >= max_items_before_dedup:
                    logging.warning(f"采集数量达到上限 {max_items_before_dedup}，停止采集")
                    break

    total_collected = len(all_articles)
    logging.info(f"总采集数: {total_collected}")

    # ---- 去重 ----
    deduped_articles = deduplicate(all_articles)

    # 截断到上限
    if len(deduped_articles) > max_items_after_dedup:
        logging.warning(f"去重后 {len(deduped_articles)} 条，截断到 {max_items_after_dedup}")
        deduped_articles = deduped_articles[:max_items_after_dedup]

    total_saved = len(deduped_articles)
    logging.info(f"去重后数量: {total_saved}")

    # ── allow_old 过滤：采集阶段不再丢弃 old 文章 ──
    # 旧逻辑：allow_old=false 的源在采集阶段就丢弃 old 文章
    # 新逻辑：所有文章都传给后续 filter 阶段处理，filter 有完善的 time_status 逻辑
    # （搜索源 old → reference/reference，非搜索源 old → reject）
    # 这样搜索源（XCrawl/Tavily/RSSHub搜索）的旧文章可以进入 reference 池作为背景资料
    old_discarded = 0
    total_saved = len(deduped_articles)

    # ---- 时间状态汇总（新增 near_window）----
    time_summary = {"in_window": 0, "near_window": 0, "old": 0, "unknown_time": 0}
    for article in deduped_articles:
        ts = article.get("time_status", "unknown_time")
        time_summary[ts] = time_summary.get(ts, 0) + 1
    logging.info(
        f"时间窗口内: {time_summary.get('in_window', 0)}, "
        f"近窗口: {time_summary.get('near_window', 0)}, "
        f"早于窗口: {time_summary.get('old', 0)}, "
        f"无法识别: {time_summary.get('unknown_time', 0)}"
    )

    # ---- 关键词匹配 ----
    for article in deduped_articles:
        matched = match_keywords(article, all_keywords)
        article["matched_keywords"] = matched

    # ---- Tavily Key 状态 ----
    tavily_status = key_rotator.get_status() if key_rotator.available else {}

    # ---- 保存结果 ----
    output_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "timezone": DEFAULT_TIMEZONE_STR,
            "total_collected": total_collected,
            "total_saved": total_saved,
            "old_discarded_by_allow_old": old_discarded,
            "time_summary": time_summary,
            "by_collector": by_collector,
            "rsshub_base": resolved_rsshub_base,
            "tavily_key_status": tavily_status,
            "collection_limits": collection_limits,
            "collected_at": datetime.now(CST).isoformat(),
        },
        "source_stats": source_stats,
        "articles": deduped_articles,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    logging.info(f"输出文件: {output_file}")
    logging.info("=" * 60)
    logging.info("采集完成汇总:")
    logging.info(f"  总采集数: {total_collected}")
    logging.info(f"  去重后数量: {total_saved}")
    for k, v in time_summary.items():
        logging.info(f"  {k}: {v}")
    for collector, count in by_collector.items():
        logging.info(f"  {collector}: {count}")
    if tavily_status:
        logging.info("  Tavily Key 状态:")
        for k, v in tavily_status.items():
            logging.info(f"    {k}: calls={v['calls_this_month']}, remaining={v['remaining']}, exhausted={v['exhausted']}")
    for src_id, src_stats in source_stats.items():
        logging.info(
            f"  源 [{src_id}]: "
            f"成功 {src_stats.get('success', 0)}, "
            f"失败 {src_stats.get('failed', 0)}, "
            f"耗时 {src_stats.get('elapsed_seconds', 0)}s, "
            f"错误: {src_stats.get('error', 'None')}"
        )
    logging.info("=" * 60)

    logging.getLogger().removeHandler(file_handler)
    file_handler.close()

    return {
        "ok": True,
        "date": date,
        "output_file": output_file,
        "log_file": log_file,
        "total_collected": total_collected,
        "total_saved": total_saved,
        "by_collector": by_collector,
        "tavily_key_status": tavily_status,
        "errors": errors,
    }


# ===================== CLI =====================


def main():
    parser = argparse.ArgumentParser(
        description="即时零售 × 个护美妆经营情报采集 V2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python collect_daily_articles.py \\\n"
            "    --project-root /app/working/projects/watsons-retail-intel \\\n"
            "    --date 2026-04-26 \\\n"
            "    --rsshub-base http://192.168.2.100:1200\n"
        ),
    )
    parser.add_argument("--project-root", required=True, help="项目根目录路径")
    parser.add_argument("--date", default=None, help="采集日期 (YYYY-MM-DD)")
    parser.add_argument("--start-time", default=None, help="覆盖窗口开始时间 (ISO 格式)")
    parser.add_argument("--end-time", default=None, help="覆盖窗口结束时间 (ISO 格式)")
    parser.add_argument("--rsshub-base", default=None, help="RSSHub 基础地址")
    parser.add_argument("--sources-file", default="config/sources.yaml", help="源配置文件路径")
    parser.add_argument("--keywords-file", default="config/keywords.yaml", help="关键词配置文件路径")
    parser.add_argument("--verbose", action="store_true", help="启用详细日志")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    result = collect_daily_articles(
        project_root=args.project_root,
        date=args.date,
        start_time=args.start_time,
        end_time=args.end_time,
        rsshub_base=args.rsshub_base,
        sources_file=args.sources_file,
        keywords_file=args.keywords_file,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()