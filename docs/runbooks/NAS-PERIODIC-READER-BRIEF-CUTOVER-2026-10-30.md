# NAS 周/月/年经营简报生产切换门禁｜2026-10-30

> 状态：**候选版本（待NAS真实环境验收）**。本文件不是上线记录。本次GitHub只准备代码/测试/操作步骤；未执行NAS命令、未接入真实SMTP、未修改生产事件账本/cron/PID1。唯一允许正式发信的环境值是 `WATSONS_PERIODIC_MAIL_MODE=reader_brief`；未设置时默认`hold`，绝不沿用旧Markdown直接发正式周期邮件。

## 0. 为什么此前47项测试≠已上线

`feat/prompt-integration`里W43在隔离的13条受控事实包上测通，但`main`未接入，NAS真实事件数据据2026-10-29交付报告只到2026-09-30。生产默认入口仍用`send_periodic_report_email`里的旧Markdown HTML转码，且`scripts/run_periodic.sh`原来显式`--send-email true`。三个问题互不替代：**生产入库连续性、P09真实批准的邮件进入SMTP、目标邮箱实际呈现与音频验收**。

## 1. 本候选分支的新改动

- `skills/reader_brief/production_delivery.py`：将完整周期报告对应的正式`periodic_synthesis` / P14/P15与原始事件库传给P05→P08→P09，只有`manifest.delivery_state=approved`、HTML SHA一致且音频与manifest匹配时返回`approved=True`。
- `generate_periodic_report.py::_send_report_email`：在`WATSONS_PERIODIC_MAIL_MODE=reader_brief`时调用新bridge；否则`hold`。周期报告无法构建synthesis的旧降级路径不再允许直接发SMTP；发送失败回写`delivery_allowed=False`和`email_delivery.error`。
- `send_periodic_report_email`：独立再次检查reader HTML、manifest、SHA，正式信必须有可播放MP3；禁止旧Markdown直发。
- **P0更正**：`run_brief.py`原先把P09 6处更正写到`reader_brief_after_revise.json`，但仍将原稿`brief`渲染/发送。现在更正后的brief覆盖最终`reader_brief.json`并作为HTML唯一输入。P09第二轮审稿明确拿**修订稿**和对应原始source证据，并且必须返回`approve`、无major/critical，才算通过。
- P14/P15结论显式传给P05/P08；生产`run_periodic_pipeline.py`月初/年初若下级未封账，月报在1～7日、年报在1月1～15日按受控时点补试，并在manifest记录交付结果。
- `scripts/run_periodic.sh`修复原来忽略指定日期及错误退出码被最后一个echo覆盖的缺陷，且默认`hold`。
- `scripts/production_preflight.py`只读检测最近一次分析事件日期、依赖、密钥存在性、代码版本和邮件配置（不打印密钥、不访问SMTP）。
- `scripts/stage_reader_brief.py`可在隔离副本用**真实事件数据**生成新版reader HTML/manifest，**从不发送邮件**。
- `tests/test_production_delivery_gate.py`新增拒发/哈希/状态/无音频/使用已审HTML的单测。

## 2. NAS第一阶段：仅预检与修复数据源（不得启动正式发送）

在QwenPaw容器内运行，先核对实际目录、容器、工作树和是否存在未提交改动；不要直接用`git reset --hard`、`git clean -fd`或覆盖生产分支：

```bash
cd /app/working/projects/watsons-retail-intel
git status --short
git fetch origin release/periodic-reader-brief-20261030
git worktree add /tmp/watsons-reader-brief-stage origin/release/periodic-reader-brief-20261030
cd /tmp/watsons-reader-brief-stage
mkdir -p data/events data/periodic data/runs/periodic reports
# 下列rsync仅复制已有真实材料到隔离副本，不写回生产
rsync -a /app/working/projects/watsons-retail-intel/data/events/ data/events/
rsync -a /app/working/projects/watsons-retail-intel/data/periodic/ data/periodic/
rsync -a /app/working/projects/watsons-retail-intel/data/runs/periodic/ data/runs/periodic/
rsync -a /app/working/projects/watsons-retail-intel/reports/ reports/
export PYTHONPATH=".venv_packages:.:$PYTHONPATH"
export WATSONS_PERIODIC_MAIL_MODE=hold
/app/venv/bin/python3 scripts/production_preflight.py --root . --max-event-lag 3
```

> 以上预检若报告最近`events_analyzed.json`仍为2026-09-30，先恢复真实采集→过滤→抽取→评分→分析链路，再考虑周报。**不得把13条E1演示事实包直接写入生产事件账本**，也不得人为把P0/P1、verified状态写死。

恢复采集先在隔离副本选取2026-10-29做**真正的单日功能验证**（这里仅作单日样例，不暗示该天数据已存在）：

```bash
cd /tmp/watsons-reader-brief-stage
export PYTHONPATH=".venv_packages:.:$PYTHONPATH"
export WATSONS_PERIODIC_MAIL_MODE=hold
/app/venv/bin/python3 -m skills.run_daily_pipeline.run_daily_pipeline \
  --project-root . --date 2026-10-29 --send-email false \
  --end-step analyze --resume false --continue-on-error false
```

核对日志中采集/过滤/抽取/评分/分析**每阶段真实成功、写入事件数与来源证据**，并核实`data/events/2026-10-29/events_analyzed.json`实际落盘，禁止只看进程“已启动”。如果本日仍失败，按LLM端点、源访问、cron/agent和数据格式逐项定位；不得直接全月盲目回填或批量消耗模型调用。单日成功后再制定分日、可断点、去重、成本可控的补齐计划，最先保证最近完整周。

## 3. NAS第二阶段：可复现验收与真实周报shadow

在隔离副本安装或复用项目依赖后，**跑旧47项+本次新增的门禁测试**。本轮ChatGPT未在NAS实际执行，下面是交给NAS执行的命令：

```bash
cd /tmp/watsons-reader-brief-stage
export PYTHONPATH=".venv_packages:.:$PYTHONPATH"
export WATSONS_PERIODIC_MAIL_MODE=hold
/app/venv/bin/python3 tests/test_prompt_integration.py
/app/venv/bin/python3 tests/test_pipeline_r0_r3.py
/app/venv/bin/python3 tests/reader_brief/run_tests.py
/app/venv/bin/python3 tests/test_production_delivery_gate.py
/app/venv/bin/python3 -m compileall -q skills scripts
```

数据、聚合门、LLM主/备路由都通过后，使用**真实、可归属于2026-W43的周期事件池**进行shadow（不发SMTP），而不是用演示`W43_fact_pack.json`冒充生产：

```bash
cd /tmp/watsons-reader-brief-stage
export WATSONS_PERIODIC_MAIL_MODE=hold
/app/venv/bin/python3 scripts/stage_reader_brief.py \
  --root . --period-type weekly --start 2026-10-19 --end 2026-10-25
```

若W43真实事件池仍未完整，这一步应明确阻断，不得以已有fixture替代。对照输出的`reader_brief.json/p09_review.json/manifest.json/brief.html`：P09必须`approved`、修订后正文真正进入HTML、SHA相等；来源链接真实有效；正文无事件ID、价格/交易/时间歧义；没有音频时不会声称附MP3。**对月/年另用P14/P15完整封账样本验证**，缺周/月时不得出正式报告。

## 4. 上线前的硬性门槛

1. **生产真实来源**：最近单日与连续一周均有真实、可追踪的原始来源→事件数据→八维规则评分，不接受把E1脚本固定P1/verified的事件写入正式账本；采集Cron有连续成功证据。
2. **报告链路**：W43等真实周期聚合覆盖合格且P05/P08/P09完成本次独立复核；P09超时/修改未确认时返回hold，不得出SMTP；月报/年报P14/P15逐级封账门通过。
3. **HTML+MP3**：已批准HTML哈希与实际发送正文相同；TTS真实文件存在、可播放、时长正常且脚本SHA对应；Gmail/Outlook至少一端用测试邮箱真实阅读，核对中文、链接、卡片、附件（Chromium 0px溢出不能代替此项）。
4. **发送/幂等**：邮件收件人经核对，先**仅单个测试收件人**走真实SMTP，再核对消息和投递台账；重复同一period不得再次群发；失败不写success。测试发信必须由运行方单独确认。
5. **回退**：切换前完成配置、代码及数据索引备份；保留旧代码和回滚方法；故障立即设置`WATSONS_PERIODIC_MAIL_MODE=hold`，停止周期发信，不删除或覆盖真实数据。

## 5. 正式启用（仅全部门槛有证据通过后）

在备份、隔离预检、生产只读检查、受控测试收信全部通过且**运营方明确批准**之后，方可考虑将此候选版本整合进main并部署到NAS。部署过程要先以`WATSONS_PERIODIC_MAIL_MODE=hold`生效，再核验版本、依赖和真实数据；只有独立批准后才将该环境值设为`reader_brief`。不改Cron的时间表也可以，现有`scripts/run_periodic.sh`已经带硬门与补试；第一封正式邮件前必须核对收件人、报告周期、附MP3和发送幂等台账。

**当前阻断项清单**：无法从GitHub证明NAS最近数据已补齐；无法直接访问NAS部署/检测实际cron；没有真实邮箱客户端验证；没有这次release代码在NAS上的测试记录。本文件与PR不代表已发布上线。
