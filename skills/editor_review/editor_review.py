#!/usr/bin/env python3
"""
editor_review.py — 日报审稿技能

三步审稿流程（单稿）或 五步双稿审稿流程：
  单稿: 规则校验 → LLM审稿 → 终稿校验
  双稿: V1检查 → V2检查 → 两稿比较 → LLM综合审稿 → 终稿校验

支持 draft_v2 不存在时回退到单稿审稿模式。

CLI:
  python editor_review.py --project-root ... --date 2026-04-26 --use-llm true
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

# ── 项目路径 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("editor_review")

# ── LLM 客户端 ──
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from skills.utils.llm_client import (
        get_llm_client, check_llm_config, test_llm_connection,
    )
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False

try:
    from skills.utils.model_router import (
        get_model_for_skill, get_model_params, is_rule_only,
    )
    _MODEL_ROUTER_AVAILABLE = True
except ImportError:
    _MODEL_ROUTER_AVAILABLE = False


# ===================== 常量 =====================

VALID_SECTIONS = [
    "01 今日一句话结论",
    "02 今日三条必听",
    "03 即时零售重点变化",
    "04 本地生活重点变化", 
    "05 竞对观察",
    "06 对屈臣氏的机会点",
    "07 风险预警",
    "08 今日建议动作",
    "09 每日八问",
    "11 近期延续观察",
]

VAGUE_PHRASES = [
    "持续关注", "加强优化", "深化布局", "提升能力",
    "赋能增长", "抢占先机", "打造闭环", "加大力度",
]

# ── 标记词：低置信度专用，不含规则兜底标记 ──
MARK_WORDS_LOW = ["待验证", "⚠️", "需验证", "低置信", "低置信度", "🔍"]

# ── 标记词：规则兜底专用，不含低置信度标记 ──
MARK_WORDS_RF = ["规则兜底", "🔄", "规则提取"]


# ===================== 工具函数 =====================

def resolve_path(project_root: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        return rel_path
    return os.path.join(project_root, rel_path)


def count_chinese_chars(text: str) -> int:
    """计算中文字符数（不含空白和标点）。"""
    chars = re.findall(r'[\u4e00-\u9fff]', text)
    return len(chars)


def count_all_chars_no_space(text: str) -> int:
    """计算总字符数（去空白）。"""
    return len(text.replace("\n", "").replace(" ", "").replace("\t", ""))


def _check_mark_near(text: str, eid: str, mark_words: List[str],
                     ctx_before: int = 200, ctx_after: int = 150) -> bool:
    """检查 event_id 首次出现位置附近是否包含指定标记词。

    只检查首次出现位置（典型写作规范：标注只在标题首现处，
    后续引用仅保留 event_id 溯源，不重复标注）。
    """
    pos = text.find(eid)
    if pos < 0:
        return True  # 事件不在终稿中，跳过检查
    ctx = text[max(0, pos - ctx_before):min(len(text), pos + ctx_after)]
    return any(w in ctx for w in mark_words)


# ===================== Step 1: 规则校验 =====================

def find_valid_unique_action_events(events: List[dict]) -> List[dict]:
    """找到所有合规的今日唯一建议动作事件。

    合规条件: priority=P1或更高, confidence≠low, extraction_method≠rule_fallback,
              action_level=immediate或test, report_eligibility≠archive
    """
    candidates = []
    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        if ev.get("report_eligibility") == "archive":
            continue
        ba = ev.get("business_analysis", {})
        if ev.get("priority") not in ("P0", "P1"):
            continue
        if ev.get("confidence") == "low":
            continue
        if ev.get("extraction_method") == "rule_fallback":
            continue
        if ba.get("action_level") not in ("immediate", "test"):
            continue
        candidates.append(ev)

    # 按 weighted_score 降序
    candidates.sort(key=lambda ev: -ev.get("weighted_score", 0))
    return candidates


def rule_validate(
    draft: str,
    events: List[dict],
    event_ids: set,
) -> Tuple[List[dict], Dict[str, Any]]:
    """规则校验日报初稿（Step 1）。

    Returns:
        (issues_list, stats_dict)
    """
    issues = []
    stats = {}

    event_id_pattern = re.compile(r'E\d{8}_\d{4}')

    # ── 1. 是否包含固定章节 ──
    # 11 近期延续观察: 只有存在 ongoing/tracking 事件时才必须
    has_ongoing_or_tracking = any(
        ev.get("novelty_status") == "ongoing"
        or ev.get("report_eligibility") == "tracking"
        for ev in events
        if ev.get("priority") != "ARCHIVE"
    )
    required_sections = list(VALID_SECTIONS)
    if not has_ongoing_or_tracking:
        # No ongoing events → 11 近期延续观察 is optional
        required_sections = [s for s in required_sections if not s.startswith("09")]
    missing_sections = []
    for section in required_sections:
        section_num = section.split()[0]  # "01", "02", ...
        if section not in draft and f"## {section_num} " not in draft and f"## {section_num}\n" not in draft:
            missing_sections.append(section)
    # 11 近期延续观察 if present but empty is also OK
    if "11 近期延续观察" not in draft and "## 11 " not in draft and "## 11\n" not in draft and has_ongoing_or_tracking:
        missing_sections.append("11 近期延续观察")
    if missing_sections:
        issues.append({
            "type": "missing_section",
            "severity": "high",
            "description": f"缺少章节: {', '.join(missing_sections)}",
            "fix": f"补充缺失章节: {', '.join(missing_sections)}",
        })

    # ── 2. 是否至少包含1个event_id ──
    found_ids = set(event_id_pattern.findall(draft))
    if not found_ids:
        issues.append({
            "type": "missing_event_id",
            "severity": "high",
            "description": "日报中未发现任何 event_id 引用",
            "fix": "至少引用1个事件ID",
        })

    # ── 3. event_id 是否全部存在于事件池 ──
    invalid_ids = found_ids - event_ids
    if invalid_ids:
        issues.append({
            "type": "invalid_event_id",
            "severity": "high",
            "description": f"引用不存在的事件ID: {', '.join(sorted(invalid_ids))}",
            "fix": "删除或替换不存在的事件ID",
        })

    # ── 4. 是否引用了不存在的数字 ──
    draft_numbers = re.findall(r'(\d+[\.\d]*%|\d+万|\d+亿|\d+\.?\d*倍)', draft)
    unsourced_numbers = []
    for num_str in draft_numbers:
        found_in_events = False
        for ev in events:
            ev_text = f"{ev.get('event_title', '')} {ev.get('fact', '')} {ev.get('evidence_text', '')}"
            if num_str in ev_text:
                found_in_events = True
                break
        if not found_in_events:
            unsourced_numbers.append(num_str)
    if len(unsourced_numbers) > 5:
        issues.append({
            "type": "unsupported_claim",
            "severity": "medium",
            "description": f"较多无法溯源的数字({len(unsourced_numbers)}个): {', '.join(unsourced_numbers[:5])}",
            "fix": "检查数字是否来自事件池，删除无来源数据",
        })

    # ── 5. 今日唯一建议动作是否只有一条 ──
    section_07_start = draft.find("08 今日建议动作")
    if section_07_start >= 0:
        section_07_end = draft.find("---", section_07_start + 1)
        if section_07_end < 0:
            section_07_end = draft.find("## 08", section_07_start + 1)
        if section_07_end < 0:
            section_07_end = len(draft)
        section_07_text = draft[section_07_start:section_07_end]

        action_lines = [l for l in section_07_text.split("\n")
                        if l.strip().startswith("-") and "建议动作" in l]
        if len(action_lines) > 1:
            issues.append({
                "type": "invalid_unique_action",
                "severity": "high",
                "description": f"今日唯一建议动作包含{len(action_lines)}条，必须只有1条",
                "fix": "只保留最优先的1条建议动作",
            })

    # ── 6. 今日唯一建议动作是否合规 ──
    valid_actions = find_valid_unique_action_events(events)
    if section_07_start >= 0 and valid_actions:
        target_id = valid_actions[0].get("event_id", "")
        if target_id and target_id not in section_07_text:
            issues.append({
                "type": "invalid_unique_action",
                "severity": "medium",
                "description": f"唯一建议动作未引用最优先事件 {target_id}",
                "fix": f"确保建议动作来自 {target_id}",
            })

    # ── 7. low confidence 事件标记（独立检查，只用低置信度标记词） ──
    low_events = [ev for ev in events if ev.get("confidence") == "low"]
    for ev in low_events:
        eid = ev.get("event_id", "")
        title = ev.get("event_title", "")
        short_title = title[:20] if title else ""

        eid_positions = [m.start() for m in re.finditer(re.escape(eid), draft)]
        title_positions = [m.start() for m in re.finditer(re.escape(short_title), draft)]
        all_positions = eid_positions + title_positions

        if all_positions:
            has_mark = False
            for pos in all_positions:
                ctx_start = max(0, pos - 150)
                ctx_end = min(len(draft), pos + 100)
                context = draft[ctx_start:ctx_end]
                # 只检查低置信度专用标记词
                if any(w in context for w in MARK_WORDS_LOW):
                    has_mark = True
                    break

            if not has_mark:
                    is_p1_or_high = (
                        ev.get("priority") == "P1"
                        or (ev.get("weighted_score", 0) >= 3.5
                        and ev.get("scores", {}).get("source_credibility", 0) >= 3)
                    )
                    issues.append({
                        "type": "low_confidence_strong_claim",
                        "severity": "low" if is_p1_or_high else "medium",
                        "description": f"低置信度事件 {eid} 缺少标记({'🔍' if is_p1_or_high else '⚠️待验证'})",
                        "fix": f"在 {eid} 附近添加{'🔍' if is_p1_or_high else '⚠️待验证'}标记",
                    })

    # ── 8. rule_fallback 事件标记（独立检查，只用规则兜底标记词） ──
    rf_events = [ev for ev in events if ev.get("extraction_method") == "rule_fallback"]
    for ev in rf_events:
        eid = ev.get("event_id", "")
        eid_positions = [m.start() for m in re.finditer(re.escape(eid), draft)]

        if eid_positions:
            has_mark = False
            for pos in eid_positions:
                ctx_start = max(0, pos - 200)
                ctx_end = min(len(draft), pos + 50)
                context = draft[ctx_start:ctx_end]
                # 只检查规则兜底专用标记词，不含低置信度标记
                if any(w in context for w in MARK_WORDS_RF):
                    has_mark = True
                    break

            if not has_mark:
                issues.append({
                    "type": "rule_fallback_strong_claim",
                    "severity": "medium",
                    "description": f"rule_fallback事件 {eid} 缺少'🔄规则兜底'标记",
                    "fix": f"在 {eid} 附近添加'🔄规则兜底'标记",
                })

    # ── 8.5 tracking事件不得出现在02-05正文节 ──
    sec_body = re.search(r'## 02.*?(?=## 06|\Z)', draft, re.DOTALL)
    sec_body_c = sec_body.group(0) if sec_body else ""
    tracking_evs = [ev for ev in events
                    if ev.get("report_eligibility") == "tracking"
                    and ev.get("priority") != "ARCHIVE"]
    for ev in tracking_evs:
        eid = ev.get("event_id", "")
        if eid and eid in sec_body_c:
            issues.append({
                "type": "tracking_in_core_signal",
                    "severity": "medium",
                "description": f"tracking事件 {eid} 出现在02-05正文节，应仅在11节",
                "fix": f"将 {eid} 从02-05节移除，仅保留在11节（若是背景引用则不阻断发送）",
            })

    # ── 9. 全文字数统计（仅信息，不约束） ──

    # ── 10. 空话检查 ──
    vague_found = []
    for phrase in VAGUE_PHRASES:
        if phrase in draft:
            positions = [m.start() for m in re.finditer(re.escape(phrase), draft)]
            for pos in positions:
                ctx_start = max(0, pos - 40)
                ctx_end = min(len(draft), pos + len(phrase) + 40)
                context = draft[ctx_start:ctx_end]
                has_specific_object = any(
                    kw in context for kw in
                    ["GMV", "订单", "复购", "客单价", "转化率", "SKU",
                     "资源位", "店数", "增速", "市场份额", "品类"]
                )
                if not has_specific_object:
                    vague_found.append(phrase)

    if vague_found:
        unique_vague = list(set(vague_found))
        issues.append({
            "type": "vague_action",
            "severity": "low",
            "description": f"空话/套话: {', '.join(unique_vague)}",
            "fix": "替换为具体行动或删除",
        })

    # ── 11. 新颖性规则检查 ──
    # repeated 事件不得出现在核心信号中
    # background 事件不得出现在核心正文中
    section_02_start = draft.find("02 今日三条必听")
    if section_02_start >= 0:
        section_02_end = draft.find("---", section_02_start + 1)
        if section_02_end < 0:
            section_02_end = draft.find("## 03", section_02_start + 1)
        if section_02_end < 0:
            section_02_end = len(draft)
        section_02_text = draft[section_02_start:section_02_end]
        # 检查 repeated 事件不得在核心信号中
        repeated_in_signal = []
        for ev in events:
            if ev.get("novelty_status") == "repeated":
                ev_id = ev.get("event_id", "")
                if ev_id and ev_id in section_02_text:
                    repeated_in_signal.append(f"{ev_id}: {ev.get('event_title', '')[:40]}")
        if repeated_in_signal:
            issues.append({
                "type": "repeated_in_core_signal",
                "severity": "high",
                "description": f"repeated事件出现在核心信号中: {', '.join(repeated_in_signal[:3])}",
                "fix": "将repeated事件移至追踪清单或延续观察区域",
            })
        # 检查 background 事件不得在核心信号中
        background_in_signal = []
        for ev in events:
            if ev.get("report_eligibility") == "archive":
                ev_id = ev.get("event_id", "")
                if ev_id and ev_id in section_02_text:
                    background_in_signal.append(f"{ev_id}: {ev.get('event_title', '')[:40]}")
        if background_in_signal:
            issues.append({
                "type": "background_in_core_signal",
                "severity": "medium",
                "description": f"background事件出现在核心信号中: {', '.join(background_in_signal[:3])}",
                "fix": "删除background事件引用，或移至追踪清单",
            })

    # ── 12. 新颖性统计 ──
    novelty_stats = {
        "core_count": sum(1 for ev in events if ev.get("report_eligibility") == "core"),
        "tracking_count": sum(1 for ev in events if ev.get("report_eligibility") == "tracking"),
        "reference_count": sum(1 for ev in events if ev.get("report_eligibility") == "reference"),
        "archive_count": sum(1 for ev in events if ev.get("report_eligibility") == "archive"),
        "new_today_count": sum(1 for ev in events if ev.get("novelty_status") == "new_today"),
        "updated_today_count": sum(1 for ev in events if ev.get("novelty_status") == "updated_today"),
        "ongoing_count": sum(1 for ev in events if ev.get("novelty_status") == "ongoing"),
        "repeated_count": sum(1 for ev in events if ev.get("novelty_status") == "repeated"),
    }
    stats["novelty_stats"] = novelty_stats

    # ── 统计 ──
    stats["date"] = ""
    stats["event_id_count"] = len(found_ids)
    stats["invalid_event_id_count"] = len(invalid_ids)
    stats["issue_count"] = len(issues)
    stats["high_severity_count"] = sum(1 for i in issues if i.get("severity") == "high")
    stats["medium_severity_count"] = sum(1 for i in issues if i.get("severity") == "medium")
    stats["low_severity_count"] = sum(1 for i in issues if i.get("severity") == "low")

    return issues, stats


# ===================== Step 2: LLM 审稿 =====================

LLM_SYSTEM_PROMPT = """你是"即时零售 × 个护美妆经营日报"的总编审稿Agent。

你的任务是审查日报初稿，并基于事件池修订为可发送终稿。

## 内容质量标准
每条核心信号必须包含（按此顺序）：
- **事实**：客观准确的事件描述，引用具体数据
- **解释**：为什么发生、背后的驱动因素
- **判断**：对竞争格局/市场趋势的影响
- **对屈臣氏的意义**：为什么屈臣氏电商负责人要在意

你必须遵守：
1. 不得新增事件池外事实。
2. 不得新增事件池外数据。
3. 不得把低置信度事件写成确定性结论。
4. 不得把规则兜底事件写成强结论。
5. 删除空话、套话、重复内容。
6. 强化屈臣氏电商负责人视角。
7. 每个核心判断必须保留 event_id。
8. 今日唯一建议动作只能有一条。
9. 终稿必须严格保留以下9个固定章节结构（09为每日八问），章节名不能改动，不能增删：
   - 01 今日一句话结论
   - 02 今日三条必听
   - 03 即时零售重点变化
   - 04 本地生活重点变化
   - 05 竞对观察
   - 06 对屈臣氏的机会点
   - 07 风险预警
   - 08 今日建议动作
   - 09 每日八问
   （如有ongoing事件，可追加11 近期延续观察）
10. report_eligibility=tracking 的事件只能出现在11 近期延续观察节（如有），严禁移入02-05节正文。
11. 不得新增事件池外的事件ID。
12. 篇幅自然：事件多信息量大自然写长，事件少精简写短。不设固定字数上下限，宁长勿短——宁可多写有价值的分析和建议，不要为了"短"砍掉洞察。
13. 06经营提示按动作类型分类（商品动作/平台动作/营销动作/试点动作），每个分类：立即可做→建议试点→需要总部支持→持续观察，建议必须是"可以立刻做的事"，不能是"分析""评估""跟踪"这类观察性动词。
14. 输出严格JSON。
15. 表格用GFM格式（|---|---|），###和---前后留空行。
16. 04竞争格局建议用表格，05品类机会建议用表格。
17. 每条事件分析必须包含"经营启示"或"对屈臣氏的意义"，不能只描述事实。

重要标记规则（仅对confidence=low的事件）：
- confidence=low 且 P1/高加权分(ws>=3.5+source_cred>=3) → 标🔍
- confidence=low 且不满足上述 → 标⚠️待验证
- confidence=medium 或 high → 不需要任何标记
- extraction_method=rule_fallback → 标🔄规则兜底（与confidence标记可叠加）

输出JSON格式：
{
  "review": {
    "issues": [...],
    "sendable": true/false,
    "summary": "审稿摘要"
  },
  "final_markdown": "终稿Markdown全文"
}"""

LLM_USER_TEMPLATE = """请审稿并修订以下日报初稿，生成可发送终稿。

日期：{date}
唯一建议动作候选事件：{action_candidates}

--- 事件池摘要 ---
{events_summary}

--- 日报初稿 ---
{draft}

--- 内容要求 ---
1. 不得新增事件池外事实和数据
2. 每条核心信号包含：事实、解释、判断、对屈臣氏的意义
3. 06经营提示按动作类型分类（商品动作/平台动作/营销动作/试点动作），每个分类：立即可做→建议试点→需要总部支持→持续观察，建议具体可执行
4. confidence=low 的事件才需要标记：P1/高加权分(ws>=3.5+source_cred>=3)→🔍，其余→⚠️待验证
5. extraction_method=rule_fallback→🔄规则兜底（可与置信度标记叠加）
6. 删除空话套话，保留具体行动建议
7. 今日唯一建议动作只保留1条，含负责人方向和时间节点
8. 04竞争格局用表格呈现，05品类机会用表格呈现
9. 篇幅由信息量决定：事件多就多写、把分析和建议写透；事件少就精简。不设固定字数限制。每一条建议必须是"可以立刻做的事"，不能是"分析""评估""跟踪""监测"这类空洞的观察性动词。
10. 保留所有 event_id 引用
11. 严格保留9个固定章节（01-09），章节名不可改动：
    01 今日一句话结论、02 今日三条必听、03 即时零售重点变化、04 本地生活重点变化、05 竞对观察、06 对屈臣氏的机会点、07 风险预警、08 今日建议动作、09 每日八问。如有ongoing事件加追11 近期延续观察。
12. 每个事件标注判断标签（A-必须关注/B-本周跟进/C-趋势观察/R-风险预警/K-竞对可借鉴/X-需跨部门）
13. 09 每日八问必须完整回答8个问题
12. ###和---前后留空行，表格用GFM格式
13. 输出严格JSON"""


def llm_review(
    draft: str,
    events: List[dict],
    date_str: str,
    llm_client,
    model: str = None,
) -> Optional[dict]:
    """使用 LLM 审稿并生成终稿。

    Returns:
        dict with keys: review, final_markdown; or None on failure
    """
    events_summary_lines = []
    for ev in events:
        if ev.get("priority") == "ARCHIVE":
            continue
        ba = ev.get("business_analysis", {})
        ev_line = (
            f"[{ev.get('priority','')}] {ev.get('event_id','')} "
            f"{ev.get('event_title','')} | "
            f"conf={ev.get('confidence','?')} "
            f"method={ev.get('extraction_method','?')} "
            f"action={ba.get('action_level','?')} | "
            f"{ev.get('fact','')[:80]}"
        )
        events_summary_lines.append(ev_line)
    events_summary = "\n".join(events_summary_lines[:15])

    valid_actions = find_valid_unique_action_events(events)
    if valid_actions:
        va = valid_actions[0]
        action_candidate = f"{va.get('event_id')} - {va.get('event_title')} (action_level={va.get('business_analysis',{}).get('action_level','?')})"
    else:
        action_candidate = "无合规事件，建议保留提示文字"

    user_prompt = LLM_USER_TEMPLATE.format(
        date=date_str,
        action_candidates=action_candidate,
        events_summary=events_summary,
        draft=draft,
    )

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=LLM_SYSTEM_PROMPT,
            response_format="json",
            temperature=0.3,
            max_tokens=8192,
            model=model,
        )

        content = result.get("content", "")
        if not content.strip():
            reasoning = result.get("reasoning_content", "")
            if reasoning.strip():
                content = reasoning

        if not content.strip():
            logger.warning("LLM 审稿返回为空")
            return None

        parsed = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            from skills.utils.llm_client import robust_json_extract
            parsed = robust_json_extract(content)

        if parsed and isinstance(parsed, dict):
            if "final_markdown" in parsed:
                return parsed
            elif "review" in parsed:
                logger.warning("LLM 返回了审稿意见但没有 final_markdown")
                return parsed

        logger.warning("LLM 审稿返回无法解析为有效 JSON")
        return None

    except Exception as e:
        logger.warning(f"LLM 审稿失败: {e}")
        return None


# ===================== Step 3: 终稿校验 =====================

def final_validate(
    final_md: str,
    events: List[dict],
    event_ids: set,
) -> Tuple[bool, List[dict]]:
    """终稿校验（Step 3）。

    独立于 Step 1，只检查终稿本身的问题。
    低置信度标记与规则兜底标记完全独立检查，互不替代。

    Returns:
        (passed, final_issues)
        passed: 基本结构合法性 (有8章节 + 有event_id + 无无效ID)
        final_issues: 结构化问题列表，含 severity 字段
    """
    final_issues = []

    # 1. 固定章节（11 近期延续观察: 如无 ongoing/tracking 事件则可选）
    has_ongoing_or_tracking = any(
        ev.get("novelty_status") == "ongoing"
        or ev.get("report_eligibility") == "tracking"
        for ev in events
        if ev.get("priority") != "ARCHIVE"
    )
    required_sections_final = list(VALID_SECTIONS)
    if not has_ongoing_or_tracking:
        required_sections_final = [s for s in required_sections_final if not s.startswith("09")]
    for section in required_sections_final:
        # 宽容匹配：精确匹配 或 数字前缀匹配（LLM 可能简化标题文字）
        section_num = section.split()[0]  # "01", "02", ...
        if section not in final_md and f"## {section_num} " not in final_md and f"## {section_num}\n" not in final_md:
            final_issues.append({
                "type": "missing_section",
                "severity": "high",
                "description": f"终稿缺少章节: {section}",
                "stage": "final",
            })

    # 2. 至少1个event_id
    found_ids = set(re.findall(r'E\d{8}_\d{4}', final_md))
    if not found_ids:
        final_issues.append({
            "type": "missing_event_id",
            "severity": "high",
            "description": "终稿中未发现任何 event_id",
            "stage": "final",
        })

    # 3. 不存在的事件ID
    invalid_ids = found_ids - event_ids
    if invalid_ids:
        final_issues.append({
            "type": "invalid_event_id",
            "severity": "high",
            "description": f"终稿引用不存在的事件ID: {', '.join(sorted(invalid_ids))}",
            "stage": "final",
        })

    # 4. 唯一建议动作只有1条
    section_07_start = final_md.find("08 今日建议动作")
    section_07_text = ""
    if section_07_start >= 0:
        section_07_end = final_md.find("---", section_07_start + 1)
        if section_07_end < 0:
            section_07_end = final_md.find("## 08", section_07_start + 1)
        if section_07_end < 0:
            section_07_end = len(final_md)
        section_07_text = final_md[section_07_start:section_07_end]
        action_lines = [l for l in section_07_text.split("\n")
                        if l.strip().startswith("-") and "建议动作" in l]
        if len(action_lines) > 1:
            final_issues.append({
                "type": "invalid_unique_action",
                "severity": "high",
                "description": f"唯一建议动作含{len(action_lines)}条，应只保留1条",
                "stage": "final",
            })

    # 5. 合规事件检查
    valid_actions = find_valid_unique_action_events(events)
    if valid_actions and section_07_start >= 0:
        target_id = valid_actions[0].get("event_id", "")
        if target_id and target_id not in section_07_text:
            final_issues.append({
                "type": "invalid_unique_action",
                "severity": "medium",
                "description": f"唯一建议动作应来自合规事件 {target_id}",
                "fix": f"替换为合规事件 {target_id}",
                "stage": "final",
            })

    # 6. 字数（仅做信息性统计，不强制约束）

    # 7. low confidence 标记（独立检查，只用低置信度专用标记词）
    low_events = [ev for ev in events if ev.get("confidence") == "low"]
    for ev in low_events:
        eid = ev.get("event_id", "")
        if eid and eid in final_md:
            if not _check_mark_near(final_md, eid, MARK_WORDS_LOW):
                is_p1_or_high = (
                    ev.get("priority") == "P1"
                    or (ev.get("weighted_score", 0) >= 3.5
                        and ev.get("scores", {}).get("source_credibility", 0) >= 3)
                )
                final_issues.append({
                    "type": "low_confidence_strong_claim",
                    "severity": "low" if is_p1_or_high else "medium",
                    "description": f"低置信度事件 {eid} 缺少标记({'🔍' if is_p1_or_high else '⚠️待验证'})",
                    "fix": f"在 {eid} 附近添加{'🔍' if is_p1_or_high else '⚠️待验证'}标记",
                    "stage": "final",
                })

    # 8. rule_fallback 标记（独立检查，只用规则兜底专用标记词）
    rf_events = [ev for ev in events if ev.get("extraction_method") == "rule_fallback"]
    for ev in rf_events:
        eid = ev.get("event_id", "")
        if eid and eid in final_md:
            if not _check_mark_near(final_md, eid, MARK_WORDS_RF):
                final_issues.append({
                    "type": "rule_fallback_strong_claim",
                    "severity": "medium",
                    "description": f"rule_fallback事件 {eid} 缺少'🔄规则兜底'标记",
                    "fix": f"在 {eid} 附近添加'🔄规则兜底'标记",
                    "stage": "final",
                })

    # ── 9. tracking事件不得出现在02-05正文节 ──
    # Sections 02-05 are the main body where tracking events don't belong
    sec_body_md = re.search(r'## 02.*?(?=## 06|\Z)', final_md, re.DOTALL)
    sec_body_final = sec_body_md.group(0) if sec_body_md else ""
    tracking_events_f = [ev for ev in events
                       if ev.get("report_eligibility") == "tracking"
                       and ev.get("priority") != "ARCHIVE"]
    for ev in tracking_events_f:
        eid = ev.get("event_id", "")
        if eid and eid in sec_body_final:
            final_issues.append({
                "type": "tracking_in_core_signal",
                    "severity": "medium",
                "description": f"tracking事件 {eid}（{ev.get('event_title','')[:30]}）出现在02-05正文节，应仅在11节出现",
                "fix": f"将 {eid} 从02-05节移除，仅保留在11节（若是背景引用则不阻断发送）",
                "stage": "final",
            })

    # ── 计算 passed ──
    # passed=True 意味着没有任何 high severity 终稿问题
    # (缺章节、无event_id、无效ID、多条建议动作 都会阻碍发送)
    high_count = sum(1 for i in final_issues if i.get("severity") == "high")
    passed = high_count == 0

    return passed, final_issues


# ===================== 规则压缩 =====================

def _clean_report_for_display(md_text: str) -> str:
    """清理日报文本，移除内部标记供展示用。"""
    # ── 0. 移除 LLM 思考泄露（开头非标题文字） ──
    md_text = re.sub(r'^[^#\n].*?(?=## 01|# )', '', md_text, count=1, flags=re.DOTALL)

    # ── 1. 移除事件 ID（各种格式） ──
    # （`E20260530_0037`，A） 或 （`E20260530_0037`、`E20260530_0003`，K）
    md_text = re.sub(r'（[`E\d_、，\s]*?[，,]\s*[A-Z]）', '', md_text)
    # （`E20260530_0037`） 不带标签
    md_text = re.sub(r'（[`E\d_、\s]+）', '', md_text)
    # [`E20260504_0028`]
    md_text = re.sub(r'\[`E\d{8}_\d{4}`\]', '', md_text)
    # `E20260530_0037`
    md_text = re.sub(r'`E\d{8}_\d{4}`', '', md_text)
    # 裸 E20260530_0037
    md_text = re.sub(r'E\d{8}_\d{4}', '', md_text)

    # ── 2. 移除判断标签行 ──
    md_text = re.sub(r'^\*?\*?判断标签[：:]\s*`?[A-Z]`?\*?\*?\s*$',
                     '', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'^\*?\*?涉及事件[：:].*$',
                     '', md_text, flags=re.MULTILINE)

    # ── 3. 移除证据事件/置信度行 ──
    md_text = re.sub(r'^-\s*\*\*证据事件\*\*[：:]\s*.*$',
                     '', md_text, flags=re.MULTILINE)
    md_text = re.sub(r'^-\s*\*\*置信度\*\*[：:].*$',
                     '', md_text, flags=re.MULTILINE)

    # ── 4. 清理残留标点 ──
    md_text = re.sub(r'（\s*[，、]\s*）', '', md_text)
    md_text = re.sub(r'（\s*）', '', md_text)
    md_text = re.sub(r'\[\]', '', md_text)
    md_text = re.sub(r'对应事件[：:]\s*[、，\s]*。', '', md_text)
    md_text = re.sub(r'围绕\s*[，,]\s*由', '由', md_text)
    md_text = re.sub(r'[ \t]+，', '，', md_text)
    md_text = re.sub(r'，[ \t]+', '，', md_text)
    md_text = re.sub(r'[ \t]+。', '。', md_text)
    md_text = re.sub(r'。[ \t]+', '。', md_text)
    md_text = re.sub(r'，，+', '，', md_text)
    md_text = re.sub(r'、、+', '、', md_text)
    md_text = re.sub(r'\*\*event_id\*\*[：:]\s*', '', md_text)
    md_text = re.sub(r'event_id[：:]\s*', '', md_text)

    # ── 5. 修复 markdown 格式 ──
    md_text = _fix_markdown_inline(md_text)

    # ── 6. 清理多余空行 ──
    md_text = re.sub(r'\n{3,}', '\n\n', md_text)
    md_text = re.sub(r'[ \t]+$', '', md_text, flags=re.MULTILINE)
    return md_text.strip()



def _fix_markdown_inline(md_text: str) -> str:
    """修复被挤到同一行的 markdown 标记。

    当事件ID被清理后，原本靠事件ID分隔的标题/分隔线可能
    粘在前一行文本末尾，需要把它们分开。
    """
    # ### 标题前补空行
    md_text = re.sub(r'([^\n])(### )', r'\1\n\n\2', md_text)
    # --- 分隔线前补空行
    md_text = re.sub(r'([^\n])(---\s*\n)', r'\1\n\n\2', md_text)
    md_text = re.sub(r'([^\n])(---)$', r'\1\n\n\2', md_text, flags=re.MULTILINE)
    # > 引用前补空行
    md_text = re.sub(r'([^\n])(> )', r'\1\n\n\2', md_text)
    return md_text


def rule_compress(draft: str, events: List[dict]) -> str:
    """规则压缩：删除空话、添加缺失标记、确保唯一建议动作。

    当 LLM 不可用或终稿校验失败时使用。
    低置信度和规则兜底标记独立添加，互不干扰。
    """
    lines = draft.split("\n")
    compressed_lines = []

    for line in lines:
        stripped = line.strip()
        is_vague_only = False
        for phrase in VAGUE_PHRASES:
            if stripped == phrase or stripped == f"- {phrase}" or stripped == f"• {phrase}":
                is_vague_only = True
                break
        if is_vague_only:
            continue
        compressed_lines.append(line)

    result = "\n".join(compressed_lines)

    # ── 确保唯一建议动作只有1条 ──
    section_07_start = result.find("## 08 今日建议动作")
    if section_07_start >= 0:
        valid_actions = find_valid_unique_action_events(events)
        section_07_end = result.find("## 08", section_07_start + 1)
        if section_07_end < 0:
            section_07_end = result.find("---", section_07_start + 1)
        if section_07_end < 0:
            section_07_end = len(result)

        if valid_actions:
            va = valid_actions[0]
            ba = va.get("business_analysis", {})
            new_section = f"""## 08 今日建议动作

- **建议动作**：{ba.get('recommended_action', '待确认')}
- **对应事件**：`{va.get('event_id', '')}` — {va.get('event_title', '')}
- **负责方向**：{ba.get('owner_hint', '待分派')}
- **今天要看的指标**：{'、'.join(ba.get('tracking_metrics', ['待确认'])[:4])}
- **为什么是今天最值得做**：{va.get('priority', 'P1')}级信号，加权评分{va.get('weighted_score', 0):.2f}
"""
            result = result[:section_07_start] + new_section + result[section_07_end:]
        else:
            new_section = """## 08 今日建议动作

今日不建议贸然推动新增动作，建议以复核高价值线索和追踪平台变化为主。
"""
            result = result[:section_07_start] + new_section + result[section_07_end:]

    # ── 添加缺失的 low confidence 标记（独立于 rule_fallback） ──
    low_events = [ev for ev in events if ev.get("confidence") == "low"]
    for ev in low_events:
        eid = ev.get("event_id", "")
        if eid and eid in result:
            if not _check_mark_near(result, eid, MARK_WORDS_LOW):
                is_p1_or_high = (
                    ev.get("priority") == "P1"
                    or (ev.get("weighted_score", 0) >= 3.5
                        and ev.get("scores", {}).get("source_credibility", 0) >= 3)
                )
                tag = "🔍" if is_p1_or_high else "⚠️待验证"
                result = result.replace(eid, f"{eid}{tag}", 1)

    # ── 添加缺失的 rule_fallback 标记（独立于 low confidence） ──
    rf_events = [ev for ev in events if ev.get("extraction_method") == "rule_fallback"]
    for ev in rf_events:
        eid = ev.get("event_id", "")
        if eid and eid in result:
            if not _check_mark_near(result, eid, MARK_WORDS_RF):
                # 在 event_id 首次出现处后面紧跟插入标记
                # 注意：如果之前已经插入了⚠️待验证，eid后可能已有内容
                # 需要找到 eid 后紧跟的位置
                result = result.replace(eid, f"{eid}🔄规则兜底", 1)

    return result


# ===================== 双稿审稿 =====================

def check_v2_restraint(
    draft_v2: str,
    events: List[dict],
    event_ids: set,
) -> Tuple[List[dict], Dict[str, Any]]:
    """检查 V2 经营总编重构稿是否克制、聚焦。

    Returns:
        (issues_list, stats_dict)
    """
    issues = []
    stats = {}

    # ── 1. 核心信号最多3条 ──
    signal_headers = re.findall(r'### 信号\d', draft_v2)
    signal_count = len(signal_headers)
    stats["v2_signal_count"] = signal_count
    if signal_count > 3:
        issues.append({
            "type": "too_many_signals",
            "severity": "medium",
            "description": f"V2核心信号{signal_count}条，超过上限3条",
            "fix": "只保留最重要的3条信号",
        })

    # ── 2. 唯一建议动作只有1条 ──
    s07_start = draft_v2.find("08 今日建议动作")
    if s07_start >= 0:
        s07_end = draft_v2.find("## 08", s07_start + 1)
        if s07_end < 0:
            s07_end = len(draft_v2)
        s07_text = draft_v2[s07_start:s07_end]
        action_lines = [l for l in s07_text.split("\n")
                        if l.strip().startswith("-") and "建议动作" in l]
        if len(action_lines) > 1:
            issues.append({
                "type": "invalid_unique_action",
                "severity": "high",
                "description": f"V2唯一建议动作含{len(action_lines)}条",
                "fix": "只保留1条",
            })

    # ── 3. 字数统计（仅信息，不约束） ──

    # ── 4. low confidence 只能作为线索 ──
    low_events = [ev for ev in events if ev.get("confidence") == "low"]
    for ev in low_events:
        eid = ev.get("event_id", "")
        if eid and eid in draft_v2:
            pos = draft_v2.find(eid)
            ctx = draft_v2[max(0, pos - 200):min(len(draft_v2), pos + 150)]
            # 必须有标记（🔍 或 ⚠️待验证）
            if not any(w in ctx for w in MARK_WORDS_LOW):
                is_p1_or_high = (
                    ev.get("priority") == "P1"
                    or (ev.get("weighted_score", 0) >= 3.5
                        and ev.get("scores", {}).get("source_credibility", 0) >= 3)
                )
                issues.append({
                    "type": "low_confidence_strong_claim",
                    "severity": "low" if is_p1_or_high else "medium",
                    "description": f"V2低置信度事件 {eid} 缺少{'🔍' if is_p1_or_high else '⚠️待验证'}标记",
                    "fix": f"在 {eid} 附近添加{'🔍' if is_p1_or_high else '⚠️待验证'}",
                })

    # ── 5. rule_fallback 只能作为线索 ──
    rf_events = [ev for ev in events if ev.get("extraction_method") == "rule_fallback"]
    for ev in rf_events:
        eid = ev.get("event_id", "")
        if eid and eid in draft_v2:
            pos = draft_v2.find(eid)
            ctx = draft_v2[max(0, pos - 200):min(len(draft_v2), pos + 150)]
            if not any(w in ctx for w in MARK_WORDS_RF):
                issues.append({
                    "type": "rule_fallback_strong_claim",
                    "severity": "medium",
                    "description": f"V2 rule_fallback事件 {eid} 缺少🔄规则兜底标记",
                    "fix": f"在 {eid} 附近添加🔄规则兜底",
                })

    # ── 5.5 tracking事件不得进入V2的02-05正文节 ──
    sec_body_v2 = re.search(r'## 02.*?(?=## 06|\Z)', draft_v2, re.DOTALL)
    sec_body_v2c = sec_body_v2.group(0) if sec_body_v2 else ""
    tracking_evs_v2 = [ev for ev in events
                       if ev.get("report_eligibility") == "tracking"
                       and ev.get("priority") != "ARCHIVE"]
    for ev in tracking_evs_v2:
        eid = ev.get("event_id", "")
        if eid and eid in sec_body_v2c:
            issues.append({
                "type": "tracking_in_core_signal",
                    "severity": "medium",
                "description": f"V2 tracking事件 {eid} 出现在02-05正文节，应仅限11节",
                "fix": f"将 {eid} 从02-05节移除（若是背景引用则不阻断发送）",
            })

    # ── 6. 固定章节 ──
    has_ongoing = any(
        ev.get("novelty_status") == "ongoing"
        or ev.get("report_eligibility") == "tracking"
        for ev in events
        if ev.get("priority") != "ARCHIVE"
    )
    v2_required = list(VALID_SECTIONS)
    if not has_ongoing:
        v2_required = [s for s in v2_required if not s.startswith("09")]
    for section in v2_required:
        if section not in draft_v2:
            issues.append({
                "type": "missing_section",
                "severity": "high",
                "description": f"V2缺少章节: {section}",
                "fix": f"补充 {section}",
            })

    stats["v2_issue_count"] = len(issues)
    stats["v2_high_count"] = sum(1 for i in issues if i.get("severity") == "high")
    stats["v2_medium_count"] = sum(1 for i in issues if i.get("severity") == "medium")
    stats["v2_low_count"] = sum(1 for i in issues if i.get("severity") == "low")
    stats["v2_char_count"] = len(draft_v2)
    stats["v2_chinese_chars"] = chinese_count
    stats["v2_event_id_count"] = len(set(re.findall(r'E\d{8}_\d{4}', draft_v2)))

    return issues, stats


def compare_drafts(
    draft_v1: str,
    draft_v2: str,
    events: List[dict],
) -> List[dict]:
    """比较两稿差异，判断是否遗漏P1事件或存在事件池外事实。

    Returns:
        issues_list
    """
    issues = []
    event_id_pattern = re.compile(r'E\d{8}_\d{4}')

    v1_ids = set(event_id_pattern.findall(draft_v1))
    v2_ids = set(event_id_pattern.findall(draft_v2))

    # ── 1. V2是否遗漏了P1事件 ──
    p1_events = [ev for ev in events if ev.get("priority") in ("P0", "P1")]
    for ev in p1_events:
        eid = ev.get("event_id", "")
        if eid in v1_ids and eid not in v2_ids:
            issues.append({
                "type": "missing_p1_event",
                "severity": "high",
                "description": f"V2遗漏P1事件 {eid} — {ev.get('event_title', '')[:40]}",
                "fix": f"在V2中补充 {eid}",
            })

    # ── 2. V2是否存在V1中没有的新数字 ──
    v1_numbers = set(re.findall(r'\d+[\.\d]*[万亿%]|\d+亿|\d+万|\d+\.?\d*倍', draft_v1))
    v2_numbers = set(re.findall(r'\d+[\.\d]*[万亿%]|\d+亿|\d+万|\d+\.?\d*倍', draft_v2))
    new_in_v2 = v2_numbers - v1_numbers

    # 再检查事件池
    pool_text = " ".join([
        f"{ev.get('event_title','')} {ev.get('fact','')} {ev.get('evidence_text','')}"
        for ev in events
    ])
    pool_numbers = set(re.findall(r'\d+[\.\d]*[万亿%]|\d+亿|\d+万|\d+\.?\d*倍', pool_text))
    unsourced = new_in_v2 - pool_numbers

    if unsourced:
        issues.append({
            "type": "unsupported_claim",
            "severity": "medium",
            "description": f"V2新增V1未有的数字且无法溯源: {', '.join(list(unsourced)[:5])}",
            "fix": "删除或标注数据来源",
        })

    # ── 3. V2唯一建议动作是否合规 ──
    valid_actions = find_valid_unique_action_events(events)
    s07_start = draft_v2.find("08 今日建议动作")
    if valid_actions and s07_start >= 0:
        s07_end = draft_v2.find("## 08", s07_start + 1)
        if s07_end < 0:
            s07_end = len(draft_v2)
        s07_text = draft_v2[s07_start:s07_end]
        target_id = valid_actions[0].get("event_id", "")
        if target_id and target_id not in s07_text:
            issues.append({
                "type": "invalid_unique_action",
                "severity": "medium",
                "description": f"V2唯一建议动作未引用最优先合规事件 {target_id}",
                "fix": f"替换为合规事件 {target_id}",
            })

    # ── 4. 差异统计 ──
    stats = {
        "v1_event_id_count": len(v1_ids),
        "v2_event_id_count": len(v2_ids),
        "ids_only_in_v1": sorted(v1_ids - v2_ids),
        "ids_only_in_v2": sorted(v2_ids - v1_ids),
        "p1_missing_in_v2": [ev.get("event_id") for ev in p1_events
                             if ev.get("event_id") in v1_ids and ev.get("event_id") not in v2_ids],
    }

    return issues, stats


# ===================== 审稿报告生成 =====================

def generate_review_report(
    issues: List[dict],
    stats: Dict[str, Any],
    events: List[dict],
    sendable: bool,
    validation_passed: bool,
    model_used: str,
    fallback_used: bool,
    draft_file: str,
    events_file: str,
    review_file: str,
    final_file: str,
    final_issues: List[dict] = None,
    draft_high_severity_count: int = 0,
    draft_medium_severity_count: int = 0,
    draft_low_severity_count: int = 0,
    v2_issues: List[dict] = None,
    v2_stats: Dict[str, Any] = None,
    compare_issues: List[dict] = None,
    compare_stats: Dict[str, Any] = None,
    v1_file: str = "",
    v2_file: str = "",
) -> str:
    """生成审稿报告 Markdown。"""
    now = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

    if final_issues is None:
        final_issues = []
    if v2_issues is None:
        v2_issues = []
    if v2_stats is None:
        v2_stats = {}
    if compare_issues is None:
        compare_issues = []
    if compare_stats is None:
        compare_stats = {}

    # 统计终稿问题
    final_high = sum(1 for i in final_issues if i.get("severity") == "high")
    final_medium = sum(1 for i in final_issues if i.get("severity") == "medium")
    final_low = sum(1 for i in final_issues if i.get("severity") == "low")

    v2_high = sum(1 for i in v2_issues if i.get("severity") == "high")
    v2_medium = sum(1 for i in v2_issues if i.get("severity") == "medium")
    v2_low = sum(1 for i in v2_issues if i.get("severity") == "low")

    compare_high = sum(1 for i in compare_issues if i.get("severity") == "high")
    compare_medium = sum(1 for i in compare_issues if i.get("severity") == "medium")

    mode_label = "双稿" if v2_file else "单稿"

    lines = [
        f"# 日报审稿报告｜{stats.get('date', 'unknown')}",
        "",
        f"**审稿时间**: {now}",
        f"**审稿模式**: {mode_label}",
        f"**审稿结论**: {'✅ 可发送' if sendable else '⚠️ 待人工复核'}",
        f"**终稿校验**: {'✅ 通过' if validation_passed else '❌ 未通过'}",
        f"**使用模型**: {model_used}{' (fallback)' if fallback_used else ''}",
        "",
        "---",
        "",
        "## 审稿统计",
        "",
        f"| 指标 | V1初稿 | V2重构 | 终稿 |",
        f"|------|--------|--------|------|",
        f"| 问题总数 | {len(issues)} | {len(v2_issues)} | {len(final_issues)} |",
        f"| 严重问题 | {draft_high_severity_count} | {v2_high} | {final_high} |",
        f"| 中等问题 | {draft_medium_severity_count} | {v2_medium} | {final_medium} |",
        f"| 轻微问题 | {draft_low_severity_count} | {v2_low} | {final_low} |",
        f"| 字符数 | {stats.get('char_count', '?')} | {v2_stats.get('v2_char_count', '?')} | {stats.get('final_char_count', '?')} |",
        f"| 中文字符 | {stats.get('chinese_char_count', '?')} | {v2_stats.get('v2_chinese_chars', '?')} | {stats.get('final_chinese_char_count', '?')} |",
    ]

    if v2_file:
        lines.extend([
            f"| 事件ID数 | {stats.get('event_id_count', '?')} | {v2_stats.get('v2_event_id_count', '?')} | {stats.get('final_event_id_count', '?')} |",
        ])
    else:
        lines.extend([
            f"| 事件ID数 | {stats.get('event_id_count', '?')} | — | {stats.get('final_event_id_count', '?')} |",
        ])

    lines.extend([
        "",
        "---",
        "",
        "## V1初稿问题（Step 1 + Step 2）",
        "",
    ])

    if not issues:
        lines.append("无问题。✅")
    else:
        for i, issue in enumerate(issues, 1):
            sev = issue.get("severity", "low")
            sev_icon = "🔴" if sev == "high" else ("🟡" if sev == "medium" else "🟢")
            lines.append(f"{i}. {sev_icon} **[{issue.get('type', '?')}]** {issue.get('description', '')}")
            if issue.get("fix"):
                lines.append(f"   - 修复: {issue['fix']}")
            lines.append("")

    # V2问题（双稿模式）
    if v2_file:
        lines.extend([
            "---",
            "",
            "## V2重构稿问题",
            "",
        ])
        if not v2_issues:
            lines.append("无问题。✅")
        else:
            for i, issue in enumerate(v2_issues, 1):
                sev = issue.get("severity", "low")
                sev_icon = "🔴" if sev == "high" else ("🟡" if sev == "medium" else "🟢")
                lines.append(f"{i}. {sev_icon} **[{issue.get('type', '?')}]** {issue.get('description', '')}")
                if issue.get("fix"):
                    lines.append(f"   - 修复: {issue['fix']}")
                lines.append("")

        # 两稿比较
        lines.extend([
            "---",
            "",
            "## 两稿比较",
            "",
        ])
        if not compare_issues:
            lines.append("无严重差异。✅")
        else:
            for i, issue in enumerate(compare_issues, 1):
                sev = issue.get("severity", "low")
                sev_icon = "🔴" if sev == "high" else ("🟡" if sev == "medium" else "🟢")
                lines.append(f"{i}. {sev_icon} **[{issue.get('type', '?')}]** {issue.get('description', '')}")
                if issue.get("fix"):
                    lines.append(f"   - 修复: {issue['fix']}")
                lines.append("")
        if compare_stats:
            lines.extend([
                f"- V1事件ID数: {compare_stats.get('v1_event_id_count', '?')}",
                f"- V2事件ID数: {compare_stats.get('v2_event_id_count', '?')}",
                f"- 仅V1包含: {', '.join(compare_stats.get('ids_only_in_v1', [])) or '无'}",
                f"- 仅V2包含: {', '.join(compare_stats.get('ids_only_in_v2', [])) or '无'}",
            ])

    # 终稿问题
    lines.extend([
        "---",
        "",
        "## 终稿复检（Step 3）",
        "",
    ])

    if not final_issues:
        lines.append("终稿无问题。✅")
    else:
        for i, issue in enumerate(final_issues, 1):
            sev = issue.get("severity", "low")
            sev_icon = "🔴" if sev == "high" else ("🟡" if sev == "medium" else "🟢")
            desc = issue.get("description", "")
            fix_text = ""
            if issue.get("fix"):
                fix_text = f"\n   - 修复: {issue['fix']}"
            lines.append(f"{i}. {sev_icon} **[{issue.get('type', '?')}]** {desc}{fix_text}")
        lines.append("")

    # 合规检查
    lines.extend([
        "---",
        "",
        "## 合规检查",
        "",
        f"- 是否存在无证据判断: {'❌ 是' if stats.get('has_unsourced_claims', False) else '✅ 否'}",
        f"- 是否存在低置信强结论(终稿): {'❌ 是' if any(i.get('type') == 'low_confidence_strong_claim' for i in final_issues) else '✅ 否'}",
        f"- 是否存在rule_fallback强结论(终稿): {'❌ 是' if any(i.get('type') == 'rule_fallback_strong_claim' for i in final_issues) else '✅ 否'}",
    ])

    valid_actions = find_valid_unique_action_events(events)
    if valid_actions:
        va = valid_actions[0]
        lines.append(f"- 今日唯一建议动作是否合规: ✅ 是 ({va.get('event_id')} - {va.get('event_title', '')[:40]})")
    else:
        lines.append("- 今日唯一建议动作是否合规: ⚠️ 无合规事件")

    lines.extend([
        "",
        "---",
        "",
        "## 文件路径",
        "",
        f"- V1初稿: `{v1_file or draft_file}`",
    ])
    if v2_file:
        lines.append(f"- V2重构稿: `{v2_file}`")
    lines.extend([
        f"- 事件池: `{events_file}`",
        f"- 审稿报告: `{review_file}`",
        f"- 终稿: `{final_file}`",
        "",
        f"*审稿报告由 Watsons Retail Intel 系统自动生成。*",
    ])

    return "\n".join(lines)


# ===================== 自动补全 11 节 =====================

def _build_tracking_section_items(events: List[dict]) -> str:
    """从事件池中提取 tracking/ongoing 事件，构建 11 节条目。

    每个条目格式：
    - **标题** [event_id]\\n  事实摘要。持续关注xxx。
    """
    items = []
    seen = set()
    for ev in events:
        eid = ev.get("event_id", "")
        if eid in seen:
            continue
        novelty = ev.get("novelty_status", "")
        eligibility = ev.get("report_eligibility", "")
        if novelty != "ongoing" and eligibility != "tracking":
            continue
        seen.add(eid)

        title = ev.get("event_title", ev.get("title", ""))
        fact = ev.get("fact", ev.get("raw_fact", ""))
        if len(fact) > 120:
            fact = fact[:120] + "..."

        # 基于事实推断关注方向
        follow_up = "持续关注最新进展"
        fact_lower = fact.lower()
        if "骑手" in fact_lower or "接单" in fact_lower:
            follow_up = "持续关注各平台履约时效变化及对美妆品类的实际影响"
        elif "场景" in fact_lower or "闪购" in fact_lower:
            follow_up = "跟踪美妆个护品类在场景化运营中的资源位和转化表现"
        elif "抖音" in fact_lower or "小时达" in fact_lower or "涨粉" in fact_lower or "vlog" in fact_lower:
            follow_up = "持续观察内容电商流量变化对即时零售的影响"

        item = f"- **{title}** [{eid}]\n  {fact}。{follow_up}。"
        items.append(item)

    if not items:
        return "暂无近期延续观察事件。"

    return "\n\n".join(items)


# ===================== 主函数 =====================

def editor_review(
    project_root: str,
    date: str,
    draft_file: Optional[str] = None,
    events_file: Optional[str] = None,
    review_file: Optional[str] = None,
    final_file: Optional[str] = None,
    use_llm: bool = True,
    draft_v1_file: Optional[str] = None,
    draft_v2_file: Optional[str] = None,
) -> dict:
    """日报审稿主函数。支持单稿和双稿审稿。

    - 如果 draft_v2_file 存在且可读，则执行双稿审稿
    - 否则回退到单稿审稿（使用 draft_file）
    """
    errors: List[str] = []

    # ── 路径 ──
    if not draft_file:
        draft_file = resolve_path(project_root, f"data/drafts/{date}/daily_report_draft.md")
    if not draft_v1_file:
        draft_v1_file = resolve_path(project_root, f"data/drafts/{date}/daily_report_draft_v1.md")
    if not draft_v2_file:
        draft_v2_file = resolve_path(project_root, f"data/drafts/{date}/daily_report_draft_v2.md")
    if not events_file:
        # 优先使用 novelty 版本 (含 report_eligibility + novelty_status)
        novelty_path = resolve_path(project_root, f"data/events/{date}/events_scored_novelty.json")
        if os.path.exists(novelty_path):
            events_file = novelty_path
        else:
            events_file = resolve_path(project_root, f"data/events/{date}/events_analyzed.json")
    if not review_file:
        review_file = resolve_path(project_root, f"data/reviews/{date}/editor_review.md")
    if not final_file:
        year, month = date.split("-")[0], date.split("-")[1]
        final_file = resolve_path(project_root, f"reports/daily/{year}/{month}/{date}.md")

    log_dir = resolve_path(project_root, f"data/logs/{date}")
    os.makedirs(os.path.dirname(review_file), exist_ok=True)
    os.makedirs(os.path.dirname(final_file), exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "editor_review.log")

    # ── 日志 ──
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]   %(message)s"))
    logger.addHandler(fh)

    try:
        logger.info("=" * 60)
        logger.info(f"开始审稿: date={date}")
        logger.info(f"  draft_file: {draft_file}")
        logger.info(f"  events_file: {events_file}")

        # ── 加载数据 ──
        # 检测双稿模式
        dual_mode = False
        v2_text = None
        v2_stats = {}
        v2_issues_list = []
        compare_issues_list = []
        compare_stats_dict = {}

        # 优先使用 v1 文件，回退到兼容文件
        v1_path = draft_v1_file if os.path.exists(draft_v1_file) else draft_file
        if not os.path.exists(v1_path):
            error_msg = f"初稿文件不存在: {v1_path}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {"ok": False, "date": date, "errors": errors}

        with open(v1_path, "r", encoding="utf-8") as f:
            draft = f.read()

        logger.info(f"加载 V1 初稿: {v1_path} ({len(draft)} 字符)")

        # 检查 V2 是否存在
        if os.path.exists(draft_v2_file):
            with open(draft_v2_file, "r", encoding="utf-8") as f:
                v2_text = f.read()
            if v2_text.strip():
                dual_mode = True
                logger.info(f"检测到 V2 重构稿: {draft_v2_file} ({len(v2_text)} 字符)")
            else:
                logger.info("V2 文件为空，回退到单稿模式")
        else:
            logger.info("V2 文件不存在，回退到单稿模式")

        if not os.path.exists(events_file):
            error_msg = f"事件文件不存在: {events_file}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {"ok": False, "date": date, "errors": errors}

        with open(events_file, "r", encoding="utf-8") as f:
            events_data = json.load(f)

        all_events = events_data.get("events", [])
        event_ids = {ev.get("event_id", "") for ev in all_events if ev.get("event_id")}

        logger.info(f"加载初稿: {len(draft)} 字符")
        logger.info(f"加载事件: {len(all_events)} 条")

        # ═══════════════════════════════════════════
        # Step 1 & 2: 规则校验 + LLM 审稿 并行
        # ═══════════════════════════════════════════
        from concurrent.futures import ThreadPoolExecutor, as_completed

        final_md = draft
        # dual_mode 下优先用 V2 作为基础（更完整）
        if dual_mode and v2_text:
            final_md = v2_text
        llm_review_data = None
        model_used = "rule_compress"
        fallback_used = False

        # Prepare LLM client upfront
        llm_client = None
        if use_llm:
            try:
                llm_client = get_llm_client()
            except Exception as e:
                logger.warning(f"LLM 客户端初始化失败: {e}")

        if llm_client and llm_client.available:
            # ── 模型路由 ──
            _review_model = None
            _review_fallback = None
            try:
                _review_model, _review_fallback = get_model_for_skill("editor_review")
                logger.info(f"模型路由: editor_review 默认={_review_model}, "
                            f"fallback={_review_fallback}")
            except Exception as e:
                logger.warning(f"模型路由加载失败: {e}，使用默认模型")

            # ── 并行执行：规则校验 + LLM 审稿 ──
            logger.info("Step 1+2: 并行规则校验 + LLM 审稿...")
            _ed_futures = {}
            with ThreadPoolExecutor(max_workers=2) as _ed_executor:
                _ed_futures[_ed_executor.submit(
                    rule_validate, draft, all_events, event_ids
                )] = "rule"
                _ed_futures[_ed_executor.submit(
                    llm_review, draft, all_events, date, llm_client, model=_review_model
                )] = "llm"

                for _future in as_completed(_ed_futures):
                    _label = _ed_futures[_future]
                    if _label == "rule":
                        try:
                            issues, stats = _future.result(timeout=30)
                            stats["date"] = date
                            logger.info(f"  V1初稿发现 {len(issues)} 个问题")
                        except Exception as e:
                            logger.error(f"规则校验失败: {e}")
                            issues, stats = [], {"date": date, "issue_count": 0}
                    else:
                        try:
                            llm_result = _future.result(timeout=180)
                            if llm_result and "final_markdown" in llm_result:
                                llm_review_data = llm_result.get("review", {})
                                final_md = llm_result["final_markdown"]
                                model_used = _review_model or "LongCat-Flash-Chat"
                                logger.info(f"  LLM 审稿成功，终稿长度: {len(final_md)} 字符")
                            else:
                                logger.warning("默认模型审稿失败，尝试 fallback...")
                                if _review_fallback:
                                    fb_model = _review_fallback[0] if isinstance(_review_fallback, (list, tuple)) else _review_fallback
                                    logger.info(f"  Fallback 模型: {fb_model}")
                                    llm_result_fb = llm_review(
                                        draft, all_events, date, llm_client, model=fb_model)
                                    if llm_result_fb and "final_markdown" in llm_result_fb:
                                        llm_review_data = llm_result_fb.get("review", {})
                                        final_md = llm_result_fb["final_markdown"]
                                        model_used = f"{fb_model} (fallback)"
                                        fallback_used = True
                                        logger.info("  Fallback 审稿成功")
                        except Exception as e:
                            logger.warning(f"LLM 审稿失败: {e}")

            draft_high_severity_count = stats.get("high_severity_count", 0)
            draft_medium_severity_count = stats.get("medium_severity_count", 0)
            draft_low_severity_count = stats.get("low_severity_count", 0)
            for issue in issues:
                sev = issue.get("severity", "low")
                logger.info(f"  [{sev}] {issue.get('type')}: {issue.get('description', '')[:80]}")

            # ── 合并 LLM 审稿问题 ──
            if llm_review_data:
                llm_issues = llm_review_data.get("issues", [])
                for li in llm_issues:
                    # 兼容 LLM 返回字符串或 dict 两种格式
                    if isinstance(li, str):
                        li = {"type": "style_issue", "severity": "low", "description": li}
                    if not any(
                        i.get("description", "")[:40] == li.get("description", "")[:40]
                        for i in issues
                    ):
                        issues.append(li)
                stats["issue_count"] = len(issues)

            # 更新 stats
            stats["high_severity_count"] = sum(1 for i in issues if i.get("severity") == "high")
            stats["medium_severity_count"] = sum(1 for i in issues if i.get("severity") == "medium")
            stats["low_severity_count"] = sum(1 for i in issues if i.get("severity") == "low")

        else:
            # 无 LLM：仅规则校验 + 规则压缩
            logger.info("Step 1: 规则校验（无LLM）...")
            issues, stats = rule_validate(draft, all_events, event_ids)
            stats["date"] = date
            draft_high_severity_count = stats.get("high_severity_count", 0)
            draft_medium_severity_count = stats.get("medium_severity_count", 0)
            draft_low_severity_count = stats.get("low_severity_count", 0)
            logger.info(f"  V1初稿发现 {len(issues)} 个问题 "
                        f"(高={draft_high_severity_count}, "
                        f"中={draft_medium_severity_count}, "
                        f"低={draft_low_severity_count})")
            for issue in issues:
                sev = issue.get("severity", "low")
                logger.info(f"  [{sev}] {issue.get('type')}: {issue.get('description', '')[:80]}")

            if llm_client and not llm_client.available:
                logger.warning("LLM 不可用，使用规则压缩")
            # dual_mode 下优先用 V2（更完整），否则用 V1
            base_draft = v2_text if dual_mode and v2_text else draft
            final_md = rule_compress(base_draft, all_events)
            model_used = "rule_compress"

        # ═══════════════════════════════════════════
        # Step 3: 终稿校验
        # ═══════════════════════════════════════════
        logger.info("Step 3: 终稿校验...")
        validation_passed, final_issues = final_validate(
            final_md, all_events, event_ids)

        final_high = sum(1 for i in final_issues if i.get("severity") == "high")
        final_medium = sum(1 for i in final_issues if i.get("severity") == "medium")
        final_low = sum(1 for i in final_issues if i.get("severity") == "low")

        if final_issues:
            logger.warning(f"终稿校验问题 ({len(final_issues)}): 高={final_high}, 中={final_medium}, 低={final_low}")
            for fi in final_issues:
                logger.warning(f"  [{fi.get('severity')}] {fi.get('type')}: {fi.get('description', '')[:80]}")
        else:
            logger.info("终稿校验通过，0个问题")

        # 如果校验失败（有结构性high问题），尝试规则修复
        fixed_issue_count = 0
        if not validation_passed:
            logger.info("终稿有结构性问题，尝试规则修复...")
            final_md = rule_compress(final_md, all_events)
            fixed_issue_count = len(final_issues)

            # 再次校验
            validation_passed, final_issues = final_validate(
                final_md, all_events, event_ids)
            final_high = sum(1 for i in final_issues if i.get("severity") == "high")
            final_medium = sum(1 for i in final_issues if i.get("severity") == "medium")
            final_low = sum(1 for i in final_issues if i.get("severity") == "low")
            logger.info(f"规则修复后再校验: passed={validation_passed}, "
                        f"高={final_high}, 中={final_medium}, 低={final_low}")

        # ── 自动修复：tracking 事件存在但终稿缺少 11 节时，自动追加 ──
        has_ongoing_or_tracking_final = any(
            ev.get("novelty_status") == "ongoing"
            or ev.get("report_eligibility") == "tracking"
            for ev in all_events
        )
        if has_ongoing_or_tracking_final and "11 近期延续观察" not in final_md:
            logger.info("检测到 tracking 事件但终稿缺少 11 节，自动追加...")
            tracking_items = _build_tracking_section_items(all_events)
            section_11 = (
                "\n\n---\n\n"
                "## 11 近期延续观察\n\n"
                "以下事件在此前日期已出现，持续跟踪中：\n\n"
                + tracking_items
            )
            final_md = final_md.rstrip() + section_11
            # 复检
            validation_passed, final_issues = final_validate(
                final_md, all_events, event_ids)
            final_high = sum(1 for i in final_issues if i.get("severity") == "high")
            final_medium = sum(1 for i in final_issues if i.get("severity") == "medium")
            final_low = sum(1 for i in final_issues if i.get("severity") == "low")
            logger.info(f"追加 11 节后复检: passed={validation_passed}, "
                        f"高={final_high}, 中={final_medium}, 低={final_low}")

        # ═══════════════════════════════════════════
        # 判断是否可发送
        # 只看终稿复检(final_issues)的结果，不看初稿问题(issues)
        # ═══════════════════════════════════════════
        sendable = validation_passed and final_high == 0

        # 不可发送时在终稿开头标注
        if not sendable:
            final_md = "> **【待人工复核】** 此终稿存在未通过自动审稿的问题，请编辑人工复核后再发送。\n\n" + final_md.lstrip("> ")

        logger.info(f"审稿结论: sendable={sendable}, validation_passed={validation_passed}, "
                    f"final_high={final_high}, final_medium={final_medium}, final_low={final_low}")

        # ── 更新统计 ──
        stats["final_char_count"] = count_all_chars_no_space(final_md)
        stats["final_chinese_char_count"] = count_chinese_chars(final_md)
        stats["final_event_id_count"] = len(set(re.findall(r'E\d{8}_\d{4}', final_md)))
        stats["has_unsourced_claims"] = any(
            i.get("type") == "unsupported_claim" for i in issues)
        # 终稿统计
        stats["final_high_severity_count"] = final_high
        stats["final_medium_severity_count"] = final_medium
        stats["final_low_severity_count"] = final_low

        # ── 写出终稿（清理展示版本） ──
        # 保留 event_id 统计后再清理
        display_md = _clean_report_for_display(final_md)
        with open(final_file, "w", encoding="utf-8") as f:
            f.write(display_md)
        logger.info(f"终稿已写入（已清理内部标记）: {final_file}")

        # ── 生成审稿报告 ──
        review_md = generate_review_report(
            issues=issues,
            stats=stats,
            events=all_events,
            sendable=sendable,
            validation_passed=validation_passed,
            model_used=model_used,
            fallback_used=fallback_used,
            draft_file=v1_path,
            events_file=events_file,
            review_file=review_file,
            final_file=final_file,
            final_issues=final_issues,
            draft_high_severity_count=draft_high_severity_count,
            draft_medium_severity_count=draft_medium_severity_count,
            draft_low_severity_count=draft_low_severity_count,
            v2_issues=v2_issues_list if dual_mode else [],
            v2_stats=v2_stats if dual_mode else {},
            compare_issues=compare_issues_list if dual_mode else [],
            compare_stats=compare_stats_dict if dual_mode else {},
            v1_file=v1_path,
            v2_file=draft_v2_file if dual_mode else "",
        )

        with open(review_file, "w", encoding="utf-8") as f:
            f.write(review_md)
        logger.info(f"审稿报告已写入: {review_file}")

        # ── 返回结果 ──
        result = {
            "ok": True,
            "date": date,
            "draft_file": v1_path,
            "draft_v1_file": v1_path,
            "draft_v2_file": draft_v2_file if dual_mode else "",
            "dual_mode": dual_mode,
            "events_file": events_file,
            "review_file": review_file,
            "final_file": final_file,
            "log_file": log_file,
            "validation_passed": validation_passed,
            # V1初稿问题（Step 1+2）
            "draft_issue_count": len(issues),
            "draft_high_severity_count": draft_high_severity_count,
            "draft_medium_severity_count": draft_medium_severity_count,
            "draft_low_severity_count": draft_low_severity_count,
            # V2问题
            "v2_issue_count": len(v2_issues_list) if dual_mode else 0,
            "v2_high_severity_count": v2_stats.get("v2_high_count", 0) if dual_mode else 0,
            "compare_issue_count": len(compare_issues_list) if dual_mode else 0,
            # 终稿问题（Step 3）
            "final_issue_count": len(final_issues),
            "final_high_severity_count": final_high,
            "final_medium_severity_count": final_medium,
            "final_low_severity_count": final_low,
            "fixed_issue_count": fixed_issue_count,
            "sendable": sendable,
            "model_used": model_used,
            "fallback_used": fallback_used,
            "draft_length": len(draft),
            "v1_length": len(draft),
            "v2_length": len(v2_text) if dual_mode and v2_text else 0,
            "final_length": len(final_md),
            "final_char_count": stats.get("final_char_count", 0),
            "final_chinese_char_count": stats.get("final_chinese_char_count", 0),
            "final_event_id_count": stats.get("final_event_id_count", 0),
            "errors": errors,
        }

        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    except Exception as e:
        error_msg = f"审稿失败: {e}"
        logger.error(error_msg, exc_info=True)
        errors.append(error_msg)
        return {"ok": False, "date": date, "errors": errors}

    finally:
        logger.removeHandler(fh)
        fh.close()


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="日报审稿技能")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 (YYYY-MM-DD)")
    parser.add_argument("--draft-file", default=None, help="初稿文件路径(兼容)")
    parser.add_argument("--draft-v1-file", default=None, help="V1初稿文件路径")
    parser.add_argument("--draft-v2-file", default=None, help="V2重构稿文件路径")
    parser.add_argument("--events-file", default=None, help="事件分析文件路径")
    parser.add_argument("--review-file", default=None, help="审稿报告输出路径")
    parser.add_argument("--final-file", default=None, help="终稿输出路径")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用LLM审稿 (true/false)")
    parser.add_argument("--test-llm", action="store_true",
                        help="测试 LLM 连接")

    args = parser.parse_args()

    if args.test_llm:
        if not _LLM_AVAILABLE:
            print("❌ llm_client 不可用")
            sys.exit(1)
        result = test_llm_connection()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result.get("ok") else 1)

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = editor_review(
        project_root=args.project_root,
        date=args.date,
        draft_file=args.draft_file,
        events_file=args.events_file,
        review_file=args.review_file,
        final_file=args.final_file,
        use_llm=use_llm,
        draft_v1_file=args.draft_v1_file,
        draft_v2_file=args.draft_v2_file,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()