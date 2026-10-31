# 即时零售 × 个护美妆经营情报系统

> **Watson's Retail Intel** — 面向即时零售（Instant Retail）赛道的个护美妆（Personal Care & Beauty）品类经营情报采集、清洗、评分、分析与推送系统，服务屈臣氏电商经营决策。

## 项目定位

围绕**即时零售 × 个护美妆**采集公开经营情报，经清洗、评分、摘要后按频率生成报告，以 **HTML 邮件 + MP3 播客** 推送（收件人 `123399974@qq.com`），助力品类经营决策。

## 当前架构形态（2026-08 起）

- **日报停发**：`scripts/run_daily.sh` 以 `--end-step analyze --send-email false` 运行，每日只产出**事件池**（供周/月/年报使用），不再生成/发送日报 HTML 与日更播客。
- **周期报告为主**：周报（每周一）/ 月报（每月 1 日）/ 年报（1 月 1 日）由 `watsons-periodic-0900` cron 触发 `scripts/run_periodic.sh` 生成。
- **自动调度**：QwenPaw 平台内有 2 个 agent 型 cron（日报 07:00、周期 09:00，Asia/Shanghai）。
- **内容边界**：仅大陆内容；仅保留即时零售与个护美妆；排除资本市场/财报/IPO/港台海外内容；无关键词命中即拒。

## 目录结构

```
watsons-retail-intel/
├── docs/                     # ★ 详细实现与流程文档（GitHub 维护入口）
│   └── README.md             #   文档索引
├── skills/                   # 管线技能（采集/过滤/事件/报告/播客/邮件…）
├── scripts/                  # cron 启动器（run_daily.sh / run_periodic.sh）
├── config/                   # 全部配置（sources/keywords/scoring/pipeline/…）
├── data/                     # 运行产物（raw/cleaned/events/runs/logs/账本）
├── reports/                  # 周期报告（weekly/monthly/yearly）
├── podcasts/                 # 播客（scripts + audio）
├── tests/                    # 测试
└── README.md / project.yaml
```

## 快速开始

1. 配置 API/密钥环境变量；`config/opencode_keys.json` 存放 OpenCode 入口 Key（建议后续改为环境变量）。
2. 编辑 `config/sources.yaml`（采集源）、`config/keywords.yaml`（关键词）、`config/search_policy.yaml`（搜索词）。
3. 手动跑一天事件池：
   ```bash
   sh scripts/run_daily.sh 2026-10-26
   ```
4. 生成周期报告：
   ```bash
   sh scripts/run_periodic.sh 2026-10-26   # 周一/每月1日/1月1日才生成对应报告
   ```

## 文档

**最新的实现与全流程文档见 [`docs/README.md`](docs/README.md)**，按子系统分 8 篇，全程以真实代码与运行产物为准整理，适合排查问题与制定优化方案。

## 许可

内部使用，请勿外传。
