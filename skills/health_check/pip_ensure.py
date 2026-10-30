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
    ("edge_tts", "edge-tts"),  # 版本下限见 MIN_VERSIONS
    ("mutagen", "mutagen"),
    ("imageio_ffmpeg", "imageio-ffmpeg"),
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

# 版本下限：已安装但低于下限也视为"需要修复"（不只是"能不能 import"）。
# edge-tts 7.2.8 起微软 DRM 令牌(Sec-MS-GEC)版本失效 → WSS 握手 403，
# 语音合成就整条挂掉，且表现为"库装好了、就是跑不通"，极易被误判成网络问题。
MIN_VERSIONS = {
    "edge-tts": (7, 3, 1),
}

VENV_PACKAGES_DIR = None  # set by setup_path()


def _parse_version(v: str):
    """把 '7.3.1' / '7.3.1rc1' 解析成可比较元组（非数字段忽略）。"""
    nums = []
    for part in str(v).replace("-", ".").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits == "":
            break
        nums.append(int(digits))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def _installed_version(pip_name: str):
    """读取已安装版本：优先 importlib.metadata，退回包内 __version__。"""
    try:
        from importlib.metadata import version as _v
        return _v(pip_name)
    except Exception:
        pass
    try:
        import importlib
        mod = importlib.import_module(pip_name.replace("-", "_"))
        return getattr(mod, "__version__", None)
    except Exception:
        return None


def setup_path(project_root: str) -> str:
    """将 .venv_packages 加入 sys.path，返回其绝对路径。"""
    global VENV_PACKAGES_DIR
    VENV_PACKAGES_DIR = os.path.abspath(os.path.join(project_root, ".venv_packages"))
    if VENV_PACKAGES_DIR not in sys.path:
        sys.path.insert(0, VENV_PACKAGES_DIR)
    return VENV_PACKAGES_DIR


def check_packages(quiet: bool = False) -> dict:
    """检查所有关键包（存在性 + 版本下限），返回需要修复列表。"""
    missing = {}
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing[import_name] = pip_name
            if not quiet:
                print(f"  ❌ {import_name} (pip: {pip_name}) 未安装", file=sys.stderr)
            continue

        floor = MIN_VERSIONS.get(pip_name)
        if floor:
            cur = _installed_version(pip_name)
            if cur is None:
                if not quiet:
                    print(f"  ✅ {import_name}（版本未知，按满足下限处理）",
                          file=sys.stderr)
                continue
            if _parse_version(cur) < floor:
                need = ".".join(str(x) for x in floor)
                missing[import_name] = pip_name
                if not quiet:
                    print(f"  ⚠️ {import_name} 版本过旧 {cur} < {need}，需升级",
                          file=sys.stderr)
                continue
        if not quiet:
            print(f"  ✅ {import_name}", file=sys.stderr)
    return missing


def install_packages(packages: dict, target_dir: str,
                     project_root: str = ".") -> bool:
    """安装缺失的包到 target_dir。返回 True 表示全部成功。

    §36: 若存在 requirements.lock 则优先用锁文件安装（生产可复现）。
    """
    if not packages:
        return True

    pip_names = list(set(packages.values()))
    os.makedirs(target_dir, exist_ok=True)

    # 版本下限不满足：直接精确升级这些包，避免重装整份 lock（慢且会动到别的包）
    floor_specs = [
        "%s>=%s" % (n, ".".join(str(x) for x in MIN_VERSIONS[n]))
        for n in pip_names if n in MIN_VERSIONS
    ]
    extra_flags: list = []
    if floor_specs:
        rest = [n for n in pip_names if n not in MIN_VERSIONS]
        install_spec = floor_specs + rest
        extra_flags = ["--upgrade"]
        print(f"⬆️ 版本下限升级: {' '.join(floor_specs)}", file=sys.stderr)
    else:
        # 锁文件优先：缺失包必须全部在锁文件覆盖范围内才使用锁文件
        lock_path = os.path.join(project_root, "requirements.lock")
        install_spec = None
        if os.path.exists(lock_path):
            try:
                locked_names = set()
                for line in open(lock_path, encoding="utf-8"):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    name = line.split("==")[0].split(">=")[0].strip().lower()
                    locked_names.add(name)
                missing_locked = {n.lower() for n in pip_names}
                if missing_locked.issubset(locked_names):
                    install_spec = ["-r", lock_path]
                    print(f"🔒 使用 requirements.lock 安装", file=sys.stderr)
            except Exception as e:
                print(f"⚠️ 锁文件解析失败，退回按包名安装: {e}", file=sys.stderr)
        if install_spec is None:
            install_spec = pip_names

    print(f"\n🔧 安装 {len(pip_names)} 个缺失包到 {target_dir}: "
          f"{' '.join(pip_names)}", file=sys.stderr)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", target_dir,
             "--quiet"] + extra_flags + install_spec,
            capture_output=True, text=True, timeout=300,
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
    ok = install_packages(missing_before, target_dir, project_root=project_root)
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
