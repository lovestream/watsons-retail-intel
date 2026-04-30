# 即时零售 × 个护美妆经营情报系统

> **Watson's Retail Intel** — 面向即时零售赛道的个护美妆品类经营情报采集、评分、分析与推送系统。

## 项目简介

本系统围绕 **即时零售（Instant Retail）** 赛道中的 **个护美妆（Personal Care & Beauty）** 品类，自动采集公开经营情报，经清洗、评分、摘要后，按设定频率推送至目标渠道（钉钉群、邮件等），辅助品类经营决策。

## 核心能力

| 能力 | 说明 |
|------|------|
| 🕵️ 情报采集 | 多平台公开数据源定时抓取 |
| 🧹 数据清洗 | 去重、格式化、关键字抽取 |
| 📊 评分排序 | 加权评分 + 阈值筛选，产出高价值情报 |
| 📝 摘要生成 | AI 生成结构化摘要 + 经营建议 |
| 📡 多渠道推送 | 钉钉群 / 邮件 / 其他渠道定向推送 |
| 🎙️ 播客生成 | 周报/月报可自动生成音频播客 |

## 目录结构

```
watsons-retail-intel/
├── README.md                 # 项目说明
├── project.yaml              # 项目全局配置
├── config/                   # 配置文件
│   ├── sources.yaml          # 采集源定义
│   ├── keywords.yaml         # 关键词字典
│   ├── scoring.yaml          # 评分权重与阈值
│   └── runtime.yaml          # 运行时配置（窗口、调度等）
├── skills/                   # Agent 技能定义
├── agents/                   # Agent 配置
├── data/                     # 数据存储
│   ├── raw/                  # 原始采集数据
│   ├── cleaned/              # 清洗后数据
│   ├── rejected/             # 被过滤的数据
│   ├── events/               # 事件型情报
│   ├── drafts/               # 待审核的摘要草稿
│   ├── reviews/              # 已审核的摘要
│   └── logs/                 # 运行日志
├── reports/                  # 定期报告
│   ├── daily/
│   ├── weekly/
│   ├── monthly/
│   └── yearly/
├── podcasts/                 # 播客内容
│   ├── scripts/              # 播客脚本
│   └── audio/                # 生成的音频文件
└── tests/                    # 测试
```

## 配置管理

- **密钥管理**：所有 API Key、密码等敏感信息通过 QwenPaw 环境变量或 Secret 配置管理，不存储于项目文件中。
- **禁止**：项目中不包含任何 `.env` 文件或硬编码密钥。

## 快速开始

1. 确认 QwenPaw 环境变量已配置所需密钥
2. 编辑 `config/sources.yaml` 添加/调整采集源
3. 编辑 `config/keywords.yaml` 调整关键词
4. 运行采集 Agent

## 许可

内部使用，请勿外传。