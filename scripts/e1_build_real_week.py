#!/usr/bin/env python3
"""E1: recover a real 2026-W43 evidence pack from archived articles (legacy IDs E1-W42-*).

Only manually reviewed, source-anchored events enter the reportable pool.
This script does not call an LLM and does not mark historical pools verified.
"""
import argparse, hashlib, json, os, re, sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

WEEK_START = date(2026, 10, 19)
WEEK_END = date(2026, 10, 25)
DATES = [f"2026-10-{i:02d}" for i in range(19, 26)]


def norm(s):
    return re.sub(r"\s+", "", str(s or ""))


def parse_pub(s):
    s = str(s or "")
    m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return date(*map(int, m.groups()))
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def load_articles(root):
    arts = {}
    for d in DATES:
        p = Path(root) / "data/raw" / d / "raw_articles.json"
        if not p.exists():
            continue
        for a in json.loads(p.read_text()).get("articles", []):
            u = a.get("url") or a.get("source_url")
            if u:
                a = dict(a); a["_raw_date"] = d
                arts.setdefault(u, a)
    return arts


def load_source_snapshots(root):
    out = {}
    base = Path(root) / "data/e1/2026-10-19_2026-10-25/sources"
    if not base.exists():
        return out
    for p in sorted(base.glob("*.json")):
        x = json.loads(p.read_text())
        out[x.get("url")] = x
    return out


def source_text(root, url, articles, snaps):
    a = articles.get(url)
    raw = a.get("content") if a else ""
    snap = snaps.get(url, {})
    stext = snap.get("text") if snap.get("ok") else ""
    # Prefer the intact raw archive for Sina; the live page snapshot is GBK
    # mojibake and sometimes longer than the archived body.
    if raw and "sina.com.cn" in url:
        return raw, hashlib.sha256(raw.encode()).hexdigest(), "raw_archive"
    # Prefer the longer archived text; several network snapshots are WAF pages.
    if raw and len(raw) >= len(stext):
        return raw, hashlib.sha256(raw.encode()).hexdigest(), "raw_archive"
    if stext:
        return stext, snap.get("page_sha256") or hashlib.sha256(stext.encode()).hexdigest(), "network_snapshot"
    return "", "", ""


def ba(impact, action, owner, metrics, follow, impact_type="watch", action_level="watch", channels=None, variables=None, confidence="medium"):
    return {
        "impact_type": impact_type,
        "affected_channels": channels or [],
        "affected_business_variables": variables or [],
        "watsons_impact": impact,
        "recommended_action": action,
        "action_level": action_level,
        "owner_hint": owner,
        "tracking_metrics": metrics,
        "follow_up_questions": follow,
        "confidence": confidence,
        "downgrade_reasons": [],
    }


def build(root):
    articles = load_articles(root)
    snaps = load_source_snapshots(root)
    e1dir = Path(root) / "data/e1/2026-10-19_2026-10-25"
    e1dir.mkdir(parents=True, exist_ok=True)

    specs = [
        dict(eid="E1-W42-001", day="2026-10-20", title="天猫双11于10月20日20点开卖，4小时124个品牌成交破亿",
             url="https://www.36kr.com/p/4035580422247302", source="36氪",
             published="Thu, 22 Oct 2026 02:24:52 GMT", occurred="2026-10-20", announced="2026-10-20",
             snippet="10月20日晚八点，天猫双11正式开启全品类售卖。",
             fact="10月20日20:00天猫双11正式开卖；开卖4小时124个品牌成交额破亿，27681个品牌成交额超过去年全天。",
             etype="平台大促", platforms=["天猫"], companies=["天猫"], brands=[],
             variables=["流量", "货盘", "价格"],
             impact="双11首日成交向头部品牌集中，说明用户在大促中仍愿意为确定性与品牌力买单；对屈臣氏即时零售而言，首日流量高峰是检验‘线上下单、门店履约’能力的窗口，而不是单纯跟价。",
             action="在京东秒送、美团闪购、淘宝闪购同步设置‘双11开门红’专区，优先保证美妆个护TOP品牌与Watsons独家套组的30分钟可达库存；把首日流量承接、缺货率、取消率纳入当日战报。",
             owner="即时零售运营", metrics=["首日GMV", "30分钟履约率", "缺货率", "取消率", "TOP品牌动销"],
             follow=["首日流量中有多少来自平台大促会场？", "门店拣货峰值是否造成缺货或超时？"],
             channels=["京东秒送", "美团闪购", "淘宝闪购"]),
        dict(eid="E1-W42-002", day="2026-10-20", title="天猫双11美妆开门红TOP5：珀莱雅、修丽可、兰蔻、雅诗兰黛、SK-II",
             url="https://finance.sina.com.cn/tech/roll/2026-10-22/doc-iniwaezt5950286.shtml", source="新浪科技/亿邦动力",
             published="Thu, 22 Oct 2026 00:00:00 +0800", occurred="2026-10-20", announced="2026-10-20",
             snippet="美妆品类TOP20中，珀莱雅、修丽可、兰蔻、雅诗兰黛、SK-II名列前五名。",
             fact="10月20日20:00-23:59天猫双11美妆开门红榜单：珀莱雅、修丽可、兰蔻、雅诗兰黛、SK-II列前五；TOP20国货占四席（珀莱雅、薇诺娜、Fan Beauty Diary、毛戈平）。",
             etype="品类榜单", platforms=["天猫"], companies=["天猫"], brands=["珀莱雅", "修丽可", "兰蔻", "雅诗兰黛", "SK-II", "薇诺娜", "毛戈平"],
             variables=["货盘", "品牌", "价格"],
             impact="榜单同时验证国际大牌与功效国货的双主线：高端修护、功效面霜和国货大单品都在增长。屈臣氏货盘若只押国际大牌，会错过国货功效客群；若只做低价，会丢掉高客单修护需求。",
             action="按‘高端修护/功效国货/敏感肌基础’三组重排双11货盘，确保榜单前五品牌与薇诺娜、毛戈平在门店和即时零售前置仓有可见陈列；每组配置一个Watsons独家组合，避免与平台旗舰店同款同价硬碰。",
             owner="商品/货盘", metrics=["TOP20品牌覆盖数", "国货功效SKU动销", "组合装毛利率", "缺货率"],
             follow=["TOP20中屈臣氏已覆盖多少个品牌？", "国货功效品的到店转化是否低于国际大牌？"],
             channels=["天猫", "京东秒送", "美团闪购"]),
        dict(eid="E1-W42-003", day="2026-10-20", title="天猫双11个护开门红TOP5：卡诗、Off&Relax、护舒宝、潘婷、欧莱雅",
             url="https://finance.sina.com.cn/tech/roll/2026-10-22/doc-iniwaezt5950286.shtml", source="新浪科技/亿邦动力",
             published="Thu, 22 Oct 2026 00:00:00 +0800", occurred="2026-10-20", announced="2026-10-20",
             snippet="个护品类TOP20中，前五名分别是卡诗、Off&Relax、护舒宝、潘婷、欧莱雅。",
             fact="10月20日20:00-23:59天猫双11个护开门红榜单：卡诗、Off&Relax、护舒宝、潘婷、欧莱雅列前五；Off&Relax开卖4小时成交首次破亿。",
             etype="品类榜单", platforms=["天猫"], companies=["天猫"], brands=["卡诗", "Off&Relax", "护舒宝", "潘婷", "欧莱雅"],
             variables=["货盘", "品牌", "客单"],
             impact="个护榜单出现高端洗护（卡诗、Off&Relax）与大众刚需（护舒宝、潘婷、欧莱雅）并存，说明‘高端护理升级’和‘高频补货’不是二选一。屈臣氏可以把高端洗护做成毛利与会员黏性，把刚需个护做成即时零售复购入口。",
             action="把卡诗、Off&Relax放入‘高端洗护’专题，与护舒宝、潘婷组成‘满额加购’；在即时零售设置‘个护补货’常驻入口，重点跟踪高端洗护的复购周期与刚需品的客单提升。",
             owner="品类运营", metrics=["高端洗护GMV", "个护复购率", "加购率", "客单价"],
             follow=["高端洗护在屈臣氏即时零售的复购周期是多少？", "刚需个护是否被平台补贴拉低毛利？"],
             channels=["天猫", "京东秒送", "美团闪购"]),
        dict(eid="E1-W42-004", day="2026-10-20", title="丸美小红笔眼霜4.0进入天猫双11去黑眼圈眼霜榜TOP4",
             url="https://www.36kr.com/p/4035580422247302", source="36氪",
             published="Thu, 22 Oct 2026 02:24:52 GMT", occurred="2026-10-20", announced="2026-10-20",
             snippet="丸美今年力推的新品小红笔眼霜4.0，登上了双11开门红去黑眼圈眼霜热卖榜的Top4",
             fact="丸美小红笔眼霜4.0登上天猫双11开门红去黑眼圈眼霜热卖榜TOP4，是TOP5中唯一国货品牌；增长来自产品升级与功效表达，而非单纯低价。",
             etype="产品动态", platforms=["天猫"], companies=["丸美"], brands=["丸美"],
             variables=["货盘", "内容", "价格"],
             impact="眼霜是屈臣氏美妆品类中高毛利、强体验的细分；丸美用‘7天淡化眼纹’的功效表达打榜，说明可解释的功效比泛泛折扣更能带来转化。屈臣氏可将眼霜从货架品转为‘可试用、可解释’的体验品。",
             action="在重点门店设置眼霜试用与7天效果记录卡，把丸美小红笔4.0与Watsons眼霜组合放在同一体验区；即时零售页同步上线‘眼霜+眼膜’组合，用短视频解释成分与使用方法。",
             owner="门店体验/内容", metrics=["眼霜试用转化率", "组合装GMV", "内容点击率", "复购率"],
             follow=["眼霜试用品类的转化率是否高于面霜？", "功效内容是否带来退货率上升？"],
             channels=["天猫", "门店", "美团闪购"]),
        dict(eid="E1-W42-005", day="2026-10-20", title="双11预售90分钟：丝塔芙大白罐登付定金人数榜首",
             url="https://www.36kr.com/p/4033954676446340", source="36氪",
             published="Tue, 20 Oct 2026 09:37:00 GMT", occurred="", announced="",
             snippet="开售90分钟，丝塔芙大白罐便荣登付定金人数榜首",
             fact="据10月20日报道，双11预售开售90分钟，丝塔芙大白罐登付定金人数榜首；550g两罐折后134元，黄牛转售价炒至250元以上。",
             etype="产品动态", platforms=["天猫", "抖音"], companies=["丝塔芙"], brands=["丝塔芙"],
             variables=["货盘", "价格", "履约"],
             impact="基础保湿大罐装的需求不是‘消费降级’，而是‘一罐多用、家庭共用’的确定性需求。屈臣氏自有/独家基础护理品可以承接这类需求，但必须解决大促期间的黄牛转售与缺货，否则会伤害会员体验。",
             action="把550g大罐保湿品放入‘家庭装/敏感肌’常驻货盘，设置会员限购与门店自提优先；即时零售端用‘两罐家庭装+小样’替代单纯降价，避免黄牛套利。",
             owner="商品/会员", metrics=["大罐装销量", "会员限购拦截率", "黄牛投诉数", "缺货率"],
             follow=["大罐装的毛利率能否覆盖即时零售履约成本？", "限购是否误伤真实家庭用户？"],
             channels=["天猫", "抖音", "门店"]),
        dict(eid="E1-W42-006", day="2026-10-23", title="双11平台规则转向：取消凑单与定金预售，预算向货架场和复购迁移",
             url="https://www.36kr.com/p/4038146420627074", source="36氪",
             published="Fri, 23 Oct 2026 10:19:35 GMT", occurred="", announced="",
             snippet="天猫把跨店满减和凑单整体拿掉了，统一成官方立减",
             fact="据10月23日报道，今年双11天猫取消跨店满减与凑单、京东取消定金预售，平台普遍缩短大促周期；大量品牌把投放预算从达播转向货架场，并把老客占比纳入考核。",
             etype="平台规则", platforms=["天猫", "京东", "抖音", "拼多多", "小红书", "快手"], companies=["天猫", "京东"], brands=[],
             variables=["价格", "流量", "会员"],
             impact="平台不再用复杂凑单制造GMV，意味着‘低价+凑单’带来的冲动订单会减少，复购与货架承接变得更重要。屈臣氏的即时零售如果仍靠大额满减拉新，可能换来低质量订单；应把预算转向会员复购与门店履约确定性。",
             action="把双11预算从‘满减凑单’拆出一部分到会员复购券与门店自提权益；用老客占比、复购间隔、退货率替代单纯GMV考核，按周复盘。",
             owner="电商/会员", metrics=["老客占比", "复购间隔", "退货率", "每单履约成本"],
             follow=["取消凑单后客单价下降多少？", "会员复购券的核销率是否高于平台满减？"],
             channels=["天猫", "京东", "抖音"]),
        dict(eid="E1-W42-007", day="2026-10-22", title="即时零售五平台集体加码餐食：朴朴厨房日均2200单，京东七鲜小厨1800家",
             url="https://www.huxiu.com/article/4899661.html", source="虎嗅/联商网",
             published="Thu, 22 Oct 2026 10:49:51 GMT", occurred="", announced="",
             snippet="朴朴厨房上线满一年，单仓日均单量超过2200单。",
             fact="据10月22日报道，即时零售头部平台集体加码餐食：朴朴厨房11个、单仓日均超2200单；京东七鲜小厨门店突破1800家、覆盖35城；淘宝闪购‘家宴’计划四季度多城上线。",
             etype="行业趋势", platforms=["美团", "京东", "淘宝闪购", "朴朴", "叮咚买菜"], companies=["京东", "阿里"], brands=[],
             variables=["品类机会", "供应链", "履约"],
             impact="平台正在用高频餐食训练用户‘30分钟送达’心智，并把供应链能力向自营/定制延伸。对屈臣氏而言，餐食不是应追的品类，但平台的前置仓、冷链和履约密度会外溢到美妆个护；屈臣氏应借平台基础设施做‘高频个护+应急美妆’，而不是扩食品SKU。",
             action="不与平台争餐食；梳理屈臣氏门店3公里内‘应急美妆/个护’TOP20 SKU，优先进入平台前置仓或门店发货；用平台餐食高峰时段测试美妆应急订单的履约时效。",
             owner="即时零售/供应链", metrics=["3公里应急SKU覆盖", "30分钟履约率", "高峰期取消率", "履约成本"],
             follow=["平台餐食高峰是否挤压美妆订单运力？", "哪些个护SKU具备‘应急’属性？"],
             channels=["美团闪购", "京东秒送", "淘宝闪购", "朴朴"]),
        dict(eid="E1-W42-008", day="2026-10-22", title="自营零售走向深水区：六成受访零售企业采用深度定制",
             url="https://www.36kr.com/p/4036858464851079", source="36氪",
             published="Thu, 22 Oct 2026 09:46:06 GMT", occurred="", announced="",
             snippet="30家受访零售企业中，六成采用深度定制模式，77%计划升级供应链与品质管控能力",
             fact="据10月22日报道，中国连锁经营协会与波士顿咨询2026年8月报告显示，30家受访零售企业中六成采用深度定制，77%计划升级供应链与品质管控；即时零售玩家正向采购、品控和线下门店延伸。",
             etype="供应链", platforms=[], companies=["山姆", "沃尔玛", "奥乐齐", "胖东来", "永辉"], brands=[],
             variables=["供应链", "自有品牌", "品质"],
             impact="零售竞争从‘货架丰富’转向‘替顾客选好商品’，自有品牌/独家定制成为差异化来源。屈臣氏若只做品牌分销，价格与货盘都容易被平台旗舰店替代；需要把自有/独家品做成可验证的品质承诺。",
             action="选3-5个高频个护SKU做Watsons独家规格或组合，公开关键品质指标与供应商审核结果；先在即时零售测试‘独家品+会员价’，用复购与退货率验证，而不是只看首单GMV。",
             owner="自有品牌/品控", metrics=["独家SKU占比", "独家品复购率", "退货率", "品质投诉率"],
             follow=["哪些品类适合做独家规格而不是直接贴牌？", "品质承诺能否转化为溢价？"],
             channels=["门店", "即时零售"]),
        dict(eid="E1-W42-009", day="2026-10-24", title="商场C位转向体验与潮玩：门店从卖货转向场景",
             url="https://www.36kr.com/p/4038591177986951", source="36氪",
             published="Sat, 24 Oct 2026 08:00:15 GMT", occurred="", announced="",
             snippet="门店不直接卖货，让消费者先感受“场景”",
             fact="据10月24日走访报道，武汉10余家商场核心C位转向泡泡玛特、老铺黄金、FILA等体验/潮流品牌；门店通过场景化陈列、试用与互动延长停留，而非直接推销。",
             etype="门店体验", platforms=[], companies=["泡泡玛特", "老铺黄金", "FILA", "宝可梦"], brands=[],
             variables=["门店体验", "会员", "客流"],
             impact="线下门店的价值正从‘货架密度’转向‘体验与停留’。屈臣氏门店若继续以堆货和促销为主，很难对抗丝芙兰等体验型竞对；但屈臣氏有社区门店密度，可以把‘快捷服务+体验’做成差异化。",
             action="选10家重点门店设置‘敏感肌/眼霜/香氛’体验台与15分钟美容服务，先测停留时长、试用转化与会员新增；即时零售页同步展示门店体验预约，把线上流量引到店。",
             owner="门店运营", metrics=["停留时长", "试用转化率", "到店预约数", "会员新增"],
             follow=["体验台是否影响坪效？", "哪些门店适合做服务而非纯货架？"],
             channels=["门店", "美团闪购"]),
        dict(eid="E1-W42-010", day="2026-10-23", title="AI素材工业化：5人团队日产千条视频，单条成本降至5元以内",
             url="https://www.36kr.com/p/4038077554218117", source="36氪",
             published="Fri, 23 Oct 2026 07:39:46 GMT", occurred="", announced="",
             snippet="比如有 5 个人的团队，一天能出上千条视频，每条成本还能控制在 10 元乃至 5 元以内。",
             fact="据10月23日报道，AIGC把电商种草视频单条成本从人工拍摄的100-200元降到5-10元，5人团队一天可产上千条；美妆、洗护等头部商家已开始用AI内容抢流量。",
             etype="内容营销", platforms=["抖音"], companies=[], brands=["温博士", "可逐"],
             variables=["内容", "流量", "转化"],
             impact="内容供给从‘稀缺’变成‘工业化’，靠堆量获取流量的边际收益会下降。屈臣氏的机会不是无脑批量生成，而是用AI降低测评/教程/门店素材成本，把内容与真实库存、门店服务绑定，形成可转化的本地化内容。",
             action="建立‘门店实拍+AI二创’内容流水线：每周选10个重点SKU，由门店拍真实试用素材，AI生成不同场景脚本；以点击率、到店核销、退货率筛选，而不是以发布量考核。",
             owner="内容/电商", metrics=["内容点击率", "到店核销率", "内容带来GMV", "退货率"],
             follow=["AI内容是否引发品牌调性/合规风险？", "门店实拍产能能否支撑周更？"],
             channels=["抖音", "小红书", "美团闪购"]),
        dict(eid="E1-W42-011", day="2026-10-20", title="到店团购竞争继续：抖省省DAU约2100万，淘宝闪购团购仍限三城试点",
             url="https://www.36kr.com/p/4034051883111297", source="36氪",
             published="Tue, 20 Oct 2026 11:02:57 GMT", occurred="", announced="",
             snippet="截至6月底，抖省省DAU已达2103万，约为大众点评同期日活的六成以上",
             fact="据10月20日报道，抖省省6月底DAU约2103万，约为大众点评六成；淘宝闪购团购仍仅在上海、深圳、嘉兴试点，类目从餐饮向休闲娱乐拓展。",
             etype="本地生活", platforms=["抖音", "高德", "淘宝闪购", "美团"], companies=["字节", "阿里", "美团"], brands=[],
             variables=["流量", "到店", "服务"],
             impact="到店流量正在分散，消费者可能从美团之外发现美妆服务/体验。屈臣氏若只有商品交易，没有到店服务，难以承接这波流量；但淘宝闪购团购仅三城，暂不宜全国投入。",
             action="仅在上海/深圳/嘉兴跟踪淘宝闪购团购的美妆服务类目；用1-2家门店测试‘15分钟修眉/敏感肌检测’团购套餐，验证到店核销与二次购买，不扩城。",
             owner="新渠道/门店", metrics=["团购核销率", "到店二次购买率", "获客成本", "服务满意度"],
             follow=["美妆服务团购是否存在合规/资质门槛？", "三城试点的流量是否足够？"],
             channels=["淘宝闪购", "抖音", "高德"]),
        dict(eid="E1-W42-012", day="2026-10-23", title="双11需求分化：即时零售承接临时缺货，大牌美妆仍是计划性大促硬通货",
             url="https://www.36kr.com/p/4037147836536454", source="36氪",
             published="Fri, 23 Oct 2026 01:32:20 GMT", occurred="", announced="",
             snippet="大牌美妆核心单品、大家电和知名数码产品，就仍是大促的硬通货",
             fact="据10月23日报道，双11需求分化：高频补货被天天低价分走，临时缺货由即时零售承接；大牌美妆核心单品、新品/限定仍是计划性大促的硬通货；消费者年均通过7种以上渠道购买快消品。",
             etype="行业趋势", platforms=["天猫", "抖音", "京东", "快手", "小红书"], companies=[], brands=[],
             variables=["货盘", "履约", "渠道"],
             impact="屈臣氏同时经营‘计划性大促’和‘即时应急’两种需求，不能用同一货盘打两场仗。大牌核心单品适合大促锁客，应急个护/小样适合即时零售拉新；两者混在一起会稀释价格与库存效率。",
             action="把货盘拆成‘大促计划装’与‘30分钟应急装’：计划装上大牌核心单品/限定套组，应急装上小规格、旅行装、敏感肌刚需；分别设置价格与库存策略，按渠道复盘。",
             owner="商品/渠道", metrics=["计划装GMV", "应急装订单占比", "缺货率", "渠道毛利率"],
             follow=["哪些SKU适合小规格应急装？", "计划装与应急装是否互相蚕食？"],
             channels=["天猫", "京东秒送", "美团闪购"]),
        dict(eid="E1-W42-013", day="2026-10-23", title="丝芙兰SEPHORiA玩妆节10月23-25日落地上海张园：4000名爱好者、60余品牌",
             url="https://www.36kr.com/p/4042615186789256", source="36氪",
             published="Mon, 26 Oct 2026 11:54:09 GMT", occurred="2026-10-23", announced="2026-10-23",
             snippet="2026 SEPHORiA丝芙兰玩妆节于10月23日至25日在上海张园圆满举办。",
             fact="10月23日至25日，2026 SEPHORiA丝芙兰玩妆节在上海张园举办；三天4000余名爱好者、60余个全球品牌、40余场大师课，会员积分可兑换入场权益。",
             etype="竞对动态", platforms=[], companies=["丝芙兰"], competitors=["丝芙兰"], brands=["丝芙兰", "KAYALI", "欧缇丽"],
             variables=["门店体验", "会员", "品牌"],
             impact="丝芙兰把年度活动做成‘会员权益+品牌体验+内容共创’的线下IP，用积分兑换把会员从交易关系转为参与关系。屈臣氏的优势是门店密度与社区客群，不能照搬高成本大展，但可以把‘会员日+门店体验’做成轻量版。",
             action="在重点城市试点‘Watsons Beauty Day’：会员积分兑换15分钟服务/小样礼包，联合3-5个独家品牌做门店大师课；用会员到场率、二次购买、内容自发传播衡量，不追求单日GMV。",
             owner="会员/门店", metrics=["会员到场率", "积分兑换率", "二次购买率", "UGC数量"],
             follow=["轻量会员日能否复制丝芙兰的参与感？", "门店服务产能是否足够？"],
             channels=["门店", "小程序"]),
    ]

    curated = []
    rejected = []
    source_records = {}
    for s in specs:
        text, sha, origin = source_text(root, s["url"], articles, snaps)
        if not text:
            raise SystemExit(f"missing source snapshot for {s['eid']} {s['url']}")
        if norm(s["snippet"]) not in norm(text):
            raise SystemExit(f"evidence snippet not found for {s['eid']}: {s['snippet'][:50]}")
        a = articles.get(s["url"], {})
        pub = parse_pub(s["published"])
        if not pub:
            raise SystemExit(f"bad published date {s['published']}")
        occurred = s["occurred"] or None
        effective = occurred or pub.isoformat()
        date_semantics = "event_occurrence" if occurred else "reported_on"
        _type_map = {"平台大促": "platform_move", "品类榜单": "data_signal",
                     "产品动态": "new_product", "平台规则": "platform_rule",
                     "行业趋势": "category_trend", "供应链": "supply_chain",
                     "门店体验": "consumer_trend", "内容营销": "marketing",
                     "本地生活": "platform_move", "竞对动态": "competitor_move"}
        event = {
            "event_id": s["eid"],
            "event_title": s["title"],
            "fact": s["fact"],
            "evidence_text": s["snippet"],
            "source_url": s["url"],
            "source_name": s["source"],
            "source_title": s.get("source_title") or a.get("title") or s["title"],
            "published_at": s["published"],
            "collected_at": "2026-10-27T00:00:00+08:00",
            "event_occurred_at": occurred or "",
            "event_announced_at": s["announced"] or "",
            "event_effective_at": "",
            "event_time_source": "explicit_text" if occurred else "article_published_at",
            "date_semantics": date_semantics,
            "effective_event_time": effective,
            "effective_time_source": "event_occurred_at" if occurred else "published_at",
            "verification_status": "source_checked",
            "verification_scope": "event_occurrence" if occurred else "article_claim",
            "verification_basis": "agent_source_checked: snippet hash + publication date + date semantics; human review pending",
            "verified_by": "agent_source_checked",
            "verified_at": "2026-10-27",
            "human_review_status": "pending",
            "canonical_event_id": s["eid"].replace("E1-W42-", "E1-W43-"),
            "report_week": "2026-W43",
            "needs_verification": True,
            "date_conflict": False,
            "hearsay_markers": False,
            "temporal_eligibility": "candidate",
            "novelty_status": "new_today",
            "novelty_eligibility": "candidate",
            "report_eligibility": "candidate",
            "editorial_rank": len(curated) + 1,
            "event_type": _type_map.get(s["etype"], s["etype"]),
            "entities": {"platforms": s["platforms"], "companies": s["companies"],
                         "competitors": s.get("competitors", []), "brands": s["brands"]},
            "business_variables": s["variables"],
            "business_analysis": ba(s["impact"], s["action"], s["owner"], s["metrics"], s["follow"],
                                     channels=s["channels"] if "channels" in s else s["platforms"],
                                     variables=s["variables"],
                                     confidence=("medium" if date_semantics == "event_occurrence" else "low")),
            "evidence_sha256": hashlib.sha256(norm(s["snippet"]).encode()).hexdigest(),
            "source_snapshot_sha256": sha,
            "source_snapshot_kind": origin,
            "_file_date": s["day"],
        }
        curated.append(event)
        source_records[s["url"]] = {
            "url": s["url"], "title": s["source_title"] if "source_title" in s else (a.get("title") or s["title"]),
            "published_at": s["published"], "snapshot_sha256": sha, "snapshot_kind": origin,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    # Recompute the production eight-dimension score; do not carry a hand-written
    # 74-92 score or P1 label into the evidence pack.
    try:
        from skills.score_events.score_events import score_events as _score_events
        _tmp = Path(root) / "data/e1" / "_score_input.json"
        _tmp.write_text(json.dumps({"events": curated}, ensure_ascii=False), encoding="utf-8")
        _score_events(str(root), "2026-10-28", events_file=str(_tmp),
                      scoring_file="config/scoring.yaml",
                      output_file=str(Path(root) / "data/e1" / "_score_output.json"))
        _scored = json.loads((Path(root) / "data/e1" / "_score_output.json").read_text())["events"]
        _by_id = {e["event_id"]: e for e in _scored}
        for e in curated:
            sc = _by_id.get(e["event_id"])
            if not sc:
                continue
            e["scores"] = sc.get("scores", {})
            e["weighted_score"] = sc.get("weighted_score")
            e["priority"] = sc.get("priority")
            e["score_reasons"] = sc.get("score_reasons", [])
            e["downgrade_reasons"] = sc.get("downgrade_reasons", [])
    except Exception as exc:
        raise SystemExit(f"eight-dimension scoring failed: {exc}")

    # Rejected examples are real records from the raw events pool, not invented.
    def find_event(pattern):
        for d in DATES:
            p = Path(root) / "data/events" / d / "events_analyzed.json"
            if not p.exists(): continue
            for e in json.loads(p.read_text()).get("events", []):
                if pattern in (e.get("event_title") or ""):
                    return d, e
        return None, None

    for pat, reason, detail in [
        ("美团闪购双11上线直播工具", "source_stale", "引用 CBNData 2022-11-17 文章，非2026年事件"),
        ("京东11.11屈臣氏抢先购", "event_outside_window", "事件日期2026-10-16，早于本周开始；不得按采集日冒充本周事件"),
        ("京东秒送屈臣氏国庆", "event_outside_window", "事件日期2026-09-30，国庆活动已结束"),
    ]:
        d, e = find_event(pat)
        if e:
            rejected.append({"date_file": d, "event_id": e.get("event_id"), "event_title": e.get("event_title"),
                             "reason": reason, "detail": detail, "source_url": e.get("source_url")})

    # Inventory and automatic exclusion accounting.
    all_events = []
    for d in DATES:
        p = Path(root) / "data/events" / d / "events_analyzed.json"
        if p.exists():
            all_events += json.loads(p.read_text()).get("events", [])
    ids = {e["event_id"] for e in curated}
    seen_fact = set()
    reasons = Counter()
    excluded = []
    for e in all_events:
        if e.get("event_id") in ids: continue
        title = e.get("event_title") or ""
        fact = e.get("fact") or ""
        fh = hashlib.md5(norm(fact or title).encode()).hexdigest()
        if fh in seen_fact:
            reasons["duplicate_repetition"] += 1; continue
        seen_fact.add(fh)
        if e.get("priority") == "ARCHIVE":
            reasons["archive_or_off_domain"] += 1; continue
        occ = e.get("event_occurred_at") or ""
        pub = e.get("published_at") or ""
        src = e.get("source_url") or ""
        if occ and occ[:10] < WEEK_START.isoformat():
            reasons["event_outside_window"] += 1; continue
        if src and src not in articles:
            if "cbndata.com" in src:
                reasons["source_stale"] += 1
            else:
                reasons["no_archived_source"] += 1
            continue
        if not occ and not pub:
            reasons["no_stable_date"] += 1; continue
        reasons["not_manual_selected"] += 1

    inventory = {
        "period": {"start": WEEK_START.isoformat(), "end": WEEK_END.isoformat()},
        "days": len(DATES),
        "raw_articles": sum(len(json.loads((Path(root)/"data/raw"/d/"raw_articles.json").read_text()).get("articles", [])) for d in DATES),
        "cleaned_articles": sum(len(json.loads((Path(root)/"data/cleaned"/d/"cleaned_articles.json").read_text()).get("articles", [])) for d in DATES if (Path(root)/"data/cleaned"/d/"cleaned_articles.json").exists()),
        "analyzed_events": len(all_events),
        "unique_fact_hashes": len({hashlib.md5(norm(e.get("fact") or e.get("event_title") or "").encode()).hexdigest() for e in all_events}),
        "published_in_week_articles": len({a.get("url") for a in articles.values() if parse_pub(a.get("published_at")) and WEEK_START <= parse_pub(a.get("published_at")) <= WEEK_END}),
        "relevant_published_in_week": len([a for a in articles.values() if parse_pub(a.get("published_at")) and WEEK_START <= parse_pub(a.get("published_at")) <= WEEK_END and re.search("屈臣氏|美妆|个护|护肤|彩妆|即时零售|闪购|秒送|电商|零售|双11|双十一", a.get("title") or "")]),
    }
    audit = {
        "inventory": inventory,
        "accepted_events": len(curated),
        "accepted_occurrence_verified": sum(1 for e in curated if e["date_semantics"] == "event_occurrence"),
        "accepted_article_claim_verified": sum(1 for e in curated if e["date_semantics"] != "event_occurrence"),
        "rejected_examples": rejected,
        "exclusion_reasons": dict(reasons),
        "excluded_total": sum(reasons.values()),
        "notes": [
            "Only 13 events were manually reviewed; the rest of the pool was not re-reviewed by an LLM.",
            "Article publication date is not treated as event occurrence date.",
            "Context claims are marked verification_scope=article_claim and date_semantics=reported_on.",
            "No historical event was bulk-marked verified.",
        ],
    }
    (e1dir/"curated_events.json").write_text(json.dumps(curated, ensure_ascii=False, indent=2))
    (e1dir/"audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    (e1dir/"sources_manifest.json").write_text(json.dumps(list(source_records.values()), ensure_ascii=False, indent=2))
    # Build an isolated report pool: only curated events, one file per day.
    pool = Path(root)/"data/e1/report_pool/events"; pool.mkdir(parents=True, exist_ok=True)
    for d in DATES:
        day_events = [e for e in curated if e["_file_date"] == d]
        (pool/d).mkdir(parents=True, exist_ok=True)
        (pool/d/"events_analyzed.json").write_text(json.dumps({"metadata": {"source": "E1 curated", "date": d}, "events": day_events}, ensure_ascii=False, indent=2))
    print(json.dumps(audit, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--project-root", default=".")
    build(ap.parse_args().project_root)
