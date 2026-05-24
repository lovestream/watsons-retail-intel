#!/usr/bin/env python3
"""generate_periodic_report — 周报/月报/年报生成器。

分 4 个 Phase:
  Phase 1: 分章节生成（每章独立 LLM 调用）
  Phase 2: 汇总审视（评估质量，输出修改意见）
  Phase 3: 定向优化（根据审视意见重新生成特定章节，最多 2 轮）
  Phase 4: 终稿合并 + 口播稿 + TTS 音频
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from skills.utils.llm_client import get_llm_client
from skills.generate_periodic_report.prompts import (
    WEEKLY_CHAPTERS, MONTHLY_CHAPTERS, YEARLY_CHAPTERS,
    SYSTEM_PROMPT_BASE, REVIEW_SYSTEM_PROMPT, PODCAST_SUMMARY_PROMPT,
)

logger = logging.getLogger(__name__)
LOG_PREFIX = "[PeriodicReport]"

MAX_REVISION_ROUNDS = 2
LLM_MODEL = "LongCat-Flash-Thinking"
MAX_TOKENS_CHAPTER = 8192
MAX_TOKENS_REVIEW = 4096
MAX_TOKENS_PODCAST = 8192
TTS_VOICE = "zh-CN-XiaoxiaoNeural"
TTS_RATE = "+5%"

_TYPE_NAMES = {"weekly": "\u5468\u62a5", "monthly": "\u6708\u62a5", "yearly": "\u5e74\u62a5"}

# __MARKER_APPEND_HERE__


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
        return f"{y}\u5e74{m}\u6708"
    elif report_type == "yearly":
        return f"{period_info['year']}\u5e74"
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


# __MARKER_PART2__


def _load_source_reports(report_type, period_info, project_root):
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
                    contents.append(f"=== \u65e5\u62a5 {date_str} ===\n{c}")
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
                    contents.append(f"=== \u5468\u62a5 {fname} ===\n{c}")
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
                    contents.append(f"=== \u6708\u62a5 {fname} ===\n{c}")
    if not contents:
        logger.warning(f"{LOG_PREFIX} \u672a\u627e\u5230\u6e90\u62a5\u544a: {report_type} {period_info}")
        return ""
    combined = "\n\n".join(contents)
    logger.info(f"{LOG_PREFIX} \u52a0\u8f7d\u6e90\u62a5\u544a: {len(contents)} \u4efd, {len(combined)} \u5b57\u7b26")
    return combined


# __MARKER_PART3__


def phase1_generate_chapters(report_type, source_text, period_label):
    """Phase 1: \u9010\u7ae0\u751f\u6210\u62a5\u544a\u5185\u5bb9\u3002"""
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
            f"\n\u5f53\u524d\u4efb\u52a1\uff1a\u751f\u6210{report_name}\u7684\u7b2c {ch_id} \u7ae0\u300c{ch_title}\u300d\u3002\n"
            f"\u62a5\u544a\u5468\u671f\uff1a{period_label}\n"
        )
        user_msg = (
            f"\u4ee5\u4e0b\u662f\u6e90\u6750\u6599\uff1a\n\n{source_text}\n\n---\n\n"
            f"\u8bf7\u751f\u6210\u7b2c {ch_id} \u7ae0\u300c{ch_title}\u300d\u7684\u5185\u5bb9\u3002\n"
            f"\u5177\u4f53\u8981\u6c42\uff1a{ch_prompt}\n\n"
            f"\u8f93\u51fa\u683c\u5f0f\uff1a\u4ee5 ## {ch_id} {ch_title} \u5f00\u5934\uff0c\u7eaf Markdown\u3002"
        )

        logger.info(f"{LOG_PREFIX} Phase1: \u751f\u6210\u7ae0\u8282 {ch_id} {ch_title}")
        result = client.chat(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            max_tokens=MAX_TOKENS_CHAPTER,
            model=LLM_MODEL,
        )

        if result["ok"] and result["content"].strip():
            results[ch_id] = result["content"].strip()
            logger.info(f"{LOG_PREFIX}   \u2713 {ch_id}: {len(results[ch_id])} \u5b57\u7b26")
        else:
            logger.warning(f"{LOG_PREFIX}   \u2717 {ch_id} \u5931\u8d25: {result.get('error', 'empty')}")
            results[ch_id] = f"## {ch_id} {ch_title}\n\n\uff08\u751f\u6210\u5931\u8d25\uff0c\u5f85\u8865\u5145\uff09"

    return results


def phase2_review(chapters, report_type):
    """Phase 2: \u6c47\u603b\u5ba1\u89c6\u3002"""
    full_text = "\n\n".join(chapters[k] for k in sorted(chapters.keys()))
    client = get_llm_client()

    user_msg = (
        f"\u4ee5\u4e0b\u662f\u4e00\u4efd{_TYPE_NAMES[report_type]}\u7684\u5b8c\u6574\u521d\u7a3f\uff1a\n\n"
        f"{full_text}\n\n---\n\u8bf7\u6309\u8981\u6c42\u5ba1\u9605\u5e76\u8f93\u51fa JSON \u683c\u5f0f\u7684\u4fee\u6539\u610f\u89c1\u3002"
    )

    logger.info(f"{LOG_PREFIX} Phase2: \u5ba1\u89c6\u5168\u6587 ({len(full_text)} \u5b57\u7b26)")
    result = client.chat(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=REVIEW_SYSTEM_PROMPT,
        response_format="json",
        max_tokens=MAX_TOKENS_REVIEW,
        model=LLM_MODEL,
    )

    if result["ok"] and result.get("parsed"):
        review = result["parsed"]
        quality = review.get("overall_quality", "unknown")
        revisions = review.get("revisions_needed", [])
        logger.info(f"{LOG_PREFIX}   \u8d28\u91cf={quality}, \u9700\u4fee\u6539={len(revisions)}\u7ae0")
        return review

    logger.warning(f"{LOG_PREFIX}   \u5ba1\u89c6\u5931\u8d25\uff0c\u8df3\u8fc7\u4f18\u5316")
    return {"overall_quality": "acceptable", "revisions_needed": [],
            "summary": "\u5ba1\u89c6\u5931\u8d25\uff0c\u4fdd\u7559\u539f\u7a3f"}


# __MARKER_PART4__


def phase3_revise(chapters, review, report_type, source_text, period_label):
    """Phase 3: \u5b9a\u5411\u4f18\u5316\u3002"""
    revisions = review.get("revisions_needed", [])
    if not revisions:
        logger.info(f"{LOG_PREFIX} Phase3: \u65e0\u9700\u4fee\u6539")
        return chapters

    client = get_llm_client()
    all_ch = _get_chapters(report_type)
    ch_map = {ch["id"]: ch for ch in all_ch}

    for round_num in range(MAX_REVISION_ROUNDS):
        if not revisions:
            break
        logger.info(f"{LOG_PREFIX} Phase3 \u7b2c{round_num+1}\u8f6e: \u4f18\u5316 {len(revisions)} \u4e2a\u7ae0\u8282")

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
                f"\n\u4f18\u5316{_TYPE_NAMES[report_type]}\u7b2c {ch_id} \u7ae0\u300c{ch_def['title']}\u300d\u3002\n"
                f"\u62a5\u544a\u5468\u671f\uff1a{period_label}\n"
            )
            user_msg = (
                f"\u6e90\u6750\u6599\uff1a\n{source_text}\n\n---\n"
                f"\u5176\u4ed6\u7ae0\u8282\u6458\u8981\uff1a\n{other_summary}\n\n---\n"
                f"\u5f53\u524d\u7248\u672c\uff1a\n{chapters[ch_id]}\n\n---\n"
                f"\u5ba1\u9605\u610f\u89c1\uff1a\u95ee\u9898\uff1a{issue}\n\u4f18\u5316\u65b9\u5411\uff1a{direction}\n\n"
                f"\u8bf7\u91cd\u65b0\u751f\u6210\u3002\u4ee5 ## {ch_id} {ch_def['title']} \u5f00\u5934\u3002"
            )

            result = client.chat(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                max_tokens=MAX_TOKENS_CHAPTER,
                model=LLM_MODEL,
            )
            if result["ok"] and result["content"].strip():
                chapters[ch_id] = result["content"].strip()
                logger.info(f"{LOG_PREFIX}     \u2713 {ch_id} \u5df2\u4f18\u5316")
            else:
                logger.warning(f"{LOG_PREFIX}     \u2717 {ch_id} \u4f18\u5316\u5931\u8d25")

        # \u518d\u6b21\u5ba1\u89c6
        if round_num < MAX_REVISION_ROUNDS - 1:
            re_review = phase2_review(chapters, report_type)
            revisions = re_review.get("revisions_needed", [])
            if not revisions:
                logger.info(f"{LOG_PREFIX}   \u7b2c{round_num+1}\u8f6e\u540e\u65e0\u9700\u7ee7\u7eed")
                break

    return chapters


# __MARKER_PART5__


def phase4_finalize(chapters, report_type, period_label, period_info,
                    project_root):
    """Phase 4: \u7ec8\u7a3f\u5408\u5e76 + \u53e3\u64ad\u7a3f + TTS\u3002"""
    result_dict = {"report_path": "", "podcast_script": "", "audio_path": ""}

    # \u5408\u5e76\u7ec8\u7a3f
    title = f"\u5373\u65f6\u96f6\u552e \u00d7 \u4e2a\u62a4\u7f8e\u5986\u7ecf\u8425{_TYPE_NAMES[report_type]}\uff5c{period_label}"
    full_report = f"# {title}\n\n"
    for ch_id in sorted(chapters.keys()):
        full_report += chapters[ch_id] + "\n\n"

    # \u4fdd\u5b58\u62a5\u544a
    report_path = _get_report_path(report_type, period_info, project_root)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(full_report)
    result_dict["report_path"] = report_path
    logger.info(f"{LOG_PREFIX} Phase4: \u62a5\u544a \u2192 {report_path} ({len(full_report)} \u5b57\u7b26)")

    # \u751f\u6210\u53e3\u64ad\u7a3f
    podcast_script = _generate_podcast_script(full_report, report_type, period_label)
    if podcast_script:
        script_path = _get_script_path(report_type, period_info, project_root)
        os.makedirs(os.path.dirname(script_path), exist_ok=True)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(podcast_script)
        result_dict["podcast_script"] = script_path
        logger.info(f"{LOG_PREFIX}   \u53e3\u64ad\u7a3f: {len(podcast_script)} \u5b57\u7b26")

        # TTS
        audio_path = _get_audio_path(report_type, period_info, project_root)
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        tts_ok = asyncio.run(_generate_tts(podcast_script, audio_path))
        if tts_ok:
            result_dict["audio_path"] = audio_path
            size_mb = os.path.getsize(audio_path) / 1024 / 1024
            logger.info(f"{LOG_PREFIX}   \u97f3\u9891: {size_mb:.1f}MB")
        else:
            logger.warning(f"{LOG_PREFIX}   TTS \u5931\u8d25\uff08\u4e0d\u963b\u585e\uff09")

    return result_dict


def _generate_podcast_script(full_report, report_type, period_label):
    client = get_llm_client()
    if report_type == "weekly":
        target_chars, target_min = "5000-8000", "12-20"
    else:
        target_chars, target_min = "4500-6000", "12-15"

    system = (
        PODCAST_SUMMARY_PROMPT +
        f"\n\n\u62a5\u544a\u7c7b\u578b: {_TYPE_NAMES[report_type]}\n"
        f"\u62a5\u544a\u5468\u671f: {period_label}\n"
        f"\u76ee\u6807\u5b57\u6570: {target_chars} \u4e2d\u6587\u5b57\u7b26\uff08\u7ea6 {target_min} \u5206\u949f\uff09\n"
    )
    user_msg = f"\u4ee5\u4e0b\u662f\u5b8c\u6574\u62a5\u544a\uff0c\u8bf7\u8f6c\u5316\u4e3a\u53e3\u64ad\u7a3f\uff1a\n\n{full_report}"

    result = client.chat(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        max_tokens=MAX_TOKENS_PODCAST,
        model=LLM_MODEL,
    )
    if result["ok"] and result["content"].strip():
        script = _strip_markdown(result["content"].strip())
        if len(script) < 2000:
            logger.warning(f"{LOG_PREFIX} \u53e3\u64ad\u7a3f\u8fc7\u77ed: {len(script)} \u5b57\u7b26")
        return script
    logger.warning(f"{LOG_PREFIX} \u53e3\u64ad\u7a3f\u751f\u6210\u5931\u8d25")
    return ""


async def _generate_tts(text, output_path):
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE)
        await communicate.save(output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except ImportError:
        logger.warning(f"{LOG_PREFIX} edge-tts \u672a\u5b89\u88c5")
        return False
    except Exception as e:
        logger.error(f"{LOG_PREFIX} TTS \u5931\u8d25: {e}")
        return False


# __MARKER_PART6__


def _get_report_path(report_type, period_info, project_root):
    if report_type == "weekly":
        year = period_info["start"][:4]
        start_date = datetime.strptime(period_info["start"], "%Y-%m-%d")
        week_num = start_date.isocalendar()[1]
        return os.path.join(project_root, "reports", "weekly", year,
                            f"{year}-W{week_num:02d}.md")
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
        year = period_info["start"][:4]
        start_date = datetime.strptime(period_info["start"], "%Y-%m-%d")
        week_num = start_date.isocalendar()[1]
        return os.path.join(base, f"weekly-{year}-W{week_num:02d}.txt")
    elif report_type == "monthly":
        y, m = period_info["year"], period_info["month"]
        return os.path.join(base, f"monthly-{y}-{m}.txt")
    elif report_type == "yearly":
        return os.path.join(base, f"yearly-{period_info['year']}.txt")
    return ""


def _get_audio_path(report_type, period_info, project_root):
    base = os.path.join(project_root, "podcasts", "audio")
    if report_type == "weekly":
        year = period_info["start"][:4]
        start_date = datetime.strptime(period_info["start"], "%Y-%m-%d")
        week_num = start_date.isocalendar()[1]
        return os.path.join(base, f"weekly-{year}-W{week_num:02d}.mp3")
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
            send_email_with_attachments)
    except ImportError:
        logger.warning(f"{LOG_PREFIX} \u90ae\u4ef6\u6a21\u5757\u5bfc\u5165\u5931\u8d25")
        return

    subject = f"\u5373\u65f6\u96f6\u552e \u00d7 \u4e2a\u62a4\u7f8e\u5986\u7ecf\u8425{_TYPE_NAMES[report_type]}\uff5c{period_label}"
    attachments = []
    if final_result.get("report_path") and os.path.exists(final_result["report_path"]):
        attachments.append(final_result["report_path"])
    if final_result.get("audio_path") and os.path.exists(final_result["audio_path"]):
        attachments.append(final_result["audio_path"])

    try:
        send_email_with_attachments(
            to=recipient, subject=subject,
            body=f"\u8bf7\u67e5\u6536{_TYPE_NAMES[report_type]}\uff08{period_label}\uff09\u3002",
            attachments=attachments,
        )
        logger.info(f"{LOG_PREFIX} \u90ae\u4ef6\u5df2\u53d1\u9001 \u2192 {recipient}")
    except Exception as e:
        logger.error(f"{LOG_PREFIX} \u90ae\u4ef6\u53d1\u9001\u5931\u8d25: {e}")


# __MARKER_PART7__


def generate_periodic_report(report_type, period_info, project_root=".",
                             send_email=True, recipient=""):
    """Main entry: \u751f\u6210\u5468\u62a5/\u6708\u62a5/\u5e74\u62a5\u7684\u5b8c\u6574\u6d41\u7a0b\u3002"""
    period_label = _make_period_label(report_type, period_info)
    logger.info(f"{LOG_PREFIX} === \u5f00\u59cb\u751f\u6210{_TYPE_NAMES[report_type]}: {period_label} ===")

    source_text = _load_source_reports(report_type, period_info, project_root)
    if not source_text:
        logger.error(f"{LOG_PREFIX} \u65e0\u6e90\u6750\u6599")
        return {"success": False, "error": "no_source_reports"}

    # Phase 1
    chapters = phase1_generate_chapters(report_type, source_text, period_label)
    # Phase 2
    review = phase2_review(chapters, report_type)
    # Phase 3
    chapters = phase3_revise(chapters, review, report_type, source_text, period_label)
    # Phase 4
    final = phase4_finalize(chapters, report_type, period_label, period_info, project_root)

    if send_email and recipient and final.get("report_path"):
        _send_report_email(report_type, period_label, final, recipient, project_root)

    logger.info(f"{LOG_PREFIX} === {_TYPE_NAMES[report_type]}\u751f\u6210\u5b8c\u6210 ===")
    return {
        "success": True,
        "report_path": final.get("report_path", ""),
        "audio_path": final.get("audio_path", ""),
        "podcast_script": final.get("podcast_script", ""),
    }


def main():
    parser = argparse.ArgumentParser(description="\u751f\u6210\u5468\u62a5/\u6708\u62a5/\u5e74\u62a5")
    parser.add_argument("--type", required=True, choices=["weekly", "monthly", "yearly"])
    parser.add_argument("--start", default="", help="weekly start (YYYY-MM-DD)")
    parser.add_argument("--end", default="", help="weekly end (YYYY-MM-DD)")
    parser.add_argument("--year", default="")
    parser.add_argument("--month", default="")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--send-email", default="true")
    parser.add_argument("--recipient", default="123399974@qq.com")
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
    )

    if result.get("success"):
        print(f"\u2705 \u62a5\u544a: {result.get('report_path', '')}")
        if result.get("audio_path"):
            print(f"\ud83c\udf99\ufe0f \u97f3\u9891: {result['audio_path']}")
    else:
        print(f"\u274c \u5931\u8d25: {result.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
