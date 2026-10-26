#!/usr/bin/env python3
"""PASS / UNKNOWN / BLOCK prefilter, inspired by AIHOT's conservative intake.

This module is deliberately *advisory* at first.  It annotates every filtered
article with a tri-state decision and a reason, but never overrides the
existing hard gates.  That lets the SelectBench metrics prove whether the
model is safe before a later release enables it as a routing decision.

AIHOT reference: KKKKhazix/AIHOT @ ce8348477438cce2b417c1403290a511c812a7c5
  - docs/selection.md: PASS / UNKNOWN / BLOCK, conservative blocking
  - packages/backend/src/sources/filters.ts
  - packages/backend/src/admin/selectbench.ts
  - industry/prompts/prefilter.md

Design choices for Watsons:
  * PASS  = strong instant-retail OR personal-care/beauty business signal.
  * UNKNOWN = not enough evidence to decide; keep for review, never silently
    throw away a possibly relevant fact.
  * BLOCK = only clear out-of-scope content: stock/capital-market, jobs,
    entertainment/sports, overseas-only, empty/advertorial spam.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

PASS = "PASS"
UNKNOWN = "UNKNOWN"
BLOCK = "BLOCK"

_STRONG_SIGNALS = [
    "即时零售", "美团闪购", "京东秒送", "京东到家", "淘宝闪购", "饿了么",
    "抖音小时达", "前置仓", "闪电仓", "屈臣氏", "watsons",
    "美妆", "个护", "护肤", "彩妆", "洗护", "防晒", "面膜", "香水",
    "化妆品", "个人护理", "口腔护理", "身体护理", "男士护理",
]
_WEAK_SIGNALS = [
    "零售", "电商", "平台", "门店", "品牌", "消费", "快消", "渠道",
    "上新", "促销", "大促", "履约", "配送", "O2O",
]
_ACTION_SIGNALS = [
    "上线", "入驻", "开城", "扩城", "补贴", "降价", "提价", "发布",
    "合作", "收购", "融资", "关店", "开店", "调整", "试点", "推出",
    "增长", "下滑", "亏损", "盈利", "签约", "招商", "上新", "焕新",
    "加码", "投入", "扩城", "开城", "进驻", "布局",
]
_STOCK_RE = re.compile(
    r"涨停|跌停|股价|市值|买入评级|增持|减持|研报|目标价|盈利预测|"
    r"估值|PE\b|PB\b|A股|港股|美股|收盘|开盘|主力资金|龙虎榜|"
    r"续创历史新高|创历史新高|券商|分析师|投资建议"
)
_BLOCK_CONTEXT = re.compile(
    r"招聘|求职|简历|面试|招生|培训课程|优惠券领取|抽奖|"
    r"娱乐八卦|明星绯闻|体育赛事|彩票|电影票房|电视剧|综艺"
)
_OVERSEAS_ONLY = re.compile(
    r"香港|台湾|澳门|新加坡|马来西亚|日本|韩国|美国|欧洲|英国|法国|"
    r"德国|澳洲|加拿大|海外市场"
)
_MAINLAND = re.compile(
    r"中国|中国大陆|内地|全国|上海|北京|广州|深圳|杭州|成都|武汉|南京|"
    r"重庆|西安|苏州|长沙|郑州|天津|宁波|青岛|合肥|福州|厦门"
)
_ADVERTORIAL = re.compile(
    r"点击领取|扫码|广告|推广|软文|种草|好物推荐|优惠券|限时抢购"
)
_OUT_VERTICALS = re.compile(
    r"乘用车|新能源车|汽车销量|车企|羽绒服|服饰品牌|服装品牌|鞋服|"
    r"安踏|李宁|优衣库|波司登|海澜之家|太平鸟"
)


def _remove_negated(text: str) -> str:
    """Do not let "未涉及即时零售/美妆" create a positive signal."""
    return re.sub(
        r"(?:未|尚未|不含|并非|不是|没有|无)(?:涉及|披露|包含|提及|布局|开展)?\s*"
        r"(?:即时零售|美妆|个护|护肤|彩妆|美团闪购|京东秒送|淘宝闪购)"
        r"(?:品类|业务|动作|或美妆|或即时零售)?",
        "", text)


def _text(article: Dict[str, Any]) -> str:
    return " ".join(str(article.get(k) or "") for k in
                    ("title", "summary", "content", "description")).strip()


def _domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", str(url or ""))
    return m.group(1).lower() if m else ""


def classify_candidate(article: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``{label, reason, signals, domain, review_queue}``.

    Conservative rule: a stock-market article is BLOCK even if it mentions
    即时零售 incidentally; a beauty word alone is UNKNOWN, not PASS.
    """
    title = str(article.get("title") or "")
    text = _text(article)
    compact = re.sub(r"\s+", "", text)
    domain = _domain(str(article.get("source_url") or article.get("url") or ""))

    if len(compact) < 8:
        early_strong = [s for s in _STRONG_SIGNALS
                        if s.lower() in text.lower()]
        if early_strong:
            return _result(UNKNOWN, "强信号但正文不足，进入复核队列",
                           early_strong[:5], domain)
        return _result(BLOCK, "内容为空或过短", [], domain)
    if _STOCK_RE.search(text):
        return _result(BLOCK, "资本市场/研报内容，非经营情报", ["stock"], domain)
    if _BLOCK_CONTEXT.search(text):
        return _result(BLOCK, "招聘/娱乐/体育等非经营内容", ["non_business"], domain)
    if _OUT_VERTICALS.search(text) and not any(s in text for s in _STRONG_SIGNALS):
        return _result(BLOCK, "汽车/服饰等非屈臣氏经营域", ["out_of_domain"], domain)
    if _ADVERTORIAL.search(text) and not any(s in text for s in _STRONG_SIGNALS):
        return _result(BLOCK, "广告/导购内容，无经营信号", ["advertorial"], domain)
    # 海外-only: require a mainland anchor.  A mainland article may mention
    # Hong Kong in passing; that is not an overseas-only subject.
    if _OVERSEAS_ONLY.search(text) and not _MAINLAND.search(text):
        return _result(BLOCK, "港台/海外主体，非大陆经营情报", ["overseas"], domain)

    clean_text = _remove_negated(text)
    strong = [s for s in _STRONG_SIGNALS if s.lower() in clean_text.lower()]
    weak = [s for s in _WEAK_SIGNALS if s.lower() in clean_text.lower()]
    actions = [s for s in _ACTION_SIGNALS if s in clean_text]
    if strong and actions:
        return _result(PASS, "强域内信号 + 经营动作", strong[:5] + actions[:3],
                       domain)
    if strong and len(re.sub(r"\s+", "", clean_text)) >= 45:
        return _result(PASS, "强域内信号（正文充足）", strong[:5], domain)
    if strong:
        return _result(UNKNOWN, "强信号但正文不足，进入人工/LLM复核队列",
                       strong[:5], domain)
    if weak and actions:
        return _result(UNKNOWN, "泛零售信号，缺少即时零售/美妆强锚点",
                       weak[:3] + actions[:2], domain)
    return _result(UNKNOWN, "证据不足，保留复核，不静默丢弃", weak[:3], domain)


def _result(label: str, reason: str, signals: List[str], domain: str) -> Dict[str, Any]:
    return {
        "label": label,
        "reason": reason,
        "signals": signals,
        "domain": domain,
        "review_queue": label == UNKNOWN,
        "model": "rules-tristate-v1",
        "reference": "KKKKhazix/AIHOT@ce8348477",
    }


def annotate_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in articles:
        item = dict(a)
        item["tri_state"] = classify_candidate(item)
        out.append(item)
    return out
