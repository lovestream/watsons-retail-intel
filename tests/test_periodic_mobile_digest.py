import re
import unittest
from pathlib import Path

from skills.send_daily_report_email.periodic_mobile_digest import (
    has_internal_marker,
    render,
)

FIXTURES = Path(__file__).parent / "fixtures" / "periodic"

SAMPLE = """# 周报
## 01 本周一句话
门店应把大促计划装和应急个护分开经营。

> **证据级别与评分口径**：7条P2，6条背景。此信息仅用于后台。

## 02 本周三大趋势
### 趋势一：双11
据媒体报道，品牌榜单呈现高端与功效国货并进。

**支撑事件**
- 样本事件。

### 趋势二：即时零售
即时配送网络扩展，个护的承接效果需要经营数据判断。

## 04 竞对动态
| 竞对 | 本周动作 | 战略影响 | 屈臣氏建议 |
| --- | --- | --- | --- |
| 丝芙兰 | 在上海举办线下活动，会员积分兑换入场权益 | 从折扣转向参与和体验 | 建议小规模试点 |

## 05 经营建议
**建议一：先检查门店库存并按需求分层**
- 依据：已归档到内部证据池。
**建议二：优化会员体验权益**
- 下一工作周验证什么：这不应该进入邮件。
**建议三：减少无效凑单投入**

## 06 下一工作周关注点
### 为什么下一工作周是关键
下一工作周验证什么。为什么下一工作周是关键。
下一工作周验证什么。

## 证据来源
- https://example.com/full-archive-url

## 附录：证据级别与关键论断来源
| 论断 | 来源与证据级别 |
| --- | --- |
| 假说 | P2候选 |
"""


class MobileDigestTest(unittest.TestCase):
    def test_only_reader_content_and_competitor_card(self):
        output = render(SAMPLE, "周报", "2026-W43")
        for wanted in ("双11", "丝芙兰", "会员积分", "门店库存", "2026-W43"):
            self.assertIn(wanted, output)
        for banned in ("证据级别", "评分口径", "P2候选",
                       "为什么下一工作周是关键", "下一工作周验证什么",
                       "https://example.com", "附录", "来源与证据级别",
                       "内部证据池"):
            self.assertNotIn(banned, output)
        self.assertNotIn("<th", output)
        self.assertIn("max-width:600px", output)
        self.assertIn("viewport", output)
        self.assertFalse(has_internal_marker(output))
        self.assertLess(len(output), 8000)

    def test_safe_escaped_subject_and_no_forced_empty_sections(self):
        output = render("## 01 摘要\n\n只有一段内容。", "<周报>", "2026-W43")
        self.assertIn("&lt;周报&gt;", output)
        self.assertIn("只有一段内容。", output)
        self.assertNotIn("竞对动作", output)
        self.assertNotIn("值得持续留意", output)

    def test_audit_only_document_is_not_sendable(self):
        with self.assertRaises(ValueError):
            render("## 01 总结\n\n> 证据级别 7P2\n\n## 证据来源\n网址", "月报", "10月")

    def test_monthly_sample_has_real_cards_actions_and_risk_watch(self):
        md = (FIXTURES / "monthly_sample.md").read_text()
        output = render(md, "月报", "2026年7月", "monthly")
        for wanted in ("双强相持", "丽人丽妆", "8月3日前",
                       "四大即时零售渠道", "补贴退坡", "2026年7月"):
            self.assertIn(wanted, output)
        self.assertNotIn("证据来源", output)
        self.assertNotIn("具体行动项", output)
        self.assertFalse(has_internal_marker(output))
        self.assertNotIn("<th", output)

    def test_yearly_sample_has_three_highlights_and_actions(self):
        md = (FIXTURES / "yearly_sample.md").read_text()
        output = render(md, "年报", "2026年", "yearly")
        for wanted in ("双强相持", "丝芙兰", "把门店网络变成即时零售履约资产",
                       "合规与稳定供货", "2026年"):
            self.assertIn(wanted, output)
        self.assertNotIn("证据来源", output)
        self.assertFalse(has_internal_marker(output))
        self.assertNotIn("<th", output)

    def test_selected_sentences_are_complete_not_ellipsized(self):
        md = (FIXTURES / "monthly_sample.md").read_text()
        output = render(md, "月报", "2026年7月", "monthly")
        visible = re.sub(r"<[^>]+>", " ", output)
        self.assertNotIn("…", visible)
        self.assertNotIn("...", visible)


if __name__ == "__main__":
    unittest.main()
