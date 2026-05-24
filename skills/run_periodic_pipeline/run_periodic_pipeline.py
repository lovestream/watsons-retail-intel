#!/usr/bin/env python3
"""run_periodic_pipeline — 周报/月报/年报调度器。

每天 09:00 CST 由 cron 触发，内部判断今天需要生成什么：
- 周一 → 生成上周周报
- 每月1日 → 生成上月月报
- 1月1日 → 生成上年年报

同日冲突时按 周报→月报→年报 顺序串行执行。
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, date

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from skills.generate_periodic_report.generate_periodic_report import (
    generate_periodic_report,
)

logger = logging.getLogger(__name__)
LOG_PREFIX = "[PeriodicPipeline]"


def get_last_week_range(today: date) -> dict:
    """获取上周一到周日的日期范围。"""
    # today 是周一，上周一 = today - 7
    last_monday = today - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    return {
        "start": last_monday.strftime("%Y-%m-%d"),
        "end": last_sunday.strftime("%Y-%m-%d"),
    }


def get_last_month(today: date) -> dict:
    """获取上个月的年月。"""
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    return {
        "year": last_month_end.strftime("%Y"),
        "month": last_month_end.strftime("%m"),
    }


def get_last_year(today: date) -> dict:
    """获取上一年。"""
    return {"year": str(today.year - 1)}


def run_periodic_pipeline(
    project_root: str = ".",
    today_str: str = "",
    send_email: bool = True,
    recipient: str = "123399974@qq.com",
) -> dict:
    """判断今天需要生成什么报告，并执行。

    Returns:
        {"tasks_run": [...], "results": {...}}
    """
    if today_str:
        today = datetime.strptime(today_str, "%Y-%m-%d").date()
    else:
        # 使用 CST 时间
        from datetime import timezone
        cst = timezone(timedelta(hours=8))
        today = datetime.now(cst).date()

    logger.info(f"{LOG_PREFIX} 今日: {today} (weekday={today.weekday()}, "
                f"day={today.day}, month={today.month})")

    tasks = []

    # 周一 → 生成上周周报
    if today.weekday() == 0:
        period = get_last_week_range(today)
        tasks.append(("weekly", period))
        logger.info(f"{LOG_PREFIX} 📋 需要生成周报: {period['start']} ~ "
                    f"{period['end']}")

    # 每月1日 → 生成上月月报
    if today.day == 1:
        period = get_last_month(today)
        tasks.append(("monthly", period))
        logger.info(f"{LOG_PREFIX} 📋 需要生成月报: "
                    f"{period['year']}-{period['month']}")

    # 1月1日 → 生成上年年报
    if today.month == 1 and today.day == 1:
        period = get_last_year(today)
        tasks.append(("yearly", period))
        logger.info(f"{LOG_PREFIX} 📋 需要生成年报: {period['year']}")

    if not tasks:
        logger.info(f"{LOG_PREFIX} 今天无需生成周期报告")
        return {"tasks_run": [], "results": {}}

    # 串行执行: 周报 → 月报 → 年报
    results = {}
    for report_type, period_info in tasks:
        logger.info(f"{LOG_PREFIX} ═══ 开始: {report_type} ═══")
        try:
            result = generate_periodic_report(
                report_type=report_type,
                period_info=period_info,
                project_root=project_root,
                send_email=send_email,
                recipient=recipient,
            )
            results[report_type] = result
            if result.get("success"):
                logger.info(f"{LOG_PREFIX} ✅ {report_type} 完成")
            else:
                logger.error(f"{LOG_PREFIX} ❌ {report_type} 失败: "
                             f"{result.get('error', '')}")
        except Exception as e:
            logger.error(f"{LOG_PREFIX} ❌ {report_type} 异常: {e}")
            results[report_type] = {"success": False, "error": str(e)}

    return {
        "tasks_run": [t[0] for t in tasks],
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(
        description="周期报告调度器")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", default="",
                        help="指定日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--send-email", default="true")
    parser.add_argument("--recipient", default="123399974@qq.com")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    result = run_periodic_pipeline(
        project_root=args.project_root,
        today_str=args.date,
        send_email=args.send_email.lower() == "true",
        recipient=args.recipient,
    )

    tasks = result.get("tasks_run", [])
    if not tasks:
        print("ℹ️ 今天无需生成周期报告")
    else:
        for t in tasks:
            r = result["results"].get(t, {})
            status = "✅" if r.get("success") else "❌"
            print(f"{status} {t}: {r.get('report_path', r.get('error', ''))}")


if __name__ == "__main__":
    main()

