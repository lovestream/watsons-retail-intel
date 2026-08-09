#!/usr/bin/env python3
"""periodic_synthesis — 周期级聚合层。

将每日事件池转换为周期级经营情报（趋势、聚类、对比、分章证据），
输出 data/periodic/{report_type}/{period_id}/periodic_synthesis.json，
作为周报/月报/年报生成的唯一标准化输入。

三批整改 · 第一批核心：1) 周期覆盖度检查  2) 按事件时间过滤周期边界
3) 周期事件聚类（cluster_key/fact_hash/实体/标题）  4) 规则化趋势方向
5) 与上一周期比较  6) 分层配额  7) 分章证据包  8) event_id 内部保留
9) 事实与分析提示分离
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import yaml
except Exception:
    yaml = None

logger = logging.getLogger(__name__)
LOG_PREFIX = "[PeriodicSynthesis]"

# ── 章节定义（与 generate_periodic_report/prompts.py 保持一致） ──
_CHAPTER_IDS = {
    "weekly": ["01", "02", "03", "04", "05", "06"],
    "monthly": ["01", "02", "03", "04", "05", "06", "07"],
    "yearly": ["01", "02", "03", "04", "05", "06", "07", "08"],
}

# 章节 → 事件维度映射（用于 chapter_evidence_pack 分配证据）
_CHAPTER_DIMENSION = {
    "weekly": {
        "01": "all", "02": "all", "03": "platform", "04": "competitor",
        "05": "all", "06": "all",
    },
    "monthly": {
        "01": "all", "02": "platform", "03": "category", "04": "competitor",
        "05": "all", "06": "all", "07": "risk",
    },
    "yearly": {
        "01": "all", "02": "platform", "03": "category", "04": "platform",
        "05": "competitor", "06": "all", "07": "all", "08": "risk",
    },
}

# ── 动作词表（用于聚类：主体 + 平台 + 动作词） ──
_ACTION_WORDS = [
    "上线", "推出", "发布", "加码", "投入", "收购", "整合", "合并", "合作",
    "补贴", "降价", "扩张", "入驻", "试点", "开放", "测试", "升级", "调整",
    "增长", "下滑", "翻倍", "布局", "成立", "关闭", "撤出", "整改", "召回",
    "涨价", "限量", "首发", "联名", "签约", "融资", "上市", "开店", "关店",
    "更名", "重组", "拆分", "剥离", "聚焦", "转型", "提速", "降温", "加速",
]

_MAINLAND_OK = True  # 采集层已做大陆过滤，此处不再重复


# ══════════════════════════════════════════════════════════════
# 配置加载
# ══════════════════════════════════════════════════════════════

def _load_config(project_root: str) -> Dict[str, Any]:
    cfg_path = os.path.join(project_root, "config", "periodic_report.yaml")
    default = {
        "coverage": {
            "weekly": {"normal": 0.85, "minimum": 0.70},
            "monthly": {"normal": 0.90, "minimum": 0.80},
            "yearly": {"normal": 0.95, "minimum": 0.90},
        },
        "source_caps": {
            "weekly": {"max_clusters": 80, "per_day_events": 15},
            "monthly": {"max_clusters": 150, "per_day_events": 20},
            "yearly": {"max_clusters": 240, "per_day_events": 15},
        },
        "quota": {
            "weekly": {"platform": 3, "competitor": 2, "category": 2, "risk": 1},
            "monthly": {"platform": 6, "competitor": 4, "category": 4, "risk": 2},
            "yearly": {"platform": 10, "competitor": 6, "category": 8, "risk": 3,
                       "min_months": 12},
        },
        "clustering": {"title_jaccard_threshold": 0.65,
                       "entity_overlap_threshold": 0.6},
        "trend": {"accel_cluster_ratio": 1.5, "min_days_active": 3,
                  "min_source_count": 3},
    }
    if yaml is None or not os.path.exists(cfg_path):
        return default
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        pr = data.get("periodic_report") or {}
        for k, v in pr.items():
            if isinstance(v, dict):
                default.setdefault(k, {})
                if isinstance(default.get(k), dict):
                    default[k].update(v)
                else:
                    default[k] = v
            else:
                default[k] = v
    except Exception as e:
        logger.warning("%s 配置加载失败 %s: %s", LOG_PREFIX, cfg_path, e)
    return default


# ══════════════════════════════════════════════════════════════
# 日期/时间工具
# ══════════════════════════════════════════════════════════════

def parse_time(value: Any) -> Optional[datetime]:
    """解析事件时间字段（published_at / first_seen_at / collected_at）。

    支持:
      - RFC 2822: "Sun, 09 Aug 2026 00:00:00 +0800"
      - ISO: "2026-08-09T00:00:00+08:00" / "2026-08-09 00:00:00"
      - 纯日期: "2026-08-09"
      - 时间戳: 1784688000（秒）
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    # RFC 2822
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_CST_TZ)
        return dt
    except Exception:
        pass
    # ISO 8601 (Python 3.11+ 支持 Z，兜底手动替换)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_CST_TZ)
        return dt
    except Exception:
        pass
    # 纯日期 / 空格分隔
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d",
                "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=_CST_TZ)
        except Exception:
            continue
    return None


from datetime import timezone as _dt_timezone
_CST_TZ = _dt_timezone(timedelta(hours=8))


def get_effective_event_time(e: dict) -> Tuple[Optional[datetime], str]:
    """返回 (effective_event_time, source)。

    优先级: published_at → first_seen_at → collected_at
    """
    for field in ("published_at", "first_seen_at", "collected_at"):
        dt = parse_time(e.get(field))
        if dt is not None:
            return dt, field
    return None, ""


def _norm_title(s: str) -> str:
    """标题归一化：去空白、小写、去标点。"""
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s.lower()


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_jaccard(t1: str, t2: str) -> float:
    n1, n2 = _norm_title(t1), _norm_title(t2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0
    # 3-gram 集合
    def ngrams(s: str, n: int = 3) -> set:
        return {s[i:i + n] for i in range(len(s) - n + 1)}
    return _jaccard(ngrams(n1), ngrams(n2))


def _extract_actions(text: str) -> List[str]:
    if not text:
        return []
    return [w for w in _ACTION_WORDS if w in text]


def _entity_overlap(e1: dict, e2: dict) -> float:
    ent1 = e1.get("entities") or {}
    ent2 = e2.get("entities") or {}
    platforms1 = set(ent1.get("platforms") or [])
    platforms2 = set(ent2.get("platforms") or [])
    companies1 = set(ent1.get("companies") or [])
    companies2 = set(ent2.get("companies") or [])
    brands1 = set(ent1.get("brands") or [])
    brands2 = set(ent2.get("brands") or [])
    s1 = platforms1 | companies1 | brands1
    s2 = platforms2 | companies2 | brands2
    return _jaccard(s1, s2)


# 财经噪音二次拦截：复用 score_events 的股票/竞对IPO/港台海外判断，
# 防止上游漏标 ARCHIVE 的研报/股价/财报类事件进入周期聚合。
try:
    from skills.score_events.score_events import (
        _is_stock_market_event, _is_corporate_finance_event,
        _is_non_mainland_subject_event)
except Exception:
    _is_stock_market_event = None
    _is_corporate_finance_event = None
    _is_non_mainland_subject_event = None


def _is_archive_event(e: dict) -> bool:
    """与 generate_periodic_report 相同的归档判断 + 财经噪音二次拦截。"""
    if not isinstance(e, dict):
        return True
    if e.get("reject") is True or e.get("rejected") is True:
        return True
    if e.get("priority") == "ARCHIVE":
        return True
    if _is_stock_market_event is not None and _is_stock_market_event(e):
        return True
    if _is_corporate_finance_event is not None and _is_corporate_finance_event(e):
        return True
    if _is_non_mainland_subject_event is not None and \
       _is_non_mainland_subject_event(e):
        return True
    return False


# ══════════════════════════════════════════════════════════════
# 1. period_coverage_check — 周期覆盖度检查
# ══════════════════════════════════════════════════════════════

def _period_dates(report_type: str, period_info: dict) -> List[str]:
    """展开周期内的自然日列表（用于覆盖度检查）。"""
    if report_type == "weekly":
        start = datetime.strptime(period_info["start"], "%Y-%m-%d").date()
        end = datetime.strptime(period_info["end"], "%Y-%m-%d").date()
    elif report_type == "monthly":
        y, m = int(period_info["year"]), int(period_info["month"])
        start = date(y, m, 1)
        if m == 12:
            end = date(y + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(y, m + 1, 1) - timedelta(days=1)
    elif report_type == "yearly":
        start = date(int(period_info["year"]), 1, 1)
        end = date(int(period_info["year"]), 12, 31)
    else:
        return []
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def period_coverage_check(report_type: str, period_info: dict,
                          project_root: str) -> Dict[str, Any]:
    """检查周期内每天的 events 数据覆盖情况。

    Returns:
        {
            "expected_days": int, "available_days": int,
            "healthy_days": int, "coverage_ratio": float,
            "missing_dates": [], "degraded_dates": [],
            "status": "normal"|"degraded"|"failing",
            "minimum": float, "normal": float,
        }
    """
    dates = _period_dates(report_type, period_info)
    cfg = _load_config(project_root)
    cov_cfg = cfg.get("coverage", {}).get(report_type, {})
    normal_th = cov_cfg.get("normal", 0.85)
    min_th = cov_cfg.get("minimum", 0.70)

    missing, degraded, healthy = [], [], 0
    for d in dates:
        ev_path = os.path.join(project_root, "data", "events", d,
                               "events_analyzed.json")
        ev_path2 = os.path.join(project_root, "data", "events", d,
                                "events_scored_novelty.json")
        manifest_path = os.path.join(project_root, "data", "runs", d,
                                     "run_manifest.json")
        ok = False
        degraded_flag = False
        if os.path.exists(ev_path):
            try:
                with open(ev_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                evs = data.get("events", []) if isinstance(data, dict) else []
                if isinstance(evs, list) and evs:
                    ok = True
                else:
                    degraded_flag = True
            except Exception:
                degraded_flag = True
        elif os.path.exists(ev_path2):
            ok = True  # 旧数据回退：文件存在即可视为可用
        if not ok:
            # 检查 run_manifest：是否人为停机（如 08-04~07 用户要求不生成日报）
            manual_stop = False
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        mf = json.load(f)
                    if mf.get("manual_stop") is True or \
                       mf.get("reason") == "manual_stop" or \
                       mf.get("skip_reason") == "manual":
                        manual_stop = True
                except Exception:
                    pass
            if not manual_stop:
                if os.path.exists(ev_path) or os.path.exists(ev_path2):
                    degraded.append(d)
                    degraded_flag = True
                else:
                    missing.append(d)
            else:
                # 人为停机不计入失败（覆盖度豁免）
                healthy += 0
                continue
        if ok:
            healthy += 1

    expected = len(dates)
    available = healthy + len(degraded)
    ratio = (healthy + len(degraded)) / expected if expected else 0.0
    # 有效覆盖 = 健康 + degraded（有数据但不完美）；missing 不算
    effective_ratio = healthy / expected if expected else 0.0

    if ratio >= normal_th:
        status = "normal"
    elif ratio >= min_th:
        status = "degraded"
    else:
        status = "failing"

    return {
        "expected_days": expected,
        "available_days": available,
        "healthy_days": healthy,
        "degraded_days": len(degraded),
        "coverage_ratio": round(effective_ratio, 4),
        "raw_ratio": round(ratio, 4),
        "missing_dates": missing,
        "degraded_dates": degraded,
        "status": status,
        "normal": normal_th,
        "minimum": min_th,
    }


# ══════════════════════════════════════════════════════════════
# 2. period_time_filter — 按事件时间过滤周期边界
# ══════════════════════════════════════════════════════════════

def _period_boundaries(report_type: str,
                       period_info: dict) -> Tuple[datetime, datetime]:
    """返回 (start, end) 带时区的 datetime。end 为开区间。"""
    if report_type == "weekly":
        start = datetime.strptime(period_info["start"], "%Y-%m-%d").replace(
            tzinfo=_CST_TZ)
        end_d = datetime.strptime(period_info["end"], "%Y-%m-%d").replace(
            tzinfo=_CST_TZ) + timedelta(days=1)
        return start, end_d
    if report_type == "monthly":
        y, m = int(period_info["year"]), int(period_info["month"])
        start = datetime(y, m, 1, tzinfo=_CST_TZ)
        if m == 12:
            end = datetime(y + 1, 1, 1, tzinfo=_CST_TZ)
        else:
            end = datetime(y, m + 1, 1, tzinfo=_CST_TZ)
        return start, end
    if report_type == "yearly":
        y = int(period_info["year"])
        return datetime(y, 1, 1, tzinfo=_CST_TZ), datetime(y + 1, 1, 1,
                                                          tzinfo=_CST_TZ)
    raise ValueError("unknown report type: %s" % report_type)


def period_time_filter(events: List[dict], report_type: str,
                       period_info: dict) -> List[dict]:
    """按 effective_event_time 过滤周期边界。

    多读取前后各 1 天事件池，保证月末/周末边界不错位。
    无时间字段的事件：保留（fallback 到文件日期已由调用方控制）。
    """
    start, end = _period_boundaries(report_type, period_info)
    out = []
    kept_without_time = 0
    for e in events:
        dt, src = get_effective_event_time(e)
        if dt is None:
            # 无时间字段：回退到文件日期（事件池文件名），保证周期边界仍可判断
            file_d = e.get("_file_date") or ""
            if file_d:
                fdt = parse_time(file_d)
                if fdt is not None:
                    if start <= fdt < end:
                        e2 = dict(e)
                        e2["effective_event_time"] = fdt.isoformat()
                        e2["effective_time_source"] = "file_date"
                        out.append(e2)
                    # 文件日期明确不在周期内 → 丢弃（不误保留跨边界事件）
                    continue
            # 连文件日期都没有：保留（避免误删）
            e2 = dict(e)
            e2["effective_event_time"] = None
            e2["effective_time_source"] = "unknown"
            out.append(e2)
            kept_without_time += 1
            continue
        if start <= dt < end:
            e2 = dict(e)
            e2["effective_event_time"] = dt.isoformat()
            e2["effective_time_source"] = src
            out.append(e2)
    if kept_without_time:
        logger.info("%s 无时间字段保留 %d 条", LOG_PREFIX, kept_without_time)
    return out


# ══════════════════════════════════════════════════════════════
# 3. period_event_cluster — 周期事件聚类
# ══════════════════════════════════════════════════════════════

def build_cluster_key(e: dict) -> str:
    """基于 主体 + 平台 + 动作词 + 主题 构建 cluster_key。"""
    ent = e.get("entities") or {}
    platforms = sorted(set(ent.get("platforms") or []))
    companies = sorted(set(ent.get("companies") or []))
    subjects = platforms + companies
    etype = (e.get("event_type") or "动态").strip()
    text = (e.get("event_title") or "") + (e.get("fact") or "")
    actions = sorted(set(_extract_actions(text)))
    parts = subjects + [etype] + actions
    return "|".join(parts) if parts else "未分类"


def fact_hash(e: dict) -> str:
    """基于 fact/event_title 归一化文本的哈希。"""
    fact = (e.get("fact") or e.get("event_title") or "").strip()
    norm = _norm_title(fact)
    if not norm:
        return ""
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def cluster_events(events: List[dict], cfg: Dict[str, Any]) -> List[dict]:
    """四级聚类：

    1. cluster_key 完全一致 → 同一 cluster
    2. fact_hash 一致 → 同一 cluster
    3. 实体重合 >= threshold 且动作词有交集 → 同一 cluster
    4. 标题 Jaccard >= threshold → 同一 cluster
    """
    title_th = cfg.get("clustering", {}).get("title_jaccard_threshold", 0.65)
    ent_th = cfg.get("clustering", {}).get("entity_overlap_threshold", 0.6)

    clusters: List[dict] = []

    def _find_cluster(e: dict) -> Optional[dict]:
        ck = build_cluster_key(e)
        fh = fact_hash(e)
        ent1 = set((e.get("entities") or {}).get("platforms", [])) | \
               set((e.get("entities") or {}).get("companies", [])) | \
               set((e.get("entities") or {}).get("brands", []))
        actions1 = set(_extract_actions(
            (e.get("event_title") or "") + (e.get("fact") or "")))
        t1 = (e.get("event_title") or e.get("fact") or "")
        for c in clusters:
            # 1. cluster_key 完全一致
            if c["cluster_key"] == ck:
                return c
            # 2. fact_hash 一致
            if fh and fh == c.get("fact_hash"):
                return c
            # 3. 实体重合 + 动作词交集
            ent2 = set((c.get("entities") or {}).get("platforms", [])) | \
                   set((c.get("entities") or {}).get("companies", [])) | \
                   set((c.get("entities") or {}).get("brands", []))
            if _jaccard(ent1, ent2) >= ent_th and actions1 and \
               (actions1 & set(c.get("action_words", []))):
                return c
            # 4. 标题相似
            if t1 and _title_jaccard(t1, c.get("rep_title", "")) >= title_th:
                return c
        return None

    for e in events:
        # 按 weighted_score 降序已排序，首个命中保留最高分代表
        c = _find_cluster(e)
        ent = e.get("entities") or {}
        eid = e.get("event_id") or ""
        if c is None:
            clusters.append({
                "cluster_id": "PC_%s_%04d" % (
                    (e.get("effective_event_time") or "")[:7].replace("-", ""),
                    len(clusters) + 1),
                "cluster_key": build_cluster_key(e),
                "theme": (e.get("event_title") or e.get("fact") or "")[:60],
                "rep_title": (e.get("event_title") or e.get("fact") or ""),
                "fact_hash": fact_hash(e),
                "first_seen_at": (e.get("effective_event_time") or "")[:10],
                "last_seen_at": (e.get("effective_event_time") or "")[:10],
                "days_active": 1,
                "event_count": 1,
                "source_count": 1,
                "platforms": sorted(set(ent.get("platforms") or [])),
                "competitors": sorted(set(ent.get("competitors") or [])),
                "event_types": sorted(set([e.get("event_type") or "动态"])),
                "business_variables": sorted(set(
                    e.get("business_variables") or [])),
                "action_words": list(_extract_actions(
                    (e.get("event_title") or "") + (e.get("fact") or ""))),
                "entities": {"platforms": sorted(set(ent.get("platforms") or [])),
                             "companies": sorted(set(ent.get("companies") or [])),
                             "brands": sorted(set(ent.get("brands") or []))},
                "representative_event_ids": [eid] if eid else [],
                "event_ids": [eid] if eid else [],
                "facts": [(e.get("fact") or "")[:200]],
                "sources": sorted(set([e.get("source_name") or ""])) if
                           e.get("source_name") else [],
                "source_urls": sorted(set([e.get("source_url") or ""])) if
                               e.get("source_url") else [],
                "max_weighted_score": e.get("weighted_score") or 0,
                "trend_direction": "unclear",
                "confidence": "low",
            })
        else:
            c["event_count"] += 1
            if eid:
                c["event_ids"].append(eid)
                if len(c["representative_event_ids"]) < 5 and \
                   eid not in c["representative_event_ids"]:
                    c["representative_event_ids"].append(eid)
            ts = (e.get("effective_event_time") or "")[:10]
            if ts:
                if not c["first_seen_at"] or ts < c["first_seen_at"]:
                    c["first_seen_at"] = ts
                if ts > c["last_seen_at"]:
                    c["last_seen_at"] = ts
            c["days_active"] = len(set([c["first_seen_at"], c["last_seen_at"]]) -
                                   {""})
            src = e.get("source_name") or ""
            if src:
                c["sources"] = sorted(set(c.get("sources", []) + [src]))
            url = e.get("source_url") or ""
            if url:
                c["source_urls"] = sorted(set(c.get("source_urls", []) + [url]))
            c["source_count"] = len(c.get("sources", []))
            plats = ent.get("platforms") or []
            for p in plats:
                if p not in c["platforms"]:
                    c["platforms"].append(p)
                    c["platforms"] = sorted(c["platforms"])
            comps = ent.get("competitors") or []
            for p in comps:
                if p not in c["competitors"]:
                    c["competitors"].append(p)
                    c["competitors"] = sorted(c["competitors"])
            et = e.get("event_type") or "动态"
            if et not in c["event_types"]:
                c["event_types"].append(et)
            bv = e.get("business_variables") or []
            for b in bv:
                if b not in c["business_variables"]:
                    c["business_variables"].append(b)
            f = (e.get("fact") or "")[:200]
            if f and len(c.get("facts", [])) < 10:
                c.setdefault("facts", []).append(f)
            c["max_weighted_score"] = max(c.get("max_weighted_score") or 0,
                                          e.get("weighted_score") or 0)

    # 排序：加权分高的 cluster 在前
    clusters.sort(key=lambda c: c.get("max_weighted_score") or 0, reverse=True)
    # 重新编号 cluster_id
    for i, c in enumerate(clusters, 1):
        c["cluster_id"] = "PC_%s_%04d" % (
            (c.get("first_seen_at") or "0000-00")[:7].replace("-", ""), i)
    return clusters


# ══════════════════════════════════════════════════════════════
# 4. trend_detection — 规则化趋势方向
# ══════════════════════════════════════════════════════════════

def detect_trend_direction(cluster: dict, prev_clusters: List[dict],
                           cfg: Dict[str, Any]) -> str:
    """规则化趋势方向判定。

    同类匹配规则（严格，避免单实体宽匹配误判）：
      - cluster_key 完全一致
      - 或 实体集合相等 且 动作词有交集
      - 或 标题 Jaccard >= 0.7

    accelerating: 本周期 event_count > 上周期同类 * ratio
                  且 days_active >= 3 且 source_count >= 3
    weakening:    上期同类有多事件(>=3) 且本期明显减少(<= 0.5)
    """
    ratio = cfg.get("trend", {}).get("accel_cluster_ratio", 1.5)
    min_days = cfg.get("trend", {}).get("min_days_active", 3)
    min_src = cfg.get("trend", {}).get("min_source_count", 3)

    def _same_cluster(c: dict, pc: dict) -> bool:
        # 1. cluster_key 完全一致
        ck1 = c.get("cluster_key", "")
        ck2 = pc.get("cluster_key", "")
        if ck1 and ck1 == ck2:
            return True
        # 2. 实体集合相等 且 动作词有交集
        ent1 = set((c.get("entities") or {}).get("platforms", [])) | \
               set((c.get("entities") or {}).get("companies", [])) | \
               set((c.get("entities") or {}).get("brands", []))
        ent2 = set((pc.get("entities") or {}).get("platforms", [])) | \
               set((pc.get("entities") or {}).get("companies", [])) | \
               set((pc.get("entities") or {}).get("brands", []))
        if ent1 and ent1 == ent2:
            act1 = set(c.get("action_words", []))
            act2 = set(pc.get("action_words", []))
            if act1 & act2:
                return True
        # 3. 标题相似
        t1 = c.get("rep_title", "") or c.get("theme", "")
        t2 = pc.get("rep_title", "") or pc.get("theme", "")
        if t1 and t2 and _title_jaccard(t1, t2) >= 0.7:
            return True
        return False

    prev_similar = [pc for pc in prev_clusters if _same_cluster(cluster, pc)]

    days = cluster.get("days_active") or 1
    srcs = cluster.get("source_count") or 1
    events = cluster.get("event_count") or 1
    prev_events = sum(pc.get("event_count") or 0 for pc in prev_similar)

    # 首次出现（上周期无同类）
    if not prev_similar:
        if days >= min_days and srcs >= min_src:
            return "emerging"
        return "unclear"

    if prev_events > 0 and events >= prev_events * ratio and \
       days >= min_days and srcs >= min_src:
        return "accelerating"
    if prev_events >= 3 and events <= prev_events * 0.5:
        return "weakening"
    if prev_events > 0 and abs(events - prev_events) <= max(1, prev_events * 0.2):
        return "stable"
    return "unclear"


# ══════════════════════════════════════════════════════════════
# 5. previous_period_compare — 与上一周期比较
# ══════════════════════════════════════════════════════════════

def _previous_period_dates(report_type: str,
                           period_info: dict) -> Optional[dict]:
    """计算上一周期 period_info。"""
    if report_type == "weekly":
        start = datetime.strptime(period_info["start"], "%Y-%m-%d").date()
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        return {"start": prev_start.strftime("%Y-%m-%d"),
                "end": prev_end.strftime("%Y-%m-%d")}
    if report_type == "monthly":
        y, m = int(period_info["year"]), int(period_info["month"])
        first = date(y, m, 1)
        prev_end = first - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        return {"year": str(prev_start.year), "month": "%02d" % prev_start.month}
    if report_type == "yearly":
        return {"year": str(int(period_info["year"]) - 1)}
    return None


def _load_events_range(start_date: date, end_date: date,
                       project_root: str) -> List[dict]:
    """加载日期范围内所有事件池（多读前后各 1 天由调用方控制）。"""
    out = []
    cur = start_date
    while cur <= end_date:
        d = cur.strftime("%Y-%m-%d")
        path = os.path.join(project_root, "data", "events", d,
                            "events_analyzed.json")
        if not os.path.exists(path):
            path = os.path.join(project_root, "data", "events", d,
                                "events_scored_novelty.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                evs = data.get("events", []) if isinstance(data, dict) else []
                for e in evs:
                    if isinstance(e, dict) and not _is_archive_event(e):
                        e2 = dict(e)
                        # 记录文件日期：无时间字段事件回退用
                        e2["_file_date"] = d
                        out.append(e2)
            except Exception as ex:
                logger.warning("%s 事件文件解析失败 %s: %s", LOG_PREFIX, path, ex)
        cur += timedelta(days=1)
    return out


def previous_period_compare(report_type: str, period_info: dict,
                            project_root: str, cfg: Dict[str, Any]) -> dict:
    """加载上一周期事件，生成比较统计。"""
    prev_info = _previous_period_dates(report_type, period_info)
    if not prev_info:
        return {}
    # 上一周期日期范围
    if report_type == "weekly":
        start = datetime.strptime(prev_info["start"], "%Y-%m-%d").date()
        end = datetime.strptime(prev_info["end"], "%Y-%m-%d").date()
        prev_events = _load_events_range(start, end, project_root)
        prev_events = period_time_filter(prev_events, "weekly", prev_info)
        prev_clusters = cluster_events(prev_events, cfg)
    elif report_type == "monthly":
        y, m = int(prev_info["year"]), int(prev_info["month"])
        start = date(y, m, 1)
        if m == 12:
            end = date(y + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(y, m + 1, 1) - timedelta(days=1)
        prev_events = _load_events_range(start, end, project_root)
        prev_events = period_time_filter(prev_events, "monthly", prev_info)
        prev_clusters = cluster_events(prev_events, cfg)
    elif report_type == "yearly":
        y = int(prev_info["year"])
        prev_events = _load_events_range(date(y, 1, 1), date(y, 12, 31),
                                         project_root)
        prev_events = period_time_filter(prev_events, "yearly", prev_info)
        prev_clusters = cluster_events(prev_events, cfg)
    else:
        return {}

    return {
        "period_info": prev_info,
        "event_count": len(prev_events),
        "cluster_count": len(prev_clusters),
        "high_priority_count": len(
            [e for e in prev_events if e.get("priority") in ("P0", "P1")]),
        "clusters": prev_clusters,
    }


# ══════════════════════════════════════════════════════════════
# 6. 分层配额 + 7. chapter_evidence_pack
# ══════════════════════════════════════════════════════════════

def _cluster_dimension(cluster: dict) -> str:
    """判断 cluster 主维度。"""
    if cluster.get("risk_keywords") or \
       any(k in (cluster.get("theme") or "") for k in
           ("风险", "处罚", "下架", "召回", "合规", "整改")):
        return "risk"
    if cluster.get("platforms"):
        return "platform"
    if cluster.get("competitors"):
        return "competitor"
    return "category"


def select_by_quota(clusters: List[dict], report_type: str,
                    cfg: Dict[str, Any]) -> List[dict]:
    """分层配额选样：

    第一层: P0/P1 cluster 全保留（由事件优先级推导）
    第二层: 按 平台/竞对/品类/风险/月份 最低配额
    第三层: 剩余名额按 weighted_score 填充
    """
    caps = cfg.get("source_caps", {}).get(report_type, {})
    max_clusters = caps.get("max_clusters", 80)
    quota = cfg.get("quota", {}).get(report_type, {})

    # 标记 P0/P1
    for c in clusters:
        high = [e for e in c.get("event_ids", [])]
        c["_is_high"] = c.get("max_weighted_score", 0) >= 3.0 or \
                        any(x in str(c.get("event_types", "")) for x in
                            ("platform_move", "policy", "merger"))

    selected: List[dict] = []
    seen_ids = set()

    def _add(c: dict) -> bool:
        if c["cluster_id"] in seen_ids:
            return False
        if len(selected) >= max_clusters:
            return False
        selected.append(c)
        seen_ids.add(c["cluster_id"])
        return True

    # 第一层: 高优先级 cluster 全保留
    high_clusters = [c for c in clusters if c.get("_is_high")]
    for c in high_clusters:
        _add(c)

    # 第二层: 维度配额
    remaining = [c for c in clusters if c["cluster_id"] not in seen_ids]
    for dim in ("platform", "competitor", "category", "risk"):
        dim_quota = quota.get(dim, 0)
        if dim_quota <= 0:
            continue
        # 该维度未覆盖的 cluster
        dim_pool = [c for c in remaining if _cluster_dimension(c) == dim]
        for c in dim_pool:
            if dim_quota <= 0:
                break
            if _add(c):
                dim_quota -= 1

    # 月份配额（年报）
    min_months = quota.get("min_months", 0)
    if min_months and report_type == "yearly":
        remaining = [c for c in clusters if c["cluster_id"] not in seen_ids]
        months_covered = set()
        for c in selected:
            m = (c.get("first_seen_at") or "")[:7]
            if m:
                months_covered.add(m)
        for c in remaining:
            m = (c.get("first_seen_at") or "")[:7]
            if m and m not in months_covered and len(selected) < max_clusters:
                if _add(c):
                    months_covered.add(m)

    # 第三层: 剩余按 weighted_score 填充
    remaining = [c for c in clusters if c["cluster_id"] not in seen_ids]
    remaining.sort(key=lambda c: c.get("max_weighted_score") or 0,
                   reverse=True)
    for c in remaining:
        _add(c)

    return selected


def build_chapter_evidence(report_type: str, clusters: List[dict],
                           selected: List[dict]) -> Dict[str, List[dict]]:
    """为每个章节分配证据（该章维度的事件/cluster）。"""
    dim_map = _CHAPTER_DIMENSION.get(report_type, {})
    all_ids = [c["cluster_id"] for c in selected]
    evidence: Dict[str, List[dict]] = {}
    for ch_id in _CHAPTER_IDS.get(report_type, []):
        dim = dim_map.get(ch_id, "all")
        if dim == "all":
            evidence[ch_id] = list(selected)
        elif dim == "platform":
            evidence[ch_id] = [c for c in selected if c.get("platforms")]
        elif dim == "competitor":
            evidence[ch_id] = [c for c in selected if c.get("competitors")]
        elif dim == "category":
            evidence[ch_id] = [c for c in selected if not c.get("platforms")
                               and not c.get("competitors")]
        elif dim == "risk":
            evidence[ch_id] = [c for c in selected
                               if _cluster_dimension(c) == "risk"]
        else:
            evidence[ch_id] = list(selected)
    return evidence


# ══════════════════════════════════════════════════════════════
# 主入口: build_periodic_synthesis
# ══════════════════════════════════════════════════════════════

def build_periodic_synthesis(report_type: str, period_info: dict,
                             project_root: str = ".") -> dict:
    """构建周期聚合 JSON。返回 {
        "success": bool, "synthesis": dict, "path": str,
        "coverage_report": dict, "error": str,
    }
    """
    cfg = _load_config(project_root)
    start_date, end_date = _period_boundaries(report_type, period_info)

    # 1. 覆盖度检查
    coverage = period_coverage_check(report_type, period_info, project_root)

    # 2. 加载事件（多读前后各 1 天，按事件时间过滤）
    pad_start = start_date.date() - timedelta(days=1)
    pad_end = end_date.date() + timedelta(days=1)
    all_events = _load_events_range(pad_start, pad_end, project_root)
    period_events = period_time_filter(all_events, report_type, period_info)
    reportable = [e for e in period_events
                  if not _is_archive_event(e) and
                  (e.get("event_title") or e.get("fact"))]
    reportable.sort(key=lambda x: x.get("weighted_score") or 0, reverse=True)

    # 3. 聚类
    clusters = cluster_events(reportable, cfg)

    # 4. 趋势方向（需要上一周期）
    prev = previous_period_compare(report_type, period_info, project_root, cfg)
    prev_clusters = prev.get("clusters", [])
    for c in clusters:
        c["trend_direction"] = detect_trend_direction(c, prev_clusters, cfg)
        c["confidence"] = "high" if c.get("event_count", 0) >= 3 else \
                          ("medium" if c.get("event_count", 0) >= 2 else "low")

    # 5. 分层配额选样
    selected = select_by_quota(clusters, report_type, cfg)

    # 6. 分章证据包
    chapter_evidence = build_chapter_evidence(report_type, clusters, selected)

    # 7. 汇总统计
    high_count = len([c for c in selected if c.get("_is_high")])
    platform_count = len([c for c in selected if c.get("platforms")])
    competitor_count = len([c for c in selected if c.get("competitors")])
    risk_count = len([c for c in selected if _cluster_dimension(c) == "risk"])

    period_label = _make_period_label(report_type, period_info)

    synthesis = {
        "report_type": report_type,
        "period": {
            "start": start_date.isoformat(),
            "end": (end_date - timedelta(seconds=1)).isoformat(),
            "label": period_label,
        },
        "coverage": coverage,
        "event_stats": {
            "raw_events": len(all_events),
            "period_events": len(period_events),
            "reportable_events": len(reportable),
            "clusters": len(clusters),
            "selected_clusters": len(selected),
            "high_priority_clusters": high_count,
            "platform_clusters": platform_count,
            "competitor_clusters": competitor_count,
            "risk_clusters": risk_count,
        },
        "previous_period_comparison": {
            "period_info": prev.get("period_info", {}),
            "event_count": prev.get("event_count", 0),
            "cluster_count": prev.get("cluster_count", 0),
            "high_priority_count": prev.get("high_priority_count", 0),
        },
        "trend_clusters": [c for c in selected if c.get("trend_direction")
                           in ("accelerating", "emerging", "weakening")],
        "channel_summary": _summarize_channels(selected),
        "platform_summary": _summarize_by_key(selected, "platforms"),
        "competitor_summary": _summarize_by_key(selected, "competitors"),
        "category_summary": _summarize_by_key(selected, "event_types"),
        "risk_summary": [c for c in selected if _cluster_dimension(c) == "risk"],
        "opportunity_summary": [
            c for c in selected if c.get("trend_direction") in
            ("emerging", "accelerating")],
        "chapter_evidence": chapter_evidence,
        "all_clusters": selected,
    }

    # 8. 落盘
    out_dir = os.path.join(project_root, "data", "periodic", report_type,
                           _period_id(report_type, period_info))
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "periodic_synthesis.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(synthesis, f, ensure_ascii=False, indent=2)
    logger.info("%s 已写出 %s", LOG_PREFIX, json_path)

    cov_path = os.path.join(out_dir, "coverage_report.json")
    with open(cov_path, "w", encoding="utf-8") as f:
        json.dump(coverage, f, ensure_ascii=False, indent=2)

    md_path = os.path.join(out_dir, "periodic_synthesis.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(_synthesis_to_md(synthesis))

    return {
        "success": True,
        "synthesis": synthesis,
        "path": json_path,
        "coverage_report": coverage,
        "md_path": md_path,
    }


def _period_id(report_type: str, period_info: dict) -> str:
    if report_type == "weekly":
        start = datetime.strptime(period_info["start"], "%Y-%m-%d")
        iso = start.isocalendar()
        return "%d-W%02d" % (iso[0], iso[1])
    if report_type == "monthly":
        return "%s-%s" % (period_info["year"], period_info["month"])
    if report_type == "yearly":
        return period_info["year"]
    return "unknown"


def _make_period_label(report_type: str, period_info: dict) -> str:
    if report_type == "weekly":
        start = period_info["start"]
        end = period_info["end"]
        s = datetime.strptime(start, "%Y-%m-%d")
        week_num = s.isocalendar()[1]
        return "%d-W%02d (%s ~ %s)" % (s.isocalendar()[0], week_num,
                                       start[5:], end[5:])
    if report_type == "monthly":
        return "%s年%d月" % (period_info["year"], int(period_info["month"]))
    if report_type == "yearly":
        return "%s年" % period_info["year"]
    return ""


def _summarize_channels(clusters: List[dict]) -> dict:
    # 渠道群: 即时零售 / B2C 旗舰电商 / ToB经销
    ir = [c for c in clusters if any(
        p in str(c.get("platforms", "")) for p in
        ("美团", "京东秒送", "京东到家", "淘宝", "饿了么", "抖音小时达", "朴朴", "叮咚"))]
    b2c = [c for c in clusters if any(
        p in str(c.get("platforms", "")) for p in ("天猫", "京东旗舰", "旗舰店"))]
    return {
        "即时零售": len(ir),
        "B2C旗舰电商": len(b2c),
        "ToB/经销": 0,
        "clusters_ir": [c["cluster_id"] for c in ir[:20]],
    }


def _summarize_by_key(clusters: List[dict], key: str) -> dict:
    out = {}
    for c in clusters:
        items = c.get(key) or []
        for it in items:
            out.setdefault(it, 0)
            out[it] += 1
    return out


def _synthesis_to_md(synthesis: dict) -> str:
    lines = ["# 周期聚合数据 (%s)" % synthesis.get("period", {}).get("label", "")],
    lines = ["# 周期聚合数据 (%s)" % synthesis.get("period", {}).get("label", "")]
    cov = synthesis.get("coverage", {})
    lines.append("")
    lines.append("## 覆盖度")
    lines.append("- 覆盖: %s/%s 天, ratio=%s, status=%s" % (
        cov.get("available_days", 0), cov.get("expected_days", 0),
        cov.get("coverage_ratio", 0), cov.get("status", "?")))
    if cov.get("missing_dates"):
        lines.append("- 缺失: %s" % ",".join(cov["missing_dates"]))
    if cov.get("degraded_dates"):
        lines.append("- 降级: %s" % ",".join(cov["degraded_dates"]))
    st = synthesis.get("event_stats", {})
    lines.append("")
    lines.append("## 事件统计")
    lines.append("- 事件: %s → 可报告 %s → 聚类 %s → 入选 %s" % (
        st.get("raw_events", 0), st.get("reportable_events", 0),
        st.get("clusters", 0), st.get("selected_clusters", 0)))
    p = synthesis.get("previous_period_comparison", {})
    lines.append("")
    lines.append("## 上一周期比较")
    lines.append("- 上期事件: %s, 上期聚类: %s, 上期高优: %s" % (
        p.get("event_count", 0), p.get("cluster_count", 0),
        p.get("high_priority_count", 0)))
    lines.append("")
    lines.append("## 趋势 Cluster")
    for c in synthesis.get("trend_clusters", []):
        lines.append("- [%s] %s (days=%s, events=%s, direction=%s)" % (
            c.get("cluster_id", ""), c.get("theme", "")[:50],
            c.get("days_active", 0), c.get("event_count", 0),
            c.get("trend_direction", "unclear")))
    lines.append("")
    lines.append("## 入选 Cluster 明细")
    for c in synthesis.get("all_clusters", []):
        lines.append("- [%s] %s (score=%.2f, events=%s)" % (
            c.get("cluster_id", ""), c.get("theme", "")[:60],
            c.get("max_weighted_score", 0), c.get("event_count", 0)))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="构建周期聚合数据")
    parser.add_argument("--type", required=True,
                        choices=["weekly", "monthly", "yearly"])
    parser.add_argument("--start", default="", help="weekly start YYYY-MM-DD")
    parser.add_argument("--end", default="", help="weekly end YYYY-MM-DD")
    parser.add_argument("--year", default="")
    parser.add_argument("--month", default="")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.type == "weekly":
        if not args.start or not args.end:
            print("ERROR: --start and --end required for weekly")
            sys.exit(1)
        period_info = {"start": args.start, "end": args.end}
    elif args.type == "monthly":
        period_info = {"year": args.year, "month": args.month}
    elif args.type == "yearly":
        period_info = {"year": args.year}

    result = build_periodic_synthesis(args.type, period_info,
                                      args.project_root)
    if result.get("success"):
        print("OK synthesis: %s" % result.get("path", ""))
        cov = result.get("coverage_report", {})
        print("coverage: %s/%s ratio=%s status=%s" % (
            cov.get("available_days", 0), cov.get("expected_days", 0),
            cov.get("coverage_ratio", 0), cov.get("status", "?")))
        st = result.get("synthesis", {}).get("event_stats", {})
        print("events: %s reportable=%s clusters=%s selected=%s" % (
            st.get("period_events", 0), st.get("reportable_events", 0),
            st.get("clusters", 0), st.get("selected_clusters", 0)))
    else:
        print("FAIL: %s" % result.get("error", "unknown"))
        sys.exit(1)


if __name__ == "__main__":
    main()
