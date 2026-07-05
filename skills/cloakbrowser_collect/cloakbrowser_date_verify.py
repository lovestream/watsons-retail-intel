#!/usr/bin/env python3
"""
cloakbrowser_date_verify.py — Level 3: CloakBrowser 日期复核

质量守门员：对合并后 published_at 与 URL 日期冲突 >7 天的文章，
用 CloakBrowser 访问原页面，从 <meta>/<time>/JSON-LD 提取真实日期。

触发条件:
  - published_at 与 URL 日期差异 > 7 天
  - 或 time_status == "unknown_time" 但有 URL 日期

用法:
    python cloakbrowser_date_verify.py --project-root . --date 2026-05-14
    python cloakbrowser_date_verify.py --project-root . --date 2026-05-14 --max 8
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

# ═══ Bootstrap ═══
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_PACKAGES = PROJECT_ROOT / ".venv_packages"
if VENV_PACKAGES.exists():
    sys.path.insert(0, str(VENV_PACKAGES))

CST = timezone(timedelta(hours=8))


def _extract_url_date(url: str) -> Optional[str]:
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


def _parse_date(d: str) -> Optional[datetime]:
    if not d or len(d) < 8:
        return None
    for fmt in ['%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z']:
        try:
            dt = datetime.strptime(d[:25].strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
            return dt
        except (ValueError, IndexError):
            continue
    try:
        return datetime.strptime(d[:10], '%Y-%m-%d').replace(tzinfo=CST)
    except (ValueError, IndexError):
        return None


def _extract_date_from_page(page) -> Optional[str]:
    """从页面 DOM 提取真实发布日期。"""
    try:
        # meta 标签
        for sel in [
            'meta[property="article:published_time"]',
            'meta[name="pubdate"]',
            'meta[name="publishdate"]',
            'meta[name="date"]',
            'meta[itemprop="datePublished"]',
        ]:
            el = page.query_selector(sel)
            if el:
                content = el.get_attribute("content")
                if content and len(content) >= 8:
                    return content[:10].replace('/', '-')
        
        # <time>
        el = page.query_selector('time[datetime]')
        if el:
            dt = el.get_attribute("datetime")
            if dt and len(dt) >= 8:
                return dt[:10].replace('/', '-')
        
        # JSON-LD
        try:
            ld = page.evaluate("""() => {
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                for (const s of scripts) {
                    try {
                        const data = JSON.parse(s.textContent);
                        return data?.datePublished || data?.['@graph']?.[0]?.datePublished || null;
                    } catch(e) {}
                }
                return null;
            }""")
            if ld and len(ld) >= 8:
                return ld[:10].replace('/', '-')
        except Exception:
            pass
    except Exception:
        pass
    return None


def _find_suspicious_articles(raw_dir: Path, max_articles: int, logger) -> List[dict]:
    """从 raw_articles_all.json 找日期可疑的文章。"""
    all_file = raw_dir / "raw_articles_all.json"
    if not all_file.exists():
        logger.warning(f"raw_articles_all.json 不存在，尝试其他源文件")
        # 尝试从原始各源文件找
        suspicious = []
        for fname in ["broad_search_urls.json", "xcrawl_enriched_articles.json"]:
            fpath = raw_dir / fname
            if fpath.exists():
                try:
                    with open(fpath) as f:
                        data = json.load(f)
                    articles = data.get("articles", data if isinstance(data, list) else [])
                    if isinstance(articles, dict):
                        articles = list(articles.values())
                        if articles and isinstance(articles[0], list):
                            articles = articles[0]
                    for a in articles:
                        if not isinstance(a, dict):
                            continue
                        url = a.get("url", "")
                        url_date = _extract_url_date(url)
                        pub = a.get("published_at", "")
                        if url_date and pub:
                            pub_dt = _parse_date(pub)
                            url_dt = _parse_date(url_date)
                            if pub_dt and url_dt:
                                diff = abs((pub_dt - url_dt).days)
                                if diff > 7:
                                    suspicious.append({
                                        "url": url,
                                        "title": a.get("title", ""),
                                        "published_at": pub,
                                        "url_date": url_date,
                                        "date_diff_days": diff,
                                        "source": fname,
                                    })
                except Exception as e:
                    logger.warning(f"读取 {fname} 失败: {e}")
        return suspicious[:max_articles]
    
    try:
        with open(all_file) as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"读取 raw_articles_all.json 失败: {e}")
        return []
    
    articles = data.get("articles", data if isinstance(data, list) else [])
    if isinstance(articles, dict):
        articles = list(articles.values())
        if articles and isinstance(articles[0], list):
            articles = articles[0]
    
    suspicious = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        url = a.get("url", "")
        url_date = _extract_url_date(url)
        pub = a.get("published_at", "")
        
        if url_date:
            pub_dt = _parse_date(pub) if pub else None
            url_dt = _parse_date(url_date)
            
            is_suspicious = False
            reason = ""
            
            if pub_dt and url_dt:
                diff = abs((pub_dt - url_dt).days)
                if diff > 7:
                    is_suspicious = True
                    reason = f"date_conflict_{diff}d"
            elif not pub:
                is_suspicious = True
                reason = "missing_published_at"
            
            if is_suspicious:
                suspicious.append({
                    "url": url,
                    "title": a.get("title", ""),
                    "published_at": pub,
                    "url_date": url_date,
                    "date_diff_days": abs((pub_dt - url_dt).days) if (pub_dt and url_dt) else None,
                    "reason": reason,
                })
    
    logger.info(f"找到 {len(suspicious)} 篇日期可疑文章")
    return suspicious[:max_articles]


def run_date_verify(
    project_root: str,
    date: str,
    max_articles: int = 8,
    headless: bool = True,
    verbose: bool = False,
) -> dict:
    """主入口：用 CloakBrowser 复核可疑文章的日期。
    
    Returns:
        {"ok": True/False, "verified": N, "corrected": N, "results": [...], ...}
    """
    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / "cloakbrowser_date_verify.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("cloakbrowser_date_verify")
    
    logger.info("=" * 60)
    logger.info(f"CloakBrowser L3 日期复核: date={date} max={max_articles}")
    logger.info("=" * 60)
    
    # 找可疑文章
    suspicious = _find_suspicious_articles(raw_dir, max_articles, logger)
    
    if not suspicious:
        logger.info("没有日期可疑的文章需要复核")
        return {"ok": True, "verified": 0, "corrected": 0, "results": [], "errors": []}
    
    # CloakBrowser
    try:
        from cloakbrowser import launch, get_default_stealth_args
    except ImportError as e:
        logger.error(f"CloakBrowser 未安装: {e}")
        return {
            "ok": True,
            "verified": 0,
            "corrected": 0,
            "results": [],
            "skipped": True,
            "reason": "cloakbrowser_not_installed",
            "errors": [str(e)],
        }
    
    # Fallback: 使用系统 Chromium
    if not os.environ.get("CLOAKBROWSER_BINARY_PATH"):
        for sys_chrome in ["/usr/bin/chromium", "/usr/bin/chromium-browser"]:
            if os.path.exists(sys_chrome):
                os.environ["CLOAKBROWSER_BINARY_PATH"] = sys_chrome
                logger.info(f"使用系统 Chromium: {sys_chrome}")
                break
    
    results = []
    corrected = 0
    errors = []
    browser = None
    
    try:
        logger.info("启动 CloakBrowser...")
        browser = launch(headless=headless, args=get_default_stealth_args() + ["--no-sandbox", "--disable-dev-shm-usage"])
        
        for i, s in enumerate(suspicious):
            url = s["url"]
            logger.info(f"\n  [{i+1}/{len(suspicious)}] {url[:80]}...")
            logger.info(f"    原日期: {s['published_at']} | URL日期: {s['url_date']} | 差异: {s.get('date_diff_days', '?')}天")
            
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
                
                page_date = _extract_date_from_page(page)
                
                verdict = {
                    "url": url,
                    "title": s["title"],
                    "original_published_at": s["published_at"],
                    "url_date": s["url_date"],
                    "page_date": page_date,
                    "date_diff_days": s.get("date_diff_days"),
                }
                
                if page_date:
                    page_dt = _parse_date(page_date)
                    url_dt = _parse_date(s["url_date"])
                    
                    if page_dt and url_dt:
                        diff = abs((page_dt - url_dt).days)
                        if diff <= 3:
                            # 页面日期与URL日期一致 → URL日期可靠
                            verdict["verdict"] = "url_date_confirmed"
                            if s["published_at"] != s["url_date"]:
                                verdict["corrected_to"] = s["url_date"]
                                corrected += 1
                                logger.info(f"    ✅ 修正: {s['published_at']} → {s['url_date']}")
                            else:
                                logger.info(f"    ✅ 确认: URL日期 {s['url_date']} 与页面一致")
                        else:
                            verdict["verdict"] = "ambiguous"
                            logger.warning(f"    ⚠️ 歧义: 页面={page_date} URL={s['url_date']}")
                    else:
                        verdict["verdict"] = "page_date_found"
                        verdict["corrected_to"] = page_date
                        corrected += 1
                        logger.info(f"    ✅ 页面日期: {page_date}")
                else:
                    # 页面无法提取日期 → 信任URL日期
                    verdict["verdict"] = "url_date_fallback"
                    if s["published_at"] != s["url_date"]:
                        verdict["corrected_to"] = s["url_date"]
                        corrected += 1
                        logger.info(f"    ⚠️ 页面无日期，回退到URL: {s['url_date']}")
                    else:
                        logger.info(f"    ⚠️ 页面无日期，URL日期已一致")
                
                results.append(verdict)
                page.close()
                
            except Exception as e:
                err_msg = f"{type(e).__name__}: {str(e)[:150]}"
                logger.warning(f"    ❌ 失败: {err_msg}")
                errors.append({"url": url, "error": err_msg})
                # 失败时也信任URL日期
                results.append({
                    "url": url,
                    "original_published_at": s["published_at"],
                    "url_date": s["url_date"],
                    "verdict": "page_failed_url_fallback",
                    "corrected_to": s["url_date"] if s["published_at"] != s["url_date"] else None,
                })
                if s["published_at"] != s["url_date"]:
                    corrected += 1
            
            time.sleep(0.5)
    
    except Exception as e:
        logger.error(f"浏览器启动失败: {e}")
        errors.append(f"browser launch failed: {e}")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
    
    # ── 输出 ──
    # 将修正信息写入文件，供后续步骤使用
    corrections_file = raw_dir / "date_corrections.json"
    corrections = []
    for r in results:
        if r.get("corrected_to"):
            corrections.append({
                "url": r["url"],
                "original_published_at": r["original_published_at"],
                "corrected_published_at": r["corrected_to"],
                "verdict": r["verdict"],
            })
    
    with open(corrections_file, "w", encoding="utf-8") as f:
        json.dump({"metadata": {"date": date, "level": "L3_date_verification",
                                "total_verified": len(results), "total_corrected": corrected},
                   "corrections": corrections}, f, ensure_ascii=False, indent=2)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"L3 完成: 复核 {len(results)} 篇, 修正 {corrected} 篇")
    logger.info(f"修正文件: {corrections_file}")
    
    return {
        "ok": True,
        "verified": len(results),
        "corrected": corrected,
        "results": results,
        "corrections_file": str(corrections_file),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="CloakBrowser L3 日期复核")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--max", type=int, default=8, dest="max_articles")
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    
    result = run_date_verify(
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
