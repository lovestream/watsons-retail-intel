#!/usr/bin/env python3
"""periodic_editor_review — 周期报告最终质量门。

对终稿执行规则化质检，输出 sendable 判定（三批整改 §10）。

必检规则：
- 结构: 章节数量完整 / 标题编号正确 / 不多章不缺章
- 内容范围: 只含中国大陆 / 无股票研报估值 / 无纯IPO融资财报 / 主题相关
- 事实: 重大数字有 evidence / 趋势词有 cluster 或 previous-period 证据 /
       无凭据市场份额变化 / 无内部数据写"屈臣氏经营表现"
- 展示: 无 event_id / 无 A/B/C/K/X 标签 / 无 ['K','X'] / 无 [A-必须关注] /
       无 涉及事件 / 无 置信度 / 无思考泄漏 / 无 空 占位 / 无孤儿表格列
- 建议: 至少存在明确可执行建议 / 建议与屈臣氏渠道群相关 /
       不得全部是"持续关注/加强关注"

Sendable 规则:
- 任何 high issue → sendable=false
- review 执行失败 → sendable=false
- 章节缺失 → sendable=false
- coverage 不达标 → sendable=false
"""

import argparse
import json
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logger = logging.getLogger(__name__)
LOG_PREFIX = "[PeriodicEditorReview]"

_CHAPTER_TITLES = {
    "weekly": ["01", "02", "03", "04", "05", "06"],
    "monthly": ["01", "02", "03", "04", "05", "06", "07"],
    "yearly": ["01", "02", "03", "04", "05", "06", "07", "08"],
}

# 判断标签正则（含多元素 ['K','X']、描述式 [A-必须关注]）
_TAG_RE = re.compile(
    r"【\s*[ABCRKX]\s*】"                      # 【A】
    r"|\[\s*['\"]?[ABCRKX]['\"]?\s*\]"         # [A] / ['A']
    r"|\[\s*['\"][ABCRKX]['\"]\s*,\s*['\"][ABCRKX]['\"]\s*\]"  # ['K','X']
    r"|\[\s*[ABCRKX]\s*-\s*[^\[\]]{1,10}\]"    # [A-必须关注]
)

_EVENT_ID_RE = re.compile(r"E\d{8}_\d{4}")

# 思考泄漏特征
_THINKING_LEAK_MARKERS = [
    "reasoning_content", "我需要", "作为AI", "作为人工智能", "我不能",
    "我无法", "根据我的训练", "让我思考", "好的，让我",
]

# 空占位特征
_EMPTY_PLACEHOLDERS = [
    "暂无数据", "待补充", "此处留空", "N/A", "NA",
]

# 资本市场噪音（出现在终稿中即 high issue）
_STOCK_NOISE_RE = re.compile(
    r"涨停|跌停|股价|目标价|买入评级|增持评级|研报|盈利预测|估值模型|"
    r"市值蒸发|续创新高|涨幅居前|北向资金|主力资金")

# 港股/台湾/海外主体（内容范围检查）
_OVERSEAS_SUBJECTS = [
    "香港", "台湾", "澳门", "美股", "港股", "美国市场", "日本市场",
    "新加坡", "东南亚",
]

# 不可无依据的市占/经营结论
_UNSUPPORTED_CLAIMS = [
    "市场份额上升", "市场份额下降", "市占率提升", "GMV增长", "营收增长",
    "利润增长", "屈臣氏经营表现", "屈臣氏经营总结",
]

# 弱建议词（全部命中 → high issue：无明确可执行建议）
_WEAK_ADVICE = ["持续关注", "加强关注", "保持关注", "密切关注", "进一步观察"]

# 规则兜底/模板化水文特征。清洗层会删掉内部标记，因此生成器必须把
# fallback_chapters 作为显式参数传进来；同时在文本里保留第二道防线。
_FALLBACK_MARKERS = [
    "generation_method=rule_fallback", "规则兜底生成", "规则兜底",
    "本期证据不足，规则兜底", "按事件密度排序",
]
_TEMPLATE_LINES = [
    "建议结合后续周期观察", "本期该维度证据较少",
    "本期事件池信号有限", "建议下期重点跟踪",
    "即时零售渠道：跟踪平台补贴与履约政策变化",
    "个护美妆品类：关注品类增长信号",
    "竞对动态：结合本期竞对动作",
]
_ACTIONABLE_VERBS = [
    "上架", "下架", "调价", "定价", "备货", "补货", "铺货", "排期",
    "投放", "投流", "报名", "谈判", "签约", "测试", "复盘", "配置",
    "设置", "锁定", "跟进", "核对", "申请", "调整", "优化", "复制",
]


# 屈臣氏渠道群关键词（建议相关性检查）
_WATSONS_CHANNELS = ["美团闪购", "京东秒送", "京东到家", "淘宝闪购", "抖音小时达",
                     "天猫", "京东", "猫超", "京东自营", "经销商", "门店",
                     "即时零售", "O2O", "线上", "渠道"]


# ══════════════════════════════════════════════════════════════
# 规则检查
# ══════════════════════════════════════════════════════════════

def _check_structure(draft: str, report_type: str,
                     expected_chapters: List[str]) -> List[str]:
    issues = []
    expected_ids = _CHAPTER_TITLES.get(report_type, expected_chapters)
    # 章节标题格式: ## 01 xxx
    found_ids = set()
    for m in re.finditer(r"^##\s+(\d{2})[\s\u4e00-\u9fff]", draft,
                         re.MULTILINE):
        found_ids.add(m.group(1))
    missing = [c for c in expected_ids if c not in found_ids]
    if missing:
        issues.append("章节缺失: %s" % ",".join(missing))
    # 多余章节（非本期定义的编号）
    extra = [c for c in found_ids if c not in expected_ids]
    if extra:
        issues.append("多余章节: %s" % ",".join(sorted(extra)))
    # 标题编号顺序
    ordered = sorted(expected_ids)
    if ordered != expected_ids:
        issues.append("章节顺序错误")
    return issues


def _check_stock_noise(draft: str) -> List[str]:
    hits = _STOCK_NOISE_RE.findall(draft)
    return ["资本市场噪音: %s" % h for h in set(hits)][:5] if hits else []


def _check_overseas(draft: str) -> List[str]:
    issues = []
    for subj in _OVERSEAS_SUBJECTS:
        if subj in draft:
            issues.append("疑似海外/港台主体提及: %s" % subj)
    return issues[:5]


def _check_display_cleanliness(draft: str) -> List[str]:
    issues = []
    # event_id
    evs = _EVENT_ID_RE.findall(draft)
    if evs:
        issues.append("泄漏 event_id: %s" % ",".join(sorted(set(evs))[:5]))
    # 判断标签
    tags = _TAG_RE.findall(draft)
    if tags:
        issues.append("泄漏判断标签: %s" % ",".join(sorted(set(tags))[:5]))
    # 涉及事件 / 置信度
    if "涉及事件" in draft:
        issues.append("含'涉及事件'字样")
    if "置信度" in draft:
        issues.append("含'置信度'字样")
    # 思考泄漏
    for marker in _THINKING_LEAK_MARKERS:
        if marker in draft:
            issues.append("思考泄漏: %s" % marker)
    # 空占位
    for ph in _EMPTY_PLACEHOLDERS:
        if ph in draft:
            issues.append("空占位: %s" % ph)
    # 孤儿表格列（行尾孤立冒号/竖线）
    orphan_cols = re.findall(r"^\|?\s*[:：]\s*$", draft, re.MULTILINE)
    if orphan_cols:
        issues.append("孤儿表格列: %d 处" % len(orphan_cols))
    return issues[:10]


def _check_unsupported_claims(draft: str, synthesis: dict) -> List[str]:
    issues = []
    prev = (synthesis or {}).get("previous_period_comparison", {})
    has_prev = bool(prev and (prev.get("event_count") is not None))
    has_clusters = bool((synthesis or {}).get("all_clusters"))
    for claim in _UNSUPPORTED_CLAIMS:
        if claim in draft:
            if claim.startswith("市场份额") or claim.startswith("市占率"):
                # 无真实份额数据 → high issue
                issues.append("无凭据市场份额结论: %s" % claim)
            elif claim.startswith("屈臣氏经营"):
                issues.append("无内部数据写屈臣氏经营: %s" % claim)
            elif not has_prev and not has_clusters:
                issues.append("无周期证据的数字结论: %s" % claim)
    return issues[:5]


def _check_advice_quality(draft: str) -> List[str]:
    issues = []
    # 建议章节: 05 (weekly), 06 (monthly), 07 (yearly)
    advice_ch = {"weekly": "05", "monthly": "06", "yearly": "07"}
    return issues  # 详细建议检查由 LLM review 补充


# ── §16 claim registry: 数字断言必须有事件证据 ──

# 需要证据支撑的数字断言模式
_NUMERIC_CLAIM_RE = re.compile(
    r"(?:增长|下降|提升|达到|突破|超|翻倍|下滑)\s*[0-9]+(?:\.[0-9]+)?\s*(?:%|倍|亿元|万元|单|个|家|座|万)"
    r"|[0-9]+\s*(?:%|倍|亿元|万元|亿)\s*(?:增长|下降|提升|突破|达到|超)"
    r"|GMV\s*[0-9]"
    r"|市占率\s*[0-9]"
)

def _check_numeric_claims(draft: str, synthesis: Optional[dict]) -> List[str]:
    """检查数字断言是否有证据支撑。

    规则化实现：抽取 draft 中的数字断言，检查 synthesis.evidence 中是否
    出现过同样的数字（同一事实），没有 → high issue。
    """
    if not synthesis:
        return []
    # 从证据中收集已出现的数字短语
    evidence_text = json.dumps(
        (synthesis.get("all_clusters") or [])[:30], ensure_ascii=False)
    issues = []
    for m in _NUMERIC_CLAIM_RE.finditer(draft):
        claim = m.group(0)
        # 提取数字+单位
        nums = re.findall(r"[0-9]+(?:\.[0-9]+)?\s*(?:%|倍|亿元|万元|单|个|家|座|万|亿)",
                          claim)
        if not nums:
            continue
        # 检查证据中是否有相同数字
        found = False
        for n in nums:
            base = n.replace(" ", "")
            # 允许 ±10% 浮动比较
            if base in evidence_text:
                found = True
                break
            # 尝试数值比较（如 500亿 vs 500.5亿）
            try:
                val = float(base.replace("%", "").replace("倍", "")
                            .replace("亿元", "").replace("万元", "")
                            .replace("亿", "").replace("万", ""))
                unit = "".join(c for c in base if c.isalpha())
                for ev_num in re.findall(
                        r"[0-9]+(?:\.[0-9]+)?\s*(?:%|倍|亿元|万元|万|亿)",
                        evidence_text):
                    try:
                        ev_val = float(ev_num.replace("%", "").replace("倍", "")
                                       .replace("亿元", "").replace("万元", "")
                                       .replace("亿", "").replace("万", ""))
                        ev_unit = "".join(c for c in ev_num if c.isalpha())
                        if unit == ev_unit and abs(val - ev_val) / max(val, 1) < 0.1:
                            found = True
                            break
                    except Exception:
                        continue
            except Exception:
                continue
            if found:
                break
        if not found:
            issues.append("无证据数字断言: %s" % claim)
    return issues[:5]


def _check_fallback_and_template(
    draft: str,
    fallback_chapters: Optional[List[str]] = None,
    generation_stats: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Reject rule-fallback/template water.  W40 proved length checks alone
    cannot distinguish a generated chapter from six copies of a fallback.
    """
    issues: List[str] = []
    fb = list(fallback_chapters or [])
    stats = generation_stats or {}
    if fb:
        expected = stats.get("expected_chapters") or 0
        ratio = len(fb) / expected if expected else 0.0
        if len(fb) >= max(1, int(expected * 0.5)) if expected else len(fb) >= 2:
            issues.append(
                "规则兜底章节过多(%d/%s): %s" %
                (len(fb), expected or "?", ",".join(fb[:8])))
        else:
            issues.append("规则兜底章节: %s" % ",".join(fb[:8]))
    text = draft or ""
    hits = [m for m in _FALLBACK_MARKERS if m in text]
    if hits:
        issues.append("规则兜底/模板标记泄漏: %s" % ",".join(hits[:4]))
    template_hits = [t for t in _TEMPLATE_LINES if t in text]
    if template_hits:
        issues.append("模板化水文: %s" % "；".join(template_hits[:3]))
    return issues


def _check_content_distinctness(draft: str) -> List[str]:
    """Detect the W40 signature: the same paragraph repeated across chapters."""
    issues: List[str] = []
    current = ""
    sections: Dict[str, List[str]] = {}
    for line in (draft or "").splitlines():
        m = re.match(r"^##\s+(\d{2})\b", line.strip())
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current and len(line.strip()) >= 25:
            sections[current].append(line.strip())
    seen: Dict[str, List[str]] = {}
    for ch, lines in sections.items():
        for line in set(lines):
            seen.setdefault(line, []).append(ch)
    repeated = [(line, chs) for line, chs in seen.items()
                if len(set(chs)) >= 3]
    if repeated:
        issues.append(
            "跨章节重复段落(%d处): %s" %
            (len(repeated), repeated[0][0][:60]))
    # 章节之间有大量完全相同的行也算重复。
    total_lines = sum(len(set(v)) for v in sections.values())
    unique_lines = len(set().union(*[set(v) for v in sections.values()])) \
        if sections else 0
    if total_lines >= 12 and unique_lines and \
            unique_lines / total_lines < 0.55:
        issues.append("章节内容高度重复(unique=%d/%d)" %
                      (unique_lines, total_lines))
    return issues


def _check_evidence_sufficiency(report_type: str,
                                synthesis: Optional[dict]) -> List[str]:
    """A periodic report must be supported by independent verified facts."""
    if not synthesis:
        return ["缺少周期证据包，无法证明事实"]
    clusters = synthesis.get("all_clusters") or []
    selected = synthesis.get("selected_clusters") or clusters
    urls = set()
    for c in clusters:
        for u in (c.get("source_urls") or []):
            if str(u).startswith("http"):
                urls.add(str(u))
    minimum = {"weekly": 2, "monthly": 4, "yearly": 6}.get(report_type, 2)
    issues: List[str] = []
    if len(clusters) < minimum:
        issues.append("独立证据簇不足(%d<%d)" % (len(clusters), minimum))
    if len(urls) < minimum:
        issues.append("独立来源链接不足(%d<%d)" % (len(urls), minimum))
    if not selected:
        issues.append("没有可报告证据簇")
    return issues


def _check_advice_quality(draft: str, report_type: str) -> List[str]:
    """Advisory chapter must contain at least one concrete Watsons action."""
    advice_ch = {"weekly": "05", "monthly": "06", "yearly": "07"}.get(
        report_type, "05")
    m = re.search(r"^##\s+%s\b.*?$\n(.*?)(?=^##\s+\d{2}|\Z)" % advice_ch,
                  draft or "", re.M | re.S)
    if not m:
        return ["建议章节缺失"]
    body = m.group(1)
    hits = [v for v in _ACTIONABLE_VERBS if v in body]
    channels = [c for c in _WATSONS_CHANNELS if c in body]
    issues: List[str] = []
    if not hits:
        issues.append("建议章节无具体可执行动作")
    if not channels:
        issues.append("建议章节未关联屈臣氏渠道")
    if sum(1 for w in _WEAK_ADVICE if w in body) >= 3 and not hits:
        issues.append("建议章节仅含泛泛关注词")
    return issues


def rule_validate(report_type: str, final_draft: str,
                  synthesis: Optional[dict] = None,
                  coverage: Optional[dict] = None,
                  expected_chapters: Optional[List[str]] = None,
                  event_pool_ids: Optional[List[str]] = None,
                  fallback_chapters: Optional[List[str]] = None,
                  generation_stats: Optional[Dict[str, Any]] = None) -> dict:
    """规则化质检。返回 {sendable, high_issues, medium_issues, low_issues,
    failed_checks}。"""
    if not final_draft or not final_draft.strip():
        return {"sendable": False, "high_issues": ["终稿为空"],
                "medium_issues": [], "low_issues": [],
                "failed_checks": ["empty_draft"]}

    high, medium, low = [], [], []
    failed = []

    # 结构
    exp = expected_chapters or _CHAPTER_TITLES.get(report_type, [])
    for issue in _check_structure(final_draft, report_type, exp):
        high.append(issue)
        failed.append("structure")

    # 章节最小长度（§9.4：避免"一句话章节"；"一句话总结"章节除外）
    # 01 是总结性章节可短，02+ 分析章节必须有一定篇幅
    for ch_id in exp:
        if ch_id == "01":
            continue
        m = re.search(
            r"^##\s+%s\s[^\n]*\n(.*?)(?=^##\s+\d{2}|\Z)" % ch_id,
            final_draft, re.MULTILINE | re.DOTALL)
        if m:
            body = m.group(1).strip()
            if body and len(body) < 180:
                high.append("章节 %s 内容过短(%d字)" % (ch_id, len(body)))
                failed.append("chapter_too_short")

    # 内容范围
    for issue in _check_stock_noise(final_draft):
        high.append(issue)
        failed.append("stock_noise")
    for issue in _check_overseas(final_draft):
        medium.append(issue)

    # 展示
    for issue in _check_display_cleanliness(final_draft):
        high.append(issue)
        failed.append("display")

    # 事实
    for issue in _check_unsupported_claims(final_draft, synthesis):
        high.append(issue)
        failed.append("unsupported_claim")
    for issue in _check_numeric_claims(final_draft, synthesis):
        high.append(issue)
        failed.append("numeric_claim_no_evidence")

    # W40 regression: fallback/template/repetition/evidence/action quality.
    # These are high issues, not warnings: a clean-looking but water report
    # must never be emailed.
    for issue in _check_fallback_and_template(
            final_draft, fallback_chapters, generation_stats):
        high.append(issue)
        failed.append("fallback_or_template")
    for issue in _check_content_distinctness(final_draft):
        high.append(issue)
        failed.append("repeated_content")
    for issue in _check_evidence_sufficiency(report_type, synthesis):
        high.append(issue)
        failed.append("insufficient_evidence")
    for issue in _check_advice_quality(final_draft, report_type):
        high.append(issue)
        failed.append("advice_quality")

    # coverage
    if coverage:
        status = coverage.get("status")
        if status == "failing":
            high.append("数据覆盖不达标: %s" % status)
            failed.append("coverage")
        elif status == "degraded":
            medium.append("数据覆盖降级: ratio=%s" %
                          coverage.get("coverage_ratio"))

    sendable = len(high) == 0
    return {
        "sendable": sendable,
        "overall_quality": "good" if sendable and not medium else
                           ("acceptable" if sendable else "needs_revision"),
        "high_issues": high,
        "medium_issues": medium,
        "low_issues": low,
        "failed_checks": sorted(set(failed)),
    }


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def review_final_draft(report_type: str, final_draft: str,
                       synthesis: Optional[dict] = None,
                       coverage: Optional[dict] = None,
                       chapter_ids: Optional[List[str]] = None,
                       event_pool_ids: Optional[List[str]] = None,
                       llm_review: bool = False,
                       fallback_chapters: Optional[List[str]] = None,
                       generation_stats: Optional[Dict[str, Any]] = None) -> dict:
    """终稿质量门。规则检查必做；LLM review 可选（默认不启用，避免额外调用）。"""
    result = rule_validate(report_type, final_draft, synthesis, coverage,
                           chapter_ids, event_pool_ids, fallback_chapters,
                           generation_stats)

    # review 执行失败 → sendable=false（审计 §10.6）
    # 此处规则检查本身不抛异常；若抛异常由调用方捕获并置 sendable=false
    logger.info("%s 质检: sendable=%s high=%d medium=%d",
                LOG_PREFIX, result["sendable"], len(result["high_issues"]),
                len(result["medium_issues"]))
    for h in result["high_issues"]:
        logger.warning("%s   HIGH: %s", LOG_PREFIX, h)
    return result


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="周期报告终稿质检")
    parser.add_argument("--type", required=True,
                        choices=["weekly", "monthly", "yearly"])
    parser.add_argument("--draft", required=True, help="终稿 md 路径")
    parser.add_argument("--synthesis", default="", help="periodic_synthesis.json 路径")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    with open(args.draft, "r", encoding="utf-8") as f:
        draft = f.read()
    synthesis = None
    coverage = None
    if args.synthesis and os.path.exists(args.synthesis):
        with open(args.synthesis, "r", encoding="utf-8") as f:
            synthesis = json.load(f)
        coverage = synthesis.get("coverage")

    result = review_final_draft(args.type, draft, synthesis, coverage)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["sendable"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
