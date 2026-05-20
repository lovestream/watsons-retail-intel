#!/usr/bin/env python3
"""
broad_search_discovery.py — 广泛搜索发现模块

通过多搜索源（Tavily、XCrawl）、多站点、多关键词矩阵，
广泛发现与即时零售、个护美妆、屈臣氏电商经营相关的新链接。

该模块只负责发现 URL，不直接进入 cleaned。

输出：
  data/raw/YYYY-MM-DD/broad_search_urls.json
  data/logs/YYYY-MM-DD/broad_search_discovery.log

用法：
    python broad_search_discovery.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-05-02 \
        [--verbose]
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urlunparse

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import yaml
except ImportError:
    print("ERROR: 缺少 PyYAML。请运行: pip install PyYAML", file=sys.stderr)
    sys.exit(1)

# ===================== 常量 =====================

# 搜索矩阵维度
PLATFORMS = [
    "美团闪购", "京东到家", "京东秒送", "淘宝闪购",
    "饿了么", "抖音小时达",
]

CATEGORIES = [
    "美妆", "个护", "护肤", "彩妆", "洗护",
    "防晒", "女性护理", "日化",
]

ACTIONS = [
    "最新", "上线", "加码", "合作", "补贴",
    "招商", "规则", "小时达", "即时零售",
]

COMPETITORS = [
    "屈臣氏", "丝芙兰", "万宁", "妍丽",
    "WOW COLOUR", "调色师", "话梅", "名创优品",
]

# 生成 query 的组合策略
# 1. 平台 + 品类 + 动作 (3-word combos)
# 2. 竞对 + 动作 (2-word combos)
# 3. 平台 + 动作 (2-word combos)
# 4. site:domain 查询 (focused)

# 排除噪音域名（与 source_packs.yaml search_sources.tavily.exclude_domains 保持一致）
DEFAULT_EXCLUDE_DOMAINS = [
    "zhihu.com", "baidu.com", "weibo.com", "douban.com",
    "taobao.com", "jd.com", "tmall.com", "pinduoduo.com",
    "instagram.com", "threads.com", "tiktok.com",
    "facebook.com", "twitter.com",
    "toutiao.com", "douyin.com", "kuaishou.com",
    "xiaohongshu.com",
    # app 下载/安装页面 — 非新闻内容
    "apps.microsoft.com", "sj.qq.com", "app.mi.com",
    "apps.apple.com", "play.google.com",
    # 优惠券/比价聚合 — 非原创内容
    "smzdm.com", "manmanbuy.com", "gwdang.com",
]

# ===================== 配置加载 =====================

def load_yaml(filepath: str) -> dict:
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(project_root: str, rel_path: str) -> str:
    return str(Path(project_root) / rel_path)


# ===================== 搜索矩阵生成 =====================

def _expand_dynamic_templates(templates: list, site_config: dict) -> list:
    """展开动态模板，生成 query 列表。
    
    Args:
        templates: source_packs.yaml 中的 dynamic_templates 列表
        site_config: site_domains 配置
    
    Returns:
        [(query_text, query_type), ...]
    """
    results = []
    for tmpl in templates:
        template_str = tmpl.get("template", "")
        max_count = tmpl.get("max", 999)
        
        # 收集所有可替换变量的值
        placeholders = {}
        for key in ["platforms", "categories", "actions", "competitors", "keywords"]:
            vals = tmpl.get(key)
            if vals and isinstance(vals, list):
                placeholders[key] = vals
        
        # 特殊: domains_from 引用 site_domains 分组
        domains_from = tmpl.get("domains_from")
        if domains_from:
            group_entries = site_config.get(domains_from, [])
            domains = []
            for entry in group_entries:
                if isinstance(entry, dict) and "domain" in entry:
                    domains.append(entry["domain"])
                elif isinstance(entry, str):
                    domains.append(entry)
            if domains:
                placeholders["domain"] = domains
        
        # 确定哪些变量参与了模板
        import re
        vars_in_template = set(re.findall(r'\{(\w+)\}', template_str))
        
        # 组合展开
        if len(vars_in_template) == 1:
            var_name = list(vars_in_template)[0]
            values = placeholders.get(var_name, [])
            count = 0
            for v in values:
                if count >= max_count:
                    break
                results.append((template_str.replace(f"{{{var_name}}}", v), "dynamic"))
                count += 1
                
        elif len(vars_in_template) == 2:
            var_names = sorted(vars_in_template, key=lambda x: list(vars_in_template).index(x))
            # 保持模板中的顺序
            var_names = list(vars_in_template)
            values1 = placeholders.get(var_names[0], [])
            values2 = placeholders.get(var_names[1], [])
            count = 0
            for v1 in values1:
                for v2 in values2:
                    if count >= max_count:
                        break
                    q = template_str.replace(f"{{{var_names[0]}}}", v1).replace(f"{{{var_names[1]}}}", v2)
                    results.append((q, "dynamic"))
                    count += 1
                if count >= max_count:
                    break
                    
        elif len(vars_in_template) == 3:
            var_names = list(vars_in_template)
            values1 = placeholders.get(var_names[0], [])
            values2 = placeholders.get(var_names[1], [])
            values3 = placeholders.get(var_names[2], [])
            count = 0
            for v1 in values1:
                for v2 in values2:
                    for v3 in values3:
                        if count >= max_count:
                            break
                        q = template_str
                        for vn, vv in zip(var_names, [v1, v2, v3]):
                            q = q.replace(f"{{{vn}}}", vv)
                        results.append((q, "dynamic"))
                        count += 1
                    if count >= max_count:
                        break
                if count >= max_count:
                    break
    
    return results


# ── 日期前缀搜索词 ──
DATE_PREFIX_PLATFORMS = ["美团闪购", "京东到家", "淘宝闪购", "抖音小时达"]
DATE_PREFIX_CATEGORIES = ["美妆", "个护", "即时零售", "美妆个护"]
DATE_PREFIX_ACTIONS = [
    "发布", "上线", "宣布", "合作",
    # was 18, cut to 4 core actions to conserve XCrawl credits
]

DATE_SITE_QUERIES = [
    ("site:ebrun.com", "即时零售 美妆"),
    ("site:36kr.com", "美团闪购"),
    ("site:huxiu.com", "即时零售"),
    ("site:jumeili.cn", "美妆 即时零售"),
    ("site:163.com", "美团闪购"),
    ("site:sina.com.cn", "京东到家 屈臣氏"),
    ("site:linkshop.com", "即时零售"),
    ("site:qq.com", "即时零售 个护"),
]


def generate_queries(config: dict, date: str = "") -> List[dict]:
    """生成搜索 query 列表 (A/B/C 三级分层)。
    
    优先级: A > B > C
    
    A类: 强相关必跑 — 每天都执行，不限预算
    B类: 平台+动作 — 按预算执行
    C类: site:domain 搜索 — 按 source_health 动态决定
    
    Args:
        config: source_packs.yaml 配置
        date: 收集日期 YYYY-MM-DD，用于生成日期前缀query
    
    返回: [{"query": str, "type": str, "source": str, "tier": str}, ...]
    """
    queries = []
    seen_queries = set()
    
    broad_config = config.get("broad_search", {})
    budget = broad_config.get("budget", {})
    max_queries = budget.get("max_queries_per_run", 120)
    
    # 加载 site_domains 配置
    site_config = config.get("site_domains", {})
    
    # 加载 query_tiers 配置
    query_tiers = config.get("query_tiers", {})
    
    # ── 如果有 query_tiers 配置 (V2)，优先使用 ──
    if query_tiers:
        tier_order = [("tier_a", "A"), ("tier_b", "B"), ("tier_c", "C")]
        
        for tier_key, tier_label in tier_order:
            tier_config = query_tiers.get(tier_key, {})
            if not tier_config:
                continue
            
            max_per_run = tier_config.get("max_per_run", 120)
            
            # 1. 静态 queries
            static_queries = tier_config.get("queries", [])
            for q_text in static_queries:
                normalized = q_text.strip()
                if normalized not in seen_queries:
                    seen_queries.add(normalized)
                    queries.append({
                        "query": normalized,
                        "type": f"tier_{tier_label.lower()}",
                        "source": "broad_discovery",
                        "tier": tier_label,
                    })
            
            # 2. 动态 templates
            dynamic_templates = tier_config.get("dynamic_templates", [])
            for tmpl in dynamic_templates:
                expanded = _expand_dynamic_templates([tmpl], site_config)
                max_from_template = tmpl.get("max", 999)
                count = 0
                for q_text, q_type in expanded:
                    if count >= max_from_template:
                        break
                    normalized = q_text.strip()
                    if normalized not in seen_queries:
                        seen_queries.add(normalized)
                        queries.append({
                            "query": normalized,
                            "type": f"tier_{tier_label.lower()}",
                            "source": "broad_discovery",
                            "tier": tier_label,
                        })
                        count += 1
    
    # ── 兼容旧配置 (没有 query_tiers) ──
    else:
        # A类: 竞对×平台 + 平台核心
        a_queries = []
        for comp in COMPETITORS[:4]:
            for plat in PLATFORMS[:4]:
                a_queries.append((f"{comp} {plat} 最新", "competitor_platform"))
        for plat in PLATFORMS[:4]:
            a_queries.append((f"{plat} 即时零售", "platform_core"))
        a_queries += [
            ("即时零售 行业趋势", "platform_core"),
            ("即时零售 美妆", "platform_core"),
            ("即时零售 个护", "platform_core"),
            ("屈臣氏 即时零售 最新", "competitor_core"),
        ]
        
        # B类: 平台×品类×动作
        b_queries = []
        for p in PLATFORMS[:3]:
            for c in CATEGORIES[:4]:
                for a in ACTIONS[:5]:
                    b_queries.append((f"{p} {c} {a}", "platform_category_action"))
        b_queries = b_queries[:35]
        
        # C类: site: 搜索
        c_queries = []
        site_domains_list = []
        for group_key, group_list in site_config.items():
            if isinstance(group_list, list):
                for entry in group_list:
                    if isinstance(entry, dict) and "domain" in entry:
                        site_domains_list.append(entry["domain"])
        
        for domain in site_domains_list:
            for kw in ["即时零售", "美妆", "屈臣氏"]:
                c_queries.append((f"site:{domain} {kw}", "site_search"))
        
        for q_text, q_type in a_queries + b_queries + c_queries:
            normalized = q_text.strip()
            if normalized not in seen_queries:
                seen_queries.add(normalized)
                queries.append({
                    "query": normalized,
                    "type": q_type,
                    "source": "broad_discovery",
                    "tier": "A" if q_type in ("competitor_platform", "competitor_core", "platform_core") else
                             "C" if q_type == "site_search" else "B",
                })
    
    # ── 日期前缀搜索 (基于收集日期) ──
    if date:
        try:
            date_dt = datetime.strptime(date, "%Y-%m-%d")
            year_month = f"{date_dt.year}年{date_dt.month}月"
            month_day = f"{date_dt.month}月{date_dt.day}日"
        except ValueError:
            year_month = ""
            month_day = ""
        
        if year_month:
            # 日期型query: YYYY年M月 + 平台 + 品类
            for plat in DATE_PREFIX_PLATFORMS:
                for cat in DATE_PREFIX_CATEGORIES:
                    q = f"{year_month} {plat} {cat}"
                    if q not in seen_queries:
                        seen_queries.add(q)
                        queries.append({"query": q, "type": "date_prefix_month", "source": "broad_discovery", "tier": "A"})
            
            # 日期型query: YYYY年M月 + 平台 + 动作词 (Tier B)
            # 限制平台数/动作词数，控制 XCrawl 消耗
            for plat in DATE_PREFIX_PLATFORMS[:2]:  # 只取前2个平台
                for action in DATE_PREFIX_ACTIONS[:8]:  # 只取前8个动作词
                    q = f"{year_month} {plat} {action}"
                    if q not in seen_queries:
                        seen_queries.add(q)
                        queries.append({"query": q, "type": "date_prefix_action", "source": "broad_discovery", "tier": "B"})
            
            # 日期型query: M月D日 + 平台
            if month_day:
                for plat in DATE_PREFIX_PLATFORMS:
                    q = f"{month_day} {plat}"
                    if q not in seen_queries:
                        seen_queries.add(q)
                        queries.append({"query": q, "type": "date_prefix_day", "source": "broad_discovery", "tier": "A"})
            
            # Site query 带年月: site:domain YYYY年M月 keyword
            for site_prefix, keyword in DATE_SITE_QUERIES:
                q = f"{site_prefix} {year_month} {keyword}"
                if q not in seen_queries:
                    seen_queries.add(q)
                    queries.append({"query": q, "type": "date_site_search", "source": "broad_discovery", "tier": "C"})

    # Limit to budget (A类不受限制)
    # 日期前缀query优先于普通B/C类
    tier_a_count = sum(1 for q in queries if q.get("tier") == "A")
    date_prefix_queries = [q for q in queries if q.get("type", "").startswith("date_") and q.get("tier") != "A"]
    non_date_queries = [q for q in queries if q.get("tier") != "A" and not q.get("type", "").startswith("date_")]
    non_a_budget = max_queries - tier_a_count
    # 日期前缀query优先占用非A预算
    date_prefix_count = min(len(date_prefix_queries), non_a_budget)
    remaining_budget = max(0, non_a_budget - date_prefix_count)
    if len(non_date_queries) > remaining_budget:
        non_date_queries = non_date_queries[:remaining_budget]
    queries = [q for q in queries if q.get("tier") == "A"] + date_prefix_queries[:date_prefix_count] + non_date_queries
    
    return queries


# ===================== 搜索执行 =====================

class SearchEngine:
    """搜索引擎基类。"""
    
    def __init__(self, name: str, logger: logging.Logger):
        self.name = name
        self.logger = logger
        self.stats = {"queries": 0, "results": 0, "errors": 0, "skipped": 0}
    
    def is_available(self) -> bool:
        """检查搜索引擎是否可用。"""
        return False
    
    def search(self, query: str, max_results: int = 5, **kwargs) -> List[dict]:
        """执行搜索，返回结果列表。"""
        raise NotImplementedError


class TavilySearchEngine(SearchEngine):
    """Tavily 搜索引擎。"""
    
    def __init__(self, logger: logging.Logger, config: dict):
        super().__init__("tavily", logger)
        self.config = config
        self._client = None
        self._available = False
        self._keys = []
        self._key_idx = 0
        
        # 从环境变量加载 keys: tavily_key, tavily_key1-6
        for i in range(7):
            env_name = f"tavily_key{i}" if i > 0 else "tavily_key"
            val = os.environ.get(env_name, "").strip()
            if val:
                self._keys.append(val)
        
        # 也检查标准 TAVILY_API_KEY / TAVILY_API_KEYS
        val = os.environ.get("TAVILY_API_KEY", "").strip()
        if val and val not in self._keys:
            self._keys.append(val)
        val = os.environ.get("TAVILY_API_KEYS", "").strip()
        if val:
            for k in val.split(","):
                k = k.strip()
                if k and k not in self._keys:
                    self._keys.append(k)
        
        if self._keys:
            try:
                from tavily import TavilyClient
                self._available = True
                logger.info(f"  Tavily keys: {len(self._keys)} 个已加载")
            except ImportError:
                logger.warning("  tavily-python 未安装，Tavily 搜索不可用")
    
    def is_available(self) -> bool:
        return self._available
    
    def search(self, query: str, max_results: int = 5, **kwargs) -> List[dict]:
        if not self._available:
            return []
        
        if not self._keys:
            self.logger.warning("[Tavily] 所有 Key 已耗尽")
            return []
        
        try:
            from tavily import TavilyClient
        except ImportError:
            self.logger.warning("[Tavily] tavily-python 未安装，跳过")
            self._available = False
            return []
        
        results = []
        
        # Key 轮换
        api_key = self._keys[self._key_idx % len(self._keys)]
        self._key_idx += 1
        
        search_depth = kwargs.get("search_depth", "advanced")
        exclude_domains = kwargs.get("exclude_domains", DEFAULT_EXCLUDE_DOMAINS)
        time_range = kwargs.get("time_range", "day")
        
        # 两级搜索: day → week（仅 day 返回0结果且为 Tier-A 时降级到 week）
        search_trs = [time_range]
        if time_range == "day":
            search_trs.append("week")  # day 0结果时兜底
        
        for tr in search_trs:
            if tr != time_range and len(results) > 0:
                break  # day 已有结果，不降级
            
            try:
                client = TavilyClient(api_key=api_key)
                
                search_kwargs = {
                    "query": query,
                    "search_depth": search_depth,
                    "max_results": max_results,
                    "include_raw_content": "text",
                }
                if exclude_domains:
                    search_kwargs["exclude_domains"] = exclude_domains
                if tr:
                    search_kwargs["time_range"] = tr
                
                response = client.search(**search_kwargs)
                
                self.stats["queries"] += 1
                articles = response.get("results", [])
                self.stats["results"] += len(articles)
                
                for article in articles:
                    results.append({
                        "title": article.get("title", ""),
                        "url": article.get("url", ""),
                        "summary": (article.get("content", "") or "")[:500],
                        "source_type": "tavily_broad",
                        "source_name": "tavily_broad",
                        "collector": "broad_search",
                        "search_query": query,
                        "published_at": article.get("published_date", ""),
                        "time_range": tr,
                        "raw": article,
                    })
                
                self.logger.debug(f"[Tavily] query='{query[:40]}' tr={tr} → {len(articles)} results")
                
                # day 轮充足则不再 week
                if tr == time_range and len(articles) >= 3:
                    break
                
            except Exception as e:
                self.logger.warning(f"[Tavily] 搜索异常: {e}")
                self.stats["errors"] += 1
                if "429" in str(e) or "rate" in str(e).lower():
                    # Rate limit — try next key
                    if self._key_idx < len(self._keys):
                        continue
                    break
                break
        
        return results


class XCrawlSearchEngine(SearchEngine):
    """XCrawl 搜索引擎。"""
    
    def __init__(self, logger: logging.Logger, config: dict):
        super().__init__("xcrawl_search", logger)
        self.config = config
        self._available = False
        self._keys = []
        self._key_idx = 0
        self._dead_keys = set()  # 标记已失效的 key 索引
        
        # 加载 XCrawl keys — 从 config/sources.yaml 读取有效 key 列表
        try:
            from xcrawl import XcrawlClient
            import yaml
            config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'sources.yaml')
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = yaml.safe_load(f)
                env_names = cfg.get('xcrawl', {}).get('keys_env_vars', [])
                for env_name in env_names:
                    k = os.environ.get(env_name, "").strip()
                    if k:
                        self._keys.append(k)
            else:
                # fallback: 尝试 xcrawl_key3~6（已知有效的）
                for i in range(3, 7):
                    k = os.environ.get(f"xcrawl_key{i}", "").strip()
                    if k:
                        self._keys.append(k)
            if self._keys:
                self._available = True
                self.logger.info(f"[XCrawl] 加载 {len(self._keys)} 个 Key")
        except ImportError:
            pass
    
    def is_available(self) -> bool:
        return self._available and len(self._dead_keys) < len(self._keys)
    
    def _get_next_key(self) -> str:
        """获取下一个可用 key，跳过已标记失效的。"""
        for _ in range(len(self._keys)):
            idx = self._key_idx % len(self._keys)
            self._key_idx += 1
            if idx not in self._dead_keys:
                return self._keys[idx]
        # 所有 key 都失效了
        self._available = False
        raise RuntimeError("所有 XCrawl Key 已失效")
    
    def search(self, query: str, max_results: int = 5, **kwargs) -> List[dict]:
        if not self._available:
            return []
        
        try:
            from xcrawl import XcrawlClient
            from xcrawl.types import SearchOptions
        except ImportError:
            self.logger.warning("[XCrawl] xcrawl SDK 未安装，跳过")
            self._available = False
            return []
        
        timeout = kwargs.get("timeout", 30)
        
        # ── Freshness: 在 query 末尾追加时间信号，提升搜索结果的时效性 ──
        # XCrawl API 不支持时间范围过滤，只能通过 query 语义推送
        # 避免重复追加：已含 "最新" / "202" / "近期" / "最新动态" 的不再加
        time_signals = ["最新", "2026", "2025", "近期", "最新动态", "最新消息", "趋势"]
        has_time_signal = any(ts in query for ts in time_signals)
        if not has_time_signal:
            query = f"{query} 最新动态 2026"
        
        try:
            api_key = self._get_next_key()
            key_idx = (self._key_idx - 1) % len(self._keys)
        except RuntimeError:
            self.logger.warning("[XCrawl] 所有 Key 已失效")
            return []
        
        try:
            client = XcrawlClient(api_key=api_key, timeout=timeout)
            response = client.search(SearchOptions(
                query=query,
                limit=max_results,
                language="zh",
            ))
            
            data_block = response.get("data", {})
            articles = data_block.get("data", [])
            
            self.stats["queries"] += 1
            self.stats["results"] += len(articles)
            
            results = []
            for article in articles:
                title = article.get("title", "")
                link = article.get("url", "")
                snippet = article.get("description", "")
                
                if not title or not link:
                    continue
                
                results.append({
                    "title": title,
                    "url": link,
                    "summary": snippet[:500],
                    "source_type": "xcrawl_broad",
                    "source_name": f"xcrawl_broad_{key_idx:02d}",
                    "collector": "broad_search",
                    "search_query": query,
                    "published_at": "",
                    "raw": article,
                })
            
            self.logger.debug(f"[XCrawl] query='{query[:40]}' → {len(results)} results (key {key_idx})")
            return results
            
        except Exception as e:
            err_str = str(e).lower()
            # 401 (auth_failed) → 标记 key 失效
            if "401" in err_str or "auth" in err_str or "invalid credentials" in err_str:
                key_idx = (self._key_idx - 1) % len(self._keys)
                self._dead_keys.add(key_idx)
                self.logger.warning(f"[XCrawl] Key #{key_idx} 已失效 (auth_failed)，标记跳过")
            else:
                self.logger.warning(f"[XCrawl] 搜索异常: {e}")
            self.stats["errors"] += 1
            return []


# ===================== URL 去重与输出 =====================

def normalize_url(url: str) -> str:
    """标准化 URL 用于去重。"""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"
        # Remove tracking params
        return urlunparse((scheme, netloc, path, parsed.params, "", ""))
    except Exception:
        return url


def load_existing_urls(project_root: str, date: str) -> Set[str]:
    """加载当日已采集的 URL，用于去重。"""
    seen = set()
    raw_file = resolve_path(project_root, f"data/raw/{date}/raw_articles.json")
    if os.path.exists(raw_file):
        try:
            with open(raw_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            articles = data.get("articles", data) if isinstance(data, dict) else data
            for a in articles:
                if isinstance(a, dict) and a.get("url"):
                    seen.add(normalize_url(a["url"]))
        except Exception:
            pass
    
    # 也加载 newly_discovered_urls
    nd_file = resolve_path(project_root, f"data/raw/{date}/newly_discovered_urls.json")
    if os.path.exists(nd_file):
        try:
            with open(nd_file, "r", encoding="utf-8") as f:
                nd_data = json.load(f)
            nd_list = nd_data if isinstance(nd_data, list) else nd_data.get("articles", [])
            for a in nd_list:
                if isinstance(a, dict) and a.get("url"):
                    seen.add(normalize_url(a["url"]))
        except Exception:
            pass
    
    return seen


def filter_urls_by_pattern(results: List[dict]) -> List[dict]:
    """过滤明显的噪音 URL。"""
    noise_patterns = [
        r"/product/", r"/products/", r"/goods/", r"/item/\d+",
        r"/sku/", r"/cart", r"/coupon", r"/promotion/",
        r"/category/", r"/search\?", r"/jobs/", r"/careers/",
        r"instagram\.com/", r"facebook\.com/", r"pinterest\.com/",
        r"twitter\.com/", r"x\.com/",
        r"/download", r"/app$", r"\.pdf$",
    ]
    
    filtered = []
    for r in results:
        url = r.get("url", "")
        if not url:
            continue
        url_lower = url.lower()
        if any(re.search(p, url_lower) for p in noise_patterns):
            r["noise_flag"] = "url_pattern_filtered"
            # Don't remove, just flag it
        filtered.append(r)
    
    return filtered


def extract_published_date(article: dict) -> Optional[str]:
    """从文章的 description/summary/url 中提取发布日期。
    
    优先级:
    1. description 前缀日期 (XCrawl格式: "2026年4月12日 — ...")
    2. description 前缀日期 (英文格式: "Apr 2, 2026 — ...")
    3. description 相对日期 ("5天前 — ...", "3小时前 — ...")
    4. URL 路径中的日期 (/2026/05/02/, /20260502/, /20260502xxxx/)
    5. raw.published_date (Tavily 格式)
    
    Returns:
        ISO 8601 日期字符串 或 None
    """
    # 0. 已有 published_at
    existing = article.get("published_at", "")
    if existing and len(existing) > 5:
        return existing
    
    # 1. 从 raw 字段提取 (Tavily published_date)
    raw = article.get("raw", {})
    if isinstance(raw, dict):
        pd = raw.get("published_date", "")
        if pd and len(pd) > 5:
            return pd
    
    # 合并可能包含日期的文本
    desc = (article.get("summary", "") or article.get("description", "") or "")
    if isinstance(raw, dict):
        desc_raw = raw.get("description", "")
        if desc_raw:
            desc = desc_raw if len(desc_raw) > len(desc) else desc
    
    # 2. 中文日期格式: "2026年4月12日 — ..." 或 "2026-04-12 — ..."
    cn_date_match = re.search(
        r'(\d{4})[年/\-\.](\d{1,2})[月/\-\.](\d{1,2})[日号]?\s*—?\s*',
        desc[:50]
    )
    if cn_date_match:
        try:
            y, m, d = int(cn_date_match.group(1)), int(cn_date_match.group(2)), int(cn_date_match.group(3))
            return f"{y:04d}-{m:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    
    # 3. 英文日期格式: "Apr 2, 2026 — ..." 或 "May 02, 2026 — ..."
    en_months = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    en_date_match = re.search(
        r'(\d{1,2})?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s*(\d{1,2})?\s*,?\s*(\d{4})',
        desc[:80], re.IGNORECASE
    )
    if en_date_match:
        try:
            month = en_months.get(en_date_match.group(2).lower()[:3], 0)
            day = int(en_date_match.group(1) or en_date_match.group(3))
            year = int(en_date_match.group(4))
            if month and year:
                return f"{year:04d}-{month:02d}-{day:02d}"
        except (ValueError, IndexError):
            pass
    
    # 4. 相对日期: "5天前 — ..." "3小时前 — ..."
    #    注意: 必须包含"前"字，避免误匹配"24小时营业"等非时间描述
    now = datetime.now(timezone(timedelta(hours=8)))
    rel_match = re.search(r'(\d+)\s*(天|小时|分钟)\s*前', desc[:40])
    if rel_match:
        try:
            num = int(rel_match.group(1))
            unit = rel_match.group(2)
            if '天' in unit:
                pub_date = now - timedelta(days=num)
                return pub_date.strftime('%Y-%m-%d')
            elif '小时' in unit:
                pub_date = now - timedelta(hours=num)
                return pub_date.strftime('%Y-%m-%d')
            elif '分钟' in unit:
                return now.strftime('%Y-%m-%d')
        except (ValueError, IndexError):
            pass
    
    # 昨天/今天/前天
    if desc.startswith('昨天') or '昨天 —' in desc[:20]:
        return (now - timedelta(days=1)).strftime('%Y-%m-%d')
    if desc.startswith('今天') or '今天 —' in desc[:20]:
        return now.strftime('%Y-%m-%d')
    if desc.startswith('前天') or '前天 —' in desc[:20]:
        return (now - timedelta(days=2)).strftime('%Y-%m-%d')
    
    # 5. URL 路径日期: /2026/05/02/, /20260502/, /2026-05-02/, /2026_05/
    url = article.get("url", "")
    url_date_match = re.search(r'/(\d{4})[/_\-](\d{2})[/_\-](\d{2})/', url)
    if url_date_match:
        try:
            y, m, d = int(url_date_match.group(1)), int(url_date_match.group(2)), int(url_date_match.group(3))
            if 2020 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    
    # URL 中的紧凑日期: /20260502
    url_compact = re.search(r'/(\d{4})(\d{2})(\d{2})', url)
    if url_compact:
        try:
            y, m, d = int(url_compact.group(1)), int(url_compact.group(2)), int(url_compact.group(3))
            if 2020 <= y <= 2030 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    
    return None


def compute_time_status(published_at: Optional[str], date: str) -> str:
    """根据发布日期和采集窗口计算 time_status。
    
    Args:
        published_at: 发布日期 (ISO 8601 或 None)
        date: 采集日期 YYYY-MM-DD
    
    Returns:
        time_status: in_window / near_window / old / unknown_time
    """
    if not published_at:
        return "unknown_time"
    
    try:
        # Parse published_at — 可能是各种格式
        pub_str = published_at.strip()
        
        # Try ISO format first
        for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z',
                    '%a, %d %b %Y %H:%M:%S', '%d %b %Y']:
            try:
                pub_dt = datetime.strptime(pub_str[:25], fmt)
                break
            except ValueError:
                continue
        else:
            return "unknown_time"
        
        # Parse date window
        window_start = datetime.strptime(date, '%Y-%m-%d') - timedelta(days=1)
        window_start = window_start.replace(hour=7)
        window_end = datetime.strptime(date, '%Y-%m-%d').replace(hour=7)
        
        # Compute days difference from window end
        diff = (window_end - pub_dt).days
        
        if 0 <= diff <= 2:
            return "in_window"        # 当天/近2天
        elif 3 <= diff <= 7:
            return "near_window"       # 近1周
        elif diff > 7:
            return "old"               # 超过1周
        else:
            # 发布日期在未来或非常近
            return "in_window"
            
    except Exception:
        return "unknown_time"


# ===================== 主函数 =====================

def broad_search_discovery(
    project_root: str,
    date: str,
    verbose: bool = False,
    skip_merge: bool = True,
) -> dict:
    """广泛搜索发现主函数。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        verbose: 是否启用详细日志
        skip_merge: 是否跳过合并到原始文章池（默认 True，由 merge_raw_articles 步骤统一合并）
    
    Returns:
        元数据 dict，包含 total_discovered, queries_executed, merge 等
    """
    
    # ── 路径 ──
    source_packs_path = resolve_path(project_root, "config/source_packs.yaml")
    search_policy_path = resolve_path(project_root, "config/search_policy.yaml")
    output_dir = resolve_path(project_root, f"data/raw/{date}")
    log_dir = resolve_path(project_root, f"data/logs/{date}")
    
    os.makedirs(output_dir, exist_ok=True)
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
                os.path.join(log_dir, "broad_search_discovery.log"),
                encoding="utf-8",
            ),
        ],
    )
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info(f"广泛搜索发现: date={date}")
    logger.info(f"项目根目录: {project_root}")
    logger.info("=" * 60)
    
    # ── 加载配置 ──
    try:
        config = load_yaml(source_packs_path)
    except FileNotFoundError:
        logger.warning(f"source_packs.yaml 不存在，使用默认配置")
        config = {}
    
    try:
        search_config = load_yaml(search_policy_path)
    except FileNotFoundError:
        logger.warning(f"search_policy.yaml 不存在，使用默认配置")
        search_config = {}
    
    # 合并配置
    full_config = {**config, **search_config}
    if "broad_search" not in full_config:
        full_config["broad_search"] = {}
    if "budget" not in full_config["broad_search"]:
        # 支持顶层 budget（source_packs.yaml）或 broad_search.budget 两种配置
        if "budget" in full_config:
            full_config["broad_search"]["budget"] = full_config["budget"]
        else:
            full_config["broad_search"]["budget"] = {}
    
    budget = full_config["broad_search"].get("budget", {})
    max_queries = budget.get("max_queries_per_run", 80)
    max_results_per_query = budget.get("max_results_per_query", 5)
    tavily_daily_limit = budget.get("tavily_daily_limit", 80)
    xcrawl_daily_limit = budget.get("xcrawl_daily_limit", 30)
    
    # ── 生成搜索矩阵 (A/B/C 三级分层) ──
    queries = generate_queries(full_config, date=date)
    
    # 按 tier 排序: A > B > C
    tier_order = {"A": 0, "B": 1, "C": 2}
    queries.sort(key=lambda q: tier_order.get(q.get("tier", "C"), 99))
    
    # 统计各 tier 数量
    tier_counts = Counter(q.get("tier", "?") for q in queries)
    logger.info(f"生成搜索 query: {len(queries)} 条 (预算上限: {max_queries})")
    logger.info(f"  A类(必跑): {tier_counts.get('A', 0)} 条")
    logger.info(f"  B类(按预算): {tier_counts.get('B', 0)} 条")
    logger.info(f"  C类(动态): {tier_counts.get('C', 0)} 条")
    
    # ── 加载已存在 URL (去重) ──
    existing_urls = load_existing_urls(project_root, date)
    logger.info(f"已存在 URL 数量: {len(existing_urls)} (用于去重)")
    
    # ── 初始化搜索引擎 ──
    engines: List[SearchEngine] = []
    
    # Tavily
    tavily_engine = TavilySearchEngine(logger, search_config.get("tavily", {}))
    if tavily_engine.is_available():
        engines.append(tavily_engine)
        logger.info(f"搜索引擎就绪: Tavily")
    else:
        logger.warning("搜索引擎不可用: Tavily (跳过)")
    
    # XCrawl
    xcrawl_engine = XCrawlSearchEngine(logger, {})
    if xcrawl_engine.is_available():
        engines.append(xcrawl_engine)
        logger.info(f"搜索引擎就绪: XCrawl")
    else:
        logger.warning("搜索引擎不可用: XCrawl (跳过)")
    
    if not engines:
        logger.error("无可用搜索引擎，退出")
        return {
            "ok": False,
            "status": "error",
            "error": "无可用搜索引擎",
            "date": date,
            "total_discovered": 0,
            "total_new": 0,
        }
    
    # ── 执行搜索 ──
    all_results = []
    seen_urls_new = set()  # 本次搜索内去重
    
    tavily_used = 0
    xcrawl_used = 0
    tier_budget_limits = budget.get("tier_budget", {})
    
    # 各 tier 已执行计数
    tier_executed = Counter()
    tier_discovered = Counter()
    
    # ── 并发执行搜索 ──
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    
    _results_lock = threading.Lock()
    
    MAX_WORKERS = int(os.environ.get("BROAD_SEARCH_WORKERS", "8"))
    PER_QUERY_TIMEOUT = int(os.environ.get("BROAD_SEARCH_PER_QUERY_TIMEOUT", "30"))
    
    # 线程安全的共享状态
    _state = {
        "tavily_used": 0,
        "xcrawl_used": 0,
        "tier_executed": defaultdict(int),
        "tier_discovered": defaultdict(int),
        "errors": 0,
    }
    _state_lock = threading.Lock()
    
    def _execute_one_query(idx: int, query_text: str, query_type: str, query_tier: str) -> dict:
        """执行单个查询（线程安全）。"""
        # 预算检查
        with _state_lock:
            st = _state
            tier_key = f"tier_{query_tier.lower()}"
            tier_budget_cfg = tier_budget_limits.get(tier_key, {})
            tier_max = tier_budget_cfg.get("max_queries", 999) if query_tier != "A" else 9999
            if st["tier_executed"][query_tier] >= tier_max:
                return {"skipped": True, "reason": "tier budget", "new": 0}
            if query_tier != "A" and st["tavily_used"] >= tavily_daily_limit and st["xcrawl_used"] >= xcrawl_daily_limit:
                return {"skipped": True, "reason": "total budget", "new": 0}
            
            use_tavily = tavily_engine.is_available() and st["tavily_used"] < tavily_daily_limit
            # XCrawl: Tier C 不跑（节省 credits），A/B 按预算
            # 如果 Tavily 已耗尽的 query，XCrawl 作为 fallback
            use_xcrawl = (
                xcrawl_engine.is_available()
                and st["xcrawl_used"] < xcrawl_daily_limit
                and query_tier != "C"
            )
            # 双引擎都跑：A 类必须双跑，B 类按各自预算
            if use_tavily:
                st["tavily_used"] += 1
            if use_xcrawl:
                st["xcrawl_used"] += 1
        
        results_this_query = []
        
        # Tavily
        if use_tavily:
            try:
                tavily_time_range = "day" if query_tier == "A" else "week"
                r = tavily_engine.search(
                    query_text, max_results=max_results_per_query,
                    exclude_domains=DEFAULT_EXCLUDE_DOMAINS,
                    time_range=tavily_time_range,
                )
                results_this_query.extend(r)
            except Exception as e:
                logger.debug(f"[Tavily] query failed: {e}")
                with _state_lock:
                    _state["errors"] += 1
        
        # XCrawl
        if use_xcrawl:
            try:
                r = xcrawl_engine.search(query_text, max_results=max_results_per_query)
                results_this_query.extend(r)
            except Exception as e:
                logger.debug(f"[XCrawl] query failed: {e}")
                with _state_lock:
                    _state["errors"] += 1
        
        # 去重
        new_count = 0
        with _results_lock:
            for r in results_this_query:
                url_norm = normalize_url(r.get("url", ""))
                if not url_norm or url_norm in existing_urls or url_norm in seen_urls_new:
                    continue
                seen_urls_new.add(url_norm)
                r["discovery_type"] = query_type
                r["discovery_tier"] = query_tier
                r["discovered_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
                all_results.append(r)
                new_count += 1
        
        with _state_lock:
            _state["tier_executed"][query_tier] += 1
            _state["tier_discovered"][query_tier] += new_count
        
        return {"skipped": False, "new": new_count}
    
    # ── 提交到线程池 ──
    logger.info(f"并发执行 {len(queries)} 查询 (workers={MAX_WORKERS}, timeout={PER_QUERY_TIMEOUT}s)...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for i, q in enumerate(queries):
            fut = executor.submit(_execute_one_query, i, q["query"], q["type"], q["tier"])
            futures[fut] = i
        
        completed = 0
        for fut in as_completed(futures, timeout=900):
            try:
                fut.result(timeout=PER_QUERY_TIMEOUT + 5)
                completed += 1
            except Exception as e:
                logger.debug(f"Query {futures[fut]} timed out: {e}")
                completed += 1
            
            if completed % 30 == 0:
                with _state_lock:
                    logger.info(f"  进度: {completed}/{len(queries)} | "
                               f"新发现: {len(all_results)} URLs | "
                               f"A={_state['tier_discovered'].get('A',0)} "
                               f"B={_state['tier_discovered'].get('B',0)} "
                               f"C={_state['tier_discovered'].get('C',0)}")
    
    # 恢复计数器
    tavily_used = _state["tavily_used"]
    xcrawl_used = _state["xcrawl_used"]
    tier_executed = _state["tier_executed"]
    tier_discovered = _state["tier_discovered"]
    logger.info(f"搜索完成: {completed}/{len(queries)} 查询, {len(all_results)} 新URL")
    all_results = filter_urls_by_pattern(all_results)
    
    # ── 提取发布日期 & 计算 time_status ──
    date_extracted = 0
    time_status_counts = Counter()
    for r in all_results:
        pub = extract_published_date(r)
        if pub:
            r["published_at"] = pub
            date_extracted += 1
        ts = compute_time_status(pub, date)
        r["time_status"] = ts
        time_status_counts[ts] += 1
    
    logger.info(f"日期提取: {date_extracted}/{len(all_results)} 篇有发布日期")
    logger.info(f"时间状态分布: {dict(time_status_counts)}")
    
    # ── 分层统计 ──
    tier_stats = {f"tier_{t}": {"queries": tier_executed.get(t, 0), "discovered": tier_discovered.get(t, 0)} 
                  for t in ["A", "B", "C"]}
    
    # ── 保存结果 ──
    output_file = os.path.join(output_dir, "broad_search_urls.json")
    output_data = {
        "metadata": {
            "date": date,
            "total_discovered": len(all_results),
            "total_queries": sum(tier_executed.values()),
            "tier_stats": tier_stats,
            "queries_executed": {
                "tavily": tavily_engine.stats["queries"],
                "xcrawl": xcrawl_engine.stats["queries"] if xcrawl_engine.is_available() else 0,
            },
            "results_per_engine": {
                "tavily": tavily_engine.stats["results"],
                "xcrawl": xcrawl_engine.stats["results"] if xcrawl_engine.is_available() else 0,
            },
            "errors": {
                "tavily": tavily_engine.stats["errors"],
                "xcrawl": xcrawl_engine.stats["errors"] if xcrawl_engine.is_available() else 0,
            },
            "existing_urls_deduped": len(existing_urls),
            "query_distribution": dict(Counter(r.get("discovery_type", "unknown") for r in all_results)),
            "date_extraction": {
                "extracted": date_extracted,
                "total": len(all_results),
                "time_status": dict(time_status_counts),
            },
        },
        "articles": all_results,
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    logger.info("=" * 60)
    logger.info(f"广泛搜索发现完成:")
    logger.info(f"  总发现: {len(all_results)} 个新 URL")
    for t in ["A", "B", "C"]:
        logger.info(f"  Tier {t}: {tier_executed.get(t,0)} queries → {tier_discovered.get(t,0)} discovered")
    logger.info(f"  Tavily: {tavily_engine.stats['queries']} queries, {tavily_engine.stats['results']} results, {tavily_engine.stats['errors']} errors")
    if xcrawl_engine.is_available():
        logger.info(f"  XCrawl: {xcrawl_engine.stats['queries']} queries, {xcrawl_engine.stats['results']} results, {xcrawl_engine.stats['errors']} errors")
    logger.info(f"  去重后: 与现有数据不重复的新 URL")
    logger.info(f"  输出: {output_file}")
    logger.info("=" * 60)
    
    # ── 合并到原始文章池 ──
    if not skip_merge:
        logger.info("开始合并广泛搜索发现文章到原始文章池...")
        merge_result = merge_broad_articles(
            project_root=project_root,
            date=date,
            broad_articles=all_results,
            logger=logger,
        )
        output_data["metadata"]["merge"] = merge_result
        output_data["metadata"]["merged_file"] = merge_result.get("merged_file", "")
        logger.info(f"合并完成: raw_count_before={merge_result.get('raw_count_before', 0)}, "
                     f"broad_added={merge_result.get('broad_added', 0)}, "
                     f"raw_count_after={merge_result.get('raw_count_after', 0)}")
    else:
        logger.info("skip_merge=True，跳过合并")
        output_data["metadata"]["merge"] = None
    
    return {
        "ok": True,
        **output_data["metadata"],
    }


# ===================== 合并函数 =====================

def merge_broad_articles(
    project_root: str,
    date: str,
    broad_articles: List[dict],
    logger: logging.Logger,
    max_total: int = 1000,
) -> dict:
    """将广泛搜索发现的文章合并到原始文章池。
    
    如果 raw_articles_merged.json 已存在（tavily_gap_search 已合并过），
    在其基础上追加；否则在 raw_articles.json 基础上追加。
    
    输出始终写入 raw_articles_merged.json。
    """
    data_dir = resolve_path(project_root, f"data/raw/{date}")
    
    # 确定基础文件：优先 merged，其次 raw
    merged_file = os.path.join(data_dir, "raw_articles_merged.json")
    raw_file = os.path.join(data_dir, "raw_articles.json")
    
    if os.path.exists(merged_file):
        base_file = merged_file
        logger.info(f"基础文件: {merged_file} (已包含 tavily_gap 补搜)")
    elif os.path.exists(raw_file):
        base_file = raw_file
        logger.info(f"基础文件: {raw_file}")
    else:
        logger.warning(f"原始文章文件不存在，创建空基础")
        base_file = None
    
    # 加载基础文章
    if base_file:
        try:
            with open(base_file, "r", encoding="utf-8") as f:
                base_data = json.load(f)
            if isinstance(base_data, dict):
                existing_articles = base_data.get("articles", [])
                base_metadata = base_data.get("metadata", {})
            else:
                existing_articles = base_data
                base_metadata = {}
        except Exception as e:
            logger.warning(f"加载基础文件失败: {e}")
            existing_articles = []
            base_metadata = {}
    else:
        existing_articles = []
        base_metadata = {}
    
    logger.info(f"基础文章数: {len(existing_articles)}")
    
    # 去重合并
    all_urls = set()
    merged = []
    
    for a in existing_articles:
        url = a.get("url", "").strip().rstrip("/")
        if url and url not in all_urls:
            all_urls.add(url)
            merged.append(a)
    
    broad_added = 0
    broad_deduped = 0
    
    # 为 broad search 文章补充 role 标记
    for a in broad_articles:
        url = a.get("url", "").strip().rstrip("/")
        if not url:
            continue
        if url in all_urls:
            broad_deduped += 1
            continue
        
        # 标记为 broad_discovery
        if "role" not in a:
            a["role"] = "broad_discovery"
        if "freshness_status" not in a:
            a["freshness_status"] = "broad_discovery"
        
        all_urls.add(url)
        merged.append(a)
        broad_added += 1
    
    # 截断
    if len(merged) > max_total:
        logger.warning(f"合并后 {len(merged)} 条超过上限 {max_total}，截断")
        merged = merged[:max_total]
    
    # 更新 metadata
    base_metadata["broad_search_added"] = broad_added
    base_metadata["broad_search_deduped"] = broad_deduped
    base_metadata["merged_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    if os.path.exists(merged_file):
        base_metadata["merge_base"] = "raw_articles_merged"
    else:
        base_metadata["merge_base"] = "raw_articles"
    
    # 写入
    merged_data = {
        "metadata": base_metadata,
        "articles": merged,
    }
    
    with open(merged_file, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"合并文件已保存: {merged_file} "
                 f"(基础 {len(existing_articles)} + 广搜新增 {broad_added} = {len(merged)})")
    
    return {
        "merged_file": merged_file,
        "raw_count_before": len(existing_articles),
        "broad_added": broad_added,
        "broad_deduped": broad_deduped,
        "raw_count_after": len(merged),
    }


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="广泛搜索发现模块")
    parser.add_argument("--project-root", required=True, help="项目根目录路径")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--verbose", action="store_true", help="启用详细日志")
    parser.add_argument("--skip-merge", action="store_true",
                        help="仅执行搜索发现，不合并到原始文章池")
    args = parser.parse_args()
    
    result = broad_search_discovery(
        project_root=args.project_root,
        date=args.date,
        verbose=args.verbose,
        skip_merge=args.skip_merge,
    )
    
    print(f"\n📊 广泛搜索发现结果:")
    print(f"  总发现: {result.get('total_discovered', 0)} 个新 URL")
    print(f"  Tavily queries: {result.get('queries_executed', {}).get('tavily', 0)}")
    print(f"  XCrawl queries: {result.get('queries_executed', {}).get('xcrawl', 0)}")
    merge_info = result.get('merge', {})
    if merge_info:
        print(f"  合并: base={merge_info.get('raw_count_before', 0)}, "
              f"added={merge_info.get('broad_added', 0)}, "
              f"after={merge_info.get('raw_count_after', 0)}")


if __name__ == "__main__":
    main()