# 周期报告系统 — 硬性验收标准（§39）

本文档是周报/月报/年报系统的**硬性验收清单**。任何一次发布/交付前必须逐项核对。
来源：2026-08 全流程审计的 19 条漏洞清单 + 三批整改。

## A. 报告内容

| # | 验收项 | 标准 | 实现位置 |
|---|--------|------|----------|
| A1 | 必需章节 | 周报 6 章 / 月报 7 章 / 年报 8 章，缺一章即不可发送 | `phase4_finalize` §9 |
| A2 | 数据覆盖 | coverage status=normal（周≥0.85/月≥0.90/年≥0.95），failing 不发 | `generate_periodic_report` §4.3 |
| A3 | 事件范围 | 事件必须在本周期日期范围内（时间过滤 + 账本重建） | `periodic_synthesis` 时间过滤 |
| A4 | 资本市场内容 | 研报/股价/涨停/评级/目标价/盈利预测 → 不进报告（双重拦截） | score_events + synthesis 过滤 |
| A5 | 港台海外主体 | 香港/台湾/澳门/海外主体事件 → 排除 | score_events `_is_non_mainland_subject_event` |
| A6 | 数字断言 | 报告中的增长/市占/GMV 数字必须能在事件证据中找到；无证据 → HIGH issue | `periodic_editor_review._check_numeric_claims` §16 |
| A7 | 内部标记 | 展示层 0 event_id、0 判断标签、0 思考泄漏、0 "置信度/涉及事件"、0 空占位 | `report_sanitizer` + `rule_validate` |
| A8 | 事实-分析分离 | "事实证据"与"分析提示"不得混用；趋势结论必须有证据支撑 | Phase1 提示词 |
| A9 | 章节去重 | 新旧章质量比较后才覆盖；审稿意见必须被实际处理 | `phase3_revise` §17 |

## B. 生成流程

| # | 验收项 | 标准 | 实现位置 |
|---|--------|------|----------|
| B1 | 聚合层 | synthesis 优先；失败回退历史报告，绝不让管线崩溃 | `generate_periodic_report` Step0 |
| B2 | 主模型重试 | 每章主模型 3 次重试 → 规则兜底章节 → 仍失败=不可发送 | `phase1_generate_chapters` §9.2 |
| B3 | 审稿失败 | Phase2 审稿失败 → sendable=false，绝不静默降级 | `phase2_review` §10 |
| B4 | 修订轮数 | Phase3 最多 2 轮，每轮后重新审稿 | `phase3_revise` |
| B5 | 总超时 | 周 1200s / 月 1800s / 年 2700s 墙钟 deadline，超时中止发送 | §35 |
| B6 | 付费兜底上限 | 周 10 / 月 15 / 年 20 次；达到上限停止付费调用 | `llm_client` + config §20 |
| B7 | 幂等 | 同周期不重复发送（delivery ledger） | `send_daily_report_email` §28 |

## C. 播客

| # | 验收项 | 标准 | 实现位置 |
|---|--------|------|----------|
| C1 | 口播稿长度 | 周 3000-5000 / 月 4500-7500 / 年 7000-12000 字（±10% 校验） | `_get_podcast_spec` §22 |
| C2 | 行动段 | 含"三件事"+ "商家动作"（周期化文案：本周/下月/下一年） | `_generate_podcast_script` §25 |
| C3 | 无 Markdown | 口播稿纯文本，无 **、##、``` 等符号 | `_strip_markdown` |
| C4 | 无日报腔 | 周报/月报不得用"今天/昨天"当时间指代 | `_generate_podcast_script` 后处理 §25 |
| C5 | 音频时长 | MP3 duration 实测（mutagen），不达标自动调语速重试；最后保留兜底 | `_generate_tts` §23/§26 |
| C6 | 分块生成 | 800 字/块，单块失败重试，ffmpeg 拼接 | `_split_tts_chunks` §26 |
| C7 | 质检 | 规则版播客质检（长度/行动段/无标记/无日报腔） | `_review_periodic_podcast` §24 |

## D. 交付

| # | 验收项 | 标准 | 实现位置 |
|---|--------|------|----------|
| D1 | 邮件正文 | 报告内容在 HTML 邮件正文；**不附加 .md 原文** | `send_periodic_report_email` |
| D2 | 附件 | 只附 MP3（音频失败且 require_audio=true → 不发邮件） | §27/§30 |
| D3 | 收件人 | 123399974@qq.com（config 可覆盖） | run_periodic.sh |
| D4 | 冲突顺序 | 同日周报→月报→年报串行 | `run_periodic_pipeline` |
| D5 | 防重入 | periodic.lock，重复触发跳过 | §32 |
| D6 | Manifest | data/runs/periodic/{type}/{period}/manifest.json 记录全状态 | §33 |
| D7 | 日志路径 | manifest.log_paths 可定位当日日志 | §34 |

## E. 运维

| # | 验收项 | 标准 | 实现位置 |
|---|--------|------|----------|
| E1 | 依赖自愈 | pip_ensure 启动检查，缺失安装到 .venv_packages；有 requirements.lock 优先 | §36 |
| E2 | 本地备份 | scripts/backup_snapshot.sh 可随时快照代码（git bundle + tarball） | §37 |
| E3 | 远端备份 | git push 恢复后必须及时推送（当前 SSH key 待恢复） | blocked |
| E4 | 模型单一来源 | 所有模型配置读 model_router.yaml，不硬编码 | §19 |
