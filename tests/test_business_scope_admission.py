"""Frozen relevance contract: this must not require LLM, network or NAS."""
from skills.filter_relevant_articles.business_scope import decide, admissible


def state(title, summary=""):
    return decide({"title": title, "summary": summary})["decision"]


def test_watsons_cocreation_pass():
    assert state("屈臣氏有效创新背后，“共创”成为关键一步") == "main"


def test_instant_retail_beauty_pass():
    assert state("美团闪购与美妆品牌合作上线个护新品") == "main"


def test_beauty_competitor_pass():
    assert state("丝芙兰扩大中国市场门店布局") == "main"


def test_county_retail_expansion_user_selected_pass():
    assert state("山姆、奥乐齐、盒马掘金“县级”市场") == "main"


def test_yeren_icecream_rejected_even_if_watsons_in_body():
    assert state("野人先生冰淇淋品牌宣布扩张开店",
                 "媒体提到屈臣氏即时零售和美妆的行业趋势") == "reject"


def test_stock_news_rejected_even_with_operations_words():
    assert state("上美股份股价上涨，券商发布研报",
                 "文章提到屈臣氏、美妆门店和即时零售合作") == "reject"


def test_recruitment_rejected_even_watsons_beauty():
    assert state("上美股份高薪招聘CHO，美妆与屈臣氏行业背景") == "reject"


def test_sushi_penalty_rejected():
    assert state("寿司郎因食品安全问题被罚，门店经营受到影响") == "reject"


def test_generic_sap_report_rejected():
    assert state("SAP报告解析零售行业趋势", "屈臣氏与美妆个护数据") == "reject"


def test_general_retail_does_not_auto_pass():
    assert state("大型零售商启动会员日促销", "市场发展动态") == "reject"


def test_channel_only_goes_to_reference():
    assert state("即时零售平台调整配送履约体系") == "reference"


def test_beauty_opinion_without_new_operating_move_reference():
    assert state("美妆消费发展趋势", "屈臣氏市场观察") == "reference"


def test_second_gate_prevents_high_scores():
    assert admissible({"event_title": "寿司郎开店", "fact": "门店扩张"})["decision"] == "reject"


def test_secondary_watsons_reference_does_not_rescue_icecream():
    assert state("野人先生推出新品冰淇淋", "评论中对比屈臣氏门店会员") == "reject"


def test_generic_market_analysis_not_main():
    assert state("即时零售市场趋势分析") != "main"


def test_nonbeauty_electric_vehicle_store_not_main():
    assert state("电动车汽车门店扩张", "市场动态") == "reject"


def test_beauty_personal_care_new_store():
    assert state("万宁升级个护门店经营模式") == "main"


def test_fallback_reference_does_not_enter_main():
    assert state("京东秒送宣布新运营策略") == "reference"
