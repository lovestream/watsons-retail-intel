"""截图脚本：用本地 chromium 在 360/375/390/600 宽度下渲染 HTML。"""
import sys
from playwright.sync_api import sync_playwright

WIDTHS = [360, 375, 390, 600]


def shoot(html_path, out_dir, prefix):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium",
                                    args=["--no-sandbox"])
        for w in WIDTHS:
            page = browser.new_page(viewport={"width": w, "height": 900},
                                    device_scale_factor=2)
            page.goto("file://" + html_path)
            page.wait_for_timeout(300)
            # 横向溢出检查（真实手机阅读的关键指标）
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth")
            height = page.evaluate("() => document.body.scrollHeight")
            page.screenshot(path=f"{out_dir}/{prefix}-{w}.png", full_page=True)
            print(f"{prefix} {w}px overflow={overflow}px height={height}px")
            page.close()
        browser.close()


if __name__ == "__main__":
    html_path, out_dir, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
    shoot(html_path, out_dir, prefix)
