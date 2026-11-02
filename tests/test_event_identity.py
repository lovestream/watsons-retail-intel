#!/usr/bin/env python3
"""S3-2/S3-3 事件身份合同测试：同稿重复 vs 实质更新。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills.event_identity import (  # noqa
    content_hash, detect_stage, evaluate, event_identity, extract_numbers,
    is_material_delta,
)


class IdentityTest(unittest.TestCase):

    def test_same_article_reseen_is_observation_only(self):
        """菜鸟同稿重复 23 次：只增加观测，不产生新事件。"""
        ev = {"event_title": "菜鸟供应链加入淘宝闪购提供小时达服务",
              "fact": "菜鸟供应链加入淘宝闪购提供小时达服务，覆盖3C数码",
              "source_url": "https://example.com/cainiao"}
        ledger = {}
        r1 = evaluate(ledger, ev)
        self.assertEqual(r1["action"], "new")
        ledger[r1["identity"]] = {"url": r1["url"], "content_hash": r1["content_hash"],
                                  "event_title": ev["event_title"], "fact": ev["fact"]}
        for _ in range(22):
            r = evaluate(ledger, ev)
            self.assertEqual(r["action"], "observation_only",
                             "重复 23 次不应产生 23 个事件")
            self.assertEqual(r["reason"], "same_article_reseen")

    def test_different_url_same_content_is_observation_only(self):
        """跨源转载同内容：不按观察次数提高置信度。"""
        ev1 = {"event_title": "山姆江阴店开业", "fact": "山姆会员商店江阴店开业，全国第70家门店",
               "source_url": "https://a.com/1"}
        ev2 = dict(ev1, source_url="https://b.com/2")
        ledger = {}
        r1 = evaluate(ledger, ev1)
        ledger[r1["identity"]] = {"url": r1["url"], "content_hash": r1["content_hash"],
                                  "event_title": ev1["event_title"], "fact": ev1["fact"]}
        r2 = evaluate(ledger, ev2)
        self.assertEqual(r2["action"], "observation_only")

    def test_store_count_change_is_material_delta(self):
        """门店数 20 -> 30 属实质更新，必须保留。"""
        old = {"event_title": "山姆全国门店达20家", "fact": "山姆会员商店全国门店数达20家"}
        new = {"event_title": "山姆全国门店达30家", "fact": "山姆会员商店全国门店数达30家",
               "source_url": "https://a.com/new"}
        self.assertTrue(is_material_delta(old, new))
        ident = event_identity(old)
        ledger = {ident: {"url": "https://a.com/old",
                          "content_hash": content_hash(old["fact"]),
                          "event_title": old["event_title"], "fact": old["fact"]}}
        r = evaluate(ledger, new)
        self.assertEqual(r["action"], "material_delta")

    def test_stage_progression_is_material_delta(self):
        """宣布 -> 上线 -> 铺开：阶段推进必须保留。"""
        old = {"fact": "美团闪购宣布将上线直播工具"}
        new = {"fact": "美团闪购直播工具已正式上线", "source_url": "https://x/y"}
        self.assertEqual(detect_stage(old["fact"]), "announced")
        self.assertEqual(detect_stage(new["fact"]), "launched")
        self.assertTrue(is_material_delta(old, new))

    def test_price_change_is_material_delta(self):
        old = {"fact": "该套餐售价 30 元"}
        new = {"fact": "该套餐售价 25 元"}
        self.assertTrue(is_material_delta(old, new))

    def test_extract_numbers(self):
        n = extract_numbers("全国门店总数突破2500家，覆盖102座城市，单量超8万单")
        self.assertEqual(n.get("store_count"), "2500")
        self.assertEqual(n.get("city_count"), "102")

    def test_identity_stable_across_wording(self):
        """同一事件不同措辞，身份应一致。"""
        a = {"event_title": "菜鸟供应链加入淘宝闪购提供小时达服务",
             "fact": "菜鸟供应链加入淘宝闪购，提供小时达配送服务"}
        b = {"event_title": "菜鸟为淘宝闪购提供配送服务",
             "fact": "菜鸟接入淘宝闪购，提供小时达配送"}
        self.assertEqual(event_identity(a), event_identity(b))


if __name__ == "__main__":
    unittest.main(verbosity=2)
