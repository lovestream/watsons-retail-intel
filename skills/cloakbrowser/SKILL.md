# CloakBrowser Skill

隐身浏览器 — 基于 Chromium 源码级反检测补丁，可绕过 Cloudflare、reCAPTCHA 等机器人检测。

## 安装

```bash
pip install --target .venv_packages cloakbrowser
python3 -c "from cloakbrowser import ensure_binary; ensure_binary()"
```

二进制缓存路径：`~/.cloakbrowser/chromium-<version>/chrome`（约 200MB）

## CLI 工具

```bash
# 抓取页面全文
python3 skills/cloakbrowser/cloak_fetch.py <url> --text-only

# 截图
python3 skills/cloakbrowser/cloak_fetch.py <url> --screenshot output.png
```

## API（同步）

```python
from cloakbrowser import launch, get_default_stealth_args

browser = launch(headless=True, args=get_default_stealth_args())
page = browser.new_page()
page.goto("https://example.com")
content = page.content()
page.close()
browser.close()
```

## 四级接入

| Level | 模块 | 作用 |
|-------|------|------|
| L1 | `cloakbrowser_collect.py` | 直接抓取新闻聚合站（36氪、百度新闻、联商网） |
| L2 | `cloakbrowser_enrich.py` | 重试 XCrawl scrape 失败 |
| L3 | `cloakbrowser_date_verify.py` | 复核可疑日期 |
| L4 | (扩展中) | 垂直媒体监控（CBO、品观等） |

## 健康检查

```bash
python3 skills/health_check/health_check.py --project-root . --json | jq '.services.cloakbrowser'
```
