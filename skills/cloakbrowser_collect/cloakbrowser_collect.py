#!/usr/bin/env python3
"""
cloakbrowser_collect.py — 使用 CloakBrowser 隐身浏览器直接抓取目标新闻站点（增强版）

绕过 XCrawl/Tavily 等搜索 API 的日期标注不准、旧闻混入、内容缺失等问题。
直接从新闻聚合页提取最新的文章列表，获取真实的发布日期和完整摘要。

目标站点（2026-05-15 两轮探测，共 91 个候选站点）:
  ✅ 可采（18变体，理论~150篇/天）:
     ▸ 36kr×5 + 华尔街见闻×2 + 品玩 + 钛媒体 (科技商业 9)
     ▸ 财联社×2 + 每日经济新闻 + 界面新闻×2 (财经 5)
     ▸ 品观 (美妆垂直 1)
     ▸ 新浪×2 (综合门户 2)
     ▸ 补丁版 Chromium 新解锁: 华尔街见闻、品玩、每日经济新闻
  ⚠️ AJAX阻塞 (9站，可访问但搜索结果JS渲染):
     DoNews, 第一财经, 经济观察报, 亿欧网, 创业邦,
     投资界, 极客公园, 网经社, 亿邦动力
  ❌ 网络阻塞 (10+): DNS/SSL/连接重置

输出: {date}/cloakbrowser_articles.json (兼容 raw_articles 格式)

用法:
    python cloakbrowser_collect.py --project-root /path --date 2026-05-14
    python cloakbrowser_collect.py --project-root /path --date 2026-05-14 --max-per-source 8 --verbose
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
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

# ═══ Bootstrap ═══
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_PACKAGES = PROJECT_ROOT / ".venv_packages"
if VENV_PACKAGES.exists():
    sys.path.insert(0, str(VENV_PACKAGES))

CST = timezone(timedelta(hours=8))

# ═══ 目标站点配置 ═══
TARGET_SITES = [
    # ═══════════════ 主力源（已接入、已验证） ═══════════════
    # ── 36kr（5 个搜索变体，日期100%准确）──
    {
        "name": "36kr_instant_retail",
        "description": "36氪-即时零售",
        "url": "https://36kr.com/search/articles/%E5%8D%B3%E6%97%B6%E9%9B%B6%E5%94%AE",
        "link_selector": "a.article-item-title",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 10,
        "category": "tech_media",
    },
    {
        "name": "36kr_beauty",
        "description": "36氪-美妆个护",
        "url": "https://36kr.com/search/articles/%E7%BE%8E%E5%A6%86%20%E4%B8%AA%E6%8A%A4",
        "link_selector": "a.article-item-title",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 10,
        "category": "tech_media",
    },
    {
        "name": "36kr_new_retail",
        "description": "36氪-新零售",
        "url": "https://36kr.com/search/articles/%E6%96%B0%E9%9B%B6%E5%94%AE%20%E7%BE%8E%E5%A6%86",
        "link_selector": "a.article-item-title",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 8,
        "category": "tech_media",
    },
    {
        "name": "36kr_consumer",
        "description": "36氪-消费零售+屈臣氏",
        "url": "https://36kr.com/search/articles/%E6%B6%88%E8%B4%B9%20%E9%9B%B6%E5%94%AE%20%E5%B1%88%E8%87%A3%E6%B0%8F",
        "link_selector": "a.article-item-title",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 6,
        "category": "tech_media",
    },
    {
        "name": "36kr_supply_chain",
        "description": "36氪-供应链+O2O",
        "url": "https://36kr.com/search/articles/%E4%BE%9B%E5%BA%94%E9%93%BE%20O2O%20%E5%8D%B3%E6%97%B6%E9%85%8D%E9%80%81",
        "link_selector": "a.article-item-title",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 6,
        "category": "tech_media",
    },

    # ── 品观（美妆垂直媒体）──
    {
        "name": "pinguan_beauty",
        "description": "品观-美妆行业",
        "url": "https://www.pinguan.com/",
        "link_selector": "a[href*='/article/content/'], .hotnews_list a[href*='/article/'], .news-list a[href*='/article/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d+)\s*(小时|分钟|天)\s*前|(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 10,
        "category": "beauty_media",
    },

    # ── 新浪（频道页，内容太泛化，禁用）──
    {
        "name": "sina_consumer",
        "description": "新浪财经-消费",
        "disabled": True,
        "note": "频道页内容太泛化，采到的全是无关消费新闻",
        "url": "https://finance.sina.com.cn/consume/",
        "link_selector": "a[href*='/consume/'], .list-news a, .news-item a",
        "date_from": "url",
        "date_pattern": r'/(\d{4})[-/](\d{2})[-/](\d{2})/',
        "max_articles": 8,
        "category": "portal",
    },
    {
        "name": "sina_beauty",
        "description": "新浪时尚-美妆",
        "disabled": True,
        "note": "频道页内容太泛化，采到的全是无关美妆新闻",
        "url": "https://fashion.sina.com.cn/beauty/",
        "link_selector": "a[href*='/beauty/'], .list-news a, .news-item a, h3 a",
        "date_from": "url",
        "date_pattern": r'/(\d{4})[-/](\d{2})[-/](\d{2})/',
        "max_articles": 6,
        "category": "portal",
    },

    # ═══════════════ 补丁版 Chromium 新解锁（2026-05-15） ═══════════════
    
    # ── 财联社（搜索页 SPA，补丁版绕过）──
    {
        "name": "cls_instant_retail",
        "description": "财联社-即时零售",
        "url": "https://www.cls.cn/searchPage?keyword=%E5%8D%B3%E6%97%B6%E9%9B%B6%E5%94%AE&type=all",
        "link_selector": "a[href*='/detail/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 10,
        "category": "finance_tech",
        "wait_until": "networkidle",
        "note": "补丁版Chromium解锁SPA渲染",
    },
    {
        "name": "cls_beauty",
        "description": "财联社-美妆个护",
        "url": "https://www.cls.cn/searchPage?keyword=%E7%BE%8E%E5%A6%86+%E4%B8%AA%E6%8A%A4&type=all",
        "link_selector": "a[href*='/detail/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 8,
        "category": "finance_tech",
        "wait_until": "networkidle",
    },

    # ── 界面新闻（频道页，内容与即时零售/美妆无关，禁用）──
    {
        "name": "jiemian_tech",
        "description": "界面新闻-科技频道",
        "disabled": True,
        "note": "频道页不是搜索页，采到的全是无关内容",
        "url": "https://www.jiemian.com/lists/63.html",
        "link_selector": "a[href*='/article/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 10,
        "category": "finance_tech",
        "wait_until": "networkidle",
    },
    {
        "name": "jiemian_finance",
        "description": "界面新闻-财经频道",
        "disabled": True,
        "note": "频道页不是搜索页，采到的全是无关内容",
        "url": "https://www.jiemian.com/lists/66.html",
        "link_selector": "a[href*='/article/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 8,
        "category": "finance_tech",
        "wait_until": "networkidle",
    },

    # ── 钛媒体（搜索页，补丁版绕过SPA）──
    {
        "name": "tmtpost_search",
        "description": "钛媒体-即时零售",
        "url": "https://www.tmtpost.com/search?q=%E5%8D%B3%E6%97%B6%E9%9B%B6%E5%94%AE",
        "link_selector": "a[href*='/a/'], a[href$='.html']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 6,
        "category": "tech_media",
        "wait_until": "networkidle",
        "note": "SPA搜索页，需等待JS渲染。排除导航链接",
    },

    # ═══════════════ 第二轮探测新解锁（2026-05-15） ═══════════════
    
    # ── 华尔街见闻（搜索页直出文章链接，22篇/搜索）──
    {
        "name": "wallstreetcn_instant",
        "description": "华尔街见闻-即时零售",
        "url": "https://wallstreetcn.com/search?q=%E5%8D%B3%E6%97%B6%E9%9B%B6%E5%94%AE",
        "link_selector": "a[href*='/articles/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 10,
        "category": "finance_tech",
        "wait_until": "networkidle",
    },
    {
        "name": "wallstreetcn_beauty",
        "description": "华尔街见闻-美妆个护",
        "url": "https://wallstreetcn.com/search?q=%E7%BE%8E%E5%A6%86+%E4%B8%AA%E6%8A%A4",
        "link_selector": "a[href*='/articles/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 5,
        "category": "finance_tech",
        "wait_until": "networkidle",
    },

    # ── 品玩（快讯页，内容全是科技/AI，与即时零售/美妆无关，禁用）──
    {
        "name": "pingwest_status",
        "description": "品玩-科技快讯",
        "disabled": True,
        "note": "内容全是科技/AI，与即时零售/美妆无关",
        "url": "https://www.pingwest.com/status",
        "link_selector": "a[href*='/a/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})',
        "max_articles": 8,
        "category": "tech_media",
        "wait_until": "networkidle",
    },

    # ── 每日经济新闻（专栏页，内容太泛化，禁用）──
    {
        "name": "nbd_topnews",
        "description": "每日经济新闻-要闻",
        "disabled": True,
        "note": "专栏页内容太泛化，采到的全是无关财经新闻",
        "url": "https://www.nbd.com.cn/columns/3/",
        "link_selector": "a[href*='/articles/']",
        "date_from": "url",
        "date_pattern": r'/articles/(\d{4}-\d{2}-\d{2})/',
        "max_articles": 8,
        "category": "finance_tech",
        "wait_until": "networkidle",
    },

    # ═══════════════ 待激活（可达但需开发） ═══════════════
    # 以下可访问但搜索结果 AJAX 动态渲染，需 API 逆向或 wait_for_selector
    
    # DoNews — 可访问但搜索结果 AJAX 渲染
    # 第一财经(yicai.com) — 频道页可访问但文章链接 AJAX 渲染
    # 经济观察报(eeo.com.cn) — 同上
    # 亿欧网(iyiou.com) — 同上
    # 创业邦(cyzone.cn) — 同上
    # 投资界(pedaily.cn) — 同上
    # 极客公园(geekpark.net) — 同上
    # 网经社(100ec.cn) — 同上
    # 亿邦动力(ebrun.com) — tag页返回专题非文章
    # 美妆头条(mztoutiao.com) — 可访问，搜索/频道页返回错误
    # 化妆品报(hzpb.com.cn) — 可访问，搜索/频道页返回错误
    # C2CC传媒(c2cc.cn) — 可访问，子路径全部404
    # chinabeauty.cn — 可访问，无可用搜索/频道页
    # 美丽修行(bevol.cn) — 成分数据库，资讯页为空

    # ═══════════════ 已禁用（网络限制或需特殊解析） ═══════════════
    # CBO化妆品财经(timeout, DNS) | 联商网(timeout, DNS)
    # 亿邦动力(可访问但tag页返回专题非文章，需找文章列表页)
    # 百度新闻(可访问但链接经百度重定向，需特殊解析真实URL)
    # 零售商业评论/第三只眼/灵兽/零售圈(连接错误)

    # ═══════════════ 2026-05-18 新增：虎嗅等即时零售资讯核心源 ═══════════════
    # ── 虎嗅（即时零售/美妆核心源，2026-05-18验证有高频更新）──
    {
        "name": "huxiu_consume_channel",
        "description": "虎嗅-消费频道",
        "url": "https://www.huxiu.com/channel/103.html",
        "link_selector": "a.article-item-title, a[href*='/article/'], .article-item h3 a, .mod-b-list a",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})|(\d+)\s*(小时|分钟)\s*前',
        "max_articles": 15,
        "category": "tech_media",
        "wait_until": "networkidle",
    },
    {
        "name": "huxiu_retail_channel",
        "description": "虎嗅-零售频道",
        "url": "https://www.huxiu.com/channel/114.html",
        "link_selector": "a.article-item-title, a[href*='/article/'], .article-item h3 a, .mod-b-list a",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})|(\d+)\s*(小时|分钟)\s*前',
        "max_articles": 15,
        "category": "tech_media",
        "wait_until": "networkidle",
    },
    {
        "name": "huxiu_search_instant",
        "description": "虎嗅搜索-即时零售",
        "url": "https://www.huxiu.com/search.html?s=即时零售&sort=time",
        "link_selector": "a.article-item-title, a[href*='/article/'], .search-result-item a, .list-item a[href*='/article/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})|(\d+)\s*(小时|分钟)\s*前',
        "max_articles": 10,
        "category": "tech_media",
    },
    {
        "name": "huxiu_search_beauty",
        "description": "虎嗅搜索-美妆个护",
        "url": "https://www.huxiu.com/search.html?s=美妆 个护&sort=time",
        "link_selector": "a.article-item-title, a[href*='/article/'], .search-result-item a, .list-item a[href*='/article/']",
        "date_from": "parent_text",
        "date_pattern": r'(\d{4}[-/]\d{2}[-/]\d{2})|(\d+)\s*(小时|分钟)\s*前',
        "max_articles": 10,
        "category": "tech_media",
    },
]


def _extract_date_from_text(text: str, pattern: Optional[str], now: datetime) -> Optional[str]:
    """从文本中提取日期。支持相对日期（X小时前）和绝对日期（YYYY-MM-DD）。"""
    if not text or not pattern:
        return None
    
    m = re.search(pattern, text)
    if not m:
        return None
    
    try:
        groups = m.groups()
        # 相对日期: "3小时前", "5天前"
        # 支持 OR 模式: groups[1] 可能是 None（当绝对日期匹配时）
        if len(groups) >= 2 and groups[0] is not None and groups[1] in ('小时', '分钟', '天', 'h', 'min', 'd'):
            num = int(groups[0])
            unit = groups[1]
            if unit in ('小时', 'h'):
                return (now - timedelta(hours=num)).strftime('%Y-%m-%d')
            elif unit in ('分钟', 'min'):
                return now.strftime('%Y-%m-%d')
            elif unit in ('天', 'd'):
                return (now - timedelta(days=num)).strftime('%Y-%m-%d')
        # 绝对日期 YYYY-MM-DD (3 groups: year, month, day)
        elif len(groups) >= 3 and groups[0] is not None and groups[1] is not None and groups[2] is not None:
            y, m_, d = int(groups[0]), int(groups[1]), int(groups[2])
            if 2020 <= y <= 2030 and 1 <= m_ <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m_:02d}-{d:02d}"
        # 绝对日期 YYYY-MM-DD (OR 模式: groups[2] 是日期，groups[0] 和 groups[1] 是 None)
        elif len(groups) >= 3 and groups[2] is not None and len(groups[2]) >= 8:
            date_str = groups[2][:10].replace('/', '-')
            # 验证日期格式
            parts = date_str.split('-')
            if len(parts) == 3:
                y, m_, d = int(parts[0]), int(parts[1]), int(parts[2])
                if 2020 <= y <= 2030 and 1 <= m_ <= 12 and 1 <= d <= 31:
                    return date_str
        # 绝对日期 YYYY-MM-DD (一个group含分隔符)
        elif len(groups) == 1 and groups[0] is not None and len(groups[0]) >= 8:
            return groups[0][:10].replace('/', '-')
    except (ValueError, IndexError, AttributeError):
        pass
    return None


def _url_to_article_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def _extract_url_date(url: str) -> Optional[str]:
    """从 URL 提取日期。"""
    m = re.search(r'/(\d{4})[/_\-](\d{2})[/_\-](\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'/(\d{4})(\d{2})(\d{2})', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def collect_from_site(browser, site: dict, now: datetime, logger) -> List[dict]:
    """使用 CloakBrowser 从单个站点收集文章。"""
    articles = []
    max_articles = site.get("max_articles", 8)
    
    try:
        page = browser.new_page()
        logger.info(f"  🌐 {site['description']}: {site['url'][:70]}...")
        
        try:
            page.goto(site["url"], wait_until=site.get("wait_until", "domcontentloaded"), timeout=30000)
        except Exception:
            # 超时也继续，部分内容可能已加载
            pass
        
        page.wait_for_timeout(3000)
        
        # 尝试多个 selector
        selectors = [site["link_selector"]] if isinstance(site["link_selector"], str) else site["link_selector"]
        links = []
        for sel in selectors:
            try:
                found = page.query_selector_all(sel)
                if found and len(found) > 0:
                    links = found
                    break
            except Exception:
                continue
        
        logger.info(f"  找到 {len(links)} 个链接元素 (selector: {selectors[0][:40]})")
        
        count = 0
        seen_titles = set()
        for link in links[:max_articles * 3]:
            if count >= max_articles:
                break
            
            try:
                href = link.get_attribute("href") or ""
                title = link.inner_text().strip()
                
                # 过滤
                if not href or not title or len(title) < 8:
                    continue
                if title in seen_titles:
                    continue
                
                # 跳过导航/非文章链接
                skip_patterns = ('登录', '注册', '首页', '关于', '更多', '下一页', '返回')
                if any(title.startswith(p) for p in skip_patterns):
                    continue
                
                seen_titles.add(title)
                
                # 补全URL
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    parsed = urlparse(site["url"])
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                
                # 提取日期
                date_str = None
                date_source = site.get("date_from", "parent_text")
                
                if date_source == "url":
                    date_str = _extract_url_date(href)
                
                if not date_str and site.get("date_pattern"):
                    # 从父元素文本提取（包含兄弟节点的文本）
                    try:
                        surrounding = page.evaluate("""
                            (el) => {
                                let p = el.closest('div,li,article,section');
                                if (!p) return '';
                                // 获取父元素的完整文本（包含所有子节点）
                                let text = p.innerText || '';
                                // 如果父元素文本太短，尝试祖父元素
                                if (text.length < 30 && p.parentElement) {
                                    text = p.parentElement.innerText || '';
                                }
                                return text.substring(0, 500);
                            }
                        """, link)
                        date_str = _extract_date_from_text(surrounding, site["date_pattern"], now)
                    except Exception:
                        pass
                
                if not date_str:
                    # 日期提取失败 → 不伪造日期，留空让硬日期门处理
                    date_str = None
                
                article = {
                    "article_id": _url_to_article_id(href),
                    "title": title,
                    "url": href,
                    "published_at": date_str or "",  # None → "" 避免序列化问题
                    "source": f"cloakbrowser_{site['name']}",
                    "source_name": f"cloakbrowser_{site['name']}",
                    "source_category": site.get("category", "general"),
                    "source_type": "cloakbrowser",
                    "collector": "cloakbrowser",
                    "collected_at": now.isoformat(),
                    "description": title,
                    "time_status": "in_window" if (date_str and date_str == now.strftime('%Y-%m-%d')) else "uncertain_date",
                }
                
                articles.append(article)
                count += 1
                
            except Exception as e:
                logger.debug(f"  跳过: {e}")
                continue
        
        logger.info(f"  ✅ {site['name']}: {len(articles)} 篇")
        
        # 如果 selector 没匹配到，尝试通用 fallback
        if len(articles) == 0:
            logger.info(f"  🔄 {site['name']}: 尝试通用 fallback selector...")
            fallback_links = page.query_selector_all("a[href]")
            for link in fallback_links[:50]:
                try:
                    href = link.get_attribute("href") or ""
                    title = link.inner_text().strip()
                    if not href or not title or len(title) < 8:
                        continue
                    if title in seen_titles:
                        continue
                    # 只保留有新闻特征的（含关键词）
                    kw = ['零售', '美妆', '电商', '品牌', '即时', '小时达', '闪购', '屈臣氏',
                          '丝芙兰', '前置仓', '美团', '京东', '淘宝', '抖音']
                    if not any(k in title for k in kw):
                        continue
                    seen_titles.add(title)
                    
                    if href.startswith("//"): href = "https:" + href
                    elif href.startswith("/"):
                        parsed = urlparse(site["url"])
                        href = f"{parsed.scheme}://{parsed.netloc}{href}"
                    
                    articles.append({
                        "article_id": _url_to_article_id(href),
                        "title": title,
                        "url": href,
                        "published_at": "",  # fallback无日期，不伪造
                        "source": f"cloakbrowser_{site['name']}_fallback",
                        "source_name": f"cloakbrowser_{site['name']}",
                        "source_category": site.get("category", "general"),
                        "source_type": "cloakbrowser",
                        "collector": "cloakbrowser",
                        "collected_at": now.isoformat(),
                        "description": title,
                        "time_status": "uncertain_date",
                    })
                    if len(articles) >= max_articles:
                        break
                except Exception:
                    continue
            logger.info(f"  ✅ {site['name']} (fallback): {len(articles)} 篇")
        
    except Exception as e:
        logger.warning(f"  ❌ {site['name']}: {type(e).__name__}: {str(e)[:100]}")
    finally:
        try:
            page.close()
        except Exception:
            pass
    
    return articles


def run_cloakbrowser_collect(
    project_root: str,
    date: str,
    max_per_source: int = 8,
    headless: bool = True,
    verbose: bool = False,
) -> dict:
    """主入口。Returns {"ok": True/False, "total": N, "articles": [...], "errors": [...]}"""
    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / "cloakbrowser_collect.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("cloakbrowser_collect")
    
    logger.info("=" * 60)
    logger.info(f"CloakBrowser 四级采集: date={date}")
    logger.info(f"目标站点: {len(TARGET_SITES)} 个, max_per_source={max_per_source}")
    logger.info("=" * 60)
    
    errors = []
    all_articles = []
    now = datetime.now(CST)
    
    # 检查 CloakBrowser
    try:
        from cloakbrowser import launch, get_default_stealth_args
    except ImportError as e:
        return {
            "ok": False, "total": 0, "articles": [],
            "errors": [f"cloakbrowser not installed: {e}"],
        }
    
    # Fallback: 使用系统 Chromium 如果补丁版不存在
    if not os.environ.get("CLOAKBROWSER_BINARY_PATH"):
        # Check patched binary first
        # Workspace-persisted patched binary
        import pathlib as _pl
        _proj = _pl.Path(__file__).resolve().parents[2]  # project root
        patched = str(_proj / "tools" / "cloakbrowser" / "chromium-146.0.7680.177.3" / "chrome")
        # Also check legacy path
        _legacy = "/root/.cloakbrowser/chromium-146.0.7680.177.3/chrome"
        if os.path.exists(_legacy) and not os.path.exists(patched):
            patched = _legacy
        if os.path.exists(patched):
            os.environ["CLOAKBROWSER_BINARY_PATH"] = patched
            logger.info(f"使用补丁版 Chromium 146: {patched}")
        else:
            for sys_chrome in ["/usr/bin/chromium", "/usr/bin/chromium-browser"]:
                if os.path.exists(sys_chrome):
                    os.environ["CLOAKBROWSER_BINARY_PATH"] = sys_chrome
                    logger.info(f"使用系统 Chromium: {sys_chrome}")
                    break
    
    browser = None
    try:
        logger.info("启动 CloakBrowser (headless)...")
        browser = launch(headless=headless, args=get_default_stealth_args() + ["--no-sandbox", "--disable-dev-shm-usage"])
        
        # 按类别分组采集
        from collections import defaultdict
        by_category = defaultdict(list)
        for site in TARGET_SITES:
            if site.get("disabled"):
                logger.info(f"  ⏭️ 跳过禁用站点: {site['name']} ({site.get('note', 'disabled')})")
                continue
            by_category[site.get("category", "general")].append(site)
        
        for category, sites in by_category.items():
            logger.info(f"\n📂 类别: {category} ({len(sites)} 个站点)")
            for site in sites:
                try:
                    articles = collect_from_site(
                        browser, site, now, logger
                    )
                    all_articles.extend(articles)
                except Exception as e:
                    msg = f"{site['name']}: {e}"
                    logger.error(f"  ❌ {msg}")
                    errors.append(msg)
                # 请求间隔，避免触发反爬限流
                import time as _t
                _t.sleep(3)
        
    except Exception as e:
        logger.error(f"浏览器启动失败: {e}")
        errors.append(f"browser launch failed: {e}")
    finally:
        if browser:
            try:
                browser.close()
                logger.info("浏览器已关闭")
            except Exception:
                pass
    
    # 去重
    seen = set()
    deduped = []
    for a in all_articles:
        url = a.get("url", "")
        if url and url not in seen:
            seen.add(url)
            deduped.append(a)
    
    # ── 日期过滤：丢弃明显旧文（>3天前）──
    from datetime import datetime as _dt, timedelta as _td
    _date_str = date  # e.g. "2026-05-15"
    try:
        _report_dt = _dt.strptime(_date_str, "%Y-%m-%d")
        _cutoff = _report_dt - _td(days=3)
        _fresh = []
        _stale = 0
        for a in deduped:
            pub = a.get("published_at", "") or a.get("date", "")
            try:
                pub_dt = _dt.strptime(str(pub)[:10], "%Y-%m-%d")
                if pub_dt < _cutoff:
                    _stale += 1
                    continue
            except:
                pass  # no parseable date → keep it
            _fresh.append(a)
        if _stale > 0:
            logger.info(f"  📅 日期过滤: 丢弃 {_stale} 篇过期文章 (>{_td(days=3).days}天前)")
        deduped = _fresh
    except Exception:
        pass
    
    # 按来源统计
    source_counts = {}
    for a in deduped:
        src = a.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
    
    logger.info(f"\n{'='*60}")
    logger.info(f"采集完成: {len(all_articles)} 篇原始 → {len(deduped)} 篇去重后")
    logger.info(f"来源分布: {json.dumps(source_counts, ensure_ascii=False)}")
    if errors:
        logger.warning(f"错误: {len(errors)} 个")
    
    # 输出
    output_file = raw_dir / "cloakbrowser_articles.json"
    output = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "collector": "cloakbrowser",
            "created_at": now.isoformat(),
            "total_raw": len(all_articles),
            "total_deduped": len(deduped),
            "sources_attempted": len(TARGET_SITES),
            "sources_successful": len([s for s in source_counts if source_counts[s] > 0]),
            "by_source": source_counts,
            "errors": errors,
        },
        "articles": deduped,
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    logger.info(f"输出: {output_file}")
    
    return {
        "ok": True,
        "total": len(deduped),
        "file": str(output_file),
        "by_source": source_counts,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="CloakBrowser 四级采集器")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--max-per-source", type=int, default=8)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    
    result = run_cloakbrowser_collect(
        project_root=args.project_root,
        date=args.date,
        max_per_source=args.max_per_source,
        headless=not args.visible,
        verbose=args.verbose,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
