#!/usr/bin/env python3
"""generate_podcast — 将最终日报转为口播稿并生成音频播客。

核心原则:
1. 不直接朗读日报，转成适合通勤收听的口播稿
2. 不新增日报外事实/数据
3. 保留经营判断，但弱化 event_id、Markdown 符号和复杂层级
4. 口播稿控制在 3000-4000 中文字符
5. 无信号日报也生成无信号版口播稿
6. 语音使用 edge-tts
7. 音频生成失败不得影响日报发送，但需记录失败
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

# ── 时区工具 ──
try:
    from zoneinfo import ZoneInfo
    CST = ZoneInfo("Asia/Shanghai")
except ImportError:
    from dateutil.tz import gettz
    CST = gettz("Asia/Shanghai")

LOG_PREFIX = "[Podcast]"

# ── 中文字符统计 ──
CJK_RANGE = re.compile(
    r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff'
    r'\u2e80-\u2eff\u31c0-\u31ef\u3200-\u32ff\u3300-\u33ff]')


def count_chinese_chars(text: str) -> int:
    """统计中文字符数。"""
    return len(CJK_RANGE.findall(text))


# ===================== 口播稿模板 =====================

NO_SIGNAL_TEMPLATE = """早安，即时零售×个护美妆经营日报，{date}。

结论先行：今天没有高质量新增信号，不建议因外部信息调整策略。

数据层面：采集{raw_count}篇，补搜{gap_count}篇，经时效性和相关性双层筛选，无一篇进入核心分析。{reference_section}

低置信信号比没信号更危险——与其强行解读，不如诚实说今天没信号。{tomorrow_section}

明天见。"""


NORMAL_TEMPLATE = """早安，这里是即时零售×个护美妆经营日报，{date}。

{one_liner}

{signals}

{platform_section}

{competitor_section}

{category_section}

{watsons_section}

{action_section}

{tomorrow_section}

以上就是今天的日报，明天见。"""


# ===================== 规则口播稿生成 =====================

def extract_section(report: str, section_num: str, section_name: str) -> str:
    """从日报中提取指定章节的文本。"""
    # 匹配 "## 01 今日一句话判断" 或 "## 01 ..." 等格式
    pattern = rf'##\s*{section_num}\s+{re.escape(section_name)}.*?(?=\n##\s|\Z)'
    match = re.search(pattern, report, re.DOTALL)
    if match:
        return match.group(0)
    return ""


def parse_section_content(section_text: str) -> str:
    """去掉章节标题和 Markdown 符号，保留纯文本。"""
    if not section_text:
        return ""
    lines = section_text.strip().split('\n')
    # 去掉章节标题行
    content_lines = []
    for line in lines:
        line = line.strip()
        # 跳过空行
        if not line:
            continue
        # 跳过章节标题
        if re.match(r'^##\s*\d{2}\s', line):
            continue
        # 去掉 Markdown 符号
        line = line.replace('**', '').replace('`', '').replace('*', '')
        # 去掉 event ID
        line = re.sub(r'E\d{8}_\d{4}', '', line)
        # 去掉 "---"
        if line == '---':
            continue
        content_lines.append(line)
    return '\n'.join(content_lines)


def rule_generate_podcast_script(
    report: str,
    date: str,
    is_no_signal: bool = False,
    raw_count: int = 0,
    gap_count: int = 0,
    reference_count: int = 0,
) -> str:
    """规则模式生成口播稿。"""
    if is_no_signal:
        # 无信号日报：极简口播，200-300字
        if reference_count > 0:
            reference_section = f"{reference_count}篇参考级文章可作行业背景备查，但不构成经营动作依据。"
        else:
            reference_section = "参考级文章也为零，说明连外围行业动态都比较平淡。"

        # 明日追踪
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        from datetime import datetime as _dt
        date_obj = _dt.strptime(date, "%Y-%m-%d")
        weekday = weekday_names[date_obj.weekday()]
        tomorrow_section = (
            f"明天{weekday}值得关注的方向："
            "美团闪购首页资源位和美妆品类坑位变化，"
            "京东到家美妆个护活动上新及屈臣氏渠道策略调整，"
            "淘宝闪购非餐品类扩张节奏，"
            "以及抖音小时达美妆日百专区的新案例。"
        )

        script = NO_SIGNAL_TEMPLATE.format(
            date=date,
            raw_count=raw_count,
            gap_count=gap_count,
            reference_section=reference_section,
            tomorrow_section=tomorrow_section,
        )
        return script.strip()

    # 正常日报：提取各章节并组装
    sections = {
        "01": "今日一句话判断",
        "02": "今日最值得关注的3个信号",
        "03": "平台变化解读",
        "04": "竞对与品牌动作",
        "05": "品类与场景机会",
        "06": "对屈臣氏的经营提示",
        "07": "今日唯一建议动作",
        "08": "明日追踪清单",
    }

    parsed = {}
    for num, name in sections.items():
        section = extract_section(report, num, name)
        parsed[num] = parse_section_content(section)

    # 组装口播稿
    one_liner = parsed.get("01", "今日无新增重大信号。")

    # 信号部分
    signals_text = parsed.get("02", "")
    if signals_text:
        # 简化信号描述
        signal_lines = []
        for line in signals_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # 去掉序号前缀
            line = re.sub(r'^\d+[\.\)、]\s*', '', line)
            signal_lines.append(line)
        signals = "今天重点关注" + "、".join(signal_lines[:3]) + "。"
    else:
        signals = ""

    # 平台变化
    platform_text = parsed.get("03", "")
    platform_section = ""
    if platform_text:
        # 简化平台描述
        platform_lines = []
        for line in platform_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # 去掉 ### 标题，但保留平台名
            line = re.sub(r'^###\s*', '', line)
            if line.startswith('今日') or line.startswith('无'):
                continue
            platform_lines.append(line)
        if platform_lines:
            platform_section = "平台动态方面，" + "。".join(platform_lines[:5]) + "。"
        else:
            platform_section = "平台方面今天没有值得特别关注的动态。"

    # 竞对
    competitor_text = parsed.get("04", "")
    competitor_section = ""
    if competitor_text:
        competitor_lines = []
        for line in competitor_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line = re.sub(r'^###\s*', '', line)
            if line.startswith('今日') or line.startswith('无'):
                continue
            competitor_lines.append(line)
        if competitor_lines:
            competitor_section = "竞对方面，" + "。".join(competitor_lines[:4]) + "。"
        else:
            competitor_section = "竞对方面今天没有新增动态。"

    # 品类
    category_text = parsed.get("05", "")
    category_section = ""
    if category_text:
        category_lines = []
        for line in category_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line = re.sub(r'^###\s*', '', line)
            if line.startswith('今日') or line.startswith('无'):
                continue
            category_lines.append(line)
        if category_lines:
            category_section = "品类方面，" + "。".join(category_lines[:4]) + "。"
        else:
            category_section = ""

    # 屈臣氏
    watsons_text = parsed.get("06", "")
    watsons_section = ""
    if watsons_text:
        watsons_lines = []
        for line in watsons_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            line = re.sub(r'^###\s*', '', line)
            if line.startswith('今日') or line.startswith('无'):
                continue
            watsons_lines.append(line)
        if watsons_lines:
            watsons_section = "对屈臣氏的经营提示：" + "。".join(watsons_lines[:4]) + "。"

    # 唯一建议
    action_text = parsed.get("07", "")
    action_section = ""
    if action_text:
        action_lines = []
        for line in action_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line == '---':
                continue
            line = re.sub(r'^[-*]\s*', '', line)
            action_lines.append(line)
        if action_lines:
            action_section = "今天的唯一建议动作：" + action_lines[0] + "。"

    # 明日追踪
    tomorrow_text = parsed.get("08", "")
    tomorrow_section = ""
    if tomorrow_text:
        tomorrow_lines = []
        for line in tomorrow_text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line == '---':
                continue
            line = re.sub(r'^[-*]\s*', '', line)
            tomorrow_lines.append(line)
        if tomorrow_lines:
            tomorrow_section = "明天需要追踪" + "、".join(tomorrow_lines[:5]) + "。"

    script = NORMAL_TEMPLATE.format(
        date=date,
        one_liner=one_liner,
        signals=signals,
        platform_section=platform_section,
        competitor_section=competitor_section,
        category_section=category_section,
        watsons_section=watsons_section,
        action_section=action_section,
        tomorrow_section=tomorrow_section,
    )

    return script.strip()


# ===================== LLM 口播稿生成 =====================

LLM_SYSTEM_PROMPT = """你是一位专业的财经播客主播，擅长将经营日报转化为适合通勤收听的口播稿。你的风格是业务顾问式的深度解读，不是新闻播报。

## 核心规则
1. **不新增日报以外的任何事实或数据**——所有内容必须来自原始日报
2. **不直接朗读日报**——要转化为口语化的、有分析深度的表达
3. **保留经营判断**——日报中的分析、建议、警示都要保留并展开
4. **弱化技术标注**——event_id、置信度标签、Markdown符号一律去掉
5. **严格控制在3000-4000中文字符之间**——这是硬性要求，低于2800字不合格
6. **自然口语**——像一位资深零售顾问在跟你聊天，有观点、有判断、有建议
7. **开头格式**：「早安，这里是即时零售×个护美妆经营日报，{date}。」
8. **结束格式**：「以上就是今天的日报，明天见。」

## 口播稿结构（正常日报）——每个部分都要充分展开

### 第一段：核心判断（约150字）
- 今天最重要的一个结论是什么？为什么重要？

### 第二段~第四段：重点信号深度解读（每条约500-600字，共3条）
对日报中最重要的2-3条信号，逐条做5维度分析：
- 发生了什么（事实）
- 为什么重要（行业意义）
- 对屈臣氏意味着什么（经营影响）
- 值得学习或警惕什么（启示/风险）
- 今天要盯什么指标（可执行）

### 第五段：平台与竞对动态速览（约400字）
- 其他平台的关键变化，逐个点评

### 第六段：机会点与建议动作（约400字）
- 对屈臣氏的具体建议，分商品/平台/营销/试点四个维度

### 第七段：风险提示与明日追踪（约200字）
- 需要警惕的风险 + 明天要追踪什么

## 口播稿结构（无信号日报）
1. 一句话结论：今天无高质量新增信号
2. 采集数据和筛选结果简述
3. 明日关注方向（1-2句）
4. 总字数控制在200-300中文字符，不要硬凑字数

## 禁止事项
- 禁止添加日报中没有的数据、数字、事件
- 禁止使用 event_id 如 E20260426_0001
- 禁止使用 Markdown 格式符号（如 **、###、- 等）
- 禁止使用"据报道"等新闻式用语——你是经营情报播客，不是新闻播报
- 禁止生成少于2800中文字符的口播稿（正常日报）"""

LLM_USER_TEMPLATE = """请将以下日报转为口播稿。

{char_hint}

重要：口播稿必须达到{char_range}中文字符。对核心信号要做深度解读（发生了什么、为什么重要、对屈臣氏意味着什么、值得学习或警惕什么、今天盯什么指标），每条信号至少300字展开分析。不要只是概括，要像业务顾问一样给出有深度的判断和建议。

日报内容：
---
{report}
---

要求：
1. 严格{char_range}中文字符（低于下限不合格，请充分展开分析）
2. 自然口语化，业务顾问风格
3. 不新增日报外事实
4. 去掉 event_id 和 Markdown 符号
5. 对2-3条核心信号做5维度深度分析"""


def llm_generate_podcast_script(
    report: str,
    date: str,
    is_no_signal: bool = False,
    project_root: str = ".",
) -> Tuple[str, bool]:
    """用 LLM 生成口播稿。优先 LongCat-Flash-Chat，fallback 用 Lite。

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
        default_model = "LongCat-Flash-Chat"
        fallbacks = ["LongCat-Flash-Lite"]

    # 如果路由返回 rule_only，则直接用规则
    if default_model == "rule_only":
        logger.info(f"{LOG_PREFIX} 模型路由返回 rule_only，使用规则生成")
        return "", False

    # 尝试主模型
    models_to_try = [default_model] + fallbacks
    last_error = ""
    min_chars_required = 200 if is_no_signal else 2800  # 重试阈值

    for model in models_to_try:
        try:
            client = LLMClient()
            logger.info(f"{LOG_PREFIX} 使用模型: {model}")

            system_prompt = LLM_SYSTEM_PROMPT.replace("{date}", date)
            char_hint = "⚠️ 这是一份无信号日报。口播稿控制在200-300中文字符，只说结论、数据、明日关注，不要硬凑字数。" if is_no_signal else "口播稿控制在3000-4000中文字符。"
            char_range = "200-300" if is_no_signal else "3000-4000"
            user_content = LLM_USER_TEMPLATE.format(
                report=report[:8000],  # 限制输入长度
                char_hint=char_hint,
                char_range=char_range,
            )

            # 最多尝试 2 次（首次 + 1 次重试）
            for attempt in range(2):
                result = client.chat(
                    messages=[{"role": "user", "content": user_content}],
                    system_prompt=system_prompt,
                    model=model,
                    temperature=0.3 if attempt == 0 else 0.5,
                    max_tokens=6000,
                )

                if result.get("ok") and result.get("content", "").strip():
                    script = result["content"].strip()
                    # 去掉可能的 markdown 代码块标记
                    script = re.sub(r'^```(?:markdown|md)?\s*\n?', '', script)
                    script = re.sub(r'\n?```\s*$', '', script)
                    cn_chars = count_chinese_chars(script)
                    logger.info(f"{LOG_PREFIX} LLM 生成成功，模型: {model}，"
                                f"中文字符数: {cn_chars} (attempt {attempt+1})")

                    # 长度检查：如果太短且还有重试机会，重新生成
                    if cn_chars < min_chars_required and attempt == 0 and not is_no_signal:
                        logger.warning(
                            f"{LOG_PREFIX} 口播稿过短({cn_chars}<{min_chars_required})，"
                            f"重试并提高 temperature...")
                        # 修改 user_content 强调长度
                        user_content = (
                            f"上次生成的口播稿只有{cn_chars}字，严重不足。"
                            f"请重新生成，必须达到3000-4000中文字符。"
                            f"对每条核心信号要做300-400字的深度分析。\n\n"
                            + user_content
                        )
                        continue  # 重试

                    return script, True
                else:
                    last_error = result.get("error", "unknown")
                    logger.warning(f"{LOG_PREFIX} 模型 {model} 调用失败: {last_error}")
                    break  # 这个模型失败了，试下一个

        except Exception as e:
            last_error = str(e)
            logger.warning(f"{LOG_PREFIX} 模型 {model} 异常: {e}")
            continue

    logger.error(f"{LOG_PREFIX} 所有 LLM 模型均失败: {last_error}")
    return "", False


# ===================== 音频生成 =====================

async def _generate_audio_with_edge_tts(
    text: str,
    output_path: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
) -> bool:
    """使用 edge-tts 异步生成音频。"""
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(output_path)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"{LOG_PREFIX} 音频生成成功: {output_path} "
                        f"({os.path.getsize(output_path):,} bytes)")
            return True
        else:
            logger.error(f"{LOG_PREFIX} 音频文件为空或不存在: {output_path}")
            return False

    except ImportError:
        logger.error(f"{LOG_PREFIX} edge-tts 未安装")
        return False
    except Exception as e:
        logger.error(f"{LOG_PREFIX} edge-tts 生成音频失败: {e}")
        traceback.print_exc()
        return False


def generate_audio(
    script: str,
    output_path: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
) -> bool:
    """同步接口：生成 MP3 音频。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已有事件循环中，使用 nest_asyncio 或新线程
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    _generate_audio_with_edge_tts(script, output_path, voice, rate),
                )
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(
                _generate_audio_with_edge_tts(script, output_path, voice, rate)
            )
    except RuntimeError:
        # 没有事件循环，创建新的
        return asyncio.run(
            _generate_audio_with_edge_tts(script, output_path, voice, rate)
        )


# ===================== 主函数 =====================

def generate_podcast(
    project_root: str,
    date: Optional[str] = None,
    report_file: Optional[str] = None,
    script_file: Optional[str] = None,
    audio_file: Optional[str] = None,
    voice: str = "zh-CN-XiaoxiaoNeural",
    rate: str = "+0%",
    use_llm: bool = True,
) -> dict:
    """将最终日报转为口播稿并生成音频播客。

    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD（默认今天）
        report_file: 指定日报文件路径（覆盖自动查找）
        script_file: 指定口播稿输出路径（覆盖默认）
        audio_file: 指定音频输出路径（覆盖默认）
        voice: edge-tts 语音（默认 zh-CN-XiaoxiaoNeural）
        rate: 语速调整（默认 +0%）
        use_llm: 是否使用 LLM 生成口播稿（fallback 用规则）

    Returns:
        结果 dict
    """
    # ── 日期 ──
    if not date:
        date = datetime.now(CST).strftime("%Y-%m-%d")

    root = Path(project_root).resolve()
    errors: List[str] = []

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

    # ── 查找日报 ──
    if report_file:
        report_path = Path(report_file)
    else:
        # 按优先级查找：reports/daily/YYYY/MM/YYYY-MM-DD.md > data/reports/final_report
        year, month = date.split("-")[0], date.split("-")[1]
        candidates = [
            root / "reports" / "daily" / year / month / f"{date}.md",
            root / "data" / "reports" / date / f"final_report_{date}.md",
            root / "data" / "reports" / date / f"daily_report_{date}_no_signal.md",
        ]
        report_path = None
        for c in candidates:
            if c.exists():
                report_path = c
                break

        if report_path is None:
            msg = f"未找到日报文件: {date}"
            log(f"❌ {msg}")
            return {
                "ok": False,
                "date": date,
                "report_file": "",
                "script_file": "",
                "audio_file": "",
                "script_length": 0,
                "audio_exists": False,
                "is_no_signal": False,
                "voice": voice,
                "tts_success": False,
                "errors": [msg],
            }

    report_path = report_path.resolve()
    log(f"日报文件: {report_path}")

    # ── 读取日报 ──
    if not report_path.exists():
        msg = f"日报文件不存在: {report_path}"
        log(f"❌ {msg}")
        return {
            "ok": False, "date": date, "report_file": str(report_path),
            "script_file": "", "audio_file": "", "script_length": 0,
            "audio_exists": False, "is_no_signal": False, "voice": voice,
            "tts_success": False, "errors": [msg],
        }

    try:
        report_text = report_path.read_text(encoding="utf-8")
    except Exception as e:
        msg = f"读取日报失败: {e}"
        log(f"❌ {msg}")
        return {
            "ok": False, "date": date, "report_file": str(report_path),
            "script_file": "", "audio_file": "", "script_length": 0,
            "audio_exists": False, "is_no_signal": False, "voice": voice,
            "tts_success": False, "errors": [msg],
        }

    # ── 判断是否无信号日报 ──
    is_no_signal = (
        "无新增信号" in report_text
        or "无信号" in report_text
        or "no_signal" in str(report_path)
        or "今日未发现足够高质量新增信号" in report_text
    )
    log(f"日报类型: {'无信号日报' if is_no_signal else '正常日报'}")

    # ── 收集统计（用于规则模板） ──
    raw_count = 0
    gap_count = 0
    reference_count = 0

    # 尝试从 pipeline manifest 读取统计
    manifest_file = root / "data" / "runs" / date / "run_manifest.json"
    if manifest_file.exists():
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            for step in manifest.get("steps", []):
                stats = step.get("stats", {})
                if step.get("name") == "collect":
                    raw_count = stats.get("raw_count", stats.get("total_saved", 0))
                elif step.get("name") in ("filter", "filter_merged"):
                    if raw_count == 0:
                        raw_count = stats.get("raw_count", 0)
                    reference_count = stats.get("reference_count", reference_count)
                elif step.get("name") == "tavily_gap_search":
                    gap_count = stats.get("tavily_unique_count", stats.get("tavily_gap_count", 0))
        except Exception:
            pass

    # 也尝试从 generate_no_signal_report 的 JSON 结果读取
    ns_json = root / "data" / "logs" / date / "generate_no_signal_report.json"
    if ns_json.exists() and is_no_signal:
        try:
            ns_data = json.loads(ns_json.read_text(encoding="utf-8"))
            raw_count = ns_data.get("raw_count", raw_count)
            gap_count = ns_data.get("gap_count", gap_count)
            reference_count = ns_data.get("reference_count", reference_count)
        except Exception:
            pass

    # ── 生成口播稿 ──
    script = ""
    llm_used = False

    if use_llm:
        log("尝试 LLM 生成口播稿...")
        script, llm_used = llm_generate_podcast_script(
            report=report_text,
            date=date,
            is_no_signal=is_no_signal,
            project_root=str(root),
        )

    if not script:
        log("LLM 不可用或失败，使用规则模板生成口播稿...")
        script = rule_generate_podcast_script(
            report=report_text,
            date=date,
            is_no_signal=is_no_signal,
            raw_count=raw_count,
            gap_count=gap_count,
            reference_count=reference_count,
        )

    if not script:
        msg = "口播稿生成失败（LLM 和规则均失败）"
        log(f"❌ {msg}")
        return {
            "ok": False, "date": date, "report_file": str(report_path),
            "script_file": "", "audio_file": "", "script_length": 0,
            "audio_exists": False, "is_no_signal": is_no_signal, "voice": voice,
            "tts_success": False, "errors": [msg],
        }

    # ── 字符数检查 ──
    cn_chars = count_chinese_chars(script)
    log(f"口播稿中文字符数: {cn_chars}")

    if is_no_signal:
        # 无信号日报：200-300字，不需要警告偏短
        if cn_chars > 400:
            log(f"  ⚠️ 无信号口播稿偏长（{cn_chars} > 400），建议精简")
    else:
        # 正常日报：3000-4000字
        if cn_chars < 2800:
            log(f"  ⚠️ 口播稿偏短（{cn_chars} < 2800），可能信息不充分")
        elif cn_chars > 2500:
            log(f"  ⚠️ 口播稿偏长（{cn_chars} > 2500），建议精简")

    # ── 保存口播稿 ──
    if script_file:
        script_path = Path(script_file)
    else:
        script_path = script_dir / f"{date}.md"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    log(f"口播稿已保存: {script_path} ({cn_chars} 中文字符)")

    # ── 生成音频 ──
    if audio_file:
        audio_path = Path(audio_file)
    else:
        audio_path = audio_dir / f"{date}.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    # 清理口播稿中的 Markdown 残留，作为 TTS 输入
    tts_text = script
    tts_text = re.sub(r'^#\s+', '', tts_text, flags=re.MULTILINE)
    tts_text = re.sub(r'^##\s+', '', tts_text, flags=re.MULTILINE)
    tts_text = re.sub(r'^###\s+', '', tts_text, flags=re.MULTILINE)
    tts_text = re.sub(r'^\*\s+', '，', tts_text, flags=re.MULTILINE)
    tts_text = re.sub(r'^-\s+', '，', tts_text, flags=re.MULTILINE)
    tts_text = re.sub(r'^\d+\.\s+', '', tts_text, flags=re.MULTILINE)
    tts_text = re.sub(r'\*{1,2}', '', tts_text)
    tts_text = re.sub(r'`{1,3}', '', tts_text)
    tts_text = re.sub(r'\n{3,}', '\n\n', tts_text)
    tts_text = tts_text.strip()

    tts_success = False
    try:
        log(f"开始生成音频: voice={voice}, rate={rate}")
        tts_success = generate_audio(tts_text, str(audio_path), voice=voice, rate=rate)
        if tts_success:
            log(f"✅ 音频生成成功: {audio_path}")
        else:
            log(f"❌ 音频生成失败")
            errors.append("音频生成失败（edge-tts 返回失败）")
    except Exception as e:
        log(f"❌ 音频生成异常: {e}")
        traceback.print_exc()
        errors.append(f"音频生成异常: {e}")

    audio_exists = audio_path.exists() and audio_path.stat().st_size > 0

    # ── 保存 JSON 结果 ──
    result = {
        "ok": True,
        "date": date,
        "report_file": str(report_path),
        "script_file": str(script_path),
        "audio_file": str(audio_path) if audio_exists else "",
        "script_length": cn_chars,
        "audio_exists": audio_exists,
        "audio_size_bytes": audio_path.stat().st_size if audio_exists else 0,
        "is_no_signal": is_no_signal,
        "voice": voice,
        "rate": rate,
        "llm_used": llm_used,
        "tts_success": tts_success,
        "errors": errors,
    }

    json_file = log_dir / "generate_podcast.json"
    json_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"结果 JSON 已保存: {json_file}")

    log("=" * 60)
    log(f"播客生成完成")
    log(f"  日报类型: {'无信号日报' if is_no_signal else '正常日报'}")
    log(f"  口播稿: {cn_chars} 中文字符")
    log(f"  LLM 生成: {'是' if llm_used else '否（规则模板）'}")
    log(f"  音频: {'✅ 成功' if audio_exists else '❌ 失败'}")
    log(f"  语音: {voice}")
    log("=" * 60)

    return result


# ===================== CLI =====================

def main():
    parser = argparse.ArgumentParser(
        description="将日报转为口播稿并生成音频播客",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--date", default=None,
                        help="日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--report-file", default=None,
                        help="指定日报文件路径（覆盖自动查找）")
    parser.add_argument("--script-file", default=None,
                        help="指定口播稿输出路径（覆盖默认）")
    parser.add_argument("--audio-file", default=None,
                        help="指定音频输出路径（覆盖默认）")
    parser.add_argument("--voice", default="zh-CN-XiaoxiaoNeural",
                        choices=[
                            "zh-CN-XiaoxiaoNeural",
                            "zh-CN-YunxiNeural",
                            "zh-CN-YunjianNeural",
                            "zh-CN-XiaoyiNeural",
                        ],
                        help="edge-tts 语音（默认 zh-CN-XiaoxiaoNeural）")
    parser.add_argument("--rate", default="+0%",
                        help="语速调整（如 +10%%, -5%%，默认 +0%%）")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用 LLM 生成口播稿 (true/false)")

    args = parser.parse_args()

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = generate_podcast(
        project_root=args.project_root,
        date=args.date,
        report_file=args.report_file,
        script_file=args.script_file,
        audio_file=args.audio_file,
        voice=args.voice,
        rate=args.rate,
        use_llm=use_llm,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()