# LLM 独立提示词接入验收（DeepSeek 执行记录）

日期：2026-10-29　分支：`feat/reader-brief`（隔离工作树 `/tmp/rb_wt`）
依据：`docs/prompts/README.md`（15 份提示词）、`docs/16_20261029_deepseek_llm_prompt_integration.md`
状态：**代码已接入并跑通真实 W43 回放；未合并 main、未发 SMTP、未动 Cron/PID1/生产账本**

## 1. 本轮的硬性变更

| 项 | 变更 | 说明 |
|---|---|---|
| 模型渠道 | **删除 OpenCode**；SiliconFlow 主 + RightCode 备 | 环境变量 `siliconflow_api_key` / `rightcode_bybt`；RightCode 模型按用户指定改为 `deepseek-flash`（`RIGHTCODE_MODEL` 可覆盖） |
| 提示词来源 | 新增 `skills/utils/prompt_registry.py` + `config/prompt_registry.yaml` | 运行时只抽取 md 的 `## System Prompt` / `## User Prompt`，变量声明+渲染校验，版本=文本 sha256 前 12 位 |
| 旧模块兼容 | 新增 `skills/utils/prompt_bindings.py` | P01/P02/P04/P06/P07/P10 以 registry 文本为准，**追加旧字段白名单**，不破坏既有解析器 |
| 邮件编辑 | 重写 `skills/reader_brief/editor.py`（P05+P08） | 结构化 `reader_brief.json`，非机械摘句 |
| 事实复核 | 新增 `skills/reader_brief/p09_reviewer.py` + `schema.py` | 程序确定性门 + P09 主张级复核；critical 未解决即 block |
| 跨期汇总 | 新增 `skills/periodic_synthesis/cross_period.py`（P14/P15） | 自然月 4～5 个 ISO 周覆盖门、12 月封账门；缺周/缺月 **incomplete，不调用 LLM** |
| 渲染 | 重写 `skills/reader_brief/render_mobile.py` | 只吃已审 JSON；竞对单列；就地来源链接；无附录/审计 |

代码 diff（已跟踪文件）：`10 files changed, 640 insertions(+), 743 deletions(-)`；新增文件 8 个
（registry/bindings/cross_period/p09_reviewer/schema + `docs/prompts/*` 16 份 + 测试）。

## 2. 15 份提示词的映射与状态

| ID | 版本 | 调用文件 | 状态 |
|---|---|---|---|
| P01 | 3fc4a1ebc88f | `skills/filter_relevant_articles/filter_relevant_articles.py` | 已接入（兼容层，回退旧模板） |
| P02 | b0e3eaa6bab6 | `skills/extract_events/extract_events.py` | 已接入（系统提示词替换，保留 events/temporal 字段） |
| P03 | — | `temporal_fact_validation`（规则引擎） | **未启用**：仅疑难按需，本轮不挂载 |
| P04 | dea759fcd2ba | `skills/analyze_business_impact/analyze_business_impact.py` | 已接入（保留 business_analysis 全部枚举） |
| P05 | fbe1742eebc1 | `skills/reader_brief/editor.py:build_topic_plan` | 新增，每期 1 次；失败回退规则排序 |
| P06 | eb13994ad402 | `skills/generate_periodic_report/prompts.py` | 已接入（章节结构与质量门保留） |
| P07 | bfdbefeed029 | 同上 | 已接入（审稿提示词追加） |
| P08 | b402c2f55c43 | `skills/reader_brief/editor.py:write_brief` | 新增，每期 1 次 |
| P09 | a1003f9ce985 | `skills/reader_brief/p09_reviewer.py` | 新增，每期 1 次；不可用时降级到确定性门并标注 |
| P10 | 7f165ecc0665 | `skills/generate_periodic_report/prompts.py` | 已接入（口播） |
| P11 | — | 播客审稿 | **未启用**：重要稿可选，避免双审 |
| P12 | — | 旧日报 | **未启用**：日报邮件/日播未恢复 |
| P13 | — | 评分二审 | **未启用**：仅 P0/P1 边界可选 |
| P14 | 14a239f7c0be | `cross_period.py` | 新增：月度 1 次；覆盖门不完整则 0 次 |
| P15 | 225c403c0c1f | `cross_period.py` | 新增：年度 1 次；12 月未封账则 0 次 |

每期调用数：周报 3 次（P05/P08/P09）；月报 +1（P14）；年报 +1（P15）。不做全量多轮审核。

## 3. 真实 W43 回放（2026-10-19 ~ 10-25，13 条已核验事实）

产物在 `docs/reviews/email-ux/2026-10-29-llm/`：`reader_brief.json`、`brief.html`、
`p09_review.json`、`manifest.json`、4 张截图、`W43_fact_pack.json`。

- 标题：**天猫双11改官方立减，屈臣氏套组要重算**
- 结构：2 个重点变化 / 1 张竞对卡（丝芙兰玩妆节）/ 2 个有条件行动 / 2 条持续留意 / 8 条明确删除并给理由
- 行动均为"前提→小范围做法→单一指标"（例：重算天猫单品折扣带 → 毛利率；核对开门红货盘资源位 → 转化率）
- 程序确定性门：通过；`schema.validate_brief` 0 error；数字/链接/状态/期限全部绑定原始事实
- P09：本轮回放中 SF 单次调用超时→降级为"确定性检查通过（未做主张级复核）"，manifest 已如实标注；
  上一轮回放 P09 成功返回结构化审稿并指出 7 处问题（标题/结论过度推断、榜单被误归为双11、名次推需求），
  说明 P09 能拦住"流畅但越界"的句子；其 `suggested_replacements` 已支持 `title` 等顶层路径并会重跑确定性门。
- 手机 360/375/390/600px：横向溢出 **0px**，页面高 1519～1887px。

## 4. 覆盖门实测（P14/P15）

`M2026-07_gate.json`：2026-07 的相交 ISO 周为 **W27–W31（5 个）**，当前仓库没有这 5 份已封账周报 →
`status=incomplete`，`missing_weeks` 全列，**未调用 LLM**、不产出"大周报"。年度同理（12 个月缺失 → incomplete）。
这满足 docs/16 "有4个文件不能强行出月报"、"12月未封账不得假装全年"。

## 5. 测试

`PYTHONPATH=".venv_packages:." python3 tests/test_prompt_integration.py` → **23 passed / 0 failed**：
registry 15 份全部章节/变量校验通过；渲染缺变量报错；P08 schema 反例（未知 fact_id、数字不在原文、
状态越级、过期期限、假链接、event_id 泄漏、伪造人工核验、超量条目）；P05 选题引用校验与 must_read 上限；
P14/P15 五周/十二月覆盖门；P01/P02/P04 旧字段兼容块；`llm_client` 不再含 OpenCode 实际端点常量。

## 6. 已知限制（不谎报）

1. **RightCode 在本沙箱不可达**：`right.codes` 解析到 `198.18.5.75`（保留网段），TLS 前即被重置。
   备用链路代码已接（`tier=fallback`），但本环境无法完成真实 200 回放；生产环境需复测。
   已按用户指定把模型名改为 `deepseek-flash`。
2. **P09 在长提示下可能超时**：已把单次 timeout 提到 ≥300s，并在超时时降级 + 显式标注，不冒充"已审"。
3. 月/年报真实重生成仍缺输入：仓库无 2026-07 周报、无 12 个月月报 → P14/P15 只能出 incomplete 门禁记录。
   未用 fixture 冒充年报通过。
4. 生产未切换：`generate_periodic_report`、`send_daily_report_email` 仍走旧链路；本轮只交付隔离分支代码与验收物。

## 7. 未做（等授权）

合并 main / 生产 switchover / 真实 SMTP 发送 / Cron 与 PID1 变更 / 生产事件账本写入 / 月报周报补齐回填。
