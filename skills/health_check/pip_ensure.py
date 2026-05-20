#!/usr/bin/env python3
"""
pip_ensure.py — 自愈启动脚本

在 pipeline 启动或健康检查时调用，确保所有关键 Python 包已安装。
安装到 PROJECT_ROOT/.venv_packages/ 持久目录，不受 venv 重建影响。

用法：
    python3 skills/health_check/pip_ensure.py --project-root .
    python3 skills/health_check/pip_ensure.py --project-root . --quiet
"""

import subprocess
import sys
import importlib
import os
import argparse

REQUIRED_PACKAGES = [
    ("xcrawl", "xcrawl"),
    ("edge_tts", "edge-tts"),
    ("cloakbrowser", "cloakbrowser"),
    ("yaml", "pyyaml"),
    ("markdown_it", "markdown-it-py"),
    ("bs4", "beautifulsoup4"),
    ("jinja2", "jinja2"),
    ("aiohttp", "aiohttp"),
    ("requests", "requests"),
    ("feedparser", "feedparser"),
    ("lxml", "lxml"),
    ("dateutil", "python-dateutil"),
]

VENV_PACKAGES_DIR = None  # set by setup_path()


def setup_path(project_root: str) -> str:
    """将 .venv_packages 加入 sys.path，返回其绝对路径。"""
    global VENV_PACKAGES_DIR
    VENV_PACKAGES_DIR = os.path.abspath(os.path.join(project_root, ".venv_packages"))
    if VENV_PACKAGES_DIR not in sys.path:
        sys.path.insert(0, VENV_PACKAGES_DIR)
    return VENV_PACKAGES_DIR


def check_packages(quiet: bool = False) -> dict:
    """检查所有关键包，返回缺失列表。"""
    missing = {}
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing[import_name] = pip_name
            if not quiet:
                print(f"  ❌ {import_name} (pip: {pip_name}) 未安装", file=sys.stderr)
        else:
            if not quiet:
                print(f"  ✅ {import_name}", file=sys.stderr)
    return missing


def install_packages(packages: dict, target_dir: str) -> bool:
    """安装缺失的包到 target_dir。返回 True 表示全部成功。"""
    if not packages:
        return True

    pip_names = list(set(packages.values()))
    os.makedirs(target_dir, exist_ok=True)
    print(f"\n🔧 安装 {len(pip_names)} 个缺失包到 {target_dir}: {' '.join(pip_names)}", file=sys.stderr)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", target_dir, "--quiet"] + pip_names,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"❌ pip install 失败:\n{result.stderr[:500]}", file=sys.stderr)
            return False
        print("✅ 安装完成", file=sys.stderr)
        return True
    except Exception as e:
        print(f"❌ pip install 异常: {e}", file=sys.stderr)
        return False


def ensure(project_root: str = ".", quiet: bool = False, auto_fix: bool = True) -> dict:
    """自愈入口：设 path → 检查 → 安装到 .venv_packages → 复检。

    Returns:
        {"ok": bool, "missing_before": [...], "missing_after": [...], "fixed": [...], "target_dir": str}
    """
    target_dir = setup_path(project_root)

    if not quiet:
        print(f"=== pip_ensure: target={target_dir} ===", file=sys.stderr)

    missing_before = check_packages(quiet=quiet)
    result = {
        "ok": True,
        "missing_before": list(missing_before.keys()),
        "missing_after": [],
        "fixed": [],
        "target_dir": target_dir,
    }

    if not missing_before:
        if not quiet:
            print("✅ 所有包完整", file=sys.stderr)
        return result

    if not auto_fix:
        result["ok"] = False
        result["missing_after"] = list(missing_before.keys())
        return result

    # 尝试修复到持久目录
    ok = install_packages(missing_before, target_dir)
    if not ok:
        result["ok"] = False
        result["missing_after"] = list(missing_before.keys())
        return result

    # 复检（target_dir 已在 sys.path 中）
    missing_after = check_packages(quiet=True)
    result["missing_after"] = list(missing_after.keys())
    result["fixed"] = [p for p in missing_before if p not in missing_after]

    if missing_after:
        result["ok"] = False
        if not quiet:
            print(f"⚠️ 仍有 {len(missing_after)} 个包缺失: {missing_after}", file=sys.stderr)
    else:
        result["ok"] = True
        if not quiet:
            print("✅ 自愈成功，所有包就绪", file=sys.stderr)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pip 自愈检查 - 安装到项目本地持久目录")
    parser.add_argument("--project-root", default=".", help="项目根目录 (默认 .)")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    parser.add_argument("--no-fix", action="store_true", help="仅检查，不修复")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    result = ensure(project_root=args.project_root, quiet=args.quiet, auto_fix=not args.no_fix)

    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False))
    else:
        if result["ok"]:
            print("OK")
        else:
            print("FAILED")
            sys.exit(1)
