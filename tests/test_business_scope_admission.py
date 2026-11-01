"""No-LLM/no-network golden admission contract: source discovery to event library."""
import json
from pathlib import Path

import pytest

from skills.filter_relevant_articles.business_scope_gate import classify_business_scope
from skills.filter_relevant_articles.cross_day_gate import (
    article_signature, canonical_url, load_previous_articles, prior_duplicate,
)
from skills.filter_relevant_articles.filter_relevant_articles import decide_final_pool


@pytest.mark.parametrize("title", [
    "野人先生冰淇淋品牌开出第100家门店",
    "野人先生与美团闪购合作，瞄准美妆个护渠道",
    "寿司郎被罚，报道提到屈臣氏美妆业务",
    "上美股份高薪招聘CHO，建设美妆人才体系",
    "屈臣氏美妆企业高薪招聘电商总监",
    "SAP报告称即时零售美妆增长",
    "港股美妆概念股股价涨停，屈臣氏同业走高",
    "券商发布美团闪购行业研报",
    "屈臣氏相关股价上涨、市值突破新高",
    "美妆品牌股东减持，股价盘中大跌",
    "美团即时零售与奶茶店签约拓店",
    "某健身中心开店，宣传中提及美妆",
    "屈臣氏2026年最新财报发布，券商评级调高",
    "名创优品海外开设玩具旗舰店",
    "山姆奥乐齐盒马县级扩张市场趋势报告",
    "即时零售行业市场规模研究报告",
    "某美容研究报告盘点2019年案例",
])
def test_reject_irrelevant_even_if_background_has_core_keywords(title):
    article = {
        "title": title, "summary": "文中提及屈臣氏、美妆、即时零售的行业背景",
        "source_name": "测试媒体", "published_at": "2026-10-30",
        "url": "https://example.com/test"
    }
    result = classify_business_scope(article)
    assert result["decision"] == "REJECT", (title, result)
    pool, reason = decide_final_pool(
        dict(article, freshness_status="newly_discovered",
             time_status="in_window", matched_keywords=["屈臣氏", "美妆"]),
        rule_score=99, rule_decision="keep",
        llm_result={"llm_reviewed": True, "recommended_pool": "main"},
    )
    assert pool == "reject", (title, pool, reason)


@pytest.mark.parametrize("article", [
    {"title": "屈臣氏有效创新背后，“共创”成为关键一步", "summary": "屈臣氏推动创新共创业务"},
    {"title": "屈臣氏接入美团闪购并升级线上线下会员权益", "summary": "屈臣氏门店履约升级"},
    {"title": "丝芙兰中国门店升级，推出个护美妆新服务"},
    {"title": "万宁宣布上线到家配送与会员服务"},
    {"title": "美团闪购调整商家佣金和门店履约规则"},
    {"title": "淘宝闪购推出美妆护肤即时配送"},
    {"title": "京东秒送推出美妆商家扶持补贴"},
    {"title": "某美妆品牌推出新品并上架天猫官方旗舰店"},
    {"title": "某护肤品牌新店开业，布局线下零售渠道"},
    {"title": "名创优品新开美妆零售集合店"},
    {"title": "化妆品备案新规正式实施，企业须调整经营合规流程"},
    {"title": "美妆品牌与屈臣氏签约合作并入驻门店"},
])
def test_accept_core_operational_stories(article):
    result = classify_business_scope(article)
    assert result["decision"] == "ACCEPT", (article, result)
    assert result["action"]


def test_curated_one_off_exception_cannot_whitelist_general_supermarkets():
    title = "山姆、奥乐齐、盒马掘金“县级”市场"
    a = {"title": title, "source_name": "零售圈", "published_at": "2026-10-28"}
    assert classify_business_scope(a)["reason_code"] == "user_curated_adjacent_format"
    b = dict(a, published_at="2026-10-29")
    assert classify_business_scope(b)["decision"] == "REJECT"
    c = dict(a, source_name="其他媒体")
    assert classify_business_scope(c)["decision"] == "REJECT"


def test_named_core_without_action_goes_to_review_not_intelligence():
    article = {"title": "美团闪购最新动态", "summary": ""}
    assert classify_business_scope(article)["decision"] == "REVIEW"
    pool, _ = decide_final_pool(
        dict(article, url="https://example.com/a", freshness_status="newly_discovered",
             time_status="in_window"),
        99, "keep",
        {"llm_reviewed": True, "recommended_pool": "main"}
    )
    assert pool == "reference"


def test_body_only_incidental_watsons_does_not_qualify():
    a = {"title": "寿司郎被罚", "summary": "",
         "content": "本文讨论餐饮行业，并顺带列举屈臣氏美妆。"}
    assert classify_business_scope(a)["decision"] == "REJECT"


def test_event_level_rejects_irrelevant_sub_event_of_good_article():
    good = {"event_title": "屈臣氏升级即时配送", "fact": "屈臣氏升级门店履约"}
    bad = {"event_title": "野人先生冰淇淋开业", "fact": "野人先生美团合作"}
    assert classify_business_scope(good, event_level=True)["decision"] == "ACCEPT"
    assert classify_business_scope(bad, event_level=True)["decision"] == "REJECT"


def test_cross_day_same_publisher_same_claim_blocked(tmp_path):
    folder = tmp_path / "data" / "cleaned" / "2026-10-30"
    folder.mkdir(parents=True)
    original = {
        "url": "https://example.com/news?a=1&utm_source=a",
        "title": "屈臣氏升级门店履约",
        "summary": "屈臣氏美团闪购正式接入", "source_name": "某媒体",
    }
    (folder / "cleaned_articles.json").write_text(json.dumps({"articles": [original]},
                                 ensure_ascii=False), encoding="utf8")
    history = load_previous_articles(tmp_path, "2026-11-01")
    assert prior_duplicate(original, history) == "2026-10-30"
    changed = dict(original, summary="屈臣氏美团闪购新增50家门店")
    assert prior_duplicate(changed, history) == ""
    corroborated = dict(original, url="https://other.example.org/1", source_name="另一媒体")
    assert prior_duplicate(corroborated, history) == ""


def test_cross_day_normalization_keeps_new_number():
    a = {"url": "https://ex.com/a?utm_source=x", "title": "屈臣氏新增20家门店",
         "summary": "新增20家", "source_name": "某媒体"}
    b = dict(a, title="屈臣氏新增30家门店", summary="新增30家")
    assert article_signature(a) != article_signature(b)
    assert canonical_url(a["url"]) == "https://ex.com/a"


def test_source_query_config_contains_precise_active_core_searches():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    text = path.read_text(encoding="utf-8")
    assert "query: 即时零售 最新 动态\n" not in text
    assert "query: 美团 京东 阿里 即时零售 大战\n" not in text
    assert "query: 美团闪购 美妆 商家\n" in text



def test_report_pack_filters_pre_scope_legacy_events_and_attributes_publication(tmp_path):
    from skills.reader_brief import facts_pack
    day = tmp_path / "2026-10-30"
    day.mkdir()
    events = [
        {
            "event_id": "E1",
            "event_title": "屈臣氏推动共创创新合作",
            "fact": "屈臣氏与合作伙伴推动会员服务共创创新",
            "source_url": "https://example.com/beauty",
            "source_name": "36氪",
            "published_at": "2026-10-30",
        },
        {
            "event_id": "E2",
            "event_title": "野人先生冰淇淋新店开业",
            "fact": "野人先生冰淇淋开设新店",
            "source_url": "https://example.com/icecream",
            "source_name": "36氪",
            "published_at": "2026-10-30",
        },
        {
            "event_id": "E3",
            "event_title": "上美股份高薪招聘CHO",
            "fact": "上美股份招聘CHO发展美妆产业",
            "source_url": "https://example.com/jobs",
            "source_name": "36氪",
            "published_at": "2026-10-30",
        },
        {
            "event_id": "E4",
            "event_title": "屈臣氏股价大涨，券商提高评级",
            "fact": "屈臣氏相关上市公司股价上涨",
            "source_url": "https://example.com/stock",
            "source_name": "36氪",
            "published_at": "2026-10-30",
        },
    ]
    (day / "events_analyzed.json").write_text(
        json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8"
    )
    pack = facts_pack.build_from_events(
        str(tmp_path), "2026-10-26", "2026-11-01", period_type="weekly"
    )
    assert len(pack["facts"]) == 1, pack
    fact = pack["facts"][0]
    assert fact["date_role"] == "published"
    assert fact["attribution_text"] == "据36氪2026年10月30日报道"
    assert fact["date"] == "2026-10-30"


def test_legacy_synthesis_does_not_resurrect_stock_or_food():
    from skills.reader_brief import facts_pack
    synth = {
        "period": {"start": "2026-10-26", "end": "2026-11-01"},
        "all_clusters": [
            {"facts": ["美团闪购升级美妆个护配送规则"],
             "event_ids": ["E1"],
             "source_urls": ["https://example.com/a"], "sources": ["官方"]},
            {"facts": ["野人先生冰激凌门店开业，布局美妆周边"],
             "event_ids": ["E2"],
             "source_urls": ["https://example.com/b"], "sources": ["36氪"]},
            {"facts": ["屈臣氏相关个股股价大涨"],
             "event_ids": ["E3"],
             "source_urls": ["https://example.com/c"], "sources": ["财经网"]},
        ],
    }
    pack = facts_pack.build_from_synthesis(synth)
    assert len(pack["facts"]) == 1, pack
    assert "美团闪购" in pack["facts"][0]["text"]


def test_no_original_article_action_not_accepted_merely_by_llm_claim():
    # Model generation can mention a related actor; admission is only
    # a topic predicate. Extract stage additionally binds claimed action
    # to original article text and stores a scope rejection if absent.
    article = {"title": "屈臣氏会员体系", "summary": "屈臣氏介绍会员现状"}
    invented = {"event_title": "屈臣氏宣布收购其他企业",
                "fact": "屈臣氏完成收购"}
    assert classify_business_scope(article)["decision"] == "ACCEPT" or (
        classify_business_scope(article)["decision"] == "REVIEW"
    )
    assert classify_business_scope(invented, event_level=True)["decision"] == "ACCEPT"
    assert "收购" not in article["title"] + article["summary"]



def test_user_curated_contextual_case_survives_only_when_linked_event_proves_it(tmp_path):
    from skills.reader_brief import facts_pack
    events_dir = tmp_path / "2026-10-28"
    events_dir.mkdir()
    (events_dir / "events_analyzed.json").write_text(json.dumps({
        "events": [{
            "event_id": "CURATED1",
            "event_title": "山姆、奥乐齐、盒马掘金“县级”市场",
            "source_name": "零售圈",
            "published_at": "2026-10-28",
        }]
    }, ensure_ascii=False), encoding="utf-8")
    synth = {
        "period": {"start": "2026-10-26", "end": "2026-11-01"},
        "all_clusters": [
            {"event_ids": ["CURATED1"], "facts": ["山姆、奥乐齐、盒马掘金“县级”市场"],
             "source_urls": ["https://example.com/retail"], "sources": ["零售圈"]},
            {"event_ids": ["UNAPPROVED"], "facts": ["山姆、奥乐齐、盒马掘金“县级”市场"],
             "source_urls": ["https://example.com/unapproved"], "sources": ["零售圈"]},
        ],
    }
    pack = facts_pack.build_from_synthesis(synth, events_dir=str(tmp_path))
    assert len(pack["facts"]) == 1, pack
    assert pack["facts"][0]["event_ids"] == ["CURATED1"]
