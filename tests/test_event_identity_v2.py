#!/usr/bin/env python3
"""P0-1 五维身份 + 先比内容 正反例测试。

重点覆盖用户指出的场景：
  - 同 URL 但内容更新（必须 material_delta，不能判为重复）
  - 不同对象/渠道/地点不应合并为同一事件
  - 屈臣氏共创、菜鸟重复、县域商超等真实案例
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.event_identity import (  # noqa
    compare, detect_stage, evaluate, event_identity, event_profile, extract_facts,
)


def run_sequence(seq):
    """按顺序喂入事件，返回每一步的 action。"""
    ledger = {}
    out = []
    for ev in seq:
        r = evaluate(ledger, ev)
        out.append(r["action"])
        if r["action"] in ("new", "material_delta"):
            ledger[r["identity"]] = {"profile": r["profile"],
                                     "event_title": r["event_title"],
                                     "fact": r["fact"], "url": r["url"]}
    return out


class ContentFirstTest(unittest.TestCase):
    """先比内容，再判同源 —— 用户明确要求的判定顺序。"""

    def test_same_url_content_updated_is_material_delta(self):
        """同 URL，但门店数 20→30：必须是 material_delta，不是重复。"""
        a = {"event_title": "山姆全国门店达20家",
             "fact": "山姆会员商店全国门店数达20家",
             "source_url": "https://same.com/a"}
        b = {"event_title": "山姆全国门店达30家",
             "fact": "山姆会员商店全国门店数达30家",
             "source_url": "https://same.com/a"}   # 同一 URL
        acts = run_sequence([a, b])
        self.assertEqual(acts, ["new", "material_delta"],
                         "同 URL 内容更新必须识别为实质变化")

    def test_same_url_identical_content_is_observation(self):
        """同 URL 且内容完全一致：只算观测。"""
        a = {"event_title": "菜鸟接入淘宝闪购",
             "fact": "菜鸟供应链加入淘宝闪购提供小时达服务",
             "source_url": "https://same.com/x"}
        acts = run_sequence([a, dict(a), dict(a)])
        self.assertEqual(acts, ["new", "observation_only", "observation_only"])

    def test_same_url_stage_advance_is_material_delta(self):
        """同 URL，宣布 → 正式上线：实质变化。"""
        a = {"event_title": "美团闪购直播工具", "fact": "美团闪购宣布将上线直播工具",
             "source_url": "https://s.com/z"}
        b = {"event_title": "美团闪购直播工具上线", "fact": "美团闪购直播工具已正式上线",
             "source_url": "https://s.com/z"}
        acts = run_sequence([a, b])
        self.assertEqual(acts, ["new", "material_delta"])

    def test_price_change_same_url_is_material_delta(self):
        a = {"fact": "该套餐售价30元", "source_url": "https://p.com/1"}
        b = {"fact": "该套餐售价25元", "source_url": "https://p.com/1"}
        self.assertEqual(run_sequence([a, b]), ["new", "material_delta"])


class DimensionTest(unittest.TestCase):
    """五维度身份：不同对象/渠道/地点不应合并。"""

    def test_different_object_not_merged(self):
        """同主体同动作，但品类不同（美妆 vs 3C）→ 两个事件。"""
        a = {"event_title": "美团闪购美妆品类增长", "fact": "美团闪购美妆个护品类增长100%",
             "source_url": "https://a/1"}
        b = {"event_title": "美团闪购3C品类增长", "fact": "美团闪购3C数码品类增长200%",
             "source_url": "https://a/2"}
        # 身份键为 主体|动作；对象差异由 compare() 判定为不同事件
        self.assertEqual(run_sequence([a, b]), ["new", "new"])

    def test_different_location_not_merged(self):
        """同主体同动作，不同城市 → 两个事件。"""
        a = {"event_title": "山姆上海开店", "fact": "山姆会员商店在上海新开一店",
             "source_url": "https://b/1"}
        b = {"event_title": "山姆成都开店", "fact": "山姆会员商店在成都新开一店",
             "source_url": "https://b/2"}
        self.assertEqual(run_sequence([a, b]), ["new", "new"])

    def test_different_channel_not_merged(self):
        """同主体，线下门店 vs 即时零售 → 渠道维度不同。"""
        a = {"event_title": "屈臣氏线下门店调改", "fact": "屈臣氏门店专柜完成调改",
             "source_url": "https://c/1"}
        b = {"event_title": "屈臣氏即时零售上线", "fact": "屈臣氏即时零售小时达上线",
             "source_url": "https://c/2"}
        pa, pb = event_profile(a), event_profile(b)
        self.assertNotEqual(pa["channel"], pb["channel"])
        self.assertEqual(run_sequence([a, b]), ["new", "new"])

    def test_same_identity_different_wording_merged(self):
        """同主体同动作同对象，措辞不同 → 同一身份。"""
        a = {"event_title": "菜鸟供应链加入淘宝闪购",
             "fact": "菜鸟供应链加入淘宝闪购，提供小时达配送", "source_url": "https://d/1"}
        b = {"event_title": "菜鸟为淘宝闪购提供配送",
             "fact": "菜鸟接入淘宝闪购，提供小时达履约服务", "source_url": "https://d/2"}
        self.assertEqual(event_identity(a), event_identity(b))


class RealCaseTest(unittest.TestCase):
    """真实案例：屈臣氏共创 / 菜鸟重复 / 县域商超。"""

    def test_cainiao_repeated_23_times_one_event(self):
        """菜鸟同稿跨日 23 次 → 1 个事件。"""
        ev = {"event_title": "菜鸟供应链加入淘宝闪购提供小时达服务",
              "fact": "菜鸟供应链加入淘宝闪购提供小时达服务，覆盖3C数码",
              "source_url": "https://e.com/cainiao"}
        seq = [ev] * 23
        acts = run_sequence(seq)
        self.assertEqual(acts[0], "new")
        self.assertTrue(all(a == "observation_only" for a in acts[1:]),
                        f"应为 1 new + 22 observation，实际 {acts}")

    def test_county_retail_three_players_separate(self):
        """山姆/盒马/奥乐齐县域动作 → 3 个独立事件。"""
        seq = [
            {"event_title": "山姆江阴店开业", "fact": "山姆会员商店江阴店开业，全国第70家门店",
             "source_url": "https://f/1"},
            {"event_title": "盒马下沉县域", "fact": "盒马鲜生和超盒算NB双业态下沉县域市场",
             "source_url": "https://f/2"},
            {"event_title": "奥乐齐县域开店", "fact": "奥乐齐沿沪宁线在江阴、张家港开店",
             "source_url": "https://f/3"},
        ]
        self.assertEqual(run_sequence(seq), ["new", "new", "new"])

    def test_watsons_co_creation_identity(self):
        """屈臣氏共创：应解析出 subject=屈臣氏 + 动作。"""
        ev = {"event_title": "屈臣氏有效创新背后共创成为关键一步",
              "fact": "屈臣氏与品牌方共创新品，落地线下门店专柜"}
        p = event_profile(ev)
        self.assertEqual(p["subject"], "屈臣氏")
        self.assertIn(p["action"], ("接入合作", "开店闭店", "直播营销", ""))


class StageTest(unittest.TestCase):
    def test_future_launch_is_announced(self):
        self.assertEqual(detect_stage("公司宣布将上线新功能"), "announced")

    def test_actual_launch_is_launched(self):
        self.assertEqual(detect_stage("新功能已正式上线"), "launched")

    def test_trial(self):
        self.assertEqual(detect_stage("项目进入试点阶段"), "trial")


class FactsTest(unittest.TestCase):
    def test_extract_store_and_city(self):
        f = extract_facts("全国门店总数突破2500家，覆盖102座城市")
        self.assertEqual(f.get("store_count"), "2500")
        self.assertEqual(f.get("city_count"), "102")


if __name__ == "__main__":
    unittest.main(verbosity=2)
