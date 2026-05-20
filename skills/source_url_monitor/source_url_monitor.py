#!/usr/bin/env python3
"""
Source URL Monitor — 发现新增 URL (V2)

对 config/sources.yaml 中 monitor.enabled=true 的来源，
抓取其列表页/频道页，提取文章链接，与 seen_urls.jsonl 比对，
输出 today newly_discovered URL 列表。

站点适配器架构：
- 每个站点有 SiteParser，优先提取 SSR/JSON 数据
- fallback 到通用 HTML <a> 提取
- 支持 36kr SSR、晚点LatePost、界面新闻 等站点

用法:
    python source_url_monitor.py --project-root /path/to/project --date 2026-05-02
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

# ── HTML 解析器选择 ──
try:
    import lxml  # noqa: F401
    BS_PARSER = "lxml"
except ImportError:
    BS_PARSER = "html.parser"

# ── 常量 ──
CST = timezone(timedelta(hours=8))
REQUEST_TIMEOUT = 30
REQUEST_DELAY_MIN = 2.0   # 最小请求间隔（秒）
REQUEST_DELAY_MAX = 5.0   # 最大请求间隔（秒）
MAX_RETRIES = 2            # 单页最大重试次数
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# 反检测：模拟真实浏览器
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

session = requests.Session()
session.headers.update(BROWSER_HEADERS)

# ── 初始化 session: 访问首页获取 cookies ──

def warm_up_session(domains: List[str]):
    """预先访问各站点首页，获取基础 cookies（如 36kr 的 anti-bot cookies）"""
    for domain in domains:
        url = f"https://{domain}"
        try:
            logging.info(f"预热 session: {url}")
            resp = session.get(url, timeout=15, allow_redirects=True)
            # 检查是否获得了 cookies
            cookies_count = len(session.cookies)
            logging.debug(f"  {domain}: status={resp.status_code}, cookies={cookies_count}")
        except requests.RequestException as e:
            logging.debug(f"  {domain}: 预热失败 {type(e).__name__}: {e}")
        time.sleep(1)


# ═══════════════════════════════════════════
# URL 规范化
# ═══════════════════════════════════════════

UTM_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "from", "is_from_web", "share_token", "share_time", "spm", "tt_from",
    "oid", "ctype", "cid", "refer", "pos", "page", "_ref", "did",
}


def canonicalize_url(url: str) -> str:
    """规范化 URL：去 fragment、去追踪参数、小写域名、去尾 /"""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # 去掉追踪参数
        params = parse_qs(parsed.query, keep_blank_values=True)
        clean_params = {
            k: v for k, v in params.items()
            if k.lower() not in UTM_PARAMS
        }
        sorted_query = urlencode(sorted(clean_params.items()), doseq=True) if clean_params else ""
        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
        return urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))
    except Exception:
        return url.rstrip("/")


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


# ═══════════════════════════════════════════
# 站点适配器
# ═══════════════════════════════════════════

class SiteParser:
    """基类：站点适配器"""
    def can_parse(self, source_id: str, url: str) -> bool:
        raise NotImplementedError

    def parse(self, html: str, final_url: str, source: dict) -> List[Dict]:
        """返回 [{url, title, list_page_url}]"""
        raise NotImplementedError


class Kr36Parser(SiteParser):
    """36kr — 提取 SSR initialState 数据"""
    def can_parse(self, source_id: str, url: str) -> bool:
        return "36kr.com" in url

    def parse(self, html: str, final_url: str, source: dict) -> List[Dict]:
        results = []

        # 尝试从 SSR initialState 提取
        ssr_data = self._extract_ssr(html)
        if ssr_data:
            items = self._parse_ssr_items(ssr_data, final_url)
            if items:
                results.extend(items)
                return results

        # Fallback: 通用 <a> 提取
        return self._fallback_html(html, final_url)

    def _extract_ssr(self, html: str) -> Optional[dict]:
        m = re.search(r'window\.initialState\s*=\s*(\{.+?\})\s*</script>', html, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    def _parse_ssr_items(self, state: dict, final_url: str) -> List[Dict]:
        """从 SSR 数据中提取文章列表"""
        items = []

        # 新闻快讯页: newsflashCatalogData.data.newsflashList.data.itemList
        try:
            catalog = state.get("newsflashCatalogData", {})
            data = catalog.get("data", {})
            flash_list = data.get("newsflashList", {})
            flash_data = flash_list.get("data", {})
            item_list = flash_data.get("itemList", [])
            for item in item_list:
                material = item.get("templateMaterial", {})
                title = material.get("widgetTitle", "")
                item_id = item.get("itemId", "")
                if item_id and title:
                    items.append({
                        "url": f"https://36kr.com/p/{item_id}",
                        "title": title.strip(),
                        "list_page_url": final_url,
                    })
        except (AttributeError, TypeError):
            pass

        # 信息流页面: information.informationList.itemList
        try:
            info = state.get("information", {})
            info_list = info.get("informationList", {})
            item_list2 = info_list.get("itemList", [])
            for item in item_list2:
                material = item.get("templateMaterial", {})
                title = material.get("widgetTitle", "")
                item_id = item.get("itemId", "")
                if item_id and title:
                    url_str = f"https://36kr.com/p/{item_id}"
                    # Dedup
                    if not any(r["url"] == url_str for r in items):
                        items.append({
                            "url": url_str,
                            "title": title.strip(),
                            "list_page_url": final_url,
                        })
        except (AttributeError, TypeError):
            pass

        return items

    def _fallback_html(self, html: str, final_url: str) -> List[Dict]:
        """从 <a> 标签提取 /p/ 链接"""
        soup = BeautifulSoup(html, BS_PARSER)
        results = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/p/" not in href:
                continue
            abs_url = urljoin(final_url, href)
            canon = canonicalize_url(abs_url)
            if canon in seen:
                continue
            seen.add(canon)
            title = a.get_text(strip=True)
            results.append({"url": canon, "title": title, "list_page_url": final_url})
        return results


class LatepostParser(SiteParser):
    """晚点LatePost — 提取 /news/dj_detail?id= 链接"""
    def can_parse(self, source_id: str, url: str) -> bool:
        return "latepost.com" in url

    def parse(self, html: str, final_url: str, source: dict) -> List[Dict]:
        soup = BeautifulSoup(html, BS_PARSER)
        results = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            abs_url = urljoin(final_url, href)
            # LatePost 文章链接: /news/dj_detail?id=xxx
            if "/news/dj_detail" not in abs_url and "/news/" not in abs_url:
                continue
            canon = canonicalize_url(abs_url)
            if canon in seen:
                continue
            seen.add(canon)
            title = a.get_text(strip=True)
            if len(title) > 2:  # 至少有标题
                results.append({"url": canon, "title": title, "list_page_url": final_url})

        return results


class JiemianParser(SiteParser):
    """界面新闻 — 提取 /article/xxx.html 链接"""
    def can_parse(self, source_id: str, url: str) -> bool:
        return "jiemian.com" in url

    def parse(self, html: str, final_url: str, source: dict) -> List[Dict]:
        soup = BeautifulSoup(html, BS_PARSER)
        results = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            abs_url = urljoin(final_url, href)
            if "/article/" not in abs_url:
                continue
            canon = canonicalize_url(abs_url)
            if canon in seen:
                continue
            seen.add(canon)
            title = a.get_text(strip=True)
            if len(title) > 3:
                results.append({"url": canon, "title": title, "list_page_url": final_url})

        return results


class EbrunParser(SiteParser):
    """亿邦动力 — 提取带日期或 /detail_ 的链接"""
    def can_parse(self, source_id: str, url: str) -> bool:
        return "ebrun.com" in url

    def parse(self, html: str, final_url: str, source: dict) -> List[Dict]:
        soup = BeautifulSoup(html, BS_PARSER)
        results = []
        seen = set()
        include_pats = source.get("monitor", {}).get("link_include_patterns", [])

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href:
                continue
            abs_url = urljoin(final_url, href)
            # 检查 include patterns
            matched = False
            for pat in include_pats:
                if pat in abs_url:
                    matched = True
                    break
            if not matched:
                continue
            canon = canonicalize_url(abs_url)
            if canon in seen:
                continue
            seen.add(canon)
            title = a.get_text(strip=True)
            if len(title) > 3:
                results.append({"url": canon, "title": title, "list_page_url": final_url})

        return results


class GenericParser(SiteParser):
    """通用 HTML 解析器 — 用配置的 include/exclude patterns"""
    def can_parse(self, source_id: str, url: str) -> bool:
        return True  # 兜底

    def parse(self, html: str, final_url: str, source: dict) -> List[Dict]:
        monitor = source.get("monitor", {})
        include_patterns = monitor.get("link_include_patterns", [])
        exclude_patterns = monitor.get("link_exclude_patterns", [])
        return extract_links(html, final_url, include_patterns, exclude_patterns)


# 所有适配器，按优先级排序
SITE_PARSERS = [
    Kr36Parser(),
    LatepostParser(),
    JiemianParser(),
    EbrunParser(),
    GenericParser(),
]


def get_parser(source_id: str, url: str) -> SiteParser:
    for parser in SITE_PARSERS:
        if parser.can_parse(source_id, url):
            return parser
    return GenericParser()


# ═══════════════════════════════════════════
# 通用链接提取
# ═══════════════════════════════════════════

def extract_links(
    html: str,
    base_url: str,
    include_patterns: List[str],
    exclude_patterns: List[str],
    min_title_len: int = 2,
) -> List[Dict]:
    """从 HTML 中提取符合 include/exclude 的链接"""
    soup = BeautifulSoup(html, BS_PARSER)
    links = []
    seen_canonical = set()

    for a_tag in soup.find_all("a", href=True):
        raw_href = a_tag["href"].strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        try:
            abs_url = urljoin(base_url, raw_href)
        except Exception:
            continue

        if not abs_url.startswith(("http://", "https://")):
            continue

        canon = canonicalize_url(abs_url)
        if not canon or canon in seen_canonical:
            continue
        seen_canonical.add(canon)

        # Include patterns
        include_match = not include_patterns
        for pat in include_patterns:
            if re.search(pat, canon):
                include_match = True
                break
        if not include_match:
            continue

        # Exclude patterns
        exclude_match = False
        for pat in exclude_patterns:
            if re.search(pat, canon):
                exclude_match = True
                break
        if exclude_match:
            continue

        link_text = a_tag.get_text(strip=True)
        if len(link_text) > 200:
            link_text = link_text[:200]

        # 跳过标题太短的链接（大概率是导航栏）
        if len(link_text) < min_title_len:
            continue

        links.append({
            "url": canon,
            "title": link_text,
            "raw_href": raw_href,
            "list_page_url": base_url,
        })

    return links


# ═══════════════════════════════════════════
# 列表页抓取
# ═══════════════════════════════════════════

def fetch_page(url: str, timeout: int = REQUEST_TIMEOUT, retries: int = MAX_RETRIES) -> Optional[Tuple[str, str]]:
    """抓取页面 HTML，返回 (final_url, html) 或 None。
    包含反检测：随机延迟、验证码检测、重试。"""
    import random

    for attempt in range(retries + 1):
        try:
            # 重试时增加随机延迟
            if attempt > 0:
                delay = random.uniform(3, 6)
                logging.info(f"重试 {url} (attempt {attempt+1}/{retries+1})，等待 {delay:.1f}s")
                time.sleep(delay)

            resp = session.get(url, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()

            final_url = resp.url
            html = resp.text

            # 检测反爬页面（验证码/JS challenge）
            anti_crawl_indicators = [
                "captcha", "验证码", "sec_sdk_build", "anti_bot",
                "cf-challenge", "challenge-platform", "js_challenge",
                "请输入验证码", "检测到异常",
            ]
            html_lower = html.lower()
            page_size = len(html)

            # 极小页面通常是反爬
            if page_size < 2000:
                is_anti = any(ind in html_lower for ind in anti_crawl_indicators)
                if is_anti or page_size < 1000:
                    logging.warning(f"检测到反爬页面 {url} (size={page_size})")
                    if attempt < retries:
                        continue
                    return None

            return final_url, html

        except requests.RequestException as e:
            logging.warning(f"抓取失败 {url} (attempt {attempt+1}): {type(e).__name__}: {e}")
            if attempt < retries:
                continue
            return None

    return None


# ═══════════════════════════════════════════
# Seen URLs Ledger
# ═══════════════════════════════════════════

def load_seen_urls(ledger_path: Path) -> Tuple[Dict[str, dict], bool]:
    """加载见过的 URL ledger。

    Returns:
        (seen_dict, is_bootstrap): seen_dict 为 URL→entry 映射,
        is_bootstrap 为 True 表示 ledger 为空（首次运行）。
    """
    seen = {}
    if not ledger_path.exists() or ledger_path.stat().st_size == 0:
        logging.info("seen_urls.jsonl 不存在或为空 → bootstrap 模式")
        return seen, True  # bootstrap: ledger 为空
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                url = entry.get("canonical_url", "")
                if url:
                    seen[url] = entry
            except json.JSONDecodeError:
                logging.warning(f"seen_urls.jsonl 第 {line_num} 行 JSON 解析失败")
    is_bootstrap = len(seen) == 0
    if is_bootstrap:
        logging.info("seen_urls.jsonl 有内容但0条有效记录 → bootstrap 模式")
    return seen, is_bootstrap


def append_seen_urls(ledger_path: Path, new_entries: List[dict]):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        for entry in new_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════
# 关键词匹配
# ═══════════════════════════════════════════

def _load_keywords(project_root: Path) -> Set[str]:
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


def match_keywords(text: str, keywords: Set[str]) -> List[str]:
    if not text or not keywords:
        return []
    text_lower = text.lower()
    return sorted({kw for kw in keywords if kw.lower() in text_lower})


# ═══════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════

def monitor_source(
    source: dict,
    seen_urls: Dict[str, dict],
    all_keywords: Set[str],
    window_end: datetime,
    idx_counter: list,
    is_bootstrap: bool = False,
) -> Tuple[List[dict], dict]:
    source_id = source.get("id", "unknown")
    monitor = source.get("monitor", {})

    stats = {
        "source": source_id,
        "total_links": 0,
        "new_links": 0,
        "seen_links": 0,
        "failed_links": 0,
        "duration_seconds": 0,
        "errors": [],
    }

    start_time = time.time()
    all_new_articles = []

    for idx, list_url in enumerate(monitor.get("list_urls", [])):
        # 同一来源多个列表页之间加延迟，避免被限流
        if idx > 0:
            import random
            delay = random.uniform(1.5, 3.0)
            logging.debug(f"[{source_id}] 列表页间隔 {delay:.1f}s")
            time.sleep(delay)

        logging.info(f"[{source_id}] 抓取列表页: {list_url}")

        result = fetch_page(list_url)
        if result is None:
            stats["failed_links"] += 1
            stats["errors"].append(f"抓取失败: {list_url}")
            continue

        final_url, html = result

        # 选择适配器
        parser = get_parser(source_id, final_url)
        logging.info(f"[{source_id}] 使用 {parser.__class__.__name__} 解析 {list_url}")

        try:
            links = parser.parse(html, final_url, source)
        except Exception as e:
            logging.warning(f"[{source_id}] 解析异常: {type(e).__name__}: {e}")
            stats["errors"].append(f"解析异常: {list_url}: {e}")
            continue

        logging.info(f"[{source_id}] 从 {list_url} 提取 {len(links)} 个链接")

        for link in links:
            stats["total_links"] += 1
            url = link.get("url", link.get("canonical_url", ""))
            title = link.get("title", "")
            list_page = link.get("list_page_url", list_url)

            if not url:
                continue

            canon = canonicalize_url(url)

            if canon in seen_urls:
                stats["seen_links"] += 1
                continue

            # 新发现 URL
            idx_counter[0] += 1
            date_str = window_end.strftime("%Y-%m-%d")
            discovered_at = window_end.isoformat()

            # 关键词匹配
            # 如果源配置了 strict_keywords，优先使用（更精确的过滤）
            combined_text = f"{title} {canon}"
            strict_kws = source.get("strict_keywords", [])
            if strict_kws:
                strict_set = set(strict_kws)
                matched_kw = match_keywords(combined_text, strict_set)
            else:
                matched_kw = match_keywords(combined_text, all_keywords)

            # Bootstrap 模式: 所有 URL 都是 bootstrap_seen，并非真正新增
            # 正常模式: 新 URL 才是 newly_discovered
            freshness = "bootstrap_seen" if is_bootstrap else "newly_discovered"

            article = {
                "article_id": f"N{date_str.replace('-', '')}_{idx_counter[0]:04d}",
                "title": title,
                "url": canon,
                "source_name": source_id,
                "source_type": "web_monitor",
                "source_tier": source.get("source_tier", source.get("tier", 2)),
                "collector": "source_url_monitor",
                "published_at": None,
                "discovered_at": discovered_at,
                "first_seen_at": discovered_at,
                "time_status": "unknown_time",
                "freshness_status": freshness,
                "summary": "",
                "content": "",
                "matched_keywords": matched_kw,
                "role": source.get("role", "primary_timeline"),
                "category": source.get("category", ""),
                "raw": {
                    "list_page_url": list_page,
                    "link_text": title,
                    "content_hash": content_hash(title + canon),
                },
            }

            all_new_articles.append(article)
            stats["new_links"] += 1

            # 记入 ledger
            seen_urls[canon] = {
                "canonical_url": canon,
                "first_seen_at": discovered_at,
                "source_name": source_id,
                "list_page_url": list_page,
                "title": title,
            }

    stats["duration_seconds"] = round(time.time() - start_time, 2)
    return all_new_articles, stats


def main():
    parser = argparse.ArgumentParser(description="Source URL Monitor — 发现新增 URL")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", help="日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    project_root = Path(args.project_root)
    date_str = args.date or datetime.now(CST).strftime("%Y-%m-%d")
    window_end = datetime.now(CST)

    # ── 日志 ──
    log_dir = project_root / "data" / "logs" / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "source_url_monitor.log"
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.info(f"Source URL Monitor V2 启动 — date={date_str}")

    # ── 加载配置 ──
    import yaml
    config_path = project_root / "config" / "sources.yaml"
    if not config_path.exists():
        logging.error(f"配置文件不存在: {config_path}")
        sys.exit(1)
    sources_config = yaml.safe_load(open(config_path, encoding="utf-8"))

    # ── 收集所有含 monitor.enabled=true 的来源 ──
    all_sources = []
    for key in ["rsshub_sources", "other_sources", "web_monitor_sources"]:
        all_sources.extend(sources_config.get(key, []))

    monitor_sources = [s for s in all_sources if s.get("monitor", {}).get("enabled", False)]

    logging.info(f"找到 {len(monitor_sources)} 个已启用监控的来源")
    for s in monitor_sources:
        urls = s.get("monitor", {}).get("list_urls", [])
        logging.info(f"  {s.get('id', '?')}: {len(urls)} 个列表页")

    if not monitor_sources:
        logging.warning("没有已启用监控的来源，退出")
        output_dir = project_root / "data" / "raw" / date_str
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "newly_discovered_urls.json"
        output_file.write_text("[]", encoding="utf-8")
        logging.info(f"输出空文件: {output_file}")
        return

    # ── 加载关键词和 seen URLs ──
    all_keywords = _load_keywords(project_root)
    logging.info(f"加载关键词 {len(all_keywords)} 个")

    ledger_path = project_root / "data" / "source_ledger" / "seen_urls.jsonl"
    seen_urls, is_bootstrap = load_seen_urls(ledger_path)
    logging.info(f"已见 URL: {len(seen_urls)} 条 | bootstrap_mode: {is_bootstrap}")

    # ── 预热 session ──
    domains = set()
    for s in monitor_sources:
        for lu in s.get("monitor", {}).get("list_urls", []):
            try:
                parsed = urlparse(lu)
                domains.add(parsed.netloc.replace("www.", ""))
            except Exception:
                pass
    if domains:
        warm_up_session(list(domains))

    # ── 逐来源监控 ──
    idx_counter = [0]
    all_new_articles = []
    all_stats = []

    for s in monitor_sources:
        source_id = s.get("id", "unknown")
        try:
            articles, stats = monitor_source(s, seen_urls, all_keywords, window_end, idx_counter, is_bootstrap=is_bootstrap)
            all_new_articles.extend(articles)
            all_stats.append(stats)
            logging.info(
                f"[{source_id}] 完成: total={stats['total_links']}, "
                f"new={stats['new_links']}, seen={stats['seen_links']}, "
                f"failed={stats['failed_links']}, duration={stats['duration_seconds']}s"
            )
        except Exception as e:
            logging.error(f"[{source_id}] 异常: {type(e).__name__}: {e}", exc_info=True)
            all_stats.append({
                "source": source_id,
                "total_links": 0, "new_links": 0, "seen_links": 0,
                "failed_links": 0, "duration_seconds": 0,
                "errors": [f"异常: {type(e).__name__}: {e}"],
            })
        # 来源间随机延迟，模拟人类行为
        import random
        delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        logging.debug(f"等待 {delay:.1f}s 后抓取下一个来源")
        time.sleep(delay)

    # ── 关键词相关性过滤 ──
    # 使用"核心关键词"过滤：只有匹配到行业核心词的文章才保留
    # 避免通用词（政策/曝光/流量）误匹配无关新闻
    CORE_KEYWORDS = {
        '即时零售', '闪购', '小时达', '到家', '外卖', '即时配送', '即配',
        '美妆', '个护', '护肤', '彩妆', '防晒', '洗护',
        '屈臣氏', '名创优品', '丝芙兰', '话梅', '调色师',
        '美团闪购', '美团到家', '京东到家', '京东秒送', '淘宝闪购', '饿了么',
        '抖音小时达', '抖音即时零售', '抖音本地生活',
        '前置仓', '闪电仓', '本地生活', '新零售', '社区团购',
        '盒马', '朴朴', '叮咚买菜', '永辉', '山姆',
        '零售', '门店', '便利店', '药店',
    }
    before_filter = len(all_new_articles)
    relevant_articles = []
    for a in all_new_articles:
        matched = a.get("matched_keywords", [])
        # 检查是否匹配了至少一个核心关键词
        if any(kw in CORE_KEYWORDS for kw in matched):
            relevant_articles.append(a)
    irrelevant_count = before_filter - len(relevant_articles)
    if irrelevant_count > 0:
        logging.info(f"关键词过滤: {before_filter} → {len(relevant_articles)} "
                     f"(排除 {irrelevant_count} 篇无关文章)")
    all_new_articles = relevant_articles

    # ── 写入输出 ──
    # 增量追加：如果文件已存在，读取旧数据并合并
    output_dir = project_root / "data" / "raw" / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "newly_discovered_urls.json"

    existing_articles = []
    if output_file.exists():
        try:
            existing_articles = json.load(open(output_file, encoding="utf-8"))
            if not isinstance(existing_articles, list):
                existing_articles = []
            logging.info(f"读取已有 {len(existing_articles)} 条 URL 数据")
        except (json.JSONDecodeError, IOError):
            existing_articles = []

    # 合并：用 URL 去重
    existing_urls = {a["url"] for a in existing_articles}
    merged = list(existing_articles)
    for article in all_new_articles:
        if article["url"] not in existing_urls:
            merged.append(article)
            existing_urls.add(article["url"])

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    logging.info(f"输出 {len(all_new_articles)} 条新增 URL (合并后共 {len(merged)} 条) → {output_file}")

    # ── 统计 bootstrap_seen vs newly_discovered ──
    bootstrap_count = sum(1 for a in all_new_articles if a.get("freshness_status") == "bootstrap_seen")
    newly_discovered_count = sum(1 for a in all_new_articles if a.get("freshness_status") == "newly_discovered")

    # ── 更新 seen_urls.jsonl ──
    new_ledger_entries = []
    for article in all_new_articles:
        canon = article["url"]
        if canon in seen_urls and seen_urls[canon].get("first_seen_at") == article.get("first_seen_at"):
            new_ledger_entries.append(seen_urls[canon])

    append_seen_urls(ledger_path, new_ledger_entries)
    logging.info(f"更新 seen_urls.jsonl: 新增 {len(new_ledger_entries)} 条")

    # ── 汇总 ──
    total_new = sum(s["new_links"] for s in all_stats)
    total_seen = sum(s["seen_links"] for s in all_stats)
    total_links = sum(s["total_links"] for s in all_stats)

    summary = {
        "ok": True,
        "date": date_str,
        "total_sources": len(monitor_sources),
        "total_links": total_links,
        "new_links": total_new,
        "seen_links": total_seen,
        "failed_links": sum(s["failed_links"] for s in all_stats),
        "bootstrap_mode": is_bootstrap,
        "bootstrap_seen_count": bootstrap_count,
        "newly_discovered_count": newly_discovered_count,
        "ledger_size_after": len(seen_urls),
        "newly_discovered_urls_file": str(output_file),
        "ledger_file": str(ledger_path),
        "per_source": all_stats,
    }

    summary_file = output_dir / "source_url_monitor_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logging.info("=" * 60)
    logging.info(f"Source URL Monitor V2 完成")
    logging.info(f"  bootstrap_mode: {is_bootstrap}")
    logging.info(f"  bootstrap_seen_count: {bootstrap_count}")
    logging.info(f"  newly_discovered_count: {newly_discovered_count}")
    logging.info(f"  seen_count: {total_seen}")
    logging.info(f"  总链接: {total_links}")
    logging.info(f"  新发现: {total_new}")
    logging.info(f"  失败: {sum(s['failed_links'] for s in all_stats)}")
    logging.info("=" * 60)

    return summary


if __name__ == "__main__":
    main()