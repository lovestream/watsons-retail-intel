#!/usr/bin/env python3
"""S3-2 跨日事件身份合同：区分「同稿重复发现」与「新增业务事实」。

规则（任务书 S3-2/S3-3）：
  - 相同 URL / 内容 hash 跨日重复 → 只增加 observation，不产生新事件
  - 主体+动作+对象+阶段相同且无新事实 → 不生成 reportable event
  - 出现真实新增：门店数变化、价格变化、履约范围、实施阶段推进 → material_delta
"""
import hashlib
import re
from typing import Dict, List, Optional

# 阶段词：宣布 -> 试点 -> 上线 -> 铺开
STAGE_WORDS = {
    "announced": ["宣布", "官宣", "公布", "签约", "达成合作", "拟", "计划"],
    "trial": ["试点", "内测", "灰度", "试运行", "试运营"],
    "launched": ["上线", "开卖", "开业", "落地", "启动"],
    "scaled": ["铺开", "全国", "全量", "扩张", "复制"],
}
_STAGE_ORDER = ["announced", "trial", "launched", "scaled"]


# 未来时态标记：出现时，紧随其后的"上线/开业"等尚未发生
FUTURE_MARK = ("将", "拟", "计划", "预计", "即将", "有望", "筹备")


def detect_stage(text: str) -> str:
    """判定事件所处阶段。

    2026-11-02 修正：原文"宣布将上线X"同时含"宣布"与"上线"，
    若简单取最后命中的阶段会误判为 launched，实际仍是 announced。
    规则：出现宣布类词，且动作被未来标记修饰 → announced（尚未落地）。
    """
    t = text or ""
    if not t:
        return "unknown"

    # 宣布 + 未来时态 → 仍在 announced 阶段
    has_announce = any(w in t for w in STAGE_WORDS["announced"])
    has_future = any(w in t for w in FUTURE_MARK)
    has_trial = any(w in t for w in STAGE_WORDS["trial"])
    has_launch = any(w in t for w in STAGE_WORDS["launched"])

    if has_future and not has_trial:
        # "将上线/拟开业/计划启动" 都是未发生
        return "announced"
    if has_trial:
        return "trial"
    if has_launch:
        return "launched"
    if has_announce:
        return "announced"
    if any(w in t for w in STAGE_WORDS["scaled"]):
        return "scaled"
    return "unknown"


def content_hash(text: str) -> str:
    """正文内容指纹（去空白后 sha1 前16位）。"""
    norm = re.sub(r"\s+", "", str(text or ""))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def extract_numbers(text: str) -> Dict[str, str]:
    """抽取关键业务数字：门店数 / 单量 / 价格 / 覆盖城市。"""
    t = text or ""
    out = {}
    m = re.search(r"(全国|累计|门店总数)[^。；]{0,12}?(\d[\d,]*)\s*(家|间|门店)", t)
    if m:
        out["store_count"] = m.group(2).replace(",", "")
    m = re.search(r"(\d[\d,]*)\s*(万)?单", t)
    if m:
        out["order_count"] = m.group(1).replace(",", "") + (m.group(2) or "")
    m = re.search(r"(\d+)\s*座?城市|覆盖\s*(\d+)\s*城", t)
    if m:
        out["city_count"] = m.group(1) or m.group(2)
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", t)
    if m:
        out["price"] = m.group(1)
    return out


def event_identity(event: Dict) -> str:
    """事件身份：主体 + 动作族 + 阶段（不含数字，数字用于判定 material_delta）。"""
    title = str(event.get("event_title") or "")
    fact = str(event.get("fact") or "")
    text = title + " " + fact[:120]
    subjects = ["菜鸟", "美团闪购", "淘宝闪购", "天猫", "京东", "抖音",
                "山姆", "盒马", "奥乐齐", "屈臣氏", "丝芙兰", "苏宁"]
    subj = next((s for s in subjects if s in text), "")
    stage = detect_stage(text)
    # 动作族
    fam = ""
    for name, words in {
        "接入服务": ["加入", "接入", "提供", "小时达", "配送", "供应链"],
        "开店扩张": ["开业", "开店", "入驻", "落地", "下沉", "扩张", "布局"],
        "直播": ["直播", "带货"],
        "营销活动": ["玩妆节", "快闪", "盛典", "节"],
        "补贴政策": ["补贴", "政策", "扶持"],
    }.items():
        if any(w in text for w in words):
            fam = name
            break
    return f"{subj}|{fam}|{stage}"


def is_material_delta(old: Dict, new: Dict) -> bool:
    """同一身份下，是否出现有证据的重要变化。"""
    o = extract_numbers((old.get("event_title") or "") + (old.get("fact") or ""))
    n = extract_numbers((new.get("event_title") or "") + (new.get("fact") or ""))
    # 数字变化即实质更新
    for k in ("store_count", "order_count", "city_count", "price"):
        if k in o and k in n and o[k] != n[k]:
            return True
    # 阶段推进
    so, sn = detect_stage(str(old.get("fact") or "")), detect_stage(str(new.get("fact") or ""))
    if so in _STAGE_ORDER and sn in _STAGE_ORDER:
        if _STAGE_ORDER.index(sn) > _STAGE_ORDER.index(so):
            return True
    return False


def evaluate(seen_ledger: Dict[str, Dict], event: Dict) -> Dict:
    """返回该事件应如何处理。

    Returns {'action': 'new'|'observation_only'|'material_delta', 'identity': ...}
    """
    ident = event_identity(event)
    url = event.get("source_url") or ""
    chash = content_hash(str(event.get("fact") or ""))

    prev = seen_ledger.get(ident)
    if prev is None:
        return {"action": "new", "identity": ident,
                "reason": "first_seen", "url": url, "content_hash": chash}

    # 完全同稿（URL 或内容指纹一致）→ 只是又一次观测
    if (url and prev.get("url") == url) or prev.get("content_hash") == chash:
        return {"action": "observation_only", "identity": ident,
                "reason": "same_article_reseen", "url": url, "content_hash": chash}

    # 同主体同动作同阶段，但数字/阶段有真实变化
    if is_material_delta(prev, event):
        return {"action": "material_delta", "identity": ident,
                "reason": "business_fact_changed", "url": url, "content_hash": chash}

    return {"action": "observation_only", "identity": ident,
            "reason": "no_new_material_fact", "url": url, "content_hash": chash}
