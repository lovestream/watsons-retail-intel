# 12｜QwenPaw 执行手册：先清理 Muse，再逐阶段优化现有 NAS Docker 系统

> 2026-10-25。执行者：**现有 NAS Docker 中的 QwenPaw + DeepSeek Flash**；总体设计与阶段验收由 ChatGPT 负责。**禁止交给 Codex，不迁移NAS，不重建QwenPaw容器。**
> 设计权威：[11｜架构决策、AIHOT、人民币价格](11_20261025_nas_qwenpaw_aihot_architecture_decision.md)；保留参考 docs/01–09、config/*.yaml、AUDIT_20260811_collection.md；旧 Muse 仅作退役对象。
> 本文是**命令与任务书**，不是已执行结果。未经单独授权不得执行正式邮箱投递、停服、删除生产数据、重建容器、替换cron。

## 总控规则：严格逐阶段执行，单步交付，ChatGPT复审后才推进

- 第一批只做 M0 Muse 退役与GitHub文档清理，不碰原NAS Python、QwenPaw cron、报告发送、事件账本和数据卷。
- 后续 M1 只读NAS审计 → M2 AIHOT对照/样本冻结 → M3信源采集与来源健康 → M4事件库/质量门 → M5模型路由价格/质量 → M6周报音频/投递 → M7影子对照与正式切换。每阶段应输出**保留、修改、删除**清单、测试命令、前后指标、文件diff、回滚方法、风险和最新commit SHA。
- 在 NAS 上实测前，绝不能声称“当前Cron已停”“官方API能通”“费用测过”“运行已完成”“历史数据已备份”。
- QwenPaw 每次必须先核对工作目录、Git分支、文件有无未提交改动，代码和日志只推**脱敏**内容到 GitHub；机密Key、Cookie、邮箱、完整内部报告、网页版权全文不推 public 仓库。
- 不在Docker容器的临时层保存唯一备份；必须查明实际持久化 mount/volume 后把备份放在可靠的NAS宿主目录或者已挂载的持久卷。

---

## M0｜清理旧 Muse 项目（第一步，必须先做）

### M0-0 范围与关系：清理对象不是生产 QwenPaw

GitHub 项目：lovestream/watsons-retail-intel。main 分支**已存在 muse/**（含 Muse 目标任务书），另有单独 **未合并 Draft PR #1**，分支 muse/pilot-2026-10-24，最后审查 HEAD 8418f299059122e523ef35848027677004ba7d9f。两部分**不是同一回事**。

预期需要处理的**现役**旧Muse内容：main 的 muse/ 目录、docs/10_muse_web_independent_architecture.md、主 README 与 docs/README 中把Muse标为优先路线的引用，以及 docs/09 开头原“Muse混合联动已废”叙述和其他被 git grep 查到的现役引用。**docs/09含 NAS API/可靠性有用内容，不可整篇删除**。docs/11、12中的“Muse已退役”历史说明与本任务指令属于审计，不是仍在启用Muse。

不操作：skills/、scripts/、config/、data/、reports/、podcasts/、QwenPaw配置、Docker容器、NAS卷、日/周/月/年cron、现有邮件/音频任务。Muse端已暂停的两个任务继续暂停；**QwenPaw现有调度任务不得因为清Muse被误停**。

### M0-1 只读预检（直接在 NAS 中运行，复制执行）

~~~bash
cd /app/working/projects/watsons-retail-intel || exit 1
printf '\n== LOCATION ==\n'
pwd
git rev-parse --show-toplevel
git remote -v
git branch --show-current
git status --short
printf '\n== MUSE TRACKED ON CURRENT BRANCH ==\n'
git ls-files muse docs/10_muse_web_independent_architecture.md
printf '\n== ALL MUSE REFERENCES OUTSIDE muse/ ==\n'
git grep -n -i -e muse -- . ':!muse/**' || true
printf '\n== RUNNING-DATA ROOT AND MOUNTS (READ-ONLY) ==\n'
ls -ld data reports config scripts muse 2>/dev/null || true
df -h .
~~~

**暂停条件**：实际目录与上面路径不一致、不是预期Git仓库、当前工作树含未提交改动、当前分支不是main、无法明确备份卷位置，**先报告，不要运行任何清理**。Docker内部路径不等于NAS宿主路径，不能猜测宿主 /volume1 目录，也不要对任何未知路径执行 rm -rf。

### M0-2 备份并核对（先由 QwenPaw 找到已挂载的真实持久化卷）

以下命令中的 **BACKUP_ROOT 必须替换为实际存在、可写、长期保留且位于Git工作树外的NAS持久化目录**。若无法确定，停止并报告，不允许用 /tmp 或容器临时层替代。

~~~bash
cd /app/working/projects/watsons-retail-intel || exit 1
# 先由QwenPaw现场核对NAS volume映射，再填这里：
BACKUP_ROOT=/REPLACE_WITH_VERIFIED_PERSISTENT_NAS_MOUNT
test -d "$BACKUP_ROOT" && test -w "$BACKUP_ROOT" || {
  echo "STOP: backup directory is not a verified writable persistent mount"
  exit 1
}
test -z "$(git status --porcelain)" || { echo "STOP: working tree dirty"; exit 1; }
test "$(git branch --show-current)" = "main" || { echo "STOP: not main"; exit 1; }
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR="$BACKUP_ROOT/muse-retired-$STAMP"
mkdir -p "$BACKUP_DIR"
git rev-parse HEAD > "$BACKUP_DIR/main-before.txt"
git archive --format=tar HEAD muse docs/10_muse_web_independent_architecture.md |
  gzip > "$BACKUP_DIR/muse-main-history.tar.gz"
gzip -t "$BACKUP_DIR/muse-main-history.tar.gz" || exit 1
tar -tzf "$BACKUP_DIR/muse-main-history.tar.gz" | head -30
# 若能读取独立试验分支，再单独保存未合并PR的完整Git对象：
git fetch origin muse/pilot-2026-10-24 || {
  echo "STOP: cannot fetch Muse PR branch; preserve remote branch and report"
  exit 1
}
git rev-parse FETCH_HEAD > "$BACKUP_DIR/muse-pr-head.txt"
git bundle create "$BACKUP_DIR/muse-pr.bundle" FETCH_HEAD ||
  { echo "STOP: Muse PR backup bundle failed"; exit 1; }
git bundle verify "$BACKUP_DIR/muse-pr.bundle" || exit 1
echo "BACKUP VERIFIED $BACKUP_DIR"
~~~

注意：上面脚本如果 Git 对 FETCH_HEAD bundle 不支持，则 **不得继续删分支**，应由 QwenPaw 改为对已获取的远端跟踪引用创建 bundle 并先验证。原PR在GitHub上也留有历史，但不应把它当作唯一备份。保存备份目录、校验结果和分支HEAD到**不含机密**的变更记录中。

### M0-3 先检查清单，再删除源代码仓库的 Muse 现役文件

只在**完成备份、确认主分支无本地改动**后运行。此命令只移除 Git 所跟踪的 Muse 项目目录及旧独立架构文档，**不会删除**NAS的QwenPaw服务、Python代码或数据目录；同时不要手工执行 rm -rf。

~~~bash
cd /app/working/projects/watsons-retail-intel || exit 1
git switch main || exit 1
git pull --ff-only origin main || exit 1
test -z "$(git status --porcelain)" || { echo "STOP: dirty tree"; exit 1; }
git ls-files muse docs/10_muse_web_independent_architecture.md
# 经QwenPaw逐项确认所有待删文件均属于退役Muse后：
git rm -r -- muse
git rm -- docs/10_muse_web_independent_architecture.md
# 以下为检查，不是自动全局替换；必须逐处辨认并改正：
git grep -n -i -e muse -- . || true
git diff --cached --stat
git diff --cached --name-status
~~~

QwenPaw必须**人工按真实内容修订**根README、docs/README、docs/09等的过期Muse“最新路线”引用，并将 docs/11 与 docs/12 标为当前实施方案。不要机械地把所有历史审计中的单词“Muse”替换为空；历史可保留“已退役，见11/12”，但不能作为仍在执行的计划。

验收必须确认：
1. main中 **git ls-files muse** 为0（在删除暂存后，并且提交后远端回读也为0）；
2. docs/10已删除，主README/docs索引不再让QwenPaw继续读取Muse任务书；
3. **git diff --cached 仅涉及 Muse 与文档清理**；如果出现 skills/ scripts/ config/ data/ 或Docker部署文件，立刻停止；
4. 本地正式事件账本、模型Key、任务配置和邮件发送记录未触碰；
5. 只创建一个清理里程碑提交，保留备份地点和commit SHA。

~~~bash
# QwenPaw完成README/索引清理并复核diff后再执行：
git status --short
git diff --cached --check
# 审查无误后，由QwenPaw提交；如果未配置Git认证，停止并报告，不尝试绕过
git commit -m "chore: retire Muse experiment and point docs to NAS QwenPaw plan"
git push origin main
git rev-parse HEAD
~~~

**提示**：上方样例在操作 README 等文件后，要先显式 git add 相关README/文档，确保提交包含修改。Git push 权限失败则保留本地commit，不能说已推送。

### M0-4 Draft PR #1 单独处理（清理main不等于处理PR）

- PR地址：https://github.com/lovestream/watsons-retail-intel/pull/1 。仍为Draft，历史HEAD为8418f299...，**不可合并**。
- **在已保存分支备份且备份可验证后**：将PR添加“实验结束、转NAS/QwenPaw现有生产系统”的评论并 **Close without merge**；之后才考虑删除远端 muse/pilot-2026-10-24 分支。
- 若NAS内有**已认证**的GitHub CLI，可运行下面的命令（会实际关闭PR，必须确认备份与权限）；没有就经GitHub网页手工关闭，**不要要求提供管理员令牌给聊天**：

~~~bash
# 仅在已验证GitHub CLI授权且已备份、希望关闭时执行：
gh pr close 1 -R lovestream/watsons-retail-intel \
  --comment "Muse standalone experiment retired. Production plan: NAS Docker + QwenPaw/Python + DeepSeek Flash; see docs/11 and docs/12. Never merged."
# 只有在用户明确确认不再保留远端实验分支、且备份验证通过后才执行：
# git push origin --delete muse/pilot-2026-10-24
~~~

PR关闭后历史评论仍可审计。若分支备份没成功，就**先关闭PR但保留远端分支**，直到备份核验成功。**不得自行启用/删除 Muse 网页后台任务**；用户端已暂停，若需彻底删除由用户在Muse账号中操作，QwenPaw无权替代。

**M0完成后立即停下，给ChatGPT回报：备份路径（不含密钥）、main提交/PR状态、删除文件列表、README索引修订、旧QwenPaw任务未改动的实测证据。未经复审不进入M1。**

---

## M1｜NAS 实际状态只读审计（不改代码）

将下面整段作为阶段任务交给 QwenPaw：

> 在同一 NAS Docker 现有环境中做**只读**事实盘点：Docker容器/持久化卷/时区、仓库真实HEAD和dirty状态、QwenPaw agent型cron及容器/宿主cron真实启用状态、过去14天日采集成功窗口、周/月/年报成功与邮箱投递去重、来源质量漏斗、P0/P1/P2数、source健康与错误、API模型路由及调用成本。重点读取 scripts/run_daily.sh、run_periodic.sh、skills/run_daily_pipeline、skills/run_periodic_pipeline、skills/utils/llm_client.py、config/model_router.yaml、scoring.yaml、source配置；所有检查不得发送邮件、跑定时、停止容器或在public GitHub泄露Key/Cookie/内部数据。写 docs/reviews/NAS-M1-READONLY-AUDIT.md：运行代码与文档差异、主风险P0/P1/P2、建议保留/改造/删除清单，附脱敏证据与可回滚范围。推送后停止。

**门禁**：必须有NAS运行事实，不能仅引用 docs/01–09 的历史审计；运行成功率必须给分母；来源贡献、模型/缓存tokens按实际日志，缺失就写unavailable。

## M2｜AIHOT机制对标 + 冻结基准（仍不改生产）

> 基于AIHOT https://github.com/KKKKhazix/AIHOT 的来源健康/动态频率/PASS-UNKNOWN-BLOCK/双评分/事件聚簇/SelectBench，逐项比对现有Python实现，输出 KEEP/REFINE/REMOVE/DEFER。冻结至少30条人工确认的历史样本起步（覆盖高价值、纯B2C误报、跨年旧闻、同事件重发、同一事项进展、官方规则发布日期≠生效日）；**不必为了数字强造P1正例**，如无真实P1则标unmeasured，同时收集可追溯真值表。评测旧管线的发现、文章抽取、去重与评分，记录召回/误报/错误归组，而不是仅统计标题分类。建立只读测试，不动正式账本。输出 docs/reviews/NAS-M2-AIHOT-GAP-AND-BASELINE.md 并停止。

## M3｜优先解决真实采集漏报

> 只针对M1/M2证据确认的高价值且失败源做改动。为平台和垂媒实现“可巡检官方列表→单篇URL→绝对发布时间→正文校验→候选事件”的实际路径，不能将搜索结果摘要冒充全文。按独有高价值事件贡献、源故障率/发布时间及时性设频率；search_only明确受限。对比两个相同闭合窗口的现有采集回放，衡量新发现、误报、数据源成本；失败需告警，不能以0篇作为已覆盖成功。原URL/正文缓存和source ledger不得破坏。输出 M3验收记录+单阶段commit，停止。

## M4｜事件化/去重/分级与本地唯一账本

> 复用现有事件字段/账本，只修M2证明的问题。先建立当前生产账本的备份和schema迁移演练，在**沙箱镜像**验证：URL→事实→跨源事件→后续进展；published/event/announcement/effective/first_seen分开；同事件vs同故事；无效旧ID和superseded不得复活；新增后重跑保持同一event_id；同一窗口重算不能重复真实ID；分数按config/scoring.yaml程序复算、硬降级不可绕过；UNKNOWN与未核全文不得擅升P1。方案优先沿用现有存储，只有现有写入无事务性且评估充分才引入SQLite，不要同时维护两份权威账本。单阶段提交+测试，停止。

## M5｜DeepSeek Flash 成本与模型质量对照（先影子测试）

> 先在 NAS Docker 中只读识别当前OpenCode入口调用链，配置项目独立的 API Provider Adapter（Key用NAS secrets，不入Git）。依次对 RightCode「国模特惠闲时¥0.5输入/¥2输出/¥0.01缓存命中」、百亿补贴¥0.6/¥1.8/¥0.02、DeepSeek官方闲时¥1/¥4/¥0.02用**相同冻结样本**真实探针验证模型版本、JSON格式、输出完整性、耗时、失败率与账单usage；别混淆美元和人民币、不同计量。主通道按质量合格前提优先国模特惠闲时，其他为备用候选而非默认为已可用。设置费用硬上限、重试、429限流、fallback和故障熔断；禁止发送生产邮件。不能仅把deepseek-v4-flash-free字符串替换为deepseek-flash。写 M5报告，停止。

## M6｜周期报告、音频与邮件交付可靠性（先dry run）

> 继续“每日只入池不发日报”；周/月/年分别从唯一事件库读取有完整日期/证据的封闭周期事实，先校验来源覆盖与schema完成信号，再调用Flash生成6/7/8章和5–10分钟中文口播稿，保留事实→机制→影响→动作KPI→反证，不能把背景重复当新事件。复用edge-tts或已验证TTS；HTML/MP3先只写NAS测试目录，检查音频能播放、报告来源可点、没有编造数字。校验 run_periodic.sh 日期参数与退出码、SMTP超时重复发送风险，建立 idempotency key。真实发送要用户另行确认，dry run绝不发送。单阶段提交+报告，停止。

## M7｜独立调度 + 一个自然周影子运行 + 受控切换

> 在保证同一套 NAS Docker 中、只存在一个调度权威的前提下，将程序启动从QwenPaw Agent理解任务中解耦：评估NAS Task Scheduler、宿主cron或容器持久scheduler，三选一，不同时启用。先在隔离输出目录shadow跑至少一个自然周，保持旧正式投递不被新任务覆盖；收集启动率、覆盖率、有效事件数、人工校准误差、token成本、周报质量、重复/失败恢复。若没有经独立确认的重要正例，不得写“召回100%”。测试通过后，提供明确切换窗口、停旧启新步骤和回滚命令，**由用户另行批准才能实施正式Cron切换与邮件投递**。PR不得未经审查直接合并。

---

## 第一次只应交给 QwenPaw 的命令性指令（复制发送）

**先执行 M0，后续一律暂停。**

> 你正在 NAS Docker 内运行，当前系统一直位于NAS；不要提迁移。请从项目GitHub main读取 docs/11_20261025_nas_qwenpaw_aihot_architecture_decision.md 与 docs/12_20261025_qwenpaw_step_by_step_execution.md。严格执行 M0 Muse 退役：先验证Git状态、真实持久卷备份、Muse main文件和未合并PR差异；备份验证过后才删main的muse/和docs/10，修README/docs索引和docs/09的现役Muse错误引导；确认diff不涉及QwenPaw/Python/data/docker/cron；单独关闭未合并的Draft PR但不能merge，远端分支删除需完整备份与明确确认。禁止发送邮件/音频，不改现有NAS生产行为。产出清理报告、主分支提交及回读证据。完成M0立即停止，等ChatGPT复审；不得自行推进M1。