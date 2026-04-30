#!/usr/bin/env python3
"""
check_rsshub_routes.py — RSSHub 路由健康检查

读取 config/sources.yaml 中 rsshub_sources，
逐条请求 RSS，统计条目数、标题数、URL数、pubDate数等健康指标，
输出 JSON 和 Markdown 摘要。

用法:
    python check_rsshub_routes.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-04-26

输出:
    data/logs/rsshub_health/YYYY-MM-DD.json
    data/logs/rsshub_health/YYYY-MM-DD.md
"""

import argparse
import json
import logging
import os
import sys
import time as time_mod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

# ===================== 依赖检查 =====================
_MISSING = []
try:
    import yaml
except ImportError:
    _MISSING.append("PyYAML")

try:
    import requests
except ImportError:
    _MISSING.append("requests")

try:
    import feedparser
except ImportError:
    _MISSING.append("feedparser")

try:
    from dateutil import parser as dateutil_parser
    from dateutil import tz as dateutil_tz
except ImportError:
    _MISSING.append("python-dateutil")

if _MISSING:
    print(f"ERROR: 缺少必要依赖: {', '.join(_MISSING)}\n"
          f"请运行: pip install {' '.join(_MISSING)}", file=sys.stderr)
    sys.exit(1)


CST = dateutil_tz.gettz("Asia/Shanghai")
DEFAULT_RSSHUB_BASE = "http://192.168.2.100:1200"
DEFAULT_TIMEOUT = 20
USER_AGENT = "WatsonRetailIntelBot/0.2-HealthCheck"


def load_yaml(filepath: str) -> dict:
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_rsshub_base(source_config: dict, rsshub_base_arg: Optional[str] = None) -> str:
    if rsshub_base_arg:
        return rsshub_base_arg.rstrip("/")
    env_val = os.environ.get("RSSHUB_BASE_URL")
    if env_val:
        return env_val.rstrip("/")
    config_val = source_config.get("rsshub_base")
    if config_val:
        return str(config_val).rstrip("/")
    return DEFAULT_RSSHUB_BASE


def classify_grade(ok, item_count, items_with_url, old_ratio):
    """分级：
    A类：ok=true 且 item_count>=5 且 items_with_url>=5 且 old_item_ratio<=0.8
    B类：ok=true 但 item_count<5
    C类：ok=true 但 old_item_ratio>0.8
    D类：失败或超时
    """
    if not ok:
        return "D"
    if item_count < 5 or items_with_url < 5:
        return "B"
    if old_ratio > 0.8:
        return "C"
    return "A"


def check_single_route(
    route_id: str,
    route_name: str,
    full_url: str,
    timeout: int = DEFAULT_TIMEOUT,
    window_hours: int = 24,
) -> dict:
    """检查单条 RSSHub 路由的健康状态。"""
    result = {
        "id": route_id,
        "name": route_name,
        "url": full_url,
        "ok": False,
        "status_code": None,
        "item_count": 0,
        "items_with_title": 0,
        "items_with_url": 0,
        "items_with_pubdate": 0,
        "latest_pubdate": None,
        "old_item_ratio": 0.0,
        "unknown_time_ratio": 0.0,
        "recent_24h_count": 0,
        "latency_ms": 0,
        "grade": "D",
        "error": None,
    }

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })

    start_ts = time_mod.time()
    try:
        resp = session.get(full_url, timeout=timeout, allow_redirects=True)
        result["latency_ms"] = int((time_mod.time() - start_ts) * 1000)
        result["status_code"] = resp.status_code

        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}"
            return result

    except requests.exceptions.Timeout:
        result["latency_ms"] = int((time_mod.time() - start_ts) * 1000)
        result["error"] = f"Timeout after {timeout}s"
        return result
    except Exception as e:
        result["latency_ms"] = int((time_mod.time() - start_ts) * 1000)
        result["error"] = str(e)
        return result

    # 解析 RSS
    try:
        feed = feedparser.parse(resp.text)
    except Exception as e:
        result["error"] = f"Feed parse error: {e}"
        return result

    # 检查 feed 是否有错误
    if hasattr(feed, "bozo_exception") and feed.bozo_exception:
        bozo_err = str(feed.bozo_exception)
        # 不因为 bozo 就判定失败，某些 RSS 有小问题但仍可用
        result["error"] = f"bozo: {bozo_err[:200]}"
        # 继续处理

    entries = feed.entries
    item_count = len(entries)
    result["item_count"] = item_count

    if item_count == 0:
        result["ok"] = True  # HTTP 200 但无条目
        result["grade"] = classify_grade(True, 0, 0, 0.0)
        return result

    items_with_title = 0
    items_with_url = 0
    items_with_pubdate = 0
    old_count = 0
    unknown_time_count = 0
    recent_24h_count = 0
    latest_dt = None

    now = datetime.now(CST)
    cutoff_old = now - timedelta(hours=window_hours * 3)  # old = 超过72小时
    cutoff_24h = now - timedelta(hours=24)

    for entry in entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        pub_date_str = entry.get("published", entry.get("updated", ""))

        if title:
            items_with_title += 1
        if link:
            items_with_url += 1

        if pub_date_str:
            items_with_pubdate += 1
            try:
                dt = dateutil_parser.parse(pub_date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CST)

                if latest_dt is None or dt > latest_dt:
                    latest_dt = dt

                if dt >= cutoff_24h:
                    recent_24h_count += 1
                if dt < cutoff_old:
                    old_count += 1
            except (ValueError, TypeError, OverflowError):
                unknown_time_count += 1
        else:
            unknown_time_count += 1

    result["ok"] = True
    result["items_with_title"] = items_with_title
    result["items_with_url"] = items_with_url
    result["items_with_pubdate"] = items_with_pubdate
    result["latest_pubdate"] = latest_dt.isoformat() if latest_dt else None
    result["old_item_ratio"] = round(old_count / max(item_count, 1), 4)
    result["unknown_time_ratio"] = round(unknown_time_count / max(item_count, 1), 4)
    result["recent_24h_count"] = recent_24h_count

    result["grade"] = classify_grade(
        result["ok"],
        result["item_count"],
        result["items_with_url"],
        result["old_item_ratio"],
    )

    return result


def check_rsshub_routes(
    project_root: str,
    date: Optional[str] = None,
    rsshub_base: Optional[str] = None,
    sources_file: str = "config/sources.yaml",
    timeout: int = DEFAULT_TIMEOUT,
    interval_seconds: float = 0.5,
) -> dict:
    """执行所有 RSSHub 路由的健康检查。"""
    if not date:
        date = datetime.now(CST).strftime("%Y-%m-%d")

    sources_path = os.path.join(project_root, sources_file)
    config = load_yaml(sources_path)

    resolved_base = get_rsshub_base(config, rsshub_base)
    rsshub_sources = config.get("rsshub_sources", [])

    enabled_routes = [s for s in rsshub_sources if s.get("enabled", True)]

    print(f"RSSHub 健康检查: date={date}")
    print(f"RSSHub 基础地址: {resolved_base}")
    print(f"启用路由数: {len(enabled_routes)}")
    print("=" * 70)

    results: List[dict] = []

    for i, source in enumerate(enabled_routes):
        route_id = source.get("id", f"unknown_{i}")
        route_name = source.get("name", route_id)
        route = source.get("route", "")

        if not route:
            # 处理 routes 列表
            routes = source.get("routes", [])
            if routes:
                for sub_route in routes:
                    full_url = f"{resolved_base}{sub_route}"
                    print(f"  [{i+1}/{len(enabled_routes)}] {route_id} (sub-route: {sub_route})...", end=" ", flush=True)
                    result = check_single_route(f"{route_id}_{sub_route.replace('/', '_')}", route_name, full_url, timeout)
                    results.append(result)
                    print(f"{'✅' if result['ok'] else '❌'} Grade={result['grade']} Items={result['item_count']}")
            else:
                result = {
                    "id": route_id, "name": route_name, "url": "",
                    "ok": False, "status_code": None, "item_count": 0,
                    "items_with_title": 0, "items_with_url": 0,
                    "items_with_pubdate": 0, "latest_pubdate": None,
                    "old_item_ratio": 0.0, "unknown_time_ratio": 0.0,
                    "recent_24h_count": 0, "latency_ms": 0,
                    "grade": "D", "error": "no route defined",
                }
                results.append(result)
                print(f"  [{i+1}/{len(enabled_routes)}] {route_id} — 无路由配置 ❌")
            continue

        full_url = f"{resolved_base}{route}"
        print(f"  [{i+1}/{len(enabled_routes)}] {route_id}: {route_name}...", end=" ", flush=True)

        result = check_single_route(route_id, route_name, full_url, timeout)
        results.append(result)
        print(f"{'✅' if result['ok'] else '❌'} Grade={result['grade']} Items={result['item_count']} Latency={result['latency_ms']}ms")

        # 请求间隔
        if i < len(enabled_routes) - 1:
            time_mod.sleep(interval_seconds)

    # ---- 统计汇总 ----
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    failed_routes: List[dict] = []
    recent_24h_sorted: List[dict] = []

    for r in results:
        grade = r.get("grade", "D")
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
        if not r.get("ok"):
            failed_routes.append(r)
        if r.get("recent_24h_count", 0) > 0:
            recent_24h_sorted.append(r)

    recent_24h_sorted.sort(key=lambda x: x.get("recent_24h_count", 0), reverse=True)
    top_10 = recent_24h_sorted[:10]

    # 建议禁用：D 类
    suggest_disable = [r for r in results if r.get("grade") == "D"]
    # 建议保留：A + B 类
    suggest_keep = [r for r in results if r.get("grade") in ("A", "B")]

    summary = {
        "date": date,
        "rsshub_base": resolved_base,
        "total_routes": len(results),
        "grade_counts": grade_counts,
        "failed_routes": [
            {"id": r["id"], "name": r.get("name", ""), "error": r.get("error", "")}
            for r in failed_routes
        ],
        "top_10_by_recent_24h": [
            {"id": r["id"], "name": r.get("name", ""), "recent_24h_count": r.get("recent_24h_count", 0),
             "item_count": r.get("item_count", 0), "grade": r.get("grade", "")}
            for r in top_10
        ],
        "suggest_disable": [
            {"id": r["id"], "name": r.get("name", ""), "error": r.get("error", "")}
            for r in suggest_disable
        ],
        "suggest_keep": [
            {"id": r["id"], "name": r.get("name", ""), "grade": r.get("grade", ""),
             "item_count": r.get("item_count", 0)}
            for r in suggest_keep
        ],
        "routes": results,
    }

    # ---- 保存 JSON ----
    output_dir = os.path.join(project_root, "data", "logs", "rsshub_health")
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, f"{date}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---- 生成 Markdown 报告 ----
    md_lines = []
    md_lines.append(f"# RSSHub 路由健康检查报告 — {date}\n")
    md_lines.append(f"**RSSHub 基础地址**: `{resolved_base}`\n")
    md_lines.append(f"**检查路由数**: {len(results)}\n")

    md_lines.append("## 分级汇总\n")
    md_lines.append("| 分级 | 数量 | 说明 |")
    md_lines.append("|------|------|------|")
    md_lines.append(f"| A类 | {grade_counts.get('A', 0)} | 可稳定使用 |")
    md_lines.append(f"| B类 | {grade_counts.get('B', 0)} | 可用但产出少 |")
    md_lines.append(f"| C类 | {grade_counts.get('C', 0)} | 旧文比例高，仅作参考 |")
    md_lines.append(f"| D类 | {grade_counts.get('D', 0)} | 失败或超时 |\n")

    md_lines.append("## 失败路由列表\n")
    if failed_routes:
        md_lines.append("| ID | 名称 | 错误 |")
        md_lines.append("|-----|------|------|")
        for r in failed_routes:
            md_lines.append(f"| {r['id']} | {r.get('name', '')} | {r.get('error', 'N/A')[:80]} |")
    else:
        md_lines.append("✅ 无失败路由")
    md_lines.append("")

    md_lines.append("## 近24小时有效条目最多的前10个源\n")
    md_lines.append("| ID | 名称 | 24h条目 | 总条目 | 分级 |")
    md_lines.append("|-----|------|---------|--------|------|")
    for r in top_10:
        md_lines.append(
            f"| {r['id']} | {r.get('name', '')} "
            f"| {r.get('recent_24h_count', 0)} | {r.get('item_count', 0)} "
            f"| {r.get('grade', '')} |"
        )
    md_lines.append("")

    md_lines.append("## 建议禁用的源\n")
    if suggest_disable:
        md_lines.append("| ID | 名称 | 错误 |")
        md_lines.append("|-----|------|------|")
        for r in suggest_disable:
            md_lines.append(f"| {r['id']} | {r.get('name', '')} | {r.get('error', 'N/A')[:80]} |")
    else:
        md_lines.append("✅ 无建议禁用的源")
    md_lines.append("")

    md_lines.append("## 建议保留的源（A/B类）\n")
    md_lines.append("| ID | 名称 | 分级 | 条目数 |")
    md_lines.append("|-----|------|------|--------|")
    for r in suggest_keep:
        md_lines.append(f"| {r['id']} | {r.get('name', '')} | {r.get('grade', '')} | {r.get('item_count', 0)} |")
    md_lines.append("")

    md_lines.append("## 全部路由详情\n")
    md_lines.append("| ID | 名称 | 分级 | OK | 条目 | 有标题 | 有URL | 有日期 | 24h | 旧文率 | 延迟ms | 错误 |")
    md_lines.append("|-----|------|------|-----|------|--------|-------|--------|-----|--------|--------|------|")
    for r in results:
        ok_text = "✅" if r.get("ok") else "❌"
        error_text = (r.get("error") or "")[:40]
        md_lines.append(
            f"| {r['id']} | {r.get('name', '')} | {r.get('grade', '')} "
            f"| {ok_text} | {r.get('item_count', 0)} | {r.get('items_with_title', 0)} "
            f"| {r.get('items_with_url', 0)} | {r.get('items_with_pubdate', 0)} "
            f"| {r.get('recent_24h_count', 0)} | {r.get('old_item_ratio', 0):.1%} "
            f"| {r.get('latency_ms', 0)} | {error_text} |"
        )

    md_content = "\n".join(md_lines)

    md_path = os.path.join(output_dir, f"{date}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # ---- 打印汇总 ----
    print("\n" + "=" * 70)
    print("RSSHub 健康检查汇总")
    print("=" * 70)
    print(f"A 类源数量: {grade_counts.get('A', 0)}")
    print(f"B 类源数量: {grade_counts.get('B', 0)}")
    print(f"C 类源数量: {grade_counts.get('C', 0)}")
    print(f"D 类源数量: {grade_counts.get('D', 0)}")
    print()
    if failed_routes:
        print("失败源列表:")
        for r in failed_routes:
            print(f"  ❌ {r['id']}: {r.get('name', '')} — {r.get('error', 'N/A')[:60]}")
    else:
        print("✅ 无失败路由")
    print()
    print("近24小时有效条目最多的前10个源:")
    for r in top_10:
        print(f"  {r['id']}: {r.get('name', '')} — {r.get('recent_24h_count', 0)} 条/24h (共 {r.get('item_count', 0)} 条)")
    print()
    print("建议禁用的源:")
    if suggest_disable:
        for r in suggest_disable:
            print(f"  ❌ {r['id']}: {r.get('name', '')} — {r.get('error', 'N/A')[:60]}")
    else:
        print("  ✅ 无建议禁用")
    print()
    print("建议保留的源 (A/B类):")
    for r in suggest_keep:
        print(f"  ✅ {r['id']}: {r.get('name', '')} — {r.get('grade', '')} 类, {r.get('item_count', 0)} 条")

    print(f"\nJSON 输出: {json_path}")
    print(f"MD 报告: {md_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="RSSHub 路由健康检查")
    parser.add_argument("--project-root", required=True, help="项目根目录路径")
    parser.add_argument("--date", default=None, help="检查日期 (YYYY-MM-DD)")
    parser.add_argument("--rsshub-base", default=None, help="RSSHub 基础地址")
    parser.add_argument("--sources-file", default="config/sources.yaml", help="源配置文件路径")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="请求超时秒数")
    parser.add_argument("--interval", type=float, default=0.5, help="请求间隔秒数")

    args = parser.parse_args()

    result = check_rsshub_routes(
        project_root=args.project_root,
        date=args.date,
        rsshub_base=args.rsshub_base,
        sources_file=args.sources_file,
        timeout=args.timeout,
        interval_seconds=args.interval,
    )

    return result


if __name__ == "__main__":
    main()