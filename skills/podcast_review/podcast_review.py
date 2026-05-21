#!/usr/bin/env python3
"""podcast_review — 播客口播稿审稿器（规则 + LLM 双审）。

检查项:
1. 是否口语化（无书面语、无复杂嵌套从句）
2. 是否照读日报（与终稿相似度）
3. 是否有行动建议（结尾"三件事"段）
4. 是否新增事实（仅引用事件池内事实）
5. 是否适合通勤收听（字数、节奏、时长估算）
6. [LLM] 深度审稿 — 口语化、非照读、行动建议质量、事实幻觉、通勤适用性

审稿不通过 → 记录问题，由 run_daily_pipeline 决定是否重试。
LLM 审稿在规则审稿后执行，提供第二意见。
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s]   %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("podcast_review")

CST = timezone(timedelta(hours=8))

# ── 阈值 ──
ORALITY_MIN_SCORE = 60
SIMILARITY_MAX = 0.45
ACTION_MIN_COUNT = 2
FACT_NOVELTY_MAX = 0
COMMUTE_MAX_CHARS = 5000
COMMUTE_MAX_EST_MINUTES = 18


def load_events(project_root: str, date: str) -> List[dict]:
    """加载事件池（评分+新颖性后的合并事件）。"""
    events_path = Path(project_root) / "data" / "events" / date / "events_scored_novelty.json"
    if not events_path.exists():
        events_path = Path(project_root) / "data" / "events" / date / "events_scored.json"
    if not events_path.exists():
        events_path = Path(project_root) / "data" / "events" / date / "events_analyzed.json"
    if not events_path.exists():
        logger.warning(f"事件文件不存在: {events_path}")
        return []

    with open(events_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    return data.get("events", data.get("articles", []))


def load_report(project_root: str, date: str) -> str:
    """加载最终日报全文。"""
    report_path = Path(project_root) / "reports" / "daily" / date[:4] / date[5:7] / f"{date}.md"
    if not report_path.exists():
        return ""
    with open(report_path, "r", encoding="utf-8") as f:
        return f.read()


def load_podcast_script(project_root: str, date: str) -> str:
    """加载口播稿。"""
    script_path = Path(project_root) / "podcasts" / "scripts" / f"{date}.md"
    if not script_path.exists():
        # Try alternate location
        script_path = Path(project_root) / "data" / "podcasts" / date / "podcast_script.md"
    if not script_path.exists():
        return ""
    with open(script_path, "r", encoding="utf-8") as f:
        return f.read()


def load_podcast_outline(project_root: str, date: str) -> Optional[dict]:
    """加载播客大纲。"""
    # Try multiple locations
    outline_path = Path(project_root) / "data" / "drafts" / date / "podcast_outline.json"
    if not outline_path.exists():
        outline_path = Path(project_root) / "data" / "podcasts" / date / "podcast_outline.json"
    if not outline_path.exists():
        outline_path = Path(project_root) / "data" / "drafts" / date.replace("-", "") / "podcast_outline.json"
    if not outline_path.exists():
        return None
    with open(outline_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════
# 检查1: 口语化评分
# ═══════════════════════════════════════════════════════════════

# 书面语特征词（口语中应避免）
WRITTEN_PATTERNS = [
    r'综上所述', r'总而言之', r'由此可见', r'值得注意',
    r'毋庸置疑', r'显著提升', r'大幅增长', r'持续优化',
    r'赋能', r'抓手', r'闭环', r'底层逻辑', r'方法论',
    r'因此[，,]', r'然而[，,]', r'此外[，,]', r'与此同时',
    r'在[^\n]{0,10}(背景|框架|体系|格局|维度|层面)下',
    r'通过对[^\n]{5,30}的分析',
    r'呈现[^\n]{0,10}(态势|趋势|格局|特征)',
    r'\b(leveraging|utilizing|implementing|optimizing)\b',
]

# 口语好特征
ORAL_PATTERNS = [
    r'[你您]可以', r'举个例子', r'说白了', r'简单说',
    r'注意了', r'关键是', r'值得[一看听]', r'我们来看',
    r'？', r'！', r'~', r'…',
    r'比如说', r'就像', r'好比',
    r'这[个件]事', r'今天[最该值得]',
    r'(听好|记住|别忘了|敲黑板)',
]


def score_orality(script: str) -> Tuple[int, List[str]]:
    """评估口语化程度。
    
    Returns:
        (score, issues): 0-100分, 问题列表
    """
    issues = []
    score = 100

    # 1. 书面语扣分
    for pat in WRITTEN_PATTERNS:
        matches = re.findall(pat, script)
        if matches:
            score -= len(matches) * 5
            issues.append(f"书面语: {matches[0]}…({'共'+str(len(matches))+'处' if len(matches)>1 else '1处'})")

    # 2. 口语特征加分（但最多回补20分）
    oral_bonus = 0
    for pat in ORAL_PATTERNS:
        matches = re.findall(pat, script)
        oral_bonus += len(matches) * 2
    oral_bonus = min(oral_bonus, 20)
    score += oral_bonus

    # 3. 句子长度检查（平均>30字扣分）
    sentences = re.split(r'[。！？\n]', script)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
    if sentences:
        avg_len = sum(len(s) for s in sentences) / len(sentences)
        if avg_len > 40:
            score -= 20
            issues.append(f"句子偏长(平均{avg_len:.0f}字)，口语建议<30字")
        elif avg_len > 30:
            score -= 10
            issues.append(f"句子略长(平均{avg_len:.0f}字)")

    # 4. 对话感检查：业务顾问播客以"我们"为自然语气，"你/您"为辅
    you_count = len(re.findall(r'[你您]', script))
    we_count = len(re.findall(r'我们', script))
    dialogue_score = you_count * 2 + we_count  # "你"权重更高
    if dialogue_score < 3:
        score -= 15
        issues.append(f"缺少对话感（'你/您/我们'出现{dialogue_score}分 < 3分）")

    return max(0, min(100, score)), issues


# ═══════════════════════════════════════════════════════════════
# 检查2: 是否照读日报
# ═══════════════════════════════════════════════════════════════

def check_report_similarity(script: str, report: str) -> Tuple[float, List[str]]:
    """检查口播稿与日报的相似度。
    
    使用 n-gram overlap 计算（不依赖外部库）。
    
    Returns:
        (similarity, issues): 0-1相似度, 问题列表
    """
    if not report or not script:
        return 0.0, []

    # 提取纯文本（去掉 Markdown 标记）
    def clean_text(text: str) -> str:
        text = re.sub(r'#[# ]*', '', text)
        text = re.sub(r'\*\*', '', text)
        text = re.sub(r'`', '', text)
        text = re.sub(r'[-*]\s', '', text)
        text = re.sub(r'E\d{8}_\d{4}', '', text)
        text = re.sub(r'---', '', text)
        return text

    clean_script = clean_text(script)
    clean_report = clean_text(report)

    # 3-gram overlap
    def get_ngrams(text: str, n: int = 3) -> set:
        chars = list(text)
        return set(''.join(chars[i:i+n]) for i in range(len(chars) - n + 1))

    script_ngrams = get_ngrams(clean_script, 4)
    report_ngrams = get_ngrams(clean_report, 4)

    if not script_ngrams:
        return 0.0, []

    overlap = len(script_ngrams & report_ngrams) / len(script_ngrams)
    issues = []
    if overlap > SIMILARITY_MAX:
        issues.append(f"与日报相似度过高({overlap:.1%} > {SIMILARITY_MAX:.0%})，疑似照读日报")
    elif overlap > 0.35:
        issues.append(f"与日报相似度偏高({overlap:.1%})，建议更多口语化改写")

    return overlap, issues


# ═══════════════════════════════════════════════════════════════
# 检查3: 是否有行动建议
# ═══════════════════════════════════════════════════════════════

def check_action_items(script: str) -> Tuple[bool, List[str]]:
    """检查结尾"三件事"段是否有足够的行动建议。
    
    Returns:
        (passed, issues)
    """
    issues = []

    # 找结尾段起始位置
    ending_start = -1
    ending_patterns = [
        r'今天听完.*?三件事',
        r'今天只做.*?三件事',
        r'结尾.*?三件事',
        r'今天[你您].*?(?:要做|记住|行动)',
    ]
    for pat in ending_patterns:
        m = re.search(pat, script, re.IGNORECASE)
        if m:
            ending_start = m.start()
            break

    if ending_start < 0:
        issues.append("缺少结尾行动建议段（'今天听完只做三件事'）")
        return False, issues

    # 从结尾起始位置到最后，找数字列表
    ending_section = script[ending_start:]
    
    # 数行动建议条数（匹配 "1. " "2、" "3）" "一、" "第一，" 等格式）
    action_items = re.findall(
        r'(?:^|\n)\s*(?:[-*]\s+)?(?:第)?(\d+|[一二三四五六七八九])[.、，）\)\s：:]',
        ending_section, re.MULTILINE
    )
    # 去重编号
    seen_nums = set()
    unique_actions = []
    for a in action_items:
        if a not in seen_nums:
            seen_nums.add(a)
            unique_actions.append(a)
    
    action_count = len(unique_actions)
    if action_count < ACTION_MIN_COUNT:
        issues.append(f"结尾行动建议不足（{action_count}条 < {ACTION_MIN_COUNT}条）")
        return False, issues

    return True, []


# ═══════════════════════════════════════════════════════════════
# 检查4: 是否新增事实
# ═══════════════════════════════════════════════════════════════

def check_fact_novelty(script: str, events: List[dict]) -> Tuple[int, List[str]]:
    """检查口播稿是否引入了事件池之外的新事实。
    
    1. 从事件池提取所有已知品牌/公司/平台名
    2. 从口播稿提取所有疑似品牌/公司名  
    3. 标记不在事件池中的品牌名
    4. 辅以事实句级别的相似度检查
    
    Returns:
        (novel_count, issues): 新增事实数, 问题列表
    """
    if not events:
        return 0, []

    # ── 1. 从事件池提取所有已知实体 ──
    known_entities = set()
    known_facts = set()
    for ev in events:
        title = ev.get("event_title", ev.get("title", ""))
        if title:
            known_facts.add(title[:80])
        fact = ev.get("fact", ev.get("raw_fact", ""))
        if fact:
            known_facts.add(fact[:120])
        # entities 字段
        entities = ev.get("entities", {})
        if isinstance(entities, dict):
            for entity_type in ("platforms", "companies", "competitors", "brands", "channels"):
                vals = entities.get(entity_type, [])
                if isinstance(vals, list):
                    for v in vals:
                        known_entities.add(str(v).strip())
    
    # ── 2. 从口播稿提取疑似品牌/公司名 ──
    brand_patterns = [
        # 即时零售/电商
        r'(?:盒马|丝芙兰|屈臣氏|WOW\s*COLOUR|THE\s*COLORIST|HARMAY|美团|饿了么|京东|淘宝|天猫|抖音|快手|拼多多|小红书|叮咚|朴朴)',
        # 美妆品牌
        r'(?:欧莱雅|雅诗兰黛|兰蔻|珀莱雅|毛戈平|修丽可|韩束|百雀羚|孔凤春|柳丝木|资生堂|SK-II|OLAY|玉兰油)',
        # 大型企业
        r'(?:阿里巴巴|腾讯|字节|百度|苏宁|永辉|山姆|Costco|好市多)',
    ]
    unknown_brands = []
    for pat in brand_patterns:
        found = re.findall(pat, script)
        for brand in set(found):
            brand_clean = brand.replace(" ", "")
            if "屈臣氏" in brand_clean:
                continue
            if brand_clean not in known_entities:
                unknown_brands.append(brand_clean)
    
    # ── 白名单：prompt 模板中的标准竞对/平台，不算编造 ──
    ALLOWED_BRANDS = {
        "丝芙兰", "WOWCOLOUR", "THECOLORIST", "THE COLORIST", "HARMAY",
        "美团", "饿了么", "京东", "淘宝", "天猫", "抖音", "快手", "拼多多",
        "大众点评", "阿里巴巴", "腾讯", "小红书", "盒马", "叮咚", "朴朴",
        "永辉", "山姆", "苏宁", "百度", "字节",
    }
    unknown_brands = [b for b in unknown_brands if b not in ALLOWED_BRANDS]
    
    issues = []
    if unknown_brands:
        issues.append(f"疑似编造品牌: {', '.join(sorted(set(unknown_brands))[:5])}")

    # ── 2.5: 检测虚构的具体数字 ──
    # 提取所有"NNN万/NNN亿"格式的数字
    fake_numbers = re.findall(r'(\d+[万亿千百]\S{0,5})', script)
    all_known_text = " ".join(known_facts)
    # 建议性上下文中的数字不算编造（如"建议追加500万预算"）
    SUGGESTION_CONTEXT = re.compile(r'(建议|推荐|预计|目标|预算|追加|提升|争取|确保|至少|不低于|约|大约|估计|预期)')
    fabricated_numbers = []
    for num in fake_numbers:
        if num not in all_known_text:
            # 检查该数字前后50字是否有建议性上下文
            num_pos = script.find(num)
            context_window = script[max(0, num_pos-50):num_pos+len(num)+50]
            if not SUGGESTION_CONTEXT.search(context_window):
                fabricated_numbers.append(num)
    if len(fabricated_numbers) >= 3:
        issues.append(f"疑似虚构数字: {', '.join(fabricated_numbers[:4])}")
    
    # ── 2.6: 检测虚构的时间/经历描述 ──
    fake_temporal = re.findall(r'(上周|前几天|最近看到|我注意到|据我了[解知])', script)
    if fake_temporal:
        issues.append(f"疑似虚构经历: {', '.join(list(set(fake_temporal))[:3])}")

    # ── 3. 事实句级别检查 ──
    # 提取事件池中的所有事实文本
    all_known_text = " ".join(known_facts)
    
    fact_sentences = re.findall(
        r'([^。！？\n]{15,80}(?:\d+[万亿千百]|[A-Z][a-z]{2,}|[\u4e00-\u9fff]{2,}(?:平台|公司|品牌|业务|市场|渠道))[^。！？\n]{5,})',
        script
    )

    novel_count = len(unknown_brands)
    for sent in fact_sentences:
        sent_clean = sent.strip()
        if sent_clean.startswith("**"):
            continue
        
        # 跳过分析/建议句
        skip_keywords = [
            '我们可以', '建议', '今天可以', '今天要看', '这意味着',
            '所以', '因此', '对屈臣氏', '意味着', '值得', '需要',
            '可以更', '可以主动', '可以考虑', '应该', '应当',
            '结论先行', '今天是', '没有发现', '今天没有',
            '今天的核心', '利用这段', '检查', '启动', '圈选',
            '重点看', '优先', '最好的办法', '关键是',
            '更主动', '把门店', '实体优势',
        ]
        if any(skp in sent_clean for skp in skip_keywords):
            continue
        
        # 检查与已知事实的相似度
        overlap = len(set(sent_clean) & set(all_known_text))
        if overlap / max(len(sent_clean), 1) < 0.4:
            novel_count += 1
            if len(issues) < 4:
                issues.append(f"疑似新增事实: {sent_clean[:60]}…")

    return novel_count, issues


# ═══════════════════════════════════════════════════════════════
# 检查5: 是否适合通勤收听
# ═══════════════════════════════════════════════════════════════

def check_commute_fitness(script: str) -> Tuple[bool, Dict[str, Any]]:
    """检查是否适合通勤收听。
    
    Returns:
        (passed, stats)
    """
    issues = []
    stats = {}

    # 用中文字符数来衡量（更准确反映口播时长）
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', script))
    char_count = len(script)
    stats["char_count"] = char_count
    stats["chinese_chars"] = chinese_chars
    if chinese_chars > 5000:
        issues.append(f"中文字数过长({chinese_chars} > 5000)，不适合通勤")

    # 预估时长（中文口播约 220-260 字/分钟，取 240）
    est_minutes = chinese_chars / 240
    stats["est_minutes"] = round(est_minutes, 1)
    if est_minutes > COMMUTE_MAX_EST_MINUTES:
        issues.append(f"预估时长{est_minutes:.0f}分钟，超过通勤上限{COMMUTE_MAX_EST_MINUTES}分钟")

    # 段落数量（过多段落=信息密度过大）
    paragraphs = [p.strip() for p in script.split('\n\n') if len(p.strip()) > 20]
    stats["paragraphs"] = len(paragraphs)
    if len(paragraphs) > 30:
        issues.append(f"段落过多({len(paragraphs)})，通勤中难以跟随")

    # 暂停/节奏标记 — 中文口播依赖。！？和段落自然断句，不强制要求 …~—
    pause_markers = len(re.findall(r'[…~—]', script))
    stats["pause_markers"] = pause_markers
    # 不做硬性检查：中文 LLM 输出几乎不使用这些符号，属于正常现象

    passed = len(issues) == 0
    return passed, {"passed": passed, "issues": issues, "stats": stats}


# ═══════════════════════════════════════════════════════════════
# 主审稿函数
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 检查6: LLM 深度审稿 (use_llm=true 时启用)
# ═══════════════════════════════════════════════════════════════

LLM_REVIEW_SYSTEM_PROMPT = """你是一个专业的播客审稿编辑。你的任务是审查一段面向屈臣氏零售管理层的日播口播稿。

## 审查维度

### 1. 口语化 (oral_style)
- 口播稿必须像人在说话，不是书面文章
- 书面语问题：大长句（>40字）、行业黑话（赋能/抓手/闭环/底层逻辑）、形容词堆砌
- 好的口语特征：短句、问句、感叹句、口语连接词（说白了/举个例子/注意了）
- 评分 0-100，60 分及格

### 2. 非照读 (not_cloning_report)
- 口播稿不能只是日报的压缩版
- 必须有独立的故事线、叙述逻辑
- 不能照搬日报的章节结构或段落
- 即使引用同一事实，表达方式必须完全不同
- 评分 0-100，70 分及格

### 3. 行动建议质量 (action_quality)
- "今天听完只做三件事"段必须具体可执行
- 不能说"关注动态""持续跟进"这种废话
- 应该包含：具体数据、渠道、时间线、负责人暗示
- 评分 0-100，60 分及格

### 4. 事实幻觉 (fact_hallucination)
- 口播稿中的所有事实、数字、日期必须能在事件池中找到依据
- 如果出现了事件池中没有的具体数据、引语、细节 → 幻觉
- 趋势性判断可以，但具体数字不可以凭空出现
- 评分 0-100（100=无幻觉），70 分及格

### 5. 通勤适用性 (commute_fitness)
- 10-15分钟通勤收听，约2000-3000字
- 结构清晰（开场→分段→结尾），每段有明确主题
- 节奏有起伏（快慢交替）而非平铺直叙
- 评分 0-100，60 分及格

## 输出格式
只输出 JSON，不要解释：
```json
{
  "oral_style": {"score": 70, "passed": true, "issues": ["书面语'综上所述'", "平均句长38字偏长"]},
  "not_cloning_report": {"score": 75, "passed": true, "issues": []},
  "action_quality": {"score": 55, "passed": false, "issues": ["第1条'关注动态'空洞无具体指标"]},
  "fact_hallucination": {"score": 85, "passed": true, "issues": ["'GMV增长30%'在事件池中未见，疑似幻觉"]},
  "commute_fitness": {"score": 80, "passed": true, "issues": []},
  "overall": {"passed": false, "summary": "行动建议过于空洞，需重写结尾段", "critical_issues": ["action_quality"]}
}
```"""


def _get_llm_client():
    """懒加载 LLM client。"""
    try:
        from skills.utils.llm_client import get_llm_client
        return get_llm_client()
    except Exception as e:
        logger.warning(f"LLM client 初始化失败: {e}")
        return None


def _build_event_summary(events: List[dict]) -> str:
    """构建事件池摘要（供 LLM 检查幻觉）。"""
    lines = []
    for i, ev in enumerate(events):
        title = ev.get("event_title") or ev.get("title") or f"事件{i+1}"
        fact = ev.get("fact") or ev.get("raw_fact") or ev.get("content", "")[:200]
        date_str = ev.get("date") or ev.get("published_at") or ""
        lines.append(f"[E{i+1}] {title}")
        if date_str:
            lines.append(f"  日期: {date_str}")
        if fact:
            lines.append(f"  事实: {fact[:200]}")
    return "\n".join(lines)


def llm_deep_review(script: str, events: List[dict], report_snippet: str = "") -> dict:
    """LLM 深度审稿。
    
    对 5 个维度分别评分，返回结构化结果。
    如果 LLM 不可用，返回空结果。
    """
    client = _get_llm_client()
    if client is None:
        return {"llm_available": False, "error": "LLM client 不可用"}

    event_summary = _build_event_summary(events)
    if not event_summary:
        event_summary = "（无事件池数据）"

    user_prompt = f"""## 待审口播稿

{script[:8000]}

## 事件池（口播稿的事实来源，检查幻觉用）

{event_summary[:4000]}

## 日报片段（检查照读用）

{report_snippet[:2000]}

请从 5 个维度审稿，只输出 JSON。"""

    try:
        result = client.chat(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=LLM_REVIEW_SYSTEM_PROMPT,
            response_format="json",
            temperature=0.2,
            max_tokens=2048,
            model="LongCat-Flash-Chat",
        )
        if result.get("ok"):
            content = result.get("content", "")
            # 尝试解析 JSON
            try:
                # 去除可能的 markdown 代码块
                json_str = content.strip()
                if json_str.startswith("```"):
                    json_str = re.sub(r'^```\w*\n', '', json_str)
                    json_str = re.sub(r'\n```$', '', json_str)
                llm_result = json.loads(json_str)
                llm_result["llm_available"] = True
                logger.info("LLM 审稿完成")
                return llm_result
            except json.JSONDecodeError:
                logger.warning(f"LLM 审稿返回非 JSON: {content[:200]}")
                return {"llm_available": True, "error": "JSON 解析失败", "raw": content[:500]}
        else:
            logger.warning(f"LLM 审稿调用失败: {result.get('error', 'unknown')}")
            return {"llm_available": True, "error": result.get("error", "LLM 调用失败")}
    except Exception as e:
        logger.warning(f"LLM 审稿异常: {e}")
        return {"llm_available": True, "error": str(e)}


def merge_rule_and_llm_reviews(rule_checks: dict, llm_result: dict) -> dict:
    """合并规则审稿和 LLM 审稿结果。
    
    LLM 审稿作为补充意见，不与规则审稿冲突。
    规则审稿发现的问题如果 LLM 也确认 → 严重度升级。
    """
    if not llm_result.get("llm_available"):
        return rule_checks

    merged = dict(rule_checks)

    # 映射：规则检查名 → LLM 维度名
    llm_dim_map = {
        "orality": "oral_style",
        "not_cloning_report": "not_cloning_report",
        "has_action_items": "action_quality",
        "no_new_facts": "fact_hallucination",
        "commute_fitness": "commute_fitness",
    }

    for rule_key, llm_key in llm_dim_map.items():
        llm_dim = llm_result.get(llm_key, {})
        if not llm_dim:
            continue

        rule_check = merged.get(rule_key, {})
        # 添加 LLM 评分
        rule_check["llm_score"] = llm_dim.get("score")
        rule_check["llm_passed"] = llm_dim.get("passed", False)
        # 合并 issues（去重）
        llm_issues = llm_dim.get("issues", [])
        existing = set(rule_check.get("issues", []))
        for issue in llm_issues:
            if issue not in existing:
                rule_check.setdefault("llm_issues", []).append(issue)

        # 如果 LLM 和规则都认为不过 → 标记为 confirmed
        if not rule_check.get("passed", True) and not llm_dim.get("passed", True):
            rule_check["confirmed"] = True

    # 添加 LLM 总评
    overall = llm_result.get("overall", {})
    merged["llm_overall"] = {
        "passed": overall.get("passed", False),
        "summary": overall.get("summary", ""),
        "critical_issues": overall.get("critical_issues", []),
    }

    return merged


def review_podcast(
    project_root: str,
    date: str,
    max_retries: int = 2,
    force: bool = False,
    use_llm: bool = False,
) -> dict:
    """审稿主入口（规则 + 可选 LLM 深度审稿）。
    
    Returns:
        {
            "ok": bool,
            "sendable_podcast": bool,
            "checks": {...},
            "llm_review": {...} if use_llm else None,
            "retries_used": int,
            "errors": [...]
        }
    """
    logger.info(f"=== Podcast Review: {date} ===")

    script = load_podcast_script(project_root, date)
    report = load_report(project_root, date)
    events = load_events(project_root, date)
    outline = load_podcast_outline(project_root, date)

    if not script:
        return {
            "ok": False,
            "sendable_podcast": False,
            "checks": {},
            "retries_used": 0,
            "errors": ["口播稿文件不存在"],
        }

    # ── 执行5项检查 ──
    oral_score, oral_issues = score_orality(script)
    similarity, sim_issues = check_report_similarity(script, report)
    action_ok, action_issues = check_action_items(script)
    novel_count, novel_issues = check_fact_novelty(script, events)
    commute_ok, commute_result = check_commute_fitness(script)

    checks = {
        "orality": {
            "passed": oral_score >= ORALITY_MIN_SCORE,
            "score": oral_score,
            "threshold": ORALITY_MIN_SCORE,
            "issues": oral_issues,
        },
        "not_cloning_report": {
            "passed": similarity <= SIMILARITY_MAX,
            "similarity": round(similarity, 3),
            "threshold": SIMILARITY_MAX,
            "issues": sim_issues,
        },
        "has_action_items": {
            "passed": action_ok,
            "issues": action_issues,
        },
        "no_new_facts": {
            "passed": novel_count <= FACT_NOVELTY_MAX,
            "novel_count": novel_count,
            "threshold": FACT_NOVELTY_MAX,
            "issues": novel_issues,
        },
        "commute_fitness": {
            "passed": commute_ok,
            "issues": commute_result.get("issues", []),
            "stats": commute_result.get("stats", {}),
        },
    }

    all_passed = all(c["passed"] for c in checks.values())

    # ── 结构完整性检查 ──
    structure_issues = []
    required_sections = [
        ("开场", ["开场", "一句话", "今天", "各位"]),
        ("平台策略", ["平台", "策略", "即时零售"]),
        ("本地生活", ["本地", "门店", "到店"]),
        ("竞对商家", ["竞对", "竞争", "对手", "商家"]),
        ("屈臣氏机会", ["屈臣氏", "机会", "机会点"]),
        ("三件事结尾", ["三件事", "只做", "行动"]),
    ]
    for section_name, keywords in required_sections:
        found = any(kw in script for kw in keywords)
        if not found:
            structure_issues.append(f"缺少结构段: {section_name}")
            all_passed = False

    checks["structure"] = {
        "passed": len(structure_issues) == 0,
        "issues": structure_issues,
    }

    # ── 大纲检查 ──
    outline_issues = []
    if outline:
        main_events = outline.get("main_events", [])
        if len(main_events) > 3:
            outline_issues.append(f"主事件超过3个({len(main_events)})")
        for i, ev in enumerate(main_events):
            required_dims = ["what", "why", "watsons_meaning", "learn_or_warn", "watch_metrics"]
            for dim in required_dims:
                if dim not in ev:
                    outline_issues.append(f"事件{i+1}缺少维度: {dim}")
        checks["outline"] = {
            "passed": len(outline_issues) == 0,
            "event_count": len(main_events),
            "issues": outline_issues,
        }
    else:
        checks["outline"] = {
            "passed": True,  # 方案C架构下不需要outline文件
            "event_count": 0,
            "issues": [],
        }

    sendable = all_passed

    # ── LLM 深度审稿（可选）──
    llm_review_result = None
    if use_llm:
        logger.info(">>> LLM 深度审稿启用")
        report_snippet = (report[:3000] + "\n...\n" + report[-500:]) if len(report) > 3500 else report
        llm_review_result = llm_deep_review(script, events, report_snippet)
        if llm_review_result.get("llm_available"):
            # 合并规则和 LLM 结果
            checks = merge_rule_and_llm_reviews(checks, llm_review_result)
            # LLM overall 作为辅助判断（不覆盖规则结果）
            llm_overall = checks.get("llm_overall", {})
            if llm_overall.get("passed") is False:
                logger.warning(f"LLM 审稿: {llm_overall.get('summary', '未通过')}")
                logger.info(f"严重问题: {llm_overall.get('critical_issues', [])}")
        else:
            logger.warning(f"LLM 审稿不可用: {llm_review_result.get('error', 'unknown')}")

    # ── 汇总 ──
    high_issues = sum(
        len(c.get("issues", []))
        for c in checks.values()
        if not c.get("passed", True)
    )

    logger.info(
        f"审稿结果: sendable={sendable}, "
        f"oral={oral_score}, sim={similarity:.2%}, "
        f"action={action_ok}, novel={novel_count}, commute={commute_ok}, "
        f"structure={len(structure_issues)==0}, outline={len(outline_issues)==0}, "
        f"总问题={high_issues}"
    )

    return {
        "ok": True,
        "sendable_podcast": sendable,
        "checks": checks,
        "llm_review": llm_review_result,
        "retries_used": 0,
        "errors": [],
    }


# ═══════════════════════════════════════════════════════════════
# CLI & import entry
# ═══════════════════════════════════════════════════════════════

def run_podcast_review(
    project_root: str,
    date: str,
    max_retries: int = 2,
    force: bool = False,
    **kwargs,
) -> dict:
    """供 import 调用的入口。保存结果到 data/logs/{date}/podcast_review.json。"""
    use_llm = kwargs.get("use_llm", True)
    result = review_podcast(project_root, date, max_retries=max_retries, force=force, use_llm=use_llm)

    # 保存审稿结果到文件
    log_dir = Path(project_root) / "data" / "logs" / date
    log_dir.mkdir(parents=True, exist_ok=True)
    review_file = log_dir / "podcast_review.json"
    review_data = {
        "date": date,
        "passed": result.get("sendable_podcast", False),
        "checks_passed": sum(1 for c in result.get("checks", {}).values()
                             if isinstance(c, dict) and c.get("passed", False)),
        "checks_total": len(result.get("checks", {})),
        "script_chars": result.get("checks", {}).get("commute_fitness", {}).get("stats", {}).get("chars", 0),
        "issues": [],
        "warnings": [],
        "errors": result.get("errors", []),
        "llm_review_available": bool(result.get("llm_review")),
    }
    # 收集所有 issues
    for check_name, check_data in result.get("checks", {}).items():
        if isinstance(check_data, dict):
            for issue in check_data.get("issues", []):
                review_data["issues"].append({
                    "check": check_name,
                    "issue": issue,
                    "severity": "high" if not check_data.get("passed", True) else "medium",
                })
    with open(review_file, "w", encoding="utf-8") as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)
    logger.info(f"审稿结果已保存: {review_file}")

    return result


# Alias for pipeline step definition compatibility
podcast_review = run_podcast_review


def main():
    parser = argparse.ArgumentParser(description="播客口播稿审稿器")
    parser.add_argument("--project-root", default=".", help="项目根目录")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD")
    parser.add_argument("--max-retries", type=int, default=2, help="最大重试次数")
    parser.add_argument("--force", action="store_true", help="强制通过（忽略问题）")
    parser.add_argument("--use-llm", type=str, default="false", choices=["true", "false"],
                        help="启用 LLM 深度审稿 (true/false)")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    result = review_podcast(
        project_root=args.project_root,
        date=args.date,
        max_retries=args.max_retries,
        force=args.force,
        use_llm=(args.use_llm == "true"),
    )

    # 保存结果
    log_dir = Path(args.project_root) / "data" / "logs" / args.date
    log_dir.mkdir(parents=True, exist_ok=True)
    result_file = log_dir / "podcast_review.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "ok": result["ok"],
        "sendable_podcast": result["sendable_podcast"],
        "checks": {
            k: v.get("passed") for k, v in result["checks"].items()
        },
        "errors": result["errors"],
    }, ensure_ascii=False, indent=2))

    sys.exit(0 if result["sendable_podcast"] or args.force else 1)


if __name__ == "__main__":
    main()
