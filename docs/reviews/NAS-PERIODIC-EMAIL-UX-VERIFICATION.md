# 周期邮件移动简报接入 R3：隔离验证记录

- 日期：2026-10-28
- 状态：**隔离验收，等待 ChatGPT / 用户复核；生产未切换、未发真实邮件**
- 代码分支：`fix/periodic-email-mobile-digest-r3`
- 代码提交 SHA：`dd7054270a7ee22d978d173841e1df9d5feb322a`
- 基线：R3/E1 隔离分支（`e1/real-period-recovery` 的 R3 发送质量门），不是生产 `main`
- 设计参考：`docs/reviews/NAS-PERIODIC-EMAIL-UX-REDESIGN-2026-10-28.md` 与更新后的 W43 HTML 预览
- 生产影响：无。未修改 Cron、SMTP、PID1、正式事件账本、生产代码或收件人配置；未发送真实邮件。

## 1. 接入内容

`skills/send_daily_report_email/periodic_mobile_digest.py` 新增面向业务读者的简报 renderer，`_build_periodic_email_html` 不再把整份 Markdown 报告直接转 HTML，而是按报告类型映射章节后生成一栏式移动邮件：

| 报告 | 摘要 | 3条重点 | 竞对 | 3项建议 | 持续留意 |
| --- | --- | --- | --- | --- | --- |
| 周报 | 01 | 02 的三个 `### 趋势` | 04 表格 | 05 的 `**建议N**` | 仅当存在真实风险/机会章节才输出；周报 06“下一工作周”整章排除 |
| 月报 | 01 | 02 的三个 `###` | 04 的 `####` 叙事 | 06 的 `### P0/P1 战略` | 07 的真实风险/机会子节 |
| 年报 | 01 | 02（不足时用 03 补齐） | 05 的 `###` 叙事 | 07 的 `### 战略` | 08 的真实风险/机会子节 |

实现约束：

- 只选完整句子，不做字符截断，不输出省略号；来源 Markdown 中的就地短链接若存在则保留。
- 竞对只用单列竖向卡片，无 `<th>`、无四列表格。
- 删除/屏蔽审计与模板内容：P0/P1/P2、评分、verification、事件 ID、证据级别、ID 映射、附录、完整来源清单、“下一工作周验证什么/为什么关键/预期效果”。
- 输出宽度 `max-width:600px`，所有样式内联；无媒体查询依赖。
- 保留 R3 发送门：`require_audio` 仍生效，附件只允许 MP3，不附 `.md`；renderer 后再做一次内部标记检查。
- 摘要/重点/竞对/行动/留意模块没有足够内容时省略，不强制凑数；完全无读者内容时拒绝生成（`digest_render_failed`）。

## 2. 三份样本与实测结果

| 样本 | 类型 | 性质 | HTML 字节 | 正文中文字符 | HTML SHA256 |
| --- | --- | --- | ---: | ---: | --- |
| `2026-W43` 真实周报 | weekly | 真实报告 + 真实 MP3 | 9,315 | 2,019 | `2d94d993bc28a8b9bd9558494cc6e22635bafb49e72ff8aefef90ec725dd6b84` |
| `2026-07` 真实月报 | monthly | 真实生产月报；无当月 MP3 | 8,804 | 1,597 | `c7c4e9e434b083625df9b1de067f49e7954bf6fc6057f2a0603579db0e1d5591` |
| 2026 年报结构样本 | yearly | 测试 fixture，按真实 5–7 月月报事实压缩；不是已交付年报 | 7,723 | 1,249 | `773ad09d31d20882a1743288b2d6077113b1714e049ee6dc431461e621fc551c` |

正文长度均在设计目标 1,200–2,200 中文字符内，低于 3,500 上限。

### 2.1 读者安全门

三份 HTML 均通过：

- `has_internal_marker == False`
- 禁用词/模式命中：0（事件 ID、A/B/K、证据级别、评分口径、事件ID、置信度、附录、来源与证据、下一工作周、为什么下一、验证什么、sendable）
- `<th>`：无
- `max-width:600px`：有
- `viewport`：有
- 完整来源/附录链接：0（正文就地链接仅在有来源 Markdown 链接时保留；这三份报告没有可保留的正文链接）

### 2.2 真实发送路径 dry-run（未发 SMTP）

| 样本 | 结果 | 主题 | HTML 长度（Python 字符） | 附件 | 说明 |
| --- | --- | --- | ---: | ---: | --- |
| W43 周报 | `ok=True, dry_run=True` | 即时零售 × 个护美妆经营周报｜2026-W43（10/19–10/25） | 5,894 | 1 | 真实 MP3 7,602,860 bytes；仅 MP3，无 `.md` |
| 2026-07 月报 | `ok=True, dry_run=True` | 即时零售 × 个护美妆经营月报｜2026年7月 | 6,049 | 0 | 样本无当月 MP3，按 HTML-only 校验关闭 `require_audio`；生产仍要求音频 |
| 年报结构样本 | `ok=True, dry_run=True` | 即时零售 × 个护美妆经营年报｜2026年 | 5,533 | 0 | 同上，仅测试 renderer/HTML，不作为可发送年报 |

`send_periodic_report_email(..., dry_run=True)` 的实际返回为 `dry_run: 邮件未发送（仅模拟）`；未创建或发送真实邮件。

### 2.3 手机/窄屏布局

Chromium headless，`device_scale_factor=1`，full-page screenshot；每份报告测 360 / 375 / 390 / 600px：

- **全部 12 个组合**：`scrollWidth == viewport width`，`bodyScrollWidth == viewport width`，越界元素数 `0`
- 无横向滚动、无 `<th>`，竞对卡片单列
- 截图：
  - `docs/reviews/email-ux/2026-10-28/screenshots/weekly-2026-W43-360-375-390.png`
  - `docs/reviews/email-ux/2026-10-28/screenshots/monthly-2026-07-360-375-390.png`
  - `docs/reviews/email-ux/2026-10-28/screenshots/yearly-2026-sample-360-375-390.png`
  - 600px 单图同目录 `*-600.png`
- 原始 HTML：
  - `docs/reviews/email-ux/2026-10-28/weekly-2026-W43.html`
  - `docs/reviews/email-ux/2026-10-28/monthly-2026-07.html`
  - `docs/reviews/email-ux/2026-10-28/yearly-2026-sample.html`
- 完整机器可读结果：`docs/reviews/email-ux/2026-10-28/manifest.json`

## 3. 自动化测试

```text
tests.test_periodic_mobile_digest: 6 tests, OK
tests.test_e1_date_semantics_and_merge: 4 tests, OK
```

覆盖：

1. 审计/附录/下一工作周填充/完整来源 URL 被屏蔽，竞对为单列卡片。
2. HTML subject escape 与空模块不硬凑。
3. 只有审计内容时拒绝生成（`ValueError`）。
4. 月报真实结构：真实竞对叙事、真实里程碑行动、真实风险观察。
5. 年报结构样本：三条重点、三项战略行动。
6. 选中句子无省略号/机械截断。

复现命令：

```bash
cd /tmp/email_ux_wt
PYTHONPATH=".:$PYTHONPATH" /app/venv/bin/python3 -m unittest tests.test_periodic_mobile_digest -v
PYTHONPATH=".:$PYTHONPATH" /app/venv/bin/python3 scripts/e1_periodic_email_ux_check.py
```

## 4. 事实与编辑口径

- W43 保留“据36氪10月23日报道”等来源归属；没有把媒体口径写成平台官方公告。
- W43 的“下周验证”章（06）不进入邮件；行动中的“下一工作周起”仅作为时间语被剥离，保留实际动作，不保留模板问句。
- 月报行动直接取报告已列出的首个里程碑（8月3日/8月7日/8月10日）与交付物，不另造动作、预算或日期。
- 年报样本明确标注为 fixture，不作为真实年度交付；其事实来自现有月报，不能用它代替正式年报。
- 未增加 LLM 调用，不引入新账单系统，不改评分/账本。

## 5. 仍需业务/评审确认

1. W43 邮件的 3 条行业变化、1 张丝芙兰竞对卡、3 项建议是否达到业务人员“3 分钟可读且可行动”的标准。
2. 周报是否应保留“持续留意”模块。本次因 06 是“下一工作周关注点”整章模板而**不输出**该模块；如果业务希望保留两条观察，需要从真实风险/机会章节提供可引用内容，而不是复制 06。
3. 月报/年报的真实发送仍需各自生成当期 MP3 后，以 `require_audio=True` 再跑一次 dry-run；本次仅验证 HTML/renderer，未伪造音频。
4. 是否将该分支合并到 R3 生产候选。当前保持隔离，不修改生产 Cron/SMTP/PID1/账本、不发真实邮件。

## 6. 变更文件

- `skills/send_daily_report_email/periodic_mobile_digest.py`
- `skills/send_daily_report_email/send_daily_report_email.py`
- `tests/test_periodic_mobile_digest.py`
- `tests/fixtures/periodic/monthly_sample.md`
- `tests/fixtures/periodic/yearly_sample.md`
- `scripts/e1_periodic_email_ux_check.py`
- `docs/reviews/email-ux/2026-10-28/*`
