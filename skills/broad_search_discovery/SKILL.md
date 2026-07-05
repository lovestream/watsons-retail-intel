# broad_search_discovery — 广泛搜索发现技能

## 功能
通过多搜索源（Tavily、XCrawl）、多站点、多关键词矩阵，广泛发现与即时零售、个护美妆、屈臣氏电商经营相关的新链接。

该模块**只负责发现 URL**，不直接进入 cleaned 池。

## 输入
- `config/source_packs.yaml` — 搜索矩阵、站点域名、预算控制
- `config/search_policy.yaml` — Tavily 搜索参数、Key 配置
- 当日已有采集数据（用于去重）

## 输出
- `data/raw/{date}/broad_search_urls.json` — 发现的新 URL 列表
- `data/logs/{date}/broad_search_discovery.log` — 运行日志

## 用法
```bash
python skills/broad_search_discovery/broad_search_discovery.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-05-02
```

## 后续
`broad_search_urls.json` 不直接进入 filter，必须先交给 `xcrawl_enrich_articles` 抓取正文，获取内容后再合并到 `raw_articles.json` 或单独走 filter 流程。

## 搜索源
| 源 | 状态 | 说明 |
|----|------|------|
| tavily | ✅ 可用 | 两级搜索（day→week），排除噪音域名 |
| xcrawl_search | ✅ 可用 | 搜索 API，支持中文 |
| baidu_search | ❌ 预留 | 暂无 API |
| bing_search | ❌ 预留 | 暂无 API |
| sogou_search | ❌ 预留 | 暂无 API |
| so360_search | ❌ 预留 | 暂无 API |
| site_search | ✅ 可用 | 通过 tavily/xcrawl 实现 site: 查询 |

## 搜索矩阵维度
- **platforms**: 美团闪购、京东到家、京东秒送、淘宝闪购、饿了么、抖音小时达
- **categories**: 美妆、个护、护肤、彩妆、洗护、防晒、女性护理、日化
- **actions**: 最新、上线、加码、合作、补贴、招商、规则、小时达、即时零售
- **competitors**: 屈臣氏、丝芙兰、万宁、妍丽、WOW COLOUR、调色师、话梅、名创优品

生成两类查询：
1. 泛搜索：平台×品类×动作 的组合
2. 站内搜索：site:domain + 关键词