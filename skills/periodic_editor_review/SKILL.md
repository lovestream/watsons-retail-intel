# periodic_editor_review — 周期报告最终质量门

对终稿执行规则化质检，输出 `sendable` 判定。

## 核心原则

- 任何 high issue → sendable=false
- review 执行失败 → sendable=false（绝不静默降级为 acceptable）
- 章节缺失 → sendable=false
- coverage 不达标 → sendable=false
- event_id / 判断标签 / 思考泄漏 / 空占位 → high issue

## 用法

```python
from skills.periodic_editor_review.periodic_editor_review import (
    review_final_draft, rule_validate)
result = review_final_draft(
    report_type="weekly",
    final_draft="...",
    synthesis={...},
    coverage={...},
    chapter_ids=["01", ..., "06"],
    event_pool_ids=["E...", ...],
)
```

输出：

```json
{
  "sendable": true,
  "overall_quality": "good",
  "high_issues": [],
  "medium_issues": [],
  "low_issues": [],
  "failed_checks": []
}
```
