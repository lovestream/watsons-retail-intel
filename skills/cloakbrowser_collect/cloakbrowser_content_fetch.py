#!/usr/bin/env python3
"""
CloakBrowser 文章正文抓取 — 为 cloakbrowser_articles.json 中的文章补充全文内容
使用 cloak_fetch.fetch_page() 逐个抓取
"""
import json
import logging
import sys
import os
from pathlib import Path

# 添加 .venv_packages 到 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENV_PKGS = os.path.join(_PROJECT_ROOT, ".venv_packages")
if _VENV_PKGS not in sys.path:
    sys.path.insert(0, _VENV_PKGS)

logger = logging.getLogger("cloakbrowser_content_fetch")


def run_cloakbrowser_content_fetch(
    project_root: str,
    date: str,
    max_articles: int = 30,
    headless: bool = True,
    verbose: bool = False,
) -> dict:
    """为 CloakBrowser 采集的文章抓取正文。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        max_articles: 最多处理文章数
        headless: 是否无头模式
        verbose: 详细日志
    
    Returns:
        {"ok": True/False, "total": N, "success": N, "failed": N, ...}
    """
    from cloakbrowser import launch, get_default_stealth_args

    project_root_path = Path(project_root)
    raw_dir = project_root_path / "data" / "raw" / date
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "cloakbrowser_content_fetch.log"
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    logger.info("=" * 60)
    logger.info(f"CloakBrowser 正文抓取: date={date} max={max_articles}")
    logger.info("=" * 60)

    # 加载 CloakBrowser 文章
    articles_file = raw_dir / "cloakbrowser_articles.json"
    if not articles_file.exists():
        logger.warning("cloakbrowser_articles.json 不存在")
        return {"ok": False, "total": 0, "success": 0, "failed": 0, "errors": ["文件不存在"]}

    with open(articles_file) as f:
        data = json.load(f)
    articles = data.get("articles", data if isinstance(data, list) else [])
    logger.info(f"加载 {len(articles)} 篇文章")

    # 筛掉已有内容的
    need_fetch = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        content = a.get("content", a.get("article_content", ""))
        if not content or len(str(content).strip()) < 100:
            need_fetch.append(a)

    need_fetch = need_fetch[:max_articles]
    logger.info(f"需要抓取正文: {len(need_fetch)}/{len(articles)} 篇")

    if not need_fetch:
        logger.info("无需抓取，所有文章已有内容")
        return {"ok": True, "total": 0, "success": 0, "failed": 0}

    # 启动浏览器
    logger.info("启动 CloakBrowser...")
    stealth_args = get_default_stealth_args()
    if headless:
        stealth_args.append("--headless=new")
    browser = launch(args=stealth_args, headless=headless)

    success_count = 0
    failed_count = 0
    errors = []

    try:
        for i, article in enumerate(need_fetch):
            url = article.get("url", "").strip()
            if not url:
                continue

            title = str(article.get("title", ""))[:60]
            logger.info(f"[{i+1}/{len(need_fetch)}] {title}")
            logger.debug(f"  URL: {url}")

            try:
                page = browser.new_page()
                page.set_default_timeout(30000)
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                # 等待内容加载
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass  # 超时也可接受

                # 提取正文
                content_text = page.evaluate("""() => {
                    const article = document.querySelector('article, .article-content, .article-body, .post-content, .news-content, .detail-content, main, .content');
                    if (article) return article.innerText;
                    // Fallback: remove nav/footer/ads and get body text
                    const body = document.body.cloneNode(true);
                    const remove = body.querySelectorAll('nav, footer, header, script, style, .nav, .footer, .header, .sidebar, .ad, .advertisement, .comment');
                    remove.forEach(el => el.remove());
                    return body.innerText.substring(0, 15000);
                }""")

                # 提取日期
                pub_date = ""
                date_script = page.evaluate("""() => {
                    const meta = document.querySelector('meta[property="article:published_time"], meta[name="pubdate"], meta[name="publishdate"]');
                    if (meta) return meta.content;
                    // Try time element
                    const time = document.querySelector('time[datetime]');
                    if (time) return time.getAttribute('datetime');
                    return '';
                }""")

                article["content"] = content_text or ""
                article["article_content"] = content_text or ""
                if pub_date:
                    article["published_date"] = pub_date
                    article["extracted_date"] = pub_date

                content_len = len(content_text or "")
                logger.info(f"  ✅ {content_len} 字符" + (f", 日期={pub_date[:10]}" if pub_date else ""))

                success_count += 1
                page.close()

            except Exception as e:
                logger.warning(f"  ❌ {type(e).__name__}: {str(e)[:100]}")
                failed_count += 1
                errors.append(f"{url}: {type(e).__name__}: {str(e)[:100]}")
                try:
                    page.close()
                except Exception:
                    pass

    finally:
        try:
            browser.close()
        except Exception:
            pass

    # 保存回原文件
    output_data = {
        "metadata": {
            "date": date,
            "total": len(articles),
            "fetched": len(need_fetch),
            "success": success_count,
            "failed": failed_count,
            "timestamp": str(Path(log_dir).stat().st_mtime if log_dir.exists() else ""),
        },
        "articles": articles,
    }
    output_file = raw_dir / "cloakbrowser_enriched_articles.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # 同时更新 cloakbrowser_articles.json
    with open(articles_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    result = {
        "ok": True,
        "total": len(need_fetch),
        "success": success_count,
        "failed": failed_count,
        "errors": errors,
        "output_files": [str(output_file)],
    }

    logger.info(f"完成: 成功={success_count}, 失败={failed_count}, 总计={len(need_fetch)}")
    logger.info(f"输出: {output_file}")

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CloakBrowser 正文抓取")
    parser.add_argument("--project-root", default=".", help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--max-articles", type=int, default=30, help="最多处理文章数")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    result = run_cloakbrowser_content_fetch(
        project_root=args.project_root,
        date=args.date,
        max_articles=args.max_articles,
        headless=not args.headed,
        verbose=args.verbose,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
