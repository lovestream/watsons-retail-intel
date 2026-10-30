#!/usr/bin/env python3
"""prompt_registry.py — 15 份独立提示词的集中加载、变量校验与版本化。

设计（docs/16 §3）：
- 权威文本仍是 `docs/prompts/*.md`；运行时只抽取其中 `## System Prompt` /
  `## User Prompt` 两节，不把"调用位置/验收说明"塞给模型。
- 每个模板声明 required 变量；渲染时缺变量直接抛错（启动/CI 可统一 check）。
- 版本号 = system+user 文本的 sha256 前缀；缓存键必须包含它，源证据变化即失效。
- 输出类型只有三种：json / markdown / text（P06 为 markdown，P10 为 text）。
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "config" / "prompt_registry.yaml"

SECTION_KEYS = {"system": "System Prompt", "user": "User Prompt"}
VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass
class PromptTemplate:
    prompt_id: str
    title: str
    file: str
    call_site: str
    output_type: str
    enabled: bool
    system: str
    user: str
    variables: List[str] = field(default_factory=list)
    sha: str = ""
    max_tokens: int = 4096
    temperature: float = 0.3

    @property
    def version(self) -> str:
        return self.sha[:12]


@dataclass
class RenderedPrompt:
    prompt_id: str
    system: str
    user: str
    version: str
    output_type: str
    temperature: float
    max_tokens: int


def _extract(md_text: str, keyword: str) -> str:
    """按标题关键词抽取章节正文（标题文字允许带后缀，如"System Prompt（完整）"）。"""
    lines = md_text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith("## ") and keyword in st:
            start = i
            break
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("## "):
            break
        out.append(ln)
    return "\n".join(out).strip()


def load_registry(path: Optional[Path] = None) -> Dict[str, PromptTemplate]:
    path = Path(path or REGISTRY_PATH)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    items: Dict[str, PromptTemplate] = {}
    for pid, spec in (cfg.get("prompts") or {}).items():
        md_path = ROOT / spec["file"]
        md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        system = _extract(md, SECTION_KEYS["system"])
        user = _extract(md, SECTION_KEYS["user"])
        blob = (system + "\n" + user).encode()
        declared = list(spec.get("variables") or [])
        found = sorted(set(VAR_RE.findall(system + "\n" + user)))
        if declared:
            missing = [v for v in declared if v not in found]
            if missing:
                logger.warning("%s 声明的变量在模板中不存在: %s", pid, missing)
        items[pid] = PromptTemplate(
            prompt_id=pid, title=spec.get("title", ""), file=spec["file"],
            call_site=spec.get("call_site", ""),
            output_type=spec.get("output_type", "json"),
            enabled=bool(spec.get("enabled", True)),
            system=system, user=user,
            variables=declared or found,
            sha=hashlib.sha256(blob).hexdigest(),
            max_tokens=int(spec.get("max_tokens", 4096)),
            temperature=float(spec.get("temperature", 0.3)),
        )
    return items


_CACHE: Dict[str, PromptTemplate] = {}


def get_prompt(pid: str) -> PromptTemplate:
    global _CACHE
    if not _CACHE:
        _CACHE = load_registry()
    if pid not in _CACHE:
        raise KeyError(f"未注册的提示词: {pid}")
    tpl = _CACHE[pid]
    if not tpl.system or not tpl.user:
        raise ValueError(f"{pid} 缺少 System/User 章节: {tpl.file}")
    return tpl


def render(pid: str, **variables) -> RenderedPrompt:
    tpl = get_prompt(pid)
    missing = [v for v in tpl.variables if v not in variables]
    if missing:
        raise ValueError(f"{pid} 渲染缺少变量: {missing}")
    user = tpl.user
    for key, val in variables.items():
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False, indent=1)
        user = user.replace("{" + key + "}", str(val))
    left = VAR_RE.findall(user)
    if left:
        raise ValueError(f"{pid} 仍有未替换变量: {sorted(set(left))}")
    return RenderedPrompt(prompt_id=pid, system=tpl.system, user=user,
                          version=tpl.version, output_type=tpl.output_type,
                          temperature=tpl.temperature, max_tokens=tpl.max_tokens)


def cache_key(pid: str, model: str, payload_sha: str) -> str:
    """缓存键：prompt_version + model + 输入 payload 指纹。"""
    return f"{pid}:{get_prompt(pid).version}:{model}:{payload_sha}"


def check_all() -> List[str]:
    """启动/CI 检查：文件存在、两节齐全、变量可解析、无未注册占位符。"""
    problems = []
    for pid, tpl in load_registry().items():
        md_path = ROOT / tpl.file
        if not md_path.exists():
            problems.append(f"{pid}: 文件不存在 {tpl.file}")
            continue
        if not tpl.system:
            problems.append(f"{pid}: 缺少 ## System Prompt")
        if not tpl.user:
            problems.append(f"{pid}: 缺少 ## User Prompt")
        left = set(VAR_RE.findall(tpl.user)) - set(tpl.variables)
        if left:
            problems.append(f"{pid}: 未声明变量 {sorted(left)}")
    return problems


def _main():
    problems = check_all()
    reg = load_registry()
    print(f"{'ID':5} {'ver':13} {'type':9} {'vars':4} {'enabled':8} title")
    for pid in sorted(reg):
        t = reg[pid]
        print(f"{pid:5} {t.version:13} {t.output_type:9} {len(t.variables):<4} "
              f"{str(t.enabled):8} {t.title}")
    print(f"\n{len(reg)} prompts, problems: {len(problems)}")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(_main())
