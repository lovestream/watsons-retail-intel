#!/usr/bin/env python3
"""
enrich_cleaned_fulltext.py — 对 cleaned 文章中缺全文的做正文补抓

在 filter 之后运行，读取 cleaned_articles.json，
找出 content 为空或过短（<200字）的文章，
用 CloakBrowser 抓取正文并回写。

用法:
    python skills/enrich_cleaned_fulltext/enrich_cleaned_fulltext.py \
        --project-root . --date 2026-05-21
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

# ── 路径设置 ──
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / ".venv_packages"))

logger = logging.getLogger("enrich_cleaned_fulltext")

# 最小正文长度（低于此视为"无全文"）
MIN_CONTENT_LENGTH = 200
# 最多抓取文章数
MAX_ENRICH = 15


def _count_chinese_chars(text: str) -> int:
    """计算中文字符数。"""
    return sum(1 for c in text if '\u4e00' <= c <= '\u9fff')


def _fetch_with_cloakbrowser(url: str, timeout: int = 30) -> Optional[str]:
    """用 CloakBrowser (Playwright) 抓取正文。"""
    try:
        from cloakbrowser import launch, get_default_stealth_args
        import os

        # 确保有 Chromium
        if not os.environ.get("CLOAKBROWSER_BINARY_PATH"):
            for sys_chrome in ["/usr/bin/chromium", "/usr/bin/chromium-browser"]:
                if os.path.exists(sys_chrome):
                    os.environ["CLOAKBROWSER_BINARY_PATH"] = sys_chrome
                    break

        browser = launch(headless=True, args=get_default_stealth_args())
        try:
            page = browser.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            time.sleep(2)  # 等待动态内容加载

            # 提取正文
            content = ""
            # 尝试常见正文选择器
            selectors = [
                "article", ".article-content", ".post-content",
                ".entry-content", "#article-content", ".content",
                "main", ".main-content"
            ]
            for sel in selectors:
                try:
                    el = page.query_selector(sel)
                    if el:
                        text = el.inner_text()
                        if _count_chinese_chars(text) >= MIN_CONTENT_LENGTH:
                            content = text
                            break
                except Exception:
                    continue

            # fallback: body text
            if not content:
                body = page.query_selector("body")
                if body:
                    text = body.inner_text()
                    if _count_chinese_chars(text) >= MIN_CONTENT_LENGTH:
                        content = text

            page.close()
            return content if _count_chinese_chars(content) >= MIN_CONTENT_LENGTH else None
        finally:
            browser.close()
    except ImportError:
        logger.debug("CloakBrowser 未安装，跳过")
        return None
    except Exception as e:
        logger.debug(f"CloakBrowser 抓取失败 {url}: {e}")
        return None


def enrich_cleaned_fulltext(
    project_root: str,
    date: str,
    max_enrich: int = MAX_ENRICH,
) -> dict:
    """主函数：对 cleaned 文章补抓全文。

    Returns:
        {"ok": bool, "enriched": int, "total_cleaned": int, "missing_before": int}
    """
    project_root_path = Path(project_root)
    cleaned_file = project_root_path / "data" / "cleaned" / date / "cleaned_articles.json"
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)

    # 设置日志
    log_file = log_dir / "enrich_cleaned_fulltext.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.INFO)

    logger.info("=" * 60)
    logger.info(f"Enrich Cleaned Fulltext: date={date}")
    logger.info("=" * 60)

    if not cleaned_file.exists():
        logger.warning(f"cleaned 文件不存在: {cleaned_file}")
        return {"ok": False, "error": "cleaned file not found"}

    with open(cleaned_file, "r", encoding="utf-8") as f:
        cleaned_data = json.load(f)

    articles = cleaned_data.get("articles", cleaned_data) if isinstance(cleaned_data, dict) else cleaned_data
    total = len(articles)

    # 找出缺全文的文章
    missing = []
    for i, art in enumerate(articles):
        content = art.get("content", "") or art.get("full_text", "") or ""
        if _count_chinese_chars(content) < MIN_CONTENT_LENGTH:
            url = art.get("url", "") or art.get("link", "")
            if url:
                missing.append((i, url, art.get("title", "")[:40]))

    logger.info(f"  总 cleaned 文章: {total}")
    logger.info(f"  缺全文: {len(missing)}")

    if not missing:
        logger.info("  所有文章已有全文，无需补抓")
        return {"ok": True, "enriched": 0, "total_cleaned": total, "missing_before": 0}

    # 加载 XCrawl keys 作为 fallback
    xcrawl_keys = []
    keys_file = project_root_path / "config" / "xcrawl_keys.json"
    if keys_file.exists():
        try:
            with open(keys_file) as f:
                keys_data = json.load(f)
            xcrawl_keys = [k for k in keys_data.get("keys", []) if k.get("active")]
            xcrawl_keys = [k["key"] for k in xcrawl_keys]
        except Exception:
            pass

    # 抓取
    enriched_count = 0
    to_process = missing[:max_enrich]
    logger.info(f"  开始补抓 {len(to_process)} 篇文章...")

    for idx, url, title in to_process:
        logger.info(f"  [{enriched_count+1}/{len(to_process)}] {title} | {url[:60]}")

        # 先试 CloakBrowser
        content = _fetch_with_cloakbrowser(url)

        # 再试 XCrawl
        if not content and xcrawl_keys:
            content = _fetch_with_xcrawl(url, xcrawl_keys)

        if content:
            # 回写到 articles
            articles[idx]["content"] = content
            articles[idx]["_fulltext_source"] = "enrich_cleaned"
            enriched_count += 1
            cn_chars = _count_chinese_chars(content)
            logger.info(f"    ✓ 抓取成功: {cn_chars} 中文字符")
        else:
            logger.info(f"    ✗ 抓取失败")

        # 避免请求过快
        time.sleep(1.5)

    # 回写 cleaned 文件
    if enriched_count > 0:
        if isinstance(cleaned_data, dict):
            cleaned_data["articles"] = articles
        else:
            cleaned_data = articles

        with open(cleaned_file, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
        logger.info(f"  已回写 {enriched_count} 篇全文到 {cleaned_file.name}")

    logger.info(f"  完成: enriched={enriched_count}/{len(to_process)}, "
                f"全文覆盖率 {total - len(missing) + enriched_count}/{total} "
                f"({(total - len(missing) + enriched_count) / max(total,1) * 100:.0f}%)")

    return {
        "ok": True,
        "enriched": enriched_count,
        "total_cleaned": total,
        "missing_before": len(missing),
        "missing_after": len(missing) - enriched_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Enrich cleaned articles with full text")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", required=True)
    parser.add_argument("--max-enrich", type=int, default=MAX_ENRICH)
    args = parser.parse_args()

    result = enrich_cleaned_fulltext(args.project_root, args.date, args.max_enrich)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


def _fetch_with_xcrawl(url: str, keys: list, timeout: int = 20) -> Optional[str]:
    """用 XCrawl search API 抓取正文（作为 fallback）。"""
    try:
        import requests
        for key in keys[:2]:  # 最多用 2 个 key 尝试
            api_url = f"https://api.xcrawl.com/v1/scrape"
            headers = {"Authorization": f"Bearer {key}"}
            resp = requests.post(
                api_url, json={"url": url}, headers=headers, timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("data", {}).get("content", "")
                if _count_chinese_chars(content) >= MIN_CONTENT_LENGTH:
                    return content
    except Exception as e:
        logger.debug(f"XCrawl 抓取失败 {url}: {e}")
    return None


def enrich_cleaned_fulltext(
    project_root: str,
    date: str,
    max_enrich: int = MAX_ENRICH,
) -> dict:
    """主函数：对 cleaned 文章补抓全文。

    Returns:
        {"ok": bool, "enriched": int, "total_cleaned": int, "missing_before": int}
    """
    project_root_path = Path(project_root)
    cleaned_file = project_root_path / "data" / "cleaned" / date / "cleaned_articles.json"
    log_dir = project_root_path / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)

    # 设置日志
    log_file = log_dir / "enrich_cleaned_fulltext.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(logging.StreamHandler(sys.stdout))
    logger.setLevel(logging.INFO)

    logger.info("=" * 60)
    logger.info(f"Enrich Cleaned Fulltext: date={date}")
    logger.info("=" * 60)

    if not cleaned_file.exists():
        logger.warning(f"cleaned 文件不存在: {cleaned_file}")
        return {"ok": False, "error": "cleaned file not found"}

    with open(cleaned_file, "r", encoding="utf-8") as f:
        cleaned_data = json.load(f)

    articles = cleaned_data.get("articles", cleaned_data) if isinstance(cleaned_data, dict) else cleaned_data
    total = len(articles)

    # 找出缺全文的文章
    missing = []
    for i, art in enumerate(articles):
        content = art.get("content", "") or art.get("full_text", "") or ""
        if _count_chinese_chars(content) < MIN_CONTENT_LENGTH:
            url = art.get("url", "") or art.get("link", "")
            if url:
                missing.append((i, url, art.get("title", "")[:40]))

    logger.info(f"  总 cleaned 文章: {total}")
    logger.info(f"  缺全文: {len(missing)}")

    if not missing:
        logger.info("  所有文章已有全文，无需补抓")
        return {"ok": True, "enriched": 0, "total_cleaned": total, "missing_before": 0}

    # 加载 XCrawl keys 作为 fallback
    xcrawl_keys = []
    keys_file = project_root_path / "config" / "xcrawl_keys.json"
    if keys_file.exists():
        try:
            with open(keys_file) as f:
                keys_data = json.load(f)
            xcrawl_keys = [k for k in keys_data.get("keys", []) if k.get("active")]
            xcrawl_keys = [k["key"] for k in xcrawl_keys]
        except Exception:
            pass

    # 抓取
    enriched_count = 0
    to_process = missing[:max_enrich]
    logger.info(f"  开始补抓 {len(to_process)} 篇文章...")

    for idx, url, title in to_process:
        logger.info(f"  [{enriched_count+1}/{len(to_process)}] {title} | {url[:60]}")

        # 先试 CloakBrowser
        content = _fetch_with_cloakbrowser(url)

        # 再试 XCrawl
        if not content and xcrawl_keys:
            content = _fetch_with_xcrawl(url, xcrawl_keys)

        if content:
            # 回写到 articles
            articles[idx]["content"] = content
            articles[idx]["_fulltext_source"] = "enrich_cleaned"
            enriched_count += 1
            cn_chars = _count_chinese_chars(content)
            logger.info(f"    ✓ 抓取成功: {cn_chars} 中文字符")
        else:
            logger.info(f"    ✗ 抓取失败")

        # 避免请求过快
        time.sleep(1.5)

    # 回写 cleaned 文件
    if enriched_count > 0:
        if isinstance(cleaned_data, dict):
            cleaned_data["articles"] = articles
        else:
            cleaned_data = articles

        with open(cleaned_file, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
        logger.info(f"  已回写 {enriched_count} 篇全文到 {cleaned_file.name}")

    logger.info(f"  完成: enriched={enriched_count}/{len(to_process)}, "
                f"全文覆盖率 {total - len(missing) + enriched_count}/{total} "
                f"({(total - len(missing) + enriched_count) / max(total,1) * 100:.0f}%)")

    return {
        "ok": True,
        "enriched": enriched_count,
        "total_cleaned": total,
        "missing_before": len(missing),
        "missing_after": len(missing) - enriched_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Enrich cleaned articles with full text")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", required=True)
    parser.add_argument("--max-enrich", type=int, default=MAX_ENRICH)
    args = parser.parse_args()

    result = enrich_cleaned_fulltext(args.project_root, args.date, args.max_enrich)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
