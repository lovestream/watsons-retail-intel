#!/usr/bin/env python3
"""report_sanitizer — 统一报告清洗器（三批整改 §18）。

周期报告（周报/月报/年报）与日报的展示层清洗共用同一实现，
避免"生成器清洗一次、邮件模块又清洗一次"导致的不一致。

清洗内容:
- event_id: E20260606_0017（含反引号/括号/中文冒号/尾随标点）
- 判断标签: 【A】/['A']/["B"]/['K','X']/[A-必须关注]
- 内部行: 涉及事件 / 置信度
- 思考泄漏: reasoning_content 片段、[思考] 标签
- 空占位: 空 / 暂无数据 / 待补充 / N/A
- 孤儿表格列: 孤立冒号/竖线/破折号行
"""

import re

# 事件编号：E + 8位日期 + _ + 4位序号
_EVENT_ID_RE = re.compile(
    r'[（(\[【`]?\s*E\d{8}_\d{4}\s*[)\]】`]?\s*[：:、，,]?')

# 判断标签（含多元素列表、描述式）
# 覆盖: 【A】 / ['A'] / ["B"] / ['K','X'] / [K, X] / [A-必须关注] / [A-必须关注 B-本周跟进]
_TAG_RE = re.compile(
    r'\s*【\s*[ABCRKX]\s*】'
    r'|\s*\[\s*[\'"]?[ABCRKX][\'"]?(?:\s*,\s*[\'"]?[ABCRKX][\'"]?)*\s*\]'
    r'|\s*\[\s*[ABCRKX]\s*-\s*[^\[\]]{1,12}'
    r'(?:\s+[ABCRKX]\s*-\s*[^\[\]]{1,12})*\s*\]'
)

# 内部行（整行清除）
_INTERNAL_LINE_RE = re.compile(
    r'^.*?(?:涉及事件|置信度).*?$', re.MULTILINE)

# 思考泄漏
# - 行首"我/我们 思考/想了想/反思"后跟任意短内容（"思考了一下"）
# - 任意位置"思考：/思路："后跟内容
# - reasoning_content 字段
# - 方括号包裹的思考标记
_THINKING_LEAK_RE = re.compile(
    r'^[（(]?\s*(?:我|我们)?\s*(?:思考|想了想|反思)[了着过]?(?:一下)?[^，。\n]{0,12}[，,:：\s]'
    r'|reasoning_content[^\n]{0,40}'
    r'|\[?(?:思考|思路)[：:\s][^\n]{0,60}')

# 规则兜底说明行（周期报告降级章节的内部说明，不应出现在交付物）
_RULE_FALLBACK_RE = re.compile(
    r'^>\s*说明[：:].*?(?:规则兜底|generation_method|rule_fallback).*$',
    re.MULTILINE)
_RULE_FALLBACK_INLINE_RE = re.compile(
    r'[（(]?\s*(?:generation_method|rule_fallback)[=：:][^）)\n]{0,40}[）)]?'
    r'|（[^）]*规则兜底[^）]*）'
    r'|规则兜底生成(?:[^，。\n]{0,20})?')

# 空占位
_EMPTY_PLACEHOLDERS = [
    "（空）", "(空)", "暂无数据", "待补充", "此处留空",
    "N/A", "NA", "null", "None",
]

# 孤儿表格列/孤立符号行
# 仅删除"全部单元格为空"的表格行（事件ID被清空后的残留，如 "|  |  |"），
# 保留正常分隔行 "|---|---|"（markdown 表格语法，不能删）。
_ORPHAN_ROW_RE = re.compile(
    r'^\s*\|(?:\s*\|)*\s*\|?\s*$', re.MULTILINE)
_ORPHAN_DASH_RE = re.compile(r'^\s*[-–—]\s*$', re.MULTILINE)


def sanitize_periodic_report(text: str, mode: str = "final") -> str:
    """统一清除周期报告交付物中的内部标记。

    Args:
        text: 原始文本
        mode: "final"（终稿/邮件/播客输入）或 "draft"（保留 event_id）
    """
    if not text:
        return text
    if mode == "draft":
        # draft 阶段只清标签，保留 event_id（editor_review 需要校验）
        text = _TAG_RE.sub('', text)
        text = _INTERNAL_LINE_RE.sub('', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    text = _EVENT_ID_RE.sub('', text)
    text = _TAG_RE.sub('', text)
    text = _INTERNAL_LINE_RE.sub('', text)
    text = _RULE_FALLBACK_RE.sub('', text)
    text = _RULE_FALLBACK_INLINE_RE.sub('', text)
    text = _THINKING_LEAK_RE.sub('', text)
    for ph in _EMPTY_PLACEHOLDERS:
        text = text.replace(ph, "")
    # 空占位"空"单独处理（避免误删"天空"等正常词——只清孤立"空"）
    text = re.sub(r'(?<=[（(])\s*空\s*(?=[）)])', '', text)
    text = _ORPHAN_ROW_RE.sub('', text)
    # 事件ID被清空后留下的空单元格（如 "|  | 大 |" → "| 大 |"；不碰 "|---|---|"）。
    # 注意只能用 [ \t]* 不能用 \s*，否则会跨行吞掉表格换行。
    text = re.sub(r'\|[ \t]*\|', '|', text)
    text = _ORPHAN_DASH_RE.sub('', text)
    # 清理因移除留下的多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    # 清理孤立行尾冒号/破折号（前面无内容的行）
    text = re.sub(r'^\s*[-–—]\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


def sanitize_daily_report(text: str) -> str:
    """日报展示层清洗（与周期报告共用核心逻辑）。"""
    return sanitize_periodic_report(text, mode="final")
