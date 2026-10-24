# 只读审计 — 配置体系 / LLM 客户端与回退 / 调度 cron / 数据模型 / 健康检查

> 审计对象：`/app/working/projects/watsons-retail-intel`
> 审计性质：**只读**。未运行 `git push`、未发送邮件、未修改仓库。
> 依据真实代码与配置（`llm_client.py`、`model_router.py`、`parallel_runner.py`、`health_check.py`、`pipeline_alert.py`、`config/*.yaml`、`config/*.json`、`cron_watsons_pipeline.json`、`scripts/run_*.sh`、`data/` 实际目录与账本）。
> 时间基点：2026-10-24（Asia/Shanghai）。

---

## 1. 配置体系总览

`config/` 目录下所有 YAML 均为**相对项目根目录解析**，绝大多数通过 `skills/**` 里的模块用 `yaml.safe_load` 显式加载（各自拥有独立加载函数与缓存，无统一配置注册中心）。健康检查项 `config_files` 也会逐一校验存在性 + YAML 可解析。

| 文件 | 用途 | 关键项 | 实际加载位置 |
|---|---|---|---|
| `model_router.yaml` | 各技能模型选择（默认模型/fallback/参数/优先级路由/ARCHIVE 规则模式） | `default_model=deepseek-v4-flash-free`、`global_fallback=[同模型]`、`routing.<skill>.default/fallback/params/priority_routing` | `skills/utils/model_router.py`（`_load_config`，`_DEFAULT_CONFIG_PATH=config/model_router.yaml`）；`generate_periodic_report.py:63` 注释称其为"模型配置单一来源"；`health_check.py:114` 也在 required_files 中 |
| `parallel.yaml` | 各 skill 并行策略：并发数、单任务超时、容错 | `defaults.max_workers=6`；`collect_daily_articles.source_parallel`（rsshub 6 / rss 4 / web 2 / tavily 6 / xcrawl 6）；`extract_events.article_parallel.max_workers=4`；`analyze_business_impact.event_parallel.priority_batches`（ARCHIVE use_llm=false、P2 workers6、P1 workers4、P0 workers2） | `skills/utils/parallel_runner.py` `load_parallel_config(project_root)`；`analyze_business_impact.py:904` import；parallel.yaml 可覆盖 model_router 的优先级路由（`analyze_business_impact.py:941` 注释） |
| `scoring.yaml` | `score_events` 八维评分权重与分级阈值 | `weights` 八维合计 1.0（watsons_relevance 0.24 最高）；`thresholds.P0=4.3 / P1=3.6 / P2=2.8 / ARCHIVE=0.0`；`hard_downgrade` 硬降级规则（confidence_low_max=P2、source_url_missing→ARCHIVE、fact_empty→ARCHIVE 等）；`push_limits.daily_P0_max=5 / daily_P1_max=20`；`source_tier_map` 来源可信度映射 | `skills/score_events/score_events.py` |
| `pipeline.yaml` | 总控流水线步骤定义（固定顺序）+ 校验阈值 | `steps[]`：collect→filter→source_health_report→tavily_gap_search→filter_merged→extract→score→analyze→generate_report→editor_review→generate_podcast→podcast_review→send_daily_report_email；`no_signal` 分支（cleaned_count=0）；`defaults.use_llm=true`、`send_email=false`；`validation.*` 阈值（collect.min_raw_count=20、extract.min_event_count=1、generate_podcast.min_chinese_chars=1800 等） | `skills/run_daily_pipeline/run_daily_pipeline.py`（pipeline 编排器） |
| `runtime.yaml` | 运行时路径与时区窗口 | `daily_window`（Asia/Shanghai，昨日07:00~今日07:00）；`schedule.*`（cloakbrowser_early `0 5 * * *`、daily_collection `0 7 * * *` 等——**这是历史 cron 定义，实际调度已迁到 agent 框架 jobs.json**）；`data_paths.raw/cleaned/rejected/events/drafts/reviews/logs`；`report_paths.daily/weekly/monthly/yearly`；`podcast_paths.scripts/audio`；`fetch`/`cleaning`/`logging` 参数 | `run_daily_pipeline`、`run_periodic_pipeline`、各 skill 读取 |
| `sources.yaml` | 采集源定义（分平台/垂类/竞对）+ 采集上限 | `collection_limits`（max_items_after_dedup=600 等）；`defaults.allow_old=false / filterout_days=14`；`other_sources[]`（tavily_gap_fill 等，`enabled:false` 的已停用源）；大量 `sources.yaml.backup`/`.bak.*` 版本文件 | `skills/collect_daily_articles/`、`skills/broad_search_discovery/` |
| `keywords.yaml` | 关键词体系（分平台/品类/竞对搜索词） | `platforms.instant_retail`（即时零售/闪购/前置仓 等）；分分类固有关键词 | `collect_daily_articles`、`broad_search_discovery`、`score_events` |
| `search_policy.yaml` | Tavily 搜索预算与两级时间范围、缺口补搜 | `tavily.daily_budget=40`（注释：从 80 削减，CloakBrowser 接管垂媒发现）、`reserve_budget=10`；`keys_env`（TAVILY_API_KEYS 多 key 轮换）；`search_depth=advanced`；`time_range.default=day / fallback=week`；`exclude_domains`（zhihu/weibo 等） | `skills/tavily_gap_search/`、`collect_daily_articles`、`verify_source` |
| `source_packs.yaml` | 广泛搜索发现模块（broad_search）的站点与 query 分层 | `site_domains`（platform_official / vertical_media 等）；A/B/C 类搜索 query 分层 | `skills/broad_search_discovery/broad_search_discovery.py` |
| `email.yaml` | 邮件发送配置 | `from = "Watsons Retail Intel <watsons@watsons-retail.com>"`；`to=[123399974@qq.com]`；密码仅经环境变量 `email_key`（不落盘） | `skills/send_daily_report_email/`、`skills/send_admin_alert/`（mailcli.py） |
| `periodic_report.yaml` | 周期报告（周/月/年）单一配置源（三批整改） | `coverage`（weekly.normal=0.85）、`source_caps`（weekly.max_clusters=80 等）、`quota` 维度最低配额、`clustering`（jaccard 0.65）、`podcast`（口播长度/时长）、`llm.max_paid_fallback_calls`（weekly 10 monthly 15 yearly 20）、`delivery.require_audio=true / idempotent=true`、`email.mailcli_path=/app/working/workspaces/default/skills/email/mailcli.py` | `skills/run_periodic_pipeline/`、`skills/generate_periodic_report/generate_periodic_report.py` |

其他非 YAML 配置：
- `config/opencode_keys.json` — OpenCode 主 Provider 入口密钥（**明文 JSON，3 个自定义 Key，各 67 字符**）。
- `config/cron_daily_0700.json` — 仓库内保存的旧版 0700 cron 完整规格（含邮件+播客全流程），**已与线上 jobs.json 的 minimal prompt 不一致（见 §3 冲突）**。
- `config/cron_backup/` — cron 历史备份：`daily_0700_newspec.json`、`periodic_0900_newspec.json`（minimal 瘦身版）、`daily_0700_20260628.json`、`periodic_0900_20260628.json`、`periodic_refactor_bak/`、`daily_0700_newtext.txt`。
- `cron_watsons_pipeline.json` — 仓库根的旧版每日 pipeline cron（task_type=agent，cron `30 0 * * *`），已被 `jobs.json` 中 `0 7 * * *` 取代。

---

## 2. LLM 客户端与回退（`skills/utils/llm_client.py`、`skills/utils/model_router.py`）

### 2.1 当前 Provider（已切换，非 LongCat/rightcode）

- **主 Provider：OpenCode `deepseek-v4-flash-free`**（`skills/utils/llm_client.py`）。
  - `OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"`
  - `OPENCODE_MODEL = "deepseek-v4-flash-free"`
  - `OPENCODE_KEYS_FILE = "config/opencode_keys.json"`
  - **4 入口轮换**：`_load_opencode_keys()` 从 `config/opencode_keys.json` 读 3 个自定义 Key（`opencode-custom-new-new` / `opencode-custom-new` / `opencode-custom`），末尾**始终附加内置 keyless 入口**（`{"name":"opencode-keyless","key":""}`，请求不带 Authorization）。
  - **Cloudflare 防护**：请求必须携带浏览器 `User-Agent`（`LLM_USER_AGENT`，Chrome/126），否则返回 HTTP 403 `error code 1010`（"banned based on browser signature"）。这是 OpenCode 特有的绕障点，`health_check.test_opencode_key` 同样内置 `_OPENCODE_UA`。
  - Key 载入优先级：env `OPENCODE_API_KEYS`（JSON dict/逗号分隔）> `config/opencode_keys.json` > 空。`OPENCODE_KEYS_FILE` env 可覆盖路径。
- **付费兜底 Provider：rightcode `gpt-5.4-xhigh`**（`FALLBACK_KEY_ENV="rightcode_key"`、`FALLBACK_BASE_URL="https://right.codes/codex/v1"`、`FALLBACK_MODEL="gpt-5.4-xhigh"`）。
  - **默认彻底禁用**：`ALLOW_PAID_FALLBACK_ENV = "ALLOW_PAID_FALLBACK"`，`_load_allow_paid_fallback()` 默认 False。仅当 OpenCode 全灭且明确同意花钱设 `ALLOW_PAID_FALLBACK=1` 才走付费。**绝不悄悄烧钱**。
  - `FORCE_FALLBACK_LLM=1` 可强制跳过免费池直接走付费（仍需 `ALLOW_PAID_FALLBACK=1` + 存在 `rightcode_key` 才生效，否则回退正常路由）。
  - §20 成本限制：`set_max_paid_fallback_calls()` 进程内付费调用上限；成功调用才计数（失败不计费）；`periodic_report.yaml.llm.max_paid_fallback_calls`（weekly 10 / monthly 15 / yearly 20）由周期报告生成器设置。
- **路由顺序（`chat()`）**：先 `_try_pool("primary")`（OpenCode 4 入口 round-robin + 瞬时重试 + 墙钟上限）→ 失败后仅在 `_allow_paid` 时走 `_call_fallback`(rightcode)。
- `model_router.yaml` 的 `default_model`、`global_fallback` 与所有 `routing.<skill>.fallback` **全部是 `deepseek-v4-flash-free`** —— 即配置文件层面的"fallback"与实际 `llm_client` 的付费兜底是**两层不同概念**（见 §2.4 偏移）。

### 2.2 重试参数（真实常量）

`skills/utils/llm_client.py` 顶部常量：

| 常量 | 值 | 语义 |
|---|---|---|
| `TRANSIENT_ROUNDS` | `2` | 瞬时错误（连接中断/超时/空响应/JSON 解析失败）对**每个 Key** 的重试轮数 |
| `TRANSIENT_BACKOFF_BASE` | `1.5`（秒） | 指数退避基数；`_transient_backoff(failure_count)`：`delay = min(1.5 * 2^(n-1), MAX)` |
| `TRANSIENT_BACKOFF_MAX` | `10.0`（秒） | 单次退避硬上限 |
| `MAX_RETRY_WALL_SECONDS` | `300`（5 分钟） | 主 Provider 重试墙钟硬上限；`chat()` 中 `_retry_deadline = monotonic() + MAX_RETRY_WALL_SECONDS`，`_try_pool` 每轮开头检查 `time.monotonic() > deadline` 则 break。防止多 Key 反复 180s 超时+退避累积成数十分钟卡死 |
| `DEFAULT_TIMEOUT` | `180`（秒） | 单请求 timeout（thinking 模型需要更长等待；model_router.yaml 模型的 timeout=240） |
| `DEFAULT_RETRIES` | `2` | 遗留字段（实际重试由 TRANSIENT_ROUNDS+入口数决定） |
| `MAX_ATTEMPTS`（计算值） | `len(entries) * TRANSIENT_ROUNDS + 1` | 即 4 入口 × 2 轮 + 1 = **最多 9 次尝试** |
| `DAILY_TOKEN_LIMIT_PER_KEY` | `5_000_000` | 每 Key 日 token 上限（常量，未在 402 逻辑里动态使用） |

HTTP 错误处理（`_try_pool`）：
- `401/403` → 该入口 `_mark_exhausted`（鉴权失败，本轮不可恢复），`continue`。
- `404` → Endpoint/模型名错误，直接 `return`（**跳过整个池**）。
- `429` → 速率限制，`sleep(2)` 后 `continue`（不标记耗尽）。
- `402` → **额度不足，`_mark_exhausted` 标记该入口耗尽**，`continue`。这是"402 标记耗尽"逻辑：额度用尽的入口在本轮被排除，只留在 keyless 兜底轮换。
- `>=400` 且含 `"unsupported model"` → 跳过整个池 `break`。
- 成功响应：解析 `choices[0].message.content`，若 content 空则回退到 `reasoning_content`；`response_format="json"` 时经 `robust_json_extract` 解析。

### 2.3 单例与安全

- `get_llm_client()` 返回进程级单例（`_llm_client_instance` + 锁）；`reset_llm_client()` 供测试。
- `check_llm_config()` 不输出 Key 原文，只返回 `key_masks`（`_mask_key`：前2后2，如 `ab****cd`）。
- `test_llm_connection()` 发最小 JSON 请求验证 `llm_config_ok / api_reachable / parsed_json_ok`。

### 2.4 偏移说明：「fallback 仍是同一模型 ≠ 独立备用」

关键审计发现：**model_router.yaml 的 `fallback` 层与 llm_client 的付费兜底层语义不一致**。
- `model_router.yaml` 中每个 skill 的 `fallback: [deepseek-v4-flash-free]`、`global_fallback: [deepseek-v4-flash-free]`，与 `default`/`default_model` **完全相同**。这里的 "fallback" 只是一个**非独立备用**（同模型、可能经不同 OpenCode 入口重试），**不具备模型多样性/独立容灾**。
- 真正独立的付费备用（rightcode gpt-5.4-xhigh）**默认关闭**（`ALLOW_PAID_FALLBACK` 未置位），且依赖 `rightcode_key` 环境变量。
- 含义：主 Provider（OpenCode）全部入口失败时，配置层面的 fallback 无法救命——因为它是同一个模型。唯一能兜底的是默认禁用的付费 gpt。因此"LLM 容灾"实际依赖 OpenCode 本身的 4 入口轮换 + 瞬时重试 + keyless 兜底，而非 model_router 的 fallback 列表。

### 2.5 `batch_parallel_map` 的 orig_idx 索引与并发限制（`skills/utils/parallel_runner.py`, L222）

- 签名：`items_by_batch: {batch_name: [(original_index, item), ...]}`、`process_fn(item, orig_idx, batch_name)`、`batch_workers: {name: max_workers}`、`batch_timeouts`。
- **orig_idx 定位规则**：`results` 列表以 `orig_idx` 为下标放置结果。注释与实现特别强调：`orig_idx` 是「完整事件列表」中的索引，可能远大于 `total_items`（当只有部分事件走 LLM 时）。必须按 `_max_idx+1` 分配（`_alloc = max(total_items, _max_idx+1)`），否则会 IndexError 或静默丢结果导致调用方按 orig_idx 取回时错位。`analyze_business_impact.py:1057/1070-1073` 有对应说明。
- **并发限制**：每批 `effective_workers = min(workers, len(batch_items))`；`workers==0` 的批直接跳过（如 ARCHIVE use_llm=false）。analyze_business_impact 的 `priority_batches`：P0 workers2 / P1 workers4 / P2 workers6（都带各自 timeout 60/120/150），`total_timeout=600`。
- `results[i]` 与 `parallel_map` 的 `items[i]` 一一对应；失败项置 `None`，`continue_on_error=True` 时不中断整批。
- checkpoint 工具：`save_checkpoint/load_checkpoint/clear_checkpoint`（JSONL，`{item_id, result, timestamp}`），`clear_checkpoint` 在每次 pipeline run 开始时调用；`load_parallel_config` 读 `config/parallel.yaml`（缺失时用内置默认值）。

---

## 3. 调度 cron（agent 框架 jobs.json）

调度实际存于 agent 框架工作区 `/app/working/workspaces/watsonmainagent/jobs.json`（**不是 OS crontab——本机 `/etc/crontab` 不存在、`crontab` 命令不存在**）。仓库内 `config/cron_*.json`、`config/cron_backup/*.json`、`cron_watsons_pipeline.json` 是规格/备份副本。

### 3.1 当前线上两个 cron（jobs.json）

| 名称 | id | task_type | schedule | timezone | runtime.timeout_seconds | enabled |
|---|---|---|---|---|---|---|
| `watsons-daily-0700` | `a892335a-…` | **agent** | cron `0 7 * * *` | Asia/Shanghai | **120** | true |
| `watsons-periodic-0900` | `7c4eb73d-…` | **agent** | cron `0 9 * * *` | Asia/Shanghai | **120** | true |

两者 `dispatch.type=channel / channel=console / mode=stream`，`save_result_to_inbox=true`，`misfire_grace_seconds=600`，`share_session=true`，`max_concurrency=1`。

- **`watsons-daily-0700` prompt（minimal）**：`执行 Watsons 日报定时任务。只做一件事…后台启动全流程脚本（脚本自身负责自愈装包、跑全流程、发邮件、写日志），然后立即回复一行"已启动 YYYY-MM-DD"即可：cd /app/working/projects/watsons-retail-intel && setsid sh scripts/run_daily.sh > /dev/null 2>&1 < /dev/null &`。要求：启动后立即返回、不要 tail/逐步检查/多行总结，日志在 `data/logs/<date>/_run_daily.out`。
- **`watsons-periodic-0900` prompt（minimal）**：同上模式，后台 `setsid sh scripts/run_periodic.sh > /dev/null 2>&1 < /dev/null &`，日志 `data/logs/<date>/_run_periodic.out`。
- **`watsons-cloakbrowser-0500`：当前 jobs.json 中不存在。** `runtime.yaml.schedule.cloakbrowser_early`（`0 5 * * *` CloakBrowser 早班）是历史形态；实际该 05:00 早班 cron 的 agent 任务已不在线上（或已并入 0700，CloakBrowser 采集已由 main pipeline 的 `cloakbrowser_collect` 步骤承担，见 `data/runs/2026-10-11` 步骤列表）。仓库 `config/cron_backup/` 与 `jobs.json` 中均未发现名为 `watsons-cloakbrowser-0500` 的 agent cron 定义。

### 3.2 minimal prompt 瘦身（cron_backup newspec）

`config/cron_backup/daily_0700_newspec.json` 与 `periodic_0900_newspec.json` 即当前 minimal 形态的来源（对比 `daily_0700_20260628.json`、`periodic_0900_20260628.json` 旧版长 prompt）。瘦身目标（newspec 注释）：**agent 几乎不消耗 token**——launcher 脚本（`run_daily.sh`/`run_periodic.sh`）自包含自愈装包、后台 `setsid` 脱离运行、立即返回一行。对应 git commit `caed332 chore: cron瘦身 - launcher脚本 + minimal prompt, 几乎零token消耗`。

### 3.3 agent 层(OpenCode)失败导致 9/8 起脚本未启动（真实记录）

**数据证据（目录断层）**：
- `data/runs/`：`2026-09-05 → 2026-09-06 → 2026-09-07 →（断层）→ 2026-09-26 → …`，**09-08 至 09-25 无 run 目录**。
- `data/logs/`：同样 `2026-09-07 →（断层）→ 2026-09-26`，无 09-08~09-25 的 `_run_daily.out`。
- 断裂后首个 `data/logs/2026-09-26/_run_daily.out` 头几行：`=== run_daily.sh start 2026-09-25T23:00:08Z date=2026-09-26 ===`，随后 `结束全流程运行: date=2026-09-26 … end_step: analyze` —— 说明恢复后脚本才被真正拉起。

根因机制：0700 的 daily **cron 是 task_type=agent**，即先由 agent（LLM）读取 prompt 再执行 `setsid sh scripts/run_daily.sh`。**若 agent 层的 OpenCode LLM 入口失败，agent 无法产出/执行启动命令，脚本就不会被拉起**——即便底层 `run_daily.sh` 自身健壮。这与"cron 无 shell 类型、只能经 agent 中转"的限制直接相关（OS crontab 不可用，见 §3.4；launcher 脚本设计本意是让 agent 只需一行启动，但启动动作本身仍依赖 agent LLM 可用）。09-08~09-25 的缺口正是该依赖链断掉的体现（OpenCode 入口在 agent 层不可用期）。

### 3.4 限制

- **cron 无 shell 类型**：jobs.json 内任务 `task_type` 仅为 `agent`，无 OS-shell 直接执行型。
- **OS crontab 不可用**：`/etc/crontab` 不存在，`crontab` 命令不存在（`/bin/sh: 1: crontab: not found`），本机没有系统级 crontab 可挂。
- 因此调度完全依赖 agent 框架（QwenPaw jobs 调度器）按时触发 agent，再由 agent 经 LLM 执行 `setsid sh scripts/run_*.sh`。这是一条 **agent LLM → 脚本** 的二段依赖，agent LLM 故障会连带调度"假成功"（触发但脚本没跑）。

---

## 4. 数据模型（data/ 各子目录 + 账本 + run_manifest）

`data/` 顶层实际目录：`articles backups cleaned delivery drafts event_ledger events evergreen locks logs periodic raw rejected reviews runs source_ledger tmp`（另有一批分析结果 json：`category_probe_results.json`、`final_search_results.json`、`site_probe_results*.json`、`search_probe_results.json`、`36kr_variant_results.json` 等）。

| 子目录 | 用途 / schema |
|---|---|
| `data/raw/` | 原始文章（按日期 `2026-05-01/…`）。源头采集原始稿 |
| `data/cleaned/` | 过滤清洗后文章（按日期，如 `2026-10-11`） |
| `data/events/` | 抽取+评分+分析后事件池（按日期 `2026-10-09…11`，含 priority/weighted_score/business_analysis 完整字段） |
| `data/drafts/` | 日报草稿（V1/V2 双稿） |
| `data/reviews/` | 审稿记录 |
| `data/periodic/` | 周期报告聚合输出，`periodic/weekly/2026-W32/…`（periodic_synthesis.json/md） |
| `data/logs/<date>/` | 每日运行日志（`_run_daily.out`、各 step `.log`、`health_check.json`、`pipeline_alert.json`、`generate_podcast.json`、`temporal_stats.json` 等） |
| `data/backups/` | 备份 |
| `data/evergreen/` | 常青事件候选（按日期） |
| `data/locks/` | 运行锁（防并发） |
| `data/tmp/` | 临时文件 |
| `data/event_ledger/events_seen.jsonl` | **事件账本**（6,104 行）。每行：`cluster_key / event_id(E20260503_0129) / event_title / event_type / fact_hash / first_seen_at / last_seen_at / last_reported_at / report_count / date / priority / source_url` |
| `data/source_ledger/seen_urls.jsonl` | **来源账本**（1,409 行）。每行：`canonical_url / first_seen_at(ISO+08:00) / source_name / list_page_url / title`；有 `.bak` 备份 |
| `data/delivery/ledger.jsonl` | **投递账本**（2 行）。每行：`ts / kind(weekly) / subject / to / ok / detail`（记录邮件投递） |
| `data/rejected/` | 被拒文章 |
| `data/runs/<date>/run_manifest.json` | 每日本次运行清单（见下）；另有 `run_summary.md`、`step_status.json` |

**`run_manifest.json` 结构**（`data/runs/2026-10-11/run_manifest.json` 实测）：
- 顶层：`date / started_at / finished_at / duration_seconds / ok / sendable / report_type / no_signal / final_report_file / podcast_ok / podcast_audio / podcast_review_passed / email_sent / steps / errors / podcast_status / email_status`（部分日含 `final_issue_count / final_high_count`）。
- `steps[]`：每步 `{name, status(success/failed/warning/skipped), duration_seconds, errors[], warnings[]}`。
- 最新一日状态：`ok=False sendable=False no_signal=False report_type=normal`；步骤含 `health_check, collect, source_url_monitor, broad_search_discovery(warning), cloakbrowser_collect, cloakbrowser_content_fetch, tophub_collect(failed), xcrawl_enrich_articles, cloakbrowser_enrich, merge_raw_articles, cloakbrowser_date_verify, filter, quality_funnel_report(warning), source_health_report, tavily_gap_search(warning), filter_merged, enrich_cleaned_fulltext, extract, temporal_fact_validation, novelty_check, score, analyze, generate_podcast, podcast_review(warning), send_daily_report_email(skipped)`。
- 关键语义：`sendable`（editor_review 通过才 true）；`ok`（pipeline 整体成功与否，可与 sendable 解耦，如 2026-05-05 实例 `sendable=True ok=False`）；`no_signal`（cleaned_count=0 走无信号日报）；`email_sent`（send_daily_report_email 是否发出）。
- **报告输出路径**：`reports/daily/`（`reports/daily/2026/…`）、`reports/weekly/`、`reports/monthly/`、`reports/yearly/`；播客：`podcasts/scripts/`（口播稿）、`podcasts/audio/`（MP3）。`runtime.yaml.report_paths / podcast_paths` 定义这些相对根路径。

---

## 5. 健康检查与告警（`skills/health_check/`）

### 5.1 health_check 做什么

`health_check.py` `SERVICES` 定义 9 大类检查（`run_health_check()` 遍历）：
1. **opencode_llm**（critical，4 入口含 keyless，逐个 `test_opencode_key` 实测）
2. **tavily_search**（critical，key_count 7，`tavily_key0~6`）
3. **xcrawl**（critical，key_count 4，`key_start=1` 跳过永久耗尽的 key0）
4. **rsshub**（非 critical，HEAD `/1200`）
5. **edge_tts**（critical，实测合成）
6. **python_packages**（critical，auto_fix=True，缺包自动 `pip_ensure`）
7. **config_files**（critical，校验 `config/*.yaml` 存在+可解析；required_files 含 model_router/parallel/runtime/pipeline/sources/keywords/search_policy/source_packs/email/scoring）
8. **directories**（非 critical，存在性+可写+磁盘 ≥100MB）
9. **pipeline_modules**（非 critical，steps 模块可 import）
10. **api_quota**（critical，`quota_tracker` 对比按天比例预期用量）
11. **cloakbrowser**（critical）+ **cloakbrowser_collector**（非 critical）

- **软失败分类** `SOFT_FAILURE_TYPES = {auth_failed, quota_exceeded, rate_limited}`：全部为软失败时不判 down（可能下月恢复）→ degraded；有 key 正常+仅软失败 → healthy。
- 退出码：`overall=="critical" → exit 2`；`degraded → exit 1`；`healthy → exit 0`。
- 结果写入 `data/logs/<date>/health_check.json`。

### 5.2 CRITICAL 误报根因与修复（commit `84be77f`）

**根因**：旧的健康检查在 OpenCode 入口全部不可用时直接判 `down` → `critical` → 触发 CRITICAL 告警，即使付费 GPT 兜底完全正常。业务上"LLM 能力"仍可用，属于误报、刷屏。

**修复（`84be77f`，“健康检查改用 LongCat-2.0-Preview + GPT兜底可用则不报critical”）**——`run_health_check` 中专为 `opencode_llm` 增加分支：
```python
if svc_id == "opencode_llm" and svc_result["status"] in ("down","degraded"):
    fb = test_fallback_llm()
    svc_result["fallback_ok"] = fb["ok"]
    if fb["ok"]:
        svc_result["status"] = "healthy"   # OpenCode 挂但 GPT 兜底正常 → 整体可用
        svc_result["degraded_reason"] = "opencode_down_fallback_ok"
    else:
        svc_result["status"] = "down"      # OpenCode 与 GPT 兜底都挂 → 真严重
```
即：**只要 GPT 兜底正常，LLM 能力即正常，不刷 CRITICAL**；仅当 OpenCode 与 GPT 兜底都失败才判 down。告警生成仅在 `status=="down" and critical` 时发 `[CRITICAL]`，`down/degraded且critical` 发 `[WARNING]`，`down且非critical` 发 `[INFO]`。

### 5.3 `send_admin_alert` 何时触发（`pipeline_alert.py`）

`pipeline_alert.py`（daily pipeline 结束后运行）：
- `collect_pipeline_errors()` 读 `run_manifest.json`：`status=failed/error` → critical；`skipped+warnings` → warnings。**观测性步骤** `{quality_funnel_report, source_health_report}` 失败仅警告；**补充性步骤** `SUPPLEMENTARY_STEPS`（broad_search_discovery、xcrawl_enrich_articles、cloakbrowser_*、source_url_monitor、tophub_collect、generate_podcast、podcast_review）失败降级 warning 不触发邮件。
- `needs_alert = (overall == "critical") or (len(critical_errors)>0) or force`。**degraded/warning 不发邮件**（注释明确：“degraded/warnings are informational only”）。
- 触发后 `send_alert()` → 调用 `python -m skills.send_admin_alert.send_admin_alert --subject "[CRITICAL] Watsons Intel Alert — <date>" --body … --force`。管理员邮箱：`ADMIN_ALERT_EMAIL` 默认 `51111211004@stu.ecnu.edu.cn`。
- 结果存 `data/logs/<date>/pipeline_alert.json`。
- 也就是说 `send_admin_alert` 仅在**整体 critical 或存在关键步骤真失败**时发；单一补充源失败/降级不打扰管理员。

---

## 6. 已知运维问题（实测）

### 6.1 git push 阻塞：30 个未推送、无 SSH

- `git status`：`Your branch is ahead of 'origin/main' by 30 commits`，另有多处未提交修改（`AUDIT_20260811_collection.md`、`data/delivery/ledger.jsonl`、`data/event_ledger/events_seen.jsonl`、`data/source_ledger/seen_urls.jsonl`、`data/periodic/weekly/2026-W32/*` 等）与未跟踪（`data/backups/`、`data/evergreen/2026-07-06~08/`）。
- 30 个本地未推送 commit 从 `d868a23`（含 84be77f 等整改）直到最新。
- **根因**：remote 为 `git@github.com:lovestream/watsons-retail-intel.git`（**SSH**），而 `~/.ssh/` **为空目录（无任何私钥）**，`git config core.sshCommand` 未设置 → SSH push 无凭据必然被拒。即"30 unpushed、no SSH"。
- 影响：本地整改全部滞留本地，未同步到远端；持续累积将加剧分叉风险。

### 6.2 opencode_keys.json 明文密钥风险

- `config/opencode_keys.json` 以 **明文 JSON** 存储 3 个 OpenCode Key（`opencode-custom-new-new` / `opencode-custom-new` / `opencode-custom`，各 67 字符），位于**仓库内且未被 .gitignore 排除**（在 git 状态里可见）。
- 风险：一旦仓库被推送/泄露，3 个可用的 OpenCode 免费入口密钥即暴露，可能被他人消耗额度/滥用。健康检查仅做掩码展示，代码层不输出原文，但**文件本身即明文落盘**是静态风险点。
- 建议（审计建议，未执行）：将 key 迁至环境变量或 secrets 托管，仓库内仅留占位/掩码。

### 6.3 日报停发冲突（config cron 规格 vs 线上 minimal 不一致 + 脚本行为变更）

- **线上 jobs.json** 的 `watsons-daily-0700` 使用 **minimal prompt**，仅 `setsid sh scripts/run_daily.sh` 一行。
- **仓库内 `config/cron_daily_0700.json`** 仍是**旧版完整规格**：明确步骤为 `run_daily_pipeline --start-step collect --end-step editor_review` → `generate_podcast` → 置 `sendable=true` → `send_daily_report_email --attach-markdown true --attach-podcast true --dry-run false`（即"旧时代"的日报+播客+日邮全流程，timeout 4200s）。该文件与线上 minimal 的 minimal 行为**直接冲突**，容易误导阅读者以为每天仍在发日报邮件。
- **更深处**：`scripts/run_daily.sh` 自 2026-08 起已**不再生成日报/播客/日邮**，`--send-email false --end-step analyze`：只采集→清洗→抽取→时效/新颖性→评分→经营分析，产出**完整事件池**供周/月/年报使用（scripts 头注释 + 0907/0926 `_run_daily.out` 实测 `end_step: analyze`、`send_email=False (默认禁用)`）。
- 因此存在三层不一致：(a) 线上 minimal cron 只跑事件池；(b) 仓库 `config/cron_daily_0700.json` 描述的是旧的"日报+邮件"全流程；(c) `pipeline.yaml` 仍把 `send_daily_report_email` 列为默认流水线步骤。三者对"日报是否停发、以什么形态发"的表述互相矛盾。**日报/日邮已于 2026-08 停发，改以事件池支撑周期报告**，但仓库内旧规格文件未同步清理，是文档/配置层面的遗留冲突。

---

## 附：关键文件与常量索引

- `skills/utils/llm_client.py` — `TRANSIENT_ROUNDS=2`、`TRANSIENT_BACKOFF_BASE=1.5`、`TRANSIENT_BACKOFF_MAX=10.0`、`MAX_RETRY_WALL_SECONDS=300`、`OPENCODE_MODEL/OPENCODE_BASE_URL/OPENCODE_KEYS_FILE`、`ALLOW_PAID_FALLBACK_ENV`、402 标记耗尽、4 入口轮换。
- `skills/utils/model_router.py` — `_DEFAULT_CONFIG_PATH=config/model_router.yaml`；fallback 与 default 同模型（非独立备用）。
- `skills/utils/parallel_runner.py` — `batch_parallel_map` orig_idx 分配 `_alloc=max(total_items, _max_idx+1)`；checkpoint JSONL。
- `config/model_router.yaml`、`config/parallel.yaml`、`config/scoring.yaml`、`config/pipeline.yaml`、`config/runtime.yaml`、`config/sources.yaml`、`config/keywords.yaml`、`config/search_policy.yaml`、`config/source_packs.yaml`、`config/email.yaml`、`config/periodic_report.yaml`。
- 调度：`/app/working/workspaces/watsonmainagent/jobs.json`（daily-0700 / periodic-0900，task_type=agent）；`config/cron_backup/*_newspec.json`；`scripts/run_daily.sh`、`scripts/run_periodic.sh`。
- `skills/health_check/health_check.py`、`pipeline_alert.py`、`pip_ensure.py`、`skills/send_admin_alert/send_admin_alert.py`。
- 数据账本：`data/event_ledger/events_seen.jsonl`、`data/source_ledger/seen_urls.jsonl`、`data/delivery/ledger.jsonl`；`data/runs/<date>/run_manifest.json`。

> 本审计仅读取与记录，未执行任何写操作、未运行 git push、未发送邮件、未修改仓库。

