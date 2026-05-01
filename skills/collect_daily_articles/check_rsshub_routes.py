#!/usr/bin/env python3
"""RSSHub 路由健康检查脚本
按 RSSHub 采集配置指引 V2 第 5 节实现。
输出: data/logs/rsshub_health/YYYY-MM-DD.json + .md
"""

import argparse, json, logging, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote

import xml.etree.ElementTree as ET

CST = timezone(timedelta(hours=8))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rsshub_health")


def load_yaml(path: str) -> dict:
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        log.error("需要 PyYAML: pip install pyyaml")
        sys.exit(1)


def parse_rss_date(date_str: str) -> Optional[datetime]:
    """解析 RSS pubDate，兼容 GMT/UTC 时区。"""
    if not date_str:
        return None
    # Python strptime %z 不认 GMT，先替换
    normalized = date_str.strip()
    normalized = normalized.replace(" GMT", " +0000").replace(" UT", " +0000")
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ]:
        try:
            return datetime.strptime(normalized, fmt)
        except (ValueError, OverflowError):
            continue
    return None


def parse_rss_items(xml_text: str) -> Tuple[int, int, int, int, Optional[str], List[str]]:
    """解析 RSS XML，返回统计信息。
    Returns:
        (item_count, items_with_title, items_with_url, items_with_pubdate,
         latest_pubdate, pubdate_list)
    """
    try:
        root = ET.fromstring(xml_text)
        # RSSHub 可能返回 RSS 2.0 或 Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//atom:entry", ns)

        item_count = len(items)
        with_title = 0
        with_url = 0
        with_pub = 0
        pubdates = []
        latest = None

        for item in items:
            title = item.findtext("title")
            if title:
                with_title += 1

            link = item.findtext("link") or item.findtext("atom:link", namespaces=ns)
            if not link:
                link_el = item.find("link")
                if link_el is not None:
                    link = link_el.text or link_el.get("href", "")
            if link:
                with_url += 1

            pub_el = item.findtext("pubDate") or item.findtext("published") or item.findtext("updated")
            if pub_el:
                with_pub += 1
                pubdates.append(pub_el)
                dt = parse_rss_date(pub_el)
                if dt and (latest is None or dt > latest):
                    latest = dt

        latest_str = latest.isoformat() if latest else None
        return item_count, with_title, with_url, with_pub, latest_str, pubdates

    except ET.ParseError as e:
        log.warning(f"XML 解析失败: {e}")
        return 0, 0, 0, 0, None, []


def compute_ratios(
    pubdates: List[str], window_start: datetime, window_end: datetime
) -> Tuple[float, float]:
    """计算 old_item_ratio 和 unknown_time_ratio。"""
    total = len(pubdates)
    if total == 0:
        return 1.0, 1.0  # 全部视为旧+未知

    old_count = 0
    unknown_count = 0
    for pd_str in pubdates:
        parsed = parse_rss_date(pd_str)
        if parsed is None:
            unknown_count += 1
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CST)
        if parsed < window_start - timedelta(hours=12):
            old_count += 1

    return old_count / total, unknown_count / total


def classify_grade(
    ok: bool,
    item_count: int,
    items_with_url: int,
    old_item_ratio: float,
) -> str:
    """A/B/C/D 分级。"""
    if not ok:
        return "D"
    if item_count >= 5 and items_with_url >= 5 and old_item_ratio <= 0.8:
        return "A"
    if item_count < 5:
        return "B"
    if old_item_ratio > 0.8:
        return "C"
    return "A"  # fallback


def check_single_route(
    source: dict,
    rsshub_base: str,
    window_start: datetime,
    window_end: datetime,
    timeout: int = 15,
) -> dict:
    """检查单条 RSSHub 路由。"""
    route = source.get("route", "")
    source_id = source.get("id", "unknown")
    source_name = source.get("name", source_id)

    full_url = urljoin(rsshub_base, route.lstrip("/"))
    if "?" in route and not ("?" in full_url.split(rsshub_base)[-1] if rsshub_base in full_url else False):
        # urljoin may drop query params, handle manually
        base = rsshub_base.rstrip("/")
        full_url = f"{base}{route}" if route.startswith("/") else f"{base}/{route}"

    # Percent-encode Chinese characters in path
    from urllib.parse import urlparse, urlunparse, quote
    parsed = urlparse(full_url)
    encoded_path = quote(parsed.path, safe="/%")
    encoded_query = parsed.query  # query is already encoded or safe
    full_url = urlunparse((parsed.scheme, parsed.netloc, encoded_path, parsed.params, encoded_query, parsed.fragment))

    result = {
        "id": source_id,
        "name": source_name,
        "url": full_url,
        "ok": False,
        "status_code": 0,
        "item_count": 0,
        "items_with_title": 0,
        "items_with_url": 0,
        "items_with_pubdate": 0,
        "latest_pubdate": None,
        "old_item_ratio": 1.0,
        "unknown_time_ratio": 1.0,
        "latency_ms": 0,
        "grade": "D",
        "error": None,
    }

    try:
        import urllib.request
        import urllib.error

        start = time.time()
        req = urllib.request.Request(full_url, headers={"User-Agent": "QwenPaw-RSSHub-HealthCheck/1.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        latency_ms = round((time.time() - start) * 1000)

        result["status_code"] = resp.status
        result["latency_ms"] = latency_ms

        if resp.status != 200:
            result["error"] = f"HTTP {resp.status}"
            return result

        xml_text = resp.read().decode("utf-8", errors="replace")

        item_count, with_title, with_url, with_pub, latest, pubdates = parse_rss_items(xml_text)

        old_ratio, unk_ratio = compute_ratios(pubdates, window_start, window_end)

        result["ok"] = True
        result["item_count"] = item_count
        result["items_with_title"] = with_title
        result["items_with_url"] = with_url
        result["items_with_pubdate"] = with_pub
        result["latest_pubdate"] = latest
        result["old_item_ratio"] = round(old_ratio, 3)
        result["unknown_time_ratio"] = round(unk_ratio, 3)
        result["grade"] = classify_grade(True, item_count, with_url, old_ratio)

    except urllib.error.URLError as e:
        result["error"] = f"连接失败: {e.reason}"
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


def run_health_check(
    project_root: str,
    date: str,
    sources_path: str = "config/sources.yaml",
) -> dict:
    """运行全部路由健康检查。"""
    config = load_yaml(os.path.join(project_root, sources_path))
    rsshub_base = config.get("rsshub_base", "http://192.168.2.100:1200")
    defaults = config.get("defaults", {})
    timeout = defaults.get("timeout_seconds", 20)
    rsshub_sources = config.get("rsshub_sources", [])

    # 时间窗口
    try:
        report_date = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=CST)
    except ValueError:
        report_date = datetime.now(CST)

    window_start = report_date - timedelta(days=1)
    window_start = window_start.replace(hour=7, minute=0, second=0, microsecond=0)
    window_end = report_date.replace(hour=7, minute=0, second=0, microsecond=0)

    log.info(f"健康检查: {len(rsshub_sources)} 条路由, 窗口: {window_start.isoformat()} ~ {window_end.isoformat()}")

    results = []
    for i, src in enumerate(rsshub_sources):
        if not src.get("enabled", True):
            continue
        log.info(f"[{i+1}/{len(rsshub_sources)}] 检查: {src.get('name', src.get('id', '?'))}")
        r = check_single_route(src, rsshub_base, window_start, window_end, timeout)
        results.append(r)
        # 友好间隔
        time.sleep(0.3)

    # 统计
    grades = {"A": 0, "B": 0, "C": 0, "D": 0}
    for r in results:
        grades[r["grade"]] += 1

    top10 = sorted(
        [r for r in results if r["ok"] and r["items_with_pubdate"] > 0],
        key=lambda r: r["latest_pubdate"] or "",
        reverse=True,
    )[:10]

    failed = [r for r in results if r["grade"] == "D"]
    suggest_disable = [r for r in results if r["grade"] in ("D",)]
    # C类搜索源：旧文比高，建议降级
    suggest_keep = [r for r in results if r["grade"] in ("A", "B")]

    summary = {
        "date": date,
        "rsshub_base": rsshub_base,
        "checked_at": datetime.now(CST).isoformat(),
        "total_routes": len(rsshub_sources),
        "checked": len(results),
        "grades": grades,
        "top10_latest": [
            {
                "id": r["id"],
                "name": r["name"],
                "latest_pubdate": r["latest_pubdate"],
                "item_count": r["item_count"],
                "old_item_ratio": r["old_item_ratio"],
                "grade": r["grade"],
            }
            for r in top10
        ],
        "failed_routes": [{"id": r["id"], "name": r["name"], "error": r["error"]} for r in failed],
        "suggest_disable": [r["id"] for r in suggest_disable],
        "suggest_keep": [r["id"] for r in suggest_keep],
        "routes": results,
    }

    return summary


def save_results(summary: dict, project_root: str):
    """保存 JSON 和 Markdown 结果。"""
    date = summary["date"]
    out_dir = Path(project_root) / "data" / "logs" / "rsshub_health"
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    json_path = out_dir / f"{date}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"JSON: {json_path}")

    # Markdown
    md_path = out_dir / f"{date}.md"
    grades = summary["grades"]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# RSSHub 路由健康报告 | {date}\n\n")
        f.write(f"**检查时间**: {summary['checked_at']}\n")
        f.write(f"**RSSHub 地址**: {summary['rsshub_base']}\n")
        f.write(f"**检查路由数**: {summary['checked']}/{summary['total_routes']}\n\n")

        f.write("## 分级统计\n\n")
        f.write(f"| 等级 | 数量 | 说明 |\n")
        f.write(f"|------|------|------|\n")
        f.write(f"| A | {grades['A']} | 稳定可用 |\n")
        f.write(f"| B | {grades['B']} | 可用但产出少 |\n")
        f.write(f"| C | {grades['C']} | 旧文比例高，仅参考 |\n")
        f.write(f"| D | {grades['D']} | 失败/超时 |\n\n")

        f.write("## 近24h有效条目 Top 10\n\n")
        f.write("| 源 | 最新时间 | 条目数 | 旧文比 | 等级 |\n")
        f.write("|----|----------|--------|--------|------|\n")
        for r in summary["top10_latest"]:
            f.write(f"| {r['name']} | {r['latest_pubdate'] or 'N/A'} | {r['item_count']} | {r['old_item_ratio']} | {r['grade']} |\n")
        f.write("\n")

        if summary["failed_routes"]:
            f.write("## 失败路由\n\n")
            for r in summary["failed_routes"]:
                f.write(f"- **{r['name']}** ({r['id']}): {r['error']}\n")
            f.write("\n")

        if summary["suggest_disable"]:
            f.write("## 建议禁用的源\n\n")
            for sid in summary["suggest_disable"]:
                f.write(f"- `{sid}`\n")
            f.write("\n")

        f.write("## 全路由详情\n\n")
        f.write("| id | 名称 | 条目 | 有标题 | 有URL | 有日期 | 旧文比 | 延迟ms | 等级 |\n")
        f.write("|----|------|------|--------|-------|--------|--------|--------|------|\n")
        for r in summary["routes"]:
            f.write(
                f"| {r['id']} | {r['name']} | {r['item_count']} | "
                f"{r['items_with_title']} | {r['items_with_url']} | "
                f"{r['items_with_pubdate']} | {r['old_item_ratio']} | "
                f"{r['latency_ms']} | **{r['grade']}** |\n"
            )

    log.info(f"Markdown: {md_path}")


def main():
    parser = argparse.ArgumentParser(description="RSSHub 路由健康检查")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", required=True, help="报告日期 YYYY-MM-DD")
    parser.add_argument("--sources", default="config/sources.yaml", help="sources.yaml 路径")
    args = parser.parse_args()

    if not os.path.isdir(args.project_root):
        log.error(f"项目目录不存在: {args.project_root}")
        sys.exit(1)

    summary = run_health_check(args.project_root, args.date, args.sources)
    save_results(summary, args.project_root)

    # 控制台摘要
    grades = summary["grades"]
    print(f"\n{'='*60}")
    print(f"RSSHub 健康检查完成 | {args.date}")
    print(f"{'='*60}")
    print(f"  A 类（稳定）: {grades['A']}")
    print(f"  B 类（产出少）: {grades['B']}")
    print(f"  C 类（旧文多）: {grades['C']}")
    print(f"  D 类（失败）: {grades['D']}")
    print(f"  失败路由: {len(summary['failed_routes'])}")
    if summary["failed_routes"]:
        for r in summary["failed_routes"]:
            print(f"    - {r['name']}: {r['error']}")
    print(f"  近24h Top 3:")
    for r in summary["top10_latest"][:3]:
        print(f"    {r['name']}: {r['item_count']}条, 最新={r['latest_pubdate']}, 等级={r['grade']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
