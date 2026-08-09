#!/usr/bin/env python3
"""run_periodic_pipeline — 周报/月报/年报调度器（三批整改后）。

每天 09:00 CST 由 cron 触发，内部判断今天需要生成什么：
- 周一 → 生成上周周报
- 每月1日 → 生成上月月报
- 1月1日 → 生成上年年报

同日冲突时按 周报→月报→年报 顺序串行执行。

三批整改新增：
- 周期级 lock（data/locks/periodic.lock，防重入）
- 每周期 run manifest（data/runs/periodic/{type}/{period_id}/manifest.json）
- 聚合层（periodic_synthesis）优先；失败回退历史报告
- 每类报告总运行超时
"""

import argparse
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, date

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

# ── 自愈：优先从项目持久 .venv_packages 加载包（如 edge-tts） ──
_VENV_PACKAGES = os.path.join(_PROJECT_ROOT, ".venv_packages")
if os.path.isdir(_VENV_PACKAGES) and _VENV_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_PACKAGES)

from skills.generate_periodic_report.generate_periodic_report import (
    generate_periodic_report,
)

logger = logging.getLogger(__name__)
LOG_PREFIX = "[PeriodicPipeline]"

# 每类报告总运行超时（秒）（三批整改 §35）
_TOTAL_TIMEOUTS = {"weekly": 1200, "monthly": 1800, "yearly": 2700}


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


def _period_id(report_type: str, period_info: dict) -> str:
    """周期 ID（与聚合层一致）。"""
    if report_type == "weekly":
        s = datetime.strptime(period_info["start"], "%Y-%m-%d")
        iso = s.isocalendar()
        return "%d-W%02d" % (iso[0], iso[1])
    if report_type == "monthly":
        return "%s-%s" % (period_info["year"], period_info["month"])
    if report_type == "yearly":
        return period_info["year"]
    return "unknown"


def _write_manifest(report_type: str, period_info: dict, project_root: str,
                    manifest: dict):
    """写周期 run manifest（三批整改 §33）。"""
    try:
        pid = _period_id(report_type, period_info)
        mdir = os.path.join(project_root, "data", "runs", "periodic",
                            report_type, pid)
        os.makedirs(mdir, exist_ok=True)
        path = os.path.join(mdir, "manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        logger.info("%s manifest → %s", LOG_PREFIX, path)
    except Exception as e:
        logger.error("%s manifest 写入失败: %s", LOG_PREFIX, e)


def _acquire_lock(project_root: str) -> bool:
    """获取周期级 lock（三批整改 §32）。非阻塞：已存在则跳过本次。"""
    lock_dir = os.path.join(project_root, "data", "locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, "periodic.lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 记录 PID 便于排查
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            logger.info("%s 已获取 lock: %s (pid=%d)", LOG_PREFIX, lock_path,
                        os.getpid())
            return True
        except OSError:
            logger.warning("%s lock 被占用，本次跳过（防重入）", LOG_PREFIX)
            os.close(fd)
            return False
    except Exception as e:
        logger.error("%s lock 获取异常: %s", LOG_PREFIX, e)
        # 极端情况：lock 失败不阻塞主流程（宁可执行也不静默跳过）
        return True


def _release_lock(fd):
    try:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    except Exception:
        pass


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

    # lock（防重入）
    lock_fd = None
    lock_dir = os.path.join(project_root, "data", "locks")
    if _acquire_lock(project_root):
        # 保持 fd 直到完成
        try:
            lock_fd = os.open(os.path.join(lock_dir, "periodic.lock"),
                              os.O_RDWR)
        except Exception:
            lock_fd = None
    else:
        return {"tasks_run": [t[0] for t in tasks], "locked": True,
                "results": {}}

    # 串行执行: 周报 → 月报 → 年报
    results = {}
    try:
        for report_type, period_info in tasks:
            logger.info(f"{LOG_PREFIX} ═══ 开始: {report_type} ═══")
            started = time.time()
            task_manifest = {
                "report_type": report_type,
                "period": period_info,
                "started_at": datetime.now().isoformat(),
                "status": "running",
            }
            _write_manifest(report_type, period_info, project_root,
                            task_manifest)
            try:
                result = generate_periodic_report(
                    report_type=report_type,
                    period_info=period_info,
                    project_root=project_root,
                    send_email=send_email,
                    recipient=recipient,
                )
                elapsed = time.time() - started
                results[report_type] = result
                task_manifest.update({
                    "status": "success" if result.get("success") else "failed",
                    "sendable": result.get("sendable", False),
                    "report_path": result.get("report_path", ""),
                    "audio_path": result.get("audio_path", ""),
                    "error": result.get("error", ""),
                    "elapsed_seconds": round(elapsed, 1),
                    "coverage": result.get("coverage"),
                    "finished_at": datetime.now().isoformat(),
                })
                if result.get("success"):
                    logger.info(f"{LOG_PREFIX} ✅ {report_type} 完成 "
                                f"({elapsed:.0f}s)")
                else:
                    logger.error(f"{LOG_PREFIX} ❌ {report_type} 失败: "
                                 f"{result.get('error', '')}")
            except Exception as e:
                elapsed = time.time() - started
                logger.error(f"{LOG_PREFIX} ❌ {report_type} 异常: {e}")
                results[report_type] = {"success": False, "error": str(e)}
                task_manifest.update({
                    "status": "error",
                    "error": str(e),
                    "elapsed_seconds": round(elapsed, 1),
                    "finished_at": datetime.now().isoformat(),
                })
            _write_manifest(report_type, period_info, project_root,
                            task_manifest)
    finally:
        _release_lock(lock_fd)

    return {
        "tasks_run": [t[0] for t in tasks],
        "results": results,
        "locked": False,
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
    if result.get("locked"):
        print("🔒 周期任务已在运行，本次跳过")
    elif not tasks:
        print("ℹ️ 今天无需生成周期报告")
    else:
        for t in tasks:
            r = result["results"].get(t, {})
            status = "✅" if r.get("success") else "❌"
            print(f"{status} {t}: {r.get('report_path', r.get('error', ''))}")


if __name__ == "__main__":
    main()
