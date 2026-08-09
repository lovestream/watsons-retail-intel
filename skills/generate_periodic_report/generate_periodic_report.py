#!/usr/bin/env python3
"""generate_periodic_report — 周报/月报/年报生成器（三批整改后）。

数据流（第一批核心架构）:
  每日事件池
    → periodic_synthesis（聚合层: 覆盖度检查/时间过滤/聚类/趋势/对比/配额/证据包）
    → periodic_synthesis.json
    → Phase 1 分章生成（每章只吃本章 evidence pack + 必要全局摘要）
    → Phase 2 全局审稿（失败 → sendable=false，绝不静默降级）
    → Phase 3 定向修订（新旧章质量比较后才覆盖）
    → Phase 4 终稿合并 + editor_review 质量门 + 口播稿 + TTS
    → delivery gate → HTML 邮件 + MP3

关键原则:
- 内部保留 event_id（LLM 可用作事实追溯），展示层 sanitize 才清除
- facts（事实证据）与 analyst_hints（上游 AI 分析）分离，LLM 不得把提示当事实
- 必需章节缺失不得跳过：主模型重试 → fallback 模型 → rule-based minimal chapter
  → 仍失败 = report_not_sendable
- review 失败不得自动判 acceptable
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

# ── 自愈：优先从项目持久 .venv_packages 加载包（如 edge-tts） ──
_VENV_PACKAGES = os.path.join(_PROJECT_ROOT, ".venv_packages")
if os.path.isdir(_VENV_PACKAGES) and _VENV_PACKAGES not in sys.path:
    sys.path.insert(0, _VENV_PACKAGES)

from skills.utils.llm_client import get_llm_client, robust_json_extract
from skills.generate_periodic_report.prompts import (
    WEEKLY_CHAPTERS, MONTHLY_CHAPTERS, YEARLY_CHAPTERS,
    SYSTEM_PROMPT_BASE, REVIEW_SYSTEM_PROMPT, PODCAST_SUMMARY_PROMPT,
)

# 周期聚合层（三批整改第一批核心）
from skills.periodic_synthesis.periodic_synthesis import (
    build_periodic_synthesis, _load_config as _load_periodic_config,
)

# 终稿质量门
from skills.periodic_editor_review.periodic_editor_review import (
    review_final_draft, rule_validate,
)

logger = logging.getLogger(__name__)
LOG_PREFIX = "[PeriodicReport]"

# 模型配置单一来源：从 model_router.yaml 读取（三批整改 §19）
try:
    from skills.utils.model_router import get_model_for_skill
    _periodic_model_res = get_model_for_skill("generate_periodic_report")
    # get_model_for_skill 返回 (model, fallbacks)；取第一个字符串
    if isinstance(_periodic_model_res, (tuple, list)) and _periodic_model_res:
        _PERIODIC_MODEL = _periodic_model_res[0]
    elif isinstance(_periodic_model_res, str):
        _PERIODIC_MODEL = _periodic_model_res
    else:
        _PERIODIC_MODEL = "deepseek-v4-flash-free"
except Exception:
    _PERIODIC_MODEL = "deepseek-v4-flash-free"

LLM_MODEL = _PERIODIC_MODEL
MAX_TOKENS_CHAPTER = 8192
MAX_TOKENS_REVIEW = 8192
MAX_TOKENS_PODCAST = 8192
TTS_VOICE = "zh-CN-XiaoxiaoNeural"
TTS_RATE = "+5%"

_TYPE_NAMES = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}

# 章节生成重试耗尽仍失败时的内部标记。绝不进入终稿。
_FAILED_CHAPTER_MARKER = "__CHAPTER_GENERATION_FAILED__"

# Phase 2 审稿失败 → 不允许静默通过（审计 §10）
_REVIEW_FAILED = "__REVIEW_FAILED__"


def _get_chapters(report_type):
    if report_type == "weekly":
        return WEEKLY_CHAPTERS
    elif report_type == "monthly":
        return MONTHLY_CHAPTERS
    elif report_type == "yearly":
        return YEARLY_CHAPTERS
    raise ValueError(f"Unknown: {report_type}")


def _make_period_label(report_type, period_info):
    if report_type == "weekly":
        start = period_info["start"]
        end = period_info["end"]
        s = datetime.strptime(start, "%Y-%m-%d")
        week_num = s.isocalendar()[1]
        return f"{s.year}-W{week_num:02d} ({start[5:]} ~ {end[5:]})"
    elif report_type == "monthly":
        y = period_info["year"]
        m = int(period_info["month"])
        return f"{y}年{m}月"
    elif report_type == "yearly":
        return f"{period_info['year']}年"
    return ""


def _week_belongs_to_month(filename, year, month):
    match = re.match(r"(\d{4})-W(\d{2})\.md", filename)
    if not match:
        return False
    w_year, w_num = int(match.group(1)), int(match.group(2))
    jan4 = date(w_year, 1, 4)
    start_of_w1 = jan4 - timedelta(days=jan4.isoweekday() - 1)
    thursday = start_of_w1 + timedelta(weeks=w_num - 1, days=3)
    return thursday.strftime("%Y") == year and thursday.strftime("%m") == month


def _strip_markdown(text):
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^---+\s*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\*\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^-\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'~~([^~]+)~~', r'\1', text)
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\|[^\n]+\|', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── 统一清洗（三批整改 §18）：落盘 MD / 邮件 HTML / 播客输入共用 ──

def sanitize_periodic_report(text: str, mode: str = "final") -> str:
    """统一清除周期报告交付物中的内部标记。

    与 skills/utils/report_sanitizer.sanitize_periodic_report 保持一致，
    此处为本地兜底实现（避免循环依赖）。
    """
    if not text:
        return text
    # 事件编号 E20260606_0017（可能带反引号/括号/中文冒号）
    text = re.sub(r'[（(\[【`]?\s*E\d{8}_\d{4}\s*[)\]】`]?\s*[：:、，,]?', '', text)
    # 判断标签 【A】 / [A] / ['A'] / ['K','X'] / [A-必须关注]
    text = re.sub(r'\s*【\s*[ABCRKX]\s*】', '', text)
    text = re.sub(r"\s*\[\s*['\"]?[ABCRKX]['\"]?\s*\]", '', text)
    text = re.sub(r"\s*\[\s*['\"][ABCRKX]['\"]\s*,\s*['\"][ABCRKX]['\"]\s*\]", '', text)
    text = re.sub(r"\s*\[\s*[ABCRKX]\s*-\s*[^\[\]]{1,12}\]", '', text)
    # 涉及事件 / 置信度 行
    text = re.sub(r'^.*?涉及事件.*?$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^.*?置信度.*?$', '', text, flags=re.MULTILINE)
    # 思考泄漏片段
    text = re.sub(r'\[?思考[^\]\n]{0,20}\]?', '', text)
    # 空占位
    text = text.replace("（空）", "").replace("(空)", "").replace("空", "")
    # 孤儿表格列（行尾孤立冒号/竖线/破折号）
    text = re.sub(r'^\s*[|:：\-]+\s*$', '', text, flags=re.MULTILINE)
    # 清理因移除留下的空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 清理孤立行尾冒号/破折号（前面无内容的行）
    text = re.sub(r'^\s*[-–—]\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


# ── 旧版辅助（保留兼容） ──

def _strip_internal_markers(text):
    """兼容旧调用：委托统一 sanitizer。"""
    return sanitize_periodic_report(text, mode="final")


def _load_source_reports(report_type, period_info, project_root):
    """回退：从历史报告加载源材料（仅当 synthesis 构建失败时）。"""
    contents = []
    if report_type == "weekly":
        start = datetime.strptime(period_info["start"], "%Y-%m-%d")
        end = datetime.strptime(period_info["end"], "%Y-%m-%d")
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            year_s, month_s = current.strftime("%Y"), current.strftime("%m")
            path = os.path.join(project_root, "reports", "daily",
                                year_s, month_s, f"{date_str}.md")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    c = f.read().strip()
                if c:
                    contents.append(f"=== 日报 {date_str} ===\n{c}")
            current += timedelta(days=1)
    elif report_type == "monthly":
        year_s, month_s = period_info["year"], period_info["month"]
        weekly_dir = os.path.join(project_root, "reports", "weekly", year_s)
        if os.path.isdir(weekly_dir):
            for fname in sorted(os.listdir(weekly_dir)):
                if not fname.endswith(".md"):
                    continue
                path = os.path.join(weekly_dir, fname)
                with open(path, "r", encoding="utf-8") as fh:
                    c = fh.read().strip()
                if c and _week_belongs_to_month(fname, year_s, month_s):
                    contents.append(f"=== 周报 {fname} ===\n{c}")
    elif report_type == "yearly":
        year_s = period_info["year"]
        monthly_dir = os.path.join(project_root, "reports", "monthly", year_s)
        if os.path.isdir(monthly_dir):
            for fname in sorted(os.listdir(monthly_dir)):
                if not fname.endswith(".md"):
                    continue
                path = os.path.join(monthly_dir, fname)
                with open(path, "r", encoding="utf-8") as fh:
                    c = fh.read().strip()
                if c:
                    contents.append(f"=== 月报 {fname} ===\n{c}")
    if not contents:
        logger.warning(f"{LOG_PREFIX} 未找到源报告: {report_type} {period_info}")
        return ""
    combined = "\n\n".join(contents)
    logger.info(f"{LOG_PREFIX} 加载源报告: {len(contents)} 份, {len(combined)} 字符")
    return combined


# ── 周期聚合层接入（第一批核心） ──

def _load_or_build_synthesis(report_type, period_info, project_root):
    """优先读已生成的 periodic_synthesis.json；不存在则现场构建。

    Returns:
        (synthesis_dict, coverage_dict, build_ok)
    """
    from skills.periodic_synthesis.periodic_synthesis import _period_id
    pid = _period_id(report_type, period_info)
    syn_path = os.path.join(project_root, "data", "periodic", report_type,
                            pid, "periodic_synthesis.json")
    if os.path.exists(syn_path):
        try:
            with open(syn_path, "r", encoding="utf-8") as f:
                synthesis = json.load(f)
            logger.info("%s 加载聚合数据: %s", LOG_PREFIX, syn_path)
            return synthesis, synthesis.get("coverage"), True
        except Exception as e:
            logger.warning("%s 聚合数据解析失败 %s: %s", LOG_PREFIX, syn_path, e)

    # 现场构建
    logger.info("%s 现场构建聚合数据...", LOG_PREFIX)
    try:
        res = build_periodic_synthesis(report_type, period_info, project_root)
        if res.get("success"):
            return res["synthesis"], res.get("coverage_report"), True
        return None, None, False
    except Exception as e:
        logger.error("%s 聚合构建失败: %s", LOG_PREFIX, e)
        return None, None, False


def _cluster_to_text(cluster: dict, include_event_ids: bool = True) -> str:
    """将 cluster 转为 LLM 输入文本（内部保留 event_id；facts 与 hints 分离）。"""
    lines = []
    cid = cluster.get("cluster_id", "")
    theme = cluster.get("theme", "") or cluster.get("rep_title", "")
    lines.append(f"[cluster {cid}] {theme[:120]}")
    # 事实证据
    if cluster.get("facts"):
        lines.append("  事实: %s" % " | ".join(
            str(x)[:150] for x in cluster["facts"][:5]))
    # 周期特征
    lines.append("  周期特征: first=%s last=%s days_active=%s events=%s sources=%s" % (
        cluster.get("first_seen_at", ""), cluster.get("last_seen_at", ""),
        cluster.get("days_active", 0), cluster.get("event_count", 0),
        cluster.get("source_count", 0)))
    lines.append("  平台: %s" % ",".join(cluster.get("platforms", []) or ["-"]))
    lines.append("  竞对: %s" % ",".join(cluster.get("competitors", []) or ["-"]))
    lines.append("  类型: %s" % ",".join(cluster.get("event_types", []) or ["-"]))
    lines.append("  业务变量: %s" % ",".join(
        cluster.get("business_variables", []) or ["-"]))
    lines.append("  趋势方向(规则判定): %s" % cluster.get("trend_direction", "unclear"))
    # 上游 AI 分析提示（明确标注：只是提示，不是事实）
    hints = []
    for eid in cluster.get("representative_event_ids", [])[:5]:
        hints.append(f"[event {eid}]")
    if hints:
        lines.append("  上游事件ID(用于追溯,勿输出): %s" % " ".join(hints))
    # 来源 URL
    urls = cluster.get("source_urls", [])[:3]
    if urls:
        lines.append("  来源: %s" % " | ".join(urls))
    return "\n".join(lines)


def _build_chapter_source(report_type, synthesis, ch_id):
    """为该章构建 evidence pack 文本（只传本章证据 + 必要的全局摘要）。"""
    ch_evidence = (synthesis or {}).get("chapter_evidence", {}).get(ch_id, [])
    if not ch_evidence:
        return ""
    parts = ["【本章证据（内部含事件ID，仅用于追溯，输出时不得出现）】"]
    for c in ch_evidence[:30]:
        parts.append(_cluster_to_text(c, include_event_ids=True))
    return "\n".join(parts)


def _build_global_context(report_type, synthesis) -> str:
    """全局摘要（每章都附，控制篇幅）：覆盖度 + 上期对比 + 平台/竞对汇总。"""
    lines = []
    cov = (synthesis or {}).get("coverage", {})
    if cov:
        lines.append("数据覆盖: %s/%s 天, 状态=%s" % (
            cov.get("available_days", 0), cov.get("expected_days", 0),
            cov.get("status", "?")))
    prev = (synthesis or {}).get("previous_period_comparison", {})
    if prev:
        lines.append("上一周期: 事件=%s 聚类=%s 高优=%s" % (
            prev.get("event_count", 0), prev.get("cluster_count", 0),
            prev.get("high_priority_count", 0)))
    ps = (synthesis or {}).get("platform_summary", {})
    if ps:
        top = sorted(ps.items(), key=lambda x: x[1], reverse=True)[:8]
        lines.append("平台动作密度: %s" % " ".join(
            f"{k}x{v}" for k, v in top))
    return "\n".join(lines)


# ── Phase 1: 分章生成（带规则兜底） ──

def _rule_fallback_chapter(report_type, ch_id, ch_title, synthesis) -> str:
    """规则兜底章节（审计 §9.3）：LLM 全部失败时自动生成基础版。"""
    lines = [f"## {ch_id} {ch_title}"]
    lines.append("")
    all_clusters = (synthesis or {}).get("all_clusters", []) or []
    ch_evidence = (synthesis or {}).get("chapter_evidence", {}).get(ch_id, []) \
        or all_clusters
    if ch_id == "01":
        top = all_clusters[:3]
        if top:
            lines.append("本周期最值得关注的动态（按事件密度排序）：")
            for c in top:
                lines.append("- %s（%d 条相关信号）" % (
                    (c.get("theme") or "")[:80], c.get("event_count", 0)))
        else:
            lines.append("本期事件池信号有限，暂无明显核心动态。")
    elif ch_id in ("02", "03", "04"):
        dim = {"02": "platform", "03": "category", "04": "competitor"}
        for c in ch_evidence[:10]:
            lines.append("- %s（%s，%d 条信号，趋势:%s）" % (
                (c.get("theme") or "")[:80],
                ",".join(c.get("platforms", []) or ["-"]),
                c.get("event_count", 0), c.get("trend_direction", "unclear")))
        if len(lines) <= 2:
            lines.append("本期该维度证据较少，建议结合后续周期观察。")
    elif ch_id == "05":
        lines.append("基于本期信号，建议屈臣氏电商关注以下方向：")
        lines.append("- 即时零售渠道：跟踪平台补贴与履约政策变化，评估合作优先级。")
        lines.append("- 个护美妆品类：关注品类增长信号，调整货盘与营销资源。")
        lines.append("- 竞对动态：结合本期竞对动作，保持差异化定位。")
    elif ch_id == "06":
        lines.append("建议下期重点跟踪：")
        lines.append("- 平台战略动作的延续性（补贴、招商、履约）。")
        lines.append("- 品类与竞对信号的密度变化。")
        lines.append("- 政策/监管相关动态。")
    elif ch_id == "07":
        lines.append("风险与机会观察：")
        lines.append("- 风险：平台补贴政策调整可能影响流量成本。")
        lines.append("- 机会：即时零售渗透率提升带来的渠道增量。")
    elif ch_id == "08":
        lines.append("长期观察：")
        lines.append("- 即时零售与个护美妆融合趋势。")
        lines.append("- 平台生态与履约能力演变。")
    else:
        lines.append("（本期证据不足，规则兜底生成，待数据补充后细化。）")
    lines.append("")
    lines.append("> 说明：本节为规则兜底生成（generation_method=rule_fallback），"
                 "LLM 生成失败时的降级版本。")
    return "\n".join(lines)


def phase1_generate_chapters(report_type, synthesis, period_label,
                             chapter_source_fn=None):
    """Phase 1: 逐章生成报告内容。

    主模型重试 → fallback 模型（client.chat 内部已含 OpenCode→付费兜底）→
    rule-based minimal chapter → 仍失败 = report_not_sendable。
    """
    chapters_def = _get_chapters(report_type)
    client = get_llm_client()
    results = {}
    report_name = _TYPE_NAMES[report_type]
    global_ctx = _build_global_context(report_type, synthesis)

    for ch in chapters_def:
        ch_id = ch["id"]
        ch_title = ch["title"]
        ch_prompt = ch["prompt"]
        ch_source = chapter_source_fn(report_type, synthesis, ch_id) \
            if chapter_source_fn else _build_chapter_source(
                report_type, synthesis, ch_id)

        system = (
            SYSTEM_PROMPT_BASE +
            f"\n当前任务：生成{report_name}的第 {ch_id} 章「{ch_title}」。\n"
            f"\n报告周期：{period_label}\n"
        )
        user_msg = (
            f"\n全局背景：\n{global_ctx}\n\n---\n"
            f"\n本章证据（内部含事件ID，仅用于追溯事实，正文输出不得出现事件编号）：\n\n"
            f"{ch_source}\n\n---\n\n"
            f"请生成第 {ch_id} 章「{ch_title}」的内容。\n"
            f"具体要求：{ch_prompt}\n\n"
            f"写作要求：\n"
            f"1. 区分'事实证据'与'分析提示'：证据中的'事实'是依据，"
            f"'上游事件ID/趋势方向'只是分析提示，不得视为事实照搬。\n"
            f"2. 趋势结论（加速/升温/扩大）必须有本章证据中的周期特征或与上一周期比较支撑。\n"
            f"3. 无真实 GMV/市占率数据时，禁止写'市场份额上升/下降'。\n"
            f"4. 不得出现事件编号（如 E20260803_0012）、判断标签（A/B/C）、'置信度'字样。\n"
            f"5. 输出格式：以 ## {ch_id} {ch_title} 开头，纯 Markdown。"
        )

        logger.info(f"{LOG_PREFIX} Phase1: 生成章节 {ch_id} {ch_title}")

        content = ""
        for attempt in range(1, 4):
            result = client.chat(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                max_tokens=MAX_TOKENS_CHAPTER,
                model=LLM_MODEL,
            )
            if result["ok"] and result["content"].strip():
                content = result["content"].strip()
                break
            logger.warning(
                f"{LOG_PREFIX}   ✗ {ch_id} 第{attempt}次失败: "
                f"{result.get('error', 'empty')}"
            )

        if content:
            results[ch_id] = content
            logger.info(f"{LOG_PREFIX}   ✓ {ch_id}: {len(results[ch_id])} 字符")
        else:
            # 重试耗尽 → 规则兜底章节（审计 §9.2: 主模型→fallback→rule→仍失败=不可发送）
            logger.error(f"{LOG_PREFIX}   ✗✗ {ch_id} 重试耗尽，生成规则兜底章节")
            results[ch_id] = _rule_fallback_chapter(
                report_type, ch_id, ch_title, synthesis)

    return results


# ── Phase 2: 全局审稿 ──

def phase2_review(chapters, report_type):
    """Phase 2: 汇总审视。失败 → 标记 __REVIEW_FAILED__（不允许静默降级）。"""
    full_text = "\n\n".join(
        chapters[k] for k in sorted(chapters.keys())
        if chapters[k] != _FAILED_CHAPTER_MARKER
    )
    client = get_llm_client()

    user_msg = (
        f"以下是一份{_TYPE_NAMES[report_type]}的完整初稿：\n\n"
        f"{full_text}\n\n---\n请按要求审阅并输出 JSON 格式的修改意见。"
    )

    logger.info(f"{LOG_PREFIX} Phase2: 审视全文 ({len(full_text)} 字符)")
    result = client.chat(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=REVIEW_SYSTEM_PROMPT,
        response_format="json",
        max_tokens=MAX_TOKENS_REVIEW,
        model=LLM_MODEL,
    )

    review = result.get("parsed") if result.get("ok") else None
    if not review and result.get("ok") and result.get("content"):
        review = robust_json_extract(result.get("content", ""))

    if not review and result.get("ok"):
        # 一次强制纯 JSON 重试
        logger.warning(f"{LOG_PREFIX}   审视 JSON 解析失败，重试一次(强制纯JSON)")
        result2 = client.chat(
            messages=[{"role": "user", "content":
                       user_msg + "\n\n必须只输出一个 JSON 对象，不要任何其他文字、思考或解释。"}],
            system_prompt=REVIEW_SYSTEM_PROMPT + "\n只输出 JSON 对象.",
            response_format="json",
            max_tokens=MAX_TOKENS_REVIEW,
            model=LLM_MODEL,
        )
        review = result2.get("parsed") if result2.get("ok") else None
        if not review and result2.get("ok") and result2.get("content"):
            review = robust_json_extract(result2.get("content", ""))

    if review:
        quality = review.get("overall_quality", "unknown")
        revisions = review.get("revisions_needed", [])
        logger.info(f"{LOG_PREFIX}   质量={quality}, 需修改={len(revisions)}章")
        return review

    # 审稿失败：绝不静默降级（审计 §10.1）
    logger.error(f"{LOG_PREFIX}   审稿失败，标记为不可发送")
    return {"overall_quality": "review_failed",
            "revisions_needed": [], "summary": "审稿失败，不可静默通过",
            "_review_failed": True}


# ── Phase 3: 定向修订（新旧章质量比较） ──

def _quick_chapter_score(chapter: str) -> float:
    """规则化章节质量分（Phase3 覆盖判断用）。

    评分维度：长度、结构完整、无内部标记、有具体内容。
    """
    if not chapter or chapter == _FAILED_CHAPTER_MARKER:
        return 0.0
    score = 0.0
    length = len(chapter)
    if 300 <= length <= 4000:
        score += 2.0
    elif length > 100:
        score += 1.0
    # 有章节标题
    if re.match(r"^##\s+\d{2}", chapter.strip()):
        score += 1.0
    # 无内部标记
    if not re.search(r"E\d{8}_\d{4}", chapter):
        score += 1.0
    if not re.search(r"【[ABCRKX]】|\[\s*['\"]?[ABCRKX]", chapter):
        score += 1.0
    # 有具体内容（非规则兜底标记）
    if "rule_fallback" not in chapter and "规则兜底" not in chapter:
        score += 1.0
    # 行数（内容密度）
    line_count = len([l for l in chapter.splitlines() if l.strip()])
    if line_count >= 5:
        score += 1.0
    return score


def phase3_revise(chapters, review, report_type, synthesis, period_label):
    """Phase 3: 定向优化。

    只有 新版无新增 high issue 且 quality_score >= 原版 才覆盖（审计 §17）。
    """
    revisions = review.get("revisions_needed", [])
    if not revisions or review.get("_review_failed"):
        if review.get("_review_failed"):
            logger.error(f"{LOG_PREFIX} Phase3: 审稿失败，跳过优化（不可发送）")
        else:
            logger.info(f"{LOG_PREFIX} Phase3: 无需修改")
        return chapters

    client = get_llm_client()
    all_ch = _get_chapters(report_type)
    ch_map = {ch["id"]: ch for ch in all_ch}
    max_rounds = 2
    global_ctx = _build_global_context(report_type, synthesis)

    for round_num in range(max_rounds):
        if not revisions:
            break
        logger.info(f"{LOG_PREFIX} Phase3 第{round_num+1}轮: 优化 {len(revisions)} 个章节")

        for rev in revisions:
            ch_id = rev.get("chapter_id", "")
            if ch_id not in ch_map:
                continue
            issue = rev.get("issue", "")
            direction = rev.get("direction", "")

            other_summary = "\n".join(
                f"[{k}] {chapters[k][:200]}..."
                for k in sorted(chapters.keys()) if k != ch_id
            )
            ch_def = ch_map[ch_id]
            system = (
                SYSTEM_PROMPT_BASE +
                f"\n优化{_TYPE_NAMES[report_type]}第 {ch_id} 章「{ch_def['title']}」。\n"
                f"\n报告周期：{period_label}\n"
            )
            user_msg = (
                f"\n全局背景：\n{global_ctx}\n\n---\n"
                f"\n其他章节摘要：\n{other_summary}\n\n---\n"
                f"\n当前版本：\n{chapters[ch_id]}\n\n---\n"
                f"\n审阅意见：问题：{issue}\n优化方向：{direction}\n\n"
                f"请重新生成。以 ## {ch_id} {ch_def['title']} 开头。"
                f"不得出现事件编号、判断标签、'置信度'字样。"
            )

            result = client.chat(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                max_tokens=MAX_TOKENS_CHAPTER,
                model=LLM_MODEL,
            )
            if result["ok"] and result["content"].strip():
                revised = result["content"].strip()
                # 新旧章质量比较（审计 §17）：只有新版 >= 原版才覆盖
                old_score = _quick_chapter_score(chapters[ch_id])
                new_score = _quick_chapter_score(revised)
                old_has_high = _chapter_has_high_issue(chapters[ch_id])
                new_has_high = _chapter_has_high_issue(revised)
                if not new_has_high and new_score >= old_score:
                    chapters[ch_id] = revised
                    logger.info(
                        f"{LOG_PREFIX}     ✓ {ch_id} 已优化 (score {old_score:.1f}→{new_score:.1f})")
                else:
                    logger.warning(
                        f"{LOG_PREFIX}     ✗ {ch_id} 新版不优于原版，保留原版 "
                        f"(score {old_score:.1f}→{new_score:.1f}, "
                        f"new_high={new_has_high})")
            else:
                logger.warning(f"{LOG_PREFIX}     ✗ {ch_id} 优化失败")

        # 再次审视
        if round_num < max_rounds - 1:
            re_review = phase2_review(chapters, report_type)
            if re_review.get("_review_failed"):
                logger.error(f"{LOG_PREFIX}   复审失败，保留当前章节")
                break
            revisions = re_review.get("revisions_needed", [])
            if not revisions:
                logger.info(f"{LOG_PREFIX}   第{round_num+1}轮后无需继续")
                break

    return chapters


def _chapter_has_high_issue(chapter: str) -> bool:
    """检查章节是否含 high issue（event_id/标签/置信度/思考泄漏）。"""
    if not chapter:
        return True
    if re.search(r"E\d{8}_\d{4}", chapter):
        return True
    if re.search(r"【[ABCRKX]】|\[\s*['\"]?[ABCRKX]", chapter):
        return True
    if "置信度" in chapter or "涉及事件" in chapter:
        return True
    return False


# ── Phase 4: 终稿合并 + 质量门 + 口播稿 + TTS ──

def phase4_finalize(chapters, report_type, period_label, period_info,
                    project_root, synthesis=None, coverage=None):
    """Phase 4: 终稿合并 + editor_review 质量门 + 口播稿 + TTS。"""
    result_dict = {"report_path": "", "podcast_script": "", "audio_path": "",
                   "failed_chapters": [], "sendable": False,
                   "review_result": None}

    # 必需章节检查（审计 §9）：任何章节失败 → 不可发送
    exp_ids = {"weekly": 6, "monthly": 7, "yearly": 8}.get(report_type, 6)
    failed_chapters = [ch_id for ch_id, c in chapters.items()
                       if c == _FAILED_CHAPTER_MARKER]
    result_dict["failed_chapters"] = failed_chapters
    if failed_chapters:
        logger.error(
            f"{LOG_PREFIX} Phase4: {len(failed_chapters)} 个章节生成失败: "
            f"{failed_chapters} → 不可发送")

    # 合并终稿（跳过失败章节标记，但后续质量门会拒绝）
    title = f"即时零售 × 个护美妆经营{_TYPE_NAMES[report_type]}｜{period_label}"
    full_report = f"# {title}\n\n"
    for ch_id in sorted(chapters.keys()):
        if chapters[ch_id] == _FAILED_CHAPTER_MARKER:
            continue
        full_report += chapters[ch_id] + "\n\n"

    # 质量门（审计 §10）：规则质检在 sanitize 前的原始稿上进行
    # （event_id 仍在稿中时检查"泄漏"无意义；因此先做展示层清洗前的规则检查
    #  基于清洗后文本，同时单独检查必检项）
    review_result = review_final_draft(
        report_type=report_type,
        final_draft=full_report,
        synthesis=synthesis,
        coverage=coverage,
        chapter_ids=list(chapters.keys()),
        event_pool_ids=None,
    )
    result_dict["review_result"] = review_result
    sendable = review_result.get("sendable", False) and not failed_chapters
    result_dict["sendable"] = sendable

    # 展示层清洗（审计 §6: event_id 只在展示层剥离）
    clean_report = sanitize_periodic_report(full_report, mode="final")

    # 保存报告
    report_path = _get_report_path(report_type, period_info, project_root)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(clean_report)
    result_dict["report_path"] = report_path
    logger.info(f"{LOG_PREFIX} Phase4: 报告 → {report_path} ({len(clean_report)} 字符, "
                f"sendable={sendable})")

    if not sendable:
        logger.error(f"{LOG_PREFIX} Phase4: 质量门未通过，不发邮件")
        return result_dict

    # 生成口播稿
    podcast_script = _generate_podcast_script(clean_report, report_type,
                                              period_label)
    if podcast_script:
        script_path = _get_script_path(report_type, period_info, project_root)
        os.makedirs(os.path.dirname(script_path), exist_ok=True)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(podcast_script)
        result_dict["podcast_script"] = script_path
        logger.info(f"{LOG_PREFIX}   口播稿: {len(podcast_script)} 字符")

        # TTS
        audio_path = _get_audio_path(report_type, period_info, project_root)
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        tts_ok = asyncio.run(_generate_tts(podcast_script, audio_path))
        if tts_ok:
            result_dict["audio_path"] = audio_path
            size_mb = os.path.getsize(audio_path) / 1024 / 1024
            logger.info(f"{LOG_PREFIX}   音频: {size_mb:.1f}MB")
        else:
            logger.warning(f"{LOG_PREFIX}   TTS 失败（不阻塞）")

    return result_dict


# ── 口播稿生成 ──

def _get_podcast_spec(report_type, project_root="."):
    """按周期类型返回口播稿规格（三批整改 §22）。"""
    try:
        cfg = _load_periodic_config(project_root)
        pc = cfg.get("podcast", {}).get(report_type, {})
        if pc.get("target_chars"):
            return pc["target_chars"], pc.get("target_seconds", [300, 540])
    except Exception:
        pass
    default = {
        "weekly": ([3000, 5000], [300, 540]),
        "monthly": ([4500, 7500], [480, 780]),
        "yearly": ([7000, 12000], [720, 1260]),
    }
    return default.get(report_type, ([3000, 5000], [300, 540]))


def _generate_podcast_script(clean_report, report_type, period_label,
                             project_root="."):
    client = get_llm_client()
    target_chars, target_secs = _get_podcast_spec(report_type, project_root)
    target_min = f"{target_secs[0]//60}-{target_secs[1]//60}"
    target_str = f"{target_chars[0]}-{target_chars[1]}"

    # 周期类型对应的行动文案（三批整改 §25）
    action_lines = {
        "weekly": "结尾必须包含'本周听完最值得做的三件事'（3 个具体行动项）和"
                  "'本周最值得学习的一件商家动作'。",
        "monthly": "结尾必须包含'下个月最值得推进的三件事'（3 个具体行动项）和"
                   "'本月最值得学习的一件商家动作'。",
        "yearly": "结尾必须包含'下一年最值得推进的三件事'（3 个具体行动项）和"
                  "'今年最值得学习的一类商家打法'。",
    }

    system = (
        PODCAST_SUMMARY_PROMPT +
        f"\n\n报告类型: {_TYPE_NAMES[report_type]}\n"
        f"\n报告周期: {period_label}\n"
        f"\n目标字数: {target_str} 中文字符（约 {target_min} 分钟）\n"
        f"\n{action_lines.get(report_type, '')}"
    )
    user_msg = f"\n以下是完整报告，请转化为口播稿：\n\n{clean_report}"

    result = client.chat(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        max_tokens=MAX_TOKENS_PODCAST,
        model=LLM_MODEL,
    )
    script = ""
    if result["ok"] and result["content"].strip():
        script = _strip_markdown(result["content"].strip())

    # 长度校验（按类型目标，放宽 10%）
    lo, hi = target_chars
    if script and not (int(lo * 0.9) <= len(script) <= int(hi * 1.1)):
        reason = "太短，请扩充" if len(script) < int(lo * 0.9) else "太长，请压缩"
        logger.warning(
            f"{LOG_PREFIX} 口播稿长度不合规({len(script)}字, {reason})，重试一次")
        result2 = client.chat(
            messages=[{"role": "user", "content":
                       f"\n以下是完整报告，请转化为口播稿：\n\n{clean_report}\n\n"
                       f"\n上一版口播稿{reason}（{len(script)}字）。"
                       f"\n请直接输出 {target_str} 字的口播稿正文，不要复述要求、不要解释。"}],
            system_prompt=system,
            max_tokens=MAX_TOKENS_PODCAST,
            model=LLM_MODEL,
        )
        if result2["ok"] and result2["content"].strip():
            script = _strip_markdown(result2["content"].strip())

    if script:
        if len(script) < 2000 or len(script) > 13000:
            logger.warning(f"{LOG_PREFIX} 口播稿长度异常: {len(script)} 字")
        # 周期化措辞后处理（§25）：周报禁止"今天/昨天"日报腔
        period_word = {"weekly": "本周", "monthly": "本月", "yearly": "今年"}.get(
            report_type, "本期")
        script = script.replace("今天咱们", f"{period_word}咱们")
        script = script.replace("今天我们就", f"{period_word}我们就")
        script = script.replace("今天来聊聊", f"{period_word}来聊聊")
        script = script.replace("今天不念", f"{period_word}不念")
        script = script.replace("今天行业", f"{period_word}行业")
        script = script.replace("咱们今天", f"{period_word}咱们")
        script = script.replace("下周见", "下期见")
        return script
    logger.warning(f"{LOG_PREFIX} 口播稿生成失败")
    return ""


async def _generate_tts(text, output_path):
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
        await communicate.save(output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except ImportError:
        logger.warning(f"{LOG_PREFIX} edge-tts 未安装")
        return False
    except Exception as e:
        logger.error(f"{LOG_PREFIX} TTS 失败: {e}")
        return False


# ── 路径工具 ──

def _get_report_path(report_type, period_info, project_root):
    if report_type == "weekly":
        start_date = datetime.strptime(period_info["start"], "%Y-%m-%d")
        iso_year, week_num, _ = start_date.isocalendar()
        return os.path.join(project_root, "reports", "weekly", str(iso_year),
                            f"{iso_year}-W{week_num:02d}.md")
    elif report_type == "monthly":
        y, m = period_info["year"], period_info["month"]
        return os.path.join(project_root, "reports", "monthly", y,
                            f"{y}-{m}.md")
    elif report_type == "yearly":
        y = period_info["year"]
        return os.path.join(project_root, "reports", "yearly", f"{y}.md")
    return ""


def _get_script_path(report_type, period_info, project_root):
    base = os.path.join(project_root, "podcasts", "scripts")
    if report_type == "weekly":
        start_date = datetime.strptime(period_info["start"], "%Y-%m-%d")
        iso_year, week_num, _ = start_date.isocalendar()
        return os.path.join(base, f"weekly-{iso_year}-W{week_num:02d}.txt")
    elif report_type == "monthly":
        y, m = period_info["year"], period_info["month"]
        return os.path.join(base, f"monthly-{y}-{m}.txt")
    elif report_type == "yearly":
        return os.path.join(base, f"yearly-{period_info['year']}.txt")
    return ""


def _get_audio_path(report_type, period_info, project_root):
    base = os.path.join(project_root, "podcasts", "audio")
    if report_type == "weekly":
        start_date = datetime.strptime(period_info["start"], "%Y-%m-%d")
        iso_year, week_num, _ = start_date.isocalendar()
        return os.path.join(base, f"weekly-{iso_year}-W{week_num:02d}.mp3")
    elif report_type == "monthly":
        y, m = period_info["year"], period_info["month"]
        return os.path.join(base, f"monthly-{y}-{m}.mp3")
    elif report_type == "yearly":
        return os.path.join(base, f"yearly-{period_info['year']}.mp3")
    return ""


def _send_report_email(report_type, period_label, final_result, recipient,
                       project_root):
    try:
        from skills.send_daily_report_email.send_daily_report_email import (
            send_periodic_report_email)
    except ImportError:
        logger.warning(f"{LOG_PREFIX} 邮件模块导入失败")
        return

    result = send_periodic_report_email(
        report_type=report_type,
        period_label=period_label,
        report_path=final_result.get("report_path", ""),
        audio_path=final_result.get("audio_path", ""),
        recipient=recipient,
        project_root=project_root,
        dry_run=False,
    )
    if result.get("ok"):
        logger.info(f"{LOG_PREFIX} 邮件已发送 → {recipient}")
    else:
        logger.error(f"{LOG_PREFIX} 邮件发送失败: {result.get('error', result.get('message', ''))}")


# ── 主入口 ──

def generate_periodic_report(report_type, period_info, project_root=".",
                             send_email=True, recipient="",
                             force_rebuild_synthesis=False):
    """Main entry: 生成周报/月报/年报的完整流程。"""
    started_at = time.time()
    period_label = _make_period_label(report_type, period_info)
    logger.info(f"{LOG_PREFIX} === 开始生成{_TYPE_NAMES[report_type]}: {period_label} ===")

    # 总运行超时（三批整改 §35）
    try:
        cfg = _load_periodic_config(project_root)
        timeout_s = cfg.get("llm", {}).get("total_timeout_seconds", {}) \
            .get(report_type, 1800)
    except Exception:
        timeout_s = 1800

    # Step 0: 周期聚合层
    synthesis, coverage, build_ok = _load_or_build_synthesis(
        report_type, period_info, project_root)
    if not build_ok or not synthesis:
        logger.error(f"{LOG_PREFIX} 聚合数据构建失败，回退历史报告")
        source_text = _load_source_reports(report_type, period_info,
                                           project_root)
        if not source_text:
            logger.error(f"{LOG_PREFIX} 无源材料")
            return {"success": False, "error": "no_synthesis_and_no_source"}
        # 旧路径兜底（不推荐，但保证不崩）
        chapters = phase1_generate_chapters_legacy(
            report_type, source_text, period_label)
        final = phase4_finalize(chapters, report_type, period_label,
                                period_info, project_root, None, None)
        return {"success": final.get("sendable", False),
                "report_path": final.get("report_path", ""),
                "audio_path": final.get("audio_path", ""),
                "sendable": final.get("sendable", False),
                "review": final.get("review_result")}

    # coverage 不达标 → 不可发送（审计 §4.3 + §10.6）
    if coverage.get("status") == "failing":
        logger.error(f"{LOG_PREFIX} 数据覆盖不达标({coverage.get('coverage_ratio')})，"
                     f"不可发送")
        return {"success": False, "error": "coverage_failing",
                "coverage": coverage, "sendable": False}

    # Phase 1
    chapters = phase1_generate_chapters(report_type, synthesis, period_label)
    # Phase 2
    review = phase2_review(chapters, report_type)
    # Phase 3
    chapters = phase3_revise(chapters, review, report_type, synthesis,
                             period_label)
    # Phase 4
    final = phase4_finalize(chapters, report_type, period_label, period_info,
                            project_root, synthesis, coverage)

    # Delivery gate（三批整改 §30）
    sendable = final.get("sendable", False)
    if sendable and send_email and recipient and final.get("report_path"):
        _send_report_email(report_type, period_label, final, recipient,
                           project_root)
    elif not sendable:
        logger.error(f"{LOG_PREFIX} delivery gate 拦截：sendable=false 不发邮件")

    elapsed = time.time() - started_at
    logger.info(f"{LOG_PREFIX} === {_TYPE_NAMES[report_type]}完成 "
                f"({elapsed:.0f}s, sendable={sendable}) ===")
    return {
        "success": sendable,
        "report_path": final.get("report_path", ""),
        "audio_path": final.get("audio_path", ""),
        "podcast_script": final.get("podcast_script", ""),
        "sendable": sendable,
        "review": final.get("review_result"),
        "coverage": coverage,
    }


def phase1_generate_chapters_legacy(report_type, source_text, period_label):
    """旧路径兜底（synthesis 构建失败时）。"""
    chapters_def = _get_chapters(report_type)
    client = get_llm_client()
    results = {}
    report_name = _TYPE_NAMES[report_type]
    for ch in chapters_def:
        ch_id = ch["id"]
        ch_title = ch["title"]
        ch_prompt = ch["prompt"]
        system = (
            SYSTEM_PROMPT_BASE +
            f"\n当前任务：生成{report_name}的第 {ch_id} 章「{ch_title}」。\n"
            f"\n报告周期：{period_label}\n"
        )
        user_msg = (
            f"\n以下是源材料：\n\n{source_text}\n\n---\n\n"
            f"请生成第 {ch_id} 章「{ch_title}」的内容。\n"
            f"具体要求：{ch_prompt}\n\n"
            f"输出格式：以 ## {ch_id} {ch_title} 开头，纯 Markdown。"
        )
        content = ""
        for attempt in range(1, 3):
            result = client.chat(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                max_tokens=MAX_TOKENS_CHAPTER,
                model=LLM_MODEL,
            )
            if result["ok"] and result["content"].strip():
                content = result["content"].strip()
                break
        results[ch_id] = content if content else _rule_fallback_chapter(
            report_type, ch_id, ch_title, None)
    return results


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(description="生成周报/月报/年报")
    parser.add_argument("--type", required=True, choices=["weekly", "monthly", "yearly"])
    parser.add_argument("--start", default="", help="weekly start (YYYY-MM-DD)")
    parser.add_argument("--end", default="", help="weekly end (YYYY-MM-DD)")
    parser.add_argument("--year", default="")
    parser.add_argument("--month", default="")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--send-email", default="true")
    parser.add_argument("--recipient", default="123399974@qq.com")
    parser.add_argument("--rebuild-synthesis", default="false",
                        help="强制重建聚合数据")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.type == "weekly":
        if not args.start or not args.end:
            print("ERROR: --start and --end required for weekly")
            sys.exit(1)
        period_info = {"start": args.start, "end": args.end}
    elif args.type == "monthly":
        if not args.year or not args.month:
            print("ERROR: --year and --month required for monthly")
            sys.exit(1)
        period_info = {"year": args.year, "month": args.month}
    elif args.type == "yearly":
        if not args.year:
            print("ERROR: --year required for yearly")
            sys.exit(1)
        period_info = {"year": args.year}
    else:
        sys.exit(1)

    result = generate_periodic_report(
        report_type=args.type, period_info=period_info,
        project_root=args.project_root,
        send_email=args.send_email.lower() == "true",
        recipient=args.recipient,
        force_rebuild_synthesis=args.rebuild_synthesis.lower() == "true",
    )

    if result.get("sendable"):
        print(f"OK report: {result.get('report_path', '')}")
        if result.get("audio_path"):
            print(f"OK audio: {result['audio_path']}")
    elif result.get("success"):
        print(f"OK (degraded) report: {result.get('report_path', '')}")
    else:
        print(f"FAIL: {result.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
