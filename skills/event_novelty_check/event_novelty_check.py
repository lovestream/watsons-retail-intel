#!/usr/bin/env python3
"""
event_novelty_check.py — 事件新颖性检查，防止日报重复报道同一事件。

读取当日 events_scored.json，与历史 events_seen.jsonl 对比，
为每条事件生成 cluster_key、novelty_status、report_eligibility 等字段。

输出: events_scored_novelty.json + 更新 events_seen.jsonl

用法:
    python3 -m skills.event_novelty_check.event_novelty_check \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-05-03
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

# ── 项目路径 ──
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("novelty_check")


def resolve_path(project_root: str, rel_path: str) -> str:
    """将相对路径转为绝对路径。"""
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.abspath(os.path.join(project_root, rel_path))


# ═══════════════════════════════════════════════════════════════
# cluster_key 生成
# ═══════════════════════════════════════════════════════════════

def normalize_for_cluster(text: str) -> str:
    """将文本规范化用于聚类: 去除日期数字、标点、多余空格。"""
    # 去除日期模式
    text = re.sub(r'\d{4}年\d{1,2}月\d{1,2}日', '', text)
    text = re.sub(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', '', text)
    text = re.sub(r'\d{1,2}月\d{1,2}日', '', text)
    # 去除纯数字(金额、百分比等)
    text = re.sub(r'\d+\.?\d*%?', '', text)
    # 去除标点
    text = re.sub(r'[，。、！？；：""''【】《》（）\[\]{},.!?;:\'"()<>]', '', text)
    # 去除多余空格
    text = re.sub(r'\s+', '', text)
    # 小写
    text = text.lower().strip()
    return text


def generate_cluster_key(event: dict) -> str:
    """为事件生成聚类键。

    策略: 提取标题核心词 + 事件类型 + 主要实体，生成确定性 hash。

    cluster_key 格式: "{event_type}:{normalized_title_prefix}:{entity_hash}"
    """
    title = (event.get("event_title") or "").strip()
    event_type = (event.get("event_type") or "unknown").strip()
    entities = event.get("entities") or []

    # 规范化标题
    norm_title = normalize_for_cluster(title)

    # 提取前30字符作为标题指纹 (规范化后通常很短)
    title_prefix = norm_title[:40] if len(norm_title) > 40 else norm_title

    # 实体 hash (从 entities dict 中提取所有实体名)
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

    # 组合 cluster_key
    raw_key = f"{event_type}:{title_prefix}:{entity_str}"

    # 如果 key 太长，hash 它
    if len(raw_key) > 200:
        raw_key = f"{event_type}:{title_prefix}:{hashlib.md5(entity_str.encode()).hexdigest()[:12]}"

    return raw_key


def generate_fact_hash(event: dict) -> str:
    """为事件的 fact 生成 hash，用于检测事件是否有实质更新。"""
    fact = (event.get("fact") or "").strip()
    norm_fact = normalize_for_cluster(fact)
    return hashlib.md5(norm_fact.encode()).hexdigest()[:12]


# ═══════════════════════════════════════════════════════════════
# 事件账本操作
# ═══════════════════════════════════════════════════════════════

def load_ledger(ledger_path: str) -> Dict[str, dict]:
    """加载事件账本，返回 {cluster_key: ledger_entry}。"""
    if not os.path.exists(ledger_path):
        return {}

    ledger = {}
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    key = entry.get("cluster_key", "")
                    if key:
                        ledger[key] = entry
                except json.JSONDecodeError:
                    logger.warning(f"Ledger line {line_num}: JSON decode error, skipping")
    except Exception as e:
        logger.warning(f"加载 ledger 失败: {e}, 使用空账本")
        return {}

    return ledger


def save_ledger_entry(ledger_path: str, entry: dict):
    """追加一条记录到账本 (JSONL)。"""
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════════
# 新颖性判定
# ═══════════════════════════════════════════════════════════════

def determine_novelty(
    event: dict,
    cluster_key: str,
    fact_hash: str,
    date: str,
    ledger: Dict[str, dict],
) -> dict:
    """根据账本判定事件新颖性。

    Returns:
        dict with keys: novelty_status, first_seen_at, last_seen_at,
                        last_reported_at, report_count, report_eligibility
    """
    existing = ledger.get(cluster_key)

    if existing is None:
        # 从未出现过
        return {
            "novelty_status": "new_today",
            "first_seen_at": date,
            "last_seen_at": date,
            "last_reported_at": None,
            "report_count": 0,
            "report_eligibility": "core",
        }

    # 已存在
    prev_fact_hash = existing.get("fact_hash", "")
    first_seen = existing.get("first_seen_at", date)
    last_seen = existing.get("last_seen_at", date)
    last_reported = existing.get("last_reported_at")
    report_count = existing.get("report_count", 0)

    date_dt = datetime.strptime(date, "%Y-%m-%d")
    last_seen_dt = datetime.strptime(last_seen, "%Y-%m-%d") if last_seen else date_dt

    days_since_seen = (date_dt - last_seen_dt).days

    if fact_hash != prev_fact_hash:
        # 事实有更新
        novelty = "updated_today"
        eligibility = "core"
    elif last_reported and days_since_seen < 3:
        # 近3天已报告且事实未变
        novelty = "repeated"
        eligibility = "reference"
    elif days_since_seen < 3:
        # 近3天见过但未报告
        novelty = "ongoing"
        eligibility = "tracking"
    elif days_since_seen < 7:
        # 3-7天
        novelty = "ongoing"
        eligibility = "tracking"
    else:
        # 7天以上
        novelty = "background"
        eligibility = "archive"

    return {
        "novelty_status": novelty,
        "first_seen_at": first_seen,
        "last_seen_at": date,
        "last_reported_at": last_reported,
        "report_count": report_count,
        "report_eligibility": eligibility,
    }


# ═══════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════

def event_novelty_check(
    project_root: str,
    date: str,
    events_file: Optional[str] = None,
    output_file: Optional[str] = None,
) -> dict:
    """事件新颖性检查主函数。"""
    errors: List[str] = []

    # ── 路径 ──
    if not events_file:
        # 优先读取 events_analyzed.json（包含 business_analysis），fallback 到 events_scored.json
        analyzed_file = resolve_path(project_root, f"data/events/{date}/events_analyzed.json")
        scored_file = resolve_path(project_root, f"data/events/{date}/events_scored.json")
        if os.path.exists(analyzed_file):
            events_file = analyzed_file
        else:
            events_file = scored_file
    if not output_file:
        output_file = resolve_path(project_root, f"data/events/{date}/events_scored_novelty.json")

    ledger_path = resolve_path(project_root, "data/event_ledger/events_seen.jsonl")
    log_dir = resolve_path(project_root, f"data/logs/{date}")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "event_novelty_check.log")

    # ── 日志 ──
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
    logger.addHandler(fh)

    try:
        logger.info("=" * 60)
        logger.info(f"开始事件新颖性检查: date={date}")
        logger.info(f"  events_file: {events_file}")
        logger.info(f"  ledger_path: {ledger_path}")
        logger.info(f"  output_file: {output_file}")

        # ── 加载事件 ──
        if not os.path.exists(events_file):
            error_msg = f"事件文件不存在: {events_file}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {"ok": False, "date": date, "errors": errors}

        with open(events_file, "r", encoding="utf-8") as f:
            events_data = json.load(f)

        all_events = events_data.get("events", [])
        if isinstance(events_data, list):
            all_events = events_data

        logger.info(f"加载 {len(all_events)} 条事件")

        # ── 加载历史账本 ──
        ledger = load_ledger(ledger_path)
        logger.info(f"历史账本: {len(ledger)} 条记录")

        # ── 逐事件检查 ──
        novelty_events = []
        stats = Counter()

        for event in all_events:
            cluster_key = generate_cluster_key(event)
            fact_hash = generate_fact_hash(event)

            novelty_info = determine_novelty(event, cluster_key, fact_hash, date, ledger)

            # 更新事件
            updated_event = dict(event)
            updated_event["cluster_key"] = cluster_key
            updated_event["fact_hash"] = fact_hash
            updated_event.update(novelty_info)

            novelty_events.append(updated_event)

            # 统计
            stats[novelty_info["novelty_status"]] += 1
            stats["total"] += 1

            # 追加到账本 (不管是否新事件都记录当天出现)
            ledger_entry = {
                "cluster_key": cluster_key,
                "event_id": event.get("event_id", ""),
                "event_title": (event.get("event_title") or "")[:100],
                "event_type": event.get("event_type", ""),
                "fact_hash": fact_hash,
                "first_seen_at": novelty_info["first_seen_at"],
                "last_seen_at": novelty_info["last_seen_at"],
                "last_reported_at": novelty_info.get("last_reported_at"),
                "report_count": novelty_info.get("report_count", 0),
                "date": date,
                "priority": event.get("priority", ""),
                "source_url": event.get("source_url", ""),
            }

            # 更新账本中的记录
            if cluster_key in ledger:
                # 已存在 → 更新
                existing_entry = ledger[cluster_key]
                existing_entry["last_seen_at"] = date
                existing_entry["fact_hash"] = fact_hash
                # 不更新 first_seen_at
            else:
                # 新事件 → 追加
                ledger[cluster_key] = ledger_entry

            # 追加到 JSONL 文件
            save_ledger_entry(ledger_path, ledger_entry)

        # ── 保存输出 ──
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        output_data = {
            "metadata": {
                "version": "1.0",
                "date": date,
                "source_file": events_file,
                "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "total_events": len(novelty_events),
                "novelty_stats": dict(stats),
            },
            "events": novelty_events,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"新颖性检查完成:")
        logger.info(f"  total: {len(novelty_events)}")
        for status, count in sorted(stats.items()):
            if status != "total":
                logger.info(f"  {status}: {count}")
        logger.info(f"  ledger_entries: {len(ledger)}")

        return {
            "ok": True,
            "date": date,
            "input_file": events_file,
            "output_file": output_file,
            "log_file": log_file,
            "event_count": len(novelty_events),
            "novelty_stats": dict(stats),
            "ledger_entries": len(ledger),
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"事件新颖性检查失败: {e}", exc_info=True)
        errors.append(str(e))
        return {"ok": False, "date": date, "errors": errors}
    finally:
        logger.removeHandler(fh)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="事件新颖性检查")
    parser.add_argument("--project-root", default=_PROJECT_ROOT)
    parser.add_argument("--date", required=True)
    parser.add_argument("--events-file", default=None)
    parser.add_argument("--output-file", default=None)
    args = parser.parse_args()

    result = event_novelty_check(
        project_root=args.project_root,
        date=args.date,
        events_file=args.events_file,
        output_file=args.output_file,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))