#!/usr/bin/env python3
"""
pipeline_alert.py — Pipeline 执行后健康检查 + 告警

在 daily pipeline 结束后运行:
1. 读取 run_manifest.json，收集所有失败的步骤
2. 运行 health_check 检查服务状态
3. 如果有告警，发送邮件给管理员

用法:
    python3 -m skills.health_check.pipeline_alert \\
        --project-root . \\
        --date 2026-05-05 \\
        --dry-run
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
ADMIN_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "51111211004@stu.ecnu.edu.cn")


def collect_pipeline_errors(project_root: str, date: str) -> tuple[list[dict], list[dict]]:
    """从 run_manifest.json 收集失败步骤。

    Returns:
        (critical_errors, warnings)
        - critical_errors: 真失败（status=failed/error）
        - warnings: 跳过但有警告（status=skipped with warnings）
    """
    manifest_path = Path(project_root) / "data" / "runs" / date / "run_manifest.json"
    if not manifest_path.exists():
        return ([{"step": "pipeline", "error": f"run_manifest not found: {manifest_path}"}], [])

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return ([{"step": "pipeline", "error": f"Failed to parse manifest: {e}"}], [])

    critical = []
    warnings = []
    
    # 这些步骤的警告是观测性/诊断性的，不应触发告警
    OBSERVATION_STEPS = {"quality_funnel_report", "source_health_report"}
    
    # 补充性采集步骤：失败不影响播客/日报生成，降级为 warning
    # 用户要求：只有真正阻塞播客生成的问题才发邮件
    SUPPLEMENTARY_STEPS = {
        "broad_search_discovery",       # XCrawl/Tavily 搜索（补充源）
        "xcrawl_enrich_articles",       # XCrawl 正文抓取（有 CloakBrowser 兜底）
        "cloakbrowser_enrich",          # CloakBrowser 增强（补充）
        "cloakbrowser_collect",         # CloakBrowser 采集（补充源）
        "cloakbrowser_content_fetch",   # CloakBrowser 正文（补充）
        "cloakbrowser_date_verify",     # 日期复核（质量优化，非必须）
        "source_url_monitor",           # URL 监控（补充源）
        "tophub_collect",               # TopHub 采集（补充源）
        "generate_podcast",             # 播客生成（失败仅缺 MP3，不影响日报）
        "podcast_review",               # 播客审稿（非阻塞）
    }

    for step in manifest.get("steps", []):
        status = step.get("status", "")
        step_name = step.get("name", "unknown")
        dur = step.get("duration_seconds", 0)

        if status in ("failed", "error"):
            err_msg = "; ".join(str(e) for e in step.get("errors", [])) or "unknown failure"
            # 观测性步骤失败不告警
            if step_name in OBSERVATION_STEPS:
                warnings.append({"step": step_name, "duration_s": dur, "error": err_msg})
            # 补充性步骤失败降级为 warning（不触发邮件）
            elif step_name in SUPPLEMENTARY_STEPS:
                warnings.append({"step": step_name, "duration_s": dur, "error": err_msg})
            else:
                critical.append({"step": step_name, "duration_s": dur, "error": err_msg})
        elif status == "skipped" and step.get("warnings"):
            # 跳过观测性步骤的警告
            if step_name in OBSERVATION_STEPS:
                continue
            for w in step.get("warnings", []):
                warnings.append({"step": step_name, "duration_s": 0, "error": str(w)})

    # Pipeline-level errors
    for e in manifest.get("errors", []):
        critical.append({"step": "pipeline", "duration_s": 0, "error": str(e)})

    return critical, warnings


def run_health_check_json(project_root: str) -> dict:
    """运行 health_check 并返回 JSON 结果。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "skills.health_check.health_check",
             "--project-root", project_root, "--json", "--quiet"],
            capture_output=True, text=True, timeout=120,
            cwd=project_root,
        )
        if result.returncode != 0 and result.stdout:
            return json.loads(result.stdout)
        if result.stdout:
            return json.loads(result.stdout)
        return {"overall": "unknown", "error": result.stderr[:500]}
    except Exception as e:
        return {"overall": "unknown", "error": str(e)[:200]}


def format_pipeline_alert_body(health_result: dict, critical_errors: list[dict],
                               warnings: list[dict], date: str, project_root: str) -> str:
    """生成 Pipeline 告警邮件正文。"""
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST")
    overall = health_result.get("overall", "unknown")
    level = overall.upper()

    lines = [
        f"Watsons Retail Intel — Pipeline Alert",
        f"=" * 60,
        f"日期：{date}",
        f"时间：{now}",
        f"整体状态：{level}",
        f"",
    ]

    # 服务健康
    lines.append(f"## 服务健康状态")
    for svc_id, svc in health_result.get("services", {}).items():
        emoji = {"healthy": "OK", "degraded": "WARN", "down": "DOWN", "unknown": "??"}.get(svc["status"], "??")
        lines.append(f"  [{emoji}] {svc['name']}: {svc['status']} — {svc['detail']}")
        for fk in svc.get("failed_keys", []):
            lines.append(f"       ❌ {fk['env_name']}: {fk['error']}")
    lines.append("")

    # Pipeline 错误
    if critical_errors:
        lines.append(f"## Pipeline 失败步骤 ({len(critical_errors)})")
        for i, err in enumerate(critical_errors, 1):
            step = err.get("step", "?")
            dur = err.get("duration_s", 0)
            msg = err.get("error", "")
            dur_str = f" ({dur:.0f}s)" if dur else ""
            lines.append(f"  {i}. ❌ {step}{dur_str}")
            lines.append(f"     原因：{msg}")
        lines.append("")

    if warnings:
        lines.append(f"## 非阻塞警告 ({len(warnings)})")
        for i, w in enumerate(warnings[:10], 1):
            lines.append(f"  {i}. ⚠️ {w.get('step','?')}: {w.get('error','?')[:100]}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"请登录检查: {project_root}")
    if critical_errors:
        lines.append(f"详细日志: data/logs/{date}/")

    return "\n".join(lines)


def send_alert(project_root: str, subject: str, body: str, dry_run: bool = False) -> bool:
    """通过 send_admin_alert 模块发送告警。"""
    try:
        cmd = [
            sys.executable, "-m", "skills.send_admin_alert.send_admin_alert",
            "--project-root", project_root,
            "--subject", subject,
            "--body", body,
            "--force",
        ]
        if dry_run:
            cmd.append("--dry-run")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=project_root)
        if result.returncode == 0:
            return True
        print(f"Alert send failed: {result.stderr[:500]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Alert send exception: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Pipeline health check and alert")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="不实际发送告警邮件")
    parser.add_argument("--force", action="store_true", help="即使健康也发送")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("pipeline_alert")

    project_root = args.project_root
    date = args.date

    # 1. 收集 pipeline 错误
    logger.info(f"Collecting pipeline errors for {date}...")
    critical_errors, warn_list = collect_pipeline_errors(project_root, date)
    if critical_errors:
        logger.warning(f"Found {len(critical_errors)} critical pipeline errors:")
        for err in critical_errors:
            logger.warning(f"  - {err['step']}: {err['error'][:100]}")
    if warn_list:
        logger.info(f"Found {len(warn_list)} non-critical warnings")

    # 2. 运行健康检查
    logger.info("Running health check...")
    health_result = run_health_check_json(project_root)

    # 3. 判断是否需要告警
    overall = health_result.get("overall", "healthy")
    needs_alert = (
        overall == "critical"
        or len(critical_errors) > 0
        or args.force
    )

    if not needs_alert:
        logger.info(f"Health={overall}, critical_errors={len(critical_errors)}. No alert needed.")
        print(json.dumps({
            "ok": True,
            "alerted": False,
            "health": overall,
            "critical_errors": len(critical_errors),
            "warnings": len(warn_list),
        }))
        return

    # 4. 生成告警邮件（仅 critical 或真失败）
    logger.info(f"Generating alert: health={overall}, critical={len(critical_errors)}, warnings={len(warn_list)}")

    # 主题
    if overall == "critical" or critical_errors:
        prefix = "[CRITICAL]"
    else:
        prefix = "[INFO]"

    subject = f"{prefix} Watsons Intel Alert — {date}"

    body = format_pipeline_alert_body(health_result, critical_errors, warn_list, date, project_root)

    # 5. 发送
    success = send_alert(project_root, subject, body, dry_run=args.dry_run)

    # 6. 保存告警记录
    alert_dir = Path(project_root) / "data" / "logs" / date
    alert_dir.mkdir(parents=True, exist_ok=True)
    alert_file = alert_dir / "pipeline_alert.json"
    alert_data = {
        "timestamp": datetime.now(CST).isoformat(),
        "date": date,
        "health": health_result,
        "critical_errors": critical_errors,
        "warnings": warn_list,
        "alerted": success,
        "dry_run": args.dry_run,
    }
    alert_file.write_text(json.dumps(alert_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Alert saved to {alert_file}")

    result = {
        "ok": True,
        "alerted": success,
        "health": overall,
        "critical_errors": len(critical_errors),
        "warnings": len(warn_list),
    }
    print(json.dumps(result))
    sys.exit(0 if success else 1)


def pipeline_alert_check(project_root: str, date: str, dry_run: bool = False, force: bool = False, **kwargs) -> dict:
    """供 import 调用的 pipeline alert 入口（与 main() 逻辑相同）。"""
    logger = logging.getLogger("pipeline_alert")

    # 1. 收集 pipeline 错误
    logger.info(f"Collecting pipeline errors for {date}...")
    critical_errors, warn_list = collect_pipeline_errors(project_root, date)
    if critical_errors:
        logger.warning(f"Found {len(critical_errors)} critical pipeline errors")
    if warn_list:
        logger.info(f"Found {len(warn_list)} non-critical warnings")

    # 2. 运行健康检查
    logger.info("Running health check...")
    health_result = run_health_check_json(project_root)

    # 3. 判断是否需要告警（仅在 critical 或真失败时发送，degraded/warning 不发邮件）
    overall = health_result.get("overall", "healthy")
    needs_alert = (
        overall == "critical"
        or len(critical_errors) > 0
        or force
    )

    if not needs_alert:
        logger.info(f"Health={overall}, critical_errors={len(critical_errors)}. No alert needed (degraded/warnings are informational only).")
        return {
            "ok": True,
            "alerted": False,
            "health": overall,
            "critical_errors": len(critical_errors),
            "warnings": len(warn_list),
        }

    # 4. 生成告警邮件（仅 critical 或真失败，统一用 [CRITICAL]）
    logger.info(f"Generating alert: health={overall}, critical={len(critical_errors)}, warnings={len(warn_list)}")

    # 构造主题
    if overall == "critical" or critical_errors:
        prefix = "[CRITICAL]"
    else:
        prefix = "[INFO]"
    subject = f"{prefix} Watsons Intel Alert — {date}"
    
    body = format_pipeline_alert_body(health_result, critical_errors, warn_list, date, project_root)

    # 5. 发送
    success = False
    if not dry_run:
        success = send_alert(project_root, subject, body, dry_run=False)
        logger.info(f"Alert sent: {success}")
    else:
        logger.info(f"DRY RUN: would send alert: {subject}")
        success = True

    # 6. 保存结果
    log_dir = Path(project_root) / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)
    alert_file = log_dir / "pipeline_alert.json"
    alert_data = {
        "date": date,
        "alerted": success,
        "health": overall,
        "health_details": health_result,
        "critical_errors": len(critical_errors),
        "warnings": len(warn_list),
        "subject": subject,
        "dry_run": dry_run,
    }
    alert_file.write_text(json.dumps(alert_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Alert saved to {alert_file}")

    return {
        "ok": True,
        "alerted": success,
        "health": overall,
        "critical_errors": len(critical_errors),
        "warnings": len(warn_list),
    }


if __name__ == "__main__":
    main()
