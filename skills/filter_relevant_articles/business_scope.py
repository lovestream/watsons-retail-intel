"""One authoritative business-scope gate for Watsons retail intelligence.

Scope: mainland Watsons, personal care/beauty retail operations, and directly
relevant instant-retail channel/competitor operating decisions. Not stock news,
generic consumer brands, recruitment, restaurant, fitness, or broad consulting.

The gate checks the SUBJECT of the article (title+summary), not arbitrary
co-occurring keywords deep in its body. It is intentionally conservative at the
article stage: uncertain high-value material goes to reference, not main.
"""
import re
from typing import Dict

WATSONS = ("屈臣氏", "watsons")
BEAUTY = (
    "美妆", "个护", "护肤", "彩妆", "化妆品", "面膜", "防晒", "美白", "洗护",
    "洗发", "护发", "护发素", "沐浴", "洗面奶", "洁面", "香氛", "香水", "口腔护理",
    "牙膏", "牙刷", "身体护理", "洗护用品", "个人护理", "母婴护理", "防脱",
    "美容", "眼霜", "精华液", "护肤品", "面部护理", "卸妆", "洗衣液",
)
BEAUTY_RETAILERS = (
    "丝芙兰", "sephora", "万宁", "mannings", "妍丽", "调色师", "话梅",
    "harmay", "only write", "wow colour", "美妆集合店", "美妆零售",
)
# Platforms and channel infrastructure. "Hema county expansion" is an
# explicitly user-selected example of a retailer's operating/channel move.
CHANNELS = (
    "即时零售", "美团闪购", "淘宝闪购", "京东秒送", "京东到家", "小时达",
    "抖音小时达", "前置仓", "闪电仓", "到家业务", "同城零售",
    "盒马", "山姆", "奥乐齐", "aldi", "盒马鲜生", "零售渠道",
    "履约配送", "到家配送", "线上线下融合", "o2o",
)
OPERATIONS = (
    "上线", "上新", "入驻", "合作", "签约", "开店", "门店", "开业",
    "布局", "拓展", "扩张", "升级", "改造", "调整", "调改", "共创",
    "供应链", "履约", "配送", "渠道", "会员", "价格", "定价",
    "促销", "营销", "服务", "新品", "战略", "试点", "落地", "运营",
    "县级市场", "县域", "下沉市场", "闭店", "关店", "品类",
)
# Topic-specific blockers; matched against the article's subject, NOT the
# full body, which may contain a generic sector comparison.
NOISE_SUBJECT = (
    "招聘", "求职", "岗位", "猎头", "年薪", "薪资", "cho", "职场",
    "冰淇淋", "冰激凌", "雪糕", "野人先生", "茶饮", "奶茶", "寿司郎",
    "寿司", "火锅", "餐饮", "咖啡", "健身", "运动健身", "私教",
    "汽车", "半导体", "芯片", "机器人", "地产",
)
# Do not block company names with 股份, or operational numeric data.
STOCK_SUBJECT = (
    "股价", "涨停", "跌停", "股市", "股票", "a股", "港股", "美股",
    "行情", "股民", "市值", "市盈率", "市净率", "目标价",
    "买入评级", "卖出评级", "券商研报", "机构研报", "业绩预告",
    "财报", "季报", "年报", "净利润", "扣非净利", "上市融资",
    "ipo", "股票回购", "股份回购", "定增", "可转债", "投资者",
    "融资融券", "增持评级", "减持评级",
)
GENERIC_REPORTS = ("白皮书", "研究报告", "sap报告", "行业榜单", "排行榜")
OPERATING_DATA = ("gmv", "单量", "客单价", "销售额", "动销", "转化率",
                  "订单量", "门店销售", "履约成本", "配送时效")


def _hit(text, words):
    return any(x in text for x in words)


def decide(article: Dict) -> dict:
    title = str(article.get("title") or article.get("event_title") or "")
    summary = str(article.get("summary") or article.get("fact") or "")
    subject = (title + " " + summary[:200]).lower()
    if not title.strip():
        return {"decision": "reference", "reason": "subject_missing"}
    # No business report should be rescued by a late unrelated mention of
    # "Watsons", "beauty", or a platform in the full content.
    if _hit(title.lower(), NOISE_SUBJECT) or (
        _hit(subject, NOISE_SUBJECT) and not _hit(title.lower(), WATSONS)
    ):
        return {"decision": "reject", "reason": "unrelated_industry_subject"}
    if _hit(title.lower(), STOCK_SUBJECT) or (
        _hit(subject, STOCK_SUBJECT) and not _hit(title.lower(), OPERATIONS)
    ):
        return {"decision": "reject", "reason": "capital_market_subject"}
    if _hit(title.lower(), GENERIC_REPORTS) and not _hit(title.lower(), OPERATIONS):
        return {"decision": "reject", "reason": "generic_research_subject"}

    watsons = _hit(subject, WATSONS)
    retailer = _hit(subject, BEAUTY_RETAILERS)
    beauty = _hit(subject, BEAUTY)
    channel = _hit(subject, CHANNELS)
    operating = _hit(subject, OPERATIONS) or _hit(subject, OPERATING_DATA)

    # Watsons itself: meaningful mainland operating actions only.
    if watsons and operating:
        return {"decision": "main", "reason": "watsons_operating_move"}
    # Direct beauty competitor actions; beauty-category channel moves.
    if retailer and operating:
        return {"decision": "main", "reason": "beauty_competitor_operating_move"}
    if beauty and channel and operating:
        return {"decision": "main", "reason": "beauty_instant_retail_move"}
    # User-selected structural retailer/channel expansion is not a license for
    # general groceries. Needs named retailers and an identifiable market move.
    grocery = _hit(subject, ("山姆", "奥乐齐", "aldi", "盒马"))
    county = _hit(subject, ("县级市场", "县域", "下沉市场"))
    if grocery and county and operating:
        return {"decision": "main", "reason": "named_retail_channel_expansion"}
    # Direct platform operational changes may influence Watsons even without
    # the category in the headline; put in reference pending category linkage.
    if channel and operating:
        return {"decision": "reference", "reason": "channel_move_needs_category_link"}
    if (watsons or retailer or beauty) and not operating:
        return {"decision": "reference", "reason": "sector_reference_no_new_action"}
    return {"decision": "reject", "reason": "not_watsons_beauty_instant_retail"}


def admissible(event: Dict) -> dict:
    """Event-level second gate, not bypassable by scoring/LLM confidence."""
    return decide({
        "title": event.get("event_title") or "",
        "summary": event.get("fact") or "",
    })
