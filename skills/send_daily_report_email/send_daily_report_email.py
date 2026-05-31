#!/usr/bin/env python3
"""
send_daily_report_email — 日报邮件发送 Skill

读取终稿日报、播客音频、审稿报告，通过 SMTP 发送 HTML 日报邮件（含附件）。

安全门：只有满足 8 项条件才允许发送，否则仅记录日志。
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── 项目根目录 ──
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from markdown_it import MarkdownIt

# ===================== 常量 =====================

CORE_SECTIONS = [
    "01 今日一句话结论",
    "02 今日三条必听",
    "03 即时零售重点变化",
    "04 本地生活重点变化",
    "05 竞对观察",
    "06 对屈臣氏的机会点",
    "07 风险预警",
    "08 今日建议动作",
]

OPTIONAL_SECTIONS = [
    "09 每日八问",
    "11 近期延续观察",
]

EVENT_ID_PATTERN = re.compile(r"E\d{8}_\d{4}")

DEFAULT_SUBJECT_PREFIX = "即时零售 × 个护美妆经营日报"

# 系统备注
SYSTEM_NOTE = (
    '<div style="background:#f0f4f8;border-left:4px solid #3b82f6;padding:12px 16px;'
    'margin:0 0 20px 0;border-radius:4px;font-size:13px;color:#1e3a5f;line-height:1.7;">'
    "📊 本报告由「即时零售 × 个护美妆经营情报系统」自动生成，每日 08:00 推送。<br>"
    "附件包含完整 Markdown 日报和播客 MP3 音频。"
    "</div>"
)

# 邮件标题头
EMAIL_HEADER = (
    '<div style="text-align:center;padding:24px 0 8px 0;border-bottom:2px solid #e5e7eb;margin-bottom:20px;">'
    '<h1 style="font-size:22px;font-weight:700;color:#1e3a5f;margin:0 0 4px 0;">'
    '即时零售 × 个护美妆经营日报'
    '</h1>'
    '<p style="font-size:13px;color:#6b7280;margin:0;">{date} ｜ 屈臣氏电商经营决策参考</p>'
    '</div>'
)

# HTML 内联 CSS 样式
HTML_BODY_STYLE = (
    'style="font-family:\'PingFang SC\',\'Microsoft YaHei\',\'Helvetica Neue\',Arial,'
    'sans-serif;line-height:1.8;color:#1a1a1a;max-width:720px;margin:0 auto;padding:20px;"'
)

HTML_H2_STYLE = (
    'style="font-size:18px;font-weight:600;color:#1e3a5f;border-bottom:1px solid #e5e7eb;'
    'padding-bottom:8px;margin-top:28px;margin-bottom:16px;"'
)

HTML_H3_STYLE = (
    'style="font-size:15px;font-weight:600;color:#374151;margin-top:20px;margin-bottom:10px;"'
)

HTML_P_STYLE = (
    'style="font-size:14px;line-height:1.8;color:#1a1a1a;margin:8px 0;"'
)

HTML_UL_STYLE = (
    'style="font-size:14px;line-height:1.8;color:#1a1a1a;padding-left:20px;"'
)

HTML_BLOCKQUOTE_STYLE = (
    'style="border-left:3px solid #f59e0b;background:#fef3c7;padding:10px 16px;'
    'margin:12px 0;font-size:13px;color:#92400e;border-radius:0 4px 4px 0;"'
)

HTML_HR_STYLE = 'style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;"'

HTML_FOOTER_STYLE = (
    'style="font-size:12px;color:#6b7280;border-top:1px solid #e5e7eb;'
    'padding-top:12px;margin-top:24px;line-height:1.6;"'
)


# ===================== 日志 =====================

def _setup_logger(log_dir: Path, date: str) -> logging.Logger:
    """配置日志，同时输出到控制台和文件。"""
    logger = logging.getLogger("send_daily_report_email")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # 控制台
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                       datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(ch)

    # 文件
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_dir / "send_daily_report_email.log",
                             encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                       datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)

    return logger


# ===================== 配置读取 =====================

def _load_email_config(project_root: str) -> dict:
    """从 config/email.yaml 或环境变量读取 SMTP 配置。

    优先级：config/email.yaml > 环境变量。
    不允许硬编码密码；密码仅通过环境变量或加密的 key 引用传入。
    """
    import yaml

    config_path = Path(project_root) / "config" / "email.yaml"
    result = {
        "from_addr": "",
        "to_addrs": [],
    }
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        result["from_addr"] = cfg.get("from", os.environ.get("SMTP_FROM", ""))
        result["to_addrs"] = cfg.get("to", [])

    # 环境变量覆盖
    if os.environ.get("SMTP_FROM"):
        result["from_addr"] = os.environ["SMTP_FROM"]

    # to_addrs 可能是逗号分隔字符串
    if isinstance(result["to_addrs"], str):
        result["to_addrs"] = [a.strip() for a in result["to_addrs"].split(",") if a.strip()]

    return result


# ===================== Markdown → HTML =====================

def _clean_report_for_display(md_text: str) -> str:
    """清理日报文本，移除内部标记供展示用。"""
    # ── 0. 移除 LLM 思考泄露（开头非标题文字） ──
    md_text = re.sub(r'^[^#\n].*?(?=## 01|# )', '', md_text, count=1, flags=re.DOTALL)

    # ── 1. 移除事件 ID（各种格式） ──
    # （`E20260530_0037`，A） 或 （`E20260530_0037`、`E20260530_0003`，K）
    md_text = re.sub(r'（[`E\d_、，\s]*?[，,]\s*[A-Z]）', '', md_text)
    # （`E20260530_0037`） 不带标签
    md_text = re.sub(r'（[`E\d_、\s]+）', '', md_text)
    # `E20260530_0037`
    md_text = re.sub(r'`E\d{8}_\d{4}`', '', md_text)
    # 裸 E20260530_0037
    md_text = re.sub(r'E\d{8}_\d{4}', '', md_text)
    # [E20260530_0037] 方括号格式
    md_text = re.sub(r'\[E\d{8}_\d{4}\]', '', md_text)

    # ── 2. 移除判断标签行 ──
    md_text = re.sub(r'^\*?\*?判断标签[：:]\s*`?[A-Z]`?\*?\*?\s*$',
                     '', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'^\*?\*?涉及事件[：:].*$',
                     '', md_text, flags=re.MULTILINE)
    # 移除内联判断标签：标题/正文末尾的【A】【B】【C】【R】【K】【X】等
    md_text = re.sub(r'【[ABCRKX]】', '', md_text)

    # ── 3. 移除证据事件/置信度行 ──
    md_text = re.sub(r'^-\s*\*\*证据事件\*\*[：:]\s*.*$',
                     '', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'^-\s*\*\*置信度\*\*[：:].*$',
                     '', md_text, flags=re.MULTILINE)

    # ── 4. 清理残留标点 ──
    md_text = re.sub(r'（\s*[，、]\s*）', '', md_text)  # （，） 残留
    md_text = re.sub(r'（\s*）', '', md_text)  # 空括号
    md_text = re.sub(r'\[\]', '', md_text)
    md_text = re.sub(r'对应事件[：:]\s*[、，\s]*。', '', md_text)  # 对应事件：、。
    md_text = re.sub(r'围绕\s*[，,]\s*由', '由', md_text)  # 围绕，由
    md_text = re.sub(r'[ \t]+，', '，', md_text)
    md_text = re.sub(r'，[ \t]+', '，', md_text)
    md_text = re.sub(r'[ \t]+。', '。', md_text)
    md_text = re.sub(r'。[ \t]+', '。', md_text)
    md_text = re.sub(r'，，+', '，', md_text)  # 连续逗号
    md_text = re.sub(r'、、+', '、', md_text)  # 连续顿号
    md_text = re.sub(r'\*\*event_id\*\*[：:]\s*', '', md_text)  # **event_id**:
    md_text = re.sub(r'event_id[：:]\s*', '', md_text)

    # ── 5. 清理多余空行 ──
    md_text = re.sub(r'\n{3,}', '\n\n', md_text)
    md_text = re.sub(r'[ \t]+$', '', md_text, flags=re.MULTILINE)
    return md_text.strip()



def _fix_markdown_formatting(md_text: str) -> str:
    """修正日报中常见的 markdown 格式问题，确保正确渲染。"""
    md_text = re.sub(r'([^\n])### ', r'\1\n\n### ', md_text)
    md_text = re.sub(r'([^\n])---(\s*\n)', r'\1\n\n---\2', md_text)
    md_text = re.sub(r'([^\n])---$', r'\1\n\n---', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'([^\n])> ', r'\1\n\n> ', md_text)
    md_text = re.sub(r'\n{3,}', '\n\n', md_text)
    return md_text


def _fix_markdown_formatting(md_text: str) -> str:
    """修正日报中常见的 markdown 格式问题，确保正确渲染。

    - ### 标题未独立成行
    - --- 分隔线未独立成行
    - > 引用未独立成行
    """
    # ### 前缺空行：确保 heading 独立成行
    md_text = re.sub(r'([^\n])### ', r'\1\n\n### ', md_text)
    # --- 前缺空行（如 "体验。---"）
    md_text = re.sub(r'([^\n])---(\s*\n)', r'\1\n\n---\2', md_text)
    md_text = re.sub(r'([^\n])---$', r'\1\n\n---', md_text, flags=re.MULTILINE)
    # > 引用前缺空行
    md_text = re.sub(r'([^\n])> ', r'\1\n\n> ', md_text)
    # 连续空行压缩
    md_text = re.sub(r'\n{3,}', '\n\n', md_text)
    return md_text


def _md_to_html(md_text: str) -> str:
    """将 Markdown 转换为 HTML 片段。启用 GFM 表格扩展。"""
    md = MarkdownIt().enable("table").enable("strikethrough")
    return md.render(md_text)


def _apply_inline_styles(html: str) -> str:
    """对 markdown-it 输出的 HTML 标签注入内联样式。"""
    # <h2>
    html = re.sub(
        r"<h2>(.*?)</h2>",
        rf"<h2 {HTML_H2_STYLE}>\1</h2>",
        html,
    )
    # <h3>
    html = re.sub(
        r"<h3>(.*?)</h3>",
        rf"<h3 {HTML_H3_STYLE}>\1</h3>",
        html,
    )
    # <p>
    html = re.sub(
        r"<p>(.*?)</p>",
        rf"<p {HTML_P_STYLE}>\1</p>",
        html,
    )
    # <ul>
    html = re.sub(
        r"<ul>",
        f"<ul {HTML_UL_STYLE}>",
        html,
    )
    # <blockquote>
    html = re.sub(
        r"<blockquote>",
        f"<blockquote {HTML_BLOCKQUOTE_STYLE}>",
        html,
    )
    # <hr>
    html = re.sub(
        r"<hr\s*/?>",
        f"<hr {HTML_HR_STYLE} />",
        html,
    )
    # <em> → 斜体但不变色
    # <strong> → 加粗但不变色

    return html


# ===================== 邮件 HTML 模板 =====================

# 参考"每日行业情报"邮件的专业样式
EMAIL_CSS = """
  body {
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', sans-serif;
    max-width: 680px;
    margin: 0 auto;
    padding: 20px;
    color: #2c3e50;
    line-height: 1.8;
    background: #f5f6fa;
  }
  .header {
    background: linear-gradient(135deg, #1a1a2e, #16213e);
    padding: 32px 28px;
    border-radius: 12px 12px 0 0;
    color: white;
  }
  .header h1 {
    margin: 0;
    font-size: 22px;
    font-weight: 600;
  }
  .header p {
    margin: 8px 0 0;
    opacity: 0.8;
    font-size: 14px;
  }
  .content {
    background: #fff;
    padding: 28px;
    border: 1px solid #e0e0e0;
    border-top: none;
  }
  .content h2 {
    color: #1a1a2e;
    font-size: 18px;
    border-bottom: 2px solid #e8e8e8;
    padding-bottom: 8px;
    margin-top: 28px;
  }
  .content h3 {
    color: #2c3e50;
    font-size: 16px;
    margin-top: 20px;
  }
  .content strong {
    color: #1a1a2e;
  }
  .content table {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 14px;
  }
  .content th, .content td {
    border: 1px solid #e0e0e0;
    padding: 10px 12px;
    text-align: left;
  }
  .content th {
    background: #f8f9fa;
    font-weight: 600;
    color: #1a1a2e;
  }
  .content tr:nth-child(even) {
    background: #fafafa;
  }
  .content ul, .content ol {
    padding-left: 20px;
  }
  .content li {
    margin-bottom: 4px;
  }
  .content blockquote {
    border-left: 4px solid #3498db;
    padding: 8px 16px;
    margin: 12px 0;
    background: #f0f7ff;
    color: #2c3e50;
  }
  .content code {
    background: #f4f4f4;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 13px;
  }
  .content hr {
    border: none;
    border-top: 1px solid #e8e8e8;
    margin: 20px 0;
  }
  .audio-notice {
    background: #fff8e1;
    border-left: 4px solid #ffc107;
    padding: 12px 16px;
    margin-bottom: 24px;
    border-radius: 4px;
  }
  .audio-notice p {
    margin: 0;
    font-size: 13px;
    color: #856404;
  }
  .system-note {
    background: #e8f4fd;
    border-left: 4px solid #2196F3;
    padding: 12px 16px;
    margin-bottom: 20px;
    border-radius: 4px;
  }
  .system-note p {
    margin: 0;
    font-size: 13px;
    color: #0d47a1;
  }
  .footer {
    background: #f8f9fa;
    padding: 16px 28px;
    border-radius: 0 0 12px 12px;
    border: 1px solid #e0e0e0;
    border-top: none;
    font-size: 12px;
    color: #999;
    text-align: center;
  }
  .footer p {
    margin: 0;
  }
"""


def _build_email_html(report_md: str, date: str, is_no_signal: bool) -> str:
    """构建邮件正文 HTML，匹配参考邮件专业样式。

    - 清理内部标记
    - Markdown → HTML（启用表格扩展）
    - 包裹参考样式模板
    """
    # 0. 清理内部标记
    clean_md = _clean_report_for_display(report_md)
    # 0.5. 修正 markdown 格式（确保标题/分隔线/引用独立成行）
    clean_md = _fix_markdown_formatting(clean_md)

    # 1. Markdown → HTML
    body_html = _md_to_html(clean_md)

    # 2. 构建完整 HTML 文档
    date_display = date  # e.g. "2026-05-04"
    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
{EMAIL_CSS}
</style>
</head>
<body>

<div class="header">
  <h1>即时零售 × 个护美妆经营日报</h1>
  <p>{date_display} ｜ 屈臣氏电商经营决策参考</p>
</div>

<div class="content">

<div class="audio-notice">
  <p>🎧 本期日报已生成AI播客音频，见邮件附件 MP3。推荐通勤/运动时收听。</p>
</div>

<div class="system-note">
  <p>📊 本报告由「即时零售 × 个护美妆经营情报系统」自动生成，每日 08:00 推送。仅供内部决策参考。</p>
</div>

{body_html}

</div>

<div class="footer">
  <p>本邮件由 Watsons Retail Intel 自动生成 · {date_display} · 仅供内部参考</p>
  <p>如无需接收此类邮件，请联系管理员退订</p>
</div>

</body>
</html>"""

    return full_html


# ===================== 安全门 =====================

def _check_gate(
    manifest: dict,
    report_path: Path,
    review_path: Path,
    report_md: str,
    recipient: str,
    logger: logging.Logger,
    project_root: str = "",
) -> tuple[bool, list[str]]:
    """检查 8 项安全门条件。

    Returns:
        (passsed, errors)  passed=True 才能发送。
    """
    errors = []

    # 1. run_manifest.json 存在
    if not manifest:
        errors.append("run_manifest.json 不存在或为空")

    # 2. sendable=true
    if manifest and not manifest.get("sendable", False):
        errors.append(f"run_manifest.sendable={manifest.get('sendable')}，不是 true")

    # 3. final_report_file 存在
    if not report_path.exists():
        errors.append(f"终稿文件不存在: {report_path}")

    # 4. 终稿不以【待人工复核】开头
    if report_path.exists():
        first_line = report_md.split("\n", 1)[0].strip()
        if "【待人工复核】" in first_line or "【待人工复核】" in report_md[:200]:
            errors.append("终稿以【待人工复核】开头，未通过审稿")

    # 5. editor_review.md 结论为"可发送"
    if review_path.exists():
        review_text = review_path.read_text(encoding="utf-8")
        if "✅ 可发送" not in review_text:
            # 提取实际审稿结论
            conclusion_match = re.search(r"审稿结论[：:]\s*(.+)", review_text)
            if conclusion_match:
                errors.append(f"审稿结论非「可发送」: {conclusion_match.group(1).strip()}")
            else:
                errors.append("审稿报告中未找到「✅ 可发送」结论")
    else:
        errors.append(f"审稿报告不存在: {review_path}")

    # 6. 终稿包含 9 个固定章节（或无信号日报）
    if report_path.exists():
        is_no_signal = manifest.get("no_signal", False) if manifest else False
        if not is_no_signal:
            found_sections = 0
            for section in CORE_SECTIONS:
                if section in report_md:
                    found_sections += 1
            if found_sections < len(CORE_SECTIONS):
                errors.append(
                    f"终稿仅包含 {found_sections}/{len(CORE_SECTIONS)} 个固定章节"
                )

    # 7. 终稿至少包含 1 个 event_id（或无信号日报）
    # 注意：终稿已做展示清理，改为从审稿报告或事件文件核实
    if report_path.exists():
        is_no_signal = manifest.get("no_signal", False) if manifest else False
        if not is_no_signal:
            event_ids = EVENT_ID_PATTERN.findall(report_md)
            if not event_ids:
                # 从 report_path 提取日期: .../2026/05/2026-05-04.md
                date_from_path = report_path.stem  # "2026-05-04"
                found_elsewhere = False
                # a) 审稿报告
                if review_path.exists():
                    review_text = review_path.read_text(encoding="utf-8")
                    if EVENT_ID_PATTERN.findall(review_text):
                        found_elsewhere = True
                # b) 事件文件
                if not found_elsewhere:
                    root = Path(project_root) if project_root else Path(_PROJECT_ROOT)
                    events_patterns = [
                        f"data/events/{date_from_path}/events_scored_novelty.json",
                        f"data/events/{date_from_path}/events_analyzed.json",
                    ]
                    for pat in events_patterns:
                        ep = root / pat
                        if ep.exists():
                            found_elsewhere = True
                            break
                # c) draft 文件
                if not found_elsewhere:
                    root = Path(project_root) if project_root else Path(_PROJECT_ROOT)
                    draft_dir = root / f"data/drafts/{date_from_path}"
                    if draft_dir.exists():
                        for dp in draft_dir.glob("daily_report_draft*.md"):
                            draft_text = dp.read_text(encoding="utf-8")
                            if EVENT_ID_PATTERN.findall(draft_text):
                                found_elsewhere = True
                                break
                if not found_elsewhere:
                    errors.append("终稿和各来源均未包含任何 event_id 引用")

    # 8. 邮件收件人存在
    if not recipient:
        errors.append("邮件收件人为空")

    passed = len(errors) == 0
    for e in errors:
        logger.warning(f"安全门未通过: {e}")
    if passed:
        logger.info("安全门全部通过 ✅")
    return passed, errors


# ===================== SMTP 发送 =====================

def _send_email(
    smtp_config: dict,
    to_addrs: list[str],
    subject: str,
    html_body: str,
    attachments: list[tuple[str, bytes, str]],  # (filename, content_bytes, mime_type)
    logger: logging.Logger,
    dry_run: bool = True,
) -> tuple[bool, str]:
    """通过 mailcli.py 发送邮件（或 dry_run 模拟）。

    使用 default agent 的 mailcli.py 工具发送，自动复用其 SMTP 配置。

    Args:
        smtp_config: 邮件配置字典（from_addr, to_addrs）
        to_addrs: 收件人列表
        subject: 邮件标题
        html_body: HTML 正文
        attachments: [(filename, content_bytes, mime_type), ...]
        dry_run: True 时不真实发送

    Returns:
        (success, message)
    """
    # mailcli.py 路径
    MAILCLI_PATH = Path("/app/working/workspaces/default/skills/email/mailcli.py")
    if not MAILCLI_PATH.exists():
        return False, f"mailcli.py 不存在: {MAILCLI_PATH}"

    if dry_run:
        logger.info(f"[DRY RUN] 邮件不真实发送")
        logger.info(f"  To: {', '.join(to_addrs)}")
        logger.info(f"  Subject: {subject}")
        logger.info(f"  HTML 正文长度: {len(html_body)} 字符")
        logger.info(f"  附件数: {len(attachments)}")
        for fn, _, _ in attachments:
            logger.info(f"    - {fn}")
        return True, "dry_run: 邮件未发送（仅模拟）"

    # ── 真实发送 ──
    # 1. 写入 HTML 正文到临时文件
    tmp_dir = Path(tempfile.mkdtemp(prefix="watsons_email_"))
    try:
        html_file = tmp_dir / f"email_body_{subject[:20].replace(' ', '_')}.html"
        html_file.write_text(html_body, encoding="utf-8")
        logger.info(f"HTML 正文写入临时文件: {html_file}")

        # 2. 写入附件到临时文件
        attach_files = []
        tmp_attach_dir = tmp_dir / "attachments"
        tmp_attach_dir.mkdir()
        for filename, content, mime_type in attachments:
            fpath = tmp_attach_dir / filename
            fpath.write_bytes(content)
            attach_files.append(str(fpath))
            logger.info(f"附件写入临时文件: {fpath}")

        # 3. 构建 mailcli.py send 命令
        cmd = [
            sys.executable, str(MAILCLI_PATH), "send",
            "--to", to_addrs[0],
            "--subject", subject,
            "--body-file", str(html_file),
            "--html",
        ]
        if len(to_addrs) > 1:
            # mailcli.py 的 --cc 只支持单个，多个收件人通过逗号分隔 --to
            for addr in to_addrs[1:]:
                cmd.extend(["--cc", addr])

        for af in attach_files:
            cmd.extend(["--attach", af])

        logger.info(f"发送命令: {' '.join(cmd)}")

        # 4. 执行发送
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        if result.returncode == 0 and "✅" in result.stdout:
            logger.info(f"邮件发送成功: {to_addrs}")
            return True, "邮件发送成功"
        else:
            error_msg = result.stderr.strip() or result.stdout.strip()
            logger.error(f"邮件发送失败: {error_msg}")
            return False, f"邮件发送失败: {error_msg}"

    except subprocess.TimeoutExpired:
        logger.error("邮件发送超时（60秒）")
        return False, "邮件发送超时"
    except Exception as e:
        logger.error(f"邮件发送异常: {e}")
        return False, str(e)
    finally:
        # 清理临时文件
        try:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


# ===================== 主函数 =====================

def send_daily_report_email(
    project_root: str,
    date: Optional[str] = None,
    recipient: Optional[str] = None,
    subject_prefix: str = DEFAULT_SUBJECT_PREFIX,
    attach_markdown: bool = False,
    attach_podcast: bool = True,
    attach_script: bool = False,
    dry_run: bool = True,
) -> dict:
    """发送日报邮件。

    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD，默认今天
        recipient: 收件人邮箱（会覆盖配置文件中的收件人）
        subject_prefix: 邮件标题前缀
        attach_markdown: 是否附加日报 Markdown
        attach_podcast: 是否附加播客 MP3
        attach_script: 是否附加播客稿 Markdown
        dry_run: True 时不真实发送

    Returns:
        结果字典
    """
    project_root = str(Path(project_root).resolve())

    # ── 日期 ──
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    year, month = date[:4], date[5:7]

    # ── 路径 ──
    run_dir = Path(project_root) / "data" / "runs" / date
    log_dir = Path(project_root) / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = _setup_logger(log_dir, date)

    # ── 0. 日期有效性 + 防重复发送 ──
    today = datetime.now().strftime("%Y-%m-%d")
    sent_marker = Path(project_root) / "data" / "logs" / date / ".email_sent"
    if date < today:
        # 允许补发昨天的，但绝对不允许更早的
        from datetime import timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        if date < yesterday:
            logger.error(f"拒绝发送 {date} 的日报：日期超过1天前（今天={today}）")
            return {
                "ok": False, "date": date, "sent": False, "dry_run": dry_run,
                "errors": [f"日期 {date} 超过允许范围（最早 {yesterday}）"]
            }
        logger.warning(f"补发昨日日报 {date}（今天={today}）")
    if sent_marker.exists() and not dry_run:
        logger.warning(f"日报 {date} 已发送过（{sent_marker} 存在），跳过重复发送")
        return {
            "ok": True, "date": date, "sent": False, "dry_run": dry_run,
            "errors": [], "note": "already_sent"
        }
    logger.info(f"{'='*60}")
    logger.info(f"send_daily_report_email 开始: date={date}, dry_run={dry_run}")

    # ── 1. 读取 run_manifest.json ──
    manifest_path = run_dir / "run_manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            logger.info(f"读取 manifest: sendable={manifest.get('sendable')}, "
                         f"no_signal={manifest.get('no_signal')}")
        except Exception as e:
            logger.error(f"读取 manifest 失败: {e}")
            manifest = {}
    else:
        logger.warning(f"run_manifest.json 不存在: {manifest_path}")

    # ── 2. 定位终稿 ──
    # 优先使用 manifest 中的 final_report_file
    final_report_file = manifest.get("final_report_file", "")
    if final_report_file:
        final_fp = Path(final_report_file)
        if final_fp.is_absolute():
            report_path = final_fp
        else:
            report_path = Path(project_root) / final_report_file.lstrip("./")
    else:
        # 回退到标准路径
        report_path = (
            Path(project_root)
            / "reports"
            / "daily"
            / year
            / month
            / f"{date}.md"
        )

    # ── 3. 读取终稿 ──
    report_md = ""
    if report_path.exists():
        report_md = report_path.read_text(encoding="utf-8")
        logger.info(f"读取终稿: {report_path} ({len(report_md)} 字符)")
    else:
        logger.error(f"终稿文件不存在: {report_path}")

    # ── 4. 审稿报告 ──
    review_path = Path(project_root) / "data" / "reviews" / date / "editor_review.md"

    # ── 5. 判断是否无信号日报 ──
    is_no_signal = manifest.get("no_signal", False)

    # ── 6. 读取播客文件 ──
    podcast_audio_path = Path(project_root) / "podcasts" / "audio" / f"{date}.mp3"
    podcast_script_path = Path(project_root) / "podcasts" / "scripts" / f"{date}.md"

    # ── 7. 收件人 ──
    smtp_config = _load_email_config(project_root)
    to_addrs = []
    if recipient:
        to_addrs = [recipient]
    elif smtp_config.get("to_addrs"):
        to_addrs = smtp_config["to_addrs"]

    # ── 8. 安全门 ──
    gate_passed, gate_errors = _check_gate(
        manifest=manifest,
        report_path=report_path,
        review_path=review_path,
        report_md=report_md,
        recipient=", ".join(to_addrs) if to_addrs else "",
        logger=logger,
        project_root=project_root,
    )

    if not gate_passed:
        logger.warning("安全门未通过，邮件不发送")
        result = {
            "ok": False,
            "date": date,
            "sent": False,
            "dry_run": dry_run,
            "recipient": ", ".join(to_addrs),
            "subject": "",
            "html_length": 0,
            "markdown_attachment": False,
            "podcast_attachment": False,
            "script_attachment": False,
            "sendable": False,
            "errors": gate_errors,
        }
        # 保存结果 JSON
        json_file = log_dir / "send_daily_report_email.json"
        json_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"结果 JSON 已保存: {json_file}")

        return result

    # ── 9. 构建邮件标题 ──
    if is_no_signal:
        subject = f"{subject_prefix}｜{date}｜无高质量新增信号"
    else:
        subject = f"{subject_prefix}｜{date}"
    logger.info(f"邮件标题: {subject}")

    # ── 10. 构建 HTML 正文 ──
    html_body = _build_email_html(report_md, date, is_no_signal)
    logger.info(f"HTML 正文长度: {len(html_body)} 字符")

    # ── 11. 构建附件列表 ──
    attachments = []

    # 日报 Markdown
    md_attached = False
    if attach_markdown and report_path.exists():
        md_bytes = report_path.read_bytes()
        attachments.append((
            f"daily_report_{date}.md",
            md_bytes,
            "text/markdown",
        ))
        md_attached = True
        logger.info(f"附加日报 Markdown: {report_path} ({len(md_bytes)} bytes)")

    # 播客 MP3
    podcast_attached = False
    if attach_podcast and podcast_audio_path.exists():
        mp3_bytes = podcast_audio_path.read_bytes()
        attachments.append((
            f"podcast_{date}.mp3",
            mp3_bytes,
            "audio/mpeg",
        ))
        podcast_attached = True
        logger.info(f"附加播客 MP3: {podcast_audio_path} ({len(mp3_bytes)} bytes)")
    elif attach_podcast:
        logger.warning(f"播客 MP3 不存在: {podcast_audio_path}")

    # 播客稿 Markdown
    script_attached = False
    if attach_script and podcast_script_path.exists():
        script_bytes = podcast_script_path.read_bytes()
        attachments.append((
            f"podcast_script_{date}.md",
            script_bytes,
            "text/markdown",
        ))
        script_attached = True
        logger.info(f"附加播客稿: {podcast_script_path} ({len(script_bytes)} bytes)")

    # ── 12. 发送邮件 ──
    sent = False
    send_msg = ""
    errors = []

    if not to_addrs:
        errors.append("收件人列表为空，无法发送")
        logger.error("收件人列表为空，无法发送")
    else:
        success, msg = _send_email(
            smtp_config=smtp_config,
            to_addrs=to_addrs,
            subject=subject,
            html_body=html_body,
            attachments=attachments,
            logger=logger,
            dry_run=dry_run,
        )
        # dry_run 模式下 sent 始终为 False（没有真实发送）
        sent = success and not dry_run
        send_msg = msg

        if not success and not dry_run:
            errors.append(f"邮件发送失败: {msg}")

    # ── 13. 结果 ──
    result = {
        "ok": gate_passed and (sent or dry_run),
        "date": date,
        "sent": sent,
        "dry_run": dry_run,
        "recipient": ", ".join(to_addrs),
        "subject": subject,
        "html_length": len(html_body),
        "markdown_attachment": md_attached,
        "podcast_attachment": podcast_attached,
        "script_attachment": script_attached,
        "sendable": gate_passed,
        "log_file": str(log_dir / "send_daily_report_email.log"),
        "errors": errors,
    }

    # ── 14. 保存结果 ──
    json_file = log_dir / "send_daily_report_email.json"
    json_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"结果 JSON 已保存: {json_file}")

    # ── 15. 写发送标记（防重复） ──
    if sent and not dry_run:
        sent_marker = log_dir / ".email_sent"
        sent_marker.write_text(datetime.now().isoformat())
        logger.info(f"发送标记已写入: {sent_marker}")

    logger.info(f"发送结果: ok={result['ok']}, sent={sent}, dry_run={dry_run}")

    return result


# ===================== 周期报告邮件发送 =====================

def send_periodic_report_email(
    report_type: str,
    period_label: str,
    report_path: str,
    audio_path: str = "",
    recipient: str = "",
    project_root: str = ".",
    dry_run: bool = False,
) -> dict:
    """发送周报/月报/年报邮件（HTML 格式 + 附件）。

    复用日报的 HTML 模板和 _send_email 基础设施。

    Args:
        report_type: "weekly" | "monthly" | "yearly"
        period_label: 人类可读的周期标签
        report_path: 报告 Markdown 文件路径
        audio_path: 播客 MP3 文件路径（可选）
        recipient: 收件人邮箱
        project_root: 项目根目录
        dry_run: 是否模拟发送
    """
    type_names = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}
    report_name = type_names.get(report_type, "报告")

    log_dir = Path(project_root) / "data" / "logs" / "periodic"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = _setup_logger(log_dir, f"periodic_{report_type}")

    logger.info(f"发送{report_name}邮件: {period_label}")

    # 读取报告
    report_fp = Path(report_path)
    if not report_fp.exists():
        logger.error(f"报告文件不存在: {report_path}")
        return {"ok": False, "error": "report_not_found"}

    report_md = report_fp.read_text(encoding="utf-8")

    # 构建 HTML
    html_body = _build_periodic_email_html(
        report_md, report_type, report_name, period_label)

    # 构建附件
    attachments = []
    # Markdown 附件
    md_filename = report_fp.name
    attachments.append((md_filename, report_fp.read_bytes(), "text/markdown"))

    # MP3 附件
    if audio_path and Path(audio_path).exists():
        audio_fp = Path(audio_path)
        attachments.append(
            (audio_fp.name, audio_fp.read_bytes(), "audio/mpeg"))
        logger.info(f"附加音频: {audio_fp.name} "
                    f"({audio_fp.stat().st_size / 1024 / 1024:.1f}MB)")

    # 收件人
    to_addrs = [recipient] if recipient else _load_email_config(project_root).get("to_addrs", [])
    if not to_addrs:
        logger.error("无收件人")
        return {"ok": False, "error": "no_recipient"}

    # 邮件标题
    subject = f"即时零售 × 个护美妆经营{report_name}｜{period_label}"

    # 发送
    smtp_config = _load_email_config(project_root)
    success, msg = _send_email(
        smtp_config=smtp_config,
        to_addrs=to_addrs,
        subject=subject,
        html_body=html_body,
        attachments=attachments,
        logger=logger,
        dry_run=dry_run,
    )

    logger.info(f"发送结果: success={success}, msg={msg}")
    return {"ok": success, "message": msg, "subject": subject}


def _build_periodic_email_html(report_md: str, report_type: str,
                               report_name: str, period_label: str) -> str:
    """构建周报/月报/年报邮件 HTML。"""
    # 清理 + 转换
    clean_md = _fix_markdown_formatting(report_md)
    body_html = _md_to_html(clean_md)

    # 根据类型调整提示语
    audio_tips = {
        "weekly": "本期周报已生成AI播客音频（约20分钟），见邮件附件 MP3。",
        "monthly": "本期月报已生成AI播客摘要音频（约15分钟），见邮件附件 MP3。",
        "yearly": "本期年报已生成AI播客摘要音频（约15分钟），见邮件附件 MP3。",
    }
    system_tips = {
        "weekly": "本报告基于上周7天日报综合分析生成，提供趋势判断与策略建议。",
        "monthly": "本报告基于本月各周报综合分析生成，提供格局判断与战略建议。",
        "yearly": "本报告基于全年月报综合分析生成，提供行业复盘与年度战略。",
    }

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
{EMAIL_CSS}
</style>
</head>
<body>

<div class="header">
  <h1>即时零售 × 个护美妆经营{report_name}</h1>
  <p>{period_label} ｜ 屈臣氏电商经营决策参考</p>
</div>

<div class="content">

<div class="audio-notice">
  <p>🎧 {audio_tips.get(report_type, "")}</p>
</div>

<div class="system-note">
  <p>📊 {system_tips.get(report_type, "")}</p>
</div>

{body_html}

</div>

<div class="footer">
  <p>本邮件由 Watsons Retail Intel 自动生成 · {period_label} · 仅供内部参考</p>
</div>

</body>
</html>"""

    return full_html


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="发送日报邮件")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--recipient", default=None, help="收件人邮箱（覆盖配置）")
    parser.add_argument(
        "--subject-prefix",
        default=DEFAULT_SUBJECT_PREFIX,
        help="邮件标题前缀",
    )
    parser.add_argument(
        "--attach-markdown",
        default="true",
        help="是否附加日报 Markdown (true/false)",
    )
    parser.add_argument(
        "--attach-podcast",
        default="true",
        help="是否附加播客 MP3 (true/false)",
    )
    parser.add_argument(
        "--attach-script",
        default="false",
        help="是否附加播客稿 Markdown (true/false)",
    )
    parser.add_argument(
        "--dry-run",
        default="true",
        help="dry_run 模式，不真实发送 (true/false)",
    )

    args = parser.parse_args()

    result = send_daily_report_email(
        project_root=args.project_root,
        date=args.date,
        recipient=args.recipient,
        subject_prefix=args.subject_prefix,
        attach_markdown=args.attach_markdown.lower() == "true",
        attach_podcast=args.attach_podcast.lower() == "true",
        attach_script=args.attach_script.lower() == "true",
        dry_run=args.dry_run.lower() == "true",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()