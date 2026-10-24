# S2 日期空值漏洞修复记录（2026-10-24，PR #1 复审评论 6383696372）

> 结论先说：**S2 未正式通过，不得宣布通过。** 本轮只修复机械规则脚本的 P0 漏洞并完成演示周报与盲测协议；真正新会话盲测尚未执行，S2 集成验收仍待验收。
> 旧测试记录（S2-DEDUP-ACCEPTANCE、S2-DEDUP-RETEST）保留为历史，不改写；本文件为新增修复记录。

## 一、漏洞：exact input / expected / actual

- 旧代码（`tools/s2_dedup_test.py` v1，去重条件）：
  ```python
  if c["event_date"] == r["event_date"] or c.get("announcement_date") == r.get("announcement_date") or (c.get("announcement_date") and c["announcement_date"] == r["event_date"]):
  ```
- 审查方反例 exact input：浙江事件 subject/object/action 不变，仅 `event_date=2026-10-23`（原事件为 2026-10-22）、`new_fact=False`，双方均无 `announcement_date`。
- Expected：日期不同且无宣布日匹配，不得判为同一次事件，不得自动合并。
- Actual（v1）：`None == None` 为 True，日期谓词返回 True，旧代码走 SAME_STORY 合并路径（本轮用旧谓词单独复现，返回 True，已验证）。
- 影响：同主体、同对象、日期不同也可能被误并；湖南 C2 的跟进特例甚至可能被空值匹配短路，未真正测到其故事关联逻辑。

## 二、修法（v2）

- `date_match()`：每个可选日期比较都要求双方实际非空；空值绝不算匹配。新增双向宣布日匹配：候选宣布日 == 记录事件日，或记录宣布日 == 候选事件日（均要求非空）。
- SAME_OCCURRENCE 要求 action 也相同；同日不同动作不再误并。
- 湖南跟进不再硬编码 ID/日期：统一规则为 subject/object/action 相同、无新事实、且候选明确标记为跟进报道（`is_followup_report=True`）时判 SAME_STORY（故事链接，不是同一次事件合并）；日期不同且非跟进报道时判 DATE_MISMATCH_REVIEW，不合并、不新增。
- `update_of` 校验：指向不存在 ID → INVALID_UPDATE_OF_NOT_FOUND；指向废止 ID → INVALID_UPDATE_OF_SUPERSEDED（并提示定向，不自动记录）；主体不符 → INVALID_UPDATE_OF_SUBJECT_MISMATCH；三者一律拒绝，不产生新记录。
- 合并历史 ID 基线：脚本可选第三参数为 LEDGER-2026-10.md，解析出不同 EV ID 9 个（与 WTD 有效 3 + 废止 1 合并后仍为 9 个不同 EV），并报告合并集合；但主 subject/object/action 匹配仍只针对 WTD JSONL，见第四节局限。

## 三、v2 回归测试结果（沙箱，真实账本未改动）

- 输入：从 GitHub 当前头下载的 WTD JSONL（SHA-256 `6cb0978286e29ed9ddb64eb8094ea3ae856b6ba1246f83931c92b8c1e2d55561`）与 LEDGER-2026-10.md；脚本为修复后的 v2。
- 测试前：WTD 4 条（有效 3、废止 1）；合并基线不同 EV 9 个。
- 决策表（全部为沙箱决策，真实账本未写入）：

| 候选 | 决策 | 新增真实 ID？ |
|---|---|---|
| C1 浙江重复报道 | SAME_OCCURRENCE → EV-20261022-01 | 否 |
| C2 湖南跟进报道 | SAME_STORY → EV-20261012-01（通用跟进规则，非硬编码） | 否 |
| C2b 旧 ID 复活 | BLOCKED_SUPERSEDED → EV-20261012-01 | 否 |
| C3 深圳重复报道 | SAME_OCCURRENCE → EV-20261022-03 | 否 |
| C4/C5 虚构新进展 | 仅沙箱 TEST-01/02，经有效 update_of | 否 |
| C6 虚构无关 | 仅沙箱 TEST-03 | 否 |
| C7 浙江日期不同（2026-10-23，审查方反例） | DATE_MISMATCH_REVIEW，不合并 | 否，且 URL 未合并进任何真实记录（断言验证） |
| C8 同日不同动作 | ACTION_MISMATCH_REVIEW，不合并 | 否，且 URL 未合并进任何真实记录（断言验证） |
| C9 update_of 不存在 | INVALID_UPDATE_OF_NOT_FOUND，拒绝 | 否 |
| C9b update_of 指向废止 ID | INVALID_UPDATE_OF_SUPERSEDED，拒绝 | 否 |
| C9c update_of 主体不符 | INVALID_UPDATE_OF_SUBJECT_MISMATCH，拒绝 | 否 |
| C10 同主体不同事件 | 仅沙箱 TEST-04，不合并 | 否 |
| C11 天猫 LEDGER-only 事件 | MERGED_BASELINE_ID_EXISTS_BUT_NOT_MATCHED_BY_WTD_SCRIPT（见局限） | 否 |

- 测试后：沙箱记录 8 条（真实 4 + TEST 4）；有效真实 ID 仍为 3 条；真实账本哈希前后一致；真实账本中 TEST- 出现 0 次；脚本断言全部通过（含 C7/C8 URL 未合并断言、TEST 记录数恰为 4 的断言）。
- 修复过程中曾出现一次中间版本错误（C2 因逻辑顺序被误判为 DATE_MISMATCH_REVIEW），已修正为统一的同主体/对象/动作分支（日期相同→SAME_OCCURRENCE，日期不同+跟进报道→SAME_STORY，日期不同+非跟进→REVIEW），并重新跑通全部候选；中间错误版本未推送。

## 四、未通过 / 局限（明确标记）

1. **S2 整体未通过**：机械规则测试 v2 通过，但真实 LLM 集成验收（盲测）尚未执行；不得把 v2 通过写成 S2 通过。
2. **合并基线匹配部分通过**：LEDGER-only 事件（如天猫）在 v2 中仅做 ID 基线核对，未做 subject/object/action 全量匹配；完整跨会话去重仍需以 WTD + LEDGER 合并基线为匹配对象，或建立 LEDGER 的机器可读镜像（本轮未新建权威账本，不得冒充已完成）。
3. **断言结构局限仍在**：脚本断言“真实 ID 不变”部分由 TEST- 前缀结构保证；“真实账本哈希不变”只证明沙箱未改真实文件，不证明真实入池路径幂等；这两点须由盲测与真实入池复核补证。
4. **隐私历史局限**：公开邮箱已从当前头文件中删除，但 PR 历史提交中仍可能含有旧邮箱；仅改当前头不能宣称历史已清理，完整历史处理（如需）须另行决定，不在本轮擅自重写历史。
5. **统计口径顺带修复**：WTD-PILOT §四已标注 R2 快照（34 源/21 源）与 R3 当前（35 源/22 源）不得混用，并说明采集执行时间与被观察内容发表截止时间不同；演示周报见 `reports/DEMO-WEEKLY-2026-10-19-24.md`。

## 五、本轮其他交付

- 演示周报：`reports/DEMO-WEEKLY-2026-10-19-24.md`，6 章齐全，顶部显著标注覆盖不足，仅作 TEST_PLAN S2 验收测试文件，不发邮件，不作为正式周报发送；其通过不等于 S2 通过。
- 盲测协议：`reviews/S2-BLIND-TEST-PROTOCOL-2026-10-24.md`，不含答案，新会话决策前禁读 S2 验收记录，失败标准明确。
- TEST_PLAN 更新：S2 第一项（新会话识别 ≥3 条旧事件）仍未勾选（盲测待做）；第二项（六章节覆盖不足演示周报）已完成并勾选，但 S2 整体仍未通过。
