#!/usr/bin/env python3
"""跨日事件身份合同 v2：五维身份 + 先比内容再判同源。

2026-11-02 修订（用户 P0-1 要求）：
  1. 身份维度扩展到 主体(subject) / 动作(action) / 对象(object)
     / 渠道(channel) / 地点(location)
  2. **先比较新旧内容**，再判断"同 URL"是否发生实质变化——
     同一 URL 内容更新（门店数、阶段、价格、覆盖范围）必须识别为 material_delta，
     不能因为 URL 相同就草率判为 observation_only
  3. 只有内容实质未变时，才把重复发现计为 observation

判定顺序（关键）：
  content_changed? -> material_delta / content_same -> (same_url ? observation_only : dup_by_content)
"""
import hashlib
import re
from typing import Dict, List, Optional

# ---------- 阶段 ----------
STAGE_WORDS = {
    "announced": ["宣布", "官宣", "公布", "签约", "达成合作", "拟", "计划"],
    "trial": ["试点", "内测", "灰度", "试运行", "试运营"],
    "launched": ["上线", "开卖", "开业", "落地", "启动", "投用"],
    "scaled": ["铺开", "全量", "扩张", "复制", "推广至"],
}
_STAGE_ORDER = ["announced", "trial", "launched", "scaled"]
FUTURE_MARK = ("将", "拟", "计划", "预计", "即将", "有望", "筹备", "拟于")

# ---------- 五维词表 ----------
SUBJECTS = [
    "美团闪购", "美团", "淘宝闪购", "京东秒送", "京东到家", "饿了么", "抖音小时达",
    "屈臣氏", "丝芙兰", "万宁", "妍丽", "调色师", "话梅",
    "山姆", "盒马", "奥乐齐", "沃尔玛", "永辉", "大润发", "华润万家",
    "菜鸟", "顺丰", "达达", "闪送",
    "上海家化", "珀莱雅", "贝泰妮", "华熙生物", "上美股份", "花西子",
    "完美日记", "薇诺娜", "六神", "佰草集",
    "博倩叶", "boscia", "玉泽", "润百颜", "夸迪",
    "天猫", "京东", "拼多多", "抖音", "快手", "苏宁",
]

# 动作族：动作动词 → 规范化动作名
ACTIONS = {
    "收购重启": ["收购", "并购", "重启", "易主", "接手", "接盘"],
    "接入合作": ["加入", "接入", "合作", "签约", "达成", "携手", "联手", "共建"],
    "履约配送": ["配送", "小时达", "履约", "供应链", "前置仓", "仓配", "送装",
                 "提供服务", "服务覆盖"],
    "开店闭店": ["开业", "开店", "闭店", "关店", "撤店", "入驻", "进驻", "落地"],
    "扩张下沉": ["扩张", "下沉", "布局已", "铺开", "进驻县域", "新开"],
    "上线发布": ["上线", "发布", "推出", "首发", "上新"],
    "经营数据": ["增长", "下滑", "成交额", "交易额", "份额", "同比", "环比"],
    "直播营销": ["直播", "带货", "玩妆节", "快闪", "盛典", "大促", "联名"],
    "补贴政策": ["补贴", "扶持", "政策", "减免", "优惠"],
    "价格调整": ["降价", "涨价", "提价", "调价", "折扣"],
    "组织人事": ["任免", "调任", "履职", "离职", "加盟商"],
}

OBJECTS = {
    "美妆个护": ["美妆", "个护", "护肤", "彩妆", "化妆品", "洗护", "面膜",
                 "防晒", "香水", "香氛", "口腔", "洗发", "沐浴"],
    "3C数码": ["3C", "数码", "手机", "电脑", "家电"],
    "生鲜食品": ["生鲜", "食品", "水果", "蔬菜", "肉类", "酒", "饮料"],
    "医药健康": ["医药", "药品", "保健品", "医疗器械"],
    "服饰百货": ["服饰", "服装", "鞋", "百货", "家居"],
    "母婴": ["母婴", "婴童", "奶粉", "纸尿裤"],
}

CHANNELS = {
    "即时零售": ["即时零售", "小时达", "闪购", "分钟达", "30分钟", "前置仓",
                 "闪电仓", "到家"],
    "线下门店": ["门店", "店", "专柜", "柜台", "旗舰店", "体验店"],
    "直播电商": ["直播", "直播间", "达人", "主播"],
    "会员私域": ["会员", "私域", "社群", "小程序", "订阅"],
    "传统电商": ["天猫", "淘宝", "京东", "拼多多", "电商平台"],
    "商超渠道": ["商超", "超市", "卖场", "大卖场"],
}

LOCATIONS = [
    "全国", "上海", "北京", "广州", "深圳", "杭州", "南京", "成都", "重庆",
    "武汉", "西安", "苏州", "天津", "长沙", "郑州", "青岛", "宁波",
    "江阴", "张家港", "如皋", "昆山", "义乌", "东莞", "佛山",
    "县域", "县级市", "下沉市场", "三四线",
]


def _first_appearing(cands, text: str) -> str:
    """返回在 text 中最早出现的候选词（按字符位置）。"""
    best, pos = "", len(text) + 1
    for c in cands:
        i = text.find(c)
        if 0 <= i < pos:
            best, pos = c, i
    return best


def _hits(text: str, words) -> List[str]:
    return [w for w in words if w in text]


def detect_stage(text: str) -> str:
    """阶段判定。未来时态优先 -> announced（尚未落地）。"""
    t = text or ""
    if not t:
        return "unknown"
    has_future = any(w in t for w in FUTURE_MARK)
    has_trial = any(w in t for w in STAGE_WORDS["trial"])
    has_launch = any(w in t for w in STAGE_WORDS["launched"])
    has_announce = any(w in t for w in STAGE_WORDS["announced"])
    has_scaled = any(w in t for w in STAGE_WORDS["scaled"])

    if has_trial:
        return "trial"
    if has_future and not has_launch:
        return "announced"
    if has_future and has_launch and not re.search(r"(已|正式)(上线|开业|落地)", t):
        return "announced"
    if has_launch:
        return "launched"
    if has_scaled:
        return "scaled"
    if has_announce:
        return "announced"
    return "unknown"


# 动作族判定优先级：越是"结果性"的动作越优先，避免
# "菜鸟加入/参与淘宝闪购提供配送服务" 因先命中"加入"而被拆成两个事件。
# 优先级原则：业务"主动作"优先于修饰性动作。
# 例如"菜鸟为淘宝闪购提供配送服务并计划拓展重点城市"，
# 主动作是履约配送，"计划拓展"只是附带计划，不能因此拆成"扩张下沉"。
# "经营数据"放在较后，避免"直播工具上线带动增长"被误归为数据类。
ACTION_PRIORITY = ["收购重启", "履约配送", "开店闭店", "直播营销", "上线发布",
                   "补贴政策", "价格调整", "组织人事", "接入合作",
                   "扩张下沉", "经营数据"]


def _primary(map_dict: Dict[str, List[str]], text: str) -> str:
    """按优先级取动作族；对 ACTIONS 使用专门优先级，避免同事件被拆。"""
    if map_dict is ACTIONS:
        for name in ACTION_PRIORITY:
            if any(w in text for w in ACTIONS[name]):
                return name
        return ""
    for name, words in map_dict.items():
        if any(w in text for w in words):
            return name
    return ""


def content_hash(text: str) -> str:
    norm = re.sub(r"\s+", "", str(text or ""))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def extract_facts(text: str) -> Dict[str, str]:
    """抽取可比较的量化事实，用于判断内容是否实质变化。"""
    t = text or ""
    out: Dict[str, str] = {}
    m = re.search(r"(?:全国|累计|门店总数|门店数)[^。；]{0,10}?(\d[\d,]*)\s*(?:家|间)", t)
    if m:
        out["store_count"] = m.group(1).replace(",", "")
    m = re.search(r"(\d[\d,]*)\s*(?:万)?(?:单|订单)", t)
    if m:
        out["order_count"] = m.group(1).replace(",", "")
    m = re.search(r"覆盖\s*(\d+)\s*座?城|(\d+)\s*座城市", t)
    if m:
        out["city_count"] = m.group(1) or m.group(2)
    m = re.search(r"(\d+(?:\.\d+)?)\s*元", t)
    if m:
        out["price"] = m.group(1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        out["percent"] = m.group(1)
    return out


def event_profile(event: Dict) -> Dict:
    """五维画像。"""
    title = str(event.get("event_title") or "")
    fact = str(event.get("fact") or "")
    text = (title + " " + fact).strip()
    return {
        # 主体取"文中最先出现"的那个：中文新闻通常主语在前，
        # 若按词表顺序取会把"苏宁易购与美团合作"误判为美团主体。
        "subject": _first_appearing(SUBJECTS, text),
        "action": _primary(ACTIONS, text),
        "object_set": tuple(sorted(n for n, ws in OBJECTS.items()
                                   if any(w in text for w in ws))),
        "object": _primary(OBJECTS, text),
        "channel": _primary(CHANNELS, text),
        "location": next((l for l in LOCATIONS if l in text), ""),
        "stage": detect_stage(text),
        "facts": extract_facts(text),
        "content_hash": content_hash(fact or title),
        "url": str(event.get("source_url") or ""),
        "text": text,
    }


def event_identity(event: Dict) -> str:
    """身份键：主体|动作|对象|渠道|地点（不含阶段与数字）。"""
    p = event_profile(event)
    # 对象用集合（多品类时不因"先命中哪个"而分裂）；地点不再进入身份，
    # 因为同一全国性动作的报道常只提部分城市。
    # 身份只用 主体|动作 两级。对象/渠道/地点作为"约束"在 compare() 中比对：
    # 因为同一动作的重复报道常详略不一（一条提品类、另一条不提），
    # 若把对象纳入身份键会把同一事件拆成多条（实测"菜鸟"被拆成2条）。
    return "|".join([p["subject"] or "?", p["action"] or "?"])


def _facts_changed(old_f: Dict, new_f: Dict) -> Optional[str]:
    """返回发生变化的字段名，无变化返回 None。"""
    for k in ("store_count", "order_count", "city_count", "price", "percent"):
        if k in old_f and k in new_f and old_f[k] != new_f[k]:
            return k
    return None


def compare(old_prof: Dict, new_prof: Dict) -> Dict:
    """先比内容，再判同源。这是 v2 的核心顺序。"""
    # 1) 内容完全相同 → 同稿
    if old_prof["content_hash"] == new_prof["content_hash"]:
        return {"content_changed": False, "reason": "identical_content"}

    # 2) 先判"是不是同一件事"：对象/渠道互斥 → 不同事件（distinct）
    #    必须在数字比较之前，否则"美妆增长100% vs 3C增长200%"会被
    #    误判为同一事件的数字更新。
    oset_a, oset_b = set(old_prof.get("object_set") or ()), set(new_prof.get("object_set") or ())
    if oset_a and oset_b and not (oset_a & oset_b):
        return {"content_changed": True, "kind": "distinct",
                "reason": "different_object_disjoint"}
    ca, cb = old_prof.get("channel"), new_prof.get("channel")
    if ca and cb and ca != cb:
        return {"content_changed": True, "kind": "distinct",
                "reason": "different_channel"}
    # 地点互斥（不同城市开店）也是不同事件；但"全国"与个别城市属包含关系，不算互斥
    la, lb = old_prof.get("location"), new_prof.get("location")
    if la and lb and la != lb and "全国" not in (la, lb) and "县域" not in (la, lb):
        return {"content_changed": True, "kind": "distinct",
                "reason": "different_location"}

    # 3) 同一件事：看量化事实是否变化
    changed = _facts_changed(old_prof["facts"], new_prof["facts"])
    if changed:
        return {"content_changed": True, "reason": f"fact_changed:{changed}",
                "field": changed}

    # 3) 阶段推进也算实质变化
    so, sn = old_prof["stage"], new_prof["stage"]
    if so in _STAGE_ORDER and sn in _STAGE_ORDER:
        if _STAGE_ORDER.index(sn) > _STAGE_ORDER.index(so):
            return {"content_changed": True,
                    "reason": f"stage_advanced:{so}->{sn}"}

    # 5) 文本有差异但无实质变化（改写/转载）
    return {"content_changed": False, "reason": "paraphrase_no_material_change"}


def extraction_quality(event: Dict) -> str:
    """抽取质量标记：llm > rule_fallback。

    金标准核查发现：同一 URL 可能同时存在「规则兜底」抽取（LLM 失败降级）
    与「LLM」抽取两条记录，若按事件身份合并会留下低质量的那条，
    按 URL 合并又会漏掉同文多事件。正确做法是：同 URL 同身份时，
    以 LLM 抽取结果为准，规则兜底版本丢弃。
    """
    fact = str(event.get("fact") or "")
    if "[规则兜底]" in fact:
        return "rule_fallback"
    if event.get("llm_extracted") is False:
        return "rule_fallback"
    if str(event.get("extraction_method") or "").lower() == "rule_fallback":
        return "rule_fallback"
    return "llm"


def is_lower_quality_dup(ledger: Dict[str, Dict], event: Dict) -> bool:
    """该事件是否为「同 URL 同身份的降级重复」。"""
    prof = event_profile(event)
    ident = event_identity(event)

    prev = ledger.get(ident)
    if not prev:
        return False
    prev_prof = prev.get("profile") or {}
    if prev_prof.get("url") != prof["url"]:
        return False
    prev_q = prev.get("quality") or "llm"
    cur_q = extraction_quality(event)
    # 已有 LLM 版本，当前是规则兜底 → 丢弃当前
    if prev_q == "llm" and cur_q == "rule_fallback":
        return True
    return False


def evaluate(ledger: Dict[str, Dict], event: Dict) -> Dict:
    """返回 {action, identity, reason}。action ∈ new/observation_only/material_delta。"""
    prof = event_profile(event)
    ident = event_identity(event)

    # 同 URL 同身份时，规则兜底版本让位于 LLM 版本（金标准核查结论）
    if is_lower_quality_dup(ledger, event):
        return {"action": "observation_only", "identity": ident,
                "reason": "lower_quality_duplicate", "profile": prof,
                "url": prof["url"], "content_hash": prof["content_hash"],
                "event_title": event.get("event_title"), "fact": event.get("fact")}

    prev = ledger.get(ident)

    if prev is None:
        return {"action": "new", "identity": ident, "reason": "first_seen",
                "profile": prof, "url": prof["url"],
                "content_hash": prof["content_hash"],
                "event_title": event.get("event_title"),
                "fact": event.get("fact"),
                "quality": extraction_quality(event)}

    # 兼容旧账本结构 {url, content_hash, event_title, fact}
    if not prev.get("profile"):
        prev = {"profile": event_profile({"event_title": prev.get("event_title", ""),
                                          "fact": prev.get("fact", ""),
                                          "source_url": prev.get("url", "")})}

    # 关键：先比内容，不因 URL 相同就判重复
    cmp = compare(prev.get("profile") or {}, prof)
    if cmp.get("kind") == "distinct":
        # 同主体同动作但对象/渠道互斥 → 是另一件事，用派生身份区分
        ident = ident + "#" + (",".join(prof.get("object_set") or ()) or
                               (prof.get("channel") or "alt"))
        prev = ledger.get(ident)
        if prev is None:
            return {"action": "new", "identity": ident, "reason": cmp["reason"],
                    "profile": prof, "url": prof["url"],
                    "content_hash": prof["content_hash"],
                    "event_title": event.get("event_title"), "fact": event.get("fact")}
        cmp = compare(prev.get("profile") or {}, prof)
    if cmp["content_changed"]:
        return {"action": "material_delta", "identity": ident,
                "reason": cmp["reason"], "profile": prof, "url": prof["url"],
                "content_hash": prof["content_hash"],
                "event_title": event.get("event_title"),
                "fact": event.get("fact"),
                "quality": extraction_quality(event)}

    if prof["url"] and prev.get("profile", {}).get("url") == prof["url"]:
        return {"action": "observation_only", "identity": ident,
                "reason": "same_url_same_content", "profile": prof,
                "url": prof["url"], "content_hash": prof["content_hash"],
                "event_title": event.get("event_title"),
                "fact": event.get("fact"),
                "quality": extraction_quality(event)}
    return {"action": "observation_only", "identity": ident,
            "reason": "dup_content_other_source", "profile": prof,
            "url": prof["url"], "content_hash": prof["content_hash"],
            "event_title": event.get("event_title"),
            "fact": event.get("fact")}


def is_material_delta(old: Dict, new: Dict) -> bool:
    """兼容旧测试入口。"""
    a, b = event_profile(old), event_profile(new)
    if a["content_hash"] == b["content_hash"]:
        return False
    return compare(a, b)["content_changed"]


# 兼容旧接口：extract_numbers == extract_facts
extract_numbers = extract_facts
