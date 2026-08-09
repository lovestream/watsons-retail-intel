#!/usr/bin/env python3
"""
回归测试 — temporal_fact_validation 时效判断机制（第十二节 9 个案例）。

运行:
    cd /app/working/projects/watsons-retail-intel
    python3 -m pytest tests/test_temporal_fact_validation.py -v
    或
    python3 tests/test_temporal_fact_validation.py
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from skills.temporal_fact_validation.temporal_fact_validation import (
    article_date_sanity_check,
    classify_fact_freshness,
    classify_page_type,
    extract_content_dates,
    validate_event_times,
    temporal_fact_validation,
    parse_flexible_date,
)

CST = timezone(timedelta(hours=8))

# 测试基准时间：2026-08-09
NOW = datetime(2026, 8, 9, 10, 0, 0, tzinfo=CST)


def make_article(published_at="2026-08-09", title="测试文章", content="", url="https://example.com/a/12345"):
    return {
        "article_id": "test_001",
        "title": title,
        "url": url,
        "published_at": published_at,
        "published_at_source": "html_time",
        "content": content,
        "source_name": "test_source",
    }


def make_event(title="测试事件", fact="测试事实", evidence="", occurred_at="", effective_at=""):
    ev = {
        "event_id": "E20260809_0001",
        "event_title": title,
        "event_type": "platform_move",
        "fact": fact,
        "evidence_text": evidence,
        "entities": {"platforms": [], "companies": [], "competitors": [],
                     "brands": [], "channels": [], "categories": []},
        "source_article_id": "test_001",
        "source_url": "https://example.com/a/12345",
        "published_at": "2026-08-09",
        "source_name": "test_source",
        "source_title": "测试文章",
    }
    if occurred_at:
        ev["event_occurred_at"] = occurred_at
    if effective_at:
        ev["event_effective_at"] = effective_at
    return ev


# ═══════════════════════════════════════════════════════════════
# 案例 1: 今天发布、今天发生 → new_fact
# ═══════════════════════════════════════════════════════════════
def test_case1_new_fact():
    article = make_article(content="今天美团闪购宣布新动作。2026年8月9日，美团在多地试点。")
    sanity = article_date_sanity_check(article, NOW)
    assert sanity["article_time_status"] == "valid"

    content_dates = extract_content_dates(article["title"] + " " + article["content"], NOW)
    assert content_dates["recent_mentions"] >= 1

    event = make_event(title="美团闪购试点新动作", fact="2026年8月9日美团闪购在多地试点新服务",
                       occurred_at="2026-08-09")
    temporal = validate_event_times(event, sanity, content_dates, NOW)
    freshness = classify_fact_freshness(event, sanity, temporal, None, NOW)
    assert freshness["fact_freshness_status"] == "new_fact", freshness
    assert freshness["temporal_eligibility"] == "core"


# ═══════════════════════════════════════════════════════════════
# 案例 2: 今天发布、去年发生 → republished_old_fact
# ═══════════════════════════════════════════════════════════════
def test_case2_republished_old_fact():
    article = make_article(content="2025年双11期间，各平台销售数据公布。")
    event = make_event(title="2025双11销售数据汇总", fact="2025年双11期间各平台销售数据公布",
                       occurred_at="2025-11-11")

    # 账本已有同 fact_hash（模拟历史出现）
    ledger_entry = {
        "cluster_key": "test:2025双11销售数据汇总:",
        "fact_hash": "",  # 我们会构造和 event 相同的 hash
        "first_seen_at": "2025-11-12",
        "last_seen_at": "2025-11-12",
        "event_title": "2025双11销售数据汇总",
    }
    from skills.temporal_fact_validation.temporal_fact_validation import generate_fact_hash
    ledger_entry["fact_hash"] = generate_fact_hash(event)

    content_dates = extract_content_dates("2025年双11期间，各平台销售数据公布。", NOW)
    temporal = validate_event_times(event, article_date_sanity_check(article, NOW), content_dates, NOW)
    freshness = classify_fact_freshness(event, article_date_sanity_check(article, NOW), temporal, ledger_entry, NOW)
    assert freshness["fact_freshness_status"] == "republished_old_fact", freshness
    assert freshness["temporal_eligibility"] == "reject"


# ═══════════════════════════════════════════════════════════════
# 案例 3: 今天发布、复盘去年多个事件 → retrospective_summary
# ═══════════════════════════════════════════════════════════════
def test_case3_retrospective_summary():
    article = make_article(
        content="回顾2025年：2025年3月美团上线闪购，2025年6月京东推出小时购，"
                "2025年11月双11各平台大战，2025年12月年货节启动。本文对去年事件进行复盘。")
    event = make_event(title="2025年即时零售大事回顾复盘", fact="复盘2025年即时零售多起事件")
    content_dates = extract_content_dates(article["title"] + " " + article["content"], NOW)
    assert content_dates["historical_date_ratio"] >= 0.6
    temporal = validate_event_times(event, article_date_sanity_check(article, NOW), content_dates, NOW)
    freshness = classify_fact_freshness(event, article_date_sanity_check(article, NOW), temporal, None, NOW)
    assert freshness["fact_freshness_status"] == "retrospective_summary", freshness
    assert freshness["temporal_eligibility"] == "reference"


# ═══════════════════════════════════════════════════════════════
# 案例 4: 今天发布、包含去年背景+今天新数据 → 拆成 historical_background + updated_fact
# ═══════════════════════════════════════════════════════════════
def test_case4_split_background_and_update():
    # 模拟 extract 阶段已拆成两个事件: 一个旧背景、一个新数据
    bg_event = make_event(title="2025年即时零售市场规模回顾",
                          fact="2025年即时零售市场规模约1.5万亿", occurred_at="2025-12-31")
    new_event = make_event(title="2026年上半年即时零售增速发布",
                           fact="2026年上半年即时零售市场同比增长20%，今天官方公布最新数据",
                           occurred_at="2026-08-09")

    article = make_article(content="2025年市场规模1.5万亿。2026年8月9日，官方公布上半年增长20%。")
    content_dates = extract_content_dates(article["title"] + " " + article["content"], NOW)

    # 背景事件 → historical_background
    temporal_bg = validate_event_times(bg_event, article_date_sanity_check(article, NOW), content_dates, NOW)
    f_bg = classify_fact_freshness(bg_event, article_date_sanity_check(article, NOW), temporal_bg, None, NOW)
    assert f_bg["fact_freshness_status"] == "historical_background", f_bg

    # 新数据事件 → new_fact（今天有明确发生日期且窗口内）
    temporal_new = validate_event_times(new_event, article_date_sanity_check(article, NOW), content_dates, NOW)
    f_new = classify_fact_freshness(new_event, article_date_sanity_check(article, NOW), temporal_new, None, NOW)
    assert f_new["fact_freshness_status"] == "new_fact", f_new
    assert f_new["temporal_eligibility"] == "core"

    # 不应整体 reject — 拆事件后各自独立判定
    assert f_bg["temporal_eligibility"] == "reference"  # 背景进 reference，不删
    assert f_new["temporal_eligibility"] == "core"


# ═══════════════════════════════════════════════════════════════
# 案例 5: 今天发布、宣布下周执行新规则 → upcoming_fact
# ═══════════════════════════════════════════════════════════════
def test_case5_upcoming_fact():
    article = make_article(content="今天官方宣布，2026年8月15日起执行即时零售新规。")
    event = make_event(title="即时零售新规发布", fact="官方宣布8月15日起执行新规",
                       occurred_at="2026-08-09", effective_at="2026-08-15")
    content_dates = extract_content_dates(article["title"] + " " + article["content"], NOW)
    temporal = validate_event_times(event, article_date_sanity_check(article, NOW), content_dates, NOW)
    freshness = classify_fact_freshness(event, article_date_sanity_check(article, NOW), temporal, None, NOW)
    assert freshness["fact_freshness_status"] == "upcoming_fact", freshness
    assert freshness["temporal_eligibility"] == "core"


# ═══════════════════════════════════════════════════════════════
# 案例 6: article published_at 错误为未来7天 → date_conflict
# ═══════════════════════════════════════════════════════════════
def test_case6_future_published_date():
    future_date = (NOW + timedelta(days=7)).strftime("%Y-%m-%d")
    article = make_article(published_at=future_date, content="测试内容")
    sanity = article_date_sanity_check(article, NOW)
    assert sanity["article_time_status"] == "invalid_future_date"

    event = make_event(title="未来日期事件", fact="测试")
    content_dates = extract_content_dates("测试内容", NOW)
    temporal = validate_event_times(event, sanity, content_dates, NOW)
    freshness = classify_fact_freshness(event, sanity, temporal, None, NOW)
    assert freshness["fact_freshness_status"] == "date_conflict", freshness
    assert freshness["temporal_eligibility"] == "reject"


# ═══════════════════════════════════════════════════════════════
# 案例 7: Tavily tag/page聚合页 → listing_page，不进入事件池
# ═══════════════════════════════════════════════════════════════
def test_case7_listing_page():
    article = make_article(url="https://example.com/tags/即时零售", title="即时零售")
    assert classify_page_type(article) == "tag_page"

    article2 = make_article(url="https://example.com/category/beauty", title="美妆资讯")
    assert classify_page_type(article2) == "category_page"

    article3 = make_article(url="https://example.com/search?q=美团", title="搜索结果")
    assert classify_page_type(article3) == "search_page"

    article4 = make_article(url="https://example.com/list/1", title="新闻列表")
    assert classify_page_type(article4) == "listing_page"


# ═══════════════════════════════════════════════════════════════
# 案例 8: 今年618新规则 → new/upcoming，不被历史618过滤
# ═══════════════════════════════════════════════════════════════
def test_case8_this_year_618():
    article = make_article(content="今年618，各平台公布新规则。")
    content_dates = extract_content_dates(article["title"] + " " + article["content"], NOW)
    assert len(content_dates["campaign_refs"]) >= 1
    # 今年618映射到今年 → 不算旧年份 → 不被历史618过滤（hdr 不为高）
    assert content_dates["historical_date_ratio"] == 0.0, content_dates

    event = make_event(title="今年618新规则发布", fact="今年618各平台公布新规则",
                       occurred_at="2026-06-18")
    temporal = validate_event_times(event, article_date_sanity_check(article, NOW), content_dates, NOW)
    freshness = classify_fact_freshness(event, article_date_sanity_check(article, NOW), temporal, None, NOW)
    # 今年618（6月18日）已过但属于今年 → 不被误杀为 republished_old_fact，也不 reject
    assert freshness["fact_freshness_status"] in ("historical_background", "ongoing_fact", "new_fact"), freshness
    assert freshness["temporal_eligibility"] in ("reference", "tracking", "core")

    # 但如果是"今年618规则即将生效"（8月宣布、未来执行）→ upcoming_fact 不被历史618过滤
    event2 = make_event(title="今年618规则补充", fact="官方宣布618规则补充条款下月生效",
                        occurred_at="2026-08-09", effective_at="2026-09-01")
    temporal2 = validate_event_times(event2, article_date_sanity_check(article, NOW), content_dates, NOW)
    freshness2 = classify_fact_freshness(event2, article_date_sanity_check(article, NOW), temporal2, None, NOW)
    assert freshness2["fact_freshness_status"] == "upcoming_fact", freshness2


# ═══════════════════════════════════════════════════════════════
# 案例 9: 去年双11旧文章今天被AI重写 → republished_old_fact
# ═══════════════════════════════════════════════════════════════
def test_case9_ai_rewrite_old():
    article = make_article(
        content="2025年11月11日双11大促落幕。各大平台公布最终战报。本文为AI生成内容整理。",
        title="双11战报回顾：2025年双11各平台数据")
    event = make_event(title="2025双11战报汇总", fact="2025年11月11日双11落幕，各平台公布战报")

    from skills.temporal_fact_validation.temporal_fact_validation import generate_fact_hash
    ledger_entry = {
        "cluster_key": "test:2025双11战报汇总:",
        "fact_hash": generate_fact_hash(event),
        "first_seen_at": "2025-11-12",
        "last_seen_at": "2025-11-12",
        "event_title": "2025双11战报汇总",
    }

    content_dates = extract_content_dates(article["title"] + " " + article["content"], NOW)
    assert content_dates["historical_date_ratio"] >= 0.6
    sanity = article_date_sanity_check(article, NOW)
    temporal = validate_event_times(event, sanity, content_dates, NOW)
    freshness = classify_fact_freshness(event, sanity, temporal, ledger_entry, NOW)
    assert freshness["fact_freshness_status"] == "republished_old_fact", freshness
    assert freshness["temporal_eligibility"] == "reject"


# ═══════════════════════════════════════════════════════════════
# 端到端: temporal_fact_validation 主函数
# ═══════════════════════════════════════════════════════════════
def test_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        date = "2026-08-09"
        events_dir = os.path.join(tmp, "data", "events", date)
        cleaned_dir = os.path.join(tmp, "data", "cleaned", date)
        os.makedirs(events_dir, exist_ok=True)
        os.makedirs(cleaned_dir, exist_ok=True)

        events = [
            make_event(title="美团闪购试点新服务", fact="2026年8月9日美团闪购在多地试点新服务",
                       occurred_at="2026-08-09"),
            make_event(title="2025年即时零售复盘", fact="复盘2025年即时零售大事"),
        ]
        events[0]["event_id"] = "E20260809_0001"
        events[1]["event_id"] = "E20260809_0002"
        events_file = os.path.join(events_dir, "events_raw.json")
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump({"events": events}, f, ensure_ascii=False)

        articles = [
            make_article(content="2026年8月9日，美团闪购在多地试点新服务。"),
            make_article(title="2025年即时零售大事回顾", content="2025年3月上线、2025年6月推出、2025年11月大战。"),
        ]
        articles[0]["article_id"] = "test_001"
        articles[1]["article_id"] = "test_001"  # 都关联 test_001（简化）
        articles_file = os.path.join(cleaned_dir, "cleaned_articles.json")
        with open(articles_file, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False)

        result = temporal_fact_validation(
            project_root=tmp,
            date=date,
            events_file=events_file,
            articles_file=articles_file,
        )
        assert result.get("ok"), result
        assert os.path.exists(os.path.join(events_dir, "events_temporal.json"))


if __name__ == "__main__":
    tests = [
        test_case1_new_fact,
        test_case2_republished_old_fact,
        test_case3_retrospective_summary,
        test_case4_split_background_and_update,
        test_case5_upcoming_fact,
        test_case6_future_published_date,
        test_case7_listing_page,
        test_case8_this_year_618,
        test_case9_ai_rewrite_old,
        test_end_to_end,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{'ALL PASS' if failed == 0 else f'{failed} FAILED'}")
    sys.exit(1 if failed else 0)
