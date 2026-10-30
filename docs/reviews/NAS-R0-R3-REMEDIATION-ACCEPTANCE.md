# 独立复审整改验收（R0–R3）

复审依据：`docs/reviews/NAS-LLM-PROMPT-INTEGRATION-INDEPENDENT-REVIEW-2026-10-29.md`（commit `a808d85`）
修订分支：`feat/prompt-integration`（本轮全部改动均在此分支，生产未切换）
测试：`tests/test_prompt_integration.py`（23）+ `tests/test_pipeline_r0_r3.py`（14）+ `tests/reader_brief/run_tests.py`（10）= **47 passed / 0 failed**

## R0 安全门与交付状态（已完成）

| 项 | 问题 | 修复 | 证据 |
|---|---|---|---|
| P0-01 | P09 不可用被记成 pass，`sendable=true` | `p09_reviewer` 改三态：`approved` / `hold_for_review` / `blocked`。任何程序门错误直接 `blocked`；模型不可用 → `hold_for_review`（不是 pass） | `skills/reader_brief/p09_reviewer.py`；`test_p09_unavailable_is_hold_not_pass` / `test_deterministic_error_is_blocked` |
| P0-01 | 报告页脚对"已核验"过描、无机检也写"附音频" | 页脚改"来自媒体原始报道的事实…未经官方确认按报道处理"；音频仅在文件存在且哈希校验后写"附本期口播音频（sha256 …）" | `render_mobile.py`、`run_brief.py`（`audio.sha256/exists`） |
| P0-01 | manifest 字段含义不明 | `manifest.json` 增 `delivery_state / sendable / deliverable / p09.rounds / audio`；仅 `approved` 可发；`hold` 退出码 7，`blocked` 退出码 2 | `/tmp/rb_w43_r0/manifest.json` → `docs/reviews/email-ux/2026-10-29-r0/` |
| P0-02 | P14 模板缺 `send_at`，正例必炸 | 载荷补齐 `send_at`（+`as_of_period_end`）；正例 5 相交周走完 render | `test_render_has_send_at_and_completes` |
| P0-03 | 文件存在即视为"已封账" | 覆盖门改读 `data/runs/periodic/{type}/{period_id}/manifest.json`：`closed + quality_pass + report_sha 匹配 + source_coverage≥0.6` 才算 verified；degraded/incomplete **不调 LLM** | `cross_period._closure`；4 个覆盖门测试 |
| P0-03 | 月报按整周取报告、年报日期硬编码 | 按 `[月份∩ISO周]` 日级切片；所有年份目录动态遍历，无 `2026-*` 硬编码 | `cross_period.iso_weeks_of_month` / `_event_facts` |
| P0-04 | degraded 仍调用 LLM；输入贫瘠；输出不校验 | 只有 `complete` 才渲染/调用；输入带周报要点、日级 claim 切片、封账证据、ledger 指纹；LLM 输出逐条校验 `fact_id/claim_id/event_id/source_url`，未知引用 → `hold_for_review`；缓存键含来源 sha + prompt 版本 + 模型 | `_validate_synthesis`、`_cache_key`；`test_unknown_ids_flag_hold`、`test_cache_hit_and_invalidation` |
| P0-04 | 月/年可用 `--pack-in` 绕过来源门 | `run_brief` 对 monthly/yearly 强制 `--synthesis-in`，`--pack-in` 直接拒绝（退出码 3） | `skills/reader_brief/run_brief.py` |
| P1-01 | P05 选题丢 ID 后静默删题、P08 仍可引用未发布事实 | 无效选题记 `rejected_topics` 并停发；P08 稿按 `published_fact_ids` 校验，越界报 `p05_topic_not_published` | `editor._validate_topic_plan`、`schema.validate_brief(allowed_fact_ids=)` |
| P1-02 | 主备混池、RC 复用 SF 模型名 | 主池仅 SiliconFlow；RightCode 单独一次调用，自带 `right.codes` + `deepseek-flash` + `rightcode_bybt`；OpenCode 残留代码删除 | `llm_client._load_provider_endpoints`；`test_primary_then_single_rightcode_fallback` 断言 RC 恰好 1 次且模型名正确 |
| P1-03 | 邮件端 CSS 依赖 `var()`，Outlook 丢色 | 模板去 `var(--…)`（0 处），颜色写死；单列 div 布局、无 flex/table | `grep -c "var(--" render_mobile.py` = 0；360/375/390/600 截图溢出 0px |
| P1-04 | 提示词写死"3 个趋势/三件事/3-5 条洞察" | 改为证据驱动："有证据几条写几条；不凑数" | `skills/generate_periodic_report/prompts.py` |

### 长报告链路接入（P14/P15 → P06/P07/P10）

- `generate_periodic_report.py` 在 Step 0 之前增加跨期来源门：月/年报必须拿到 P14/P15 且 `status=complete`，否则 `sendable=false`（`cross_period_unavailable|degraded|incomplete`）。
- 章节源注入：月报 `02/03/06/07`、年报 `01/02/06/07` 追加"跨期综合结论"块，并明确"引用必须与具体事件对得上"。
- 播客沿用同一份报告（报告已含综合结论），不二次引入未校验数字。

### 内容错误根因（不是简单改字）

| 现象 | 根因 | 修复 |
|---|---|---|
| "SK-II"被写成"S-II" | `facts_pack._clean` 的标签正则 `【?[ABK]】?` 把品牌名中的 `K` 当标签删掉 | 正则只匹配 `【A】/[B]/(K)/独立 A·B·K` 与 `【A-必须关注】`，SK-II 保留 |
| 事实日期显示"Tue, 20 Oc" | `_date_of` 只认 ISO/中文，RFC822 被截前 10 字符 | 新增 RFC822/英文月解析，解析失败则留空，不再写假日期 |
| 丝芙兰"4000 余人"与门店客流混淆 | 事实文本未区分活动参与人数与门店转化 | 事实显式标注"4000余人为活动参与人数，报道未披露门店客流与转化"；本期按证据不足列入未选并写明理由 |
| 天猫规则改写归属不清 | 行业分析被当作平台官方规则 | 事实文本标注"来自36氪转引行业分析，未见图官方规则原文，需按商家后台类目规则核对" |

## R1 正例与测试（已完成）

- 正例：5 相交周（2026-07 覆盖 W27–W31）+ 12 个月（含 P14 complete 结果）离线夹具，`monthly_synthesis` / `yearly_synthesis` 均 `status=complete`、render 全变量走通、`_validation.ok=true`、缓存命中与失效各一次。
- 反例：缺 manifest → `degraded` 且 `llm_called=false`；质量门失败 → `degraded`；未知 ID/URL → `hold_for_review`。
- 测试入库：`.gitignore` 增加 `!tests/test_*.py`，`tests/test_prompt_integration.py`、`tests/test_pipeline_r0_r3.py`、`tests/reader_brief/test_reader_brief.py` 均已被跟踪。

## R2 主线整合（进行中）

- 已 fetch `origin/main`（`2f1a4f0`，领先 29 提交，均为 P14/P15 文档迭代，不含 `skills/reader_brief`），本轮在此分支合并后再验收；合并冲突若涉及本文档以本文件为准。

## R3 交付证据与已知限制

- 证据目录：`docs/reviews/email-ux/2026-10-29-r0/`（`weekly-2026-W43.html`、`manifest.json`、`reader_brief.json`、P09 两轮原始审稿、`topic_plan.json`、360/375/390/600 截图）。
- 本期 W43 交接状态：`approved`、`sendable=true`、13 条事实、引用 4 条、P09 定点更正 6 处后复核通过；渲染 0 内标记、0 溢出。
- **限制 1**：沙箱无 Gmail/Outlook 账号，无法做真实客户端截图；已用"去 CSS 变量 + 单列 + 无 flex/表格"降低风险，仍建议生产侧用真实邮箱做一次渲染核对。
- **限制 2**：仓库无 CI 服务；测试以脚本形式提供，运行命令写入本文档与测试文件头。
- **限制 3**：生产 `data/events` 仅到 2026-09-30，生产侧仍无法生成真实 W43；本期用受控事实包（13 条）验证链路，未伪造生产数据。
- **未做（需单独授权）**：生产切换、真实发信、Cron 变更、回填生产事件库。
