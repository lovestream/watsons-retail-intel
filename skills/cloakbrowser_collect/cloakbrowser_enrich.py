#!/usr/bin/env python3
"""
cloakbrowser_enrich.py — Level 2: CloakBrowser 内容增强

对 XCrawl scrape 失败或内容不足的 URL，用 CloakBrowser 重新抓取正文。
绕过反爬 → 更高成功率 → 更完整的文章内容。

触发条件:
  - xcrawl_enrich 失败的 URL
  - 或内容长度 < 200 字符的文章

输出: data/raw/{date}/xcrawl_cloakbrowser_fallback.json

用法:
    python cloakbrowser_enrich.py --project-root . --date 2026-05-14
    python cloakbrowser_enrich.py --project-root . --date 2026-05-14 --max 10
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
from urllib.parse import urlparse

# ═══ Bootstrap ═══
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_PACKAGES = PROJECT_ROOT / ".venv_packages"
if VENV_PACKAGES.exists():
    sys.path.insert(0, str(VENV_PACKAGES))

CST = timezone(timedelta(hours=8))

# ═══ 日期提取 ═══

def _extract_url_date(url: str) -> Optional[str]:
    """从 URL 提取 YYYY-MM-DD 格式日期。"""
    if not url:
        return None
    m = re.search(r'/(\d{4})[/_\-](\d{2})[/_\-](\d{2})/', url)
    if m:
        try:
            y, m_num, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2030 and 1 <= m_num <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m_num:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    m2 = re.search(r'/(\d{4})(\d{2})(\d{2})', url)
    if m2:
        try:
            y, m_num, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
            if 2020 <= y <= 2030 and 1 <= m_num <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{m_num:02d}-{d:02d}"
        except (ValueError, IndexError):
            pass
    return None


def _extract_date_from_page(page) -> Optional[str]:
    """从页面 DOM 中提取真实发布日期。优先级：meta > time标签 > JSON-LD。"""
    try:
        # 1. meta 标签
        meta_selectors = [
            'meta[property="article:published_time"]',
            'meta[name="pubdate"]',
            'meta[name="publishdate"]',
            'meta[name="date"]',
            'meta[itemprop="datePublished"]',
        ]
        for sel in meta_selectors:
            el = page.query_selector(sel)
            if el:
                content = el.get_attribute("content")
                if content and len(content) >= 8:
                    return content[:10].replace('/', '-')
        
        # 2. <time> 标签
        time_el = page.query_selector('time[datetime]')
        if time_el:
            dt = time_el.get_attribute("datetime")
            if dt and len(dt) >= 8:
                return dt[:10].replace('/', '-')
        
        # 3. JSON-LD
        try:
            ld_json = page.evaluate("""() => {
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                for (const s of scripts) {
                    try {
                        const data = JSON.parse(s.textContent);
                        const dp = data?.datePublished || data?.['@graph']?.[0]?.datePublished;
                        if (dp) return dp;
                    } catch(e) {}
                }
                return null;
            }""")
            if ld_json and len(ld_json) >= 8:
                return ld_json[:10].replace('/', '-')
        except Exception:
            pass
        
        # 4. 从正文搜索中文日期
        text = page.evaluate("() => document.body.innerText")
        cn_date = re.search(r'(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})[日号]', text[:500])
        if cn_date:
            try:
                y, m, d = int(cn_date.group(1)), int(cn_date.group(2)), int(cn_date.group(3))
                return f"{y:04d}-{m:02d}-{d:02d}"
            except (ValueError, IndexError):
                pass
        
        # 5. 英文日期
        en_match = re.search(
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{1,2}),?\s+(\d{4})',
            text[:500], re.IGNORECASE
        )
        if en_match:
            months = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                      'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
            try:
                m_num = months[en_match.group(1).lower()[:3]]
                d = int(en_match.group(2))
                y = int(en_match.group(3))
                return f"{y:04d}-{m_num:02d}-{d:02d}"
            except (ValueError, IndexError):
                pass
    except Exception:
        pass
    
    return None


def _extract_article_content(page) -> dict:
    """从页面提取文章标题、正文、发布日期。"""
    result = {
        "title": "",
        "content": "",
        "published_at": "",
        "text_length": 0,
    }
    
    try:
        # 标题
        title_selectors = ['h1', 'article h1', '.article-title', '.post-title', 
                          '.news-title', '[data-role="title"]']
        for sel in title_selectors:
            el = page.query_selector(sel)
            if el:
                result["title"] = el.inner_text().strip()
                if len(result["title"]) > 5:
                    break
        
        if not result["title"]:
            result["title"] = page.title()
        
        # 正文
        content_selectors = [
            'article', '.article-content', '.post-content', '.news-content',
            '.article-body', '.content', '#article', '.entry-content',
            '[data-role="content"]', '.rich_media_content',
        ]
        for sel in content_selectors:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text()
                if len(text) > 200:
                    result["content"] = text
                    result["text_length"] = len(text)
                    break
        
        if not result["content"]:
            # 兜底：body 文本（去掉 nav/footer/script）
            text = page.evaluate("""() => {
                const body = document.body.cloneNode(true);
                body.querySelectorAll('nav,footer,script,style,iframe,header,.nav,.footer,.header,.sidebar,.advertisement').forEach(el => el.remove());
                return body.innerText;
            }""")
            result["content"] = text[:10000]
            result["text_length"] = len(text)
        
        # 日期
        pub_date = _extract_date_from_page(page)
        if pub_date:
            result["published_at"] = pub_date
    
    except Exception as e:
        result["error"] = str(e)[:200]
    
    return result


def run_cloakbrowser_enrich(
    project_root: str,
    date: str,
    max_articles: int = 40,
    headless: bool = True,
    verbose: bool = False,
) -> dict:
    """主入口：用 CloakBrowser 为内容不足的文章补充正文。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        max_articles: 最多处理文章数
        headless: 是否无头模式
        verbose: 详细日志
    
    Returns:
        {"ok": True/False, "enriched": N, "articles": [...], "errors": [...]}
    """
    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / "cloakbrowser_enrich.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("cloakbrowser_enrich")
    
    logger.info("=" * 60)
    logger.info(f"CloakBrowser L2 内容增强: date={date} max={max_articles}")
    logger.info("=" * 60)
    
    errors = []
    enriched = []
    skipped_no_browser = False
    
    # ── 确定需要增强的URL ──
    # 优先：xcrawl_enrich 失败或内容短的
    # 其次：tophub / raw_articles 中没有全文的高相关文章
    candidates = []
    seen_urls = set()
    
    # 从 xcrawl_enriched 中找内容不足的
    xcrawl_file = raw_dir / "xcrawl_enriched_articles.json"
    if xcrawl_file.exists():
        try:
            with open(xcrawl_file) as f:
                xcrawl_data = json.load(f)
            xcrawl_articles = xcrawl_data.get("articles", xcrawl_data if isinstance(xcrawl_data, list) else [])
            if isinstance(xcrawl_articles, dict):
                xcrawl_articles = list(xcrawl_articles.values())
                if xcrawl_articles and isinstance(xcrawl_articles[0], list):
                    xcrawl_articles = xcrawl_articles[0]
            
            for a in xcrawl_articles:
                if not isinstance(a, dict):
                    continue
                content = a.get("content", "") or a.get("markdown", "") or ""
                if len(content) < 200:
                    url = a.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        candidates.append({
                            "url": url,
                            "title": a.get("title", ""),
                            "original_published_at": a.get("published_at", ""),
                            "reason": "xcrawl_content_too_short",
                        })
        except Exception as e:
            logger.warning(f"读取 xcrawl_enriched 失败: {e}")
    
    # 从 tophub_articles 中找无全文的（高优先级线索）
    tophub_file = raw_dir / "tophub_articles.json"
    if tophub_file.exists():
        try:
            with open(tophub_file) as f:
                tophub_data = json.load(f)
            tophub_articles = tophub_data.get("articles", tophub_data if isinstance(tophub_data, list) else [])
            for a in tophub_articles:
                if not isinstance(a, dict):
                    continue
                content = a.get("content", "") or ""
                url = a.get("url", "")
                if len(content) < 200 and url and url not in seen_urls:
                    seen_urls.add(url)
                    candidates.append({
                        "url": url,
                        "title": a.get("title", ""),
                        "original_published_at": a.get("published_at", ""),
                        "reason": "tophub_no_content",
                    })
        except Exception as e:
            logger.warning(f"读取 tophub_articles 失败: {e}")
    
    # 限制数量
    candidates = candidates[:max_articles]
    logger.info(f"候选URL: {len(candidates)} 个需要增强")
    
    if not candidates:
        logger.info("无需增强的候选URL，跳过")
        return {"ok": True, "enriched": 0, "articles": [], "errors": []}
    
    # ── 使用 CloakBrowser 抓取 ──
    try:
        from cloakbrowser import launch, get_default_stealth_args
    except ImportError as e:
        logger.error(f"CloakBrowser 未安装: {e}")
        return {
            "ok": True,  # 不阻塞管道
            "enriched": 0,
            "articles": [],
            "errors": [f"cloakbrowser not installed: {e}"],
            "skipped": True,
            "reason": "cloakbrowser_not_installed",
        }
    
    # Fallback: 使用系统 Chromium
    if not os.environ.get("CLOAKBROWSER_BINARY_PATH"):
        for sys_chrome in ["/usr/bin/chromium", "/usr/bin/chromium-browser"]:
            if os.path.exists(sys_chrome):
                os.environ["CLOAKBROWSER_BINARY_PATH"] = sys_chrome
                logger.info(f"使用系统 Chromium: {sys_chrome}")
                break
    
    browser = None
    try:
        logger.info("启动 CloakBrowser...")
        browser = launch(headless=headless, args=get_default_stealth_args() + ["--no-sandbox", "--disable-dev-shm-usage"])
        
        for i, candidate in enumerate(candidates):
            url = candidate["url"]
            logger.info(f"\n  [{i+1}/{len(candidates)}] {url[:80]}...")
            
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(2000)
                
                result = _extract_article_content(page)
                result["url"] = url
                result["original_title"] = candidate.get("title", "")
                result["original_published_at"] = candidate.get("original_published_at", "")
                result["reason"] = candidate.get("reason", "")
                
                # URL 日期作为后备
                if not result["published_at"]:
                    url_date = _extract_url_date(url)
                    if url_date:
                        result["published_at"] = url_date
                
                if result["text_length"] > 100:
                    logger.info(f"    ✅ 正文 {result['text_length']} 字符, 日期={result['published_at']}")
                else:
                    logger.warning(f"    ⚠️ 正文仅 {result['text_length']} 字符")
                
                enriched.append(result)
                page.close()
                
            except Exception as e:
                err_msg = f"{type(e).__name__}: {str(e)[:150]}"
                logger.warning(f"    ❌ 失败: {err_msg}")
                errors.append({"url": url, "error": err_msg})
            
            time.sleep(0.5)
    
    except Exception as e:
        logger.error(f"浏览器启动失败: {e}")
        errors.append(f"browser launch failed: {e}")
        skipped_no_browser = True
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
    
    # ── 输出 ──
    output_file = raw_dir / "xcrawl_cloakbrowser_fallback.json"
    output = {
        "metadata": {
            "version": "1.0",
            "date": date,
            "level": "L2_content_enrichment",
            "created_at": datetime.now(CST).isoformat(),
            "total_candidates": len(candidates),
            "total_enriched": len(enriched),
            "skipped_no_browser": skipped_no_browser,
            "errors": errors,
        },
        "articles": enriched,
    }
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"L2 完成: {len(enriched)}/{len(candidates)} 篇增强")
    logger.info(f"输出: {output_file}")
    
    return {
        "ok": True,  # 永远不阻塞管道
        "enriched": len(enriched),
        "total_candidates": len(candidates),
        "file": str(output_file),
        "errors": errors,
        "skipped_no_browser": skipped_no_browser,
    }


def main():
    parser = argparse.ArgumentParser(description="CloakBrowser L2 内容增强")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--max", type=int, default=40, dest="max_articles")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    
    result = run_cloakbrowser_enrich(
        project_root=args.project_root,
        date=args.date,
        max_articles=args.max_articles,
        headless=not args.visible,
        verbose=args.verbose,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
