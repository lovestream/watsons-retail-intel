# PR #2 W43 真实来源shadow｜复审与收敛/音频硬门整改（2026-10-31）

**本复审分支**：`review/reader-brief-evidence-gates-20261031`，从 `release/periodic-reader-brief-20261030` 的 `b2da0b6` 拆出；不是生产分支，也未合并到PR #2。**当前结论：NO-GO 正式发送、NO-GO 合并PR #2。** 本次仅GitHub静态审查及代码/测试提交，**未在NAS或本地对新代码实际跑47项/新增测试，也未进行LLM多轮实测或音频试听；任何通过率数字须由NAS执行后产生**。

## 一、已观察到的根因

1. **提示词“硬要求”没有落到程序门**。P08要求每张 `competitor_cards` 显式 `claim_ids`，但原 `schema.validate_brief` 在 `claim_ids` 缺失时跳过ID检查；`supports_claim_ids` 错误关联仅warning；任意合法CLxxx甚至可绑定另一合法事实ID/另一来源，因未要求`CLxxx -> Fxxx -> URL`严格一一对应。修复为无主张、无原始链接、不全覆盖、串绑/错绑均error（不能发送）。
2. **发文/采集/发生时间混淆源于事实包构造**。`facts_pack.build_from_synthesis` 原将cluster `last_seen_at/first_seen_at`当日期，却默认标为`published`；`build_from_events`缺`published_at`时把目录日期也标为published。两者本质都是*observed*。修复为保留各自的 `observed/published/occurred`日期角色；没有真实发生时间的`competitor_cards.occurred_on`须在事实文本中显式有同日证据，否则阻断。
3. **混合状态洗白**。原代码仅在所有引用状态`<= {proposed,reported}`时检查“已上线/已落地”等词；一个completed与一个reported混合即可绕过。修复为只要包含reported/proposed仍整体声称已落地，就阻断，要求拆分事实主张。
4. **审核是必要但不充分的程序门**。P09可识别跨主体因果和逻辑矛盾，但一次复核通过无法证明其他运行不产生重大错判。旧的 P08 deterministic repair 最多1次，P09 major revision最多2轮；正确处置是停止/留人工审阅，不是无限重试直到approve。模型温度小≠确定性。
5. **旁路MP3不代表正文一致**。原 `stage_reader_brief --audio-file`只做路径/哈希，无证据证明音频讲述的是P09修订后的JSON；正式周期邮件也可能仍附“完整长报告”的另一份播客。此版新增 `skills/reader_brief/audio.py`：在P09最终更正稿落盘以后**程序确定性**生成口播稿、合成MP3、保存 `reader_brief_sha256 + podcast_script_sha256 + mp3_sha256`，缺一不得正式送信；staging禁外部旁路音频。正式发送入口还会复核同run路径和哈希。
6. **PR分支与主线漂移**：本轮检查`b2da0b6`时相对`main`为ahead 25 / behind 2；需合入当前main新变更、避免覆盖A热修。SiliconFlow `deepseek-ai/DeepSeek-V4.1-Flash` 和RightCode `deepseek-flash` 是不同端点模型名，按端点分别验证，不能简单用一个统一模型字符串覆盖全部。

## 二、具体代码

- `skills/reader_brief/schema.py`：强制期别一致、CL→F→URL完整来源约束、拟推进/仅报道状态洗白阻断、竞对发生日来源约束。
- `skills/reader_brief/facts_pack.py`：`observed`与`published`、`occurred`分离。
- `skills/reader_brief/audio.py`：基于P09最终JSON程序性提取口播内容，复用项目现有edge-tts及音频时长门；不再调用另一个可能引入新事实的播客编辑LLM。
- `skills/reader_brief/run_brief.py`：新增显式`--generate-audio`；P09失败不产出可发MP3；TTS失败则`hold_for_review`；manifest分别记录`text_review_approved`和`audio_delivery_ready`。
- `skills/reader_brief/production_delivery.py`：正式桥接始终生成P09最终稿音频，校验三段SHA、真实P09 gate、文件路径均属于本次执行。
- `skills/generate_periodic_report/generate_periodic_report.py`：正式邮件只附经过P09最终稿生成的MP3，不再附长报告旧MP3。
- `skills/send_daily_report_email/send_daily_report_email.py`：正式SMTP再次检查P09批准、最终稿SHA、口播稿SHA和MP3 SHA；dry-run允许无音频仅做HTML预览，但不视为交付通过。
- `scripts/stage_reader_brief.py`：禁止`--audio-file`旁路；自动同步生成MP3。
- `tests/reader_brief/test_reader_brief.py`：新增7个事实链与日期反例。
- `tests/test_production_delivery_gate.py`：新增侧载MP3拒发、最终稿音频来源单测。
- `scripts/measure_reader_brief_stability.py`：冻结同一真实fact pack，最多20轮不发送SMTP、不生成音频的P05/P08/P09文本稳定性实验。保存每轮原始工件、通过率、问题分布、模型和内容变体；**这是可复现实验工具，不是已经运行的实验结果**。

## 三、NAS隔离验收顺序（不可跳过）

1. 在独立worktree检出本复审分支，保存隔离来源包、分支SHA及运行环境。对单日真实事件样本检查`occurred_at/published_at/observed_at`的来源；对跨主体类比、混合状态、合法ID错绑、竞对错日期、P09-major未修复等编造负例跑单测。注意本轮新增strict gate可能令旧工件从pass变为blocked，**正确行为是修复真实引用而非放宽门槛**。
2. 运行原47项、新增7项事实门与新增音频/送信单测，`python3 -m compileall`，记录环境、失败点和实际数量，不得直接沿用上轮“47 green”作为此次验证。
3. **真实W43固定输入**：从NAS生产真实事件池生成`canonical_weekly_pack.json`，冻结文件SHA后，在不发SMTP的隔离副本运行：
   ```bash
   PYTHONPATH=".venv_packages:." python3 tests/reader_brief/run_tests.py
   PYTHONPATH=".venv_packages:." python3 tests/test_production_delivery_gate.py
   PYTHONPATH=".venv_packages:." python3 -m compileall -q skills scripts
   PYTHONPATH=".venv_packages:." python3 scripts/measure_reader_brief_stability.py \
     --pack-in data/shadow/w43/canonical_weekly_pack.json \
     --start 2026-10-19 --end 2026-10-25 --runs 10 \
     --outdir data/shadow/w43/stability
   ```
   候选最低条件：**至少10次完全独立运行，文本完整P05/P08/P09门通过≥9次；任何一轮被判approved却含已知日期/状态/主体因果错误均为0容忍**。这不是自动“10次平均正确”或“多数投票”放行。正式合并前建议扩展到20轮，至少19轮可审通过，且失败应fail-closed。
4. 单独执行真实完整W43 `scripts/stage_reader_brief.py --root . --period-type weekly --start 2026-10-19 --end 2026-10-25`：输出 `reader_brief.json + brief.html + podcast-script.txt + podcast-audio.mp3 + manifest`，确保文本/P09/HTML/脚本/MP3属于同一run和同一brief SHA；故意删除或替换任一文件应拒发。**不进入SMTP**。
5. 真人试听MP3：逐一核对品牌读音、时间金额单位、音量/停顿、长句是否自然，结合Gmail/Outlook客户端的HTML与附件实际渲染；移动浏览器`scrollWidth=clientWidth`不能代替邮箱客户端。
6. 保持`PR #2 Draft`、`WATSONS_PERIODIC_MAIL_MODE=hold`、不合并、不动Cron/真实账本。RightCode按真实密钥与端点测试单次 fallback 模型名`deepseek-flash`，不允许误用SiliconFlow的模型名。

## 四、尚有的独立风险（本轮代码未宣称修复）

- 原始`infer_status`仍是关键词启发式：含“已上线”的媒体稿可能误判`completed`。需要从来源证据/原始状态字段建立严格事件发生状态与文章报道状态两个维度，不能仅靠LLM语气判断。这可能继续造成少数“已落地”误标。
- 固定取前80条聚类可能偏离真实业务优先度；检查P0/P1、即时零售、个护美妆、B2C和ToB来源是否按真实评分和覆盖度保留，不能把多来源等同同一事件。
- 当前TTS属于edge-tts在线免费服务，可能有网络/接口时效性风险；有音频SHA不等于“发音准确”，仍需真实试听。
- 仍无真实Gmail/Outlook客户端截图，也没有在此独立容器运行测试的证明。若多轮事实审核不收敛，默认`hold`，不允许“重试直到approve”。
