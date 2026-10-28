"""Reader-first mobile email renderer, separate from the audited full report."""
import re
from html import escape

def _plain(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = s.replace(chr(96), "")
    return re.sub(r"\s+", " ", re.sub(r"[#*_<>]", "", s)).strip()

def _clip(s, n=175):
    s = _plain(s)
    if len(s) <= n:
        return s
    chunks = re.split(r"(?<=[。！？])", s)
    out = ""
    for chunk in chunks:
        if len(out) + len(chunk) > n:
            break
        out += chunk
    return out.strip() if out else s[:n - 1].rstrip("，；、 ") + "…"

def _parts(md):
    hits = list(re.finditer(r"(?m)^##\s+(\d{2})\b[^\n]*\n", md))
    return {m.group(1): md[m.end():hits[i+1].start() if i+1 < len(hits) else len(md)]
            for i, m in enumerate(hits)}

def _first(body):
    for p in re.split(r"\n\s*\n", body):
        p = p.strip()
        if not p or p.startswith(("##", ">", "|", "- ", "* ", "**建议")):
            continue
        if re.search(r"证据级别|评分口径|官方规则原件|下一工作周|为什么下一", p):
            continue
        return _clip(p)
    return ""

def _trends(body):
    heads = list(re.finditer(r"(?m)^###\s+(.+)\n", body))
    return [(_clip(m.group(1), 42), _first(body[m.end():heads[i+1].start() if i+1 < len(heads) else len(body)]))
            for i,m in enumerate(heads[:3])]

def _competitors(body):
    rows = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    if len(rows) < 3 or "竞对" not in rows[0]:
        return []
    result = []
    for row in rows[2:]:
        cols = [s.strip() for s in row.strip("|").split("|")]
        if len(cols) >= 3 and cols[0] and not cols[0].startswith("---"):
            result.append((_clip(cols[0], 30), _clip(cols[1], 160), _clip(cols[2], 140)))
        if len(result) >= 2:
            break
    return result

def _actions(body):
    items = re.findall(r"(?m)^\*\*建议[一二三四五六七八九]+[：:]\s*(.+?)\*\*\s*$", body)
    return [_clip(re.sub(r"（?下一工作周[^）]*）?", "", s), 125) for s in items[:3]]

def render(report_md, report_name, period_label):
    sections = _parts(report_md)
    summary = _first(sections.get("01", ""))
    trends = _trends(sections.get("02", ""))
    rivals = _competitors(sections.get("04", ""))
    actions = _actions(sections.get("05", ""))
    lines = []
    if summary:
        lines.append('<p style="font-size:17px;line-height:1.75;color:#183b45;margin:0 0 20px;">'+escape(summary)+'</p>')
    if trends:
        lines.append('<h2 style="font-size:15px;color:#52717b;margin:22px 0 8px;">本期关键变化</h2>')
        for title, body in trends:
            if body:
                lines.append('<div style="border-top:1px solid #e2e9ec;padding:12px 0"><b style="font-size:17px;color:#183b45">'+escape(title)+'</b><p style="margin:6px 0 0;line-height:1.75;font-size:15px;">'+escape(body)+'</p></div>')
    if rivals:
        lines.append('<h2 style="font-size:15px;color:#52717b;margin:23px 0 10px;">竞对动作</h2>')
        for name, action, effect in rivals:
            lines.append('<div style="border-left:4px solid #237c76;background:#f0f7f5;padding:16px;margin-bottom:12px;word-break:break-word;"><b style="font-size:17px">'+escape(name)+'</b><p style="font-size:15px;line-height:1.75;">'+escape(action)+'</p><p style="font-size:15px;line-height:1.75;margin-bottom:0"><b>启示：</b>'+escape(effect)+'</p></div>')
    if actions:
        lines.append('<h2 style="font-size:15px;color:#52717b;margin:24px 0 10px;">经营动作建议</h2>')
        for i, a in enumerate(actions, 1):
            lines.append('<p style="font-size:15px;line-height:1.75;border-top:1px solid #e2e9ec;padding-top:10px;"><b style="color:#237c76">'+str(i).zfill(2)+'　</b>'+escape(a)+'</p>')
    if not lines:
        lines = ['<p>本期没有符合简报条件的信息。</p>']
    body = "\n".join(lines)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
            '<body style="background:#f2f4f6;margin:0;padding:0;font-family:Arial,'
            'PingFang SC,Microsoft YaHei,sans-serif;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
            'style="width:100%;border-collapse:collapse"><tr><td align="center" '
            'style="padding:12px 8px;"><table role="presentation" width="100%" '
            'style="max-width:600px;background:white;border-collapse:collapse;">'
            '<tr><td style="padding:24px 18px;background:#183b45;color:#fff;">'
            '<h1 style="color:#fff;margin:0;font-size:23px;">本期经营观察</h1>'
            '<p style="font-size:13px;margin:8px 0 0;">'+escape(report_name)+' · '
            +escape(period_label)+'</p></td></tr><tr><td style="padding:22px 18px;">'
            +body+'</td></tr><tr><td style="padding:15px;background:#f5f7f8;'
            'text-align:center;font-size:12px;color:#70838a;">屈臣氏经营情报 · '
            '仅供内部参考</td></tr></table></td></tr></table></body></html>')
