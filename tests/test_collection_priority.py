#!/usr/bin/env python3
"""S3 确定性测试：采集优先级与 800 条上限。

验证任务书 S3-1 的关切：
  可信官方/行业 RSS 是否会被 XCrawl、Tavily 泛搜索提前挤占额度。

本测试用纯函数模拟 method 顺序 + 额度消耗，不发起真实网络请求。
"""
import unittest

# 现状顺序（取自 collect_daily_articles.py:2146）
CURRENT_ORDER = ["xcrawl", "tavily", "rsshub", "rss", "web", "baidu_news"]

# 各 method 的单日典型产量（来自 10-27~30 实测 quality_funnel_report）
TYPICAL_YIELD = {
    "xcrawl": 0,      # 本环境实测 0（仅正文增强，非采集源）
    "tavily": 15,     # tavily_gap 四日 15 篇
    "rsshub": 113,    # RSSHub 四日 113 篇
    "rss": 0,
    "web": 70,        # web_monitor 四日 70 篇
    "baidu_news": 0,
}

CAP = 800


def simulate(order, cap=CAP, yields=None):
    """按 order 依次消耗额度，返回各 method 的采集量与是否被截断。"""
    yields = yields or TYPICAL_YIELD
    used = 0
    taken = {}
    truncated = []
    for m in order:
        if used >= cap:
            taken[m] = 0
            truncated.append(m)
            continue
        want = yields.get(m, 0)
        take = min(want, cap - used)
        taken[m] = take
        used += take
        if take < want:
            truncated.append(m)
    return taken, used, truncated


class CollectionPriorityTest(unittest.TestCase):

    def test_current_order_puts_generic_search_before_vertical(self):
        """现状：泛搜索（xcrawl/tavily）排在垂直订阅（rsshub）之前。"""
        i_x = CURRENT_ORDER.index("xcrawl")
        i_t = CURRENT_ORDER.index("tavily")
        i_r = CURRENT_ORDER.index("rsshub")
        self.assertLess(i_x, i_r, "xcrawl 在 rsshub 之前")
        self.assertLess(i_t, i_r, "tavily 在 rsshub 之前")

    def test_high_volume_search_can_starve_vertical(self):
        """若泛搜索产量巨大，会挤占垂直订阅额度。"""
        heavy = dict(TYPICAL_YIELD)
        heavy["tavily"] = 700          # 泛搜索产量飙升
        taken, used, trunc = simulate(CURRENT_ORDER, cap=800, yields=heavy)
        # 垂直订阅被截断
        self.assertIn("rsshub", trunc,
                      f"rsshub 应被截断，实际 {taken}")
        self.assertLess(taken["rsshub"], heavy["rsshub"])
        self.assertGreaterEqual(used, 800)

    def test_vertical_first_protects_high_value_source(self):
        """改序后：垂直订阅优先，不会被泛搜索挤掉。"""
        v_first = ["rsshub", "rss", "web", "tavily", "xcrawl", "baidu_news"]
        heavy = dict(TYPICAL_YIELD)
        heavy["tavily"] = 700
        taken, used, trunc = simulate(v_first, cap=800, yields=heavy)
        self.assertEqual(taken["rsshub"], heavy["rsshub"],
                         "垂直订阅应完整保留")
        self.assertIn("tavily", trunc, "泛搜索应被截断")

    def test_cap_enforced(self):
        """总量上限必须严格生效。"""
        taken, used, _ = simulate(CURRENT_ORDER, cap=800)
        self.assertLessEqual(used, 800)

    def test_zero_yield_method_does_not_block(self):
        """产量为 0 的 method 不应消耗额度或阻塞后续。"""
        taken, used, _ = simulate(CURRENT_ORDER, cap=800)
        self.assertEqual(taken["xcrawl"], 0)
        self.assertGreater(taken["rsshub"], 0, "rsshub 仍应被采集")


if __name__ == "__main__":
    unittest.main(verbosity=2)
