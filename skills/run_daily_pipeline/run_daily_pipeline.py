#!/usr/bin/env python3
"""
run_daily_pipeline.py — 日报全流程总控脚本

新版流水线 13 步骤：
collect → filter → source_health_report → tavily_gap_search
  → filter_merged → [no_signal_or_extract] → score → analyze
  → generate_report / generate_no_signal_report → editor_review
  → generate_podcast → send_daily_report_email

当 cleaned_count == 0（且 Tavily gap search 后仍为 0）时，
走 no_signal 分支，输出坦诚的"无新增信号"日报。

send_daily_report_email 默认禁用（send_email=False），
启用时需 sendable=true 通过安全门检查。
generate_podcast 失败不阻塞 pipeline，但邮件日志记录播客缺失。

每步校验输出合法性，失败停止后续（除非 continue_on_error=true）。
支持 start_step / end_step / resume / force / dry_run。
最终输出 run_manifest.json + step_status.json + run_summary.md。
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# ── 项目路径 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_daily_pipeline")

# ── 时区 ──
CST = timezone(timedelta(hours=8))

# ── YAML ──
try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ===================== 常量 =====================

# 新版 9 步流水线
STEP_ORDER = [
    "collect",
    "filter",
    "source_health_report",
    "tavily_gap_search",
    "filter_merged",
    "extract",
    "score",
    "analyze",
    "generate_report",
    "editor_review",
    "generate_podcast",
    "send_daily_report_email",
]

# 步骤 → (模块路径, 函数名, CLI脚本路径)
STEP_DEFS = {
    "collect": {
        "module": "skills.collect_daily_articles.collect_daily_articles",
        "function": "collect_daily_articles",
        "cli": "skills/collect_daily_articles/collect_daily_articles.py",
    },
    "filter": {
        "module": "skills.filter_relevant_articles.filter_relevant_articles",
        "function": "filter_relevant_articles",
        "cli": "skills/filter_relevant_articles/filter_relevant_articles.py",
    },
    "source_health_report": {
        "module": "skills.source_health_report.source_health_report",
        "function": "source_health_report",
        "cli": "skills/source_health_report/source_health_report.py",
    },
    "tavily_gap_search": {
        "module": "skills.tavily_gap_search.tavily_gap_search",
        "function": "tavily_gap_search",
        "cli": "skills/tavily_gap_search/tavily_gap_search.py",
    },
    "filter_merged": {
        "module": "skills.filter_relevant_articles.filter_relevant_articles",
        "function": "filter_relevant_articles",
        "cli": "skills/filter_relevant_articles/filter_relevant_articles.py",
    },
    "extract": {
        "module": "skills.extract_events.extract_events",
        "function": "extract_events",
        "cli": "skills/extract_events/extract_events.py",
    },
    "score": {
        "module": "skills.score_events.score_events",
        "function": "score_events",
        "cli": "skills/score_events/score_events.py",
    },
    "analyze": {
        "module": "skills.analyze_business_impact.analyze_business_impact",
        "function": "analyze_business_impact",
        "cli": "skills/analyze_business_impact/analyze_business_impact.py",
    },
    "generate_report": {
        "module": "skills.generate_daily_report.generate_daily_report",
        "function": "generate_daily_report",
        "cli": "skills/generate_daily_report/generate_daily_report.py",
    },
    "generate_no_signal_report": {
        "module": "skills.generate_no_signal_report.generate_no_signal_report",
        "function": "generate_no_signal_report",
        "cli": "skills/generate_no_signal_report/generate_no_signal_report.py",
    },
    "editor_review": {
        "module": "skills.editor_review.editor_review",
        "function": "editor_review",
        "cli": "skills/editor_review/editor_review.py",
    },
    "generate_podcast": {
        "module": "skills.generate_podcast.generate_podcast",
        "function": "generate_podcast",
        "cli": "skills/generate_podcast/generate_podcast.py",
    },
    "send_daily_report_email": {
        "module": "skills.send_daily_report_email.send_daily_report_email",
        "function": "send_daily_report_email",
        "cli": "skills/send_daily_report_email/send_daily_report_email.py",
    },
}

VALID_SECTIONS = [
    "01 今日一句话判断",
    "02 今日最值得关注的3个信号",
    "03 平台变化解读",
    "04 竞对与品牌动作",
    "05 品类与场景机会",
    "06 对屈臣氏的经营提示",
    "07 今日唯一建议动作",
    "08 明日追踪清单",
]

# No-signal 日报章节
NO_SIGNAL_SECTIONS = [
    "01 今日一句话判断",
    "02 今日信息质量说明",
    "03 平台变化解读",
    "04 竞对与品牌动作",
    "05 对屈臣氏的经营提示",
    "06 今日唯一建议动作",
    "07 明日追踪清单",
]


# ===================== 工具函数 =====================

def resolve_path(project_root: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(project_root, rel_path)


def count_chinese_chars(text: str) -> int:
    """统计中文字符数。"""
    return sum(1 for ch in text if '\u4e00' <= ch <= '\u9fff')


def load_yaml_config(project_root: str) -> dict:
    """加载 pipeline.yaml 配置。"""
    config_path = resolve_path(project_root, "config/pipeline.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            if _YAML_AVAILABLE:
                return yaml.safe_load(f) or {}
            else:
                logger.warning("PyYAML not available, using empty pipeline config")
                return {}
    return {}


def load_json_safe(filepath: str) -> Optional[dict]:
    """安全加载 JSON 文件。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_file_safe(filepath: str) -> Optional[str]:
    """安全读取文本文件。"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def yesterday_date() -> str:
    """返回昨天日期（Asia/Shanghai）。"""
    return (datetime.now(CST) - timedelta(days=1)).strftime("%Y-%m-%d")


def today_date() -> str:
    """返回今天日期（Asia/Shanghai）。"""
    return datetime.now(CST).strftime("%Y-%m-%d")


def get_cleaned_count(project_root: str, date: str) -> int:
    """获取当前 cleaned_count。"""
    cleaned_file = resolve_path(project_root, f"data/cleaned/{date}/cleaned_articles.json")
    data = load_json_safe(cleaned_file)
    if data is None:
        return 0
    if isinstance(data, list):
        return len(data)
    return len(data.get("articles", []))


def get_merged_raw_count(project_root: str, date: str) -> int:
    """获取合并后的 raw_count。"""
    merged_file = resolve_path(project_root, f"data/raw/{date}/raw_articles_merged.json")
    data = load_json_safe(merged_file)
    if data:
        return len(data.get("articles", []))
    # fallback to original raw
    raw_file = resolve_path(project_root, f"data/raw/{date}/raw_articles.json")
    data = load_json_safe(raw_file)
    if data:
        return len(data.get("articles", []))
    return 0


# ===================== 步骤参数构建 =====================

def build_step_args(step: str, project_root: str, date: str,
                    use_llm: bool) -> dict:
    """为每个步骤构建参数字典。"""
    base = {
        "project_root": project_root,
        "date": date,
    }

    if step == "collect":
        base["date"] = date
    elif step == "filter":
        base["use_llm"] = use_llm
    elif step == "source_health_report":
        base["date"] = date
    elif step == "tavily_gap_search":
        # tavily_gap_search 不需要 use_llm
        pass
    elif step == "filter_merged":
        base["use_llm"] = False  # 补搜后的重过滤用 rule-only
        base["raw_file"] = resolve_path(
            project_root, f"data/raw/{date}/raw_articles_merged.json")
        base["date"] = date
    elif step == "extract":
        base["use_llm"] = use_llm
    elif step == "score":
        pass
    elif step == "analyze":
        base["use_llm"] = use_llm
    elif step == "generate_report":
        base["use_llm"] = use_llm
    elif step == "generate_no_signal_report":
        # 不需要 use_llm
        pass
    elif step == "editor_review":
        base["use_llm"] = use_llm
    elif step == "generate_podcast":
        base["use_llm"] = use_llm
    elif step == "send_daily_report_email":
        # send_daily_report_email 参数通过额外 pipeline 参数传入
        # 需要后续在调用时补充 send_email / dry_run / recipient
        pass

    return base


# ===================== 调用方式 =====================

def call_step_by_import(step: str, args: dict) -> dict:
    """通过 import 调用步骤的主函数。"""
    defn = STEP_DEFS[step]
    module_path = defn["module"]
    func_name = defn["function"]

    logger.info(f"  调用方式: import {module_path}.{func_name}()")

    try:
        import importlib
        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        result = func(**args)
        return result if result else {"ok": False, "errors": ["步骤返回空结果"]}
    except Exception as e:
        logger.warning(f"  import 调用失败: {e}")
        raise


def call_step_by_subprocess(step: str, args: dict) -> dict:
    """通过 subprocess 调用步骤的 CLI。"""
    import subprocess

    defn = STEP_DEFS[step]
    cli_path = defn["cli"]

    cmd = [sys.executable, cli_path]
    cmd.extend(["--project-root", args["project_root"]])
    cmd.extend(["--date", args["date"]])

    if step in ("filter", "extract", "analyze", "generate_report", "editor_review",
                   "generate_podcast"):
        cmd.extend(["--use-llm", "true" if args.get("use_llm", True) else "false"])
    elif step == "filter_merged":
        cmd.extend(["--use-llm", "false"])  # 补搜后重过滤用 rule-only
    elif step == "send_daily_report_email":
        cmd.extend(["--dry-run", "true" if args.get("dry_run", True) else "false"])
        if args.get("recipient"):
            cmd.extend(["--recipient", args["recipient"]])

    if step == "filter_merged" and "raw_file" in args:
        cmd.extend(["--raw-file", args["raw_file"]])

    logger.info(f"  调用方式: subprocess {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            cwd=args["project_root"],
        )
        stdout = result.stdout.strip()
        if stdout:
            lines = stdout.split("\n")
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue

        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stderr": result.stderr[:2000] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "errors": ["subprocess 超时 (600s)"]}
    except Exception as e:
        return {"ok": False, "errors": [f"subprocess 异常: {e}"]}


def call_step(step: str, args: dict) -> dict:
    """调用步骤主函数，优先 import，失败时 subprocess。"""
    try:
        result = call_step_by_import(step, args)
        result["_call_method"] = "import"
        return result
    except Exception as e:
        logger.warning(f"  import 失败，尝试 subprocess: {e}")
        result = call_step_by_subprocess(step, args)
        result["_call_method"] = "subprocess"
        return result


# ===================== 输出校验 =====================

def validate_collect(project_root: str, date: str) -> dict:
    """校验 collect 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {}}
    raw_file = resolve_path(project_root, f"data/raw/{date}/raw_articles.json")

    if not os.path.exists(raw_file):
        result["valid"] = False
        result["errors"].append(f"采集文件不存在: {raw_file}")
        return result

    data = load_json_safe(raw_file)
    if data is None:
        result["valid"] = False
        result["errors"].append(f"JSON 解析失败: {raw_file}")
        return result

    raw_count = len(data.get("articles", []))
    result["stats"]["raw_count"] = raw_count
    result["output_files"] = [raw_file]

    if raw_count < 20:
        result["warnings"].append(
            f"采集文章数 {raw_count} 低于阈值 20，数据可能不完整")

    return result


def validate_filter(project_root: str, date: str) -> dict:
    """校验 filter 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    cleaned = resolve_path(project_root, f"data/cleaned/{date}/cleaned_articles.json")
    reference = resolve_path(project_root, f"data/cleaned/{date}/reference_articles.json")
    rejected = resolve_path(project_root, f"data/rejected/{date}/rejected_articles.json")

    for path in [cleaned, reference]:
        if not os.path.exists(path):
            result["valid"] = False
            result["errors"].append(f"过滤文件不存在: {path}")

    if not result["valid"]:
        return result

    cleaned_data = load_json_safe(cleaned) or {}
    reference_data = load_json_safe(reference) or {}

    if isinstance(cleaned_data, list):
        cleaned_count = len(cleaned_data)
    elif isinstance(cleaned_data, dict):
        cleaned_count = len(cleaned_data.get("articles", []))
    else:
        cleaned_count = 0

    if isinstance(reference_data, list):
        reference_count = len(reference_data)
    elif isinstance(reference_data, dict):
        reference_count = len(reference_data.get("articles", []))
    else:
        reference_count = 0

    if os.path.exists(rejected):
        rejected_data = load_json_safe(rejected) or {}
        if isinstance(rejected_data, list):
            rejected_count = len(rejected_data)
        elif isinstance(rejected_data, dict):
            rejected_count = len(rejected_data.get("articles", []))
        else:
            rejected_count = 0
    else:
        rejected_count = 0

    result["stats"]["cleaned_count"] = cleaned_count
    result["stats"]["reference_count"] = reference_count
    result["stats"]["rejected_count"] = rejected_count
    result["output_files"] = [cleaned, reference]
    if os.path.exists(rejected):
        result["output_files"].append(rejected)

    if cleaned_count < 3:
        result["warnings"].append(
            f"清洗文章数 {cleaned_count} 低于阈值 3，后续步骤可能数据不足")

    return result


def validate_source_health_report(project_root: str, date: str) -> dict:
    """校验 source_health_report 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    report_json = resolve_path(
        project_root, f"data/logs/{date}/source_health_report.json")
    report_md = resolve_path(
        project_root, f"data/logs/{date}/source_health_report.md")

    if os.path.exists(report_json):
        data = load_json_safe(report_json)
        if data:
            result["stats"]["health_report_sources"] = len(
                data.get("sources", {}))
            summary = data.get("summary", {})
            for grade, info in summary.items():
                cnt = info.get("count", 0) if isinstance(info, dict) else info
                result["stats"][f"health_grade_{grade}"] = cnt
        result["output_files"].append(report_json)
    else:
        result["warnings"].append("源健康报告 JSON 不存在（非阻塞）")

    if os.path.exists(report_md):
        result["output_files"].append(report_md)

    # source_health_report 生成是 best-effort，不算阻塞
    return result


def validate_tavily_gap_search(project_root: str, date: str) -> dict:
    """校验 tavily_gap_search 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}

    log_json = resolve_path(
        project_root, f"data/logs/{date}/tavily_gap_search.json")
    gap_file = resolve_path(
        project_root, f"data/raw/{date}/tavily_gap_articles.json")
    merged_file = resolve_path(
        project_root, f"data/raw/{date}/raw_articles_merged.json")

    # 检查是否触发了 gap search
    triggered = False
    if os.path.exists(log_json):
        data = load_json_safe(log_json)
        if data:
            triggered = data.get("triggered", False)
            result["stats"]["tavily_triggered"] = triggered
            result["stats"]["tavily_gap_count"] = data.get("gap_count", 0)
            result["stats"]["tavily_unique_count"] = data.get("unique_count", 0)
            result["stats"]["tavily_queries"] = data.get("queries", 0)

    if triggered:
        if os.path.exists(gap_file):
            result["output_files"].append(gap_file)
        if os.path.exists(merged_file):
            result["output_files"].append(merged_file)
    else:
        result["warnings"].append("Tavily gap search 未触发（无需补搜或条件不满足）")

    # 不阻塞
    return result


def validate_filter_merged(project_root: str, date: str) -> dict:
    """校验 filter_merged（补搜后重过滤）步骤输出。与 validate_filter 相同。"""
    # filter_merged 产出与 filter 相同的文件
    return validate_filter(project_root, date)


def validate_extract(project_root: str, date: str) -> dict:
    """校验 extract 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    events_raw = resolve_path(project_root, f"data/events/{date}/events_raw.json")
    events_rejected = resolve_path(project_root, f"data/events/{date}/events_rejected_articles.json")

    if not os.path.exists(events_raw):
        result["valid"] = False
        result["errors"].append(f"事件文件不存在: {events_raw}")
        return result

    data = load_json_safe(events_raw)
    if data is None:
        result["valid"] = False
        result["errors"].append(f"JSON 解析失败: {events_raw}")
        return result

    events = data.get("events", [])
    event_count = len(events)
    result["stats"]["event_count"] = event_count
    result["output_files"] = [events_raw]

    if os.path.exists(events_rejected):
        result["output_files"].append(events_rejected)

    failed_file = resolve_path(project_root, f"data/events/{date}/events_failed_articles.json")
    if os.path.exists(failed_file):
        result["output_files"].append(failed_file)

    if event_count < 1:
        result["valid"] = False
        result["errors"].append(f"事件数 {event_count} 为 0，无法继续")

    return result


def validate_score(project_root: str, date: str) -> dict:
    """校验 score 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    scored_file = resolve_path(project_root, f"data/events/{date}/events_scored.json")
    raw_file = resolve_path(project_root, f"data/events/{date}/events_raw.json")

    if not os.path.exists(scored_file):
        result["valid"] = False
        result["errors"].append(f"评分文件不存在: {scored_file}")
        return result

    data = load_json_safe(scored_file)
    if data is None:
        result["valid"] = False
        result["errors"].append(f"JSON 解析失败: {scored_file}")
        return result

    # events_scored.json 可能是列表或字典
    if isinstance(data, list):
        events = data
    else:
        events = data.get("events", [])
    scored_count = len(events)
    result["stats"]["scored_count"] = scored_count
    result["output_files"] = [scored_file]

    missing_scores = 0
    priorities = {}
    for ev in events:
        if "scores" not in ev and "weighted_score" not in ev:
            missing_scores += 1
        p = ev.get("priority", "unknown")
        priorities[p] = priorities.get(p, 0) + 1

    result["stats"]["p0_count"] = priorities.get("P0", 0)
    result["stats"]["p1_count"] = priorities.get("P1", 0)
    result["stats"]["p2_count"] = priorities.get("P2", 0)
    result["stats"]["archive_count"] = priorities.get("ARCHIVE", 0)

    if missing_scores > 0:
        result["warnings"].append(f"{missing_scores} 条事件缺少评分字段")

    raw_data = load_json_safe(raw_file)
    if raw_data:
        raw_count = len(raw_data.get("events", []))
        if scored_count != raw_count:
            result["warnings"].append(
                f"评分事件数 {scored_count} ≠ 原始事件数 {raw_count}")

    return result


def validate_analyze(project_root: str, date: str) -> dict:
    """校验 analyze 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    analyzed_file = resolve_path(project_root, f"data/events/{date}/events_analyzed.json")
    scored_file = resolve_path(project_root, f"data/events/{date}/events_scored.json")

    if not os.path.exists(analyzed_file):
        result["valid"] = False
        result["errors"].append(f"分析文件不存在: {analyzed_file}")
        return result

    data = load_json_safe(analyzed_file)
    if data is None:
        result["valid"] = False
        result["errors"].append(f"JSON 解析失败: {analyzed_file}")
        return result

    # events_analyzed.json 可能是列表或字典
    if isinstance(data, list):
        events = data
    else:
        events = data.get("events", [])
    analyzed_count = len(events)
    result["stats"]["analyzed_count"] = analyzed_count
    result["output_files"] = [analyzed_file]

    no_analysis = sum(1 for ev in events if not ev.get("business_analysis"))
    if no_analysis > 0:
        result["warnings"].append(f"{no_analysis} 条事件缺少 business_analysis")

    scored_data = load_json_safe(scored_file)
    if scored_data:
        # events_scored.json 也可能是列表
        if isinstance(scored_data, list):
            scored_events = scored_data
        else:
            scored_events = scored_data.get("events", [])
        scored_count = len(scored_events)
        if analyzed_count != scored_count:
            result["warnings"].append(
                f"分析事件数 {analyzed_count} ≠ 评分事件数 {scored_count}")

    return result


def validate_generate_report(project_root: str, date: str) -> dict:
    """校验 generate_report 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    drafts_dir = resolve_path(project_root, f"data/drafts/{date}")

    v1_file = os.path.join(drafts_dir, "daily_report_draft_v1.md")
    v2_file = os.path.join(drafts_dir, "daily_report_draft_v2.md")
    compat_file = os.path.join(drafts_dir, "daily_report_draft.md")

    for f in [v1_file, v2_file, compat_file]:
        if not os.path.exists(f):
            result["valid"] = False
            result["errors"].append(f"日报文件不存在: {f}")

    if not result["valid"]:
        return result

    v2_text = read_file_safe(v2_file)
    if v2_text and len(v2_text.strip()) < 100:
        result["warnings"].append("V2 重构稿内容过短（<100字符）")

    combined = (read_file_safe(v1_file) or "") + (read_file_safe(v2_file) or "")
    event_ids = re.findall(r'E\d{8}_\d{4}', combined)
    unique_ids = set(event_ids)
    result["stats"]["event_id_count"] = len(unique_ids)

    if len(unique_ids) < 1:
        result["valid"] = False
        result["errors"].append("日报中未包含任何 event_id")

    v1_text = read_file_safe(v1_file) or ""
    for section in VALID_SECTIONS:
        if section not in v1_text:
            result["warnings"].append(f"V1 缺少章节: {section}")

    result["output_files"] = [v1_file, v2_file, compat_file]
    return result


def validate_generate_no_signal_report(project_root: str, date: str) -> dict:
    """校验 generate_no_signal_report 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    report_dir = resolve_path(project_root, f"data/reports/{date}")

    no_signal_file = os.path.join(report_dir, f"daily_report_{date}_no_signal.md")
    final_file = os.path.join(report_dir, f"final_report_{date}.md")

    if not os.path.exists(no_signal_file):
        result["valid"] = False
        result["errors"].append(f"无信号日报不存在: {no_signal_file}")
        return result

    no_signal_text = read_file_safe(no_signal_file) or ""

    # 检查关键章节
    for section in NO_SIGNAL_SECTIONS:
        if section not in no_signal_text:
            result["warnings"].append(f"无信号日报缺少章节: {section}")

    result["stats"]["report_type"] = "no_signal"
    result["stats"]["sendable"] = True
    result["output_files"] = [no_signal_file]
    if os.path.exists(final_file):
        result["output_files"].append(final_file)

    return result


def validate_editor_review(project_root: str, date: str) -> dict:
    """校验 editor_review 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    review_file = resolve_path(project_root, f"data/reviews/{date}/editor_review.md")
    year, month = date.split("-")[0], date.split("-")[1]
    final_file = resolve_path(project_root, f"reports/daily/{year}/{month}/{date}.md")

    if not os.path.exists(review_file):
        result["valid"] = False
        result["errors"].append(f"审稿报告不存在: {review_file}")
        return result

    if not os.path.exists(final_file):
        result["valid"] = False
        result["errors"].append(f"终稿文件不存在: {final_file}")
        return result

    final_text = read_file_safe(final_file) or ""
    missing_sections = []
    # 判断是无信号日报还是正常日报
    is_no_signal = "无新增信号" in final_text or "无信号" in final_text
    sections_to_check = NO_SIGNAL_SECTIONS if is_no_signal else VALID_SECTIONS

    for section in sections_to_check:
        if section not in final_text:
            missing_sections.append(section)

    if missing_sections:
        result["warnings"].append(f"终稿缺少章节: {', '.join(missing_sections)}")

    event_ids = set(re.findall(r'E\d{8}_\d{4}', final_text))
    result["stats"]["event_id_count"] = len(event_ids)

    # sendable 判断
    review_text = read_file_safe(review_file) or ""
    result["stats"]["sendable"] = "✅ 可发送" in review_text

    result["output_files"] = [review_file, final_file]
    return result


def validate_generate_podcast(project_root: str, date: str) -> dict:
    """校验 generate_podcast 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    script_dir = resolve_path(project_root, f"podcasts/scripts")
    script_file = os.path.join(script_dir, f"{date}.md")
    audio_dir = resolve_path(project_root, f"podcasts/audio")
    audio_file = os.path.join(audio_dir, f"{date}.mp3")
    log_json = resolve_path(
        project_root, f"data/logs/{date}/generate_podcast.json")

    # 口播稿
    if os.path.exists(script_file):
        script_text = read_file_safe(script_file) or ""
        cn_chars = count_chinese_chars(script_text)
        result["stats"]["script_length"] = cn_chars
        result["output_files"].append(script_file)

        if cn_chars < 1800:
            result["warnings"].append(
                f"口播稿偏短（{cn_chars} < 1800 中文字符）")
        elif cn_chars > 2500:
            result["warnings"].append(
                f"口播稿偏长（{cn_chars} > 2500 中文字符）")
    else:
        result["warnings"].append("口播稿文件不存在")

    # 音频
    if os.path.exists(audio_file):
        size = os.path.getsize(audio_file)
        result["stats"]["audio_exists"] = True
        result["stats"]["audio_size_bytes"] = size
        result["output_files"].append(audio_file)
    else:
        result["warnings"].append("音频文件不存在（edge-tts 生成失败不影响日报）")

    # JSON 结果
    if os.path.exists(log_json):
        data = load_json_safe(log_json)
        if data:
            result["stats"]["is_no_signal"] = data.get("is_no_signal", False)
            result["stats"]["llm_used"] = data.get("llm_used", False)
            result["stats"]["tts_success"] = data.get("tts_success", False)
            result["stats"]["voice"] = data.get("voice", "")

    # generate_podcast 是 best-effort，不阻塞 pipeline
    return result


def validate_send_daily_report_email(project_root: str, date: str) -> dict:
    """校验 send_daily_report_email 步骤输出。"""
    result = {"valid": True, "warnings": [], "errors": [], "stats": {},
              "output_files": []}
    log_json = resolve_path(
        project_root, f"data/logs/{date}/send_daily_report_email.json")

    if os.path.exists(log_json):
        data = load_json_safe(log_json)
        if data:
            result["stats"]["sent"] = data.get("sent", False)
            result["stats"]["dry_run"] = data.get("dry_run", True)
            result["stats"]["sendable"] = data.get("sendable", False)
            result["stats"]["html_length"] = data.get("html_length", 0)
            result["stats"]["markdown_attachment"] = data.get("markdown_attachment", False)
            result["stats"]["podcast_attachment"] = data.get("podcast_attachment", False)
            result["output_files"].append(log_json)
    else:
        # 邮件可能未运行（默认 disabled）或未到该步骤
        result["warnings"].append("邮件发送结果 JSON 不存在")

    # send_daily_report_email 是 best-effort，不阻塞 pipeline
    return result


# 步骤校验函数映射
STEP_VALIDATORS = {
    "collect": validate_collect,
    "filter": validate_filter,
    "source_health_report": validate_source_health_report,
    "tavily_gap_search": validate_tavily_gap_search,
    "filter_merged": validate_filter_merged,
    "extract": validate_extract,
    "score": validate_score,
    "analyze": validate_analyze,
    "generate_report": validate_generate_report,
    "generate_no_signal_report": validate_generate_no_signal_report,
    "editor_review": validate_editor_review,
    "generate_podcast": validate_generate_podcast,
    "send_daily_report_email": validate_send_daily_report_email,
}


# ===================== Resume 检查 =====================

def check_step_completed(step: str, project_root: str, date: str) -> bool:
    """检查步骤是否已有合格输出（用于 resume 跳过）。"""
    validator = STEP_VALIDATORS.get(step)
    if not validator:
        return False
    result = validator(project_root, date)
    return result["valid"] and not result["errors"]


# ===================== 运行摘要 =====================

def generate_run_summary(date: str, manifest: dict) -> str:
    """生成人类可读的运行摘要 Markdown。"""
    lines = [
        f"# Daily Pipeline Run Summary｜{date}",
        "",
        "## 运行结论",
        "",
        f"- **状态**: {'✅ 成功' if manifest.get('ok') else '❌ 失败'}",
        f"- **可发送**: {'✅ 是' if manifest.get('sendable') else '⚠️ 否/未审稿'}",
        f"- **播客**: {manifest.get('podcast_status', '⚠️ 未生成')}",
        f"- **邮件**: {manifest.get('email_status', '⏭️ 未启用')}",
        f"- **日报类型**: {manifest.get('report_type', '正常日报')}",
        f"- **开始时间**: {manifest.get('started_at', '?')}",
        f"- **结束时间**: {manifest.get('finished_at', '?')}",
        f"- **总耗时**: {manifest.get('duration_seconds', 0):.1f}s",
        "",
        "## 步骤状态",
        "",
        "| 步骤 | 状态 | 耗时 | 核心统计 | 警告 | 错误 |",
        "|------|------|------|----------|------|------|",
    ]

    for step_info in manifest.get("steps", []):
        status = step_info.get("status", "?")
        status_icon = {"success": "✅", "warning": "⚠️", "failed": "❌",
                       "skipped": "⏭️", "dry_run": "🔍",
                       "no_signal": "🔕"}.get(status, status)
        duration = f"{step_info.get('duration_seconds', 0):.1f}s"
        stats = step_info.get("stats", {})

        # 提取核心计数
        stat_str = ""
        key_map = {
            "collect": "raw_count",
            "filter": "cleaned_count",
            "source_health_report": "health_report_sources",
            "tavily_gap_search": "tavily_unique_count",
            "filter_merged": "cleaned_count",
            "extract": "event_count",
            "score": "scored_count",
            "analyze": "analyzed_count",
            "generate_report": "event_id_count",
            "generate_no_signal_report": "report_type",
            "editor_review": "sendable",
            "generate_podcast": "script_length",
            "send_daily_report_email": "sent",
        }
        stat_key = key_map.get(step_info.get("name"), "")
        if stat_key and stat_key in stats:
            stat_str = str(stats[stat_key])

        warn_count = len(step_info.get("warnings", []))
        err_count = len(step_info.get("errors", []))
        lines.append(
            f"| {step_info.get('name', '?')} | {status_icon} {status} | "
            f"{duration} | {stat_str} | {warn_count} | {err_count} |"
        )

    lines.extend(["", "## 核心统计", ""])

    all_stats = {}
    for step_info in manifest.get("steps", []):
        all_stats.update(step_info.get("stats", {}))

    stat_items = [
        ("采集文章数", all_stats.get("raw_count")),
        ("清洗文章数", all_stats.get("cleaned_count")),
        ("参考文章数", all_stats.get("reference_count")),
        ("Tavily补搜", all_stats.get("tavily_unique_count")),
        ("抽取事件数", all_stats.get("event_count")),
        ("评分事件数", all_stats.get("scored_count")),
        ("分析事件数", all_stats.get("analyzed_count")),
        ("日报事件ID数", all_stats.get("event_id_count")),
        ("P0事件", all_stats.get("p0_count")),
        ("P1事件", all_stats.get("p1_count")),
        ("P2事件", all_stats.get("p2_count")),
        ("口播稿字数", all_stats.get("script_length")),
        ("播客音频", "✅ 有" if all_stats.get("audio_exists") else "⚠️ 无"),
        ("邮件发送", "✅ 已发送" if all_stats.get("sent") else (
            "🔍 Dry Run" if all_stats.get("dry_run") else "⏭️ 未启用")),
    ]

    for label, value in stat_items:
        if value is not None:
            lines.append(f"- **{label}**: {value}")

    lines.extend(["", "## Warning", ""])
    all_warnings = []
    for step_info in manifest.get("steps", []):
        for w in step_info.get("warnings", []):
            all_warnings.append(f"[{step_info.get('name')}] {w}")
    if all_warnings:
        for w in all_warnings:
            lines.append(f"- ⚠️ {w}")
    else:
        lines.append("无")

    lines.extend(["", "## Error", ""])
    all_errors = []
    for step_info in manifest.get("steps", []):
        for e in step_info.get("errors", []):
            all_errors.append(f"[{step_info.get('name')}] {e}")
    for e in manifest.get("errors", []):
        all_errors.append(e)
    if all_errors:
        for e in all_errors:
            lines.append(f"- ❌ {e}")
    else:
        lines.append("无")

    lines.extend(["", "## 是否可发送", "",
                   f"**{'✅ 可发送' if manifest.get('sendable') else '⚠️ 不可发送/未审稿'}**", ""])

    return "\n".join(lines)


# ===================== 主函数 =====================

def run_daily_pipeline(
    project_root: str,
    date: Optional[str] = None,
    start_step: Optional[str] = None,
    end_step: Optional[str] = None,
    use_llm: bool = True,
    send_email: bool = False,
    recipient: Optional[str] = None,
    resume: bool = True,
    force: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
) -> dict:
    """日报全流程总控主函数。

    新版流水线:
    collect → filter → source_health_report → tavily_gap_search
      → filter_merged → [no_signal 或 extract] → score → analyze
      → generate_report / generate_no_signal_report → editor_review

    当 cleaned_count == 0 after filter_merged 时,
    走 no_signal 分支, 输出"无新增信号"日报。
    """
    errors: List[str] = []
    started_at = datetime.now(CST)

    if not date:
        date = today_date()

    logger.info("=" * 60)
    logger.info(f"开始全流程运行: date={date}")
    logger.info(f"  project_root: {project_root}")
    logger.info(f"  start_step: {start_step}")
    logger.info(f"  end_step: {end_step}")
    logger.info(f"  use_llm: {use_llm}")
    logger.info(f"  resume: {resume}")
    logger.info(f"  force: {force}")
    logger.info(f"  dry_run: {dry_run}")
    logger.info("=" * 60)

    if start_step and start_step not in STEP_ORDER:
        return {"ok": False, "date": date, "errors": [f"未知步骤: {start_step}"]}
    if end_step and end_step not in STEP_ORDER:
        return {"ok": False, "date": date, "errors": [f"未知步骤: {end_step}"]}

    start_idx = STEP_ORDER.index(start_step) if start_step else 0
    end_idx = STEP_ORDER.index(end_step) if end_step else len(STEP_ORDER) - 1
    steps_to_run = STEP_ORDER[start_idx:end_idx + 1]

    logger.info(f"将运行步骤: {' → '.join(steps_to_run)}")

    run_dir = resolve_path(project_root, f"data/runs/{date}")
    os.makedirs(run_dir, exist_ok=True)

    config = load_yaml_config(project_root)

    step_results: List[dict] = []
    pipeline_ok = True
    sendable = False
    report_type = "normal"  # 或 "no_signal"
    no_signal = False  # 是否走无信号分支

    # ── 计算实际要跑的步骤序列（含动态分支） ──
    actual_steps = []
    skip_after = None  # 跳过某些步骤

    for step in steps_to_run:
        actual_steps.append(step)

    # ── 执行各步骤 ──
    step_idx = 0
    while step_idx < len(actual_steps):
        step = actual_steps[step_idx]
        step_idx += 1

        # ── 动态分支：filter_merged 后检查 cleaned_count ──
        if step == "filter_merged":
            # 先执行 filter_merged
            pass  # 正常执行，下面执行完后检查

        step_info: Dict[str, Any] = {
            "name": step,
            "status": "pending",
            "started_at": "",
            "finished_at": "",
            "duration_seconds": 0,
            "call_method": "",
            "input_files": [],
            "output_files": [],
            "stats": {},
            "warnings": [],
            "errors": [],
        }

        logger.info(f"\n{'─' * 50}")
        logger.info(f"步骤: {step}")
        logger.info(f"{'─' * 50}")

        step_start = time.time()
        step_info["started_at"] = datetime.now(CST).isoformat()

        # ── Resume 检查 ──
        if resume and not force:
            if check_step_completed(step, project_root, date):
                logger.info(f"  ⏭️ 步骤 {step} 已有合格输出，跳过 (resume)")
                step_info["status"] = "skipped"
                step_info["finished_at"] = datetime.now(CST).isoformat()
                step_info["duration_seconds"] = 0
                v_result = STEP_VALIDATORS[step](project_root, date)
                step_info["stats"] = v_result.get("stats", {})
                step_info["output_files"] = v_result.get("output_files", [])
                step_info["warnings"] = v_result.get("warnings", [])
                step_info["output_files"] = list(dict.fromkeys(step_info.get("output_files", [])))
                step_results.append(step_info)

                # ── 对于 filter_merged，检查 resume 后的 cleaned_count ──
                # （已在 filter_merged 步骤的 resume 中检查过了）
                continue

        # ── Dry run ──
        if dry_run:
            logger.info(f"  🔍 dry_run: 仅检查步骤 {step}")
            v_result = STEP_VALIDATORS[step](project_root, date)
            step_info["status"] = "dry_run"
            step_info["stats"] = v_result.get("stats", {})
            step_info["output_files"] = v_result.get("output_files", [])
            step_info["warnings"] = v_result.get("warnings", [])
            step_info["errors"] = v_result.get("errors", [])
            step_info["finished_at"] = datetime.now(CST).isoformat()
            step_info["duration_seconds"] = 0
            step_info["output_files"] = list(dict.fromkeys(step_info.get("output_files", [])))
            step_results.append(step_info)
            continue

        # ── 构建参数并调用 ──
        args = build_step_args(step, project_root, date, use_llm)

        # send_daily_report_email: send_email=False 时跳过
        if step == "send_daily_report_email" and not send_email:
            logger.info(f"  ⏭️ 步骤 {step}: 跳过（send_email=False）")
            step_info["status"] = "skipped"
            step_info["stats"] = {"sent": False, "dry_run": dry_run, "sendable": False,
                                   "reason": "send_email=False (默认禁用)"}
            step_info["finished_at"] = datetime.now(CST).isoformat()
            step_info["duration_seconds"] = 0
            step_results.append(step_info)
            continue

        # send_daily_report_email 需要额外参数
        if step == "send_daily_report_email":
            args["dry_run"] = dry_run
            if recipient:
                args["recipient"] = recipient

        try:
            result = call_step(step, args)
            step_info["call_method"] = result.get("_call_method", "unknown")

            if not result.get("ok", False):
                step_info["status"] = "failed"
                step_errors = result.get("errors", [])
                step_info["errors"] = step_errors if isinstance(step_errors, list) else [str(step_errors)]
                logger.error(f"  ❌ 步骤 {step} 失败: {step_info['errors']}")
                pipeline_ok = False
                if not continue_on_error:
                    step_info["finished_at"] = datetime.now(CST).isoformat()
                    step_info["duration_seconds"] = time.time() - step_start
                    step_results.append(step_info)
                    break
            else:
                step_info["status"] = "success"

                # 收集输出信息
                for key in ["output_file", "v1_file", "v2_file", "v2_compat_file",
                            "cleaned_file", "reference_file",
                            "review_file", "final_file", "log_file"]:
                    if key in result and result[key]:
                        step_info["output_files"].append(result[key])

                # 收集统计
                stat_keys = [
                    "total_collected", "total_saved",
                    "raw_count", "cleaned_count", "reference_count", "rejected_count",
                    "event_count", "rejected_article_count", "failed_article_count",
                    "rule_fallback_count", "llm_success_count",
                    "scored_count", "p0_count", "p1_count", "p2_count", "archive_count",
                    "analyzed_count", "llm_failed_count",
                    "event_id_count", "v1_length", "v2_length",
                    "v1_chinese_chars", "v2_chinese_chars",
                    "core_signal_count", "unique_action_event_id",
                    "draft_issue_count", "final_issue_count", "sendable",
                    "dual_mode",
                    # source_health_report stats
                    "health_report_sources", "health_grade_A", "health_grade_B",
                    "health_grade_C", "health_grade_D",
                    # tavily_gap_search stats
                    "tavily_triggered", "tavily_gap_count", "tavily_unique_count",
                    "tavily_queries",
                    # no_signal stats
                    "report_type", "gap_count", "gap_query_count",
                    # podcast stats
                    "script_length", "audio_exists", "audio_size_bytes",
                    "is_no_signal", "llm_used", "tts_success", "voice",
                    # email stats
                    "sent", "dry_run", "sendable", "html_length",
                    "markdown_attachment", "podcast_attachment", "script_attachment",
                ]
                for k in stat_keys:
                    if k in result:
                        step_info["stats"][k] = result[k]

                # 特殊处理：从步骤结果中提取全局状态
                if step == "send_daily_report_email":
                    email_sent = result.get("sent", False) is True
                    logger.info(f"  邮件发送结果: sent={result.get('sent')}, email_sent={email_sent}")

                logger.info(f"  ✅ 步骤 {step} 成功")

        except Exception as e:
            step_info["status"] = "failed"
            step_info["errors"] = [str(e)]
            logger.error(f"  ❌ 步骤 {step} 异常: {e}")
            logger.error(traceback.format_exc())
            pipeline_ok = False
            if not continue_on_error:
                step_info["finished_at"] = datetime.now(CST).isoformat()
                step_info["duration_seconds"] = time.time() - step_start
                step_results.append(step_info)
                break

        # ── 步骤后校验 ──
        if step_info["status"] == "success" and step in STEP_VALIDATORS:
            v_result = STEP_VALIDATORS[step](project_root, date)
            if v_result["warnings"]:
                step_info["warnings"].extend(v_result["warnings"])
                step_info["status"] = "warning"
            if v_result["errors"]:
                step_info["errors"].extend(v_result["errors"])
            step_info["stats"].update(v_result.get("stats", {}))
            if v_result.get("output_files"):
                step_info["output_files"].extend(v_result["output_files"])

        step_info["finished_at"] = datetime.now(CST).isoformat()
        step_info["duration_seconds"] = time.time() - step_start
        step_info["output_files"] = list(dict.fromkeys(step_info.get("output_files", [])))
        step_results.append(step_info)

        # ── 动态分支 ──
        # 在 filter_merged 之后，检查 cleaned_count
        if step in ("filter", "filter_merged") and step_info["status"] in ("success", "warning"):
            cleaned_count = get_cleaned_count(project_root, date)
            logger.info(f"  📊 {step} 后 cleaned_count = {cleaned_count}")

            if cleaned_count == 0 and step == "filter_merged":
                # 只在 filter_merged 后检查（因为中间可能有 tavily gap search）
                # 且不在显式 end_step 限制的情况下跳过
                logger.info("  🔕 cleaned_count=0 after filter_merged, 走无信号日报分支")
                no_signal = True
                report_type = "no_signal"

                # 跳过 extract/score/analyze/generate_report，走 no_signal 分支
                # 插入 generate_no_signal_report 替换 generate_report
                skip_remaining_analysis = True

                # 在剩余步骤中，跳过 extract/score/analyze/generate_report
                # 保留 editor_review（no_signal 报告也需要审计）
                remaining_skip = {"extract", "score", "analyze", "generate_report"}
                new_actual = []
                i = step_idx
                while i < len(actual_steps):
                    s = actual_steps[i]
                    i += 1
                    if s in remaining_skip:
                        logger.info(f"  ⏭️ 跳过步骤 {s}（无信号分支）")
                        skip_info = {
                            "name": s,
                            "status": "skipped_no_signal",
                            "started_at": datetime.now(CST).isoformat(),
                            "finished_at": datetime.now(CST).isoformat(),
                            "duration_seconds": 0,
                            "call_method": "",
                            "input_files": [],
                            "output_files": [],
                            "stats": {},
                            "warnings": [f"因 cleaned_count=0 跳过（无信号日报分支）"],
                            "errors": [],
                        }
                        step_results.append(skip_info)
                    elif s == "generate_report":
                        # 替换为 generate_no_signal_report
                        logger.info(f"  🔕 替换 generate_report → generate_no_signal_report")
                        new_actual.append("generate_no_signal_report")
                    else:
                        new_actual.append(s)

                # 替换剩余步骤
                actual_steps = actual_steps[:step_idx] + new_actual
                # 修正 step_idx 已经递增的问题，需要从当前位置继续
                # 由于我们修改了 actual_steps，remaining 已处理，直接 break
                break

    # ── 处理无信号日报分支 ──
    if no_signal:
        logger.info("\n" + "=" * 60)
        logger.info("进入无信号日报分支")
        logger.info("=" * 60)

        # 生成无信号日报
        ns_step = {
            "name": "generate_no_signal_report",
            "status": "pending",
            "started_at": "",
            "finished_at": "",
            "duration_seconds": 0,
            "call_method": "",
            "input_files": [],
            "output_files": [],
            "stats": {},
            "warnings": [],
            "errors": [],
        }

        if end_step and STEP_ORDER.index(end_step) < STEP_ORDER.index("generate_report"):
            # end_step 在 generate_report 之前，不需要生成日报
            logger.info(f"  终止步骤 {end_step} 在日报之前，跳过无信号日报")
        else:
            # 如果 run 步骤还没跑过 generate_no_signal_report
            if not any(s["name"] == "generate_no_signal_report" and s["status"] in ("success", "warning") for s in step_results):
                ns_start = time.time()
                ns_step["started_at"] = datetime.now(CST).isoformat()

                try:
                    ns_result = call_step("generate_no_signal_report",
                                          build_step_args("generate_no_signal_report", project_root, date, use_llm))
                    ns_step["call_method"] = ns_result.get("_call_method", "unknown")

                    if ns_result.get("ok", False):
                        ns_step["status"] = "success"
                        for key in ["output_file", "final_file", "log_file",
                                    "raw_count", "gap_count", "gap_query_count",
                                    "reference_count", "rejected_count", "sendable",
                                    "report_type"]:
                            if key in ns_result:
                                ns_step["stats"][key] = ns_result[key]
                                if key in ("output_file", "final_file", "log_file"):
                                    ns_step["output_files"].append(ns_result[key])
                        logger.info("  ✅ 无信号日报生成成功")
                    else:
                        ns_step["status"] = "failed"
                        ns_step["errors"] = ns_result.get("errors", [])
                        pipeline_ok = False
                        logger.error(f"  ❌ 无信号日报生成失败: {ns_step['errors']}")

                except Exception as e:
                    ns_step["status"] = "failed"
                    ns_step["errors"] = [str(e)]
                    pipeline_ok = False
                    logger.error(f"  ❌ 无信号日报异常: {e}")

                ns_step["finished_at"] = datetime.now(CST).isoformat()
                ns_step["duration_seconds"] = time.time() - ns_start
                step_results.append(ns_step)

                # 校验
                if ns_step["status"] in ("success", "warning"):
                    v_result = validate_generate_no_signal_report(project_root, date)
                    ns_step["stats"].update(v_result.get("stats", {}))
                    ns_step["warnings"].extend(v_result.get("warnings", []))

        # editor_review（如果需要）
        if end_step is None or STEP_ORDER.index("editor_review") <= STEP_ORDER.index(end_step if end_step else "editor_review"):
            if not any(s["name"] == "editor_review" and s["status"] in ("success", "warning") for s in step_results):
                er_step = {
                    "name": "editor_review",
                    "status": "pending",
                    "started_at": "",
                    "finished_at": "",
                    "duration_seconds": 0,
                    "call_method": "",
                    "input_files": [],
                    "output_files": [],
                    "stats": {},
                    "warnings": [],
                    "errors": [],
                }
                er_start = time.time()
                er_step["started_at"] = datetime.now(CST).isoformat()

                try:
                    er_result = call_step("editor_review",
                                          build_step_args("editor_review", project_root, date, use_llm))
                    er_step["call_method"] = er_result.get("_call_method", "unknown")

                    if er_result.get("ok", False):
                        er_step["status"] = "success"
                        for key in ["review_file", "final_file", "log_file", "sendable"]:
                            if key in er_result:
                                er_step["stats"][key] = er_result[key]
                                if key in ("review_file", "final_file", "log_file"):
                                    er_step["output_files"].append(er_result[key])
                        sendable = er_result.get("sendable", False)
                    else:
                        er_step["status"] = "failed"
                        er_step["errors"] = er_result.get("errors", [])
                        pipeline_ok = False

                except Exception as e:
                    er_step["status"] = "failed"
                    er_step["errors"] = [str(e)]
                    pipeline_ok = False

                er_step["finished_at"] = datetime.now(CST).isoformat()
                er_step["duration_seconds"] = time.time() - er_start
                step_results.append(er_step)

                # ── editor_review 后写入中间 manifest ──
                # 后续步骤（podcast、email）需要读取 sendable 等状态
                interim_manifest = {
                    "date": date,
                    "sendable": sendable,
                    "no_signal": no_signal,
                    "report_type": report_type,
                    "final_report_file": final_report_file,
                }
                interim_path = os.path.join(run_dir, "run_manifest.json")
                try:
                    # 尝试读取已有 manifest 并合并
                    if os.path.exists(interim_path):
                        with open(interim_path, "r", encoding="utf-8") as f:
                            existing = json.load(f)
                        existing.update(interim_manifest)
                        interim_manifest = existing
                    os.makedirs(run_dir, exist_ok=True)
                    with open(interim_path, "w", encoding="utf-8") as f:
                        json.dump(interim_manifest, f, ensure_ascii=False, indent=2)
                    logger.info(f"中间 manifest 已写入: {interim_path} (sendable={sendable})")
                except Exception as e:
                    logger.warning(f"写入中间 manifest 失败（非阻塞）: {e}")

    # ── generate_podcast 步骤 ──
    # generate_podcast 是 best-effort，失败不阻塞 pipeline
    # 仅在主循环未处理时运行
    podcast_ok = False
    gp_step_in_main = any(s["name"] == "generate_podcast" for s in step_results)
    if not gp_step_in_main:
        gp_step = {
            "name": "generate_podcast",
            "status": "pending",
            "started_at": "",
            "finished_at": "",
            "duration_seconds": 0,
            "call_method": "",
            "input_files": [],
            "output_files": [],
            "stats": {},
            "warnings": [],
            "errors": [],
        }
        gp_start = time.time()
        gp_step["started_at"] = datetime.now(CST).isoformat()

        try:
            gp_result = call_step("generate_podcast",
                                  build_step_args("generate_podcast", project_root, date, use_llm))
            gp_step["call_method"] = gp_result.get("_call_method", "unknown")

            if gp_result.get("ok", False):
                gp_step["status"] = "success"
                podcast_ok = True
                for key in ["script_file", "audio_file", "log_file",
                            "script_length", "is_no_signal", "llm_used",
                            "tts_success", "voice"]:
                    if key in gp_result:
                        gp_step["stats"][key] = gp_result[key]
                        if key in ("script_file", "audio_file", "log_file"):
                            gp_step["output_files"].append(gp_result[key])
            else:
                # generate_podcast 失败不阻塞 pipeline
                gp_step["status"] = "warning"
                gp_step["warnings"].append("播客生成失败，不影响日报")
                gp_step["errors"] = gp_result.get("errors", [])
                logger.warning(f"  ⚠️ 播客生成失败（非阻塞）: {gp_result.get('errors', [])}")

        except Exception as e:
            gp_step["status"] = "warning"
            gp_step["warnings"].append("播客生成异常，不影响日报")
            gp_step["errors"] = [str(e)]
            logger.warning(f"  ⚠️ 播客生成异常（非阻塞）: {e}")

        gp_step["finished_at"] = datetime.now(CST).isoformat()
        gp_step["duration_seconds"] = time.time() - gp_start
        step_results.append(gp_step)

        # 校验
        if gp_step["status"] in ("success", "warning"):
            v_result = validate_generate_podcast(project_root, date)
            gp_step["stats"].update(v_result.get("stats", {}))
            gp_step["warnings"].extend(v_result.get("warnings", []))
    else:
        # 主循环已处理 generate_podcast，检查其状态
        for s in step_results:
            if s["name"] == "generate_podcast" and s["status"] in ("success", "warning"):
                podcast_ok = True

    # ── send_daily_report_email 步骤 ──
    # 只有 send_email=True 时才运行，默认 disabled
    se_step_in_main = any(s["name"] == "send_daily_report_email" for s in step_results)

    # email_sent: 始终初始化，再从主循环结果覆盖
    email_sent = False

    if send_email and not se_step_in_main:
        se_step = {
            "name": "send_daily_report_email",
            "status": "pending",
            "started_at": "",
            "finished_at": "",
            "duration_seconds": 0,
            "call_method": "",
            "input_files": [],
            "output_files": [],
            "stats": {},
            "warnings": [],
            "errors": [],
        }
        se_start = time.time()
        se_step["started_at"] = datetime.now(CST).isoformat()

        try:
            se_args = build_step_args("send_daily_report_email", project_root, date, use_llm)
            se_args["dry_run"] = dry_run
            if recipient:
                se_args["recipient"] = recipient

            se_result = call_step("send_daily_report_email", se_args)
            se_step["call_method"] = se_result.get("_call_method", "unknown")

            if se_result.get("ok", False):
                se_step["status"] = "success"
                email_sent = se_result.get("sent", False)
                for key in ["log_file", "sendable", "sent", "dry_run",
                            "subject", "html_length",
                            "markdown_attachment", "podcast_attachment",
                            "script_attachment"]:
                    if key in se_result:
                        se_step["stats"][key] = se_result[key]

                # 邮件日志记录播客缺失
                if not podcast_ok:
                    se_step["warnings"].append("播客音频缺失（generate_podcast 未成功）")
                    logger.warning("  ⚠️ 邮件：播客音频缺失，附件中不包含 MP3")
            else:
                se_step["status"] = "failed"
                se_step["errors"] = se_result.get("errors", [])
                logger.error(f"  ❌ 邮件发送失败: {se_result.get('errors', [])}")

        except Exception as e:
            se_step["status"] = "failed"
            se_step["errors"] = [str(e)]
            logger.error(f"  ❌ 邮件发送异常: {e}")

        se_step["finished_at"] = datetime.now(CST).isoformat()
        se_step["duration_seconds"] = time.time() - se_start
        step_results.append(se_step)

        # 校验
        if se_step["status"] in ("success", "warning"):
            v_result = validate_send_daily_report_email(project_root, date)
            se_step["stats"].update(v_result.get("stats", {}))
            se_step["warnings"].extend(v_result.get("warnings", []))

    elif not send_email and not se_step_in_main:
        # send_email=False，记录 skipped 状态
        se_skip = {
            "name": "send_daily_report_email",
            "status": "skipped",
            "started_at": datetime.now(CST).isoformat(),
            "finished_at": datetime.now(CST).isoformat(),
            "duration_seconds": 0,
            "call_method": "",
            "input_files": [],
            "output_files": [],
            "stats": {"sent": False, "dry_run": dry_run, "sendable": False, "reason": "send_email=False (默认禁用)"},
            "warnings": [],
            "errors": [],
        }
        step_results.append(se_skip)
        logger.info("  ⏭️ send_daily_report_email: 跳过（send_email=False）")

    # ── 最终状态 ──
    finished_at = datetime.now(CST)

    # 判断 sendable
    if not sendable:
        if any(s["name"] == "editor_review" and s["status"] in ("success", "warning")
               for s in step_results):
            er_step = next(s for s in step_results if s["name"] == "editor_review")
            sendable = er_step.get("stats", {}).get("sendable", False)

    if not sendable:
        v_result = validate_editor_review(project_root, date)
        sendable = v_result.get("stats", {}).get("sendable", False)

    # ── 最终报告文件 ──
    year, month = date.split("-")[0], date.split("-")[1]
    final_report_file = resolve_path(
        project_root, f"reports/daily/{year}/{month}/{date}.md")
    if not os.path.exists(final_report_file):
        # 试试 no_signal 日报
        ns_final = resolve_path(
            project_root, f"data/reports/{date}/final_report_{date}.md")
        if os.path.exists(ns_final):
            final_report_file = ns_final
        else:
            final_report_file = ""

    # ── 播客和邮件状态汇总 ──
    podcast_status = "⚠️ 未生成"
    podcast_audio = False
    for s in step_results:
        if s["name"] == "generate_podcast":
            podcast_status = "✅ 成功" if s["status"] == "success" else "⚠️ 失败/部分成功"
            podcast_audio = s.get("stats", {}).get("audio_exists", False) or s.get("stats", {}).get("tts_success", False)

    email_status = "⏭️ 未启用"
    for s in step_results:
        if s["name"] == "send_daily_report_email":
            if s["status"] == "skipped":
                email_status = "⏭️ 跳过（send_email=False）"
            elif s["status"] == "success":
                email_status = "✅ 已发送" if s.get("stats", {}).get("sent") else "🔍 Dry Run"
            else:
                email_status = "❌ 失败"

    # ── 写 run_manifest.json ──
    manifest = {
        "date": date,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "ok": pipeline_ok,
        "sendable": sendable,
        "report_type": report_type,
        "no_signal": no_signal,
        "final_report_file": final_report_file,
        "podcast_ok": podcast_ok,
        "podcast_audio": podcast_audio,
        "email_sent": email_sent,
        "steps": step_results,
        "errors": errors,
    }

    # 补充 podcast/email 状态到 manifest（写入前）
    manifest["podcast_status"] = podcast_status
    manifest["email_status"] = email_status

    manifest_file = os.path.join(run_dir, "run_manifest.json")
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    logger.info(f"运行清单已写入: {manifest_file}")

    # ── 写 step_status.json ──
    step_status = {}
    for s in step_results:
        step_status[s["name"]] = {
            "status": s["status"],
            "duration_seconds": s.get("duration_seconds", 0),
            "stats": s.get("stats", {}),
        }
    step_status_file = os.path.join(run_dir, "step_status.json")
    with open(step_status_file, "w", encoding="utf-8") as f:
        json.dump(step_status, f, ensure_ascii=False, indent=2)
    logger.info(f"步骤状态已写入: {step_status_file}")

    # ── 写 run_summary.md ──
    summary_md = generate_run_summary(date, manifest)
    summary_file = os.path.join(run_dir, "run_summary.md")
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(summary_md)
    logger.info(f"运行摘要已写入: {summary_file}")

    # ── send_email 占位（已接入 send_daily_report_email 步骤） ──
    # 邮件发送已作为 pipeline 步骤执行，此处仅记录日志
    if send_email:
        logger.info(f"  邮件发送: {email_status}")
    else:
        logger.info("  邮件发送: ⏭️ 未启用（--send-email true 启用）")

    # ── 汇总日志 ──
    logger.info("\n" + "=" * 60)
    logger.info("全流程运行完成")
    logger.info(f"  状态: {'✅ 成功' if pipeline_ok else '❌ 失败'}")
    logger.info(f"  日报类型: {'🔕 无信号日报' if no_signal else '📝 正常日报'}")
    logger.info(f"  可发送: {'✅ 是' if sendable else '⚠️ 否'}")
    logger.info(f"  播客: {podcast_status}")
    logger.info(f"  邮件: {email_status}")
    logger.info(f"  总耗时: {manifest['duration_seconds']:.1f}s")
    for s in step_results:
        status_icon = {"success": "✅", "warning": "⚠️", "failed": "❌",
                       "skipped": "⏭️", "skipped_no_signal": "🔕"}.get(s["status"], "?")
        logger.info(f"  {status_icon} {s['name']}: {s['status']} "
                    f"({s.get('duration_seconds', 0):.1f}s)")
    logger.info("=" * 60)

    return {
        "ok": pipeline_ok,
        "date": date,
        "report_type": report_type,
        "no_signal": no_signal,
        "run_dir": run_dir,
        "manifest_file": manifest_file,
        "summary_file": summary_file,
        "final_report_file": final_report_file,
        "sendable": sendable,
        "podcast_ok": podcast_ok,
        "email_sent": email_sent,
        "steps": step_results,
        "errors": errors,
    }


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(
        description="日报全流程总控脚本（13步+无信号分支+邮件）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "步骤顺序: collect → filter → source_health_report → "
            "tavily_gap_search → filter_merged → extract → score → "
            "analyze → generate_report → editor_review → generate_podcast → send_daily_report_email\n\n"
            "当 cleaned_count=0 时（Tavily gap search 后仍为0），"
            "自动走 no_signal 分支:\n"
            "  ... → filter_merged → generate_no_signal_report → editor_review → generate_podcast → send_daily_report_email\n\n"
            "send_daily_report_email 默认禁用，需 --send-email true 启用。\n"
            "邮件发送需通过安全门检查（sendable=true 等）。\n\n"
            "示例:\n"
            "  # 完整运行（不含邮件）\n"
            "  python run_daily_pipeline.py --project-root .\n\n"
            "  # 完整运行 + 发送邮件（dry_run）\n"
            "  python run_daily_pipeline.py --project-root . --send-email true\n\n"
            "  # 完整运行 + 真实发送邮件\n"
            "  python run_daily_pipeline.py --project-root . --send-email true --dry-run false\n\n"
            "  # 从 filter 开始\n"
            "  python run_daily_pipeline.py --project-root . "
            "--start-step filter\n\n"
            "  # 强制重跑\n"
            "  python run_daily_pipeline.py --project-root . --force true"
        ),
    )
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", default=None,
                        help="日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--start-step", default=None,
                        choices=STEP_ORDER,
                        help="从哪一步开始运行")
    parser.add_argument("--end-step", default=None,
                        choices=STEP_ORDER,
                        help="运行到哪一步停止")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用LLM (true/false)")
    parser.add_argument("--send-email", default="false",
                        help="是否发送日报邮件 (true/false, 默认禁用，需 sendable=true 通过安全门)")
    parser.add_argument("--recipient", default=None,
                        help="邮件收件人（覆盖 config/email.yaml 配置）")
    parser.add_argument("--resume", default="true",
                        help="已有合格输出可跳过 (true/false)")
    parser.add_argument("--force", default="false",
                        help="强制重跑 (true/false)")
    parser.add_argument("--dry-run", default="false",
                        help="只检查不执行 (true/false)")
    parser.add_argument("--continue-on-error", default="false",
                        help="任一步失败后是否继续 (true/false)")

    args = parser.parse_args()

    use_llm = args.use_llm.lower() in ("true", "1", "yes")
    send_email = args.send_email.lower() in ("true", "1", "yes")
    resume = args.resume.lower() in ("true", "1", "yes")
    force = args.force.lower() in ("true", "1", "yes")
    dry_run = args.dry_run.lower() in ("true", "1", "yes")
    continue_on_error = args.continue_on_error.lower() in ("true", "1", "yes")

    result = run_daily_pipeline(
        project_root=args.project_root,
        date=args.date,
        start_step=args.start_step,
        end_step=args.end_step,
        use_llm=use_llm,
        send_email=send_email,
        recipient=args.recipient,
        resume=resume,
        force=force,
        dry_run=dry_run,
        continue_on_error=continue_on_error,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()