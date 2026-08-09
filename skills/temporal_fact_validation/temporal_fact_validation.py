#!/usr/bin/env python3
"""
temporal_fact_validation.py — 时效判断机制（文章时间 vs 事实时间分离）

解决的问题:
  当前系统主要使用 article.published_at 判断新旧，无法识别
  "今天发布、但正文重新汇总去年旧闻" 的 AI 转载/汇总文章。

两层机制:
  1. article_date_sanity_check   — 文章层日期合理性检查（未来日期拦截）
  2. temporal_fact_validation    — 事实层时间验证（事件时间字段 + 新鲜度分类 + 历史账本比对）

Pipeline 位置:
  extract → temporal_fact_validation → event_novelty_check → score → analyze
  避免旧闻进入昂贵的经营分析步骤。

输出:
  data/events/{date}/events_temporal.json
  data/logs/{date}/temporal_fact_validation.log
  data/logs/{date}/temporal_stats.json
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("temporal_fact_validation")

# ── 时区 ──
CST = timezone(timedelta(hours=8))
UTC = timezone.utc

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

# 未来日期容忍窗口：published_at 超过 now+6h 视为无效未来日期
FUTURE_TOLERANCE_HOURS = 6

# 事实新鲜度状态枚举
FRESHNESS_STATUSES = [
    "new_fact",              # 今天发布、今天/窗口内发生 → 全新事实
    "updated_fact",          # 事实历史存在，今天有新增数据/动作/规则
    "ongoing_fact",          # 持续进行中的事实（跟踪）
    "upcoming_fact",         # 今天宣布、未来某日执行 → 不误杀
    "republished_old_fact",  # 今天发布、事实历史已存在且无新增 delta
    "retrospective_summary", # 今天发布、复盘去年/历史多个事件
    "historical_background", # 旧背景事实（拆事件时单独归入）
    "date_conflict",         # 时间矛盾（如 published_at 为未来）
    "unknown_fact_time",     # 无法确定事实时间
]

# 准入映射（第十节）
FRESHNESS_ELIGIBILITY = {
    "new_fact": "core",
    "updated_fact": "core",
    "upcoming_fact": "core",
    "ongoing_fact": "tracking",
    "historical_background": "reference",
    "retrospective_summary": "reference",
    "republished_old_fact": "reject",
    "date_conflict": "reject",
    "unknown_fact_time": "review",  # 高价值进 temporal review；普通进 reference
}

# 页面类型（第八节）
PAGE_TYPES = [
    "homepage",
    "listing_page",
    "tag_page",
    "category_page",
    "search_page",
    "topic_page",
    "article_page",
    "unknown",
]

# published_at_source 置信度（第七节）
DATE_SOURCE_CONFIDENCE = {
    "json_ld": "high",
    "html_time": "high",
    "og_metadata": "high",
    "url_date": "medium",
    "tavily": "low",
    "search_snippet": "low",
    "": "unknown",
}

# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def resolve_path(project_root: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.abspath(os.path.join(project_root, rel_path))


def _now_cst() -> datetime:
    return datetime.now(CST)


def parse_flexible_date(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """灵活解析日期字符串，支持中文格式。返回 CST datetime 或 None。"""
    if not text or not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    if now is None:
        now = _now_cst()

    # 已带时区的 ISO 格式
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        else:
            dt = dt.astimezone(CST)
        return dt
    except ValueError:
        pass

    # 纯日期格式
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.replace(tzinfo=CST)
        except ValueError:
            continue

    # 中文日期
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CST)
        except ValueError:
            return None

    return None


def date_str(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
# 一、article_date_sanity_check — 文章层日期合理性
# ═══════════════════════════════════════════════════════════════

def article_date_sanity_check(
    article: dict,
    now: Optional[datetime] = None,
) -> dict:
    """文章层日期合理性检查。

    规则:
      - published_at > now + 6h → article_time_status = invalid_future_date
      - published_at 可解析且在过去 → valid
      - published_at 缺失/无法解析 → unknown
      - 附带 published_at_source 置信度评级（第七节）

    Returns:
        dict: article_time_status, published_at_parsed, published_at_source,
              published_at_confidence, sanity_reason
    """
    if now is None:
        now = _now_cst()

    raw = article.get("published_at") or ""
    source = article.get("published_at_source") or article.get("date_source") or ""

    # 归一化 source
    src_norm = source.strip().lower()
    if src_norm in ("tavily", "search_snippet", "url_date", "json_ld", "html_time", "og_metadata"):
        confidence = DATE_SOURCE_CONFIDENCE.get(src_norm, "unknown")
    elif src_norm:
        confidence = "unknown"
    else:
        confidence = "unknown"

    parsed = parse_flexible_date(raw, now)

    if parsed is None:
        return {
            "article_time_status": "unknown",
            "published_at_parsed": "",
            "published_at_source": source or "unknown",
            "published_at_confidence": confidence,
            "sanity_reason": "published_at 缺失或无法解析",
        }

    if parsed > now + timedelta(hours=FUTURE_TOLERANCE_HOURS):
        return {
            "article_time_status": "invalid_future_date",
            "published_at_parsed": parsed.isoformat(),
            "published_at_source": source or "unknown",
            "published_at_confidence": confidence,
            "sanity_reason": (
                f"published_at={date_str(parsed)} 超过当前时间+{FUTURE_TOLERANCE_HOURS}h "
                f"(now={date_str(now)}) → 未来日期，不得进入主事件池"
            ),
        }

    return {
        "article_time_status": "valid",
        "published_at_parsed": parsed.isoformat(),
        "published_at_source": source or "unknown",
        "published_at_confidence": confidence,
        "sanity_reason": f"published_at={date_str(parsed)} 正常",
    }


# ═══════════════════════════════════════════════════════════════
# 页面类型识别（第八节）
# ═══════════════════════════════════════════════════════════════

_PAGE_TYPE_URL_PATTERNS = [
    (r"(?:^|/)(?:tags?|tagged|topic|topics?)/", "tag_page"),
    (r"(?:^|/)(?:category|categories|channel|section|column|subject)/", "category_page"),
    (r"(?:^|/)(?:search|s\?|query|so\?|find)", "search_page"),
    (r"(?:^|/)(?:list|listing|index|archive|news|资讯|新闻)(?:/|$)", "listing_page"),
    (r"(?:^|/)(?:page|p)\d+", "listing_page"),
]


def classify_page_type(article: dict) -> str:
    """识别页面类型。列表/标签/分类/搜索页不得直接成为新闻事件。"""
    url = (article.get("url") or "").lower()
    title = (article.get("title") or "").strip()

    # URL 模式
    for pattern, ptype in _PAGE_TYPE_URL_PATTERNS:
        if re.search(pattern, url):
            return ptype

    # 标题特征
    if re.search(r"^(首页|主页|home)$", title, re.I):
        return "homepage"
    if re.search(r"(标签|话题|专题)\s*[:：]?", title) and len(title) < 30:
        return "tag_page"
    if re.search(r"^(分类|栏目|频道|资讯列表|全部文章)", title) and len(title) < 30:
        return "listing_page"

    # 有正文内容且像文章 → article_page
    content = article.get("content") or article.get("article_content") or ""
    if isinstance(content, str) and len(content.strip()) > 200:
        return "article_page"

    return "unknown"


# ═══════════════════════════════════════════════════════════════
# 正文日期提取（第五节）— 用于 historical_date_ratio
# ═══════════════════════════════════════════════════════════════

_ABS_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"),
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月"),
]

_REL_TIME_PATTERNS = [
    (re.compile(r"今天"), 0),
    (re.compile(r"今日"), 0),
    (re.compile(r"昨天"), 1),
    (re.compile(r"昨日"), 1),
    (re.compile(r"前天"), 2),
    (re.compile(r"(\d+)\s*天前"), None),
    (re.compile(r"(\d+)\s*小时前"), None),
    (re.compile(r"(\d+)\s*分钟前"), None),
    (re.compile(r"(\d+)\s*周前"), None),
]

_CAMPAIGN_PATTERNS = [
    (re.compile(r"今年\s*(618|双\s*11|双\s*12|年货节|双旦|女神节|三八|情人节|520|圣诞|春节|国庆|中秋)"), "this_year"),
    (re.compile(r"(618|双\s*11|双\s*12|年货节|双旦|女神节|三八|情人节|520|圣诞|春节|国庆|中秋)"), "unspecified_year"),
]


def extract_content_dates(
    text: str,
    now: Optional[datetime] = None,
    article_date: Optional[str] = None,
) -> Dict[str, Any]:
    """从正文/事实提取所有明确日期与相对时间。

    Returns:
        {
          "absolute_dates": [datetime 列表],
          "relative_dates": [datetime 列表],
          "campaign_refs": [str],
          "total_mentions": int,
          "historical_mentions": int,
          "recent_mentions": int,
          "historical_date_ratio": float,
          "oldest_date": str,
          "newest_date": str,
        }
    """
    if now is None:
        now = _now_cst()
    if not text:
        return {
            "absolute_dates": [], "relative_dates": [], "campaign_refs": [],
            "total_mentions": 0, "historical_mentions": 0, "recent_mentions": 0,
            "historical_date_ratio": 0.0, "oldest_date": "", "newest_date": "",
        }

    abs_dates: List[datetime] = []
    rel_dates: List[datetime] = []
    campaign_refs: List[str] = []
    this_year_dates: List[datetime] = []

    # 绝对日期 — 先匹配完整"年月日"，挖掉后避免"年月"模式重复匹配
    work_text = text
    full_date_re = _ABS_DATE_PATTERNS[0]
    for m in full_date_re.finditer(text):
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CST)
            abs_dates.append(dt)
        except (ValueError, IndexError):
            continue
    work_text = full_date_re.sub(" ", text)
    # 纯数字日期 YYYY-MM-DD 等
    for m in _ABS_DATE_PATTERNS[1].finditer(text):
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=CST)
            abs_dates.append(dt)
        except (ValueError, IndexError):
            continue
    work_text = _ABS_DATE_PATTERNS[1].sub(" ", work_text)
    # 年月模式（在年月日挖掉之后）
    for m in _ABS_DATE_PATTERNS[2].finditer(work_text):
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=CST)
            abs_dates.append(dt)
        except (ValueError, IndexError):
            continue

    # 相对时间
    for pat, offset_days in _REL_TIME_PATTERNS:
        for m in pat.finditer(text):
            if offset_days is not None:
                rel_dates.append(now - timedelta(days=offset_days))
            else:
                num = int(m.group(1))
                unit = m.group(0)
                if "小时" in unit:
                    rel_dates.append(now - timedelta(hours=num))
                elif "分钟" in unit:
                    rel_dates.append(now - timedelta(minutes=num))
                elif "周" in unit:
                    rel_dates.append(now - timedelta(weeks=num))
                else:
                    rel_dates.append(now - timedelta(days=num))

    # 营销节点
    for pat, year_kind in _CAMPAIGN_PATTERNS:
        for m in pat.finditer(text):
            campaign_refs.append(m.group(0))
            if year_kind == "this_year":
                # 今年618/双11 → 映射到今年，作为近期/未来参考
                campaign_name = re.sub(r"\s+", "", m.group(1))
                if "618" in campaign_name:
                    rel_dates.append(datetime(now.year, 6, 18, tzinfo=CST))
                elif "双11" in campaign_name:
                    rel_dates.append(datetime(now.year, 11, 11, tzinfo=CST))
                elif "双12" in campaign_name:
                    rel_dates.append(datetime(now.year, 12, 12, tzinfo=CST))
                # 其他节日节点映射到今年（如年货节→1月，春节→2月附近），一律算今年内
                else:
                    rel_dates.append(datetime(now.year, 6, 1, tzinfo=CST))
            # unspecified_year 仅记录引用，不映射（避免去年618旧文误判）

    all_dates = abs_dates + rel_dates
    if not all_dates:
        return {
            "absolute_dates": [d.isoformat() for d in abs_dates],
            "relative_dates": [d.isoformat() for d in rel_dates],
            "campaign_refs": campaign_refs,
            "total_mentions": 0, "historical_mentions": 0, "recent_mentions": 0,
            "historical_date_ratio": 0.0, "oldest_date": "", "newest_date": "",
        }

    # 旧年份 = 非当前年份（用户定义"正文主要事实属于旧年份"）
    historical = [d for d in all_dates if d.year < now.year]
    # 近期 = 近 7 天窗口内
    window = timedelta(days=7)
    recent = [d for d in all_dates if d >= now - window]
    ratio = len(historical) / len(all_dates) if all_dates else 0.0

    return {
        "absolute_dates": [d.isoformat() for d in abs_dates],
        "relative_dates": [d.isoformat() for d in rel_dates],
        "campaign_refs": campaign_refs,
        "total_mentions": len(all_dates),
        "historical_mentions": len(historical),
        "recent_mentions": len(recent),
        "historical_date_ratio": round(ratio, 3),
        "oldest_date": min(all_dates).strftime("%Y-%m-%d"),
        "newest_date": max(all_dates).strftime("%Y-%m-%d"),
    }


# ═══════════════════════════════════════════════════════════════
# 事件时间字段提取（第一节）
# ═══════════════════════════════════════════════════════════════

def validate_event_times(
    event: dict,
    article_sanity: dict,
    content_dates: Dict[str, Any],
    now: Optional[datetime] = None,
) -> dict:
    """填充事件层时间字段。

    event_occurred_at    — 事件实际发生时间
    event_announced_at   — 事件宣布/公布时间
    event_effective_at   — 事件生效/执行时间（政策规则类）
    event_time_precision — day/week/month/quarter/year/unknown
    event_time_confidence— high/medium/low
    temporal_evidence    — 时间证据（原文片段或说明）
    content_dates        — 正文日期提取结果
    historical_date_ratio— 历史日期占比
    is_retrospective     — 是否为回顾文
    """
    if now is None:
        now = _now_cst()

    # LLM 已在抽取时提供（如果有）
    occurred = event.get("event_occurred_at") or ""
    announced = event.get("event_announced_at") or ""
    effective = event.get("event_effective_at") or ""
    precision = event.get("event_time_precision") or ""
    confidence = event.get("event_time_confidence") or ""

    # 未提供的 → 从正文日期/文章发布时间推断
    occurred_dt = parse_flexible_date(occurred, now)
    announced_dt = parse_flexible_date(announced, now)
    effective_dt = parse_flexible_date(effective, now)

    if occurred_dt is None:
        # 用正文中最新日期作为发生时间近似
        dates = []
        for iso in content_dates.get("absolute_dates", []) + content_dates.get("relative_dates", []):
            d = parse_flexible_date(iso, now)
            if d:
                dates.append(d)
        if dates:
            occurred_dt = max(dates)
            if not precision:
                precision = "day"
            if not confidence:
                confidence = "medium"

    if announced_dt is None and article_sanity.get("published_at_parsed"):
        announced_dt = parse_flexible_date(article_sanity["published_at_parsed"], now)

    if not precision:
        precision = "unknown"
    if not confidence:
        confidence = "unknown"

    temporal_evidence = []
    ev = (event.get("evidence_text") or "")[:200]
    if occurred:
        temporal_evidence.append(f"occurred={occurred}")
    if announced:
        temporal_evidence.append(f"announced={announced}")
    if effective:
        temporal_evidence.append(f"effective={effective}")
    if not temporal_evidence and ev:
        temporal_evidence.append(ev[:80])

    return {
        "event_occurred_at": date_str(occurred_dt),
        "event_announced_at": date_str(announced_dt),
        "event_effective_at": date_str(effective_dt),
        "event_time_precision": precision,
        "event_time_confidence": confidence,
        "temporal_evidence": temporal_evidence,
        "content_dates": content_dates,
        "historical_date_ratio": content_dates.get("historical_date_ratio", 0.0),
        "is_retrospective": False,
    }


# ═══════════════════════════════════════════════════════════════
# 事实新鲜度分类（第三/四/五节）
# ═══════════════════════════════════════════════════════════════

_RETROSPECTIVE_HINT = re.compile(r"回顾|复盘|时间线|盘点|总结|对比|大事记|本文由AI生成|AI生成")

# 事件可能的高价值信号（unknown_fact_time 分流用）
_HIGH_VALUE_SIGNALS = [
    "即时零售", "美团", "京东", "阿里", "淘宝", "天猫", "抖音", "饿了么",
    "拼多多", "快手", "小红书", "屈臣氏", "永辉", "山姆", "盒马",
    "政策", "新规", "补贴", "上线", "发布", "收购", "投资", "开城",
]


def classify_fact_freshness(
    event: dict,
    article_sanity: dict,
    temporal: dict,
    ledger_entry: Optional[dict],
    now: Optional[datetime] = None,
) -> dict:
    """判定 fact_freshness_status（第三/四/五节的核心）。

    结合:
      - 文章发布时间（sanity）
      - 事件时间字段（temporal）
      - 正文历史日期占比（historical_date_ratio）
      - 历史事件账本（ledger_entry 是否存在同一 cluster_key/fact_hash）

    逻辑优先级:
      1. 文章日期无效（未来）→ date_conflict
      2. 事件有未来生效日期 → upcoming_fact
      3. 账本已有同 fact_hash：
           a. 事实有新增 delta（标题/fact 变化）→ updated_fact
           b. 无新增 → republished_old_fact
      4. 账本已有同 cluster_key（不同 fact_hash）→ updated_fact（有更新）
      5. 正文历史占比高 + 无近期日期 → retrospective_summary / historical_background
      6. 事件发生时间在近期窗口 → new_fact
      7. 无法确定 → unknown_fact_time
    """
    if now is None:
        now = _now_cst()

    reasons: List[str] = []
    status = "unknown_fact_time"

    # ── 规则1: 未来日期文章 ──
    if article_sanity.get("article_time_status") == "invalid_future_date":
        status = "date_conflict"
        reasons.append(f"文章日期为未来({article_sanity.get('published_at_parsed')}) → date_conflict")
        return {"fact_freshness_status": status, "reasons": reasons,
                "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}

    # ── 规则2: 未来生效 → upcoming_fact ──
    effective_at = event.get("event_effective_at") or temporal.get("event_effective_at") or ""
    effective_dt = parse_flexible_date(effective_at, now)
    if effective_dt and effective_dt > now:
        status = "upcoming_fact"
        reasons.append(f"事件生效日期 {date_str(effective_dt)} 在未来 → upcoming_fact（不误杀新政策）")
        return {"fact_freshness_status": status, "reasons": reasons,
                "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}

    # ── 规则3/4: 历史账本比对（第四节）──
    if ledger_entry is not None:
        prev_fact_hash = ledger_entry.get("fact_hash", "")
        cur_fact_hash = event.get("fact_hash") or generate_fact_hash(event)
        same_fact = bool(cur_fact_hash) and cur_fact_hash == prev_fact_hash
        same_cluster = True  # ledger_entry 已按 cluster_key 匹配

        has_delta = _detect_new_delta(event, ledger_entry)
        if has_delta.get("has_new_delta"):
            status = "updated_fact"
            reasons.append(f"账本存在同cluster且今天有新增delta({has_delta.get('new_delta_fields', [])}) → updated_fact")
            return {"fact_freshness_status": status, "reasons": reasons,
                    "temporal_eligibility": FRESHNESS_ELIGIBILITY[status],
                    "delta_info": has_delta}

        if same_fact:
            # 事实 hash 完全一致，无新增 → 重复转载/汇总
            first_seen = ledger_entry.get("first_seen_at", "")
            if first_seen and first_seen < (now - timedelta(days=3)).strftime("%Y-%m-%d"):
                status = "republished_old_fact"
                reasons.append(f"账本首次出现 {first_seen}，今天fact_hash一致且无新增delta → republished_old_fact")
            else:
                # 近3天内首次出现 → 算 ongoing（同日重跑/近期延续）
                status = "ongoing_fact"
                reasons.append(f"账本首次出现 {first_seen}（近期），无新增 → ongoing_fact")
            return {"fact_freshness_status": status, "reasons": reasons,
                    "temporal_eligibility": FRESHNESS_ELIGIBILITY[status],
                    "delta_info": has_delta}

        # 同 cluster 不同 fact → 有更新
        status = "updated_fact"
        reasons.append("账本存在同cluster但fact_hash变化 → updated_fact")
        return {"fact_freshness_status": status, "reasons": reasons,
                "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}

    # ── 规则5: 回顾文识别（第五节）──
    hdr = temporal.get("historical_date_ratio", 0.0)
    occurred = temporal.get("event_occurred_at", "")
    occurred_dt = parse_flexible_date(occurred, now)
    recent_mentions = temporal.get("content_dates", {}).get("recent_mentions", 0)

    if hdr >= 0.6 and recent_mentions == 0:
        # 正文主要事实属于旧年份、无近期动作
        title_fact = (event.get("event_title") or "") + " " + (event.get("fact") or "")
        if _RETROSPECTIVE_HINT.search(title_fact):
            status = "retrospective_summary"
            reasons.append(f"historical_date_ratio={hdr} 且含回顾特征 → retrospective_summary")
        else:
            status = "historical_background"
            reasons.append(f"historical_date_ratio={hdr}，事实时间在历史窗口外 → historical_background")
        return {"fact_freshness_status": status, "reasons": reasons,
                "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}

    # ── 规则6: 事件发生时间判定 ──
    if occurred_dt:
        if occurred_dt >= now - timedelta(days=7):
            status = "new_fact"
            reasons.append(f"事件发生时间 {date_str(occurred_dt)} 在近7天窗口内 → new_fact")
            return {"fact_freshness_status": status, "reasons": reasons,
                    "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}
        if occurred_dt.year < now.year:
            # 明确旧年份发生 → 旧背景（如拆事件中的背景部分）
            status = "historical_background"
            reasons.append(f"事件发生时间 {date_str(occurred_dt)} 属于旧年份 → historical_background")
            return {"fact_freshness_status": status, "reasons": reasons,
                    "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}
        if occurred_dt < now - timedelta(days=30):
            # 今年内但超过30天 → 旧背景
            status = "historical_background"
            reasons.append(f"事件发生时间 {date_str(occurred_dt)} 距今>30天 → historical_background")
            return {"fact_freshness_status": status, "reasons": reasons,
                    "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}
        # 今年内 7-30 天 → 延续/ongoing
        status = "ongoing_fact"
        reasons.append(f"事件发生时间 {date_str(occurred_dt)} 在今年7-30天窗口 → ongoing_fact")
        return {"fact_freshness_status": status, "reasons": reasons,
                "temporal_eligibility": FRESHNESS_ELIGIBILITY[status]}

    # ── 规则7: 无法确定 ──
    title_fact = (event.get("event_title") or "") + " " + (event.get("fact") or "")
    is_high_value = any(s in title_fact for s in _HIGH_VALUE_SIGNALS)
    status = "unknown_fact_time"
    reasons.append(f"无法确定事实时间 (occurred={occurred or '空'}, hdr={hdr})")
    return {
        "fact_freshness_status": status,
        "reasons": reasons,
        "temporal_eligibility": "review" if is_high_value else "reference",
        "review_hint": "高价值事件进入 temporal review" if is_high_value else "普通事件进入 reference",
    }


def _detect_new_delta(event: dict, ledger_entry: dict) -> dict:
    """判断今天的事实相比账本历史是否有新增 delta（数据/动作/规则）。

    简单实现: 比较 event_title 与账本 title；fact 长度变化；含"新增/首次/上线/发布/宣布/出台/调整"等动作词。
    """
    new_delta_fields = []
    has_new_delta = False

    ledger_title = (ledger_entry.get("event_title") or "").strip()
    cur_title = (event.get("event_title") or "").strip()
    cur_fact = (event.get("fact") or "").strip()

    if cur_title and ledger_title and cur_title != ledger_title and len(cur_title) > 4:
        new_delta_fields.append("event_title")

    action_words = ["新增", "首次", "上线", "发布", "宣布", "出台", "调整", "升级", "推出", "试点", "落地", "开放"]
    if any(w in cur_fact for w in action_words):
        new_delta_fields.append("fact_action_word")

    if len(cur_fact) > 50 and ledger_entry.get("fact_hash"):
        # fact 更长且 hash 不同 → 可能是更详细的新 delta（保守处理，避免误判）
        pass

    has_new_delta = len(new_delta_fields) > 0
    return {"has_new_delta": has_new_delta, "new_delta_fields": new_delta_fields}


# ═══════════════════════════════════════════════════════════════
# 历史账本加载（复用 event_novelty_check 的账本）
# ═══════════════════════════════════════════════════════════════

def load_ledger(ledger_path: str) -> Dict[str, dict]:
    if not os.path.exists(ledger_path):
        return {}
    ledger = {}
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    key = entry.get("cluster_key", "")
                    if key:
                        ledger[key] = entry
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"加载 ledger 失败: {e}, 使用空账本")
        return {}
    return ledger


def normalize_for_cluster(text: str) -> str:
    text = re.sub(r'\d{4}年\d{1,2}月\d{1,2}日', '', text)
    text = re.sub(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', '', text)
    text = re.sub(r'\d{1,2}月\d{1,2}日', '', text)
    text = re.sub(r'\d+\.?\d*%?', '', text)
    text = re.sub(r'[，。、！？；：""''【】《》（）\[\]{},.!?;:\'"()<>]', '', text)
    text = re.sub(r'\s+', '', text)
    return text.lower().strip()


def generate_cluster_key(event: dict) -> str:
    title = (event.get("event_title") or "").strip()
    event_type = (event.get("event_type") or "unknown").strip()
    entities = event.get("entities") or []
    norm_title = normalize_for_cluster(title)
    title_prefix = norm_title[:40] if len(norm_title) > 40 else norm_title
    entity_names = []
    if isinstance(entities, dict):
        for key, vals in entities.items():
            if isinstance(vals, list):
                for v in vals:
                    if isinstance(v, dict):
                        entity_names.append(v.get("name", str(v)))
                    elif isinstance(v, str):
                        entity_names.append(v)
    elif isinstance(entities, list):
        for e in entities:
            if isinstance(e, dict):
                entity_names.append(e.get("name", str(e)))
            elif isinstance(e, str):
                entity_names.append(e)
    entity_names = sorted(set(entity_names))[:5]
    entity_str = "|".join(entity_names)
    raw_key = f"{event_type}:{title_prefix}:{entity_str}"
    if len(raw_key) > 200:
        raw_key = f"{event_type}:{title_prefix}:{hashlib.md5(entity_str.encode()).hexdigest()[:12]}"
    return raw_key


def generate_fact_hash(event: dict) -> str:
    fact = (event.get("fact") or "").strip()
    norm_fact = normalize_for_cluster(fact)
    return hashlib.md5(norm_fact.encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

def temporal_fact_validation(
    project_root: str,
    date: str,
    events_file: Optional[str] = None,
    output_file: Optional[str] = None,
    articles_file: Optional[str] = None,
) -> dict:
    """时效判断主函数。

    读取 extract 输出（events_raw.json），对每个事件:
      1. 文章层 date sanity（未来日期拦截）
      2. 页面类型识别（listing/tag/search 等 → 不直接成为事件）
      3. 正文日期提取 + historical_date_ratio
      4. 事件时间字段填充
      5. 账本比对 → fact_freshness_status 分类
      6. 准入标记（core/tracking/reference/reject/review）

    输出 events_temporal.json + 日志 + 统计。
    """
    errors: List[str] = []
    now = _now_cst()

    # ── 路径 ──
    if not events_file:
        events_file = resolve_path(project_root, f"data/events/{date}/events_raw.json")
    if not output_file:
        output_file = resolve_path(project_root, f"data/events/{date}/events_temporal.json")
    if not articles_file:
        articles_file = resolve_path(project_root, f"data/cleaned/{date}/cleaned_articles.json")

    ledger_path = resolve_path(project_root, "data/event_ledger/events_seen.jsonl")
    log_dir = resolve_path(project_root, f"data/logs/{date}")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "temporal_fact_validation.log")

    # ── 日志 ──
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
    logger.addHandler(fh)

    try:
        logger.info("=" * 60)
        logger.info(f"开始时效判断: date={date}")
        logger.info(f"  events_file: {events_file}")
        logger.info(f"  articles_file: {articles_file}")
        logger.info(f"  ledger_path: {ledger_path}")

        # ── 加载事件 ──
        if not os.path.exists(events_file):
            error_msg = f"事件文件不存在: {events_file}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {"ok": False, "date": date, "errors": errors}

        with open(events_file, "r", encoding="utf-8") as f:
            events_data = json.load(f)
        all_events = events_data.get("events", []) if isinstance(events_data, dict) else events_data

        # ── 加载文章（用于正文日期提取）──
        articles_by_id = {}
        if os.path.exists(articles_file):
            try:
                with open(articles_file, "r", encoding="utf-8") as f:
                    adata = json.load(f)
                aitems = adata.get("articles", []) if isinstance(adata, dict) else adata
                for a in aitems:
                    aid = a.get("article_id") or a.get("id") or ""
                    if aid:
                        articles_by_id[aid] = a
            except Exception as e:
                logger.warning(f"加载文章失败（不影响事件处理）: {e}")

        # ── 加载历史账本 ──
        ledger = load_ledger(ledger_path)
        logger.info(f"历史账本: {len(ledger)} 条记录")

        # ── 逐事件处理 ──
        temporal_events = []
        stats = Counter()
        reject_reasons: List[dict] = []
        future_date_articles: List[dict] = []

        for idx, event in enumerate(all_events):
            # 关联文章
            aid = event.get("source_article_id", "")
            article = articles_by_id.get(aid, {})
            if not article:
                article = {
                    "url": event.get("source_url", ""),
                    "title": event.get("source_title", ""),
                    "published_at": event.get("published_at", ""),
                    "content": event.get("fact", "") + " " + (event.get("evidence_text") or ""),
                }

            # 1. 文章层 date sanity
            sanity = article_date_sanity_check(article, now)

            # 2. 页面类型
            page_type = classify_page_type(article)
            if page_type in ("homepage", "listing_page", "tag_page", "category_page", "search_page", "topic_page"):
                logger.info(f"  事件 {event.get('event_id','')} 来自 {page_type} 页面 → 不直接成为新闻事件")
                reject_reasons.append({
                    "event_id": event.get("event_id", ""),
                    "event_title": (event.get("event_title") or "")[:100],
                    "page_type": page_type,
                    "reason": "列表/标签/搜索页仅用于发现 URL，不直接成为事件",
                })
                continue

            # 3. 正文日期提取
            content = article.get("content") or article.get("article_content") or ""
            title = article.get("title") or event.get("event_title") or ""
            text_for_dates = title + " " + content
            content_dates = extract_content_dates(text_for_dates, now, event.get("published_at", ""))

            # 4. 事件时间字段
            temporal = validate_event_times(event, sanity, content_dates, now)

            # 5. 账本比对（用于分类）
            cluster_key = generate_cluster_key(event)
            fact_hash = generate_fact_hash(event)
            ledger_entry = ledger.get(cluster_key)

            # 6. 新鲜度分类
            freshness = classify_fact_freshness(event, sanity, temporal, ledger_entry, now)

            # 组合更新
            updated = dict(event)
            updated["cluster_key"] = cluster_key
            updated["fact_hash"] = fact_hash
            updated["article_time_status"] = sanity["article_time_status"]
            updated["published_at_source"] = sanity["published_at_source"]
            updated["published_at_confidence"] = sanity["published_at_confidence"]
            updated["page_type"] = page_type
            updated["event_occurred_at"] = temporal["event_occurred_at"]
            updated["event_announced_at"] = temporal["event_announced_at"]
            updated["event_effective_at"] = temporal["event_effective_at"]
            updated["event_time_precision"] = temporal["event_time_precision"]
            updated["event_time_confidence"] = temporal["event_time_confidence"]
            updated["temporal_evidence"] = temporal["temporal_evidence"]
            updated["content_dates"] = temporal["content_dates"]
            updated["historical_date_ratio"] = temporal["historical_date_ratio"]
            updated["is_retrospective"] = freshness["fact_freshness_status"] in (
                "retrospective_summary", "historical_background")
            updated["fact_freshness_status"] = freshness["fact_freshness_status"]
            updated["temporal_eligibility"] = freshness["temporal_eligibility"]
            updated["temporal_reasons"] = freshness.get("reasons", [])

            temporal_events.append(updated)
            stats[freshness["fact_freshness_status"]] += 1

            # 记录 Top 20 republished_old_fact
            if freshness["fact_freshness_status"] == "republished_old_fact":
                reject_reasons.append({
                    "event_id": event.get("event_id", ""),
                    "event_title": (event.get("event_title") or "")[:100],
                    "source_url": event.get("source_url", ""),
                    "source_name": event.get("source_name", ""),
                    "published_at": event.get("published_at", ""),
                    "fact_freshness_status": "republished_old_fact",
                    "reason": "; ".join(freshness.get("reasons", []))[:300],
                })

            if sanity["article_time_status"] == "invalid_future_date":
                future_date_articles.append({
                    "event_id": event.get("event_id", ""),
                    "event_title": (event.get("event_title") or "")[:100],
                    "published_at": event.get("published_at", ""),
                    "reason": sanity.get("sanity_reason", ""),
                })

        # ── Top 20 republished_old_fact ──
        republished = [r for r in reject_reasons if r.get("fact_freshness_status") == "republished_old_fact"]
        top20 = republished[:20]

        # ── 保存输出 ──
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        output_data = {
            "metadata": {
                "version": "2.0",
                "date": date,
                "source_file": events_file,
                "created_at": now.isoformat(),
                "total_events": len(temporal_events),
                "freshness_stats": dict(stats),
                "republished_old_fact_count": len(republished),
                "future_date_article_count": len(future_date_articles),
            },
            "events": temporal_events,
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        # ── 统计文件 ──
        stats_file = os.path.join(log_dir, "temporal_stats.json")
        stats_data = {
            "date": date,
            "freshness_stats": dict(stats),
            "republished_old_fact_count": len(republished),
            "republished_old_fact_top20": top20,
            "future_date_articles": future_date_articles,
            "page_type_rejects": len([r for r in reject_reasons if "页面" in r.get("reason", "")]),
        }
        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(stats_data, f, ensure_ascii=False, indent=2)

        # ── 日志输出 ──
        logger.info(f"时效判断完成:")
        logger.info(f"  total_events: {len(temporal_events)}")
        for status, count in sorted(stats.items()):
            logger.info(f"  {status}: {count}")
        logger.info(f"  republished_old_fact: {len(republished)} (Top20 见 temporal_stats.json)")
        logger.info(f"  future_date_articles: {len(future_date_articles)}")
        logger.info(f"  输出: {output_file}")

        return {
            "ok": True,
            "date": date,
            "input_file": events_file,
            "output_file": output_file,
            "log_file": log_file,
            "stats_file": stats_file,
            "event_count": len(temporal_events),
            "freshness_stats": dict(stats),
            "republished_old_fact_count": len(republished),
            "future_date_article_count": len(future_date_articles),
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"时效判断失败: {e}", exc_info=True)
        errors.append(str(e))
        return {"ok": False, "date": date, "errors": errors}
    finally:
        logger.removeHandler(fh)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="时效判断（文章时间 vs 事实时间）")
    parser.add_argument("--project-root", default=_PROJECT_ROOT)
    parser.add_argument("--date", required=True)
    parser.add_argument("--events-file", default=None)
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--articles-file", default=None)
    args = parser.parse_args()

    result = temporal_fact_validation(
        project_root=args.project_root,
        date=args.date,
        events_file=args.events_file,
        output_file=args.output_file,
        articles_file=args.articles_file,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        sys.exit(1)
