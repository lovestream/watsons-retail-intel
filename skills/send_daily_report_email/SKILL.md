# send_daily_report_email — 日报邮件发送

## 功能

读取最终日报、播客音频、审稿报告，通过 mailcli.py 发送 HTML 日报邮件（含附件）。

## 安全门

发送前检查 8 项条件，任一不满足则拒绝发送：

1. `run_manifest.json` 存在
2. `sendable=true`（来自 manifest）
3. `final_report_file` 存在
4. 终稿不以 `【待人工复核】` 开头
5. `editor_review.md` 结论为「✅ 可发送」
6. 终稿包含 8 个固定章节（无信号日报豁免）
7. 终稿至少包含 1 个 event_id（无信号日报豁免）
8. 邮件收件人存在

## 邮件标题

- 正常日报: `即时零售 × 个护美妆经营日报｜YYYY-MM-DD`
- 无信号日报: `即时零售 × 个护美妆经营日报｜YYYY-MM-DD｜无高质量新增信号`

## 邮件正文

- Markdown 转为 HTML，内联 CSS 样式
- 顶部加系统备注（自动生成、附件说明）
- 底部加页脚（日期、日报类型）

## 附件

| 附件 | 条件 |
|------|------|
| 日报 Markdown | `attach_markdown=true`（默认） |
| 播客 MP3 | `attach_podcast=true`（默认）且文件存在 |
| 播客稿 Markdown | `attach_script=false`（默认关闭） |

## 发送方式

使用 default agent 的 `/app/working/workspaces/default/skills/email/mailcli.py` 发送：
- SMTP 配置由 mailcli.py 自动管理（腾讯企业邮 SSL）
- 认证密码通过环境变量 `email_key` 传入
- 支持 HTML 正文（`--html --body-file`）和附件（`--attach`）

`config/email.yaml` 仅配置 `from`（发件人显示名）和 `to`（默认收件人）。

## 函数

```python
send_daily_report_email(
    project_root: str,
    date: str | None = None,
    recipient: str | None = None,
    subject_prefix: str = "即时零售 × 个护美妆经营日报",
    attach_markdown: bool = True,
    attach_podcast: bool = True,
    attach_script: bool = False,
    dry_run: bool = True,
) -> dict
```

返回：

```python
{
    "ok": bool,               # 安全门通过 且（已发送 或 dry_run）
    "date": str,              # YYYY-MM-DD
    "sent": bool,             # 是否真实发送（dry_run 时为 False）
    "dry_run": bool,          # 是否 dry_run
    "recipient": str,         # 收件人
    "subject": str,           # 邮件标题
    "html_length": int,       # HTML 正文字符数
    "markdown_attachment": bool,
    "podcast_attachment": bool,
    "script_attachment": bool,
    "sendable": bool,         # 安全门是否通过
    "log_file": str,          # 日志文件路径
    "errors": list[str],      # 错误列表
}
```

## CLI

```bash
# dry_run 模式（默认，不真实发送）
python skills/send_daily_report_email/send_daily_report_email.py \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-04-26

# 真实发送
python skills/send_daily_report_email/send_daily_report_email.py \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-04-26 \
    --recipient xxx@example.com \
    --dry-run false
```

## 输出

| 文件 | 路径 |
|------|------|
| 日志 | `data/logs/YYYY-MM-DD/send_daily_report_email.log` |
| JSON 结果 | `data/logs/YYYY-MM-DD/send_daily_report_email.json` |