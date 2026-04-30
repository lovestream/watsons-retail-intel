#!/usr/bin/env python3
"""generate_no_signal_report — 当所有采集方式都无法获得高质量信号时，
输出坦诚的"无新增信号"日报。

触发条件：cleaned_count == 0 after RSSHub + Tavily gap search
这不是失败，而是诚实输出。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 时区 ──
try:
    from zoneinfo import ZoneInfo
    CST = ZoneInfo("Asia/Shanghai")
except ImportError:
    from dateutil.tz import gettz
    CST = gettz("Asia/Shanghai")

LOG_PREFIX = "[NoSignal]"


def generate_no_signal_report(
    project_root: str,
    date: Optional[str] = None,
) -> dict:
    """生成无信号日报。

    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD（默认今天）

    Returns:
        结果 dict，包含 ok, output_file, log_file 等
    """
    # ── 日期 ──
    if not date:
        date = datetime.now(CST).strftime("%Y-%m-%d")

    root = Path(project_root).resolve()

    # ── 目录 ──
    log_dir = root / "data" / "logs" / date
    report_dir = root / "data" / "reports" / date
    log_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "generate_no_signal_report.log"

    def log(msg: str):
        ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {LOG_PREFIX} {msg}"
        print(line)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("=" * 60)
    log(f"开始生成无信号日报: date={date}")
    log("=" * 60)

    # ── 收集统计 ──
    raw_file = root / "data" / "raw" / date / "raw_articles.json"
    merged_file = root / "data" / "raw" / date / "raw_articles_merged.json"
    cleaned_file = root / "data" / "cleaned" / date / "cleaned_articles.json"
    reference_file = root / "data" / "cleaned" / date / "reference_articles.json"
    rejected_file = root / "data" / "rejected" / date / "rejected_articles.json"
    gap_file = root / "data" / "raw" / date / "tavily_gap_articles.json"
    health_file = root / "data" / "logs" / date / "source_health_report.json"

    # ── 读取统计 ──
    raw_count = 0
    gap_count = 0
    reference_count = 0
    rejected_count = 0
    by_source = {}
    by_collector = {}
    gap_query_count = 0
    health_summary = {}
    filter_stats = {}

    # 原始文章数
    if merged_file.exists():
        data = json.loads(merged_file.read_text(encoding="utf-8"))
        raw_count = len(data.get("articles", []))
    elif raw_file.exists():
        data = json.loads(raw_file.read_text(encoding="utf-8"))
        raw_count = len(data.get("articles", []))

    # 参考文章数
    if reference_file.exists():
        data = json.loads(reference_file.read_text(encoding="utf-8"))
        reference_count = len(data.get("articles", []))

    # 拒绝文章数
    if rejected_file.exists():
        data = json.loads(rejected_file.read_text(encoding="utf-8"))
        rejected_count = len(data) if isinstance(data, list) else len(data.get("articles", []))

    # 按来源统计
    for f_path in [raw_file, merged_file]:
        if f_path.exists():
            data = json.loads(f_path.read_text(encoding="utf-8"))
            for a in data.get("articles", []):
                src = a.get("source_name", "unknown")
                clt = a.get("collector", "unknown")
                by_source[src] = by_source.get(src, 0) + 1
                by_collector[clt] = by_collector.get(clt, 0) + 1
            break  # 只读一个

    # Tavily gap search 统计
    if gap_file.exists():
        data = json.loads(gap_file.read_text(encoding="utf-8"))
        gap_count = len(data.get("articles", []))
        gap_query_count = data.get("metadata", {}).get("query_count", 0)

    # Source health 统计
    if health_file.exists():
        try:
            health = json.loads(health_file.read_text(encoding="utf-8"))
            summary = health.get("summary", {})
            for grade, info in summary.items():
                health_summary[grade] = info.get("count", 0) if isinstance(info, dict) else info
        except Exception:
            pass

    # Filter 统计
    filter_log = root / "data" / "logs" / date / "filter.log"
    run_manifest = root / "data" / "runs" / date / "run_manifest.json"
    if run_manifest.exists():
        try:
            manifest = json.loads(run_manifest.read_text(encoding="utf-8"))
            for step in manifest.get("steps", []):
                if step.get("name") == "filter":
                    filter_stats = step.get("stats", {})
                    break
        except Exception:
            pass

    # ── 确定主要原因 ──
    main_reason = ""
    if raw_count == 0:
        main_reason = "所有采集源（RSSHub/Tavily/Web）均未返回文章，可能是网络故障或采集配置问题"
    elif reference_count > 0 and rejected_count > 0:
        main_reason = f"采集到 {raw_count} 篇文章，但无一篇通过关键词+时间窗口+相关性筛选；{reference_count} 篇为参考级"
    elif reference_count == 0 and rejected_count > 0:
        main_reason = f"采集到 {raw_count} 篇文章，全部被拒绝（时间过旧或主题不相关）"
    else:
        main_reason = f"采集到 {raw_count} 篇文章，但无高质量新增信号"

    # ── 生成日报 ──
    weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    date_obj = datetime.strptime(date, "%Y-%m-%d")
    weekday = weekday_names[date_obj.weekday()]

    # 构建信息质量说明
    collector_lines = []
    for clt, cnt in sorted(by_collector.items(), key=lambda x: -x[1]):
        collector_lines.append(f"  - {clt}: {cnt} 篇")

    source_lines = []
    top_sources = sorted(by_source.items(), key=lambda x: -x[1])[:10]
    for src, cnt in top_sources:
        source_lines.append(f"  - {src}: {cnt} 篇")

    health_lines = []
    for grade, cnt in sorted(health_summary.items()):
        label = {"A": "主选", "B": "备选", "C": "参考", "D": "停用"}.get(grade, grade)
        health_lines.append(f"  - {grade}级（{label}）: {cnt} 个源")

    # 时间窗口统计
    time_status_lines = []
    filter_by_time = filter_stats.get("by_time_status", {})
    if filter_by_time:
        for ts, cnt in sorted(filter_by_time.items()):
            time_status_lines.append(f"  - {ts}: {cnt} 篇")

    # 关键词统计
    keyword_lines = []
    filter_keywords = filter_stats.get("top_matched_keywords", {})
    if filter_keywords:
        top_kw = sorted(filter_keywords.items(), key=lambda x: -x[1])[:10]
        for kw, cnt in top_kw:
            keyword_lines.append(f"  - {kw}: {cnt} 次")

    report = f"""# 即时零售 × 个护美妆经营日报｜{date}（{weekday}）

## 01 今日一句话判断

今日公开信息中未发现足够高置信的即时零售/个护美妆新增信号，暂不建议基于外部资讯调整策略。

**注意**：无信号日报 ≠ 系统故障。当天确实没有足够新增信号是正常现象。

## 02 今日信息质量说明

- RSSHub/RSS/Web 采集数量：**{raw_count}** 篇
- Tavily 补搜数量：**{gap_count}** 篇（{gap_query_count} 条查询）
- 有效信号数量：**0**
- 参考级文章：{reference_count} 篇
- 拒绝文章：{rejected_count} 篇
- 主要原因：{main_reason}

### 采集来源分布
{chr(10).join(collector_lines) if collector_lines else "  - 无采集数据"}

### 时间窗口分布
{chr(10).join(time_status_lines) if time_status_lines else "  - 无时间分布数据"}

### 关键词命中率
{chr(10).join(keyword_lines) if keyword_lines else "  - 无关键词数据"}

### 来源健康度
{chr(10).join(health_lines) if health_lines else "  - 源健康报告未生成"}

## 03 平台变化解读

今日未发现足够高质量新增信号。

以下平台暂无新动态可供分析：
- 美团闪购
- 京东到家 / 京东秒送
- 淘宝闪购 / 饿了么
- 抖音小时达

今日无需基于外部资讯做出平台策略调整。

## 04 竞对与品牌动作

今日未发现高置信竞对新增动作。

可能的原因：
- 竞对当日无重大官宣或活动上线
- 行业媒体当日未报道相关动态
- 相关新闻发生在采集窗口之外

## 05 对屈臣氏的经营提示

今日无新增外部信号。建议：

1. **转向内部经营数据复盘** — 今日更适合关注内部指标（库存周转、门店动销、滞销预警）而非依赖外部资讯判断
2. **复核各平台经营指标** — 关注美团闪购/京东到家等核心平台的日常运营数据
3. **检查昨日追踪项执行情况** — 确认此前跟进事项的落地状态
4. **关注天气/节假日等影响因子** — 这些是比外部资讯更可靠的短期经营变量

## 06 今日唯一建议动作

复核昨日追踪项和各平台核心经营指标。

## 07 明日追踪清单

- [ ] 美团闪购平台资源位变化（首页Banner、品类坑位）
- [ ] 京东到家/京东秒送美妆个护活动上新
- [ ] 淘宝闪购与天猫货盘协同动态
- [ ] 抖音小时达美妆日百新案例
- [ ] 屈臣氏竞对（丝芙兰、调色师、WOW COLOUR、名创优品）即时零售渠道动态
- [ ] 行业政策与监管信息更新
- [ ] 防晒/美白/洗护等应季品类即时零售趋势

---

> 📋 本日报由「即时零售 × 个护美妆经营情报系统」自动生成  
> 📅 {datetime.now(CST).strftime('%Y-%m-%d %H:%M:%S')} Asia/Shanghai  
> 🏷️ 类型：无新增信号日报
"""

    # ── 保存日报 ──
    output_file = report_dir / f"daily_report_{date}_no_signal.md"
    output_file.write_text(report, encoding="utf-8")
    log(f"无信号日报已保存: {output_file}")

    # ── 同时写入 final_report 以兼容 pipeline ──
    final_file = report_dir / f"final_report_{date}.md"
    final_file.write_text(report, encoding="utf-8")
    log(f"最终日报已保存: {final_file}")

    # ── 返回结果 ──
    result = {
        "ok": True,
        "date": date,
        "report_type": "no_signal",
        "output_file": str(output_file),
        "final_file": str(final_file),
        "log_file": str(log_file),
        "raw_count": raw_count,
        "gap_count": gap_count,
        "gap_query_count": gap_query_count,
        "reference_count": reference_count,
        "rejected_count": rejected_count,
        "main_reason": main_reason,
        "by_collector": by_collector,
        "by_source_top": dict(top_sources),
        "health_summary": health_summary,
        "filter_stats": filter_stats,
        "sendable": True,
        "errors": [],
    }

    # ── 保存 JSON 结果 ──
    json_file = log_dir / "generate_no_signal_report.json"
    json_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"结果 JSON 已保存: {json_file}")

    log("=" * 60)
    log(f"无信号日报生成完成")
    log(f"  报告类型: no_signal")
    log(f"  原始文章: {raw_count}")
    log(f"  Tavily 补搜: {gap_count} 篇（{gap_query_count} 条查询）")
    log(f"  有效信号: 0")
    log(f"  参考: {reference_count}, 拒绝: {rejected_count}")
    log(f"  可发送: True")
    log("=" * 60)

    return result


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(
        description="生成无信号日报（当所有采集方式都无法获得高质量信号时）",
    )
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", default=None,
                        help="日期 YYYY-MM-DD（默认今天）")

    args = parser.parse_args()

    result = generate_no_signal_report(
        project_root=args.project_root,
        date=args.date,
    )

    # 输出 JSON 到 stdout
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()