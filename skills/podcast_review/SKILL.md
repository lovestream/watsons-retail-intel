# podcast_review — 播客审稿

审查 LLM 生成的口播稿质量，确保不照读日报。

## 审查维度

| # | 检查项 | 说明 |
|---|--------|------|
| 1 | 口语化检查 | Markdown 符号残留、书面语、超长句、event_id |
| 2 | 非照读检查 | 是否只是日报压缩版（章节引用、日报标记） |
| 3 | 行动建议检查 | "三件事"结尾、编号动作、具体指标 |
| 4 | 事实准确性检查 | 数字过多需人工确认（简化版信号） |
| 5 | 通勤适用性检查 | 字数 800-5000、开场问候、结束语、段落长度 |

## 使用方式

```bash
python skills/podcast_review/podcast_review.py \
  --project-root . \
  --date 2026-05-17 \
  --use-llm false
```

## 在 pipeline 中的位置

```
generate_podcast → podcast_review → send_daily_report_email
```

- 只在 `generate_podcast` 成功时运行
- 失败不阻塞 pipeline（non_blocking）
- 5 项至少 3 项通过才算审稿可用

## 输出

`data/logs/{date}/podcast_review.json`:
```json
{
  "date": "2026-05-17",
  "passed": false,
  "checks_passed": 4,
  "checks_total": 5,
  "script_chars": 2214,
  "issues": [{"check": "行动建议检查", "issue": "缺少具体指标", "severity": "high"}]
}
```
