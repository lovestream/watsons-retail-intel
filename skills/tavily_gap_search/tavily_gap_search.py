#!/usr/bin/env python3
"""
tavily_gap_search.py — Tavily 缺口补搜

当 RSSHub/RSS/Web 采集后 cleaned_count 过低或核心平台缺失时，
调用 Tavily 做精准补搜，补充文章并重新过滤。

用法:
    python skills/tavily_gap_search/tavily_gap_search.py \
        --project-root . --date 2026-04-26

    # 仅执行补搜，不合并和重过滤:
    python skills/tavily_gap_search/tavily_gap_search.py \
        --project-root . --date 2026-04-26 --skip-merge
"""

import argparse
import json
import hashlib
import logging
import os
import re
import sys
import time as time_mod
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# ── 时区与常量 ──
CST = timezone(timedelta(hours=8))
DEFAULT_TIMEZONE_STR = "Asia/Shanghai"

# ── 日志 ──
logger = logging.getLogger("tavily_gap_search")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ══════════════════════════════════════════════════════════
#  Tavily Key 轮换器（独立实现，不依赖 collect 模块）
# ══════════════════════════════════════════════════════════

class TavilyKeyRotator:
    """Tavily API Key 简单轮换器。"""

    def __init__(self, keys: List[str], monthly_limit: int = 1000):
        self.keys = keys
        self.monthly_limit = monthly_limit
        self.current_index = 0
        self.call_counts: Dict[str, int] = {k: 0 for k in keys}
        self.exhausted_keys: set = set()

    @property
    def available(self) -> bool:
        return len(self.keys) > 0

    def get_key(self) -> Optional[str]:
        if not self.keys:
            return None
        tried = 0
        while tried < len(self.keys):
            key = self.keys[self.current_index % len(self.keys)]
            self.current_index = (self.current_index + 1) % len(self.keys)
            if key not in self.exhausted_keys and self.call_counts.get(key, 0) < self.monthly_limit:
                return key
            tried += 1
        return None

    def record_call(self, key: str):
        self.call_counts[key] = self.call_counts.get(key, 0) + 1

    def get_status(self) -> Dict:
        return {
            k: {"calls": self.call_counts.get(k, 0), "remaining": max(0, self.monthly_limit - self.call_counts.get(k, 0))}
            for k in self.keys
        }

    def mark_exhausted(self, key: str):
        """标记某个 Key 已耗尽（如收到 401/403）。"""
        self.exhausted_keys.add(key)
        self.current_index = 0  # 重置以跳过已耗尽的 key


# ══════════════════════════════════════════════════════════
#  触发条件判定
# ══════════════════════════════════════════════════════════

CORE_PLATFORMS = {
    "美团闪购": ["美团闪购", "美团到家", "美团即时", "美团闪购"],
    "京东到家/京东秒送": ["京东到家", "京东秒送", "达达配送"],
    "淘宝闪购/饿了么": ["淘宝闪购", "饿了么", "蜂鸟即配"],
    "抖音小时达": ["抖音小时达", "抖音即时"],
}

COMPETITOR_KEYWORDS = {
    "丝芙兰": ["丝芙兰"],
    "万宁": ["万宁"],
    "调色师": ["调色师", "WOW COLOUR", "wow colour"],
    "名创优品": ["名创优品"],
}


def check_trigger(
    cleaned_count: int,
    cleaned_articles: List[dict],
    reference_articles: List[dict],
    raw_articles: List[dict],
    threshold: int = 5,
) -> Tuple[bool, List[str]]:
    """判断是否需要 Tavily gap search。

    Returns:
        (should_trigger, reasons)
    """
    reasons = []

    # 条件1: cleaned_count < threshold
    if cleaned_count < threshold:
        reasons.append(f"cleaned_count={cleaned_count} < {threshold}")

    # 条件2: 核心平台无有效文章
    for platform, keywords in CORE_PLATFORMS.items():
        found = False
        for a in cleaned_articles:
            title = a.get("title", "") or ""
            summary = a.get("summary", "") or ""
            content = a.get("content", "") or ""
            combined = f"{title} {summary} {content}"
            for kw in keywords:
                if kw in combined:
                    found = True
                    break
            if found:
                break
        if not found:
            reasons.append(f"核心平台缺失: {platform}")

    # 条件3: reference 中有高价值旧线索
    ref_with_kw = 0
    for a in reference_articles:
        title = a.get("title", "") or ""
        summary = a.get("summary", "") or ""
        combined = f"{title} {summary}"
        for platform, keywords in CORE_PLATFORMS.items():
            for kw in keywords:
                if kw in combined:
                    ref_with_kw += 1
                    break
    if ref_with_kw > 0 and cleaned_count < 10:
        reasons.append(f"reference中有{ref_with_kw}条核心平台线索但cleaned不足")

    if not reasons:
        return False, []

    return True, reasons


# ══════════════════════════════════════════════════════════
#  Gap Search Queries
# ══════════════════════════════════════════════════════════

GAP_SEARCH_QUERIES = {
    "platform": [
        "美团闪购 美妆 个护 最新活动",
        "美团闪购 屈臣氏 入驻 最新",
        "京东到家 屈臣氏 最新合作 动态",
        "京东秒送 美妆 个护 最新",
        "淘宝闪购 美妆 个护 最新",
        "淘宝闪购 天猫 官旗 最新",
        "抖音小时达 美妆 日百 最新",
        "抖音小时达 个护 即时零售 最新",
    ],
    "competitor": [
        "丝芙兰 即时零售 最新动态",
        "万宁 即时零售 最新布局",
        "调色师 美团闪购 最新投入",
        "WOW COLOUR 即时零售",
        "名创优品 美妆 即时零售",
    ],
    "category": [
        "美妆 个护 即时零售 最新动态",
        "防晒 即时零售 销量 趋势",
        "洗护 即时零售 最新",
        "女性护理 即时零售 最新",
        "卸妆 小时达 即时零售",
    ],
}


def select_queries(
    reasons: List[str],
    max_queries_per_platform: int = 3,
    max_total_queries: int = 30,
) -> List[str]:
    """根据触发原因选择补搜 query。

    如果是核心平台缺失，优先补那个平台的 query。
    否则执行所有 gap search query。
    """
    queries = []
    seen = set()

    # 如果明确缺失某个平台，优先补
    for reason in reasons:
        for platform_key, keywords in CORE_PLATFORMS.items():
            if platform_key in reason or any(kw in reason for kw in keywords):
                # 找到 search_policy 里的 gap_search query
                for q in GAP_SEARCH_QUERIES.get("platform", []):
                    for kw in keywords:
                        if kw in q and q not in seen:
                            queries.append(q)
                            seen.add(q)
                            break

    # 如果 cleaned_count 太低，执行全部 query
    if any("cleaned_count" in r for r in reasons):
        for category, qs in GAP_SEARCH_QUERIES.items():
            for q in qs:
                if q not in seen:
                    queries.append(q)
                    seen.add(q)

    # 如果 reference 有线索但 cleaned 不足
    if any("reference" in r for r in reasons) and len(queries) < 10:
        for category, qs in GAP_SEARCH_QUERIES.items():
            for q in qs:
                if q not in seen:
                    queries.append(q)
                    seen.add(q)

    # 截断
    if len(queries) > max_total_queries:
        logger.info(f"补搜 query 数量 {len(queries)} 超过上限 {max_total_queries}，截断")
        queries = queries[:max_total_queries]

    return queries


# ══════════════════════════════════════════════════════════
#  Tavily 搜索执行
# ══════════════════════════════════════════════════════════

def _generate_article_id(url: str, source: str, idx: int) -> str:
    """生成文章 ID。"""
    raw = f"{url}|{source}|{idx}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def _classify_time_status(
    published_at: Optional[str],
    window_start: datetime,
    window_end: datetime,
) -> str:
    """判断文章发布时间相对于采集窗口的状态。"""
    if not published_at:
        return "unknown_time"
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        if dt < window_start:
            if dt >= window_start - timedelta(hours=12):
                return "near_window"
            return "old"
        elif dt <= window_end:
            return "in_window"
        else:
            if dt <= window_end + timedelta(hours=12):
                return "near_window"
            return "old"
    except (ValueError, TypeError, OverflowError):
        return "unknown_time"


def _match_keywords(text: str, keywords: List[str]) -> List[str]:
    """匹配关键词。"""
    matched = []
    for kw in keywords:
        if kw.lower() in text.lower():
            matched.append(kw)
    return matched


def _load_all_keywords(keywords_file: str, project_root: str) -> List[str]:
    """加载所有关键词。"""
    import yaml
    path = Path(project_root) / keywords_file
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    all_kw = []
    for category, kws in config.items():
        if isinstance(kws, list):
            all_kw.extend(kws)
        elif isinstance(kws, dict):
            for sub_cat, sub_kws in kws.items():
                if isinstance(sub_kws, list):
                    all_kw.extend(sub_kws)
    return list(set(all_kw))


def search_tavily(
    queries: List[str],
    key_rotator: TavilyKeyRotator,
    window_start: datetime,
    window_end: datetime,
    all_keywords: List[str],
    search_depth: str = "advanced",
    max_results_per_query: int = 5,
    timeout: int = 30,
    project_root: Optional[str] = None,
) -> Tuple[List[dict], Dict]:
    """执行 Tavily 搜索并返回文章列表（并行版，基于 key 分组）。

    使用 keyed_parallel_map 保证:
    - 同一 API Key 同时只有 1 个请求（防 rate limit）
    - 不同 Key 之间可并行
    - 输出按 query 顺序稳定排序
    - 单 query 失败不影响整体
    """
    from skills.utils.parallel_runner import keyed_parallel_map, load_parallel_config

    if not queries:
        return [], {"query_stats": {}, "errors": []}

    # ── 加载并行配置 ──
    if project_root:
        _p_cfg = load_parallel_config(project_root)
    else:
        _p_cfg = {}
    _gap_cfg = _p_cfg.get("tavily_gap_search", {}).get("query_parallel", {})
    _max_workers = _gap_cfg.get("max_workers", 3)
    _per_key_limit = _gap_cfg.get("max_concurrent_per_key", 1)
    _parallel_enabled = _gap_cfg.get("enabled", True)

    # ── 预分配 key: 轮转分配给各 query ──
    tavily_keys = key_rotator.keys if key_rotator.keys else []
    key_list: List[str] = []
    if tavily_keys:
        for i in range(len(queries)):
            key_list.append(tavily_keys[i % len(tavily_keys)])
    else:
        # 无可用 key 时用 placeholder（实际会在 process_fn 中 get_key）
        key_list = ["_no_key_"] * len(queries)

    logger.info(
        f"[Tavily Gap] 并行搜索 {len(queries)} 个查询, "
        f"max_workers={min(_max_workers, len(queries))}, "
        f"per_key_limit={_per_key_limit}, keys={len(tavily_keys)}"
    )

    def _search_one_query(item, idx, api_key):
        """单条 Tavily 搜索。

        Args:
            item: query 字符串
            idx: query 索引
            api_key: 预分配的 API Key
        Returns:
            dict: {"articles": [...], "result_count": N, "error": None/str}
        """
        query = item
        # 如果没有预分配 key，尝试从轮换器获取
        if not api_key or api_key == "_no_key_":
            api_key = key_rotator.get_key()
        if not api_key:
            return {"articles": [], "result_count": 0,
                    "error": "All Tavily API Keys exhausted"}

        try:
            # ── V2: 搜索策略优化 ──
            # topic="news" 只对英文查询有效（中文词返回英文结果），中文用 "general"
            # days 参数仅对 topic="news" 生效
            # 检测是否包含中文字符
            has_chinese = any('\u4e00' <= c <= '\u9fff' for c in query)
            topic = "general" if has_chinese else "news"
            payload = {
                "api_key": api_key,
                "query": query,
                "search_depth": search_depth,
                "include_raw_content": False,
                "max_results": max_results_per_query,
                "topic": topic,
                "days": 3 if topic == "news" else 30,  # general模式不严格限制时间
            }
            resp = requests.post(
                "https://api.tavily.com/search",
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            key_rotator.record_call(api_key)

            results = data.get("results", [])
            articles = []
            for r_idx, result in enumerate(results):
                title = result.get("title", "")
                link = result.get("url", "")
                content = result.get("content", "")
                pub_date = result.get("published_date", "")

                article = {
                    "article_id": _generate_article_id(link, "tavily_gap_search", r_idx),
                    "title": title,
                    "url": link,
                    "source_name": "tavily_gap_search",
                    "source_type": "search",
                    "source_tier": 3,
                    "collector": "tavily_gap_search",
                    "published_at": pub_date,
                    "collected_at": datetime.now(CST).isoformat(),
                    "time_status": _classify_time_status(pub_date, window_start, window_end),
                    "summary": content[:2000] if content else "",
                    "content": content[:50000] if content else "",
                    "matched_keywords": _match_keywords(
                        f"{title} {content}", all_keywords
                    ),
                    "raw": {
                        "query": query,
                        "score": result.get("score", 0),
                        "search_depth": search_depth,
                    },
                }
                articles.append(article)

            return {"articles": articles, "result_count": len(articles), "error": None}

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "N/A"
            if status_code in (401, 403):
                key_rotator.mark_exhausted(api_key)
            error_msg = f"HTTP {status_code}: {e}"
            logger.error(f"[Tavily Gap] 搜索 '{query}' 失败: {error_msg}")
            return {"articles": [], "result_count": 0, "error": error_msg}

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[Tavily Gap] 搜索 '{query}' 失败: {error_msg}")
            return {"articles": [], "result_count": 0, "error": error_msg}

    # ── 执行 ──
    if _parallel_enabled and len(queries) > 1 and tavily_keys:
        # 并行: 同 key 串行, 不同 key 可并行
        results, stats = keyed_parallel_map(
            items=queries,
            process_fn=_search_one_query,
            key_list=key_list,
            max_workers=min(_max_workers, len(queries)),
            max_concurrent_per_key=_per_key_limit,
            timeout=timeout + 15,
            desc="Tavily Gap Search",
        )
    else:
        # 串行 fallback
        results = []
        for i, query in enumerate(queries):
            r = _search_one_query(query, i, key_list[i] if i < len(key_list) else "")
            results.append(r)
        stats = {"total": len(queries), "success": 0, "failed": 0, "elapsed_seconds": 0}

    # ── 汇总结果（按 query 顺序保序）──
    all_articles = []
    query_stats = {}
    errors = []

    for i, result in enumerate(results):
        query = queries[i]
        if result is None:
            errors.append(f"Tavily search '{query}' failed: unknown error")
            query_stats[query] = {"results": 0, "unique_after_dedup": 0, "key_used": "?"}
            continue

        articles = result.get("articles", [])
        error = result.get("error")

        if error:
            errors.append(f"Tavily search '{query}' failed: {error}")
            query_stats[query] = {"results": 0, "unique_after_dedup": 0,
                                   "key_used": f"{key_list[i][:10]}..." if i < len(key_list) else "?"}
        else:
            all_articles.extend(articles)
            key_used = f"{key_list[i][:10]}..." if i < len(key_list) else "?"
            query_stats[query] = {
                "results": result.get("result_count", len(articles)),
                "unique_after_dedup": 0,
                "key_used": key_used,
            }
            logger.info(f"[Tavily Gap] '{query}': 返回 {len(articles)} 条结果")

    logger.info(f"[Tavily Gap] 搜索完成: {len(all_articles)} 条结果, "
                f"{len(errors)} 错误, 耗时 {stats.get('elapsed_seconds', '?')}s")

    return all_articles, {"query_stats": query_stats, "errors": errors}


# ══════════════════════════════════════════════════════════
#  去重
# ══════════════════════════════════════════════════════════

def deduplicate_with_existing(
    new_articles: List[dict],
    existing_articles: List[dict],
) -> List[dict]:
    """与已有文章做 URL 去重，返回不重复的新文章。"""
    existing_urls = set()
    for a in existing_articles:
        url = a.get("url", "").strip()
        if url:
            # 标准化: 去除末尾斜杠、query参数中的tracking
            normalized = url.rstrip("/")
            existing_urls.add(normalized)

    unique = []
    seen_new = set()
    for a in new_articles:
        url = a.get("url", "").strip().rstrip("/")
        if not url:
            continue
        if url in existing_urls:
            continue
        if url in seen_new:
            continue
        seen_new.add(url)
        unique.append(a)

    return unique


# ══════════════════════════════════════════════════════════
#  合并
# ══════════════════════════════════════════════════════════

def merge_with_raw(
    existing_articles: List[dict],
    gap_articles: List[dict],
    max_total: int = 800,
) -> List[dict]:
    """将 gap 文章合并到原始文章列表。"""
    merged = list(existing_articles) + list(gap_articles)
    if len(merged) > max_total:
        logger.warning(f"合并后文章数 {len(merged)} 超过 {max_total}，截断")
        merged = merged[:max_total]
    return merged


# ══════════════════════════════════════════════════════════
#  主函数
# ══════════════════════════════════════════════════════════

def tavily_gap_search(
    date: str,
    project_root: str = ".",
    cleaned_threshold: int = 5,
    skip_merge: bool = False,
    search_policy_file: str = "config/search_policy.yaml",
    keywords_file: str = "config/keywords.yaml",
) -> dict:
    """Tavily 缺口补搜主函数。

    Args:
        date: 日期字符串 (YYYY-MM-DD)
        project_root: 项目根目录
        cleaned_threshold: cleaned_count 低于此值触发
        skip_merge: True 则仅执行补搜，不合并和重过滤
        search_policy_file: 搜索策略配置文件
        keywords_file: 关键词配置文件

    Returns:
        结果字典，包含 triggered, gap_count, merged 文件路径等
    """
    root = Path(project_root)

    # ── 日志文件 ──
    log_dir = root / f"data/logs/{date}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "tavily_gap_search.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(file_handler)

    logger.info("=" * 60)
    logger.info(f"开始 Tavily Gap Search: date={date}")
    logger.info(f"  project_root: {project_root}")
    logger.info(f"  skip_merge: {skip_merge}")
    logger.info("=" * 60)

    # ── 加载配置 ──
    import yaml

    sp_path = root / search_policy_file
    if sp_path.exists():
        with open(sp_path, "r", encoding="utf-8") as f:
            sp_config = yaml.safe_load(f)
    else:
        sp_config = {}
        logger.warning(f"配置文件不存在: {sp_path}，使用默认值")

    tavily_config = sp_config.get("tavily", {})
    gap_config = sp_config.get("gap_search", {})
    daily_budget = tavily_config.get("daily_budget", 80)
    max_results_per_query = tavily_config.get("max_results_per_query", 5)
    max_queries_per_platform = gap_config.get("max_queries_per_platform", 3)
    max_total_queries = min(daily_budget, 30)

    # ── 读取已有数据 ──
    raw_file = root / f"data/raw/{date}/raw_articles.json"
    cleaned_file = root / f"data/cleaned/{date}/cleaned_articles.json"
    reference_file = root / f"data/cleaned/{date}/reference_articles.json"

    raw_articles = []
    cleaned_articles = []
    reference_articles = []

    if raw_file.exists():
        with open(raw_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        raw_articles = raw_data.get("articles", [])
    else:
        logger.warning(f"原始文章文件不存在: {raw_file}")

    if cleaned_file.exists():
        with open(cleaned_file, "r", encoding="utf-8") as f:
            cd = json.load(f)
        cleaned_articles = cd.get("articles", []) if isinstance(cd, dict) else cd

    if reference_file.exists():
        with open(reference_file, "r", encoding="utf-8") as f:
            rd = json.load(f)
        reference_articles = rd.get("articles", []) if isinstance(rd, dict) else rd

    cleaned_count = len(cleaned_articles)
    logger.info(f"当前数据: raw={len(raw_articles)}, cleaned={cleaned_count}, reference={len(reference_articles)}")

    # ── 触发判定 ──
    triggered, reasons = check_trigger(
        cleaned_count, cleaned_articles, reference_articles, raw_articles,
        threshold=cleaned_threshold,
    )

    if not triggered:
        logger.info("未触发 Tavily gap search，数据充足")
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        return {
            "ok": True,
            "triggered": False,
            "trigger_reasons": [],
            "date": date,
            "queries": 0,
            "gap_count": 0,
            "unique_count": 0,
            "merged_file": None,
        }

    logger.info(f"触发 Tavily gap search，原因: {reasons}")

    # ── 时间窗口 ──
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    window_start = datetime(date_obj.year, date_obj.month, date_obj.day, 7, 0, 0, tzinfo=CST) - timedelta(days=1)
    window_end = datetime(date_obj.year, date_obj.month, date_obj.day, 7, 0, 0, tzinfo=CST)
    # 扩展窗口到当前时刻
    now = datetime.now(CST)
    if now > window_end:
        window_end = now

    # ── 初始化 Tavily Key ──
    all_keys = []
    for env_name in ["TAVILY_API_KEYS", "TAVILY_API_KEY", "TAVILY_KEY"]:
        val = os.environ.get(env_name, "").strip()
        if val:
            # 支持逗号分隔的多 key
            for k in val.split(","):
                k = k.strip()
                if k and k not in all_keys:
                    all_keys.append(k)
    # 兼容 collect 模块的环境变量
    for env_name in ["tavily_key", "tavily_key1", "tavily_key2"]:
        val = os.environ.get(env_name, "").strip()
        if val and val not in all_keys:
            all_keys.append(val)

    if not all_keys:
        logger.error("无可用 Tavily API Key，无法执行 gap search")
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        return {"ok": False, "error": "no_tavily_keys", "triggered": True, "trigger_reasons": reasons}

    key_rotator = TavilyKeyRotator(all_keys, monthly_limit=1000)
    logger.info(f"Tavily Key: {len(all_keys)} 个可用")

    # ── 选择 query ──
    queries = select_queries(reasons, max_queries_per_platform, max_total_queries)
    logger.info(f"计划执行 {len(queries)} 个搜索 query")

    # ── 加载关键词 ──
    all_keywords = _load_all_keywords(keywords_file, str(root))
    logger.info(f"加载 {len(all_keywords)} 个关键词")

    # ── 执行搜索 ──
    gap_articles, search_info = search_tavily(
        queries, key_rotator, window_start, window_end, all_keywords,
        search_depth=tavily_config.get("search_depth", "advanced"),
        max_results_per_query=max_results_per_query,
        timeout=tavily_config.get("timeout_seconds", 30),
        project_root=project_root,
    )

    logger.info(f"Tavily 返回 {len(gap_articles)} 条结果")

    # ── 与已有文章去重 ──
    unique_articles = deduplicate_with_existing(gap_articles, raw_articles)
    logger.info(f"去重后保留 {len(unique_articles)} 条新文章 (与 {len(raw_articles)} 条已有文章比对)")

    # ── 时间状态统计 ──
    time_dist = Counter(a.get("time_status", "unknown") for a in unique_articles)
    logger.info(f"新文章时间分布: {dict(time_dist)}")

    # ── 保存 gap 文章 ──
    gap_dir = root / f"data/raw/{date}"
    gap_dir.mkdir(parents=True, exist_ok=True)
    gap_file = gap_dir / "tavily_gap_articles.json"

    gap_data = {
        "metadata": {
            "version": "1.0",
            "date": date,
            "triggered": True,
            "trigger_reasons": reasons,
            "queries": queries,
            "query_count": len(queries),
            "total_results": len(gap_articles),
            "unique_results": len(unique_articles),
            "time_distribution": dict(time_dist),
            "tavily_key_status": key_rotator.get_status(),
            "search_info": search_info,
            "collected_at": datetime.now(CST).isoformat(),
        },
        "articles": unique_articles,
    }

    with open(gap_file, "w", encoding="utf-8") as f:
        json.dump(gap_data, f, ensure_ascii=False, indent=2)
    logger.info(f"Gap 文章已保存: {gap_file}")

    # ── skip_merge 模式 ──
    if skip_merge:
        logger.info("skip_merge=True，跳过合并和重过滤")
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()
        return {
            "ok": True,
            "triggered": True,
            "trigger_reasons": reasons,
            "date": date,
            "queries": len(queries),
            "gap_count": len(gap_articles),
            "unique_count": len(unique_articles),
            "gap_file": str(gap_file),
            "merged_file": None,
            "time_distribution": dict(time_dist),
        }

    # ── 合并到 raw_articles ──
    merged_articles = merge_with_existing(raw_articles, unique_articles)
    merged_file = root / f"data/raw/{date}/raw_articles_merged.json"

    # 保留原始 metadata 并更新
    merged_metadata = {}
    if raw_file.exists():
        with open(raw_file, "r", encoding="utf-8") as f:
            orig_data = json.load(f)
        merged_metadata = orig_data.get("metadata", {})
    merged_metadata["gap_search_added"] = len(unique_articles)
    merged_metadata["merged_at"] = datetime.now(CST).isoformat()

    merged_data = {
        "metadata": merged_metadata,
        "articles": merged_articles,
    }

    with open(merged_file, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)
    logger.info(f"合并文件已保存: {merged_file} (原始 {len(raw_articles)} + 补搜 {len(unique_articles)} = {len(merged_articles)})")

    # ── 重新运行 filter ──
    logger.info("开始重新运行 filter_relevant_articles ...")

    # 添加项目根目录到 sys.path 以支持 import
    project_root_str = str(root.resolve())
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
    from skills.filter_relevant_articles.filter_relevant_articles import filter_relevant_articles

    filter_result = filter_relevant_articles(
        date=date,
        project_root=str(root),
        raw_file=str(merged_file),
        use_llm=False,  # gap search 补漏时用规则模式即可
    )

    logger.info(f"重过滤完成: cleaned={filter_result.get('cleaned_count', '?')}, "
                f"reference={filter_result.get('reference_count', '?')}, "
                f"rejected={filter_result.get('rejected_count', '?')}")

    # ── 汇总结果 ──
    result = {
        "ok": True,
        "triggered": True,
        "trigger_reasons": reasons,
        "date": date,
        "queries": len(queries),
        "gap_count": len(gap_articles),
        "unique_count": len(unique_articles),
        "gap_file": str(gap_file),
        "merged_file": str(merged_file),
        "raw_count_before": len(raw_articles),
        "raw_count_after": len(merged_articles),
        "cleaned_count_before": cleaned_count,
        "cleaned_count_after": filter_result.get("cleaned_count", 0),
        "reference_count_after": filter_result.get("reference_count", 0),
        "rejected_count_after": filter_result.get("rejected_count", 0),
        "time_distribution": dict(time_dist),
        "filter_result": filter_result,
        "tavily_key_status": key_rotator.get_status(),
        "search_info": search_info,
    }

    logger.info("=" * 60)
    logger.info("Tavily Gap Search 完成")
    logger.info(f"  触发原因: {reasons}")
    logger.info(f"  补搜 query 数: {len(queries)}")
    logger.info(f"  补搜结果数: {len(gap_articles)}")
    logger.info(f"  去重后新文章: {len(unique_articles)}")
    logger.info(f"  raw_count: {len(raw_articles)} → {len(merged_articles)}")
    logger.info(f"  cleaned_count: {cleaned_count} → {filter_result.get('cleaned_count', 0)}")
    logger.info("=" * 60)

    logging.getLogger().removeHandler(file_handler)
    file_handler.close()

    return result


def merge_with_existing(
    existing_articles: List[dict],
    gap_articles: List[dict],
    max_total: int = 800,
) -> List[dict]:
    """合并已有文章和补搜文章，并去重。"""
    # 去重
    all_urls = set()
    merged = []
    for a in existing_articles:
        url = a.get("url", "").strip().rstrip("/")
        if url and url not in all_urls:
            all_urls.add(url)
            merged.append(a)
    for a in gap_articles:
        url = a.get("url", "").strip().rstrip("/")
        if url and url not in all_urls:
            all_urls.add(url)
            merged.append(a)

    if len(merged) > max_total:
        logger.warning(f"合并后 {len(merged)} 条超过上限 {max_total}，截断")
        merged = merged[:max_total]

    return merged


# ══════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Tavily Gap Search — 缺口补搜")
    parser.add_argument("--project-root", default=".", help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--cleaned-threshold", type=int, default=5,
                        help="cleaned_count 低于此值触发 (默认 5)")
    parser.add_argument("--skip-merge", action="store_true",
                        help="仅执行补搜，不合并和重过滤")
    parser.add_argument("--search-policy-file", default="config/search_policy.yaml")
    parser.add_argument("--keywords-file", default="config/keywords.yaml")
    args = parser.parse_args()

    result = tavily_gap_search(
        date=args.date,
        project_root=args.project_root,
        cleaned_threshold=args.cleaned_threshold,
        skip_merge=args.skip_merge,
        search_policy_file=args.search_policy_file,
        keywords_file=args.keywords_file,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()