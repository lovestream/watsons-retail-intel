"""Reader-first mobile email digest for periodic reports.

The full Markdown report remains the traceable internal document.  This module
turns its *editorial* chapters into a short business mail: what happened, why it
matters to Watsons, what can be tried next, and (only when the report contains
it) a real watch item.  Audit material, evidence appendices, scores, event IDs
and template-style "next week validate..." chapters never reach this renderer.

Design rules:
* Keep complete sentences.  We select whole sentences, never cut a sentence at
  an arbitrary character count.
* Prefer fact + implication over a mechanical chapter excerpt.
* Omit a section when the source does not support it.
* All styles are inline and single-column; no horizontal scroll at 360px.
"""
from __future__ import annotations

import re
from html import escape, unescape

# Internal/audit language that must not appear in a recipient email.
_AUDIT_RE = re.compile(
    r"证据级别|评分口径|八维|P[0-2]\b|事件ID|event[_ ]?id|置信度|"
    r"事件池|聚类|ID映射|正式事件账本|可追溯|官方.{0,8}原件|"
    r"截至本稿|按媒体报道处理|待验证|需以|校验|支撑事件|情报池|"
    r"覆盖数据|不进入正式|机器审查|质量门|sendable|verification|"
    r"内部审计|审计底稿|附录|来源与证据|证据池|内部证据|^依据[：:]"
)
_TEMPLATE_RE = re.compile(
    r"下一工作周|为什么下一|验证什么|关注什么|建议的跟踪方式|"
    r"预期效果|本章不复述|第0?6章|跟踪口径"
)
_WATCH_TITLE_RE = re.compile(r"风险|机会|展望|观察|留意|不确定性")
_ACTION_TITLE_RE = re.compile(
    r"^(?:P[0-2]\s*)?(?:战略|建议)[一二三四五六七八九十0-9]*[：:]\s*(.+)$"
)
_TREND_PREFIX_RE = re.compile(r"^(?:趋势[一二三四五六七八九十]+|\d+(?:\.\d+)*)[：:\s]+")
_NUM_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)*[：:\s]+")
_BOLD_LABEL_RE = re.compile(r"^\s*\*\*[^*]{1,24}\*\*\s*[：:]?\s*")

_CFG = {
    "weekly": {"summary": "01", "trends": "02", "competitors": "04", "actions": "05"},
    "monthly": {"summary": "01", "trends": "02", "competitors": "04", "actions": "06"},
    "yearly": {"summary": "01", "trends": "02", "competitors": "05", "actions": "07"},
}


def _plain(text: str) -> str:
    """Markdown-ish text to readable plain text (used for checks/lengths)."""
    text = unescape(text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\((?:https?://)[^)]+\)", r"\1", text)
    text = text.replace("`", "")
    text = re.sub(r"\*\*|__|~~|<[^>]+>", "", text)
    text = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _strip_leading_label(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    text = re.sub(r"^\*\*(?:趋势描述|对屈臣氏的影响判断|核心判断|建议内容|具体动作|依据|预期效果|战略主题|核心逻辑)\*\*\s*[：:]?\s*", "", text)
    text = _BOLD_LABEL_RE.sub("", text)
    text = re.sub(r"^\s*(?:趋势描述|对屈臣氏的影响判断|核心判断|建议内容|具体动作)\s*[：:]\s*", "", text)
    return text.strip()


def _inline_html(text: str) -> str:
    """Escape a fragment while retaining short in-text source links."""
    links: dict[str, tuple[str, str]] = {}

    def _link(match: re.Match) -> str:
        label = _plain(match.group(1)) or "来源"
        url = match.group(2).strip()
        if not re.match(r"^https?://", url, re.I):
            return label
        key = f"@@WX_LINK_{len(links)}@@"
        links[key] = (label, url)
        return key

    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _link, text)
    text = _plain(text)
    text = escape(text)
    for key, (label, url) in links.items():
        text = text.replace(
            key,
            '<a href="%s" style="color:#187983;text-decoration:underline">%s</a>'
            % (escape(url, quote=True), escape(label)),
        )
    return text


def _parts(md: str) -> dict[str, tuple[str, str]]:
    hits = list(re.finditer(r"(?m)^##\s+(\d{2})\b[^\n]*\n", md))
    result: dict[str, tuple[str, str]] = {}
    for i, match in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(md)
        result[match.group(1)] = (match.group(0).strip(), md[match.end():end])
    return result


def _subsections(body: str, level: int = 3) -> list[tuple[str, str]]:
    marker = "#" * level
    hits = list(re.finditer(rf"(?m)^{re.escape(marker)}\s+(.+)\n", body))
    result = []
    for i, match in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        result.append((match.group(1).strip(), body[match.end():end]))
    return result


def _paragraphs(body: str) -> list[str]:
    out = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block or block.startswith(("|", ">", "```", "![", "##", "###", "####")):
            continue
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        # A bullet list is a set of independent reader statements; treating
        # the whole list as one paragraph leaks "依据" into the action text.
        if sum(1 for ln in lines if re.match(r"^[-*+]\s+", ln)) >= 2:
            chunks = [ln for ln in lines if re.match(r"^[-*+]\s+", ln)]
        else:
            chunks = [block]
        for raw in chunks:
            text = _strip_leading_label(raw)
            if text:
                out.append(text)
    return out


def _sentences(text: str) -> list[str]:
    # Keep inline Markdown links in the selected sentence; lengths/audit checks
    # below use _plain().  Splitting on sentence punctuation is safe for the
    # short source links used in these reports.
    text = text.strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])", text)
    return [p.strip() for p in parts if p.strip()]


def _audity(sentence: str) -> bool:
    return bool(_AUDIT_RE.search(_plain(sentence)) or _TEMPLATE_RE.search(_plain(sentence)))


def _select_sentences(
    paragraphs: list[str],
    max_chars: int,
    prefer: re.Pattern | None = None,
    limit_sentences: int = 3,
    exclude: str = "",
) -> str:
    """Select complete sentences only; never slice a sentence."""
    excluded = _plain(exclude)
    candidates: list[str] = []
    for para in paragraphs:
        for sentence in _sentences(para):
            sentence = re.sub(r"^下一工作周起\s*", "", sentence)
            sentence = re.sub(r"^下一工作周\s*", "", sentence)
            if re.search(r"验证什么|关注什么|为什么下一|建议的跟踪方式|本章不复述", sentence):
                continue
            if excluded and _plain(sentence) and _plain(sentence) in excluded:
                continue
            if _audity(sentence):
                continue
            candidates.append(sentence)

    # Keep source order.  "prefer" is only used to avoid starting the excerpt
    # with a purely contextual sentence when a concrete implication is present.
    if prefer and candidates:
        preferred_idx = [i for i, s in enumerate(candidates) if prefer.search(_plain(s))]
        if preferred_idx:
            first = preferred_idx[0]
            if limit_sentences > 1 and first > 0 and len(_plain(candidates[0])) <= 80:
                candidates = [candidates[0]] + candidates[first:]
            else:
                candidates = candidates[first:]

    selected: list[str] = []
    used = 0
    for sentence in candidates:
        size = len(_plain(sentence))
        if selected and used + size > max_chars:
            continue
        if not selected and size > max_chars * 1.8:
            # A single very long sentence is still kept whole rather than cut.
            selected.append(sentence)
            break
        selected.append(sentence)
        used += size
        if len(selected) >= limit_sentences:
            break
    return "".join(selected).strip()


def _extract_fact(body: str, max_chars: int = 210, limit_sentences: int = 2) -> str:
    split = re.split(
        r"\*\*对屈臣氏的影响判断\*\*|\*\*对屈臣氏的影响\*\*|对屈臣氏的影响判断",
        body,
        maxsplit=1,
    )
    before = split[0]
    paras = _paragraphs(before)
    if not paras:
        return ""
    if limit_sentences == 1:
        # Monthly/yearly chapters commonly open with a meta sentence ("three
        # signals", "two main lines").  Prefer the first substantive sentence
        # so the mail doesn't label a table of contents as a finding.
        meta = re.compile(r"三个结构性信号|两条主线|核心信号可概括|速度超出市场预期|"
                          r"可概括为|主要体现在以下|从以下.{0,4}方面")
        for para in paras:
            for sentence in _sentences(para):
                sentence = re.sub(r"^下一工作周起\s*", "", sentence).strip()
                if not sentence or _audity(sentence) or meta.search(_plain(sentence)):
                    continue
                if len(_plain(sentence)) > max_chars * 1.8:
                    continue
                return sentence
        return ""
    return _select_sentences(paras, max_chars, limit_sentences=limit_sentences)


def _extract_impact(body: str, max_chars: int = 190, exclude: str = "") -> str:
    marker = re.search(
        r"\*\*对屈臣氏的影响判断\*\*|\*\*对屈臣氏的影响\*\*|对屈臣氏的影响判断",
        body,
    )
    after = body[marker.end():] if marker else body
    paras = _paragraphs(after)
    if not paras:
        return ""
    excluded = _plain(exclude)
    signal = re.compile(
        r"屈臣氏|意味着|对.{0,6}影响|影响集中|启示|机会窗口|挑战|应把|"
        r"应该|需要|建议|优先|试点|评估|区分|避免|建立|可先|可将|可以"
    )
    candidates = []
    for para in paras:
        for sentence in _sentences(para):
            sentence = re.sub(r"^下一工作周起\s*", "", sentence).strip()
            if not sentence or _audity(sentence):
                continue
            plain = _plain(sentence)
            if excluded and (plain in excluded or excluded in plain):
                continue
            if signal.search(plain):
                candidates.append(sentence)
    selected, used = [], 0
    for sentence in candidates:
        size = len(_plain(sentence))
        if selected and used + size > max_chars:
            continue
        selected.append(sentence)
        used += size
        if len(selected) >= 2:
            break
    return "".join(selected).strip()


def _terminal(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in "。！？!?":
        text += "。"
    return text


def _clean_title(title: str) -> str:
    title = _plain(title)
    title = _TREND_PREFIX_RE.sub("", title)
    title = _NUM_PREFIX_RE.sub("", title)
    title = re.sub(r"^P[0-2]\s*", "", title, flags=re.I)
    return title.strip()


def _highlights(md: str, report_type: str) -> list[tuple[str, str, str]]:
    cfg = _CFG.get(report_type, _CFG["weekly"])
    section = _parts(md).get(cfg["trends"], ("", ""))[1]
    blocks = _subsections(section, 3)
    if len(blocks) < 3 and report_type == "yearly":
        # Annual reports may split industry and beauty into 02/03; use 03 only
        # to fill a genuine shortfall, never to pad a complete section.
        extra = _parts(md).get("03", ("", ""))[1]
        blocks += _subsections(extra, 3)
    out = []
    for title, body in blocks:
        has_explicit_impact = bool(re.search(
            r"\*\*对屈臣氏的影响判断\*\*|\*\*对屈臣氏的影响\*\*|对屈臣氏的影响判断",
            body,
        ))
        fact = _extract_fact(
            body, limit_sentences=2 if has_explicit_impact else 1)
        if not fact:
            continue
        impact = _extract_impact(body, exclude=fact)
        out.append((_clean_title(title), fact, impact))
        if len(out) >= 3:
            break
    return out


def _table_cards(body: str) -> list[tuple[str, str, str]]:
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    if "竞对" not in "".join(header):
        return []
    result = []
    for line in lines[2:]:
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3 or not cols[0] or set(cols[0]) <= {"-", ":", " "}:
            continue
        name = _plain(cols[0])[:30]
        action = _select_sentences([cols[1]], 235, limit_sentences=2) if len(cols) > 1 else ""
        advice_col = cols[3] if len(cols) > 3 else (cols[2] if len(cols) > 2 else "")
        impact = _select_sentences([advice_col], 190, limit_sentences=2) if advice_col else ""
        if impact and (impact == action or _plain(action).startswith(_plain(impact))):
            impact = ""
        if name and action:
            result.append((name, _terminal(action), _terminal(impact) if impact else ""))
        if len(result) >= 2:
            break
    return result


def _narrative_cards(body: str) -> list[tuple[str, str, str]]:
    blocks = _subsections(body, 4)
    if not blocks:
        blocks = _subsections(body, 3)
    result = []
    skip = re.compile(r"总览|研判|战略方向|竞争态势|其他|概述|总结")
    for title, block in blocks:
        title = _clean_title(title)
        if skip.search(title):
            continue
        name = re.split(r"[：:]", title, maxsplit=1)[0].strip()
        if not name or len(name) > 24:
            continue
        paras = _paragraphs(block)
        action = _select_sentences(paras, 235, limit_sentences=2)
        impact = _select_sentences(
            paras, 190,
            prefer=re.compile(r"屈臣氏|启示|意味着|影响|机会|挑战"),
            limit_sentences=1,
            exclude=action,
        )
        if action:
            if impact and (impact == action or _plain(action).startswith(_plain(impact))):
                impact = ""
            result.append((name, _terminal(action), _terminal(impact) if impact else ""))
        if len(result) >= 2:
            break
    return result


def _competitors(md: str, report_type: str) -> list[tuple[str, str, str]]:
    cfg = _CFG.get(report_type, _CFG["weekly"])
    body = _parts(md).get(cfg["competitors"], ("", ""))[1]
    cards = _table_cards(body)
    return cards if cards else _narrative_cards(body)


def _action_sections(body: str, report_type: str) -> list[tuple[str, str]]:
    if report_type == "weekly":
        hits = list(re.finditer(
            r"(?m)^\*\*建议[一二三四五六七八九十0-9]+[：:]\s*(.+?)\*\*\s*$", body
        ))
    else:
        hits = list(re.finditer(
            r"(?m)^###\s+(.+?)\s*$", body
        ))
    result = []
    for i, match in enumerate(hits):
        title = _clean_title(match.group(1))
        if report_type != "weekly":
            if not _ACTION_TITLE_RE.match(_plain(match.group(1))) and not re.search(
                r"战略|建议|行动", title
            ):
                continue
            title = re.sub(r"^战略[一二三四五六七八九十0-9]+[：:]\s*", "", title)
            title = re.sub(r"^建议[一二三四五六七八九十0-9]+[：:]\s*", "", title)
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        block = body[match.end():end]
        result.append((title, block))
    return result


def _action_text(block: str, report_type: str) -> str:
    paras = _paragraphs(block)
    # Prefer the explicit "建议内容" paragraph; then the first substantive
    # paragraph.  For strategic monthly/yearly chapters, use the first concrete
    # milestone row/bullet when available.
    content = []
    capture = False
    for para in paras:
        if re.match(r"^\*\*建议内容\*\*", para) or para.startswith("建议内容"):
            capture = True
            continue
        if not capture and re.match(r"^\*\*(依据|预期效果)\*\*", para):
            break
        if capture:
            if re.match(r"^\*\*(依据|预期效果)\*\*", para):
                break
            content.append(para)
    if not content:
        content = [p for p in paras if not re.match(r"^\*\*(依据|预期效果)\*\*", p)][:1]
    text = _select_sentences(content, 210, limit_sentences=2)

    if report_type != "weekly":
        # Strategic chapters contain a real first milestone.  Use it as the
        # concrete next step instead of repeating only the background.
        rows = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
        if len(rows) >= 3:
            cols = [c.strip() for c in rows[2].strip("|").split("|")]
            if len(cols) >= 3:
                milestone = _plain(cols[0])
                deliverable = _plain(cols[-1])
                if milestone and deliverable and len(deliverable) > 8:
                    return _terminal(_select_sentences(
                        ["%s：%s" % (milestone, deliverable)], 210,
                        limit_sentences=1))
        # A bulleted concrete action also beats a generic background sentence.
        bullets = [ln.strip() for ln in block.splitlines()
                   if re.match(r"^[-*+]\s+", ln.strip())]
        if bullets and not text:
            text = _select_sentences(
                [re.sub(r"^[-*+]\s+", "", bullets[0])], 190,
                limit_sentences=1)
    return _terminal(text) if text else ""


def _actions(md: str, report_type: str) -> list[tuple[str, str]]:
    cfg = _CFG.get(report_type, _CFG["weekly"])
    body = _parts(md).get(cfg["actions"], ("", ""))[1]
    out = []
    for title, block in _action_sections(body, report_type):
        text = _action_text(block, report_type)
        if title:
            out.append((title, text))
        if len(out) >= 3:
            break
    if len(out) < 3 and report_type in ("monthly", "yearly"):
        # Some annual drafts use numbered H3 action headings without the
        # "战略" prefix.  Accept any remaining action chapter subsection.
        for title, block in _subsections(body, 3):
            clean = _clean_title(title)
            if any(clean == existing[0] for existing in out):
                continue
            text = _action_text(block, report_type)
            if text and re.search(r"行动|建议|推进|建设|试点|优化|提升", text):
                out.append((clean, text))
            if len(out) >= 3:
                break
    return out[:3]


def _summary(md: str, report_type: str) -> str:
    cfg = _CFG.get(report_type, _CFG["weekly"])
    body = _parts(md).get(cfg["summary"], ("", ""))[1]
    paras = _paragraphs(body)
    return _select_sentences(paras, 300, limit_sentences=3)


def _watch(md: str, report_type: str) -> str:
    """A short watch item only when the report has a real risk/opportunity chapter.

    Weekly "下一工作周关注点" is deliberately excluded: copying a validation
    template into the mail is exactly the failure this renderer prevents.
    """
    sections = _parts(md)
    for key in ("06", "07", "08"):
        title, body = sections.get(key, ("", ""))
        if not title or not _WATCH_TITLE_RE.search(title) or "下一" in title:
            continue
        blocks = _subsections(body, 3)
        if blocks:
            items = []
            for sub_title, sub_body in blocks[:2]:
                clean = _clean_title(sub_title)
                paras = _paragraphs(sub_body)
                fact = _select_sentences(paras, 150, limit_sentences=1)
                if clean and fact:
                    items.append("%s：%s" % (clean, fact))
            if items:
                return "；".join(i.rstrip("。；") for i in items) + "。"
        paras = _paragraphs(body)
        if paras:
            return _select_sentences(paras, 220, limit_sentences=2)
    return ""


def _summary_label(report_type: str) -> str:
    return {"weekly": "本期判断", "monthly": "本月判断", "yearly": "年度判断"}.get(
        report_type, "本期判断"
    )


def _highlights_label(report_type: str) -> str:
    return {"weekly": "本周", "monthly": "本月", "yearly": "年度"}.get(report_type, "本期")


def _headline(report_type: str) -> str:
    return {"weekly": "本周零售经营观察", "monthly": "本月零售经营观察",
            "yearly": "年度零售经营观察"}.get(report_type, "经营观察")


def has_internal_marker(html: str) -> bool:
    """Return True if a rendered digest still carries audit/template material."""
    text = _plain(re.sub(r"<[^>]+>", " ", html))
    return bool(re.search(
        r"证据级别|评分口径|置信度|事件ID|event[_ ]?id|附录|审计底稿|"
        r"下一工作周|为什么下一|验证什么|sendable|verification|"
        r"\bP[0-2]\b",
        text,
        re.I,
    ))


def render(report_md: str, report_name: str, period_label: str,
           report_type: str = "weekly", has_audio: bool = True) -> str:
    report_type = report_type if report_type in _CFG else "weekly"
    summary = _summary(report_md, report_type)
    highlights = _highlights(report_md, report_type)
    rivals = _competitors(report_md, report_type)
    actions = _actions(report_md, report_type)
    watch = _watch(report_md, report_type)

    if not (summary or highlights or rivals or actions):
        raise ValueError("periodic_digest_empty: report has no reader-facing business content")

    out: list[str] = []
    if summary:
        out.append(
            '<p class="copy" style="font-size:17px;line-height:1.8;margin:0 0 18px">'
            '<strong style="color:#183b45">%s：</strong>%s</p>'
            % (escape(_summary_label(report_type)), _inline_html(summary))
        )

    if highlights:
        out.append('<p style="font-size:12px;letter-spacing:1px;color:#68838c;margin:22px 0 12px">01 · %s值得关注的三件事</p>' % escape(_highlights_label(report_type)))
        for title, fact, impact in highlights:
            block = (
                '<div style="padding:16px 0;border-top:1px solid #e6ecee">'
                '<h2 style="margin:0 0 7px;font-size:18px;line-height:1.45;color:#183b45">%s</h2>'
                '<p class="copy" style="margin:0;font-size:15px;line-height:1.8">%s'
                % (escape(title), _inline_html(fact))
            )
            if impact:
                block += '<strong>经营启示：</strong>' + _inline_html(impact)
            block += "</p></div>"
            out.append(block)

    if rivals:
        out.append('<p style="font-size:12px;letter-spacing:1px;color:#68838c;margin:28px 0 12px">02 · 竞对动作</p>')
        for i, (name, action, impact) in enumerate(rivals):
            border = "border-bottom:1px solid #e6ecee;" if i == len(rivals) - 1 else ""
            card = (
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%%" '
                'style="border-collapse:collapse;width:100%%;background:#f1f7f6;margin-bottom:12px">'
                '<tr><td style="padding:19px 18px;border-left:4px solid #1e7c78;%s">'
                '<p style="margin:0 0 5px;color:#18736f;font-size:12px;font-weight:700;letter-spacing:.5px">%s</p>'
                '<p class="copy" style="margin:0 0 10px;font-size:15px;line-height:1.75">%s</p>'
                % (border, escape(name), _inline_html(action))
            )
            if impact:
                card += (
                    '<p class="copy" style="margin:0;font-size:15px;line-height:1.75">'
                    '<strong>对屈臣氏的启示：</strong>%s</p>' % _inline_html(impact)
                )
            card += "</td></tr></table>"
            out.append(card)

    if actions:
        out.append('<p style="font-size:12px;letter-spacing:1px;color:#68838c;margin:29px 0 12px">03 · 对屈臣氏最有用的三项建议</p>')
        rows = []
        for i, (title, text) in enumerate(actions, 1):
            border = "border-bottom:1px solid #edf0f1" if i < len(actions) else ""
            rows.append(
                '<tr><td valign="top" style="width:34px;padding:10px 10px 10px 0;'
                'color:#1c7d78;font-size:19px;font-weight:700">%02d</td>'
                '<td style="padding:10px 0;%s"><strong style="font-size:16px;color:#183b45">%s</strong>%s</td></tr>'
                % (i, border, escape(title),
                   ('<p class="copy" style="font-size:15px;line-height:1.75;margin:5px 0 0">%s</p>' % _inline_html(text))
                   if text else '')
            )
        out.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="width:100%;border-collapse:collapse">' + "".join(rows) + "</table>"
        )

    if watch:
        out.append('<p style="font-size:12px;letter-spacing:1px;color:#68838c;margin:27px 0 10px">04 · 值得持续留意</p>')
        out.append('<p class="copy" style="margin:0;font-size:15px;line-height:1.85">' + _inline_html(watch) + "</p>")

    if has_audio:
        out.append('<p style="font-size:13px;color:#6d8588;line-height:1.6;margin:22px 0 0">🎧 可搭配本期中文播客收听（见附件 MP3）。</p>')

    body = "\n".join(out)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>即时零售 × 个护美妆经营简报｜%s</title></head>"
        '<body style="margin:0;padding:0;background:#f3f5f7;font-family:-apple-system,'
        "BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',Arial,sans-serif;"
        'color:#172532;-webkit-text-size-adjust:100%%">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%%" '
        'style="width:100%%;border-collapse:collapse;background:#f3f5f7"><tr><td align="center" '
        'style="padding:18px 8px"><table role="presentation" cellpadding="0" cellspacing="0" '
        'border="0" width="100%%" style="width:100%%;max-width:600px;border-collapse:collapse;background:#fff">'
        '<tr><td style="background:#183b45;padding:28px 24px;color:#fff">'
        '<p style="margin:0 0 9px;font-size:12px;letter-spacing:1.2px;color:#b5d4d2">WATSONS RETAIL INTELLIGENCE</p>'
        '<h1 style="margin:0;font-weight:700;font-size:25px;line-height:1.35;color:#fff">%s</h1>'
        '<p style="margin:8px 0 0;color:#d3e8e5;font-size:14px">%s · %s</p></td></tr>'
        '<tr><td style="padding:26px 24px 24px">%s</td></tr>'
        '<tr><td style="padding:18px 22px;background:#f5f7f8;text-align:center;color:#74858b;'
        'font-size:12px;line-height:1.5">屈臣氏经营情报 · %s · 仅供内部参考</td></tr>'
        "</table></td></tr></table></body></html>"
        % (
            escape(period_label), escape(_headline(report_type)),
            escape(report_name), escape(period_label), body, escape(period_label),
        )
    )
