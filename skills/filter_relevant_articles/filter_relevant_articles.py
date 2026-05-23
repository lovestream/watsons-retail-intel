#!/usr/bin/env python3
"""
filter_relevant_articles.py — 规则过滤 + LLM 语义复核

读取 raw_articles.json，结合 keywords.yaml 和 scoring.yaml，
对文章评分后分为 cleaned / reference / rejected 三类池。

用法:
    python filter_relevant_articles.py \
        --project-root /app/working/projects/watsons-retail-intel \
        --date 2026-04-26 \
        --use-llm true \
        --llm-mode borderline_only
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ===================== 依赖检查 =====================
_MISSING = []
try:
    import yaml
except ImportError:
    _MISSING.append("PyYAML")

if _MISSING:
    print(f"ERROR: 缺少必要依赖: {', '.join(_MISSING)}\n"
          f"请运行: pip install {' '.join(_MISSING)}", file=sys.stderr)
    sys.exit(1)

# ===================== 常量与关键词词典 =====================

# 即时零售平台词 — 命中 +4
INSTANT_RETAIL_KEYWORDS = [
    "美团闪购", "京东秒送", "京东到家", "淘宝闪购", "饿了么",
    "抖音小时达", "即时零售", "小时达", "同城零售", "本地生活",
    "前置仓", "闪电仓", "门店履约",
    "大众点评", "抖音本地生活", "抖音本服", "抖音团购", "到店",
]

# 美妆个护品类词 — 命中 +3
BEAUTY_CARE_KEYWORDS = [
    "美妆", "个护", "护肤", "彩妆", "洗护", "防晒", "女性护理",
    "男士护理", "身体护理", "口腔护理", "香氛", "面膜", "卸妆",
    "旅行装", "小规格", "凑品类", "凑单品",
]

# 竞对词 — 命中 +3
COMPETITOR_KEYWORDS = [
    "丝芙兰", "万宁", "妍丽", "WOW COLOUR", "调色师", "话梅",
    "名创优品", "KK集团",
    "便利蜂", "全家", "711", "罗森", "美宜佳",
    "盒马", "永辉", "大润发", "山姆",
]

# B2C / To B 渠道词 — 命中 +2
B2B_CHANNEL_KEYWORDS = [
    "天猫官旗", "天猫官方旗舰店", "天猫超市", "京东旗舰店",
    "京东POP", "京东自营",
]

# 经营变量词 — 命中 +1
BUSINESS_VAR_KEYWORDS = [
    "流量", "转化率", "客单价", "复购", "价格", "补贴", "毛利",
    "货盘", "SKU", "履约", "会员", "私域", "活动", "投流", "资源位",
]

# 屈臣氏直接命中 — +5
WATSONS_KEYWORDS = ["屈臣氏", "Watsons", "watsons"]

# ─── 核心命中词（用于 cleaned 准入快通道） ──
# 屈臣氏直接命中
CORE_WATSONS_KEYWORDS = ["屈臣氏", "Watsons", "watsons"]
# 即时零售核心平台（平台级强信号）
CORE_PLATFORM_KEYWORDS = [
    "美团闪购", "京东秒送", "京东到家", "淘宝闪购", "饿了么",
    "抖音小时达", "即时零售", "前置仓", "闪电仓",
]
# 美妆个护核心品类（扩展版 — 覆盖更多细分品类和成分词）
CORE_BEAUTY_KEYWORDS = [
    "美妆", "个护", "护肤", "彩妆", "防晒", "面膜",
    # 品类词
    "精华", "面霜", "乳液", "爽肤水", "洁面", "卸妆",
    "口红", "唇膏", "唇釉", "眼影", "粉底", "遮瑕",
    "香水", "香氛", "身体乳", "沐浴露", "洗发水", "护发素",
    "美白", "抗衰", "抗老", "抗皱", "修复", "保湿",
    # 成分功效词
    "玻尿酸", "视黄醇", "烟酰胺", "水杨酸", "维C", "VC精华",
    "胜肽", "胶原蛋白", "神经酰胺", "虾青素", "熊果苷",
    # 趋势词
    "纯净美妆", "功效护肤", "敏感肌", "成分党", "以油养肤",
    "早C晚A", "刷酸", "轻医美", "药妆", "男士护肤",
    # 英文
    "skincare", "cosmetics", "makeup", "beauty",
    "serum", "moisturizer", "sunscreen", "fragrance",
]
# 竞对核心（直接竞对品牌）
CORE_COMPETITOR_KEYWORDS = [
    "丝芙兰", "万宁", "调色师", "话梅", "妍丽",
    "便利蜂", "全家", "711", "罗森",
]


def has_core_hit(article: dict) -> Tuple[bool, str]:
    """检测文章是否命中屈臣氏/核心平台/竞对的核心关键词。
    
    用于 cleaned 准入快通道判断。
    Returns:
        (has_hit, hit_type): hit_type 是 "watsons" | "platform" | "beauty" | "competitor" | ""
    """
    combined = " ".join([
        article.get("title", "") or "",
        article.get("summary", "") or "",
        (article.get("content", "") or "")[:1000],
    ]).lower()

    for kw in CORE_WATSONS_KEYWORDS:
        if kw.lower() in combined:
            return True, "watsons"
    for kw in CORE_PLATFORM_KEYWORDS:
        if kw.lower() in combined:
            return True, "platform"
    # 美妆关键词需要双重验证：单一弱信号词不足以确认相关性
    # 强信号词（品类核心）单独即可；弱信号词需要配合上下文
    _BEAUTY_STRONG = {"美妆", "个护", "护肤", "彩妆", "防晒", "面膜", "化妆品",
                      "skincare", "cosmetics", "makeup", "beauty",
                      "丝芙兰", "屈臣氏", "万宁", "妍丽"}
    _BEAUTY_CONTEXT = {"品牌", "零售", "电商", "渠道", "消费", "市场", "产品",
                       "门店", "销售", "增长", "下滑", "美容", "化妆", "个人护理"}
    beauty_hits = [kw for kw in CORE_BEAUTY_KEYWORDS if kw.lower() in combined]
    if beauty_hits:
        # 强信号词命中 → 直接通过
        if any(kw in _BEAUTY_STRONG for kw in beauty_hits):
            return True, "beauty"
        # 多个美妆词命中 → 通过
        if len(beauty_hits) >= 2:
            return True, "beauty"
        # 单个弱信号词（如"抗衰""流量"） → 需要上下文佐证
        if any(ctx in combined for ctx in _BEAUTY_CONTEXT):
            return True, "beauty"
        # 单个弱信号词 + 无上下文 → 不算核心命中
        # (文章仍可通过rule_score进入reference，但不享受core_hit快通道)
    for kw in CORE_COMPETITOR_KEYWORDS:
        if kw.lower() in combined:
            return True, "competitor"
    return False, ""

# ═══════════════════════════════════════════════
    # freshness_status=bootstrap_seen（首次运行，历史URL，不算新发现）
    # ═══════════════════════════════════════════════
    if freshness == "bootstrap_seen":
        # bootstrap_seen 绝不进 cleaned，最高只能进 reference
        if core_hit and rule_score >= 5:
            return _noise_downgrade("reference", f"bootstrap_seen+core_{core_type} score={rule_score}→reference(历史背景)")
        if rule_score >= 6:
            return _noise_downgrade("reference", f"bootstrap_seen score={rule_score}→reference(历史背景)")
        if rule_score >= 3:
            return "reference", f"bootstrap_seen score={rule_score}→reference"
        return "reject", f"bootstrap_seen score={rule_score}<3→reject"

    # ═══════════════════════════════════════════════════════════════
# 页面类型分类 / 地区标签 / 噪音标记
# ═══════════════════════════════════════════════
    # freshness_status=bootstrap_seen（首次运行，历史URL，不算新发现）
    # ═══════════════════════════════════════════════
    if freshness == "bootstrap_seen":
        # bootstrap_seen 绝不进 cleaned，最高只能进 reference
        if core_hit and rule_score >= 5:
            return _noise_downgrade("reference", f"bootstrap_seen+core_{core_type} score={rule_score}→reference(历史背景)")
        if rule_score >= 6:
            return _noise_downgrade("reference", f"bootstrap_seen score={rule_score}→reference(历史背景)")
        if rule_score >= 3:
            return "reference", f"bootstrap_seen score={rule_score}→reference"
        return "reject", f"bootstrap_seen score={rule_score}<3→reject"

    # ═══════════════════════════════════════════════════════════════

# page_type 枚举
PAGE_TYPE_NEWS = "news"
PAGE_TYPE_ARTICLE = "article"
PAGE_TYPE_REPORT = "report"
PAGE_TYPE_OFFICIAL_NOTICE = "official_notice"
PAGE_TYPE_PRODUCT_PAGE = "product_page"
PAGE_TYPE_HOMEPAGE = "homepage"
PAGE_TYPE_SOCIAL_POST = "social_post"
PAGE_TYPE_PROMOTION = "promotion"
PAGE_TYPE_JOB = "job"
PAGE_TYPE_UNKNOWN = "unknown"

# 不允许进入 cleaned 的 page_type
CLEANED_BLOCKED_PAGE_TYPES = {
    PAGE_TYPE_PRODUCT_PAGE, PAGE_TYPE_HOMEPAGE,
    PAGE_TYPE_SOCIAL_POST, PAGE_TYPE_PROMOTION,
    PAGE_TYPE_JOB, PAGE_TYPE_UNKNOWN,
}

# region_tag 枚举
REGION_MAINLAND = "mainland"
REGION_HK = "hk"
REGION_TW = "tw"
REGION_OVERSEAS = "overseas"
REGION_UNKNOWN = "unknown"

# 社媒平台域名
SOCIAL_MEDIA_DOMAINS = [
    "instagram.com", "facebook.com", "fb.com", "twitter.com", "x.com",
    "pinterest.com", "tiktok.com", "douyin.com", "weibo.com",
    "xiaohongshu.com", "reddit.com", "threads.net",
    "linkedin.com", "weibo.cn",
]

# 产品/促销 URL 路径模式
PRODUCT_URL_PATTERNS = [
    r"/product/", r"/products/", r"/goods/", r"/item/",
    r"/sku/", r"/cart", r"/coupon", r"/promotion",
    r"/category", r"/search", r"/p/\d+", r"/dp/",
    r"/detail/", r"/buy/", r"/order/", r"/shop/",
    # app 下载/安装页面
    r"apps\.microsoft\.com/detail/", r"sj\.qq\.com/appdetail/",
    r"apps\.apple\.com/.*app/", r"play\.google\.com/store/apps/",
    r"app\.mi\.com/details", r"/appdetail/",
    # 优惠券/比价聚合页
    r"smzdm\.com/.*[hf]\d+", r"faxian\.smzdm\.com/",
]

# 品牌/官网首页域名模式
HOMEPAGE_DOMAINS = {
    "www.watsons.com.cn": "watsons_hk",
    "www.watsons.com.hk": "watsons_hk",
    "www.watsons.com.tw": "watsons_tw",
    "www.watsons.cn": "watsons_cn",
    "watsons.com.cn": "watsons_cn",
    "www.sephora.com": "sephora_intl",
    "www.sephora.cn": "sephora_cn",
    "www.sephora.com.hk": "sephora_hk",
    "m.sephora.cn": "sephora_cn",
    "www.mannings.com.cn": "mannings_cn",
    "www.mannings.com.hk": "mannings_hk",
}

# HK/TW 屈臣氏域名和关键词
HK_TW_DOMAINS = [
    "watsons.com.hk", "watsons.com.tw",
    "sephora.com.hk",
    "mannings.com.hk",
]
HK_TW_KEYWORDS = [
    "屈臣氏hk", "屈臣氏tw", "watsonshk", "watsonstw",
    "香港屈臣氏", "台灣屈臣氏", "台灣屈臣氏",
    "購物優惠", "換購", "檔期", "銅板價", "加價購",
    "第二件5折", "滿額", "折價", "優惠碼", "折扣碼",
    "春日愛購物", "嘉年華", "免費好禮",
    "會員", "積分抵現", "門市同品質秒殺",
    "香港", "hk ", "tw ",
]

# 促销/营销内容关键词
PROMOTION_KEYWORDS = [
    "購物優惠", "換購", "檔期", "銅板價", "加價購",
    "折扣碼", "優惠碼", "促銷活動", "滿額贈",
    "免運費", "限時特價", "閃購價", "會員價",
    "新用戶禮", "首單", "新人券",
]


def classify_page_type(article: dict) -> str:
    """分类文章的页面类型。
    
    基于 URL、标题、摘要等判断页面类型。
    Returns:
        page_type: news | article | report | official_notice |
                   product_page | homepage | social_post | promotion |
                   job | unknown
    """
    url = article.get("url", "").lower().strip()
    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    content = (article.get("content", "") or "")[:500].lower()
    combined = f"{title} {summary} {content}"
    
    # 1. 社媒帖子 — 最高优先级
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.lower()
    except Exception:
        domain = ""
        path = ""
    
    for sm_domain in SOCIAL_MEDIA_DOMAINS:
        if sm_domain in domain:
            return PAGE_TYPE_SOCIAL_POST
    
    # 2. 产品页面 — URL 特征
    import re as _re
    for pattern in PRODUCT_URL_PATTERNS:
        if _re.search(pattern, url):
            # 检查是否是新闻报道谈论产品（而非产品页本身）
            news_indicators = ["评测", "测评", "报道", "新闻", "分析", "趋势", "市场", "行业"]
            if any(ind in combined for ind in news_indicators):
                return PAGE_TYPE_ARTICLE
            return PAGE_TYPE_PRODUCT_PAGE
    
    # 3. 官网首页 — 域名根路径
    # 域名首页或极短路径
    if domain in HOMEPAGE_DOMAINS:
        if path in ("", "/", "/index.html", "/index.htm", "/zh-cn/", "/zh-tw/"):
            return PAGE_TYPE_HOMEPAGE
    
    # 非品牌域名但路径只有 /
    if path in ("", "/") and len(url) < 30:
        # 可能是首页，但需要更多判断
        homepage_title_patterns = [
            r'^官网$', r'^首页$', r'^home$',
            r'welcome\s+to', r'^official\s+site$',
        ]
        for pat in homepage_title_patterns:
            if _re.search(pat, title.strip()):
                return PAGE_TYPE_HOMEPAGE
    
    # 4. 促销/营销页面
    promo_score = 0
    for pkw in PROMOTION_KEYWORDS:
        if pkw.lower() in combined:
            promo_score += 1
    # 标题含明确促销词
    promo_title_patterns = [
        "購物優惠", "换购", "换购价", "优惠码", "折扣码",
        "满减", "限时优惠", "新用户礼", "首单优惠",
        "优惠券", "促销", "特卖", "秒杀",
        "8折", "5折", "7折", "6折", "9折",  # 打折
        "免运费", "免费领取", "免费好礼", "免費好禮",
        "春日愛購物", "愛購物嘉年華", "嘉年华", "市集", "檔期",
        "僅需", "低至", "折起", "特惠", "精选优惠",
    ]
    for pp in promo_title_patterns:
        if pp.lower() in title:
            promo_score += 2
    if promo_score >= 3:
        return PAGE_TYPE_PROMOTION
    
    # 5. 招聘类
    job_keywords = ["招聘", "招聘岗位", "求职", "简历投递", "社招", "校招"]
    if any(jk in combined for jk in job_keywords):
        return PAGE_TYPE_JOB
    
    # 6. 官方公告/新闻
    official_notice_patterns = [
        r'官方(?:公告|声明|通知|回应)',
        r'声明$',
        r'澄清$',
    ]
    for pat in official_notice_patterns:
        if _re.search(pat, title):
            return PAGE_TYPE_OFFICIAL_NOTICE
    
    # 7. 报告
    report_patterns = [
        r'(?:行业|市场|发展|产业).*(?:报告|白皮书|蓝皮书|研究)',
        r'(?:报告|白皮书|蓝皮书).*(?:发布|出炉)',
        r'^(?:中国|全球|中国(?:内地)?).*(?:报告|研究|趋势)',
    ]
    for pat in report_patterns:
        if _re.search(pat, title):
            return PAGE_TYPE_REPORT
    
    # 8. 新闻/资讯 — URL 特征
    news_url_patterns = [
        r'/news/', r'/article/', r'/detail/', r'/post/',
        r'/\d{4}/\d{2}/',  # 日期路径如 /2026/05/01/
        r'/\d{4}-\d{2}-\d{2}',  # 日期路径如 /2026-05-01-
    ]
    news_domains = [
        "36kr.com", "jiemian.com", "huxiu.com", "latepost.com",
        "ebrun.com", "thepaper.cn", "news.cn", "caixin.com",
        "eastmoney.com", "stcn.com", "yicai.com", "cbnweek.com",
        "cyzone.cn", "ifanr.com", "36kr.cn",
    ]
    if any(nd in domain for nd in news_domains):
        # 新闻站点但可能是首页
        if path and path != "/":
            return PAGE_TYPE_NEWS
    
    for pat in news_url_patterns:
        if _re.search(pat, url):
            return PAGE_TYPE_NEWS
    
    # 9. 默认: 如果标题看起来像文章
    article_indicators = [
        r'(?:分析|解读|深度|盘点|预测|趋势|复盘|观察)',
        r'(?:如何|为什么|怎样|怎么|什么意思)',
        r'(?:盛宴|洗牌|爆发|变局|重塑|崛起|跌落)',
        r'(?:报告|数据|增长|下降|突破|首次|最大)',
    ]
    article_count = sum(1 for pat in article_indicators if _re.search(pat, title))
    if article_count >= 1:
        return PAGE_TYPE_ARTICLE
    
    # 10. 兜底
    # 长 URL + 有内容 → 倾向 article
    if len(url) > 40 and len(combined) > 100:
        return PAGE_TYPE_ARTICLE
    
    return PAGE_TYPE_UNKNOWN


def classify_region_tag(article: dict) -> str:
    """分类文章的地区标签。
    
    Returns:
        region_tag: mainland | hk | tw | overseas | unknown
    """
    url = article.get("url", "").lower()
    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    content = (article.get("content", "") or "")[:500].lower()
    combined = f"{title} {summary} {content}"
    
    # 1. HK 域名直接判定
    hk_domains = [".hk", ".com.hk", "hongkong", "mingpao", "hk01", "bastillepost",
                  "orientaldaily", "singtao", "takungpao", "wenweipo", "hket.com"]
    for hd in hk_domains:
        if hd in url:
            return REGION_HK
    
    # 2. TW 域名直接判定
    tw_domains = [".tw", ".com.tw", "taiwan", "台灣", "台湾"]
    for td in tw_domains:
        if td in url:
            return REGION_TW
    
    # 3. 海外域名
    overseas_tlds = [".sg", ".my", ".th", ".ph", ".id", ".vn", ".jp", ".kr", ".au", ".uk", ".in"]
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc.lower()
        for tld in overseas_tlds:
            if domain.endswith(tld):
                return REGION_OVERSEAS
    except Exception:
        pass
    
    # 4. 内容特征判断 HK/TW
    hk_tw_strong = [
        "香港屈臣氏", "台灣屈臣氏", "watsonshk", "watsonstw",
        "屈臣氏hk", "屈臣氏tw", "屈臣氏hk", "屈臣氏tw",
        "購物優惠", "換購", "檔期", "銅板價",
        "加價購", "滿額", "折價", "優惠碼",
        "春日愛購物", "愛購物嘉年華",
        "免費好禮", "僅在絲芙蘭",
    ]
    hk_tw_moderate = [
        "香港", "hk ", "tw ", "商圈", "店鋪",
        "明報", "星島", "東方日報", "大公報", "文匯報", "經濟日報",
        "港股", "港元", "港幣", "港藥", "港人", "港區",
        "聯合報", "中時", "自由時報", "蘋果日報",
    ]
    # 标题出现繁体/港台词
    import re as _re
    # 常见繁体字（与简体不同的高频字）
    _TRADITIONAL_CHARS = (
        r'[購換檔銅價額幣廠業場際藥妝龍豐過訊僅萬寧贏經濟報證審億據營較競轉導選環優質'
        r'設備圖處點網這裡來開區東實產開關門問題學習機構體驗廳廣場運動員會議論壇發佈會'
        r'觀點評論記錄視頻節課題組織機構體係統計劃項對話題組織機構體係統計劃項對話題'
        r'聯繫電話號碼認證書籍雜誌報紙網絡電視臺廣播電臺節課題組織機構體係統計劃項'
        r'藥妝龍豐過訊僅萬寧贏經濟報證審億據營較競轉導選環優質設備圖處點網這裡來開區]'
    )
    traditional_chinese = _re.findall(_TRADITIONAL_CHARS, title + summary[:200])
    if len(traditional_chinese) >= 3:
        return REGION_HK  # 繁体字居多 → HK/TW
    
    for kw in hk_tw_strong:
        if kw.lower() in combined:
            return REGION_HK
    
    # 标题含港台媒体名或地区关键词
    for kw in hk_tw_moderate:
        if kw in title or kw in summary[:200]:
            return REGION_HK
    
    # 标题含香港/台湾
    if "香港" in title or "hk" in title.split():
        return REGION_HK
    if "台灣" in title or "台湾" in title or "tw" in title.split():
        return REGION_TW
    
    # 内容包含大量繁体字 → HK/TW
    traditional_chars = _re.findall(_TRADITIONAL_CHARS, title + summary[:300])
    if len(traditional_chars) >= 4:
        return REGION_HK
    
    # 5. 大陆特征
    mainland_indicators = [
        "中国大陆", "内地", "全国", "国内",
        "即时零售", "美团闪购", "京东到家", "京东秒送",
        "淘宝闪购", "饿了么", "抖音小时达", "前置仓",
    ]
    for mi in mainland_indicators:
        if mi in combined:
            return REGION_MAINLAND
    
    # 6. 默认
    # 含中文 → 倾向 mainland，否则 unknown
    has_chinese = _re.search(r'[\u4e00-\u9fff]', combined)
    if has_chinese:
        return REGION_MAINLAND
    
    return REGION_UNKNOWN


def compute_noise_flags(article: dict, page_type: str, region_tag: str) -> List[str]:
    """计算文章的噪音标记列表。
    
    Returns:
        List of noise flags
    """
    flags: List[str] = []
    url = article.get("url", "").lower()
    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    combined = f"{title} {summary}"
    
    # 基于页面类型
    if page_type in CLEANED_BLOCKED_PAGE_TYPES:
        flags.append(f"page_type:{page_type}")
    
    # 基于地区
    if region_tag in (REGION_HK, REGION_TW):
        flags.append(f"region:{region_tag}")
    
    # 基于内容特征
    # 官网首页特征
    homepage_title_patterns = ["官网", "官方商城", "线上商城", "線上商城", "official site"]
    for hp in homepage_title_patterns:
        if hp in title:
            flags.append("official_site_title")
            break
    
    # 纯营销内容
    promo_title_patterns = ["優惠", "折扣", "滿額", "換購", "秒殺", "特賣"]
    promo_count = sum(1 for pp in promo_title_patterns if pp in combined)
    if promo_count >= 2:
        flags.append("promo_title")
    
    # 社媒体特征（未在 URL 中但标题含 Instagram 等特征）
    social_title_patterns = ["on instagram", "on facebook", "on twitter", "转发", "单条微博"]
    for sp in social_title_patterns:
        if sp in combined:
            flags.append("social_content")
            break
    
    # 无实际内容
    if len(summary.strip()) < 20 and len(title.strip()) < 15:
        flags.append("thin_content")
    
    # 非 mainland 的屈臣氏门店信息
    if region_tag in (REGION_HK, REGION_TW) and ("屈臣氏" in combined or "watsons" in combined):
        if page_type in (PAGE_TYPE_PROMOTION, PAGE_TYPE_HOMEPAGE, PAGE_TYPE_PRODUCT_PAGE):
            flags.append("hk_tw_watsons_promo")
    
    return flags


# ═══════════════════════════════════════════════════════════════
# 大促时效性分类 (Campaign Temporality)
# ═══════════════════════════════════════════════════════════════

# 大促名称 → 关键词列表
CAMPAIGN_KEYWORDS = {
    "38节": ["38节", "3.8节", "3·8", "妇女节", "女王节", "女神节"],
    "520": ["520", "5·20", "520大促"],
    "618": ["618", "618大促", "6·18", "6.18"],
    "七夕": ["七夕节", "七夕大促", "七夕促销"],
    "双11": ["双11", "双十一", "11.11", "11·11", "11.11大促"],
    "双12": ["双12", "双十二", "12.12", "12·12", "十二.12"],
    "年货节": ["年货节", "年货大促", "年货节大促"],
}

# 大促名称 → 大促时间窗口 (month_start, day_start, month_end, day_end)
# 窗口含义：该大促的预售+正式+余波期
CAMPAIGN_CALENDAR = {
    "38节":  (2, 15, 3, 15),
    "520":   (5, 1, 5, 25),
    "618":   (5, 1, 6, 30),
    "七夕":  (7, 15, 8, 31),
    "双11":  (9, 1, 11, 20),
    "双12":  (11, 20, 12, 20),
    "年货节": (12, 15, 1, 25),
}


def _extract_years(text: str) -> List[int]:
    """从文本中提取所有出现的年份。"""
    years = set()
    for m in re.finditer(r'(202[0-9])', text):
        years.add(int(m.group(1)))
    return sorted(years)


def _extract_year_month(text: str) -> List[Tuple[int, int]]:
    """从文本和URL中提取 (年, 月) 对。"""
    pairs = set()
    # /YYYY/MM/
    for m in re.finditer(r'/(\d{4})/(\d{2})/', text):
        y, mo = int(m.group(1)), int(m.group(2))
        if 2020 <= y <= 2030 and 1 <= mo <= 12:
            pairs.add((y, mo))
    # 纯6位: 202311, 202406
    for m in re.finditer(r'(?<![0-9])(\d{6})(?![0-9])', text):
        s = m.group(1)
        y, mo = int(s[:4]), int(s[4:6])
        if 2020 <= y <= 2030 and 1 <= mo <= 12:
            pairs.add((y, mo))
    # 短格式: 2311, 2406 + NN前缀
    for m in re.finditer(r'[Nn]{0,2}(\d{2})(0[1-9]|1[0-2])', text):
        y_short, mo = int(m.group(1)), int(m.group(2))
        y = 2000 + y_short
        if 2020 <= y <= 2030:
            pairs.add((y, mo))
    return sorted(pairs)


def classify_campaign_temporality(
    article: dict,
    current_date: Optional[str] = None,
) -> dict:
    """判断文章的大促时效性。

    Returns:
        dict: campaign_name, campaign_temporality, campaign_year, campaign_reason
    """
    if current_date is None:
        current_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    current_dt = datetime.strptime(current_date, "%Y-%m-%d")
    current_year = current_dt.year

    title = (article.get("title", "") or "").lower()
    summary = (article.get("summary", "") or "").lower()
    url = (article.get("url", "") or "").lower()
    content = (article.get("content", "") or "")[:500].lower()
    combined = f"{title} {summary} {url} {content}"

    # ── Step 1: 匹配大促关键词 ──
    detected_campaigns = []
    for campaign_name, keywords in CAMPAIGN_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                detected_campaigns.append(campaign_name)
                break

    if not detected_campaigns:
        return {"campaign_name": "not_campaign", "campaign_temporality": "not_campaign",
                "campaign_year": None, "campaign_reason": "未匹配大促关键词"}

    campaign_name = detected_campaigns[0]
    cal_entry = CAMPAIGN_CALENDAR.get(campaign_name)

    # ── Step 2: 提取年份 ──
    years_in_text = _extract_years(combined)
    year_months_in_text = _extract_year_month(combined)

    campaign_year = None
    year_source = ""

    if years_in_text:
        for y in years_in_text:
            if 2020 <= y <= current_year + 1:
                campaign_year = y
                year_source = f"text_year={y}"
                break

    if year_months_in_text and (campaign_year is None or campaign_year < current_year):
        for y, m in year_months_in_text:
            if cal_entry:
                ms, ds, me, de = cal_entry
                # URL年月与大促窗口匹配 → 确认年份
                if y <= current_year + 1:
                    campaign_year = y
                    year_source = f"url_ym={y}-{m:02d}"
                    break

    # ── Step 3: 无明确年份时，用 time_status/published_at 推断 ──
    if campaign_year is None:
        time_status = article.get("time_status", "")
        published_at = article.get("published_at", "") or ""

        if time_status in ("in_window", "near_window"):
            campaign_year = current_year
            year_source = f"time_status={time_status}"
        elif published_at and len(published_at) >= 10:
            try:
                pub_dt = datetime.strptime(published_at[:10], "%Y-%m-%d")
                campaign_year = pub_dt.year
                year_source = f"pub={published_at[:10]}"
            except ValueError:
                pass

        if campaign_year is None:
            return {"campaign_name": campaign_name,
                    "campaign_temporality": "unknown_campaign_time",
                    "campaign_year": None,
                    "campaign_reason": f"提及{campaign_name}但无法判断年份(ts={time_status})"}

    # ── Step 4: 判断时效性 ──
    if campaign_year > current_year:
        return {"campaign_name": campaign_name, "campaign_temporality": "upcoming_campaign",
                "campaign_year": campaign_year,
                "campaign_reason": f"{campaign_name}{campaign_year}即将到来({year_source})"}

    if campaign_year == current_year:
        if cal_entry:
            ms, ds, me, de = cal_entry
            window_start = datetime(current_year, ms, ds)
            window_end = datetime(current_year, me, de)
            # 跨年 (年货节)
            if ms > me:
                if current_dt >= window_start or current_dt <= window_end:
                    return {"campaign_name": campaign_name, "campaign_temporality": "current_campaign",
                            "campaign_year": campaign_year,
                            "campaign_reason": f"当年{campaign_name}窗口期内({year_source})"}
                elif current_dt < window_start:
                    return {"campaign_name": campaign_name, "campaign_temporality": "upcoming_campaign",
                            "campaign_year": campaign_year,
                            "campaign_reason": f"当年{campaign_name}尚未开始({year_source})"}

            if window_start <= current_dt <= window_end:
                return {"campaign_name": campaign_name, "campaign_temporality": "current_campaign",
                        "campaign_year": campaign_year,
                        "campaign_reason": f"当年{campaign_name}窗口期内({year_source})"}
            elif current_dt < window_start:
                days_until = (window_start - current_dt).days
                return {"campaign_name": campaign_name, "campaign_temporality": "upcoming_campaign",
                        "campaign_year": campaign_year,
                        "campaign_reason": f"当年{campaign_name}即将到来({days_until}天后,{year_source})"}
            else:
                days_since = (current_dt - window_end).days
                if days_since <= 30:
                    return {"campaign_name": campaign_name, "campaign_temporality": "retrospective",
                            "campaign_year": campaign_year,
                            "campaign_reason": f"当年{campaign_name}刚结束({days_since}天前,回顾,{year_source})"}
                else:
                    return {"campaign_name": campaign_name, "campaign_temporality": "historical_campaign",
                            "campaign_year": campaign_year,
                            "campaign_reason": f"当年{campaign_name}已过较久({days_since}天前,{year_source})"}
        return {"campaign_name": campaign_name, "campaign_temporality": "current_campaign",
                "campaign_year": campaign_year,
                "campaign_reason": f"当年{campaign_name}({year_source})"}

    # campaign_year < current_year → 历史
    years_ago = current_year - campaign_year
    if years_ago == 1:
        return {"campaign_name": campaign_name, "campaign_temporality": "retrospective",
                "campaign_year": campaign_year,
                "campaign_reason": f"{campaign_name}{campaign_year}年回顾(1年前,{year_source})"}
    else:
        return {"campaign_name": campaign_name, "campaign_temporality": "historical_campaign",
                "campaign_year": campaign_year,
                "campaign_reason": f"{campaign_name}{campaign_year}年历史({years_ago}年前,{year_source})"}


# ═══════════════════════════════════════════════════════════════
NEGATIVE_GENERAL_KEYWORDS = [
    # 泛科技/泛财经/泛宏观 -3
    "宏观经济", "GDP", "CPI", "联储", "加息", "降息", "A股", "港股",
    "美股", "IPO", "独角兽", "融资轮",
]

NEGATIVE_TOPIC_KEYWORDS = [
    # 汽车、房产、游戏、芯片、AI大模型、农业、国际政治、医药审批 -3
    "汽车", "新能源车", "电动车", "房产", "楼市", "房价",
    "游戏", "手游", "芯片", "半导体", "大模型", "AI大模型",
    "农业", "国际政治", "医药审批", "临床试验",
]

NEGATIVE_JUNK_KEYWORDS = [
    # 招聘、公益、投诉电话、免责声明、导航页 -5
    "招聘", "求职", "简历", "公益", "慈善", "投诉电话",
    "免责声明", "导航页", "网站地图", "版权所有", "备案号",
]

# ── 内容农场域名黑名单 ──
CONTENT_FARM_DOMAINS = {
    "kgblm.com",           # 通用内容聚合站，改日期重发旧闻
    # sohu.com 已移除 — 搜狐有原创新闻报道，不能作为内容农场处理
    # 低质搜狐号内容通过关键词和噪音拦截处理
}
CONTENT_FARM_SUFFIXES = [
    # 常见内容农场模式
    # 注意：不要加过于宽泛的域名后缀，会误杀正常新闻源
]

# ── 季节错位关键词 ──
# 大促关键词 → 合理月份范围
SEASONAL_CAMPAIGN_CHECKS = [
    # (关键词列表, 允许月份集合, 违规说明)
    (["双11", "双十一", "天猫双11", "京东双11", "双11预售", "双十一预售"],
     {10, 11, 12, 1},     # 仅10月-1月合理
     "双11内容出现在非促销季（5月），疑为旧闻改日期重发"),
    (["618", "618大促", "618预售"],
     {5, 6, 7},            # 5-7月合理
     "618内容但月份不正确"),
    (["双12", "双十二"],
     {11, 12, 1},
     "双12内容出现在非促销季"),
    (["年货节"],
     {1, 2},
     "年货节内容出现在非促销季"),
]


def check_seasonal_mismatch(title: str, summary: str = "") -> str:
    """检查文章是否包含季节错位的大促关键词。
    
    Returns:
        空字符串表示通过，否则返回拒绝原因。
    """
    from datetime import datetime, timezone, timedelta
    CST = timezone(timedelta(hours=8))
    now = datetime.now(CST)
    current_month = now.month
    
    combined = f"{title} {summary}".lower()
    
    for keywords, allowed_months, reason in SEASONAL_CAMPAIGN_CHECKS:
        if current_month not in allowed_months:
            for kw in keywords:
                if kw.lower() in combined:
                    return reason
    
    return ""

# 合并分类用于统计
_KEYWORD_CATEGORIES = {
    "watsons": (WATSONS_KEYWORDS, 5),
    "instant_retail": (INSTANT_RETAIL_KEYWORDS, 4),
    "beauty_care": (BEAUTY_CARE_KEYWORDS, 3),
    "competitor": (COMPETITOR_KEYWORDS, 3),
    "b2b_channel": (B2B_CHANNEL_KEYWORDS, 2),
    "business_var": (BUSINESS_VAR_KEYWORDS, 1),
    "negative_general": (NEGATIVE_GENERAL_KEYWORDS, -3),
    "negative_topic": (NEGATIVE_TOPIC_KEYWORDS, -3),
    "negative_junk": (NEGATIVE_JUNK_KEYWORDS, -5),
}


# ===================== 配置加载 =====================


def load_yaml(filepath: str) -> dict:
    p = Path(filepath)
    if not p.exists():
        raise FileNotFoundError(f"配置文件不存在: {filepath}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(project_root: str, rel_path: str) -> str:
    return str(Path(project_root) / rel_path)


def load_scoring_config(scoring_file: str) -> dict:
    """加载 scoring.yaml 中的阈值配置。"""
    config = load_yaml(scoring_file)
    return config


def _get_source_tier(article: dict) -> int:
    """统一获取 source_tier（兼容 tier 和 source_tier 两种字段名）。"""
    t = article.get("source_tier")
    if t is None:
        t = article.get("tier")
    if t is None:
        return 3
    if isinstance(t, str):
        try:
            return int(t)
        except (ValueError, TypeError):
            pass
        tier_map = {
            "tier1_direct_signal": 1, "tier1": 1, "direct_signal": 1,
            "tier2_analysis": 2, "tier2": 2,
            "tier3_anchor": 3, "tier3": 3,
            "tier4_clue": 4, "tier4": 4,
        }
        return tier_map.get(t, 3)
    if isinstance(t, (int, float)):
        return int(t)
    return 3


def compute_rule_score(
    article: dict,
    extra_keywords: Optional[dict] = None,
) -> Tuple[int, List[str]]:
    """计算单篇文章的规则评分和原因列表。
    
    Returns:
        (rule_score, rule_reasons)
    """
    score = 0
    reasons: List[str] = []

    # 合并判断文本（防御 list 类型字段）
    def _to_str(val):
        if isinstance(val, list):
            return " ".join(str(v) for v in val if v)
        return str(val) if val else ""

    combined = " ".join([
        _to_str(article.get("title", "")),
        _to_str(article.get("summary", "")),
        _to_str(article.get("content", ""))[:2000],
    ]).lower()

    # ── 加分 ──
    # 屈臣氏直接命中 +5
    for kw in WATSONS_KEYWORDS:
        if kw.lower() in combined:
            score += 5
            reasons.append(f"+5 命中屈臣氏: {kw}")
            break  # 只加一次

    # 即时零售平台词 +4
    for kw in INSTANT_RETAIL_KEYWORDS:
        if kw.lower() in combined:
            score += 4
            reasons.append(f"+4 命中即时零售: {kw}")
            break

    # 美妆个护品类词 +3
    matched_beauty = [kw for kw in BEAUTY_CARE_KEYWORDS if kw.lower() in combined]
    if matched_beauty:
        score += 3
        reasons.append(f"+3 命中美妆个护: {', '.join(matched_beauty[:3])}")

    # 竞对词 +3
    for kw in COMPETITOR_KEYWORDS:
        if kw.lower() in combined:
            score += 3
            reasons.append(f"+3 命中竞对: {kw}")
            break

    # B2C 渠道词 +2
    for kw in B2B_CHANNEL_KEYWORDS:
        if kw.lower() in combined:
            score += 2
            reasons.append(f"+2 命中B2C渠道: {kw}")
            break

    # 经营变量词 +1
    matched_vars = [kw for kw in BUSINESS_VAR_KEYWORDS if kw.lower() in combined]
    if matched_vars:
        score += 1
        reasons.append(f"+1 命中经营变量: {', '.join(matched_vars[:3])}")

    # 额外关键词（来自 keywords.yaml 的匹配结果）
    if extra_keywords:
        # 如果文章已有 matched_keywords，额外加一点
        mk = article.get("matched_keywords", [])
        if mk and len(mk) >= 3:
            score += 1
            reasons.append(f"+1 关键词命中≥3: {', '.join(mk[:3])}")

    # source_tier 加分
    tier = _get_source_tier(article)
    if tier == 1:
        score += 2
        reasons.append("+2 source_tier=1")
    elif tier == 2:
        score += 1
        reasons.append("+1 source_tier=2")

    # ── time_status 加分 ──
    ts = article.get("time_status", "")
    # 搜索源和 tavily 允许旧文章，只减1分；其他源减2分
    search_sources = ("search", "tavily", "gap")
    is_search_source = any(s in (article.get("source_name", "") + article.get("source_type", "")) for s in search_sources)
    allow_old = article.get("allow_old", False)

    if ts == "in_window":
        score += 2
        reasons.append("+2 time_status=in_window")
    elif ts == "near_window":
        score += 1
        reasons.append("+1 time_status=near_window")
    elif ts == "old":
        if is_search_source or allow_old:
            score -= 1
            reasons.append("-1 time_status=old(搜索源/allow_old)")
        else:
            score -= 2
            reasons.append("-2 time_status=old")

    # ── freshness_status 调整 ──
    freshness = article.get("freshness_status", "")
    if freshness == "week_fallback":
        score -= 1
        reasons.append("-1 freshness_status=week_fallback(周补搜)")
    elif freshness == "newly_discovered":
        reasons.append("+0 freshness_status=newly_discovered(网址监测)")
    elif freshness == "bootstrap_seen":
        score -= 2
        reasons.append("-2 freshness_status=bootstrap_seen(首次运行历史URL)")

    # ── 减分 ──
    # 泛科技/泛财经/泛宏观 -3
    for kw in NEGATIVE_GENERAL_KEYWORDS:
        if kw.lower() in combined:
            score -= 3
            reasons.append(f"-3 泛领域: {kw}")
            break

    # 汽车、房产、游戏等 -3
    for kw in NEGATIVE_TOPIC_KEYWORDS:
        if kw.lower() in combined:
            score -= 3
            reasons.append(f"-3 无关主题: {kw}")
            break

    # 招聘、公益等 -5
    for kw in NEGATIVE_JUNK_KEYWORDS:
        if kw.lower() in combined:
            score -= 5
            reasons.append(f"-5 垃圾内容: {kw}")
            break

    # time_status 加减分已在上面处理（搜索源和 allow_old 源减1分，其他源减2分）

    # title 为空或 url 为空 -5
    title = article.get("title", "") or ""
    url = article.get("url", "") or ""
    if not title.strip():
        score -= 5
        reasons.append("-5 title为空")
    if not url.strip():
        score -= 5
        reasons.append("-5 url为空")

    # content + summary 极短且关键词为空 -2
    content_len = len(article.get("content", "") or "")
    summary_len = len(article.get("summary", "") or "")
    mk = article.get("matched_keywords", [])
    if content_len < 50 and summary_len < 50 and not mk:
        score -= 2
        reasons.append("-2 内容极短且无关键词")

    return score, reasons


def make_rule_decision(rule_score: int) -> str:
    """根据 rule_score 做初步决策。
    
    V3: review 阈值从2升到3，避免单关键词匹配触发大量 LLM 调用。
    """
    if rule_score >= 6:
        return "keep"
    elif rule_score >= 3:
        return "review"
    else:
        return "reject"


# ===================== LLM 语义复核 =====================


def _build_llm_prompt(article: dict) -> str:
    """构建 LLM 复核 prompt。"""
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    content = (article.get("content", "") or "")[:1500]  # 截取避免过长
    source = article.get("source_name", "")
    matched_kw = article.get("matched_keywords", [])

    return f"""请判断以下文章是否与"屈臣氏即时零售×个护美妆经营"相关。

文章信息：
- 标题：{title}
- 来源：{source}
- 摘要：{summary}
- 正文（前1500字）：{content}
- 已匹配关键词：{', '.join(str(k) for k in (matched_kw or []))}

请严格按以下 JSON 格式回复，不要添加任何其他文字：

{{
  "llm_relevance": "high|medium|low|none",
  "business_relevance_type": "direct|indirect|background|irrelevant",
  "related_channels": [],
  "related_categories": [],
  "business_variables": [],
  "reason": "一句话说明判断理由",
  "recommended_pool": "main|reference|reject",
  "confidence": "high|medium|low"
}}

判断标准：
- direct: 直接讨论屈臣氏电商经营、即时零售×个护美妆
- indirect: 间接影响屈臣氏经营（竞对动态、平台政策变化、品类趋势）
- background: 提供行业背景但不直接影响今日经营判断
- irrelevant: 明显无关"""


# ===================== 模型路由（模块级单次初始化） =====================

_MODEL_FOR_FILTER = None
_MODEL_PARAMS_FOR_FILTER = {}


def _init_model_router(logger_override=None):
    """初始化模型路由（模块级单例，避免每次调用重复导入）。"""
    global _MODEL_FOR_FILTER, _MODEL_PARAMS_FOR_FILTER
    if _MODEL_FOR_FILTER is not None:
        return _MODEL_FOR_FILTER, _MODEL_PARAMS_FOR_FILTER
    try:
        _utils_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "utils")
        if _utils_dir not in sys.path:
            sys.path.insert(0, _utils_dir)
        from skills.utils.model_router import get_model_for_skill, get_model_params
        _MODEL_FOR_FILTER, _ = get_model_for_skill("filter_relevant_articles")
        _MODEL_PARAMS_FOR_FILTER = get_model_params("filter_relevant_articles")
        log = logger_override or logging.getLogger("filter")
        log.info(f"filter_relevant_articles 使用模型: {_MODEL_FOR_FILTER}")
    except Exception:
        _MODEL_FOR_FILTER = None
        _MODEL_PARAMS_FOR_FILTER = {}
    return _MODEL_FOR_FILTER, _MODEL_PARAMS_FOR_FILTER


def llm_review_article(
    article: dict,
    llm_client,
    logger: logging.Logger,
) -> dict:
    """对单篇文章进行 LLM 语义复核。
    
    Returns:
        dict with keys: llm_relevance, business_relevance_type,
        related_channels, related_categories, business_variables,
        reason, recommended_pool, confidence, llm_reviewed (bool)
    """
    default_result = {
        "llm_relevance": "none",
        "business_relevance_type": "irrelevant",
        "related_channels": [],
        "related_categories": [],
        "business_variables": [],
        "reason": "",
        "recommended_pool": "reject",
        "confidence": "low",
        "llm_reviewed": False,
    }

    if llm_client is None or not llm_client.available:
        logger.warning("LLM 不可用，跳过语义复核")
        default_result["reason"] = "LLM不可用"
        return default_result

    prompt = _build_llm_prompt(article)

    # ── 模型路由：使用模块级单例（已在 Phase 2 入口初始化）──
    _model_for_skill = _MODEL_FOR_FILTER
    _model_params_for_skill = _MODEL_PARAMS_FOR_FILTER or {}

    try:
        result = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "你是一个即时零售×个护美妆行业的经营情报分析专家。"
                "你必须严格按照用户要求的JSON格式输出，"
                "不要输出任何解释性文字、不要输出思考过程、"
                "不要使用Markdown代码块包裹，只输出纯JSON。"
            ),
            response_format="json",
            temperature=_model_params_for_skill.get("temperature", 0.2),
            max_tokens=_model_params_for_skill.get("max_tokens", 2048),
            model=_model_for_skill,
        )

        if not result.get("ok"):
            logger.warning(f"LLM 调用失败: {result.get('error', 'unknown')}")
            default_result["reason"] = f"LLM调用失败: {result.get('error', 'unknown')[:100]}"
            return default_result

        parsed = result.get("parsed")
        content = result.get("content", "")

        if parsed and isinstance(parsed, dict):
            parsed["llm_reviewed"] = True
            # 校验必要字段，缺失的用默认值填充
            for field in ["llm_relevance", "business_relevance_type",
                          "recommended_pool", "confidence"]:
                if field not in parsed:
                    parsed[field] = default_result.get(field)
            return parsed
        else:
            # JSON 解析失败 — 记录原始内容以便调试
            content_preview = content[:300] if content else "(空)"
            logger.warning(f"LLM JSON 解析失败, 原始内容前300字: {content_preview}")
            default_result["reason"] = "LLM JSON解析失败"
            default_result["llm_reviewed"] = True
            return default_result

    except Exception as e:
        logger.warning(f"LLM 复核异常: {e}")
        default_result["reason"] = f"LLM异常: {str(e)[:100]}"
        return default_result



# ===================== 最终分池（V3: 精准准入规则） ====================


def decide_final_pool(
    article: dict,
    rule_score: int,
    rule_decision: str,
    llm_result: Optional[dict],
    page_type: Optional[str] = None,
    region_tag: Optional[str] = None,
    noise_flags: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """决定文章最终属于哪个池（V4: 噪音过滤 + 精准准入规则）。

    准入规则（从上到下依次判定）：

    0. 噪音硬拦截: page_type 在阻断列表 + region 过滤
       - product_page, homepage, social_post, promotion, job, unknown 不得进入 cleaned
       - hk/tw 地区的屈臣氏促销页不得进入 cleaned
    1. 硬拒绝: url/title 空、垃圾内容、old（非搜索源）、unknown_time 无关键词
    2. freshness_status=newly_discovered（web_monitor 文章）
       + 命中屈臣氏/核心平台/竞对 + score>=4 → 直接进 cleaned
       + score >= 5 → reference（LLM候选，需要 LLM 确认才可升级到 main）
       + unknown_time 的 web_monitor → reference（低阈值）或 reject
       + 其余 → reference 或 reject
    3. freshness_status=day_primary（Tavily 日搜索结果）
       + score >= 6 → cleaned
       + core_hit + score >= 4 → cleaned
       + 其他按 review/reject 规则
    4. freshness_status=week_fallback（Tavily 周补搜）
       + 默认 reference
       + 除非直接命中屈臣氏/核心平台 + score>=5 → 可进 cleaned
    5. 普通文章按 rule_score + time_status + core_hit 判定

    只有当页面同时满足:
    - freshness_status 或 time_status 显示新
    - 且命中屈臣氏/核心平台/竞对/明确动作词
    才能进入 cleaned（噪音类型页面即使被解封也不能进 cleaned）。

    Returns:
        (final_pool, final_reason)
        final_pool: "main" | "reference" | "reject"
    """
    url = article.get("url", "") or ""
    title = article.get("title", "") or ""
    time_status = article.get("time_status", "")
    matched_keywords = article.get("matched_keywords", [])
    freshness = article.get("freshness_status", "")
    source_type = article.get("source_type", "")

    # ── Phase 0: 噪音硬拦截（V4 新增）──
    is_noise_blocked = False
    noise_reason = ""

    if page_type is None:
        page_type = classify_page_type(article)
    if region_tag is None:
        region_tag = classify_region_tag(article)
    if noise_flags is None:
        noise_flags = compute_noise_flags(article, page_type, region_tag)

    # 不允许进入 cleaned 的 page_type
    if page_type in CLEANED_BLOCKED_PAGE_TYPES:
        is_noise_blocked = True
        noise_reason = f"page_type={page_type}"

        # 例外: 高分 social_post + 核心命中 + 近期 → 允许 reference
        # 但仍然不允许进 cleaned

    # HK/TW 屈臣氏促销 → reject 或最多 reference
    if "hk_tw_watsons_promo" in noise_flags:
        is_noise_blocked = True
        noise_reason = f"hk_tw_watsons_promo"
        # 港台促销页直接 reject
        return "reject", f"噪音拦截: {noise_reason}"

    # HK/TW 地区非核心内容 → 降级
    if region_tag in (REGION_HK, REGION_TW, REGION_OVERSEAS):
        return "reject", f"噪音拦截: 非大陆地区 region={region_tag}"

    # ── 硬拒绝条件 ──
    if not url.strip():
        return "reject", "url为空"
    if not title.strip():
        return "reject", "title为空"

    # ── 内容农场域名黑名单 ──
    domain = ""
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        pass
    if domain in CONTENT_FARM_DOMAINS:
        return "reject", f"内容农场: {domain}"
    # 泛匹配：常见内容农场后缀
    for farm_suffix in CONTENT_FARM_SUFFIXES:
        if domain.endswith(farm_suffix):
            return "reject", f"内容农场(泛): {domain}"

    # ── 季节错位检测：大促关键词出现在错误月份 ──
    seasonal_mismatch = check_seasonal_mismatch(title, article.get("summary", ""))
    if seasonal_mismatch:
        return "reject", seasonal_mismatch

    # 垃圾内容硬拒绝
    content_lower = " ".join([title, article.get("summary", "") or ""]).lower()
    for junk_kw in NEGATIVE_JUNK_KEYWORDS:
        if junk_kw.lower() in content_lower and rule_score <= 1:
            return "reject", f"垃圾内容: {junk_kw}"
    
    # ── 大促时效性检测 ──
    # campaign_info 已在外部计算，通过 article 临时字段传入
    # historical_campaign → reject（旧促销无关当日情报）
    # unknown_campaign_time + (unknown_time|old) → reject
    # unknown_campaign_time + in_window/near_window → reference
    # current/upcoming/retrospective → 正常走评分逻辑
    campaign_temporality = article.get("_campaign_temporality", "not_campaign")
    campaign_year = article.get("_campaign_year")
    campaign_name = article.get("_campaign_name", "")
    if campaign_temporality == "historical_campaign":
        return "reject", f"历史大促: {campaign_name}{campaign_year}年已过"
    if campaign_temporality == "unknown_campaign_time":
        if time_status in ("unknown_time", "old"):
            return "reject", f"大促年份不明+{time_status}: 无法确认时效"
        return "reference", f"大促年份不明+{time_status}: 降级为reference"

    # ── old 文章处理 ──
    # 旧闻一律拒绝。merge_raw_articles 的硬日期门已在前端过滤，
    # 此处作为兜底，不再给搜索源开后门。
    if time_status == "old":
        return "reject", f"time_status=old rule_score={rule_score}"

    # ── 核心命中检测（屈臣氏/核心平台/竞对） ──
    core_hit, core_type = has_core_hit(article)

    # ── 噪音页面降级（V4 新增）──
    # 噪音类型页面不得进入 cleaned，只能 reference 或 reject
    def _noise_downgrade(target_pool: str, reason: str) -> Tuple[str, str]:
        """噪音页面降级：main → reference, reference → reject"""
        if not is_noise_blocked:
            return target_pool, reason
        if target_pool == "main":
            # 噪音页面降级到 reference 而非 main（保留但不推送到报告）
            return "reference", f"噪音降级({noise_reason}): {reason}"
        if target_pool == "reference":
            # 噪音页面的 reference 也可能降级为 reject
            if page_type in (PAGE_TYPE_SOCIAL_POST, PAGE_TYPE_HOMEPAGE, PAGE_TYPE_JOB):
                return "reject", f"噪音拦截({noise_reason}): {reason}"
            return "reference", f"噪音保留({noise_reason}): {reason}"
        return target_pool, reason

    # ═══════════════════════════════════════════════
    # freshness_status=bootstrap_seen（首次运行，历史URL，不算新发现）
    # ═══════════════════════════════════════════════
    if freshness == "bootstrap_seen":
        # bootstrap_seen 绝不进 cleaned，最高只能进 reference
        if core_hit and rule_score >= 5:
            return _noise_downgrade("reference", f"bootstrap_seen+core_{core_type} score={rule_score}→reference(历史背景)")
        if rule_score >= 6:
            return _noise_downgrade("reference", f"bootstrap_seen score={rule_score}→reference(历史背景)")
        if rule_score >= 3:
            return "reference", f"bootstrap_seen score={rule_score}→reference"
        return "reject", f"bootstrap_seen score={rule_score}<3→reject"

    # ═══════════════════════════════════════════════
    # freshness_status=newly_discovered（web_monitor 文章）
    # ═══════════════════════════════════════════════
    if freshness == "newly_discovered":
        # 核心命中 → 可直接进 cleaned
        if core_hit and rule_score >= 4:
            return _noise_downgrade("main", f"newly_discovered+core_{core_type} score={rule_score}>=4→main")
        # rule_score >= 5 → reference（LLM 候选，需要 LLM 确认才可升级到 main）
        if rule_score >= 5:
            return _noise_downgrade("reference", f"newly_discovered score={rule_score}>=5→reference(LLM候选)")
        # time_status=unknown_time 的 web_monitor 特殊处理
        if time_status == "unknown_time":
            if core_hit:
                return _noise_downgrade("reference", f"newly_discovered+unknown_time+core_{core_type}→reference")
            if rule_score >= 2 and len(matched_keywords) >= 2:
                return _noise_downgrade("reference", f"newly_discovered+unknown_time kw={len(matched_keywords)} score={rule_score}→reference")
            return "reject", f"newly_discovered+unknown_time score={rule_score}<2→reject"
        # 有时间但分数不够高
        if rule_score >= 3:
            return _noise_downgrade("reference", f"newly_discovered score={rule_score}→reference")
        return "reject", f"newly_discovered score={rule_score}<3→reject"


    # ═══════════════════════════════════════════════
    # freshness_status=week_fallback（Tavily 周补搜）

    # ═══════════════════════════════════════════════
    if freshness == "week_fallback":
        # 直接命中屈臣氏/核心平台强事件 → 可进 cleaned
        if core_hit and rule_score >= 5:
            return _noise_downgrade("main", f"week_fallback+core_{core_type} score={rule_score}>=5→main")
        # 其余 → 最多 reference
        if rule_score >= 3 and (core_hit or matched_keywords):
            return _noise_downgrade("reference", f"week_fallback score={rule_score}→reference")
        return "reject", f"week_fallback score={rule_score}<3→reject"


    # ═══════════════════════════════════════════════
    # freshness_status=day_primary（Tavily 日搜索）

    # ═══════════════════════════════════════════════
    if freshness == "day_primary":
        # rule_score >= 6 → cleaned（传统 keep 阈值）
        if rule_score >= 6:
            return _noise_downgrade("main", f"day_primary score={rule_score}>=6→main")
        # 核心命中 + score >= 4 → cleaned
        if core_hit and rule_score >= 4:
            return _noise_downgrade("main", f"day_primary+core_{core_type} score={rule_score}>=4→main")
        # Fall through to normal flow for review/reject cases


    # ═══════════════════════════════════════════════
    # unknown_time 通用处理（非 newly_discovered）

    # ═══════════════════════════════════════════════
    # uncertain_date 处理（CloakBrowser 日期提取失败但可能仍是新文章）
    # ═══════════════════════════════════════════════
    if time_status == "uncertain_date":
        # CloakBrowser 文章：日期提取失败，但采集端已过滤>3天旧文
        # V5: CloakBrowser 是最可靠的新鲜来源，核心命中应直接进 main
        is_cloakbrowser = bool(
            article.get("collector") == "cloakbrowser" or
            "cloakbrowser" in article.get("_source_file", "")
        )
        if is_cloakbrowser and core_hit and rule_score >= 3:
            return _noise_downgrade("main", f"cloakbrowser+uncertain_date+core_{core_type} score={rule_score}>=3→main")
        if core_hit and rule_score >= 5:
            return _noise_downgrade("main", f"uncertain_date+core_{core_type} score={rule_score}>=5→main")
        if core_hit:
            return _noise_downgrade("reference", f"uncertain_date+core_{core_type}→reference")
        if rule_score >= 4:
            return _noise_downgrade("reference", f"uncertain_date score={rule_score}>=4→reference")
        return "reject", f"uncertain_date score={rule_score}<4→reject"

    # ═══════════════════════════════════════════════
    if time_status == "unknown_time":
        search_types = {"xcrawl", "tavily", "search", "gap"}
        is_search = source_type in search_types

        # 核心命中 → reference（即使无日期也保留）
        if core_hit:
            return _noise_downgrade("reference", f"unknown_time+core_{core_type}→reference")

        if is_search and len(matched_keywords) >= 3 and rule_score >= 3:
            return _noise_downgrade("reference", f"unknown_time(搜索源) kw={len(matched_keywords)} score={rule_score}→reference")
        if is_search and rule_score >= 5:
            return _noise_downgrade("reference", f"unknown_time(搜索源) score={rule_score}→reference")
        return "reject", "time_status=unknown_time（无日期，无法确认时效性）"


    # ═══════════════════════════════════════════════
    # LLM 推荐（所有已通过硬拒绝+特殊来源逻辑的文章）

    # ═══════════════════════════════════════════════
    if llm_result and llm_result.get("llm_reviewed"):
        llm_pool = llm_result.get("recommended_pool", "reject")
        llm_conf = llm_result.get("confidence", "low")

        if llm_pool == "main" and rule_score >= 3:
            return _noise_downgrade("main", f"LLM推荐main (confidence={llm_conf})")
        elif llm_pool == "reference":
            return _noise_downgrade("reference", f"LLM推荐reference (confidence={llm_conf})")
        elif llm_pool == "reject":
            if rule_score >= 6:
                return _noise_downgrade("reference", f"rule=keep但LLM=reject，降级为reference")
            return "reject", f"LLM推荐reject (confidence={llm_conf})"


    # ═══════════════════════════════════════════════
    # 纯规则决策（day_primary/non-special 文章走这里）

    # ═══════════════════════════════════════════════
    if rule_decision == "keep":
        return _noise_downgrade("main", f"rule_score={rule_score}>=6")

    if rule_decision == "review":
        # review 文章：score 3-5
        # core hit + in_window → main（核心命中+当日 = 强信号）
        if core_hit and rule_score >= 4 and time_status == "in_window":
            return _noise_downgrade("main", f"review+core_{core_type}+in_window score={rule_score}>=4→main")
        # 无关键词命中 → reject（防止 source_tier+time 分数虚高）
        if not core_hit and not matched_keywords:
            return "reject", f"rule=review, score={rule_score}, no keywords→reject"
        # in_window + score>=5 → main（当日新闻，相关性强）
        if rule_score >= 5 and time_status == "in_window":
            return _noise_downgrade("main", f"rule=review, score={rule_score}>=5, in_window→main")
        if rule_score >= 4:
            return _noise_downgrade("reference", f"rule=review, score={rule_score}>=4→reference")
        if rule_score >= 2:
            if time_status in ("in_window", "near_window"):
                return _noise_downgrade("reference", f"rule=review, score={rule_score}, time={time_status}→reference")
            return _noise_downgrade("reference", f"rule=review, score={rule_score}→reference(降级保留)")
        return _noise_downgrade("reference", f"rule=review, score={rule_score}→reference")

    # rule_decision == "reject"
    if time_status in ("in_window", "near_window") and rule_score >= 0 and (core_hit or matched_keywords):
        return _noise_downgrade("reference", f"rule=reject但time={time_status}→reference(降级保留)")
    return "reject", f"rule_score={rule_score}<3"


def _load_parallel_config(project_root: str) -> dict:
    """加载 parallel.yaml。"""
    try:
        _pp = os.path.join(project_root, "config", "parallel.yaml")
        import yaml
        with open(_pp, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ===================== 主函数 =====================


def filter_relevant_articles(
    project_root: str,
    date: str,
    raw_file: Optional[str] = None,
    keywords_file: str = "config/keywords.yaml",
    scoring_file: str = "config/scoring.yaml",
    use_llm: bool = True,
    llm_mode: str = "borderline_only",
) -> dict:
    """过滤主函数。
    
    Args:
        project_root: 项目根目录
        date: 日期 YYYY-MM-DD
        raw_file: 覆盖输入文件路径
        keywords_file: 关键词配置文件（相对项目根）
        scoring_file: 评分配置文件（相对项目根）
        use_llm: 是否使用 LLM 语义复核
        llm_mode: LLM 模式 "borderline_only" 或 "all"
    
    Returns:
        标准结果 dict
    """
    errors: List[str] = []

    # ── 路径 ──
    if not raw_file:
        # 优先使用合并后的文件，否则回退到单独文件
        merged_file = resolve_path(project_root, f"data/raw/{date}/raw_articles_all.json")
        single_file = resolve_path(project_root, f"data/raw/{date}/raw_articles.json")
        raw_file = merged_file if os.path.exists(merged_file) else single_file
    keywords_path = resolve_path(project_root, keywords_file)
    scoring_path = resolve_path(project_root, scoring_file)
    cleaned_dir = resolve_path(project_root, f"data/cleaned/{date}")
    rejected_dir = resolve_path(project_root, f"data/rejected/{date}")
    log_dir = resolve_path(project_root, f"data/logs/{date}")

    os.makedirs(cleaned_dir, exist_ok=True)
    os.makedirs(rejected_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    cleaned_file = os.path.join(cleaned_dir, "cleaned_articles.json")
    reference_file = os.path.join(cleaned_dir, "reference_articles.json")
    rejected_file = os.path.join(rejected_dir, "rejected_articles.json")
    log_file = os.path.join(log_dir, "filter_relevant_articles.log")

    # ── 日志 ──
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger = logging.getLogger("filter")
    logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)

    logger.info("=" * 60)
    logger.info(f"开始过滤: date={date}")
    logger.info(f"use_llm={use_llm}, llm_mode={llm_mode}")
    logger.info(f"输入文件: {raw_file}")
    logger.info("=" * 60)

    # ── 加载原始数据 ──
    try:
        with open(raw_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        error_msg = f"无法加载原始数据: {e}"
        logger.error(error_msg)
        logger.removeHandler(file_handler)
        file_handler.close()
        return {"ok": False, "date": date, "input_file": raw_file,
                "cleaned_file": cleaned_file, "reference_file": reference_file,
                "rejected_file": rejected_file, "log_file": log_file,
                "raw_count": 0, "cleaned_count": 0, "reference_count": 0,
                "rejected_count": 0, "llm_reviewed_count": 0, "errors": [error_msg]}

    articles = raw_data.get("articles", [])
    raw_count = len(articles)
    logger.info(f"加载原始文章: {raw_count} 条")

    # ── 加载关键词配置 ──
    try:
        keywords_config = load_yaml(keywords_path)
    except Exception as e:
        logger.warning(f"无法加载 keywords.yaml: {e}，使用内置关键词")
        keywords_config = {}

    # ── 加载评分配置（暂未直接使用，预留） ──
    try:
        scoring_config = load_yaml(scoring_path)
    except Exception as e:
        logger.warning(f"无法加载 scoring.yaml: {e}")
        scoring_config = {}

    # ── LLM 客户端 ──
    llm_client = None
    llm_reviewed_count = 0
    llm_failed_count = 0

    if use_llm:
        try:
            # 导入 LLM 客户端
            utils_path = os.path.join(project_root, "skills", "utils")
            if utils_path not in sys.path:
                sys.path.insert(0, project_root)
            from skills.utils.llm_client import get_llm_client
            llm_client = get_llm_client()
            if llm_client.available:
                logger.info(f"LLM 客户端就绪: {llm_client.available_keys} 个 Key 可用, 模型={llm_client.model}")
            else:
                logger.warning("LLM 客户端无可用 Key，降级为纯规则过滤")
                llm_client = None
                use_llm = False
        except Exception as e:
            logger.warning(f"LLM 客户端初始化失败: {e}，降级为纯规则过滤")
            llm_client = None
            use_llm = False

    # ── Phase 1: 规则评分（快速，串行） ──
    cleaned_articles: List[dict] = []
    reference_articles: List[dict] = []
    rejected_articles: List[dict] = []

    by_time_status = Counter()
    by_freshness = Counter()
    by_source = Counter()
    by_page_type = Counter()
    by_region = Counter()
    by_noise = Counter()
    reject_reasons = Counter()
    all_matched_keywords = Counter()

    # 中间结果：存储每篇文章的规则评分和是否需要 LLM
    _phase1_results = []  # list of (article, rule_score, rule_reasons, rule_decision, need_llm, page_type, region_tag, noise_flags)
    llm_reviewed_count = 0
    llm_failed_count = 0

    for i, article in enumerate(articles):
        # ── 字段类型标准化：防御 list/非str 类型 ──
        for _field in ("title", "summary", "description", "content"):
            _val = article.get(_field)
            if isinstance(_val, list):
                article[_field] = " ".join(str(v) for v in _val if v)
            elif _val is not None and not isinstance(_val, str):
                article[_field] = str(_val)

        source_name = article.get("source_name", "unknown")
        time_status = article.get("time_status", "unknown_time")
        by_time_status[time_status] += 1
        by_freshness[article.get("freshness_status", "") or "none"] += 1
        by_source[source_name] += 1

        for kw in article.get("matched_keywords", []):
            all_matched_keywords[str(kw)] += 1

        rule_score, rule_reasons = compute_rule_score(article)
        rule_decision = make_rule_decision(rule_score)

        # ── V4: 计算页面类型、地区标签、噪音标记 ──
        page_type = classify_page_type(article)
        region_tag = classify_region_tag(article)
        noise_flags = compute_noise_flags(article, page_type, region_tag)
        by_page_type[page_type] += 1
        by_region[region_tag] += 1
        for nf in noise_flags:
            by_noise[nf] += 1

        # 判断是否需要 LLM 复核
        need_llm = False
        if use_llm and llm_client:
            if llm_mode == "borderline_only":
                if rule_decision == "review":
                    need_llm = True
                # source_tier 高但 rule_score 极低 → 可能是关键词未匹配，LLM 复核
                elif _get_source_tier(article) <= 2 and rule_score <= 1:
                    need_llm = True
            elif llm_mode == "all":
                need_llm = True

        _phase1_results.append((article, rule_score, rule_reasons, rule_decision, need_llm, page_type, region_tag, noise_flags))

        if (i + 1) % 100 == 0:
            logger.info(f"  Phase1 进度: {i + 1}/{raw_count}")

    logger.info(f"Phase1 完成: {raw_count} 篇规则评分, 需要LLM复核: "
                f"{sum(1 for _,_,_,_,nl,_,_,_ in _phase1_results if nl)} 篇")

    # ── Phase 2: LLM 语义复核（并行批量，配置驱动） ──
    _llm_results = {}  # idx → llm_result dict

    # 从 parallel.yaml 读取配置
    _filter_cfg = {}
    _FILTER_MAX_LLM = 100
    _FILTER_BATCH_SIZE = 5
    try:
        _par_yaml = _load_parallel_config(project_root)
        _filter_cfg = _par_yaml.get("filter_relevant_articles", {}).get("llm_review_parallel", {})
        if _filter_cfg.get("enabled", True):
            _FILTER_MAX_LLM = _filter_cfg.get("max_llm_articles", 100)
            _FILTER_BATCH_SIZE = _filter_cfg.get("batch_size", 5)
            _timeout_single = _filter_cfg.get("single_timeout", 90)
            _model_strategy = _filter_cfg.get("model_strategy", {})
            # Pre-load model strategy into env so llm_review_article uses it
            if _model_strategy.get("default"):
                _MODEL_FOR_FILTER = _model_strategy["default"]
                _MODEL_PARAMS_FOR_FILTER = {
                    "temperature": 0.2,
                    "max_tokens": 2048,
                }
            if _model_strategy.get("skip_thinking") and _MODEL_FOR_FILTER == "LongCat-Flash-Thinking":
                _MODEL_FOR_FILTER = "LongCat-Flash-Lite"
    except Exception:
        pass

    _llm_indices = [idx for idx, (_,_,_,_, nl,_,_,_) in enumerate(_phase1_results) if nl]
    
    # 超限时优先保留高 tier 低 score 的文章（更需要 LLM 协助判断）
    if len(_llm_indices) > _FILTER_MAX_LLM:
        _prioritized = sorted(_llm_indices, key=lambda idx: (
            _get_source_tier(_phase1_results[idx][0]),  # tier 越小优先级越高
            _phase1_results[idx][1],  # score 越低优先级越高（需要 LLM 帮助）
        ))
        _llm_indices = _prioritized[:_FILTER_MAX_LLM]
        logger.info(f"  Phase2 LLM 超限: {len(_prioritized)} → {_FILTER_MAX_LLM} (按 tier/score 优先级截断)")
    if _llm_indices:
        _llm_batch_size = _FILTER_BATCH_SIZE
        _init_model_router(logger)

        for _batch_start in range(0, len(_llm_indices), _llm_batch_size):
            _batch_indices = _llm_indices[_batch_start:_batch_start + _llm_batch_size]
            logger.info(f"  Phase2 LLM 批量: {_batch_start + 1}-{min(_batch_start + _llm_batch_size, len(_llm_indices))}"
                        f"/{len(_llm_indices)}")

            if len(_batch_indices) == 1:
                # 单篇直接处理
                idx = _batch_indices[0]
                article, _, _, _, _, _, _, _ = _phase1_results[idx]
                llm_result = llm_review_article(article, llm_client, logger)
                _llm_results[idx] = llm_result
                if llm_result.get("llm_reviewed"):
                    llm_reviewed_count += 1
                else:
                    llm_failed_count += 1
            else:
                # 并行处理批量
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=len(_batch_indices)) as executor:
                    _futures = {}
                    for idx in _batch_indices:
                        article, _, _, _, _, _, _, _ = _phase1_results[idx]
                        _futures[executor.submit(llm_review_article, article, llm_client, logger)] = idx

                    for future in as_completed(_futures):
                        idx = _futures[future]
                        try:
                            llm_result = future.result(timeout=90)
                            _llm_results[idx] = llm_result
                            if llm_result.get("llm_reviewed"):
                                llm_reviewed_count += 1
                            else:
                                llm_failed_count += 1
                        except Exception as e:
                            logger.warning(f"  Phase2 LLM idx={idx} 超时/异常: {e}")
                            _llm_results[idx] = {"llm_reviewed": False, "reason": str(e)[:100]}
                            llm_failed_count += 1

        logger.info(f"Phase2 完成: {llm_reviewed_count} 成功, {llm_failed_count} 失败")

    # ── Phase 3: 最终分池（快速，串行） ──
    for idx, (article, rule_score, rule_reasons, rule_decision, need_llm, page_type, region_tag, noise_flags) in enumerate(_phase1_results):
        llm_result = _llm_results.get(idx)

        # 计算大促时效性分类
        campaign_info = classify_campaign_temporality(article, current_date=date)
        # 注入临时字段供 decide_final_pool 使用
        article["_campaign_name"] = campaign_info["campaign_name"]
        article["_campaign_temporality"] = campaign_info["campaign_temporality"]
        article["_campaign_year"] = campaign_info["campaign_year"]

        final_pool, final_reason = decide_final_pool(
            article, rule_score, rule_decision, llm_result,
            page_type=page_type, region_tag=region_tag, noise_flags=noise_flags
        )

        output_article = dict(article)
        output_article["filter"] = {
            "rule_score": rule_score,
            "rule_decision": rule_decision,
            "rule_reasons": rule_reasons,
            "llm_reviewed": llm_result.get("llm_reviewed", False) if llm_result else False,
            "llm_result": llm_result if llm_result else None,
            "final_pool": final_pool,
            "final_reason": final_reason,
            "page_type": page_type,
            "region_tag": region_tag,
            "noise_flags": noise_flags,
            "campaign_name": campaign_info["campaign_name"],
            "campaign_temporality": campaign_info["campaign_temporality"],
            "campaign_year": campaign_info["campaign_year"],
            "campaign_reason": campaign_info["campaign_reason"],
        }

        # 清除临时字段（避免污染原始文章数据）
        for k in ("_campaign_name", "_campaign_temporality", "_campaign_year"):
            article.pop(k, None)

        if final_pool == "main":
            cleaned_articles.append(output_article)
        elif final_pool == "reference":
            reference_articles.append(output_article)
        else:
            output_article["filter"]["reject_reason"] = final_reason
            rejected_articles.append(output_article)

        reject_reasons[final_pool] += 1

    # ── 保存输出 ──
    cleaned_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "source_file": raw_file,
            "created_at": datetime.now().isoformat(),
            "total_raw": raw_count,
            "total_cleaned": len(cleaned_articles),
            "total_reference": len(reference_articles),
            "total_rejected": len(rejected_articles),
            "llm_reviewed_count": llm_reviewed_count,
            "llm_failed_count": llm_failed_count,
            "by_time_status": dict(by_time_status),
            "by_freshness": dict(by_freshness),
            "by_source": dict(by_source),
            "by_page_type": dict(by_page_type),
            "by_region": dict(by_region),
            "by_noise": dict(by_noise),
            "top_matched_keywords": dict(all_matched_keywords.most_common(20)),
            "reject_reasons": dict(reject_reasons),
        },
        "articles": cleaned_articles,
    }

    reference_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "source_file": raw_file,
            "created_at": datetime.now().isoformat(),
            "total_raw": raw_count,
            "total_reference": len(reference_articles),
        },
        "articles": reference_articles,
    }

    rejected_data = {
        "metadata": {
            "version": "2.0",
            "date": date,
            "source_file": raw_file,
            "created_at": datetime.now().isoformat(),
            "total_raw": raw_count,
            "total_rejected": len(rejected_articles),
            "reject_reason_distribution": dict(reject_reasons),
        },
        "articles": rejected_articles,
    }

    for filepath, data in [(cleaned_file, cleaned_data),
                            (reference_file, reference_data),
                            (rejected_file, rejected_data)]:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── 日志汇总 ──
    logger.info("=" * 60)
    logger.info("过滤完成汇总:")
    logger.info(f"  raw_count: {raw_count}")
    logger.info(f"  cleaned_count: {len(cleaned_articles)}")
    logger.info(f"  reference_count: {len(reference_articles)}")
    logger.info(f"  rejected_count: {len(rejected_articles)}")
    logger.info(f"  llm_reviewed_count: {llm_reviewed_count}")
    logger.info(f"  llm_failed_count: {llm_failed_count}")
    logger.info(f"  by_time_status: {dict(by_time_status)}")
    logger.info(f"  by_freshness: {dict(by_freshness)}")
    logger.info(f"  by_source: {dict(by_source)}")
    logger.info(f"  top_matched_keywords: {dict(all_matched_keywords.most_common(10))}")
    logger.info(f"  reject_reasons: {dict(reject_reasons)}")
    if llm_client:
        status = llm_client.get_status()
        logger.info(f"  LLM status: available_keys={status['available_keys']}, "
                     f"calls={status['total_calls']}, failures={status['total_failures']}")
    logger.info("=" * 60)

    logger.removeHandler(file_handler)
    file_handler.close()

    return {
        "ok": True,
        "date": date,
        "input_file": raw_file,
        "cleaned_file": cleaned_file,
        "reference_file": reference_file,
        "log_file": log_file,
        "raw_count": raw_count,
        "cleaned_count": len(cleaned_articles),
        "reference_count": len(reference_articles),
        "rejected_count": len(rejected_articles),
        "llm_reviewed_count": llm_reviewed_count,
        "errors": errors,
    }


# ===================== CLI =====================


def run_test_llm():
    """运行 LLM 连通性测试。"""
    # 添加项目根目录到 sys.path
    project_root = "/app/working/projects/watsons-retail-intel"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from skills.utils.llm_client import check_llm_config, test_llm_connection

    print("=" * 60)
    print("LongCat LLM 连通性测试")
    print("=" * 60)

    # 1. 配置检查
    config = check_llm_config()
    print()
    print("📋 LLM 配置:")
    print(f"  keys_found:    {config['keys_found']}")
    print(f"  key_count:     {config['key_count']}")
    print(f"  base_url:      {config['base_url']}")
    print(f"  model:         {config['model']}")
    print(f"  endpoint:      {config['endpoint']}")
    print(f"  key_masks:     {config['key_masks']}")

    if not config["keys_found"]:
        print()
        print("❌ 未找到任何 API Key，无法进行连通性测试。")
        print("   请设置环境变量: LONGCAT_API_KEYS 或 longcat / longcat1~5")
        return

    # 2. 连通性测试
    print()
    print("🔄 正在发送测试请求...")
    result = test_llm_connection()

    print()
    print("📊 测试结果:")
    print(f"  llm_config_ok:   {result['llm_config_ok']}")
    print(f"  api_reachable:   {result['api_reachable']}")
    print(f"  model:           {result['model']}")
    print(f"  parsed_json_ok:  {result['parsed_json_ok']}")
    print(f"  error_message:   {result['error_message'] or '(无)'}")

    print()
    if result["api_reachable"] and result["parsed_json_ok"]:
        print("✅ LLM 连通性测试通过！")
    elif result["api_reachable"]:
        print("⚠️  API 可达但 JSON 解析异常，请检查模型输出格式。")
    elif result["llm_config_ok"]:
        print("❌ API 不可达，请检查 base_url 和网络连通性。")
    else:
        print("❌ LLM 配置不完整，请检查环境变量。")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="过滤相关文章 — 规则过滤 + LLM 语义复核",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", required=False, help="项目根目录")
    parser.add_argument("--date", help="日期 YYYY-MM-DD")
    parser.add_argument("--raw-file", default=None, help="覆盖输入文件路径")
    parser.add_argument("--keywords-file", default="config/keywords.yaml",
                        help="关键词配置文件路径（相对项目根）")
    parser.add_argument("--scoring-file", default="config/scoring.yaml",
                        help="评分配置文件路径（相对项目根）")
    parser.add_argument("--use-llm", default="true",
                        help="是否使用LLM (true/false)")
    parser.add_argument("--llm-mode", default="borderline_only",
                        choices=["borderline_only", "all"],
                        help="LLM模式: borderline_only 或 all")
    parser.add_argument("--verbose", action="store_true", help="启用详细日志")
    parser.add_argument("--test-llm", action="store_true",
                        help="运行 LLM 连通性测试并退出")

    args = parser.parse_args()

    # ── LLM 连通性测试模式 ──
    if args.test_llm:
        run_test_llm()
        sys.exit(0)

    # ── 正常过滤模式，需要 --project-root 和 --date ──
    if not args.project_root:
        parser.error("正常模式需要 --project-root 参数")
    if not args.date:
        parser.error("正常模式需要 --date 参数")

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")

    use_llm = args.use_llm.lower() in ("true", "1", "yes")

    result = filter_relevant_articles(
        project_root=args.project_root,
        date=args.date,
        raw_file=args.raw_file,
        keywords_file=args.keywords_file,
        scoring_file=args.scoring_file,
        use_llm=use_llm,
        llm_mode=args.llm_mode,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()