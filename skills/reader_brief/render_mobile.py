#!/usr/bin/env python3
"""render_mobile.py — 只消费通过 P08/P09 的 reader_brief.json 生成手机邮件 HTML。

约束（docs/15、docs/16）：
- 前台视觉由模板控制，模型不输出 HTML/Markdown；
- 竞对单列竖向卡片；重要来源就地短链接（每条 ≤2），不做文末来源附录；
- 360/375/390/600px 无横向滚动；首屏即可看到本期结论；
- 不出现 event_id、评分、审计/证据清单、模型提示过程。
"""

import html
from typing import Dict, List

CSS = """
body{margin:0;background:#f5f6f8;color:#1f2328;
font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:600px;margin:0 auto;background:#ffffff;padding:20px 18px 28px}
.kicker{font-size:12px;letter-spacing:.08em;color:#0b7a5e;font-weight:700}
h1{font-size:22px;line-height:1.35;margin:6px 0 10px;color:#1f2328}
.take{background:#eef7f4;border-left:4px solid #0b7a5e;
padding:12px 14px;border-radius:6px;font-size:15px;margin:0 0 22px;color:#1f2328}
h2{font-size:13px;letter-spacing:.06em;color:#6b7280;margin:26px 0 10px;font-weight:700}
.card{border:1px solid #e6e8eb;border-radius:8px;padding:13px 14px;margin:0 0 12px}
.card h3{font-size:16px;margin:0 0 6px;line-height:1.4;color:#1f2328}
.card p{margin:0 0 8px;font-size:15px;word-break:break-word;color:#1f2328}
.impact{font-size:14px;color:#0d5f4a;background:#f6fbf9;border-radius:6px;
padding:8px 10px;margin:6px 0 0}
.meta{font-size:12px;color:#6b7280;margin-top:8px}
a{color:#0b7a5e;text-decoration:none;border-bottom:1px solid #bfe0d5}
ol.actions{margin:0;padding-left:20px}
ol.actions li{margin:0 0 14px}
.at{font-weight:700;font-size:15px}
.row{font-size:14px;margin:4px 0}
.row b{color:#6b7280;font-weight:600}
.watch{font-size:14px;border-left:3px solid #b45309;padding:2px 0 2px 10px;margin:0 0 10px}
footer{font-size:12px;color:#6b7280;border-top:1px solid #e6e8eb;
margin-top:24px;padding-top:12px}
@media (max-width:400px){.wrap{padding:16px 14px 24px}h1{font-size:20px}}
"""


def _e(v) -> str:
    return html.escape(str(v or ""), quote=True)


PERIOD_LABEL = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}


def _source_links(refs: List[Dict], limit: int = 2) -> str:
    seen, bits = set(), []
    for ref in refs or []:
        url = str(ref.get("url") or "")
        name = str(ref.get("name") or "来源")
        if not url or url in seen:
            continue
        seen.add(url)
        bits.append(f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(name[:18])}</a>')
        if len(bits) >= limit:
            break
    return " · ".join(bits)


def _signal_card(i: int, s: Dict) -> str:
    body = [f'<div class="card"><h3>{_e(s.get("headline"))}</h3>',
            f'<p>{_e(s.get("fact"))}</p>']
    if s.get("business_impact"):
        body.append(f'<div class="impact">{_e(s.get("business_impact"))}</div>')
    meta = []
    if s.get("is_inference"):
        meta.append("含推断")
    links = _source_links(s.get("source_refs") or [])
    if links:
        meta.append(links)
    if meta:
        body.append(f'<div class="meta">{" · ".join(meta)}</div>')
    body.append("</div>")
    return "".join(body)


def _competitor_card(c: Dict) -> str:
    body = [f'<div class="card"><h3>{_e(c.get("name"))}</h3>',
            f'<p>{_e(c.get("move"))}</p>']
    if c.get("why_matters"):
        body.append(f'<div class="impact">{_e(c.get("why_matters"))}</div>')
    meta = []
    if c.get("occurred_on"):
        meta.append(_e(c.get("occurred_on")))
    links = _source_links(c.get("source_refs") or [])
    if links:
        meta.append(links)
    if meta:
        body.append(f'<div class="meta">{" · ".join(meta)}</div>')
    body.append("</div>")
    return "".join(body)


def _action_li(a: Dict) -> str:
    rows = []
    if a.get("precondition"):
        rows.append(f'<div class="row"><b>前提 · </b>{_e(a.get("precondition"))}</div>')
    if a.get("trial"):
        rows.append(f'<div class="row"><b>做法 · </b>{_e(a.get("trial"))}</div>')
    if a.get("metric"):
        rows.append(f'<div class="row"><b>衡量 · </b>{_e(a.get("metric"))}</div>')
    return f'<li><span class="at">{_e(a.get("title"))}</span>{"".join(rows)}</li>'


def render(brief: Dict, pack: Dict, period_type: str = "",
           period_start: str = "", period_end: str = "",
           audio_note: str = "", generated_at: str = "",
           basis_note: str = "") -> str:
    ptype = period_type or brief.get("period_type", "weekly")
    p0 = period_start or brief.get("period_start", "")
    p1 = period_end or brief.get("period_end", "")
    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{_e(brief.get("title"))}</title><style>{CSS}</style></head>',
             '<body><div class="wrap">',
             f'<div class="kicker">{PERIOD_LABEL.get(ptype, "简报")} · {_e(p0)} — {_e(p1)}</div>',
             f'<h1>{_e(brief.get("title"))}</h1>',
             f'<div class="take">{_e(brief.get("one_line_takeaway"))}</div>']

    signals = brief.get("signals") or []
    if signals:
        parts.append("<h2>本期变化</h2>")
        parts.extend(_signal_card(i, s) for i, s in enumerate(signals))

    comps = brief.get("competitor_cards") or []
    if comps:
        parts.append("<h2>竞对动作</h2>")
        parts.extend(_competitor_card(c) for c in comps)

    actions = brief.get("priority_actions") or []
    if actions:
        parts.append("<h2>可以怎么用</h2><ol class='actions'>")
        parts.extend(_action_li(a) for a in actions)
        parts.append("</ol>")

    watch = brief.get("watch_items") or []
    if watch:
        parts.append("<h2>持续留意</h2>")
        for w in watch:
            parts.append(f'<div class="watch">{_e(w.get("trigger"))}<br>'
                         f'<span class="meta">{_e(w.get("business_relevance"))}</span></div>')

    n_facts = len({fid for s in signals for fid in (s.get("fact_ids") or [])})
    foot = [f"本期基于 {len(pack.get('facts', []))} 条来自媒体原始报道的事实，"
            f"正文引用 {n_facts} 条；来源链接附在对应事实处，"
            "未经当事方官方确认的内容按报道处理。"]
    if basis_note:
        foot.append(_e(basis_note))
    if audio_note:
        foot.append(_e(audio_note))
    if generated_at:
        foot.append(f"生成于 {_e(generated_at)}")
    parts.append(f'<footer>{"<br>".join(foot)}</footer></div></body></html>')
    return "".join(parts)
