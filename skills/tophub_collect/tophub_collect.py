#!/usr/bin/env python3
"""
tophub_collect.py — TopHub.today 聚合搜索采集器

利用 tophub.today 的聚合搜索功能，一次性覆盖数百个新闻源。
无需登录即可搜索，按时间排序获取最新文章。

优势：
  - 覆盖 Sina/Huxiu/Sohu/Ebrun/36kr/Readhub/163/Google News 等数百源
  - 精确的发布时间（分钟级）
  - 完整标题 + 摘要
  - 无需维护各站点选择器

搜索策略：
  - "即时零售" — 核心赛道
  - "美妆 零售" — 美妆+零售交叉
  - "本地生活 到店" — 本地生活/门店机会

输出: data/raw/{date}/tophub_articles.json (标准 raw_articles 格式)

用法:
    python3 -m skills.tophub_collect.tophub_collect --project-root . --date 2026-05-19
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

# ═══ Bootstrap ═══
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_PACKAGES = PROJECT_ROOT / ".venv_packages"
if VENV_PACKAGES.exists():
    sys.path.insert(0, str(VENV_PACKAGES))

CST = timezone(timedelta(hours=8))

# ═══ 搜索配置 ═══
SEARCH_QUERIES = [
    "即时零售",
    "美妆 零售",
    "本地生活 到店",
    "屈臣氏",
    "美团闪购",
    "前置仓 闪电仓",
]

# 来源域名 → 中文名映射
DOMAIN_SOURCE_MAP = {
    "163.com": "网易",
    "huxiu.com": "虎嗅",
    "sohu.com": "搜狐",
    "sina.com": "新浪",
    "sina.com.cn": "新浪",
    "sina.cn": "新浪",
    "bjd.com.cn": "北京日报",
    "ebrun.com": "亿邦动力",
    "36kr.com": "36氪",
    "readhub.cn": "Readhub",
    "myzaker.com": "ZAKER",
    "wallstreetcn.com": "华尔街见闻",
    "baijing.cn": "白鲸出海",
    "qq.com": "腾讯",
    "10jqka.com.cn": "同花顺",
    "chinastarmarket.cn": "科创板日报",
    "google.com": "Google News",
    "news.google.com": "Google News",
    "thepaper.cn": "澎湃",
    "jiemian.com": "界面",
    "cls.cn": "财联社",
    "nbd.com.cn": "每日经济新闻",
    "yicai.com": "第一财经",
    "caixin.com": "财新",
    "latepost.com": "晚点",
    "pinguan.com": "品观",
}

# 提取文章的 JavaScript（在浏览器中执行）
EXTRACT_JS = """
(function() {
    const articles = [];
    const h3s = document.querySelectorAll('h3');
    
    h3s.forEach(h3 => {
        const link = h3.querySelector('a[href]');
        if (!link) return;
        
        const title = link.textContent.trim();
        const url = link.href;
        if (!title || title.length < 5) return;
        if (url.includes('tophub.today')) return;
        
        // 找到包含此 h3 的最近容器
        let container = h3.closest('div, li, article, section');
        if (!container) container = h3.parentElement;
        if (!container) return;
        
        const allText = container.innerText || container.textContent || '';
        
        // 提取日期 YYYY-MM-DD
        let pubDate = '', pubTime = '', domain = '';
        const dateMatch = allText.match(/(\\d{4}-\\d{2}-\\d{2})/);
        if (dateMatch) {
            pubDate = dateMatch[1];
            const timeMatch = allText.match(/(\\d{2}:\\d{2})/);
            if (timeMatch) pubTime = timeMatch[1];
        }
        
        // 从 URL 提取域名
        try {
            const u = new URL(url);
            domain = u.hostname.replace(/^www\\./, '');
        } catch(e) {}
        
        // 提取摘要：h3 后面的 p 标签或长文本
        let summary = '';
        let nextEl = h3.nextElementSibling;
        for (let i = 0; i < 3 && nextEl; i++) {
            if (nextEl.tagName === 'P' || nextEl.classList.contains('desc')) {
                summary = nextEl.textContent.trim();
                break;
            }
            nextEl = nextEl.nextElementSibling;
        }
        // 如果标题本身很长（tophub 有时把摘要放在标题里），截取
        if (!summary && title.length > 80) {
            summary = title;
        }
        
        articles.push({
            title: title.substring(0, 200),
            url: url,
            domain: domain,
            pub_date: pubDate,
            pub_time: pubTime,
            summary: (summary || title).substring(0, 500)
        });
    });
    
    // 去重
    const seen = new Set();
    return articles.filter(a => {
        if (seen.has(a.url)) return false;
        seen.add(a.url);
        return true;
    });
})()
"""


def _domain_to_source(domain: str) -> str:
    """域名映射为中文来源名。"""
    for k, v in DOMAIN_SOURCE_MAP.items():
        if k in domain:
            return v
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return domain or "Unknown"


def _search_tophub(browser, page, query: str, logger: logging.Logger) -> List[Dict]:
    """执行一次 tophub 搜索并提取结果。"""
    encoded_q = quote(query)
    url = f"https://tophub.today/search?q={encoded_q}&orderby=time"
    
    logger.info(f"  搜索: {query} → {url}")
    
    try:
        page.goto(url, timeout=45000, wait_until="networkidle")
        
        # 检测是否触发了验证码
        captcha_check = page.query_selector("text=安全验证")
        if captcha_check:
            logger.warning(f"  ⚠️ 触发验证码！Cookie 可能已失效，跳过此查询")
            return []
        
        # 多种等待策略
        try:
            page.wait_for_selector("h3 a[href]", timeout=20000)
        except Exception:
            try:
                page.wait_for_selector("a[href*='http']", timeout=10000)
            except Exception:
                pass
        
        time.sleep(3)  # 额外等待动态渲染
        
        # 执行提取 JS
        raw_articles = page.evaluate(EXTRACT_JS)
        logger.info(f"  → 提取到 {len(raw_articles)} 篇文章")
        return raw_articles
        
    except Exception as e:
        logger.warning(f"  搜索失败 [{query}]: {e}")
        return []


def run_tophub_collect(
    project_root: str,
    date: str,
    queries: List[str] = None,
    headless: bool = True,
    max_days_back: int = 2,
    verbose: bool = False,
) -> dict:
    """TopHub 聚合采集主入口。
    
    Args:
        project_root: 项目根目录
        date: 采集日期 YYYY-MM-DD
        queries: 搜索关键词列表（默认使用 SEARCH_QUERIES）
        headless: 是否无头模式
        max_days_back: 最多保留几天前的文章（默认2天）
        verbose: 详细日志
    
    Returns:
        {"ok": bool, "total": int, "articles": [...], "errors": [...]}
    """
    if queries is None:
        queries = SEARCH_QUERIES
    
    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / "tophub_collect.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    logger = logging.getLogger("tophub_collect")
    
    logger.info("=" * 60)
    logger.info(f"TopHub 聚合采集: date={date}, queries={queries}")
    logger.info("=" * 60)
    
    # 计算日期窗口
    target_date = datetime.strptime(date, "%Y-%m-%d")
    cutoff_date = (target_date - timedelta(days=max_days_back)).strftime("%Y-%m-%d")
    logger.info(f"日期窗口: {cutoff_date} ~ {date}")
    
    errors = []
    all_raw = []
    
    # 启动浏览器
    try:
        from cloakbrowser import launch, get_default_stealth_args
        use_cloakbrowser = True
    except ImportError:
        use_cloakbrowser = False
    
    browser = None
    page = None
    
    try:
        if use_cloakbrowser:
            logger.info("使用 CloakBrowser 隐身浏览器")
            # 查找 Chromium 二进制（通过环境变量传递给 cloakbrowser）
            patched = str(project_root_path / "tools" / "cloakbrowser" / "chromium-146.0.7680.177.3" / "chrome")
            legacy = "/root/.cloakbrowser/chromium-146.0.7680.177.3/chrome"
            system_chromium = "/usr/bin/chromium"
            
            if not os.environ.get("CLOAKBROWSER_BINARY_PATH"):
                for p in [patched, legacy, system_chromium]:
                    if os.path.exists(p):
                        os.environ["CLOAKBROWSER_BINARY_PATH"] = p
                        logger.info(f"Chromium binary: {p}")
                        break
            
            stealth_args = get_default_stealth_args()
            browser = launch(
                headless=headless,
                args=stealth_args + ["--no-sandbox", "--disable-dev-shm-usage"],
            )
        else:
            # Fallback: 使用 playwright 直接启动
            logger.info("CloakBrowser 不可用，使用 Playwright")
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                headless=headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        
        page = browser.new_page()
        page.set_default_timeout(30000)
        
        # 注入已验证的 cookies（绕过 CAPTCHA）
        cookies_file = project_root_path / "config" / "tophub_cookies.json"
        if cookies_file.exists():
            try:
                import json as _json
                cookies = _json.loads(cookies_file.read_text(encoding="utf-8"))
                # Playwright requires 'url' or 'domain' for cookie context
                for c in cookies:
                    if "domain" not in c:
                        c["domain"] = ".tophub.today"
                page.context.add_cookies(cookies)
                logger.info(f"已注入 {len(cookies)} 个 cookies（绕过验证码）")
            except Exception as e:
                logger.warning(f"Cookie 注入失败: {e}")
        else:
            logger.warning("未找到 tophub_cookies.json，可能触发验证码")
        
        # 逐个查询搜索
        for query in queries:
            raw = _search_tophub(browser, page, query, logger)
            all_raw.extend(raw)
            time.sleep(2)  # 礼貌间隔
        
    except Exception as e:
        errors.append(f"Browser error: {str(e)[:200]}")
        logger.error(f"浏览器异常: {e}")
    finally:
        try:
            if page:
                page.close()
            if browser:
                browser.close()
        except Exception:
            pass
    
    # 去重 + 清洗 + 日期过滤
    seen_urls = set()
    cleaned = []
    
    for raw in all_raw:
        url = raw.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        
        # 跳过 Google News 重定向链接（太长且不稳定）
        if "news.google.com/rss/articles" in url and len(url) > 200:
            continue
        
        pub_date = raw.get("pub_date", "")
        
        # 日期过滤
        if pub_date and pub_date < cutoff_date:
            continue
        
        domain = raw.get("domain", "")
        source = _domain_to_source(domain)
        title = raw.get("title", "").strip()
        
        # 清理标题中的来源后缀
        title = re.sub(r'\s*[-–—]\s*(搜狐网|新浪财经|手机新浪网|东方财富|MSN)\s*$', '', title)
        
        # 截断过长标题（tophub 有时把摘要放在标题里）
        if len(title) > 100:
            summary = title
            title = title[:80] + "..."
        else:
            summary = raw.get("summary", title)
        
        article = {
            "title": title,
            "url": url,
            "source": source,
            "source_type": "tophub",
            "domain": domain,
            "pub_date": pub_date,
            "pub_time": raw.get("pub_time", ""),
            "summary": summary[:500],
            "collected_at": datetime.now(CST).isoformat(),
            "raw_text": summary[:500],
        }
        cleaned.append(article)
    
    # 保存
    output_file = raw_dir / "tophub_articles.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
    
    # 统计
    sources = {}
    dates = {}
    for a in cleaned:
        s = a["source"]
        sources[s] = sources.get(s, 0) + 1
        d = a["pub_date"]
        dates[d] = dates.get(d, 0) + 1
    
    logger.info(f"\n{'='*60}")
    logger.info(f"TopHub 采集完成: {len(cleaned)} 篇文章 (去重前 {len(all_raw)})")
    logger.info(f"来源分布: {dict(sorted(sources.items(), key=lambda x: -x[1]))}")
    logger.info(f"日期分布: {dict(sorted(dates.items()))}")
    if errors:
        logger.warning(f"错误: {errors}")
    logger.info("=" * 60)
    
    return {
        "ok": len(cleaned) > 0 or len(errors) == 0,
        "total": len(cleaned),
        "articles": cleaned,
        "errors": errors,
        "stats": {
            "queries": len(queries),
            "raw_total": len(all_raw),
            "after_dedup": len(cleaned),
            "sources": sources,
            "dates": dates,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="TopHub.today 聚合搜索采集")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--queries", nargs="+", default=None, help="搜索关键词")
    parser.add_argument("--max-days-back", type=int, default=2, help="最多保留几天前")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()
    
    date = args.date or datetime.now(CST).strftime("%Y-%m-%d")
    headless = not args.headed
    
    result = run_tophub_collect(
        project_root=args.project_root,
        date=date,
        queries=args.queries,
        headless=headless,
        max_days_back=args.max_days_back,
        verbose=args.verbose,
    )
    
    print(json.dumps({
        "ok": result["ok"],
        "total": result["total"],
        "errors": result["errors"],
        "stats": result.get("stats", {}),
    }, ensure_ascii=False, indent=2))
    
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
