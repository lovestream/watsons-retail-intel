#!/usr/bin/env python3
"""generate_podcast — 从事件池直接生成口播稿并生成音频播客。

方案 C 架构：播客直接从 events_scored_novelty.json 生成，
与日报并行，互不依赖。

核心原则:
1. 直接读取事件池（含 business_analysis），不依赖日报文件
2. 独立选择 top 3-5 个核心事件做深度分析
3. 口播稿控制在 3000-5000 中文字符（约 5-8 分钟）
4. 无信号时（0 个 core 事件）生成极简版口播稿
5. 语音使用 edge-tts
6. 音频生成失败不阻塞 pipeline
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 项目路径 ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ── 日志 ──
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("generate_podcast")

# ── 时区 ──
from datetime import timezone
CST = timezone(timedelta(hours=8))

try:
    from zoneinfo import ZoneInfo
    CST = ZoneInfo("Asia/Shanghai")
except ImportError:
    try:
        from dateutil.tz import gettz
        CST = gettz("Asia/Shanghai")
    except ImportError:
        pass

LOG_PREFIX = "[Podcast]"

# ── 中文字符统计 ──
CJK_RANGE = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff'
    r'\u2e80-\u2eff\u31c0-\u31ef\u3200-\u32ff\u3300-\u33ff]')


def count_chinese_chars(text: str) -> int:
    return len(CJK_RANGE.findall(text))


# ===================== 事件选择 =====================

def select_podcast_events(events: List[dict], max_signals: int = 5) -> List[dict]:
    """从事件池中选择播客要深度分析的事件。

    优先级：
    1. report_eligibility=core + novelty_status=new_today
    2. priority=P1 > P2
    3. action_level=immediate > test > watch
    4. weighted_score 降序
    """
    # 排除 ARCHIVE 和 archive
    candidates = [e for e in events
                  if e.get("priority") != "ARCHIVE"
                  and e.get("report_eligibility") not in ("archive", "reference")]

    # 优先 core + new_today
    core_new = [e for e in candidates
                if e.get("report_eligibility") == "core"
                and e.get("novelty_status") in ("new_today", "updated_today")]

    # 排序
    def sort_key(ev):
        p = {"P0": 0, "P1": 1, "P2": 2}.get(ev.get("priority", "P2"), 2)
        al = ev.get("business_analysis", {}).get("action_level", "watch")
        al_score = {"immediate": 0, "test": 1, "watch": 2}.get(al, 2)
        ws = -ev.get("weighted_score", 0)
        return (p, al_score, ws)

    core_new.sort(key=sort_key)

    # 如果 core_new 不够，从 tracking 补充
    if len(core_new) < max_signals:
        tracking = [e for e in candidates
                    if e.get("report_eligibility") == "tracking"
                    or (e.get("report_eligibility") == "core"
                        and e.get("novelty_status") == "ongoing")]
        tracking.sort(key=sort_key)
        core_new.extend(tracking)

    return core_new[:max_signals]


def format_event_for_llm(event: dict, index: int) -> str:
    """将单个事件格式化为 LLM 输入文本。"""
    ba = event.get("business_analysis", {})
    entities = event.get("entities", {})

    parts = [f"### 事件{index+1}: {event.get('event_title', '未知事件')}"]
    parts.append(f"- 类型: {event.get('event_type', '未知')}")
    parts.append(f"- 优先级: {event.get('priority', 'P2')}")
    parts.append(f"- 置信度: {event.get('confidence', 'medium')}")

    if event.get("fact"):
        parts.append(f"- 事实: {event['fact']}")
    if event.get("evidence_text"):
        parts.append(f"- 证据: {event['evidence_text'][:500]}")

    # 实体
    ent_parts = []
    for key in ("companies", "brands", "platforms", "people"):
        vals = entities.get(key, [])
        if vals:
            ent_parts.append(f"{key}: {', '.join(vals[:5])}")
    if ent_parts:
        parts.append(f"- 涉及: {'; '.join(ent_parts)}")

    # 经营分析
    if ba:
        if ba.get("watsons_impact"):
            parts.append(f"- 对屈臣氏影响: {ba['watsons_impact']}")
        if ba.get("recommended_action"):
            parts.append(f"- 建议动作: {ba['recommended_action']}")
        if ba.get("action_level"):
            parts.append(f"- 行动级别: {ba['action_level']}")
        if ba.get("tracking_metrics"):
            metrics = ba["tracking_metrics"]
            if isinstance(metrics, list):
                parts.append(f"- 追踪指标: {', '.join(metrics[:5])}")
        if ba.get("impact_type"):
            parts.append(f"- 影响类型: {ba['impact_type']}")
        if ba.get("affected_channels"):
            channels = ba["affected_channels"]
            if isinstance(channels, list):
                parts.append(f"- 影响渠道: {', '.join(channels[:5])}")

    return "\n".join(parts)


# ===================== LLM Prompt =====================

LLM_SYSTEM_PROMPT = """你是一位专业的零售行业播客主播，擅长将行业事件转化为适合通勤收听的口播稿。你的风格是业务顾问式的深度解读，不是新闻播报。

你的听众是屈臣氏的中高层管理者，他们在通勤路上听你的节目。他们需要的不是信息搬运，而是——
- 这件事对我的生意意味着什么？
- 我今天应该做什么？
- 竞争对手在做什么我还没做的？
- 有没有具体的数字、指标、行动项？

## 核心规则
1. **所有内容必须基于提供的事件数据**——不得编造事实、数据或事件
2. **口语化表达**——像一位资深零售顾问在跟你聊天，有观点、有判断、有建议
3. **严格控制在3000-5000中文字符之间**——低于2500字不合格
4. **去掉技术标注**——event_id、置信度标签一律不出现
5. **开头格式**：「早安，这里是即时零售×个护美妆经营日报，{date}。」
6. **结尾格式**：以「今天听完只做三件事」收尾，列出3条最具体的行动项

## 口播稿结构

### 第一段：核心判断（约150字）
- 今天最重要的一个结论是什么？为什么重要？

### 第二段~第四段：重点信号深度解读（每条约500-600字，共2-3条）
对最重要的事件，逐条做5维度分析：
1. 发生了什么（事实，简洁）
2. 为什么重要（行业意义）
3. 对屈臣氏意味着什么（经营影响，要具体）
4. 值得学习或警惕什么（启示/风险）
5. 今天要盯什么指标（可执行的追踪项）

### 第五段：平台与竞对动态速览（约400字）
- 其他事件中的平台变化，逐个点评

### 第六段：今天值得学习的一件商家动作（约300字）
- 从事件中挑选一个竞对或跨行业的具体动作
- 分析底层逻辑，屈臣氏可以怎么借鉴

### 第七段：机会点与建议动作（约300字）
- 对屈臣氏的具体建议，分商品/平台/营销/试点四个维度

### 第八段：风险提示与明日追踪（约200字）
- 需要警惕的风险 + 明天要追踪什么

### 结尾：今天听完只做三件事
- 列出3条最具体、最可执行的行动项

## 禁止事项
- 禁止添加事件数据中没有的数据、数字、事件
- 禁止使用"据报道""有消息称"等模糊来源
- 禁止出现 event_id、Markdown 符号
- 禁止空洞的套话（"值得关注""需要重视"必须跟具体内容）"""


LLM_USER_TEMPLATE = """以下是今天（{date}）的即时零售×个护美妆行业事件数据。请根据这些事件生成一份口播稿。

## 今日核心事件（共{event_count}条）

{events_text}

## 今日事件池概况
- 总事件数: {total_events}
- 核心事件(core): {core_count}
- 今日新增(new_today): {new_today_count}
- P1事件: {p1_count}

---

要求：
1. 严格3000-5000中文字符（低于2500不合格，请充分展开分析）
2. 对前2-3条核心事件做500-600字/条的5维度深度分析
3. 自然口语化，业务顾问风格
4. 所有分析必须基于上述事件数据，不得编造
5. 以「今天听完只做三件事」收尾"""


NO_SIGNAL_TEMPLATE = """早安，即时零售×个护美妆经营日报，{date}。

结论先行：今天没有高质量新增信号，不建议因外部信息调整策略。

数据层面：采集{total_events}条事件，经时效性和相关性双层筛选，无一条进入核心分析。

低置信信号比没信号更危险——与其强行解读，不如诚实说今天没信号。

明天见。"""


# ===================== LLM 生成 =====================

def llm_generate_podcast_script(
    events: List[dict],
    date: str,
    is_no_signal: bool = False,
    project_root: str = ".",
) -> Tuple[str, bool]:
    """用 LLM 从事件池直接生成口播稿。

    Returns:
        (script, llm_used)
    """
    from skills.utils.llm_client import LLMClient
    from skills.utils.model_router import get_model_for_skill

    root = Path(project_root).resolve()

    # 获取模型路由
    try:
        default_model, fallbacks = get_model_for_skill(
            "generate_podcast",
            config_path=str(root / "config" / "model_router.yaml"),
        )
    except Exception:
        default_model = "LongCat-Flash-Thinking"
        fallbacks = ["LongCat-Flash-Chat", "LongCat-Flash-Lite"]

    if default_model == "rule_only":
        logger.info(f"{LOG_PREFIX} 模型路由返回 rule_only，跳过 LLM")
        return "", False

    # 选择播客事件
    selected = select_podcast_events(events, max_signals=5)
    if not selected:
        return "", False

    # 格式化事件
    events_text = "\n\n".join(
        format_event_for_llm(ev, i) for i, ev in enumerate(selected)
    )

    # 统计
    total_events = len(events)
    core_count = sum(1 for e in events if e.get("report_eligibility") == "core")
    new_today_count = sum(1 for e in events if e.get("novelty_status") == "new_today")
    p1_count = sum(1 for e in events if e.get("priority") == "P1")

    # 构建 user content
    user_content = LLM_USER_TEMPLATE.format(
        date=date,
        event_count=len(selected),
        events_text=events_text,
        total_events=total_events,
        core_count=core_count,
        new_today_count=new_today_count,
        p1_count=p1_count,
    )

    system_prompt = LLM_SYSTEM_PROMPT.replace("{date}", date)

    # 尝试模型
    models_to_try = [default_model] + fallbacks
    last_error = ""
    min_chars_required = 2500

    for model in models_to_try:
        try:
            client = LLMClient()
            logger.info(f"{LOG_PREFIX} 使用模型: {model}")

            first_script = None
            first_cn_chars = 0

            for attempt in range(2):
                result = client.chat(
                    messages=[{"role": "user", "content": user_content}],
                    system_prompt=system_prompt,
                    model=model,
                    temperature=0.3 if attempt == 0 else 0.5,
                    max_tokens=8192,
                )

                if result.get("ok") and result.get("content", "").strip():
                    script = result["content"].strip()
                    script = re.sub(r'^```(?:markdown|md)?\s*\n?', '', script)
                    script = re.sub(r'\n?```\s*$', '', script)
                    cn_chars = count_chinese_chars(script)
                    logger.info(f"{LOG_PREFIX} LLM 生成成功，模型: {model}，"
                                f"中文字符数: {cn_chars} (attempt {attempt+1})")

                    if attempt == 0:
                        first_script = script
                        first_cn_chars = cn_chars

                    if cn_chars < min_chars_required and attempt == 0:
                        logger.warning(
                            f"{LOG_PREFIX} 口播稿过短({cn_chars}<{min_chars_required})，重试...")
                        user_content = (
                            f"上次生成只有{cn_chars}字，不合格。"
                            f"请重新生成，必须达到3000-5000中文字符。"
                            f"对每条核心事件做500-600字的深度分析。\n\n"
                            + user_content
                        )
                        continue

                    # 重试后选更长的
                    if attempt == 1 and first_script and first_cn_chars > cn_chars:
                        logger.info(f"{LOG_PREFIX} 重试更短，使用第一次结果({first_cn_chars}字)")
                        return first_script, True

                    return script, True
                else:
                    last_error = result.get("error", "unknown")
                    logger.warning(f"{LOG_PREFIX} 模型 {model} 调用失败: {last_error}")
                    if first_script:
                        return first_script, True
                    break

        except Exception as e:
            last_error = str(e)
            logger.warning(f"{LOG_PREFIX} 模型 {model} 异常: {e}")
            continue

    logger.error(f"{LOG_PREFIX} 所有模型均失败: {last_error}")
    return "", False


# ===================== TTS 音频生成 =====================

async def _tts_generate(text: str, output_path: str, voice: str, rate: str) -> bool:
    """用 edge-tts 生成音频。"""
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(output_path)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        logger.error(f"{LOG_PREFIX} TTS 生成失败: {e}")
        return False



def generate_audio(script: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural",
                   rate: str = "+0%") -> bool:
    """同步包装 TTS 生成。"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_tts_generate(script, output_path, voice, rate))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"{LOG_PREFIX} 音频生成异常: {e}")
        return False


# ===================== 主函数 =====================

def generate_podcast(
    project_root: str,
    date: Optional[str] = None,
    events_file: Optional[str] = None,
    script_file: Optional[str] = None,
    audio_file: Optional[str] = None,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
    use_llm: bool = True,
    **kwargs,
) -> dict:
    """从事件池直接生成播客（方案C：独立于日报）。

    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD（默认今天）
        events_file: 事件文件路径（覆盖自动查找）
        script_file: 口播稿输出路径
        audio_file: 音频输出路径
        voice: edge-tts 语音
        rate: 语速
        use_llm: 是否使用 LLM

    Returns:
        结果 dict
    """
    if not date:
        date = datetime.now(CST).strftime("%Y-%m-%d")

    root = Path(project_root).resolve()

    # ── 目录 ──
    script_dir = root / "podcasts" / "scripts"
    audio_dir = root / "podcasts" / "audio"
    log_dir = root / "data" / "logs" / date

    for d in [script_dir, audio_dir, log_dir]:
        d.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "generate_podcast.log"

    def log(msg: str):
        ts = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {LOG_PREFIX} {msg}"
        print(line)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("=" * 60)
    log(f"开始生成播客: date={date}")
    log(f"  project_root: {root}")
    log(f"  voice: {voice}")
    log(f"  rate: {rate}")
    log(f"  use_llm: {use_llm}")
    log("=" * 60)

    # ── 查找事件文件 ──
    if not events_file:
        # 优先 events_scored_novelty（含 novelty + business_analysis）
        candidates = [
            root / "data" / "events" / date / "events_scored_novelty.json",
            root / "data" / "events" / date / "events_analyzed.json",
            root / "data" / "events" / date / "events_scored.json",
        ]
        for c in candidates:
            if c.exists():
                events_file = str(c)
                break

    if not events_file or not Path(events_file).exists():
        msg = f"未找到事件文件: {date}"
        log(f"❌ {msg}")
        return {
            "ok": False, "date": date, "events_file": "",
            "script_file": "", "audio_file": "", "script_length": 0,
            "errors": [msg],
        }

    # ── 加载事件 ──
    log(f"事件文件: {events_file}")
    with open(events_file, "r", encoding="utf-8") as f:
        events_data = json.load(f)
    all_events = events_data.get("events", events_data) if isinstance(events_data, dict) else events_data
    log(f"加载 {len(all_events)} 条事件")

    # ── 补充 business_analysis（如果主文件缺失）──
    has_ba = any(e.get("business_analysis") for e in all_events[:5])
    if not has_ba:
        analyzed_file = root / "data" / "events" / date / "events_analyzed.json"
        if analyzed_file.exists():
            try:
                with open(analyzed_file, "r", encoding="utf-8") as f:
                    analyzed_data = json.load(f)
                analyzed_events = analyzed_data.get("events", [])
                # 按 event_id 建索引
                ba_map = {e.get("event_id"): e.get("business_analysis", {})
                          for e in analyzed_events if e.get("business_analysis")}
                merged = 0
                for ev in all_events:
                    eid = ev.get("event_id")
                    if eid and eid in ba_map and not ev.get("business_analysis"):
                        ev["business_analysis"] = ba_map[eid]
                        merged += 1
                if merged:
                    log(f"  从 events_analyzed.json 合并 {merged} 条 business_analysis")
            except Exception as e:
                log(f"  ⚠️ 合并 business_analysis 失败: {e}")

    # ── 判断是否无信号 ──
    core_events = [e for e in all_events
                   if e.get("report_eligibility") == "core"
                   and e.get("novelty_status") in ("new_today", "updated_today")]
    is_no_signal = len(core_events) == 0
    log(f"事件类型: {'无信号' if is_no_signal else f'正常({len(core_events)}条core+new_today)'}")

    # ── 生成口播稿 ──
    script = ""
    llm_used = False

    if is_no_signal:
        script = NO_SIGNAL_TEMPLATE.format(date=date, total_events=len(all_events))
        log(f"无信号日报，使用模板生成 ({count_chinese_chars(script)} 字)")
    elif use_llm:
        log("尝试 LLM 生成口播稿...")
        script, llm_used = llm_generate_podcast_script(
            events=all_events,
            date=date,
            is_no_signal=False,
            project_root=str(root),
        )

    if not script and not is_no_signal:
        # LLM 失败，用简单规则生成
        log("LLM 失败，使用规则模板...")
        selected = select_podcast_events(all_events, 3)
        parts = [f"早安，这里是即时零售×个护美妆经营日报，{date}。\n"]
        parts.append(f"今天有{len(core_events)}条核心事件值得关注。\n")
        for i, ev in enumerate(selected):
            parts.append(f"第{i+1}条：{ev.get('event_title', '')}。")
            if ev.get("fact"):
                parts.append(f"{ev['fact']}")
            ba = ev.get("business_analysis", {})
            if ba.get("watsons_impact"):
                parts.append(f"对屈臣氏的影响：{ba['watsons_impact']}")
            if ba.get("recommended_action"):
                parts.append(f"建议动作：{ba['recommended_action']}\n")
        parts.append("以上就是今天的日报，明天见。")
        script = "\n".join(parts)

    if not script:
        msg = "口播稿生成失败"
        log(f"❌ {msg}")
        return {
            "ok": False, "date": date, "events_file": events_file,
            "script_file": "", "audio_file": "", "script_length": 0,
            "errors": [msg],
        }

    # ── 字符统计 ──
    cn_chars = count_chinese_chars(script)
    log(f"口播稿中文字符数: {cn_chars}")

    if not is_no_signal:
        if cn_chars < 1800:
            log(f"  ⚠️ 口播稿偏短（{cn_chars} < 1800），可能信息不充分")
        elif cn_chars > 5500:
            log(f"  ⚠️ 口播稿偏长（{cn_chars} > 5500），建议精简")

    # ── 保存口播稿 ──
    if not script_file:
        script_file = str(script_dir / f"{date}.md")
    Path(script_file).parent.mkdir(parents=True, exist_ok=True)
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(script)
    log(f"口播稿已保存: {script_file} ({cn_chars} 中文字符)")

    # ── 生成音频 ──
    if not audio_file:
        audio_file = str(audio_dir / f"{date}.mp3")
    Path(audio_file).parent.mkdir(parents=True, exist_ok=True)

    log(f"开始生成音频: voice={voice}, rate={rate}")
    tts_success = generate_audio(script, audio_file, voice=voice, rate=rate)

    if tts_success:
        audio_size = os.path.getsize(audio_file)
        log(f"✅ 音频生成成功: {audio_file}")
        log(f"  音频大小: {audio_size / 1024:.0f} KB")
    else:
        log(f"⚠️ 音频生成失败，口播稿已保存")

    # ── 保存结果 JSON ──
    result = {
        "ok": True,
        "date": date,
        "events_file": events_file,
        "script_file": script_file,
        "audio_file": audio_file if tts_success else "",
        "script_length": cn_chars,
        "audio_exists": tts_success,
        "audio_size_bytes": os.path.getsize(audio_file) if tts_success else 0,
        "is_no_signal": is_no_signal,
        "llm_used": llm_used,
        "tts_success": tts_success,
        "voice": voice,
        "core_event_count": len(core_events),
        "selected_event_count": len(select_podcast_events(all_events, 5)),
        "errors": [],
    }

    result_file = log_dir / "generate_podcast.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"结果 JSON 已保存: {result_file}")

    log("=" * 60)
    log("播客生成完成")
    log(f"  事件类型: {'无信号' if is_no_signal else '正常'}")
    log(f"  口播稿: {cn_chars} 中文字符")
    log(f"  LLM 生成: {'是' if llm_used else '否'}")
    log(f"  音频: {'✅ 成功' if tts_success else '❌ 失败'}")
    log(f"  语音: {voice}")
    log("=" * 60)

    return result


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(description="生成播客（从事件池直接生成）")
    parser.add_argument("--project-root", default=".", help="项目根目录")
    parser.add_argument("--date", default=None, help="日期 YYYY-MM-DD")
    parser.add_argument("--events-file", default=None, help="事件文件路径")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    parser.add_argument("--rate", default="+0%")
    parser.add_argument("--use-llm", default="true")
    args = parser.parse_args()

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = generate_podcast(
        project_root=args.project_root,
        date=args.date,
        events_file=args.events_file,
        voice=args.voice,
        rate=args.rate,
        use_llm=use_llm,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
