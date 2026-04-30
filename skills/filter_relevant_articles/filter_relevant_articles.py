#!/usr/bin/env python3
"""
filter_relevant_articles.py — 规则过滤 + LLM 语义复核

读取 raw_articles.json，结合 keywords.yaml 和 scoring.yaml，
对文章评分后分为 cleaned / reference / rejected 三类池。

用法:
    python filter_relevant_articles.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-04-26 \
        --use-llm true \
        --llm-mode borderline_only
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ===================== 依赖检查 =====================
_MISSING = []
try:
    import yaml
except ImportError:
    _MISSING.append("PyYAML")

if _MISSING:
    print(f"ERROR: 缺少必要依赖: {', '.join(_MISSING)}\n"
          f"请运行: pip install {' '.join(_MISSING)}", file=sys.stderr)
    sys.exit(1)

# ===================== 常量与关键词词典 =====================

# 即时零售平台词 — 命中 +4
INSTANT_RETAIL_KEYWORDS = [
    "美团闪购", "京东秒送", "京东到家", "淘宝闪购", "饿了么",
    "抖音小时达", "即时零售", "小时达", "同城零售", "本地生活",
    "前置仓", "闪电仓", "门店履约",
]

# 美妆个护品类词 — 命中 +3
BEAUTY_CARE_KEYWORDS = [
    "美妆", "个护", "护肤", "彩妆", "洗护", "防晒", "女性护理",
    "男士护理", "身体护理", "口腔护理", "香氛", "面膜", "卸妆",
    "旅行装", "小规格", "凑品类", "凑单品",
]

# 竞对词 — 命中 +3
COMPETITOR_KEYWORDS = [
    "丝芙兰", "万宁", "妍丽", "WOW COLOUR", "调色师", "话梅",
    "名创优品", "KK集团",
]

# B2C / To B 渠道词 — 命中 +2
B2B_CHANNEL_KEYWORDS = [
    "天猫官旗", "天猫官方旗舰店", "天猫超市", "京东旗舰店",
    "京东POP", "京东自营",
]

# 经营变量词 — 命中 +1
BUSINESS_VAR_KEYWORDS = [
    "流量", "转化率", "客单价", "复购", "价格", "补贴", "毛利",
    "货盘", "SKU", "履约", "会员", "私域", "活动", "投流", "资源位",
]

# 屈臣氏直接命中 — +5
WATSONS_KEYWORDS = ["屈臣氏", "Watsons", "watsons"]

# 负面排除词 — 明显无关泛领域
NEGATIVE_GENERAL_KEYWORDS = [
    # 泛科技/泛财经/泛宏观 -3
    "宏观经济", "GDP", "CPI", "联储", "加息", "降息", "A股", "港股",
    "美股", "IPO", "独角兽", "融资轮",
]

NEGATIVE_TOPIC_KEYWORDS = [
    # 汽车、房产、游戏、芯片、AI大模型、农业、国际政治、医药审批 -3
    "汽车", "新能源车", "电动车", "房产", "楼市", "房价",
    "游戏", "手游", "芯片", "半导体", "大模型", "AI大模型",
    "农业", "国际政治", "医药审批", "临床试验",
]

NEGATIVE_JUNK_KEYWORDS = [
    # 招聘、公益、投诉电话、免责声明、导航页 -5
    "招聘", "求职", "简历", "公益", "慈善", "投诉电话",
    "免责声明", "导航页", "网站地图", "版权所有", "备案号",
]

# 合并分类用于统计
_KEYWORD_CATEGORIES = {
    "watsons": (WATSONS_KEYWORDS, 5),
    "instant_retail": (INSTANT_RETAIL_KEYWORDS, 4),
    "beauty_care": (BEAUTY_CARE_KEYWORDS, 3),
    "competitor": (COMPETITOR_KEYWORDS, 3),
    "b2b_channel": (B2B_CHANNEL_KEYWORDS, 2),
    "business_var": (BUSINESS_VAR_KEYWORDS, 1),
    "negative_general": (NEGATIVE_GENERAL_KEYWORDS, -3),
    "negative_topic": (NEGATIVE_TOPIC_KEYWORDS, -3),
    "negative_junk": (NEGATIVE_JUNK_KEYWORDS, -5),
}


# ===================== 配置加载 =====================


def load_yaml(filepath: str) -> dict:
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(project_root: str, rel_path: str) -> str:
    return str(Path(project_root) / rel_path)


def load_scoring_config(scoring_file: str) -> dict:
    """加载 scoring.yaml 中的阈值配置。"""
    config = load_yaml(scoring_file)
    return config


def _get_source_tier(article: dict) -> int:
    """统一获取 source_tier（兼容 tier 和 source_tier 两种字段名）。"""
    t = article.get("source_tier")
    if t is None:
        t = article.get("tier")
    if t is None:
        return 3
    if isinstance(t, str):
        try:
            return int(t)
        except (ValueError, TypeError):
            pass
        tier_map = {
            "tier1_direct_signal": 1, "tier1": 1, "direct_signal": 1,
            "tier2_analysis": 2, "tier2": 2,
            "tier3_anchor": 3, "tier3": 3,
            "tier4_clue": 4, "tier4": 4,
        }
        return tier_map.get(t, 3)
    if isinstance(t, (int, float)):
        return int(t)
    return 3


def compute_rule_score(
    article: dict,
    extra_keywords: Optional[dict] = None,
) -> Tuple[int, List[str]]:
    """计算单篇文章的规则评分和原因列表。
    
    Returns:
        (rule_score, rule_reasons)
    """
    score = 0
    reasons: List[str] = []

    # 合并判断文本
    combined = " ".join([
        article.get("title", "") or "",
        article.get("summary", "") or "",
        (article.get("content", "") or "")[:2000],  # 只取前2000字符避免过长
    ]).lower()

    # ── 加分 ──
    # 屈臣氏直接命中 +5
    for kw in WATSONS_KEYWORDS:
        if kw.lower() in combined:
            score += 5
            reasons.append(f"+5 命中屈臣氏: {kw}")
            break  # 只加一次

    # 即时零售平台词 +4
    for kw in INSTANT_RETAIL_KEYWORDS:
        if kw.lower() in combined:
            score += 4
            reasons.append(f"+4 命中即时零售: {kw}")
            break

    # 美妆个护品类词 +3
    matched_beauty = [kw for kw in BEAUTY_CARE_KEYWORDS if kw.lower() in combined]
    if matched_beauty:
        score += 3
        reasons.append(f"+3 命中美妆个护: {', '.join(matched_beauty[:3])}")

    # 竞对词 +3
    for kw in COMPETITOR_KEYWORDS:
        if kw.lower() in combined:
            score += 3
            reasons.append(f"+3 命中竞对: {kw}")
            break

    # B2C 渠道词 +2
    for kw in B2B_CHANNEL_KEYWORDS:
        if kw.lower() in combined:
            score += 2
            reasons.append(f"+2 命中B2C渠道: {kw}")
            break

    # 经营变量词 +1
    matched_vars = [kw for kw in BUSINESS_VAR_KEYWORDS if kw.lower() in combined]
    if matched_vars:
        score += 1
        reasons.append(f"+1 命中经营变量: {', '.join(matched_vars[:3])}")

    # 额外关键词（来自 keywords.yaml 的匹配结果）
    if extra_keywords:
        # 如果文章已有 matched_keywords，额外加一点
        mk = article.get("matched_keywords", [])
        if mk and len(mk) >= 3:
            score += 1
            reasons.append(f"+1 关键词命中≥3: {', '.join(mk[:3])}")

    # source_tier 加分
    tier = _get_source_tier(article)
    if tier == 1:
        score += 2
        reasons.append("+2 source_tier=1")
    elif tier == 2:
        score += 1
        reasons.append("+1 source_tier=2")

    # ── time_status 加分 ──
    ts = article.get("time_status", "")
    # 搜索源和 tavily 允许旧文章，只减1分；其他源减2分
    search_sources = ("search", "tavily", "gap")
    is_search_source = any(s in (article.get("source_name", "") + article.get("source_type", "")) for s in search_sources)
    allow_old = article.get("allow_old", False)

    if ts == "in_window":
        score += 2
        reasons.append("+2 time_status=in_window")
    elif ts == "near_window":
        score += 1
        reasons.append("+1 time_status=near_window")
    elif ts == "old":
        if is_search_source or allow_old:
            score -= 1
            reasons.append("-1 time_status=old(搜索源/allow_old)")
        else:
            score -= 2
            reasons.append("-2 time_status=old")

    # ── 减分 ──
    # 泛科技/泛财经/泛宏观 -3
    for kw in NEGATIVE_GENERAL_KEYWORDS:
        if kw.lower() in combined:
            score -= 3
            reasons.append(f"-3 泛领域: {kw}")
            break

    # 汽车、房产、游戏等 -3
    for kw in NEGATIVE_TOPIC_KEYWORDS:
        if kw.lower() in combined:
            score -= 3
            reasons.append(f"-3 无关主题: {kw}")
            break

    # 招聘、公益等 -5
    for kw in NEGATIVE_JUNK_KEYWORDS:
        if kw.lower() in combined:
            score -= 5
            reasons.append(f"-5 垃圾内容: {kw}")
            break

    # time_status 加减分已在上面处理（搜索源和 allow_old 源减1分，其他源减2分）

    # title 为空或 url 为空 -5
    title = article.get("title", "") or ""
    url = article.get("url", "") or ""
    if not title.strip():
        score -= 5
        reasons.append("-5 title为空")
    if not url.strip():
        score -= 5
        reasons.append("-5 url为空")

    # content + summary 极短且关键词为空 -2
    content_len = len(article.get("content", "") or "")
    summary_len = len(article.get("summary", "") or "")
    mk = article.get("matched_keywords", [])
    if content_len < 50 and summary_len < 50 and not mk:
        score -= 2
        reasons.append("-2 内容极短且无关键词")

    return score, reasons


def make_rule_decision(rule_score: int) -> str:
    """根据 rule_score 做初步决策。
    
    V3: review 阈值从2升到3，避免单关键词匹配触发大量 LLM 调用。
    """
    if rule_score >= 6:
        return "keep"
    elif rule_score >= 3:
        return "review"
    else:
        return "reject"


# ===================== LLM 语义复核 =====================


def _build_llm_prompt(article: dict) -> str:
    """构建 LLM 复核 prompt。"""
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    content = (article.get("content", "") or "")[:1500]  # 截取避免过长
    source = article.get("source_name", "")
    matched_kw = article.get("matched_keywords", [])

    return f"""请判断以下文章是否与"屈臣氏即时零售×个护美妆经营"相关。

文章信息：
- 标题：{title}
- 来源：{source}
- 摘要：{summary}
- 正文（前1500字）：{content}
- 已匹配关键词：{', '.join(str(k) for k in (matched_kw or []))}

请严格按以下 JSON 格式回复，不要添加任何其他文字：

{{
  "llm_relevance": "high|medium|low|none",
  "business_relevance_type": "direct|indirect|background|irrelevant",
  "related_channels": [],
  "related_categories": [],
  "business_variables": [],
  "reason": "一句话说明判断理由",
  "recommended_pool": "main|reference|reject",
  "confidence": "high|medium|low"
}}

判断标准：
- direct: 直接讨论屈臣氏电商经营、即时零售×个护美妆
- indirect: 间接影响屈臣氏经营（竞对动态、平台政策变化、品类趋势）
- background: 提供行业背景但不直接影响今日经营判断
- irrelevant: 明显无关"""


# ===================== 模型路由（模块级单次初始化） =====================

_MODEL_FOR_FILTER = None
_MODEL_PARAMS_FOR_FILTER = {}


def _init_model_router(logger_override=None):
    """初始化模型路由（模块级单例，避免每次调用重复导入）。"""
    global _MODEL_FOR_FILTER, _MODEL_PARAMS_FOR_FILTER
    if _MODEL_FOR_FILTER is not None:
        return _MODEL_FOR_FILTER, _MODEL_PARAMS_FOR_FILTER
    try:
        _utils_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils")
        if _utils_dir not in sys.path:
            sys.path.insert(0, _utils_dir)
        from skills.utils.model_router import get_model_for_skill, get_model_params
        _MODEL_FOR_FILTER, _ = get_model_for_skill("filter_relevant_articles")
        _MODEL_PARAMS_FOR_FILTER = get_model_params("filter_relevant_articles")
        log = logger_override or logging.getLogger("filter")
        log.info(f"filter_relevant_articles 使用模型: {_MODEL_FOR_FILTER}")
    except Exception:
        _MODEL_FOR_FILTER = None
        _MODEL_PARAMS_FOR_FILTER = {}
    return _MODEL_FOR_FILTER, _MODEL_PARAMS_FOR_FILTER


def llm_review_article(
    article: dict,
    llm_client,
    logger: logging.Logger,
) -> dict:
    """对单篇文章进行 LLM 语义复核。
    
    Returns:
        dict with keys: llm_relevance, business_relevance_type,
        related_channels, related_categories, business_variables,
        reason, recommended_pool, confidence, llm_reviewed (bool)
    """
    default_result = {
        "llm_relevance": "none",
        "business_relevance_type": "irrelevant",
        "related_channels": [],
        "related_categories": [],
        "business_variables": [],
        "reason": "",
        "recommended_pool": "reject",
        "confidence": "low",
        "llm_reviewed": False,
    }

    if llm_client is None or not llm_client.available:
        logger.warning("LLM 不可用，跳过语义复核")
        default_result["reason"] = "LLM不可用"
        return default_result

    prompt = _build_llm_prompt(article)

    # ── 模型路由：使用模块级单例（已在 Phase 2 入口初始化）──
    _model_for_skill = _MODEL_FOR_FILTER
    _model_params_for_skill = _MODEL_PARAMS_FOR_FILTER or {}

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "你是一个即时零售×个护美妆行业的经营情报分析专家。"
                "你必须严格按照用户要求的JSON格式输出，"
                "不要输出任何解释性文字、不要输出思考过程、"
                "不要使用Markdown代码块包裹，只输出纯JSON。"
            ),
            response_format="json",
            temperature=_model_params_for_skill.get("temperature", 0.2),
            max_tokens=_model_params_for_skill.get("max_tokens", 2048),
            model=_model_for_skill,
        )

        if not result.get("ok"):
            logger.warning(f"LLM 调用失败: {result.get('error', 'unknown')}")
            default_result["reason"] = f"LLM调用失败: {result.get('error', 'unknown')[:100]}"
            return default_result

        parsed = result.get("parsed")
        content = result.get("content", "")

        if parsed and isinstance(parsed, dict):
            parsed["llm_reviewed"] = True
            # 校验必要字段，缺失的用默认值填充
            for field in ["llm_relevance", "business_relevance_type",
                          "recommended_pool", "confidence"]:
                if field not in parsed:
                    parsed[field] = default_result.get(field)
            return parsed
        else:
            # JSON 解析失败 — 记录原始内容以便调试
            content_preview = content[:300] if content else "(空)"
            logger.warning(f"LLM JSON 解析失败, 原始内容前300字: {content_preview}")
            default_result["reason"] = "LLM JSON解析失败"
            default_result["llm_reviewed"] = True
            return default_result

    except Exception as e:
        logger.warning(f"LLM 复核异常: {e}")
        default_result["reason"] = f"LLM异常: {str(e)[:100]}"
        return default_result


# ===================== 最终分池 =====================


def decide_final_pool(
    article: dict,
    rule_score: int,
    rule_decision: str,
    llm_result: Optional[dict],
) -> Tuple[str, str]:
    """决定文章最终属于哪个池。
    
    Returns:
        (final_pool, final_reason)
        final_pool: "main" | "reference" | "reject"
    """
    url = article.get("url", "") or ""
    title = article.get("title", "") or ""
    time_status = article.get("time_status", "")
    matched_keywords = article.get("matched_keywords", [])

    # ── 硬拒绝条件 ──
    if not url.strip():
        return "reject", "url为空"
    if not title.strip():
        return "reject", "title为空"
    if time_status == "old":
        # 搜索源和 allow_old 源的旧文章不算硬拒绝
        # 高质量搜索源旧文章可以直接进 main 池
        search_sources = ("search", "tavily", "gap")
        is_search_source = any(s in (article.get("source_name", "") + article.get("source_type", "")) for s in search_sources)
        allow_old = article.get("allow_old", False)
        if is_search_source or allow_old:
            if rule_score >= 4:
                return "main", f"time_status=old(搜索源/allow_old) score={rule_score}>=4→main"
            elif rule_score >= 2:
                return "reference", f"time_status=old(搜索源/allow_old) score={rule_score}→reference"
            else:
                return "reject", f"time_status=old(搜索源) 且 rule_score={rule_score}<2"
        # 非搜索源：old 但高质量 → reference
        if rule_score >= 6:
            return "reference", "rule_score>=6 但 time_status=old"
        if llm_result and llm_result.get("recommended_pool") == "reference":
            return "reference", "LLM推荐reference 但 time_status=old"
        return "reject", "time_status=old"

    # ── 垃圾内容硬拒绝 ──
    content_lower = " ".join([
        title, article.get("summary", "") or ""
    ]).lower()
    for junk_kw in NEGATIVE_JUNK_KEYWORDS:
        if junk_kw.lower() in content_lower and rule_score <= 1:
            return "reject", f"垃圾内容: {junk_kw}"

    # ── LLM 推荐 ──
    if llm_result and llm_result.get("llm_reviewed"):
        llm_pool = llm_result.get("recommended_pool", "reject")
        llm_conf = llm_result.get("confidence", "low")

        if llm_pool == "main" and rule_score >= 3:
            return "main", f"LLM推荐main (confidence={llm_conf})"
        elif llm_pool == "reference":
            return "reference", f"LLM推荐reference (confidence={llm_conf})"
        elif llm_pool == "reject":
            if rule_score >= 6:
                # 规则认为 keep，但 LLM 认为 reject → 保守放入 reference
                return "reference", f"rule=keep但LLM=reject，降级为reference"
            return "reject", f"LLM推荐reject (confidence={llm_conf})"

    # ── 纯规则决策 ──
    if rule_decision == "keep":
        return "main", f"rule_score={rule_score}>=6"

    if rule_decision == "review":
        # V2: review 文章即使没有 LLM 结果，也要保留为 reference 而非直接 reject
        # 因为 rule_score>=2 意味着至少命中了关键词，很可能有参考价值
        if rule_score >= 4:
            return "reference", f"rule=review, score={rule_score}>=4, 无LLM→reference"
        elif rule_score >= 2:
            # score 2-3 的文章如果 in_window 或 near_window，也保留为 reference
            if time_status in ("in_window", "near_window"):
                return "reference", f"rule=review, score={rule_score}, time={time_status}→reference"
            else:
                return "reference", f"rule=review, score={rule_score}→reference(降级保留)"
        return "reference", f"rule=review, score={rule_score}→reference"

    # rule_decision == "reject"
    # V2: reject 但 in_window 且有内容 → reference而非直接扔掉
    if time_status in ("in_window", "near_window") and rule_score >= 0:
        return "reference", f"rule=reject但time={time_status}→reference(降级保留)"
    return "reject", f"rule_score={rule_score}<3"


def _load_parallel_config(project_root: str) -> dict:
    """加载 parallel.yaml。"""
    try:
        _pp = os.path.join(project_root, "config", "parallel.yaml")
        import yaml
        with open(_pp, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ===================== 主函数 =====================


def filter_relevant_articles(
    project_root: str,
    date: str,
    raw_file: Optional[str] = None,
    keywords_file: str = "config/keywords.yaml",
    scoring_file: str = "config/scoring.yaml",
    use_llm: bool = True,
    llm_mode: str = "borderline_only",
) -> dict:
    """过滤主函数。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        raw_file: 覆盖输入文件路径
        keywords_file: 关键词配置文件（相对项目根）
        scoring_file: 评分配置文件（相对项目根）
        use_llm: 是否使用 LLM 语义复核
        llm_mode: LLM 模式 "borderline_only" 或 "all"
    
    Returns:
        标准结果 dict
    """
    errors: List[str] = []

    # ── 路径 ──
    if not raw_file:
        raw_file = resolve_path(project_root, f"data/raw/{date}/raw_articles.json")
    keywords_path = resolve_path(project_root, keywords_file)
    scoring_path = resolve_path(project_root, scoring_file)
    cleaned_dir = resolve_path(project_root, f"data/cleaned/{date}")
    rejected_dir = resolve_path(project_root, f"data/rejected/{date}")
    log_dir = resolve_path(project_root, f"data/logs/{date}")

    os.makedirs(cleaned_dir, exist_ok=True)
    os.makedirs(rejected_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    cleaned_file = os.path.join(cleaned_dir, "cleaned_articles.json")
    reference_file = os.path.join(cleaned_dir, "reference_articles.json")
    rejected_file = os.path.join(rejected_dir, "rejected_articles.json")
    log_file = os.path.join(log_dir, "filter_relevant_articles.log")

    # ── 日志 ──
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger = logging.getLogger("filter")
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info(f"开始过滤: date={date}")
    logger.info(f"use_llm={use_llm}, llm_mode={llm_mode}")
    logger.info(f"输入文件: {raw_file}")
    logger.info("=" * 60)

    # ── 加载原始数据 ──
    try:
        with open(raw_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        error_msg = f"无法加载原始数据: {e}"
        logger.error(error_msg)
        logger.removeHandler(file_handler)
        file_handler.close()
        return {"ok": False, "date": date, "input_file": raw_file,
                "cleaned_file": cleaned_file, "reference_file": reference_file,
                "rejected_file": rejected_file, "log_file": log_file,
                "raw_count": 0, "cleaned_count": 0, "reference_count": 0,
                "rejected_count": 0, "llm_reviewed_count": 0, "errors": [error_msg]}

    articles = raw_data.get("articles", [])
    raw_count = len(articles)
    logger.info(f"加载原始文章: {raw_count} 条")

    # ── 加载关键词配置 ──
    try:
        keywords_config = load_yaml(keywords_path)
    except Exception as e:
        logger.warning(f"无法加载 keywords.yaml: {e}，使用内置关键词")
        keywords_config = {}

    # ── 加载评分配置（暂未直接使用，预留） ──
    try:
        scoring_config = load_yaml(scoring_path)
    except Exception as e:
        logger.warning(f"无法加载 scoring.yaml: {e}")
        scoring_config = {}

    # ── LLM 客户端 ──
    llm_client = None
    llm_reviewed_count = 0
    llm_failed_count = 0

    if use_llm:
        try:
            # 导入 LLM 客户端
            utils_path = os.path.join(project_root, "skills", "utils")
            if utils_path not in sys.path:
                sys.path.insert(0, project_root)
            from skills.utils.llm_client import get_llm_client
            llm_client = get_llm_client()
            if llm_client.available:
                logger.info(f"LLM 客户端就绪: {llm_client.available_keys} 个 Key 可用, 模型={llm_client.model}")
            else:
                logger.warning("LLM 客户端无可用 Key，降级为纯规则过滤")
                llm_client = None
                use_llm = False
        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}，降级为纯规则过滤")
            llm_client = None
            use_llm = False

    # ── Phase 1: 规则评分（快速，串行） ──
    cleaned_articles: List[dict] = []
    reference_articles: List[dict] = []
    rejected_articles: List[dict] = []

    by_time_status = Counter()
    by_source = Counter()
    reject_reasons = Counter()
    all_matched_keywords = Counter()

    # 中间结果：存储每篇文章的规则评分和是否需要 LLM
    _phase1_results = []  # list of (article, rule_score, rule_reasons, rule_decision, need_llm)
    llm_reviewed_count = 0
    llm_failed_count = 0

    for i, article in enumerate(articles):
        source_name = article.get("source_name", "unknown")
        time_status = article.get("time_status", "unknown_time")
        by_time_status[time_status] += 1
        by_source[source_name] += 1

        for kw in article.get("matched_keywords", []):
            all_matched_keywords[str(kw)] += 1

        rule_score, rule_reasons = compute_rule_score(article)
        rule_decision = make_rule_decision(rule_score)

        # 判断是否需要 LLM 复核
        need_llm = False
        if use_llm and llm_client:
            if llm_mode == "borderline_only":
                if rule_decision == "review":
                    need_llm = True
                # source_tier 高但 rule_score 极低 → 可能是关键词未匹配，LLM 复核
                elif _get_source_tier(article) <= 2 and rule_score <= 1:
                    need_llm = True
            elif llm_mode == "all":
                need_llm = True

        _phase1_results.append((article, rule_score, rule_reasons, rule_decision, need_llm))

        if (i + 1) % 100 == 0:
            logger.info(f"  Phase1 进度: {i + 1}/{raw_count}")

    logger.info(f"Phase1 完成: {raw_count} 篇规则评分, 需要LLM复核: "
                f"{sum(1 for _,_,_,_,nl in _phase1_results if nl)} 篇")

    # ── Phase 2: LLM 语义复核（并行批量，配置驱动） ──
    _llm_results = {}  # idx → llm_result dict

    # 从 parallel.yaml 读取配置
    _filter_cfg = {}
    _FILTER_MAX_LLM = 100
    _FILTER_BATCH_SIZE = 5
    try:
        _par_yaml = _load_parallel_config(project_root)
        _filter_cfg = _par_yaml.get("filter_relevant_articles", {}).get("llm_review_parallel", {})
        if _filter_cfg.get("enabled", True):
            _FILTER_MAX_LLM = _filter_cfg.get("max_llm_articles", 100)
            _FILTER_BATCH_SIZE = _filter_cfg.get("batch_size", 5)
            _timeout_single = _filter_cfg.get("single_timeout", 90)
            _model_strategy = _filter_cfg.get("model_strategy", {})
            # Pre-load model strategy into env so llm_review_article uses it
            if _model_strategy.get("default"):
                _MODEL_FOR_FILTER = _model_strategy["default"]
                _MODEL_PARAMS_FOR_FILTER = {
                    "temperature": 0.2,
                    "max_tokens": 2048,
                }
            if _model_strategy.get("skip_thinking") and _MODEL_FOR_FILTER == "LongCat-Flash-Thinking":
                _MODEL_FOR_FILTER = "LongCat-Flash-Lite"
    except Exception:
        pass

    _llm_indices = [idx for idx, (_,_,_,_, nl) in enumerate(_phase1_results) if nl]
    
    # 超限时优先保留高 tier 低 score 的文章（更需要 LLM 协助判断）
    if len(_llm_indices) > _FILTER_MAX_LLM:
        _prioritized = sorted(_llm_indices, key=lambda idx: (
            _get_source_tier(_phase1_results[idx][0]),  # tier 越小优先级越高
            _phase1_results[idx][1],  # score 越低优先级越高（需要 LLM 帮助）
        ))
        _llm_indices = _prioritized[:_FILTER_MAX_LLM]
        logger.info(f"  Phase2 LLM 超限: {len(_prioritized)} → {_FILTER_MAX_LLM} (按 tier/score 优先级截断)")
    if _llm_indices:
        _llm_batch_size = _FILTER_BATCH_SIZE
        _init_model_router(logger)

        for _batch_start in range(0, len(_llm_indices), _llm_batch_size):
            _batch_indices = _llm_indices[_batch_start:_batch_start + _llm_batch_size]
            logger.info(f"  Phase2 LLM 批量: {_batch_start + 1}-{min(_batch_start + _llm_batch_size, len(_llm_indices))}"
                        f"/{len(_llm_indices)}")

            if len(_batch_indices) == 1:
                # 单篇直接处理
                idx = _batch_indices[0]
                article, _, _, _, _ = _phase1_results[idx]
                llm_result = llm_review_article(article, llm_client, logger)
                _llm_results[idx] = llm_result
                if llm_result.get("llm_reviewed"):
                    llm_reviewed_count += 1
                else:
                    llm_failed_count += 1
            else:
                # 并行处理批量
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=len(_batch_indices)) as executor:
                    _futures = {}
                    for idx in _batch_indices:
                        article, _, _, _, _ = _phase1_results[idx]
                        _futures[executor.submit(llm_review_article, article, llm_client, logger)] = idx

                    for future in as_completed(_futures):
                        idx = _futures[future]
                        try:
                            llm_result = future.result(timeout=90)
                            _llm_results[idx] = llm_result
                            if llm_result.get("llm_reviewed"):
                                llm_reviewed_count += 1
                            else:
                                llm_failed_count += 1
                        except Exception as e:
                            logger.warning(f"  Phase2 LLM idx={idx} 超时/异常: {e}")
                            _llm_results[idx] = {"llm_reviewed": False, "reason": str(e)[:100]}
                            llm_failed_count += 1

        logger.info(f"Phase2 完成: {llm_reviewed_count} 成功, {llm_failed_count} 失败")

    # ── Phase 3: 最终分池（快速，串行） ──
    for idx, (article, rule_score, rule_reasons, rule_decision, need_llm) in enumerate(_phase1_results):
        llm_result = _llm_results.get(idx)

        final_pool, final_reason = decide_final_pool(
            article, rule_score, rule_decision, llm_result
        )

        output_article = dict(article)
        output_article["filter"] = {
            "rule_score": rule_score,
            "rule_decision": rule_decision,
            "rule_reasons": rule_reasons,
            "llm_reviewed": llm_result.get("llm_reviewed", False) if llm_result else False,
            "llm_result": llm_result if llm_result else None,
            "final_pool": final_pool,
            "final_reason": final_reason,
        }

        if final_pool == "main":
            cleaned_articles.append(output_article)
        elif final_pool == "reference":
            reference_articles.append(output_article)
        else:
            output_article["filter"]["reject_reason"] = final_reason
            rejected_articles.append(output_article)

        reject_reasons[final_pool] += 1

    # ── 保存输出 ──
    cleaned_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "source_file": raw_file,
            "created_at": datetime.now().isoformat(),
            "total_raw": raw_count,
            "total_cleaned": len(cleaned_articles),
            "total_reference": len(reference_articles),
            "total_rejected": len(rejected_articles),
            "llm_reviewed_count": llm_reviewed_count,
            "llm_failed_count": llm_failed_count,
            "by_time_status": dict(by_time_status),
            "by_source": dict(by_source),
            "top_matched_keywords": dict(all_matched_keywords.most_common(20)),
            "reject_reasons": dict(reject_reasons),
        },
        "articles": cleaned_articles,
    }

    reference_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "source_file": raw_file,
            "created_at": datetime.now().isoformat(),
            "total_raw": raw_count,
            "total_reference": len(reference_articles),
        },
        "articles": reference_articles,
    }

    rejected_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "source_file": raw_file,
            "created_at": datetime.now().isoformat(),
            "total_raw": raw_count,
            "total_rejected": len(rejected_articles),
            "reject_reason_distribution": dict(reject_reasons),
        },
        "articles": rejected_articles,
    }

    for filepath, data in [(cleaned_file, cleaned_data),
                            (reference_file, reference_data),
                            (rejected_file, rejected_data)]:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 日志汇总 ──
    logger.info("=" * 60)
    logger.info("过滤完成汇总:")
    logger.info(f"  raw_count: {raw_count}")
    logger.info(f"  cleaned_count: {len(cleaned_articles)}")
    logger.info(f"  reference_count: {len(reference_articles)}")
    logger.info(f"  rejected_count: {len(rejected_articles)}")
    logger.info(f"  llm_reviewed_count: {llm_reviewed_count}")
    logger.info(f"  llm_failed_count: {llm_failed_count}")
    logger.info(f"  by_time_status: {dict(by_time_status)}")
    logger.info(f"  by_source: {dict(by_source)}")
    logger.info(f"  top_matched_keywords: {dict(all_matched_keywords.most_common(10))}")
    logger.info(f"  reject_reasons: {dict(reject_reasons)}")
    if llm_client:
        status = llm_client.get_status()
        logger.info(f"  LLM status: available_keys={status['available_keys']}, "
                     f"calls={status['total_calls']}, failures={status['total_failures']}")
    logger.info("=" * 60)

    logger.removeHandler(file_handler)
    file_handler.close()

    return {
        "ok": True,
        "date": date,
        "input_file": raw_file,
        "cleaned_file": cleaned_file,
        "reference_file": reference_file,
        "log_file": log_file,
        "raw_count": raw_count,
        "cleaned_count": len(cleaned_articles),
        "reference_count": len(reference_articles),
        "rejected_count": len(rejected_articles),
        "llm_reviewed_count": llm_reviewed_count,
        "errors": errors,
    }


# ===================== CLI =====================


def run_test_llm():
    """运行 LLM 连通性测试。"""
    # 添加项目根目录到 sys.path
    project_root = "/app/working/projects/watsons-retail-intel"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from skills.utils.llm_client import check_llm_config, test_llm_connection

    print("=" * 60)
    print("LongCat LLM 连通性测试")
    print("=" * 60)

    # 1. 配置检查
    config = check_llm_config()
    print()
    print("📋 LLM 配置:")
    print(f"  keys_found:    {config['keys_found']}")
    print(f"  key_count:     {config['key_count']}")
    print(f"  base_url:      {config['base_url']}")
    print(f"  model:         {config['model']}")
    print(f"  endpoint:      {config['endpoint']}")
    print(f"  key_masks:     {config['key_masks']}")

    if not config["keys_found"]:
        print()
        print("❌ 未找到任何 API Key，无法进行连通性测试。")
        print("   请设置环境变量: LONGCAT_API_KEYS 或 longcat / longcat1~5")
        return

    # 2. 连通性测试
    print()
    print("🔄 正在发送测试请求...")
    result = test_llm_connection()

    print()
    print("📊 测试结果:")
    print(f"  llm_config_ok:   {result['llm_config_ok']}")
    print(f"  api_reachable:   {result['api_reachable']}")
    print(f"  model:           {result['model']}")
    print(f"  parsed_json_ok:  {result['parsed_json_ok']}")
    print(f"  error_message:   {result['error_message'] or '(无)'}")

    print()
    if result["api_reachable"] and result["parsed_json_ok"]:
        print("✅ LLM 连通性测试通过！")
    elif result["api_reachable"]:
        print("⚠️  API 可达但 JSON 解析异常，请检查模型输出格式。")
    elif result["llm_config_ok"]:
        print("❌ API 不可达，请检查 base_url 和网络连通性。")
    else:
        print("❌ LLM 配置不完整，请检查环境变量。")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="过滤相关文章 — 规则过滤 + LLM 语义复核",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", required=False, help="项目根目录")
    parser.add_argument("--date", help="日期 YYYY-MM-DD")
    parser.add_argument("--raw-file", default=None, help="覆盖输入文件路径")
    parser.add_argument("--keywords-file", default="config/keywords.yaml",
                        help="关键词配置文件路径（相对项目根）")
    parser.add_argument("--scoring-file", default="config/scoring.yaml",
                        help="评分配置文件路径（相对项目根）")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用LLM (true/false)")
    parser.add_argument("--llm-mode", default="borderline_only",
                        choices=["borderline_only", "all"],
                        help="LLM模式: borderline_only 或 all")
    parser.add_argument("--verbose", action="store_true", help="启用详细日志")
    parser.add_argument("--test-llm", action="store_true",
                        help="运行 LLM 连通性测试并退出")

    args = parser.parse_args()

    # ── LLM 连通性测试模式 ──
    if args.test_llm:
        run_test_llm()
        sys.exit(0)

    # ── 正常过滤模式，需要 --project-root 和 --date ──
    if not args.project_root:
        parser.error("正常模式需要 --project-root 参数")
    if not args.date:
        parser.error("正常模式需要 --date 参数")

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = filter_relevant_articles(
        project_root=args.project_root,
        date=args.date,
        raw_file=args.raw_file,
        keywords_file=args.keywords_file,
        scoring_file=args.scoring_file,
        use_llm=use_llm,
        llm_mode=args.llm_mode,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()