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
    "博倩叶", "boscia", "玉泽", "薇诺娜", "润百颜", "夸迪",
    # 主流美妆个护品牌（2026-11-02 补：11月数据暴露品牌词典过窄）
    "雅诗兰黛", "兰蔻", "欧莱雅", "资生堂", "sk-ii", "skii", "玉兰油", "olay",
    "欧诗漫", "苏菲娜", "sofina", "百雀羚", "自然堂", "丸美", "韩束", "完美日记",
    "花西子", "毛戈平", "林清轩", "可复美", "巨子生物", "华熙生物", "夸迪",
    "理肤泉", "雅漾", "修丽可", "赫莲娜", "海蓝之谜", "娇兰", "迪奥", "香奈儿",
    "联合利华", "宝洁", "高露洁", "狮王", "花王", "资生堂中国",
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
# 即时零售平台侧"基础设施级"动作（用户裁定应进主池）：
# 投入/补贴/入驻/开放能力，直接影响屈臣氏怎么做生意。
PLATFORM_INVEST = (
    "投入", "加码", "补贴", "扶持", "战略合作", "入驻", "开放能力",
    "新增投入", "百亿", "亿投入", "资源倾斜", "流量扶持",
    "履约", "供应链", "配送服务", "小时达", "仓配",
)
PLATFORMS_CORE = ("美团闪购", "美团", "淘宝闪购", "淘宝",
                  "京东秒送", "京东到家",
                  "抖音小时达", "饿了么", "即时零售")

OPERATIONS = (
    "上线", "上新", "入驻", "合作", "签约", "开店", "门店", "开业",
    "布局", "拓展", "扩张", "升级", "改造", "调整", "调改", "共创",
    "供应链", "履约", "配送", "渠道", "会员", "价格", "定价",
    "促销", "营销", "服务", "新品", "战略", "试点", "落地", "运营",
    "县级市场", "县域", "下沉市场", "闭店", "关店", "连关", "关停", "品类",
    "掘金", "抢滩", "进击", "加码", "深耕", "抢占", "攻坚",
    "盛典", "快闪", "联名", "首店", "旗舰店", "主题店", "专柜",
)
# Topic-specific blockers; matched against the article's subject, NOT the
# full body, which may contain a generic sector comparison.
NOISE_SUBJECT = (
    # 2026-11-02 用户裁定：厨房、餐饮、生鲜、家宴一律不进
    "厨房", "餐饮", "生鲜", "家宴", "菜谱", "食材", "预制菜", "熟食",
    "会员卡", "积分规则", "会员等级",      # 会员卡规则类与经营情报无关
    "白酒", "酒类", "酒水", "啤酒", "葡萄酒", "酒行", "酒业",
    "招聘", "求职", "岗位", "猎头", "年薪", "薪资", "cho", "职场",
    "冰淇淋", "冰激凌", "雪糕", "野人先生", "茶饮", "奶茶", "寿司郎",
    "寿司", "火锅", "餐饮", "咖啡", "健身", "运动健身", "私教",
    "汽车", "半导体", "芯片", "机器人", "地产",
)
# Do not block company names with 股份, or operational numeric data.
STOCK_SUBJECT = (
    "股价", "涨停", "跌停", "股市", "股票", "a股", "港股", "美股",
    "行情", "股民", "市值", "市盈率", "市净率", "目标价",
    "研报", "买入评级", "卖出评级", "券商研报", "机构研报", "业绩预告",
    "业绩报告", "利润大涨", "利润同比", "营业收入", "营收同比", "净利",
    "亏损总额", "估算", "净利润同比",
    "财报", "季报", "年报", "净利润", "扣非净利", "上市融资",
    "ipo", "股票回购", "股份回购", "定增", "可转债", "投资者",
    "融资融券", "增持评级", "减持评级",
)
GENERIC_REPORTS = ("白皮书", "研究报告", "sap报告", "行业榜单", "排行榜")
OPERATING_DATA = ("gmv", "单量", "客单价", "销售额", "动销", "转化率",
                  "订单量", "门店销售", "履约成本", "配送时效")


_QUOTES = str.maketrans("", "", "“”\"'‘’「」『』《》 \t\u3000")


def _norm(text: str) -> str:
    """去掉引号/空白，避免"“县级”市场"漏匹配"县级市场"。"""
    return str(text or "").translate(_QUOTES).lower().replace("\u00a0", "")


def _hit(text, words):
    t = _norm(text)
    return any(x in t for x in words)


def decide(article: Dict) -> dict:
    title = str(article.get("title") or article.get("event_title") or "")
    summary = str(article.get("summary") or article.get("fact") or "")
    subject = _norm(title + " " + summary[:200])
    if not title.strip():
        return {"decision": "reference", "reason": "subject_missing"}
    # No business report should be rescued by a late unrelated mention of
    # "Watsons", "beauty", or a platform in the full content.
    if _hit(title, NOISE_SUBJECT) or (
        _hit(subject, NOISE_SUBJECT) and not _hit(title, WATSONS)
    ):
        return {"decision": "reject", "reason": "unrelated_industry_subject"}
    if _hit(title, STOCK_SUBJECT) or (
        _hit(subject, STOCK_SUBJECT) and not _hit(title, OPERATIONS)
    ):
        return {"decision": "reject", "reason": "capital_market_subject"}
    if _hit(title, GENERIC_REPORTS) and not _hit(title, OPERATIONS):
        return {"decision": "reject", "reason": "generic_research_subject"}

    # 3C/手机/家电/智能硬件方向的品牌或政策，不属美妆个护经营情报（用户裁定）。
    # 必须在"平台投入"判定之前，否则"智能眼镜补贴+即时零售"会被误纳入。
    DEVICE_BRANDS = ("vivo", "oppo", "华为", "小米", "荣耀", "苹果",
                     "iphone", "三星", "realme", "一加", "联想", "戴尔",
                     "智能眼镜", "无人机", "游戏机", "智能手表")
    if _hit(subject, DEVICE_BRANDS):
        return {"decision": "reject", "reason": "device_brand_not_beauty"}

    watsons = _hit(subject, WATSONS)
    retailer = _hit(subject, BEAUTY_RETAILERS)
    beauty = _hit(subject, BEAUTY)
    channel = _hit(subject, CHANNELS)
    operating = _hit(subject, OPERATIONS) or _hit(subject, OPERATING_DATA)

    # 平台侧基础设施级动作（投入/补贴/入驻/开放）→ 主池。
    # 金标准核查：美团300亿投入即时零售(13次)、北京补贴100万、
    # 苏宁600家门店入驻美团闪购，均被判应进 main。
    _platform = _hit(subject, PLATFORMS_CORE)
    _invest = _hit(subject, PLATFORM_INVEST)
    if _platform and _invest:
        return {"decision": "main", "reason": "platform_infrastructure_move"}

    # Watsons itself: meaningful mainland operating actions only.
    if watsons and operating:
        return {"decision": "main", "reason": "watsons_operating_move"}
    # 实体零售商（含综合卖场）入驻即时零售平台 = 供给结构变化 → 主池。
    # 金标准：苏宁易购600家门店入驻美团闪购 判应进；vivo品牌合作 判不进。
    BIG_RETAILER = ("苏宁", "国美", "沃尔玛", "永辉", "大润发", "华润万家",
                    "麦德龙", "costco", "开市客", "罗森", "全家", "7-eleven")
    if _hit(subject, BIG_RETAILER) and _invest:
        return {"decision": "main", "reason": "retailer_channel_entry"}

    # Direct beauty competitor actions; beauty-category channel moves.
    if retailer and operating:
        return {"decision": "main", "reason": "beauty_competitor_operating_move"}
    if beauty and channel and operating:
        return {"decision": "main", "reason": "beauty_instant_retail_move"}
    # User-selected structural retailer expansion (山姆/奥乐齐/盒马 下沉县域)
    # must be decided BEFORE the generic channel fallback, otherwise it is
    # misclassified as reference-only.
    grocery = _hit(subject, ("山姆", "奥乐齐", "aldi", "盒马", "costco", "开市客"))
    county = _hit(subject, ("县级市场", "县域", "下沉市场", "县级市"))
    if grocery and county and operating:
        return {"decision": "main", "reason": "named_retail_channel_expansion"}

    # 美妆个护品牌自身的经营模式动作（DTC/直营/重启/进入中国）→ 主池。
    BRAND_MODEL = ("dtc", "直营", "重启", "进入中国", "落地中国", "独家代理")
    if _hit(subject, BEAUTY) and _hit(subject, BRAND_MODEL):
        return {"decision": "main", "reason": "beauty_brand_model_move"}

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
