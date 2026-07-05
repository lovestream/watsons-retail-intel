#!/usr/bin/env python3
"""
health_check.py — 全流程健康检查

检查 9 大类：
  1. Longcat LLM      — 6 个 Key 逐个测
  2. Tavily Search    — 6 个 Key 逐个测
  3. XCrawl           — 7 个 Key 逐个测
  4. RSSHub           — HEAD 连通性
  5. Edge TTS         — TTS 合成实测（播客依赖）
  6. Python 依赖包    — import 所有关键包
  7. 配置文件         — 存在性 + YAML 可解析
  8. 目录结构         — 存在性 + 可写
  9. Pipeline 模块    — 所有 step 模块可 import

用法:
    python3 -m skills.health_check.health_check --project-root .
    python3 -m skills.health_check.health_check --project-root . --json
"""

# ── 自愈：优先从项目持久 .venv_packages 加载包 ──
import os, sys
_VENV_PACKAGES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".venv_packages")
if os.path.isdir(_VENV_PACKAGES) and _VENV_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_PACKAGES)

import argparse
import asyncio
import importlib
import json
import logging
import os
import shutil
import sys
import time
import traceback
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CST = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════
#  服务定义
# ═══════════════════════════════════════════════════════

SERVICES = {
    # ── 核心外部服务（Key-based） ──
    "longcat_llm": {
        "name": "Longcat LLM",
        "critical": True,
        "key_prefixes": ["longcat"],
        "key_count": 6,
        "test_model": "LongCat-2.0",
        "test_type": "api_key",
    },
    "tavily_search": {
        "name": "Tavily Search",
        "critical": True,
        "key_prefixes": ["tavily_key"],
        "key_count": 7,  # was 6, user added tavily_key6 → now 7 total (0-6)
        "test_type": "api_key",
    },
    "xcrawl": {
        "name": "XCrawl",
        "critical": True,
        "key_prefixes": ["xcrawl_key"],
        "key_count": 4,  # key1~key4 (跳过key0永久耗尽)，从7减半到4
        "key_start": 1,   # 跳过索引0（xcrawl_key已永久耗尽）
        "test_type": "api_key",
    },
    "rsshub": {
        "name": "RSSHub",
        "critical": False,
        "test_url": "http://192.168.2.100:1200",
        "test_type": "url",
    },

    # ── 播客语音合成 ──
    "edge_tts": {
        "name": "Edge TTS (播客语音)",
        "critical": True,  # 播客是日报标配输出
        "test_type": "tts",
    },

    # ── Python 依赖 ──
    "python_packages": {
        "name": "Python 依赖包",
        "critical": True,
        "test_type": "imports",
        "auto_fix": True,  # 缺包时自动 pip install
        "required_packages": [
            ("xcrawl", "XCrawl SDK"),
            ("edge_tts", "Edge TTS (播客)"),
            ("yaml", "YAML 解析"),
            ("markdown_it", "Markdown 渲染"),
            ("bs4", "BeautifulSoup"),
            ("jinja2", "邮件模板"),
    ("cloakbrowser", "CloakBrowser 隐身浏览器"),
        ],
    },

    # ── 配置文件 ──
    "config_files": {
        "name": "配置文件",
        "critical": True,
        "test_type": "config",
        "required_files": [
            "config/source_packs.yaml",
            "config/pipeline.yaml",
            "config/model_router.yaml",
            "config/email.yaml",
            "config/scoring.yaml",
            "config/runtime.yaml",
            "config/sources.yaml",
            "config/keywords.yaml",
            "config/search_policy.yaml",
            "config/parallel.yaml",
        ],
    },

    # ── 目录结构 ──
    "directories": {
        "name": "目录与磁盘空间",
        "critical": False,
        "test_type": "directories",
        "required_dirs": [
            "data/raw",
            "data/runs",
            "data/logs",
            "reports/daily",
            "podcasts/audio",
            "podcasts/scripts",
        ],
        "min_free_bytes": 100 * 1024 * 1024,  # 100 MB
    },

    # ── Pipeline 模块可导入性 ──
    "pipeline_modules": {
        "name": "Pipeline 模块",
        "critical": False,
        "test_type": "modules",
        "required_modules": [
            ("skills.collect_daily_articles.collect_daily_articles", "数据采集"),
            ("skills.broad_search_discovery.broad_search_discovery", "广度搜索"),
            ("skills.xcrawl_enrich_articles.xcrawl_enrich_articles", "正文抓取"),
            ("skills.merge_raw_articles.merge_raw_articles", "数据合并"),
            ("skills.filter_relevant_articles.filter_relevant_articles", "相关性过滤"),
            ("skills.extract_events.extract_events", "事件提取"),
            ("skills.score_events.score_events", "事件评分"),
            ("skills.generate_daily_report.generate_daily_report", "日报生成"),
            ("skills.editor_review.editor_review", "编辑审查"),
            ("skills.generate_podcast.generate_podcast", "播客生成"),
            ("skills.send_daily_report_email.send_daily_report_email", "邮件发送"),
            ("skills.health_check.pipeline_alert", "告警邮件"),
        ],
    },

    # ── API 用量配额 ──
    "api_quota": {
        "name": "API 用量配额",
        "critical": True,
        "test_type": "quota",
    },

    # ── CloakBrowser 隐身浏览器 ──
    "cloakbrowser": {
        "name": "CloakBrowser 隐身浏览器",
        "critical": True,  # 采集链路的关键替代方案
        "test_type": "cloakbrowser",
        "test_url": "https://httpbin.org/headers",
    },

    # ── CloakBrowser Collector（采集模块） ──
    "cloakbrowser_collector": {
        "name": "CloakBrowser 采集器",
        "critical": False,
        "test_type": "modules",
        "required_modules": [
            ("skills.cloakbrowser_collect.cloakbrowser_collect", "CloakBrowser 采集器"),
        ],
    },
}


# ═══════════════════════════════════════════════════════
#  通用工具函数
# ═══════════════════════════════════════════════════════

def _load_keys(prefix: str, count: int, start: int = 0) -> list:
    """从环境变量加载 key 列表。
    
    Args:
        prefix: 前缀，如 "xcrawl_key"
        count: 加载数量（含起始索引）
        start: 起始索引，默认0。设为1则跳过 {prefix}（base key）
    """
    keys = []
    if start == 0:
        names = [prefix] if count == 1 else [prefix] + [f"{prefix}{i}" for i in range(1, count)]
    else:
        names = [f"{prefix}{i}" for i in range(start, start + count)]
    for name in names:
        val = os.environ.get(name, "")
        if val:
            keys.append((name, val))
    return keys


def _mask_key(key: str) -> str:
    if len(key) <= 12:
        return key[:4] + "***"
    return key[:8] + "..." + key[-4:]


def _error_type_from_exception(e: Exception) -> str:
    s = str(e).lower()
    if "401" in s or "auth" in s:
        return "auth_failed"
    if "429" in s or "rate" in s:
        return "rate_limited"
    if "402" in s or "quota" in s:
        return "quota_exceeded"
    if "timeout" in s:
        return "timeout"
    if "refused" in s:
        return "connection_refused"
    if "not installed" in s or "modulenotfound" in s or "no module" in s:
        return "sdk_missing"
    return "unknown"


# ═══════════════════════════════════════════════════════
#  各服务测试函数
# ═══════════════════════════════════════════════════════

def test_longcat_key(name: str, key: str, model: str, timeout: int = 20) -> dict:
    try:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 3,
        }).encode()
        req = urllib.request.Request(
            "https://api.longcat.chat/openai/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode())
        tok = data.get("usage", {}).get("completion_tokens", 0)
        return {"ok": True, "tokens": tok}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"ok": False, "status": e.code, "error_type": _error_type_from_exception(e), "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "error_type": _error_type_from_exception(e)}


def test_fallback_llm(timeout: int = 30) -> dict:
    """测试 GPT 兜底 Provider（付费，应始终可用）。

    用 llm_client 的 FALLBACK_* 常量，避免重复硬编码。
    """
    try:
        from skills.utils.llm_client import (
            FALLBACK_KEY_ENV, FALLBACK_BASE_URL, FALLBACK_MODEL,
        )
        key = os.environ.get(FALLBACK_KEY_ENV, "").strip()
        if not key:
            return {"ok": False, "error_type": "no_key",
                    "error": f"env {FALLBACK_KEY_ENV} 未配置"}
        base = FALLBACK_BASE_URL.rstrip("/")
        endpoint = (f"{base}/chat/completions" if base.endswith("/v1")
                    else f"{base}/v1/chat/completions")
        payload = json.dumps({
            "model": FALLBACK_MODEL,
            "messages": [{"role": "user", "content": "ok"}],
            "max_tokens": 3,
        }).encode()
        req = urllib.request.Request(
            endpoint, data=payload,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        json.loads(resp.read().decode())
        return {"ok": True, "model": FALLBACK_MODEL}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return {"ok": False, "status": e.code,
                "error_type": _error_type_from_exception(e), "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200],
                "error_type": _error_type_from_exception(e)}


def test_tavily_key(name: str, key: str, timeout: int = 15) -> dict:
    try:
        payload = json.dumps({"query": "test", "max_results": 1, "search_depth": "basic"}).encode()
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode())
        n = len(data.get("results", []))
        return {"ok": True, "results": n}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        return {"ok": False, "status": e.code, "error_type": _error_type_from_exception(e), "body": body}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "error_type": _error_type_from_exception(e)}


def test_xcrawl_key(name: str, key: str, timeout: int = 15) -> dict:
    try:
        from xcrawl import XcrawlClient
        from xcrawl.types import SearchOptions
        client = XcrawlClient(api_key=key, timeout=timeout)
        opts = SearchOptions(query="test", limit=1)
        result = client.search(opts)
        if isinstance(result, dict) and result.get("status") == "completed":
            data_block = result.get("data", {})
            items = data_block.get("data", []) if isinstance(data_block, dict) else []
            return {"ok": True, "results": len(items)}
        return {"ok": False, "error": f"unexpected response: {str(result)[:100]}"}
    except ImportError:
        return {"ok": False, "error": "xcrawl SDK not installed", "error_type": "sdk_missing"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "error_type": _error_type_from_exception(e)}


def test_rsshub(url: str, timeout: int = 10) -> dict:
    try:
        req = urllib.request.Request(url, method="HEAD")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return {"ok": True, "status": resp.status}
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}


def test_edge_tts(project_root: str = ".", timeout: int = 15) -> dict:
    """实测 edge-tts TTS 合成：生成一小段测试音频。"""
    try:
        import edge_tts
    except ImportError:
        return {"ok": False, "error": "edge-tts 未安装", "error_type": "sdk_missing"}

    test_text = "健康检查测试。"
    test_path = Path(project_root) / "podcasts" / "audio" / ".health_check_test.mp3"
    test_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async def _run():
            communicate = edge_tts.Communicate(test_text, "zh-CN-XiaoxiaoNeural", rate="+0%")
            await communicate.save(str(test_path))

        asyncio.run(_run())

        if test_path.exists() and test_path.stat().st_size > 100:
            test_path.unlink()  # 清理测试文件
            return {"ok": True, "voice": "zh-CN-XiaoxiaoNeural"}
        else:
            test_path.unlink(missing_ok=True)
            return {"ok": False, "error": "TTS 生成文件为空"}
    except Exception as e:
        test_path.unlink(missing_ok=True)
        return {"ok": False, "error": str(e)[:200], "error_type": _error_type_from_exception(e)}


def test_python_packages(packages: List[Tuple[str, str]]) -> dict:
    """逐个 import 关键 Python 包。"""
    ok_list = []
    fail_list = []

    for pkg_name, pkg_label in packages:
        try:
            importlib.import_module(pkg_name)
            ok_list.append(pkg_label)
        except ImportError as e:
            fail_list.append({"pkg": pkg_name, "label": pkg_label, "error": f"ImportError: {e}"})
        except Exception as e:
            fail_list.append({"pkg": pkg_name, "label": pkg_label, "error": str(e)[:120]})

    ok = len(fail_list) == 0
    detail = f"all {len(ok_list)} packages ok" if ok else f"{len(ok_list)} ok, {len(fail_list)} FAILED"
    return {"ok": ok, "ok_count": len(ok_list), "fail_count": len(fail_list),
            "ok_packages": ok_list, "failed_packages": fail_list, "detail": detail}


def test_config_files(project_root: str, files: List[str]) -> dict:
    """检查配置文件存在 + YAML 可解析。"""
    root = Path(project_root)
    ok_list = []
    fail_list = []

    for fpath in files:
        full = root / fpath
        if not full.exists():
            fail_list.append({"file": fpath, "error": "文件不存在"})
            continue
        try:
            import yaml
            with open(full, "r", encoding="utf-8") as fh:
                yaml.safe_load(fh)
            ok_list.append(fpath)
        except ImportError:
            fail_list.append({"file": fpath, "error": "yaml 模块不可用"})
        except Exception as e:
            fail_list.append({"file": fpath, "error": f"YAML 解析失败: {e}"})

    ok = len(fail_list) == 0
    detail = f"all {len(ok_list)} config files valid" if ok else f"{len(ok_list)} ok, {len(fail_list)} INVALID"
    return {"ok": ok, "ok_count": len(ok_list), "fail_count": len(fail_list),
            "ok_files": ok_list, "failed_files": fail_list, "detail": detail}


def test_directories(project_root: str, required_dirs: List[str],
                     min_free_bytes: int = 100 * 1024 * 1024) -> dict:
    """检查目录存在 + 可写 + 磁盘空间。"""
    root = Path(project_root)
    ok_list = []
    fail_list = []
    disk_warning = None

    for dpath in required_dirs:
        full = root / dpath
        if not full.exists():
            fail_list.append({"dir": dpath, "error": "目录不存在"})
        elif not os.access(str(full), os.W_OK):
            fail_list.append({"dir": dpath, "error": "目录不可写"})
        else:
            ok_list.append(dpath)

    # 磁盘空间
    try:
        usage = shutil.disk_usage(str(root))
        free_mb = usage.free / (1024 * 1024)
        if usage.free < min_free_bytes:
            disk_warning = f"磁盘空间不足: {free_mb:.0f} MB (最低要求 {min_free_bytes // (1024*1024)} MB)"
            fail_list.append({"dir": "/", "error": disk_warning})
    except Exception as e:
        fail_list.append({"dir": "/", "error": f"磁盘检测失败: {e}"})

    ok = len(fail_list) == 0
    detail = f"all {len(ok_list)} directories ok" if ok else f"{len(ok_list)} ok, {len(fail_list)} issues"
    return {"ok": ok, "ok_count": len(ok_list), "fail_count": len(fail_list),
            "ok_dirs": ok_list, "failed_dirs": fail_list,
            "disk_free_mb": free_mb if disk_warning is None else free_mb,
            "detail": detail}


def test_pipeline_modules(modules: List[Tuple[str, str]]) -> dict:
    """检查所有 pipeline 步骤模块可 import（不执行）。"""
    ok_list = []
    fail_list = []

    for mod_path, mod_label in modules:
        try:
            importlib.import_module(mod_path)
            ok_list.append(mod_label)
        except ImportError as e:
            fail_list.append({"module": mod_path, "label": mod_label, "error": f"ImportError: {e}"})
        except Exception as e:
            fail_list.append({"module": mod_path, "label": mod_label,
                              "error": f"{type(e).__name__}: {str(e)[:120]}"})

    ok = len(fail_list) == 0
    detail = f"all {len(ok_list)} modules importable" if ok else f"{len(ok_list)} ok, {len(fail_list)} FAILED"
    return {"ok": ok, "ok_count": len(ok_list), "fail_count": len(fail_list),
            "ok_modules": ok_list, "failed_modules": fail_list, "detail": detail}


def test_quota(project_root: str) -> dict:
    """检测本月 API 用量配额，对比按天数比例的预期用量。"""
    try:
        from skills.health_check.quota_tracker import track_quota
    except ImportError:
        return {"ok": True, "status": "healthy", "detail": "quota tracker unavailable"}

    r = track_quota(project_root)

    xc = r["xcrawl"]
    tv = r["tavily"]

    # 综合状态：取两个服务中最差的
    status_order = {"healthy": 0, "warning": 1, "degraded": 2, "critical": 3}
    overall_status = "healthy"
    for s in [xc["status"], tv["status"]]:
        if status_order.get(s, 0) > status_order.get(overall_status, 0):
            overall_status = s

    detail = (
        f"XCrawl {xc['credits_used']}/{xc['monthly_budget']} ({xc['usage_ratio']*100:.0f}%), "
        f"Tavily {tv['credits_used']}/{tv['monthly_budget']} ({tv['usage_ratio']*100:.0f}%)"
    )

    return {
        "ok": overall_status != "critical",
        "status": overall_status,
        "detail": detail,
        "xcrawl": xc,
        "tavily": tv,
        "alerts": r.get("alerts", []),
        "days_elapsed": r["days_elapsed"],
        "days_in_month": r["days_in_month"],
    }


def test_cloakbrowser(test_url: str = "https://httpbin.org/headers") -> dict:
    """测试 CloakBrowser 隐身浏览器是否可用。
    
    检测步骤：
    1. 包是否可导入
    2. Chromium 二进制是否存在（优先补丁版，fallback 系统 Chromium）
    3. 是否能启动 headless 浏览器
    4. 是否能访问测试页面
    """
    import os
    
    # Step 1: 导入
    try:
        from cloakbrowser import launch, get_default_stealth_args, binary_info
    except ImportError as e:
        return {
            "ok": False,
            "error_type": "sdk_missing",
            "error": f"cloakbrowser not installed: {e}",
            "detail": "pip install cloakbrowser",
        }
    
    # Step 2: 检查二进制（优先补丁版，fallback 系统 Chromium）
    info = binary_info()
    bp = info.get("binary_path", "")
    binary_source = "patched"
    
    if not (bp and os.path.exists(bp)):
        # Fallback: 系统 Chromium
        for sys_chrome in [
            os.environ.get("CLOAKBROWSER_BINARY_PATH", ""),
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ]:
            if sys_chrome and os.path.exists(sys_chrome):
                os.environ["CLOAKBROWSER_BINARY_PATH"] = sys_chrome
                binary_source = f"system ({sys_chrome})"
                bp = sys_chrome
                break
        
        if not bp or not os.path.exists(bp):
            return {
                "ok": False,
                "error_type": "binary_missing",
                "error": "Chromium binary not installed (patched or system)",
                "detail": f"Patched: {info.get('cache_dir', '?')}, system: /usr/bin/chromium",
            }
    
    # Step 3: 启动浏览器并访问测试页
    browser = None
    try:
        browser = launch(
            headless=True,
            args=get_default_stealth_args() + ["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.goto(test_url, wait_until="domcontentloaded", timeout=20000)
        title = page.title()
        page.close()
        
        return {
            "ok": True,
            "detail": f"Headless launch OK ({binary_source}), page: {title[:50]}",
            "chromium_version": info.get("version", "?"),
            "binary_path": bp,
            "binary_source": binary_source,
        }
    except Exception as e:
        err_str = str(e).lower()
        error_type = "unknown"
        if "timeout" in err_str:
            error_type = "timeout"
        elif "refused" in err_str:
            error_type = "connection_refused"
        elif "sandbox" in err_str:
            error_type = "sandbox_error"
        
        return {
            "ok": False,
            "error_type": error_type,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════
#  主健康检查函数
# ═══════════════════════════════════════════════════════

def run_health_check(project_root: str = ".", logger: logging.Logger = None) -> dict:
    if logger is None:
        logger = logging.getLogger("health_check")

    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    result = {
        "timestamp": now,
        "overall": "healthy",
        "services": {},
        "alerts": [],
        "ok": True,
    }

    root_path = Path(project_root)

    for svc_id, svc_cfg in SERVICES.items():
        svc_result = {
            "name": svc_cfg["name"],
            "critical": svc_cfg["critical"],
            "status": "healthy",
            "detail": "",
            "keys_tested": 0,
            "keys_ok": 0,
            "keys_failed": 0,
            "failed_keys": [],
            "keys_total": 0,
        }

        test_type = svc_cfg.get("test_type", "api_key")

        # ── API Key 类服务 ──
        if test_type == "api_key":
            keys = _load_keys(svc_cfg["key_prefixes"][0], svc_cfg["key_count"],
                              svc_cfg.get("key_start", 0))
            svc_result["keys_total"] = len(keys)

            if not keys:
                svc_result["status"] = "down"
                svc_result["detail"] = "no keys configured"
                result["alerts"].append(f"[CRITICAL] {svc_cfg['name']}: no keys configured")
            else:
                for key_name, key_val in keys:
                    if svc_id == "longcat_llm":
                        r = test_longcat_key(key_name, key_val, svc_cfg.get("test_model", "LongCat-2.0"))
                    elif svc_id == "tavily_search":
                        r = test_tavily_key(key_name, key_val)
                    elif svc_id == "xcrawl":
                        r = test_xcrawl_key(key_name, key_val)
                    else:
                        continue

                    svc_result["keys_tested"] += 1
                    if r["ok"]:
                        svc_result["keys_ok"] += 1
                    else:
                        error_type = r.get("error_type", r.get("error", str(r.get("status", "unknown"))))
                        svc_result["keys_failed"] += 1
                        svc_result["failed_keys"].append({
                            "key": _mask_key(key_val),
                            "env_name": key_name,
                            "error": error_type,
                        })
                        # 记录失败类型，用于区分"暂时耗尽"和"真正坏了"
                        svc_result.setdefault("failure_types", set()).add(error_type)
                    time.sleep(0.2)

        # ── URL 连通性 ──
        elif test_type == "url":
            r = test_rsshub(svc_cfg["test_url"])
            svc_result["keys_tested"] = 1
            svc_result["keys_total"] = 1
            if r["ok"]:
                svc_result["keys_ok"] = 1
                svc_result["detail"] = f"HTTP {r.get('status', 'ok')}"
            else:
                svc_result["keys_failed"] = 1
                svc_result["failed_keys"].append({
                    "key": svc_cfg["test_url"],
                    "env_name": "RSSHUB_BASE",
                    "error": r.get("error", "unknown"),
                })

        # ── Edge TTS ──
        elif test_type == "tts":
            r = test_edge_tts(str(root_path))
            svc_result["keys_tested"] = 1
            svc_result["keys_total"] = 1
            if r["ok"]:
                svc_result["keys_ok"] = 1
                svc_result["detail"] = f"TTS 合成正常 ({r.get('voice', '?')})"
            else:
                svc_result["keys_failed"] = 1
                svc_result["failed_keys"].append({
                    "key": "edge-tts",
                    "env_name": "EDGE_TTS",
                    "error": r.get("error_type", r.get("error", "TTS failed")),
                })

        # ── Python 包 ──
        elif test_type == "imports":
            r = test_python_packages(svc_cfg.get("required_packages", []))
            svc_result.update(r)
            if r.get("failed_packages"):
                for fp in r["failed_packages"]:
                    svc_result["failed_keys"].append({
                        "key": fp["pkg"],
                        "env_name": f"pip:{fp['pkg']}",
                        "error": fp["error"],
                    })
                # ── 自愈：缺包时自动 pip install ──
                auto_fix = svc_cfg.get("auto_fix", False)
                if auto_fix:
                    logger.info(f"[自愈] 检测到 {len(r['failed_packages'])} 个缺失包，尝试自动安装...")
                    try:
                        from skills.health_check.pip_ensure import ensure
                        fix_result = ensure(project_root=str(root_path), quiet=True, auto_fix=True)
                        if fix_result["ok"]:
                            # 复检 — 清空 failed_keys 重新收集
                            r2 = test_python_packages(svc_cfg.get("required_packages", []))
                            svc_result.update(r2)
                            svc_result["failed_keys"] = []  # 重置
                            if r2.get("failed_packages"):
                                for fp in r2["failed_packages"]:
                                    svc_result["failed_keys"].append({
                                        "key": fp["pkg"],
                                        "env_name": f"pip:{fp['pkg']}",
                                        "error": fp["error"],
                                    })
                            r = r2  # 更新 r 引用，让后续状态判断用复检结果
                            logger.info(f"[自愈] pip install 完成，复检: ok={r2.get('ok')}, "
                                        f"fail={r2.get('fail_count', 0)}")
                        else:
                            logger.warning(f"[自愈] pip_ensure 修复失败: {fix_result}")
                    except Exception as e:
                        logger.warning(f"[自愈] pip_ensure 失败: {e}")

        # ── 配置文件 ──
        elif test_type == "config":
            r = test_config_files(str(root_path), svc_cfg.get("required_files", []))
            svc_result.update(r)
            if r.get("failed_files"):
                for ff in r["failed_files"]:
                    svc_result["failed_keys"].append({
                        "key": ff["file"],
                        "env_name": ff["file"],
                        "error": ff["error"],
                    })

        # ── 目录 / 磁盘 ──
        elif test_type == "directories":
            min_bytes = svc_cfg.get("min_free_bytes", 100 * 1024 * 1024)
            r = test_directories(str(root_path), svc_cfg.get("required_dirs", []), min_bytes)
            svc_result.update(r)
            if r.get("failed_dirs"):
                for fd in r["failed_dirs"]:
                    svc_result["failed_keys"].append({
                        "key": fd["dir"],
                        "env_name": fd["dir"],
                        "error": fd["error"],
                    })

        # ── Pipeline 模块 ──
        elif test_type == "modules":
            r = test_pipeline_modules(svc_cfg.get("required_modules", []))
            svc_result.update(r)
            if r.get("failed_modules"):
                for fm in r["failed_modules"]:
                    svc_result["failed_keys"].append({
                        "key": fm["module"],
                        "env_name": f"import:{fm['module'].rsplit('.', 1)[-1]}",
                        "error": fm["error"],
                    })

        # ── CloakBrowser 隐身浏览器 ──
        elif test_type == "cloakbrowser":
            r = test_cloakbrowser(svc_cfg.get("test_url", "https://httpbin.org/headers"))
            svc_result["keys_tested"] = 1
            svc_result["keys_total"] = 1
            if r["ok"]:
                svc_result["keys_ok"] = 1
                svc_result["detail"] = r.get("detail", f"CloakBrowser v{r.get('chromium_version', '?')}")
                svc_result["chromium_version"] = r.get("chromium_version", "?")
            else:
                svc_result["keys_failed"] = 1
                error_type = r.get("error_type", "unknown")
                svc_result["failed_keys"].append({
                    "key": "cloakbrowser",
                    "env_name": "CLOAKBROWSER",
                    "error": error_type,
                })
                svc_result["detail"] = r.get("error", r.get("detail", "unknown error"))
                # sdk_missing 和 binary_missing 是硬失败
                if error_type in ("sdk_missing", "binary_missing"):
                    svc_result["status"] = "down"
                    result["alerts"].append(
                        f"[CRITICAL] CloakBrowser: {error_type} — {r.get('detail', r.get('error', '?'))}"
                    )
                else:
                    svc_result["status"] = "degraded"
                    result["alerts"].append(
                        f"[WARNING] CloakBrowser: {error_type} — {r.get('error', '?')}"
                    )
            result["services"][svc_id] = svc_result
            continue

        # ── API 用量配额 ──
        elif test_type == "quota":
            r = test_quota(str(root_path))
            svc_result.update(r)
            quota_status = r.get("status", "healthy")
            # 将 quota 内部状态映射到健康检查状态
            status_map = {"healthy": "healthy", "warning": "degraded",
                          "degraded": "degraded", "critical": "down"}
            svc_result["status"] = status_map.get(quota_status, "healthy")
            svc_result["detail"] = r.get("detail", "")
            for alert in r.get("alerts", []):
                result["alerts"].append(alert)
            # quota 状态自己决定，跳过后续汇总逻辑
            result["services"][svc_id] = svc_result
            continue

        # ── 汇总状态 ──
        total = svc_result.get("keys_tested", 0)
        ok = svc_result.get("keys_ok", 0)
        fail_count = svc_result.get("fail_count", svc_result.get("keys_failed", 0))

        # 判断所有失败是否都是"软失败"（可能自行恢复，如额度用完/限流）
        SOFT_FAILURE_TYPES = {"auth_failed", "quota_exceeded", "rate_limited"}
        failure_types = svc_result.get("failure_types", set())
        all_soft_failures = bool(failure_types) and failure_types.issubset(SOFT_FAILURE_TYPES)

        # 对于 imports/config/directories/modules 类，用他们的 ok 字段
        if test_type in ("imports", "config", "directories", "modules") and "ok" in r:
            if r["ok"]:
                svc_result["status"] = "healthy"
            else:
                svc_result["status"] = "down" if svc_cfg["critical"] else "degraded"
        elif total == 0:
            svc_result["status"] = "unknown"
        elif svc_result.get("keys_ok", 0) == 0:
            # 全部 key 都挂了
            if all_soft_failures:
                # 全是额度/限流问题，下个月可能恢复 → degraded 而非 down
                svc_result["status"] = "degraded"
                svc_result["detail"] = f"{fail_count} keys temporarily unavailable (quota/auth), may recover next cycle"
            else:
                svc_result["status"] = "down"
        elif fail_count > 0:
            # 至少有一个 key 正常工作
            if all_soft_failures:
                # 只有软失败，轮换会自动跳过 → healthy
                svc_result["status"] = "healthy"
                svc_result["detail"] = f"{ok}/{total} keys ok, {fail_count} temporarily exhausted"
            elif svc_cfg["critical"]:
                svc_result["status"] = "degraded"
            else:
                svc_result["status"] = "healthy"

        # ── LongCat 专属：GPT 兜底可用则不算严重故障 ──
        # 业务逻辑：所有 LongCat key 不可用时才用付费 GPT 兜底；
        # 只要 GPT 兜底正常，LLM 能力即正常，不应刷 CRITICAL 告警。
        if svc_id == "longcat_llm" and svc_result["status"] in ("down", "degraded"):
            fb = test_fallback_llm()
            svc_result["fallback_ok"] = fb["ok"]
            if fb["ok"]:
                # LongCat 挂了但 GPT 兜底正常 → 整体可用，仅信息提示
                svc_result["status"] = "healthy"
                svc_result["detail"] = (
                    f"LongCat {ok}/{total} keys ok; "
                    f"GPT 兜底({fb.get('model','')})正常，LLM 能力可用")
                svc_result["degraded_reason"] = "longcat_down_fallback_ok"
            else:
                # LongCat 和 GPT 兜底都挂了 → 真正的严重故障
                svc_result["status"] = "down"
                svc_result["detail"] = (
                    f"LongCat 全部不可用，且 GPT 兜底也失败"
                    f"({fb.get('error_type', fb.get('error',''))})")

        # 生成告警
        now_ds = svc_result["status"]
        if now_ds == "down" and svc_cfg["critical"]:
            result["alerts"].append(f"[CRITICAL] {svc_cfg['name']}: {svc_result.get('detail', 'all keys unavailable')}")
        elif now_ds in ("down", "degraded") and svc_cfg["critical"]:
            result["alerts"].append(f"[WARNING] {svc_cfg['name']}: {svc_result.get('detail', 'partial failure')}")
        elif now_ds == "down":
            result["alerts"].append(f"[INFO] {svc_cfg['name']}: {svc_result.get('detail', 'unavailable')}")

        result["services"][svc_id] = svc_result

    # ── 清理：set → list 以便 JSON 序列化 ──
    for svc in result["services"].values():
        if "failure_types" in svc and isinstance(svc["failure_types"], set):
            svc["failure_types"] = sorted(svc["failure_types"])

    # ── Overall 判定 ──
    critical_issues = any(
        s["status"] == "down" and s.get("critical", False) for s in result["services"].values()
    )
    degraded_issues = any(
        s["status"] in ("down", "degraded") for s in result["services"].values()
    )
    result["overall"] = "critical" if critical_issues else ("degraded" if degraded_issues else "healthy")
    return result


# ═══════════════════════════════════════════════════════
#  格式化输出
# ═══════════════════════════════════════════════════════

def format_health_report(result: dict) -> str:
    emoji_map = {"healthy": "OK", "degraded": "WARN", "down": "DOWN", "unknown": "UNKN"}

    lines = [
        f"# Service Health Check",
        f"Time: {result['timestamp']}",
        f"Overall: {result['overall'].upper()}",
        "",
    ]
    for svc_id, svc in result["services"].items():
        emoji = emoji_map.get(svc.get("status", "?"), "??")
        lines.append(f"## {emoji} {svc['name']}")
        # detail
        detail = svc.get("detail", "")
        if not detail:
            total = svc.get("keys_total", svc.get("keys_tested", 0))
            ok = svc.get("keys_ok", 0)
            detail = f"{ok}/{total} available" if total > 0 else "no data"
        lines.append(f"Status: {svc.get('status', '?')} | {detail}")
        # failed items
        for fk in svc.get("failed_keys", []):
            lines.append(f"  - FAIL {fk.get('env_name', fk.get('key','?'))}: {fk.get('error','?')}")
        lines.append("")

    if result["alerts"]:
        lines.append("## ALERTS")
        for a in result["alerts"]:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="全流程健康检查")
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--date", default=None, help="Date YYYY-MM-DD for log saving")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--quiet", action="store_true", help="Only alerts")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("health_check")
    result = run_health_check(args.project_root, logger)

    # 保存结果到日志目录
    if args.date and args.project_root:
        log_dir = Path(args.project_root) / "data" / "logs" / args.date
        log_dir.mkdir(parents=True, exist_ok=True)
        hc_file = log_dir / "health_check.json"
        hc_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Health check result saved: {hc_file}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_health_report(result))

    if result["overall"] == "critical":
        sys.exit(2)
    elif result["overall"] == "degraded":
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
