#!/usr/bin/env python3
"""
send_admin_alert.py — 发送管理员告警邮件

当健康检查发现关键服务不可用或 pipeline 步骤失败时，
发送告警到管理员邮箱（51111211004@stu.ecnu.edu.cn）。

用法:
    python3 -m skills.send_admin_alert.send_admin_alert \\
        --project-root . \\
        --health-check-json data/logs/2026-05-05/health_check.json \\
        --dry-run

    或直接传告警文本:
    python3 -m skills.send_admin_alert.send_admin_alert \\
        --project-root . \\
        --subject "[CRITICAL] Longcat LLM all keys down" \\
        --body "longcat, longcat1 均返回 401"
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
ADMIN_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "51111211004@stu.ecnu.edu.cn")
MAILCLI_PATH = Path("/app/working/workspaces/default/skills/email/mailcli.py")


def _send_alert_mail(to_addr: str, subject: str, body: str,
                     dry_run: bool = False, logger: logging.Logger = None) -> tuple[bool, str]:
    """通过 mailcli.py 发送纯文本告警邮件。"""
    if logger is None:
        logger = logging.getLogger("send_admin_alert")

    if dry_run:
        logger.info(f"[DRY-RUN] To: {to_addr}, Subject: {subject}")
        return True, "dry_run"

    if not MAILCLI_PATH.exists():
        return False, f"mailcli.py not found: {MAILCLI_PATH}"

    tmp_dir = None
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="watsons_alert_"))
        body_file = tmp_dir / "alert_body.txt"
        body_file.write_text(body, encoding="utf-8")

        cmd = [
            sys.executable, str(MAILCLI_PATH), "send",
            "--to", to_addr,
            "--subject", subject,
            "--body-file", str(body_file),
        ]
        logger.info(f"mailcli send: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        if result.returncode == 0:
            logger.info(f"Alert sent to {to_addr}")
            return True, "sent"
        else:
            err = result.stderr.strip() or result.stdout.strip()
            logger.error(f"Alert send failed: {err}")
            return False, err[:200]

    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)[:200]
    finally:
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


def format_alert_email(health_result: dict, pipeline_errors: list = None) -> tuple[str, str]:
    """生成告警邮件主题和正文。"""
    overall = health_result.get("overall", "healthy")
    ts = health_result.get("timestamp", datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S CST"))
    level = overall.upper()
    subject = f"[{level}] Watsons Intel Health Alert — {ts}"

    lines = [
        f"Watsons Retail Intel — 系统健康告警",
        f"=" * 50,
        f"时间：{ts}",
        f"整体状态：{level}",
        f"",
    ]

    for svc_id, svc in health_result.get("services", {}).items():
        emoji = {"healthy": "OK", "degraded": "WARN", "down": "DOWN", "unknown": "??"}.get(svc["status"], "??")
        lines.append(f"[{emoji}] {svc['name']}: {svc['status']} — {svc['detail']}")
        for fk in svc.get("failed_keys", []):
            lines.append(f"    X {fk['env_name']} ({fk['key']}): {fk['error']}")
    lines.append("")

    if pipeline_errors:
        lines.append(f"Pipeline Errors ({len(pipeline_errors)}):")
        for i, err in enumerate(pipeline_errors[:20], 1):
            lines.append(f"  {i}. {err.get('step','?')}: {err.get('error','?')[:100]}")
        lines.append("")

    lines.append(f"--")
    lines.append(f"自动告警邮件，请检查并处理。")
    return subject, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Send admin alert email")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--health-check-json", default=None, help="健康检查 JSON 文件路径")
    parser.add_argument("--subject", default=None, help="自定义主题")
    parser.add_argument("--body", default=None, help="自定义正文")
    parser.add_argument("--pipeline-errors", default=None, help="Pipeline 错误 JSON 数组")
    parser.add_argument("--dry-run", action="store_true", help="不实际发送")
    parser.add_argument("--force", action="store_true", help="即使 healthy 也发送")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("send_admin_alert")

    subject = None
    body = None

    if args.subject and args.body:
        subject = args.subject
        body = args.body
    elif args.health_check_json:
        hc_path = Path(args.health_check_json)
        if not hc_path.exists():
            logger.error(f"Health check JSON not found: {hc_path}")
            sys.exit(1)
        health_result = json.loads(hc_path.read_text(encoding="utf-8"))

        pipeline_errors = []
        if args.pipeline_errors:
            try:
                pipeline_errors = json.loads(args.pipeline_errors)
            except Exception:
                pipeline_errors = [args.pipeline_errors]

        overall = health_result.get("overall", "healthy")
        if overall == "healthy" and not args.force:
            logger.info(f"Status healthy, skipping alert (use --force to override)")
            sys.exit(0)

        subject, body = format_alert_email(health_result, pipeline_errors)
    else:
        logger.error("Must provide --health-check-json or both --subject and --body")
        sys.exit(1)

    success, msg = _send_alert_mail(
        to_addr=ADMIN_EMAIL,
        subject=subject,
        body=body,
        dry_run=args.dry_run,
        logger=logger,
    )

    if success:
        logger.info(f"OK: {msg}")
    else:
        logger.error(f"FAIL: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
