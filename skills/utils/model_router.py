#!/usr/bin/env python3
"""
model_router.py — 模型路由工具

读取 config/model_router.yaml，为各技能提供模型选择:
- 默认模型和 fallback 顺序
- 按事件优先级选模型
- 参数覆盖

用法:
    from skills.utils.model_router import get_model_for_skill, get_model_params

    model, fallbacks = get_model_for_skill("extract_events")
    params = get_model_params("extract_events")

    # 或按优先级:
    model, fallbacks = get_model_for_skill("analyze_business_impact", priority="P1")
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ── 项目根目录 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_DEFAULT_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config", "model_router.yaml")

# ── 缓存 ──
_config_cache: Optional[Dict[str, Any]] = None


def _load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载 model_router.yaml 配置。"""
    global _config_cache
    path = config_path or _DEFAULT_CONFIG_PATH

    if _config_cache is not None:
        return _config_cache

    if not os.path.exists(path):
        logger.warning(f"模型路由配置不存在: {path}，将使用默认模型")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        _config_cache = config
        logger.info(f"加载模型路由配置: {path}")
        return config
    except Exception as e:
        logger.warning(f"加载模型路由配置失败: {e}，将使用默认模型")
        return {}


def reload_config(config_path: Optional[str] = None):
    """强制重新加载配置（用于测试或配置变更后）。"""
    global _config_cache
    _config_cache = None
    _load_config(config_path)


def get_default_model() -> str:
    """获取全局默认模型。"""
    config = _load_config()
    return config.get("default_model", "LongCat-Flash-Thinking")


def get_models_config() -> Dict[str, Any]:
    """获取模型定义配置。"""
    config = _load_config()
    return config.get("models", {})


def get_model_for_skill(
    skill_name: str,
    priority: Optional[str] = None,
    config_path: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """获取指定技能的默认模型和 fallback 列表。

    Args:
        skill_name: 技能名称，如 "extract_events"
        priority: 事件优先级，如 "P1", "P2", "ARCHIVE"（可选）
        config_path: 配置文件路径（可选，默认自动检测）

    Returns:
        (default_model, fallback_models)
        - default_model: 该技能/优先级的默认模型名
        - fallback_models: fallback 模型列表
    """
    # 强制重新加载（用于首次调用时确保配置最新）
    if config_path:
        reload_config(config_path)

    config = _load_config(config_path)
    routing = config.get("routing", {})
    global_fallback = config.get("global_fallback", ["LongCat-Flash-Chat", "LongCat-Flash-Thinking"])
    default_model = config.get("default_model", "LongCat-Flash-Thinking")

    if skill_name not in routing:
        logger.info(f"技能 '{skill_name}' 未在路由配置中，使用全局默认模型: {default_model}")
        return default_model, global_fallback

    skill_config = routing[skill_name]

    # ── ARCHIVE 优先级：规则模式 ──
    if priority == "ARCHIVE":
        priority_routing = skill_config.get("priority_routing", {})
        archive_model = priority_routing.get("ARCHIVE", "rule_only")
        if archive_model == "rule_only":
            logger.info(f"技能 '{skill_name}' ARCHIVE 事件: 使用规则模式（不调 LLM）")
            return "rule_only", []

    # ── 按优先级选模型（用于 analyze_business_impact） ──
    if priority and "priority_routing" in skill_config:
        priority_routing = skill_config.get("priority_routing", {})
        if priority in priority_routing:
            model = priority_routing[priority]
            if model == "rule_only":
                return "rule_only", []
            fallbacks = skill_config.get("fallback", global_fallback)
            logger.info(f"技能 '{skill_name}' 优先级 {priority}: 使用模型 {model}, fallback={fallbacks}")
            return model, fallbacks

    # ── 默认模型 ──
    model = skill_config.get("default", default_model)
    fallbacks = skill_config.get("fallback", global_fallback)

    logger.info(f"技能 '{skill_name}': 使用模型 {model}, fallback={fallbacks}")
    return model, fallbacks


def get_model_params(
    skill_name: str,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """获取指定技能的模型参数覆盖。

    Args:
        skill_name: 技能名称
        config_path: 配置文件路径（可选）

    Returns:
        参数字典，如 {"temperature": 0.2, "max_tokens": 4096}
    """
    config = _load_config(config_path)
    routing = config.get("routing", {})
    models_config = config.get("models", {})

    # 技能级参数
    skill_params = routing.get(skill_name, {}).get("params", {})

    # 获取使用的模型名
    model_name, _ = get_model_for_skill(skill_name, config_path=config_path)

    # 模型级默认参数
    model_defaults = models_config.get(model_name, {})

    # 合并：技能参数覆盖模型默认
    merged = {}
    for key in ("temperature", "max_tokens", "timeout"):
        if key in model_defaults:
            merged[key] = model_defaults[key]
    merged.update(skill_params)

    return merged


def is_rule_only(
    skill_name: str,
    priority: Optional[str] = None,
    config_path: Optional[str] = None,
) -> bool:
    """判断指定技能/优先级是否应使用规则模式（不调 LLM）。

    Args:
        skill_name: 技能名称
        priority: 事件优先级
        config_path: 配置文件路径（可选）

    Returns:
        True 表示应使用规则模式
    """
    model, _ = get_model_for_skill(skill_name, priority=priority, config_path=config_path)
    return model == "rule_only"


def get_high_value_model(
    skill_name: str,
    config_path: Optional[str] = None,
) -> Optional[str]:
    """获取技能的高价值事件模型（如 extract_events 的 Thinking 模型）。

    Args:
        skill_name: 技能名称
        config_path: 配置文件路径（可选）

    Returns:
        高价值模型名，如未配置则返回 None
    """
    config = _load_config(config_path)
    routing = config.get("routing", {})
    skill_config = routing.get(skill_name, {})
    return skill_config.get("high_value", None)


def get_high_value_threshold(
    skill_name: str,
    config_path: Optional[str] = None,
) -> float:
    """获取技能的高价值事件阈值。"""
    config = _load_config(config_path)
    routing = config.get("routing", {})
    skill_config = routing.get(skill_name, {})
    return skill_config.get("high_value_threshold", 4.0)


def get_config_summary() -> Dict[str, Any]:
    """返回模型路由配置摘要。"""
    config = _load_config()
    if not config:
        return {"status": "no_config", "default_model": get_default_model()}

    routing = config.get("routing", {})
    summary = {}
    for skill, cfg in routing.items():
        if "priority_routing" in cfg:
            summary[skill] = {
                "type": "priority_routing",
                "routing": cfg["priority_routing"],
                "fallback": cfg.get("fallback", []),
            }
        else:
            summary[skill] = {
                "type": "default",
                "default": cfg.get("default", "unknown"),
                "fallback": cfg.get("fallback", []),
                "high_value": cfg.get("high_value"),
            }

    return {
        "status": "loaded",
        "default_model": config.get("default_model", "unknown"),
        "skills": summary,
    }