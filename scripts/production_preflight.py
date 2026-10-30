#!/usr/bin/env python3
"""Read-only NAS release preflight; no cron, SMTP, data mutation or LLM calls.

Usage:
  PYTHONPATH=.venv_packages:. python3 scripts/production_preflight.py --root .
  PYTHONPATH=.venv_packages:. python3 scripts/production_preflight.py --root . --max-event-lag 3
Exit 0=ready for staging dry-run, 2=blocked. Never prints key values.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path


def latest_event(root):
    base = root / "data" / "events"
    rows = []
    if base.is_dir():
        for child in base.iterdir():
            if not child.is_dir():
                continue
            try:
                d = date.fromisoformat(child.name)
            except ValueError:
                continue
            if (child / "events_analyzed.json").is_file():
                try:
                    doc = json.loads((child / "events_analyzed.json").read_text("utf-8"))
                    events = doc if isinstance(doc, list) else doc.get("events") or []
                    rows.append((d, len(events)))
                except (ValueError, OSError):
                    pass
    if not rows:
        return None
    return max(rows, key=lambda row: row[0])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", default=".")
    p.add_argument("--max-event-lag", type=int, default=3)
    a = p.parse_args()
    root = Path(a.root).resolve()
    now = datetime.now(timezone(timedelta(hours=8))).date()
    checks = {}
    ev = latest_event(root)
    checks["data_recency"] = {
        "ok": bool(ev and 0 <= (now - ev[0]).days <= a.max_event_lag),
        "latest_event_day": ev[0].isoformat() if ev else None,
        "latest_analyzed_events": ev[1] if ev else 0,
        "lag_days": (now - ev[0]).days if ev else None,
        "max_lag_days": a.max_event_lag,
    }
    expected = ["skills/reader_brief/production_delivery.py",
                "skills/reader_brief/run_brief.py",
                "skills/periodic_synthesis/cross_period.py",
                "skills/generate_periodic_report/generate_periodic_report.py",
                "skills/send_daily_report_email/send_daily_report_email.py"]
    checks["reader_brief_modules"] = {
        "ok": all((root / item).is_file() for item in expected),
        "missing": [item for item in expected if not (root / item).is_file()],
    }
    checks["model_keys"] = {
        "ok": bool(os.environ.get("siliconflow_api_key") or
                   os.environ.get("SILICONFLOW_API_KEY") or
                   os.environ.get("rightcode_bybt")),
        "siliconflow_key_present": bool(os.environ.get("siliconflow_api_key") or
                                        os.environ.get("SILICONFLOW_API_KEY")),
        "rightcode_key_present": bool(os.environ.get("rightcode_bybt")),
        "note": "此项只检查存在性，真实连通需在NAS单独调用，绝不打印密钥",
    }
    checks["mail_config"] = {
        "ok": bool((root / "config" / "email.yaml").is_file() or
                   os.environ.get("SMTP_FROM")),
        "config_file_present": (root / "config" / "email.yaml").is_file(),
        "note": "不会连接SMTP；还需单独测试附件、收件地址与Gmail/Outlook显示",
    }
    checks["python_deps"] = {
        "ok": all(importlib.util.find_spec(m) is not None for m in ("yaml", "markdown_it", "requests")),
        "missing": [m for m in ("yaml", "markdown_it", "requests")
                    if importlib.util.find_spec(m) is None],
    }
    tests = ["tests/test_prompt_integration.py", "tests/test_pipeline_r0_r3.py",
             "tests/reader_brief/test_reader_brief.py"]
    checks["tests_checked_in"] = {
        "ok": all((root / f).is_file() for f in tests),
        "missing": [f for f in tests if not (root / f).is_file()],
    }
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                              check=True, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=8).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = ""
    checks["commit"] = {"ok": bool(head), "sha": head}
    mode = os.environ.get("WATSONS_PERIODIC_MAIL_MODE", "hold").strip().lower()
    checks["delivery_mode"] = {"mode": mode,
                                "safe_default": mode != "reader_brief",
                                "note": "正式发送只有在显式reader_brief模式及双门通过时才启用"}
    blocked = [name for name, value in checks.items()
               if isinstance(value, dict) and value.get("ok") is False]
    output = {"checked_at_beijing": now.isoformat(), "root": str(root),
              "checks": checks, "blocked": blocked,
              "ready_for_shadow_dry_run": not blocked,
              "smtp_sent": False, "cron_changed": False, "production_data_modified": False}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
