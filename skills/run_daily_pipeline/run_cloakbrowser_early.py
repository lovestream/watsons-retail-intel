#!/usr/bin/env python3
"""
CloakBrowser 早班采集脚本 — 每天 05:00 运行
仅执行 CloakBrowser 三步：collect → enrich → date_verify
主管道 07:00 运行时通过 resume=true 跳过已完成的步骤。

用法:
    python run_cloakbrowser_early.py --project-root . [--date 2026-05-15]
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

# 确保项目根目录在 sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT_CANDIDATE = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
if PROJECT_ROOT_CANDIDATE not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_CANDIDATE)

CST = timezone(timedelta(hours=8))
LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
logger = logging.getLogger("cloakbrowser_early")


def run_step(step_name: str, module_path: str, func_name: str,
             project_root: str, date: str, extra_args: dict,
             cli_path: str = None) -> dict:
    """运行单个步骤，返回 {"ok": bool, ...}"""
    t0 = time.time()
    logger.info(f"{'─' * 60}")
    logger.info(f"步骤: {step_name}")
    logger.info(f"{'─' * 60}")

    # 尝试 import 调用
    try:
        import importlib
        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        sig = __import__('inspect').signature(func)
        kwargs = {"project_root": project_root, "date": date}
        for k, v in extra_args.items():
            if k in sig.parameters:
                kwargs[k] = v
        result = func(**kwargs)
        elapsed = time.time() - t0
        logger.info(f"  ✅ {step_name} 成功 ({elapsed:.1f}s)")
        return {"ok": True, "elapsed": elapsed}
    except Exception as e:
        logger.error(f"  ❌ {step_name} 失败: {e}")
        return {"ok": False, "error": str(e), "elapsed": time.time() - t0}


def main():
    parser = argparse.ArgumentParser(description="CloakBrowser 早班采集")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    project_root = os.path.abspath(args.project_root)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    now = datetime.now(CST)
    date = args.date or now.strftime("%Y-%m-%d")

    logger.info(f"CloakBrowser 早班采集: date={date}")
    logger.info(f"项目根目录: {project_root}")

    # 确保目录存在
    for d in [f"data/raw/{date}", f"data/logs/{date}", f"data/runs/{date}"]:
        os.makedirs(os.path.join(project_root, d), exist_ok=True)

    steps = [
        {
            "name": "cloakbrowser_collect",
            "module": "skills.cloakbrowser_collect.cloakbrowser_collect",
            "function": "run_cloakbrowser_collect",
            "cli": "skills/cloakbrowser_collect/cloakbrowser_collect.py",
            "args": {"verbose": True},
        },
        {
            "name": "cloakbrowser_content_fetch",
            "module": "skills.cloakbrowser_collect.cloakbrowser_content_fetch",
            "function": "run_cloakbrowser_content_fetch",
            "cli": "skills/cloakbrowser_collect/cloakbrowser_content_fetch.py",
            "args": {"max_articles": 30, "headless": True},
        },
        {
            "name": "cloakbrowser_enrich",
            "module": "skills.cloakbrowser_collect.cloakbrowser_enrich",
            "function": "run_cloakbrowser_enrich",
            "cli": "skills/cloakbrowser_collect/cloakbrowser_enrich.py",
            "args": {"max_articles": 40},
        },
        {
            "name": "cloakbrowser_date_verify",
            "module": "skills.cloakbrowser_collect.cloakbrowser_date_verify",
            "function": "run_date_verify",
            "cli": "skills/cloakbrowser_collect/cloakbrowser_date_verify.py",
            "args": {},
        },
    ]

    results = {}
    for step in steps:
        r = run_step(
            step_name=step["name"],
            module_path=step["module"],
            func_name=step["function"],
            project_root=project_root,
            date=date,
            extra_args=step["args"],
            cli_path=step.get("cli"),
        )
        results[step["name"]] = r
        if not r["ok"]:
            logger.warning(f"  ⚠️ {step['name']} 失败，后续步骤可能受影响，但不阻塞主流程")

    # 输出运行清单
    total_elapsed = sum(r.get("elapsed", 0) for r in results.values())
    success_count = sum(1 for r in results.values() if r["ok"])
    manifest = {
        "date": date,
        "started_at": now.isoformat(),
        "finished_at": datetime.now(CST).isoformat(),
        "total_elapsed_seconds": round(total_elapsed, 1),
        "steps": {k: {"ok": v["ok"], "elapsed": round(v.get("elapsed", 0), 1)}
                  for k, v in results.items()},
        "success_count": success_count,
        "total_steps": len(steps),
    }
    manifest_path = os.path.join(project_root, f"data/runs/{date}/cloakbrowser_early_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    logger.info(f"\n{'=' * 60}")
    logger.info(f"早班采集完成: {success_count}/{len(steps)} 步成功, 耗时 {total_elapsed:.0f}s")
    logger.info(f"清单: {manifest_path}")

    # 0 = 全部成功, 1 = 有失败
    sys.exit(0 if success_count == len(steps) else 1)


if __name__ == "__main__":
    main()
