"""Single source of truth for mainland beauty/personal-care retail intelligence.

Hard topic rejection precedes keyword score, source priority, model suggestions,
and event fallback. Article admission is not event verification. No LLM/network.
"""
from __future__ import annotations
import re
from typing import Any, Dict

VERSION = "scope-v1.0"
WATSONS = ("屈臣氏", "watsons")
DIRECT_RIVALS = ("丝芙兰", "sephora", "万宁", "mannings", "妍丽",
                 "wow colour", "调色师", "话梅", "harmay")
ADJACENT_RIVALS = ("名创优品", "miniso", "kk集团")
INSTANT = ("即时零售", "美团闪购", "京东秒送", "京东到家", "淘宝闪购",
           "抖音小时达", "天猫小时达", "闪电仓", "前置仓",
           "即时配送", "门店履约", "小时达", "万物到家", "同城零售")
BEAUTY = ("美妆", "个护", "化妆品", "护肤", "彩妆", "洗护", "防晒",
          "面膜", "精华液", "洁面", "洗发水", "护发", "沐浴露",
          "身体护理", "香水", "香氛", "口腔护理", "牙膏", "牙刷",
          "女性护理", "卫生巾", "私护", "美妆集合店", "化妆品零售")
CHANNEL = ("天猫", "京东", "抖音电商", "京东自营", "天猫超市", "小红书电商")
MECHANICS = ("闪电仓", "前置仓", "仓配", "履约", "入驻", "招商",
             "商家佣金", "平台佣金", "配送费", "配送时效", "到家",
             "小时达", "即时配送", "门店配送", "运力", "商家规则")
BUSINESS_ACTIONS = (
    "开店", "新店", "开业", "新开", "闭店", "关店", "关停", "扩店", "门店扩张",
    "拓店", "开拓", "扩张", "布局", "入驻", "进驻", "上架", "上线", "推出",
    "发布", "升级", "改造", "转型", "迭代", "调整", "落地", "试点",
    "合作", "联名", "共创", "共建", "签约", "启动", "加码", "实施",
    "接入", "上线", "招商", "投放", "补贴", "降价", "涨价", "定价",
    "佣金", "费率", "履约", "配送", "新规", "规则", "政策",
    "会员", "私域", "渠道", "新产品", "新品", "首店", "首发",
    "召回", "抽检", "备案", "处罚", "整改", "收购", "并购",
    "控股", "经销", "分销", "供货", "数字化", "全渠道", "新模式",
)
# Specific unrelated *primary* story topics always outrank incidental
# body mentions such as "屈臣氏", "美妆". Do not use as whole-body veto.
PRIMARY_EXCLUDE = {
    "food_icecream": r"野人先生|冰淇淋|冰激凌|雪糕|冰品|乳品冰激凌",
    "food_restaurant": r"寿司郎|餐饮|餐厅|饭店|火锅|茶饮|奶茶|咖啡|烧烤|烘焙|餐馆|餐企|快餐|茶咖",
    "recruitment": r"招聘|高薪|猎聘|求职|招工|校招|社招|人才招聘|首席人力|人力资源总监|\bCHO\b|薪酬",
    "sports": r"健身|体育赛事|运动比赛|球赛|运动健身|马拉松|健身房",
    "finance": r"股价|股票|股市|港股|A股|美股|股民|涨停|跌停|股价上涨|股价下跌|股价涨|股价跌|开盘|收盘|市值|股票回购|券商|研报|证券|分析师评级|增持评级|减持评级|IPO|融资轮|定增|债券|财报|季报|年报|业绩预告|资本市场|估值|上市进程",
    "vendor_report": r"SAP.{0,12}报告|ERP.{0,12}报告|软件市场报告|咨询报告|研究报告|行业白皮书|券商报告",
}
# A user may explicitly retain individual editorial exceptions after review.
# Never turn an exception into permission for adjacent supermarket articles.
CURATED = (
    (r"山姆.*奥乐齐.*盒马.*县级", "零售圈", "2026-10-28",
     "user_curated_adjacent_format"),
)
REGION_EXCLUDE = r"仅在美国|美国门店|仅在欧洲|欧洲门店|仅在香港|香港市场|仅在台湾|台湾市场"
NON_EVENT = r"排行榜|盘点|历史回顾|复盘|一文看懂|怎么看|观察报告|投资策略|股市行情|募集说明书"


def _as_text(v: Any) -> str:
    if isinstance(v, list):
        return " ".join(str(x) for x in v if x)
    return str(v or "")


def _hit(text: str, words: tuple) -> bool:
    return any(w in text for w in words)


def _match(pattern: str, text: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def _result(decision: str, reason: str, scope: str = "", action: str = "",
            evidence: str = "") -> Dict[str, str]:
    return {
        "version": VERSION, "decision": decision, "reason_code": reason,
        "scope": scope, "action": action, "evidence_excerpt": evidence[:180],
    }


def classify_business_scope(article: dict, *, event_level: bool = False) -> Dict[str, str]:
    """Return ACCEPT / REVIEW / REJECT with an auditable reason.

    The headline and short original lead determine the *primary* topic.
    Full body cannot unlock an excluded subject through incidental terms.
    """
    title = _as_text(article.get("event_title") if event_level else article.get("title")).strip()
    lead = _as_text(article.get("fact") if event_level else article.get("summary")).strip()[:250]
    if not lead:
        lead = _as_text(article.get("evidence_text") if event_level else article.get("description")).strip()[:250]
    focus = (title + " " + lead).lower()
    title_low = title.lower()
    source = _as_text(article.get("source_name"))
    pub = _as_text(article.get("published_at"))[:10]
    if not title:
        return _result("REVIEW", "missing_title")
    for reason, pattern in PRIMARY_EXCLUDE.items():
        if _match(pattern, title):
            return _result("REJECT", reason, evidence=title)
        # Reject near-lead exclusions where the headline itself has no
        # direct named Watsons/beauty/instant-retail subject.
        if _match(pattern, lead) and not (
            _hit(title_low, WATSONS) or _hit(title_low, DIRECT_RIVALS)
            or _hit(title_low, BEAUTY) or _hit(title_low, INSTANT)
        ):
            return _result("REJECT", reason, evidence=lead)
    if _match(REGION_EXCLUDE, title):
        return _result("REJECT", "overseas_only", evidence=title)

    for pattern, curated_source, curated_date, reason in CURATED:
        if _match(pattern, title) and curated_source in source and pub == curated_date:
            return _result("ACCEPT", reason, "explicit_editorial_exception",
                           "market_expansion", title)

    watsons = _hit(focus, WATSONS)
    direct_rival = _hit(focus, DIRECT_RIVALS)
    adjacent_rival = _hit(focus, ADJACENT_RIVALS)
    instant = _hit(focus, INSTANT)
    beauty = _hit(focus, BEAUTY)
    channel = _hit(focus, CHANNEL)
    mechanic = _hit(focus, MECHANICS)
    action = next((w for w in BUSINESS_ACTIONS if w in focus), "")
    # "发布行业报告" by itself is not a retail operating action.
    report_only = _match(NON_EVENT, title) or (
        _match(r"发布.{0,12}(?:报告|指数|白皮书|榜单|排行榜|研报)", title)
        and not _match(r"开店|上新|升级|上线|合作|转型|入驻|规则|费率", title)
    )
    if report_only:
        return _result("REJECT", "no_operating_event", evidence=title)
    if not (watsons or direct_rival or instant or beauty or (adjacent_rival and beauty)):
        return _result("REJECT", "out_of_scope", evidence=title)
    if not action:
        # Narrow review queue only for named core businesses, never database.
        if watsons or direct_rival:
            return _result("REVIEW", "core_actor_no_specific_action",
                           "watsons" if watsons else "direct_rival", evidence=title)
        return _result("REJECT", "no_operating_action", evidence=title)

    if watsons:
        return _result("ACCEPT", "watsons_operating_action", "watsons", action, title)
    if direct_rival:
        return _result("ACCEPT", "competitor_operating_action", "direct_competitor", action, title)
    if beauty and (instant or channel):
        return _result("ACCEPT", "beauty_channel_action", "beauty_x_channel", action, title)
    if instant and mechanic:
        return _result("ACCEPT", "instant_retail_mechanism", "instant_retail", action, title)
    if beauty and (adjacent_rival or _match(r"品牌|门店|上市新品|产品|零售|渠道|工厂|企业|公司", focus)):
        return _result("ACCEPT", "beauty_operating_action", "beauty_personal_care", action, title)
    # Instant retail action without merchant/fulfillment implications can be
    # product-adjacent noise. Beauty without actor/channel can be generic copy.
    return _result("REVIEW", "scope_requires_evidence",
                   "instant_retail" if instant else "beauty_personal_care",
                   action, title)
