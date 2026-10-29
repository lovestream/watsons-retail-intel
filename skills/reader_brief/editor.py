#!/usr/bin/env python3
"""editor.py — 经营编辑层：可信事实包 → 结构化简报 JSON。

关键点（docs/15）：
- 编辑层只拿到事实包（含 fact id / 日期 / 状态 / 链接），不拿到长报告，
  因此不会把上一版报告里的判断当成"事实"复述；
- 输出必须是 JSON，且每条判断都要挂 fact_ids；没有事实支撑的判断不允许出现；
- 三种周期用不同的编辑骨架，而不是同一模板换标题。
"""

import json
import logging
from typing import Dict, List, Tuple

from skills.reader_brief.facts_pack import compact_pack_for_llm

logger = logging.getLogger(__name__)

READER_PROFILE = (
    "读者是屈臣氏中国线上业务（电商 + 即时零售）的经营负责人。"
    "他要回答三个问题：这周/这月市场发生了什么真实变化；这对屈臣氏意味着什么；"
    "我下周/下月能做什么具体动作。他不看内部评分、不看事件编号、不看附录。"
)

SKELETONS = {
    "weekly": {
        "sections": ["signals", "competitors", "actions", "watch"],
        "caps": {"signals": 3, "competitors": 3, "actions": 3, "watch": 2},
        "guide": (
            "周报骨架：\n"
            "- lead：一句话说清本周最重要的变化（≤90 字），必须可被 fact 支撑；\n"
            "- signals：3 条变化。每条 title（≤26 字）+ what_changed（≤80 字，"
            "写事实与状态）+ why_it_matters（≤80 字，写对屈臣氏即时零售的具体影响，"
            "不要空泛套话）；\n"
            "- competitors：2-3 个竞对动作卡。name / move（≤60 字）/ implication（≤60 字）；\n"
            "- actions：3 条本周可执行动作。action（≤70 字，含可观察指标）/ horizon"
            "（如“本周内”“双11首周”）；不要写未在事实中出现的预算、折扣幅度、上线时间；\n"
            "- watch：2 条下周要看什么，说明看什么指标、为什么。"
        ),
    },
    "monthly": {
        "sections": ["trends", "tradeoffs", "actions", "watch"],
        "caps": {"trends": 4, "tradeoffs": 3, "actions": 3, "watch": 2},
        "guide": (
            "月报骨架：\n"
            "- lead：本月最值得记住的一个结构性变化（≤110 字）；\n"
            "- trends：3-4 条持续变化。title / evidence（≤90 字，事实+状态）/"
            " meaning（≤90 字，对经营的含义）；只写跨多日/多来源能看出的趋势，"
            "单日爆点不要当月度趋势；\n"
            "- tradeoffs：2-3 条战略取舍。choice（≤70 字，例如“把补贴预算从全品类"
            "转到美妆个护的即时履约”要写成取舍逻辑而不是指令）/ tension（≤70 字，"
            "写放弃什么、风险是什么）；\n"
            "- actions：3 条下月动作，必须能在不追加预算的前提下先做验证；\n"
            "- watch：2 条下月要跟踪的指标或对手动作。"
        ),
    },
    "yearly": {
        "sections": ["structure", "decisions", "actions", "watch"],
        "caps": {"structure": 3, "decisions": 3, "actions": 2, "watch": 2},
        "guide": (
            "年报骨架：\n"
            "- lead：年度判断，一句话说清即时零售/个护美妆渠道结构的变化（≤120 字）；\n"
            "- structure：3 条长期结构变化（渠道权力、履约半径、品类集中度、价格机制），"
            "每条要指出是哪一年的证据支撑；\n"
            "- decisions：3 条年度级决策建议，写清“今年必须定的一件事 + 不定的代价”；\n"
            "- actions：2 条明年 Q1 就能启动的动作；\n"
            "- watch：2 条长期观察项。"
        ),
    },
}

JSON_SPEC = """{
  "lead": "一句话结论",
  "signals": [{"title": "", "what_changed": "", "why_it_matters": "", "fact_ids": ["F001"]}],
  "competitors": [{"name": "", "move": "", "implication": "", "fact_ids": ["F002"]}],
  "trends": [{"title": "", "evidence": "", "meaning": "", "fact_ids": ["F003"]}],
  "tradeoffs": [{"choice": "", "tension": "", "fact_ids": ["F004"]}],
  "structure": [{"title": "", "detail": "", "fact_ids": ["F005"]}],
  "decisions": [{"decision": "", "cost_of_waiting": "", "fact_ids": ["F006"]}],
  "actions": [{"action": "", "horizon": "", "fact_ids": ["F001"]}],
  "watch": [{"item": "", "metric": "", "fact_ids": ["F001"]}]
}"""

HARD_RULES = """硬性规则：
1. 只输出 JSON，不要 Markdown、不要解释、不要前言后语。
2. 每个条目都必须带 fact_ids，且只能引用事实包里出现的 id（如 F001）。
3. 任何数字（成交额、门店数、增速、比例、时间点）都必须能在所引用的 fact 原文中找到；
   找不到就不要写数字，改成定性表述。
4. 事实的 status 决定写法：completed→“已…”，announced→“宣布/公布”，
   proposed→“拟/计划”，reported→“据媒体报道/据报道”。不得把 proposed 写成已落地。
5. 不写内部信息：不要出现事件编号（E2026…）、评分、优先级、置信度、附录、证据清单。
6. 不写无法验证的经营指令：不要出现具体折扣力度、预算金额、上线日期、限购规则，
   除非事实里明确写了。动作要写成“可先验证的下一步”，而不是“已决定执行”。
7. 不写已经过期的期限（今天是 {today}）。horizon 用“本周内/下周/本月/下月/Q1”等相对表述。
8. 不要套话（“赋能/闭环/抓手/全面提升”），每句话都要有主语和具体对象。
"""


def build_prompt(pack: Dict, period_type: str, today: str) -> Tuple[str, str]:
    sk = SKELETONS[period_type]
    system = (
        "你是一位零售经营编辑，为屈臣氏线上业务负责人写内部经营简报。"
        "你只依据给定的事实包写作，不允许引入外部知识，不允许编造数字。"
        + READER_PROFILE
    )
    compact = compact_pack_for_llm(pack) if isinstance(pack, dict) and "facts" in pack \
        else compact_pack_for_llm({"facts": pack.get("facts", [])})
    user = (
        f"周期类型：{period_type}\n"
        f"周期范围：{json.dumps(pack.get('period', {}), ensure_ascii=False)}\n\n"
        f"{sk['guide']}\n\n"
        f"输出 JSON 结构（只使用本周期需要的键）：\n{JSON_SPEC}\n\n"
        + HARD_RULES.replace("{today}", today)
        + "\n事实包（唯一事实来源）：\n"
        + json.dumps(compact, ensure_ascii=False, indent=1)
    )
    return system, user


def _siliconflow_chat(system: str, user: str) -> Dict:
    """文本大模型兜底（SiliconFlow DeepSeek，非语音；不涉及任何 TTS 费用）。

    OpenCode 免费池偶发 "Model is unavailable" 时使用。仅文本。
    """
    import os
    import requests

    key = os.environ.get("siliconflow_api_key") or os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        raise RuntimeError("siliconflow_api_key 未配置")
    payload = {
        "model": os.environ.get("READER_BRIEF_TEXT_MODEL",
                                "deepseek-ai/DeepSeek-V4.1-Flash"),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    r = requests.post("https://api.siliconflow.cn/v1/chat/completions",
                      headers={"Authorization": "Bearer " + key,
                               "Content-Type": "application/json"},
                      json=payload, timeout=300)
    if r.status_code >= 300:
        raise RuntimeError(f"SiliconFlow 文本 LLM HTTP {r.status_code}: {r.text[:160]}")
    data = r.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return {"ok": bool(content.strip()), "content": content,
            "provider": "siliconflow_text", "model": payload["model"],
            "error": "" if content.strip() else "empty content"}


def compose(pack: Dict, period_type: str, today: str,
            max_attempts: int = 2) -> Dict:
    """生成结构化简报：先走统一 LLM 池，失败再走文本兜底；都失败才抛异常。"""
    from skills.utils.llm_client import get_llm_client

    if period_type not in SKELETONS:
        raise ValueError(f"unknown period_type: {period_type}")

    system, user = build_prompt(pack, period_type, today)
    last_err = ""

    def _try(res: Dict, provider: str, attempt: int):
        if not res.get("ok"):
            return None
        brief = res.get("parsed") or _parse_json(res.get("content", ""))
        if isinstance(brief, dict) and brief.get("lead"):
            brief["_meta"] = {"editor_mode": "llm",
                              "provider": res.get("provider") or provider,
                              "model": res.get("model"),
                              "attempt": attempt}
            return brief
        return None

    client = get_llm_client()
    for attempt in range(1, max_attempts + 1):
        res = client.chat(
            messages=[{"role": "user", "content": user}],
            system_prompt=system,
            response_format="json",
            temperature=0.3,
            max_tokens=4096,
        )
        brief = _try(res, "opencode_pool", attempt)
        if brief:
            return brief
        last_err = str(res.get("error"))[:200]
        logger.warning("editor 主池失败 attempt=%s: %s", attempt, last_err)

    for attempt in range(1, max_attempts + 1):
        try:
            res = _siliconflow_chat(system, user)
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:200]
            logger.warning("editor 文本兜底失败 attempt=%s: %s", attempt, last_err)
            continue
        brief = _try(res, "siliconflow_text", attempt)
        if brief:
            logger.info("editor 使用文本兜底 provider=%s", brief["_meta"]["provider"])
            return brief
        last_err = "兜底返回无法解析为 JSON 或缺少 lead"

    raise RuntimeError(f"编辑层生成失败: {last_err}")


def _parse_json(text: str):
    from skills.utils.llm_client import robust_json_extract
    try:
        return json.loads(text)
    except Exception:
        return robust_json_extract(text)


def fallback_brief(pack: Dict, period_type: str) -> Dict:
    """LLM 全挂时的确定性兜底：只陈述事实，不做判断，并显式标注降级。"""
    facts: List[Dict] = [f for f in pack.get("facts", []) if f.get("text")][:6]
    lead = facts[0]["text"] if facts else "本期无可核实事实。"
    brief = {"lead": lead[:120],
             "actions": [], "watch": [], "competitors": [],
             "signals": [], "trends": [], "tradeoffs": [],
             "structure": [], "decisions": [],
             "_meta": {"editor_mode": "fallback_rule",
                       "note": "LLM 不可用，仅陈述事实，不含判断"}}
    key = {"weekly": "signals", "monthly": "trends", "yearly": "structure"}[period_type]
    for f in facts:
        brief[key].append({"title": f["text"][:26],
                           "what_changed": f["text"][:80],
                           "why_it_matters": "",
                           "evidence": f["text"][:80],
                           "detail": f["text"][:80],
                           "fact_ids": [f["fact_id"]]})
    return brief


REVISE_SYSTEM = (
    "你是零售经营编辑的审稿人。你要按事实质量审查报告修改简报 JSON："
    "只修正被指出的问题，不要新增事实、不要新增数字、不要改变未被指出的条目。"
    "仍然只输出完整 JSON。"
)


def revise(brief: Dict, issues: List[Dict], pack: Dict, period_type: str,
           today: str) -> Dict:
    """把审查错误回灌给编辑层做定向修订（最多两轮）。"""
    from skills.utils.llm_client import get_llm_client

    compact = compact_pack_for_llm(pack)
    problem_lines = "\n".join(
        f"- {i['where']}: {i['code']} — {i['message']}"
        for i in issues if i.get("severity") == "error")
    body = {k: v for k, v in brief.items() if not k.startswith("_")}
    user = (
        f"{SKELETONS[period_type]['guide']}\n\n"
        f"审查发现以下问题（必须全部修掉）：\n{problem_lines}\n\n"
        + HARD_RULES.replace("{today}", today)
        + "\n待修改的简报 JSON：\n"
        + json.dumps(body, ensure_ascii=False, indent=1)
        + "\n\n事实包（唯一事实来源）：\n"
        + json.dumps(compact, ensure_ascii=False, indent=1)
    )
    last_err = ""
    res = get_llm_client().chat(messages=[{"role": "user", "content": user}],
                                system_prompt=REVISE_SYSTEM,
                                response_format="json", temperature=0.2,
                                max_tokens=4096)
    if res.get("ok"):
        fixed = res.get("parsed") or _parse_json(res.get("content", ""))
        if isinstance(fixed, dict) and fixed.get("lead"):
            fixed["_meta"] = dict(brief.get("_meta") or {})
            fixed["_meta"]["revised"] = True
            return fixed
        last_err = "修订结果无法解析"
    else:
        last_err = str(res.get("error"))[:160]

    try:
        res = _siliconflow_chat(REVISE_SYSTEM, user)
        fixed = _parse_json(res.get("content", ""))
        if isinstance(fixed, dict) and fixed.get("lead"):
            fixed["_meta"] = dict(brief.get("_meta") or {})
            fixed["_meta"].update({"revised": True, "provider": "siliconflow_text"})
            return fixed
    except Exception as e:  # noqa: BLE001
        last_err = str(e)[:160]

    logger.warning("修订失败，保留原稿: %s", last_err)
    return brief
