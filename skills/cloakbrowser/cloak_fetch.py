#!/usr/bin/env python3
"""
cloak_fetch.py — CloakBrowser CLI 工具

用法:
    python3 cloak_fetch.py <url> [--text-only] [--screenshot <path>]
    python3 cloak_fetch.py <url> --json          # 输出 JSON

示例:
    python3 skills/cloakbrowser/cloak_fetch.py "https://36kr.com/search/articles/即时零售" --text-only
    python3 skills/cloakbrowser/cloak_fetch.py "https://example.com" --json
"""

import argparse
import json
import sys
import os
import time

# ── 确保 .venv_packages 在 sys.path ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
_VENV_PKGS = os.path.join(_PROJECT_ROOT, ".venv_packages")
if _VENV_PKGS not in sys.path:
    sys.path.insert(0, _VENV_PKGS)


def fetch_page(url: str, text_only: bool = False, timeout: int = 30) -> dict:
    """使用 CloakBrowser 抓取页面。"""
    from cloakbrowser import launch, get_default_stealth_args
    
    # Fallback: system Chromium if patched binary not found
    import os as _os
    if not _os.environ.get("CLOAKBROWSER_BINARY_PATH"):
        for p in ["/usr/bin/chromium", "/usr/bin/chromium-browser"]:
            if _os.path.exists(p):
                _os.environ["CLOAKBROWSER_BINARY_PATH"] = p
                break

    browser = None
    try:
        browser = launch(
            headless=True,
            args=get_default_stealth_args() + ["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

        # 等待页面稳定
        time.sleep(1)

        title = page.title()

        if text_only:
            # 提取可见文本
            text = page.evaluate("() => document.body.innerText")
            body = text[:50000] if text else ""
            markdown = ""
        else:
            body = page.content()[:100000]
            # 简单提取可见文本作为 markdown fallback
            text = page.evaluate("() => document.body.innerText")
            markdown = text[:50000] if text else ""

        page.close()

        return {
            "ok": True,
            "url": url,
            "title": title,
            "text": body if text_only else text[:50000] if text_only else body,
            "markdown": markdown if not text_only else "",
            "content_type": "text" if text_only else "html",
        }

    except Exception as e:
        return {
            "ok": False,
            "url": url,
            "error": str(e),
            "error_type": type(e).__name__,
        }
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="CloakBrowser CLI — 隐身浏览器页面抓取")
    parser.add_argument("url", help="目标 URL")
    parser.add_argument("--text-only", action="store_true", help="仅提取可见文本")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--timeout", type=int, default=30, help="超时秒数（默认30）")
    parser.add_argument("--screenshot", help="截图保存路径 (.png)")

    args = parser.parse_args()

    result = fetch_page(args.url, text_only=args.text_only, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print(f"Title: {result.get('title', 'N/A')}")
        print(f"---")
        if args.text_only:
            print(result.get("text", ""))
        else:
            print(result.get("markdown", result.get("text", "")))
    else:
        print(f"ERROR: {result.get('error', 'unknown')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
