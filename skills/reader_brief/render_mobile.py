#!/usr/bin/env python3
"""render_mobile.py — 结构化简报 JSON → 手机端 HTML（周/月/年三套骨架）。

约束：
- 单列卡片，无表格、无侧栏，最大宽度 640px，360px 下不横向滚动；
- 只出现读者要看的东西：结论、变化、竞对、动作、观察项；
- 原文链接做成行内短链（来源名 + ↗），不出现来源清单/附录/评分。
"""

import html as _html
from typing import Dict, List

SECTION_TITLES = {
    "weekly": {
        "signals": "本周三个变化",
        "competitors": "竞对在做什么",
        "actions": "本周可做的三件事",
        "watch": "下周盯什么",
    },
    "monthly": {
        "trends": "这个月哪些变化是持续的",
        "tradeoffs": "要做的取舍",
        "actions": "下月先验证这三件事",
        "watch": "下月跟踪",
    },
    "yearly": {
        "structure": "结构变了什么",
        "decisions": "今年必须定的三件事",
        "actions": "明年 Q1 就能启动",
        "watch": "长期观察",
    },
}
PERIOD_LABEL = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}
ACCENT = {"weekly": "#0f766e", "monthly": "#1d4ed8", "yearly": "#7c2d12"}

CSS = """
body{margin:0;padding:0;background:#f4f5f7;-webkit-text-size-adjust:100%%;}
.wrap{max-width:640px;margin:0 auto;padding:18px 16px 28px;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",
 "Microsoft YaHei",sans-serif;color:#1f2328;line-height:1.62;font-size:16px;}
.kicker{font-size:12px;letter-spacing:.08em;color:#6b7280;text-transform:uppercase;}
h1{font-size:21px;line-height:1.35;margin:6px 0 2px;font-weight:700;}
.period{font-size:13px;color:#6b7280;margin-bottom:14px;}
.lead{background:#fff;border-left:4px solid %(accent)s;border-radius:10px;
 padding:14px 15px;font-size:17px;font-weight:600;margin:0 0 18px;
 box-shadow:0 1px 2px rgba(0,0,0,.05);}
h2{font-size:15px;margin:22px 0 10px;color:%(accent)s;font-weight:700;
 letter-spacing:.02em;}
.card{background:#fff;border-radius:10px;padding:13px 15px;margin:0 0 10px;
 box-shadow:0 1px 2px rgba(0,0,0,.05);}
.card h3{font-size:16px;margin:0 0 6px;line-height:1.45;font-weight:700;}
.card p{margin:0 0 6px;}
.card p:last-child{margin-bottom:0;}
.lbl{color:#6b7280;font-size:13px;}
.why{color:#0f3d3a;}
.meta{font-size:12px;color:#8b949e;margin-top:8px;}
.chip{display:inline-block;font-size:12px;color:#2563eb;text-decoration:none;
 border-bottom:1px dashed #bfdbfe;margin-right:10px;}
.foot{margin-top:24px;padding-top:12px;border-top:1px solid #e5e7eb;
 font-size:12px;color:#8b949e;line-height:1.7;}
.status{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;
 background:#eef2f7;color:#4b5563;margin-left:6px;vertical-align:2px;}
.action{border-left:3px solid %(accent)s;}
"""

STATUS_TEXT = {"completed": "已发生", "announced": "已宣布",
               "proposed": "拟推进", "reported": "媒体报道"}


def _e(v) -> str:
    return _html.escape(str(v or ""))


def _sources_for(item: Dict, facts: Dict[str, Dict], limit: int = 2) -> str:
    seen, chips = set(), []
    for fid in (item.get("fact_ids") or []):
        f = facts.get(fid)
        if not f:
            continue
        url = (f.get("source") or {}).get("url") or ""
        name = (f.get("source") or {}).get("name") or "原文"
        if not url or url in seen:
            continue
        seen.add(url)
        short = "原文" if len(chips) else f"{name} ↗"
        chips.append(f'<a class="chip" href="{_e(url)}" target="_blank">{_e(short)}</a>')
        if len(chips) >= limit:
            break
    return "".join(chips)


def _status_of(item: Dict, facts: Dict[str, Dict]) -> str:
    sts = {facts[x]["status"] for x in (item.get("fact_ids") or []) if x in facts}
    if not sts:
        return ""
    if "completed" in sts:
        return STATUS_TEXT["completed"]
    if "announced" in sts:
        return STATUS_TEXT["announced"]
    if sts <= {"proposed", "reported"}:
        return STATUS_TEXT["proposed"] if "proposed" in sts else STATUS_TEXT["reported"]
    return ""


def _card(title: str, body_html: str, item: Dict, facts: Dict[str, Dict],
          extra_class: str = "", show_status: bool = True) -> str:
    status = _status_of(item, facts) if show_status else ""
    st = f'<span class="status">{_e(status)}</span>' if status else ""
    src = _sources_for(item, facts)
    meta = f'<div class="meta">{src}</div>' if src else ""
    cls = "card " + extra_class if extra_class else "card"
    return f'<div class="{cls}"><h3>{_e(title)}{st}</h3>{body_html}{meta}</div>'


def render(brief: Dict, pack: Dict, period_type: str,
           period_label: str = "", generated_at: str = "") -> str:
    facts = {f["fact_id"]: f for f in pack.get("facts", [])}
    titles = SECTION_TITLES[period_type]
    accent = ACCENT[period_type]
    parts: List[str] = []

    parts.append(f'<div class="kicker">屈臣氏 · 经营简报 · {PERIOD_LABEL[period_type]}</div>')
    parts.append(f'<h1>{_e(period_label or "本期经营简报")}</h1>')
    parts.append(f'<div class="lead">{_e(brief.get("lead", ""))}</div>')

    for key in ("signals", "trends", "structure"):
        if key not in titles:
            continue
        items = brief.get(key) or []
        if not items:
            continue
        parts.append(f"<h2>{_e(titles[key])}</h2>")
        for it in items:
            if key == "signals":
                body = (f'<p><span class="lbl">变化</span> {_e(it.get("what_changed"))}</p>'
                        f'<p class="why"><span class="lbl">对屈臣氏</span> {_e(it.get("why_it_matters"))}</p>')
            elif key == "trends":
                body = (f'<p><span class="lbl">证据</span> {_e(it.get("evidence"))}</p>'
                        f'<p class="why"><span class="lbl">含义</span> {_e(it.get("meaning"))}</p>')
            else:
                body = f'<p>{_e(it.get("detail"))}</p>'
            parts.append(_card(it.get("title") or "", body, it, facts))

    comps = brief.get("competitors") or []
    if comps and "competitors" in titles:
        parts.append(f"<h2>{_e(titles['competitors'])}</h2>")
        for it in comps:
            body = (f'<p><span class="lbl">动作</span> {_e(it.get("move"))}</p>'
                    f'<p class="why"><span class="lbl">对我们的影响</span> {_e(it.get("implication"))}</p>')
            parts.append(_card(it.get("name") or "竞对", body, it, facts))

    for key in ("tradeoffs", "decisions"):
        if key not in titles:
            continue
        items = brief.get(key) or []
        if not items:
            continue
        parts.append(f"<h2>{_e(titles[key])}</h2>")
        for it in items:
            if key == "tradeoffs":
                body = (f'<p class="why"><span class="lbl">代价/风险</span> {_e(it.get("tension"))}</p>')
                title = it.get("choice") or ""
            else:
                body = ""
                if it.get("cost_of_waiting"):
                    body += (f'<p class="why"><span class="lbl">不定的代价</span> '
                             f'{_e(it.get("cost_of_waiting"))}</p>')
                title = it.get("decision") or ""
            parts.append(_card(title, body, it, facts))

    actions = brief.get("actions") or []
    if actions and "actions" in titles:
        parts.append(f"<h2>{_e(titles['actions'])}</h2>")
        for i, it in enumerate(actions, 1):
            body = (f'<p>{_e(it.get("action"))}</p>'
                    f'<p class="lbl">时间窗：{_e(it.get("horizon"))}</p>')
            parts.append(_card(f"动作 {i}", body, it, facts,
                               extra_class="action", show_status=False))

    watch = brief.get("watch") or []
    if watch and "watch" in titles:
        parts.append(f"<h2>{_e(titles['watch'])}</h2>")
        for it in watch:
            body = f'<p class="lbl">看什么</p><p>{_e(it.get("metric"))}</p>'
            parts.append(_card(it.get("item") or "", body, it, facts,
                               show_status=False))

    mode = (brief.get("_meta") or {}).get("editor_mode", "llm")
    note = ("<br>本期由规则兜底生成（LLM 不可用），仅陈述事实。" if mode != "llm" else "")
    parts.append(
        f'<div class="foot">本期纳入事实 {len(facts)} 条，均带原文来源；'
        f'未纳入未核实传闻。{generated_at and ("生成时间 " + _e(generated_at)) or ""}{note}</div>')

    css = CSS % {"accent": accent}
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>屈臣氏经营简报</title><style>{css}</style></head>"
            f'<body><div class="wrap">{"".join(parts)}</div></body></html>')
