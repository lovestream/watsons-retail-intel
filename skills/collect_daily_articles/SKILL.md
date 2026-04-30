# collect_daily_articles

即时零售 × 个护美妆经营情报采集 Skill。

## 何时使用

- 每日定时采集昨 07:00 ~ 今 07:00 窗口内的经营情报
- 手动指定日期采集历史数据
- 需要从多源（RSSHub / 原生RSS / 网页 / Tavily）聚合文章

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_root` | str | ✅ | 项目根目录绝对路径 |
| `date` | str | ❌ | YYYY-MM-DD，默认今天；`date=2026-04-26` 时窗口为 `2026-04-25 07:00 ~ 2026-04-26 07:00` |
| `start_time` | str | ❌ | 覆盖窗口开始时间 (ISO 格式) |
| `end_time` | str | ❌ | 覆盖窗口结束时间 (ISO 格式) |
| `rsshub_base` | str | ❌ | RSSHub 基础地址，优先级高于环境变量和配置 |
| `sources_file` | str | ❌ | 源配置文件路径（相对项目根），默认 `config/sources.yaml` |
| `keywords_file` | str | ❌ | 关键词配置文件路径（相对项目根），默认 `config/keywords.yaml` |

## 输出

### 文件

| 文件 | 路径 |
|------|------|
| 采集结果 | `data/raw/<date>/raw_articles.json` |
| 运行日志 | `data/logs/<date>/collect_daily_articles.log` |

### 函数返回值

```json
{
  "ok": true,
  "date": "2026-04-26",
  "output_file": "data/raw/2026-04-26/raw_articles.json",
  "log_file": "data/logs/2026-04-26/collect_daily_articles.log",
  "total_collected": 42,
  "total_saved": 35,
  "by_collector": { "rsshub": 10, "rss": 8, "web": 12, "tavily": 5 },
  "errors": []
}
```

### raw_articles.json 结构

```json
{
  "metadata": {
    "date": "2026-04-26",
    "window_start": "2026-04-25T07:00:00+08:00",
    "window_end": "2026-04-26T07:00:00+08:00",
    "total_collected": 42,
    "total_saved": 35,
    "time_summary": { "in_window": 28, "old": 5, "unknown_time": 2 },
    "by_collector": { ... }
  },
  "source_stats": { ... },
  "articles": [
    {
      "article_id": "a1b2c3d4e5f6",
      "title": "...",
      "url": "...",
      "source_name": "meituan_flash",
      "source_type": "rsshub",
      "source_tier": 2,
      "collector": "rsshub",
      "published_at": "2026-04-25T14:30:00+08:00",
      "collected_at": "2026-04-26T08:15:00+08:00",
      "time_status": "in_window",
      "summary": "...",
      "content": "...",
      "matched_keywords": ["即时零售", "闪购"],
      "raw": { ... }
    }
  ]
}
```

## 采集方式

### 1. RSSHub (`method: rsshub`)
- RSSHub 基础地址优先级：函数参数 > `RSSHUB_BASE_URL` 环境变量 > `config/sources.yaml` 的 `rsshub_base` > 默认 `http://192.168.2.100:1200`
- 读取 sources 中 `routes` 字段拼接基础地址获取 RSS

### 2. 原生 RSS (`method: rss`)
- 读取 source.url 直接解析 RSS/Atom feed

### 3. 网页抓取 (`method: web`)
- 先抓取首页，提取同域名候选文章链接（最多 20 篇）
- 再逐篇抓取正文、标题、发布时间
- 第一版不做深层爬取

### 4. Tavily 搜索 (`method: tavily`)
- 从环境变量读取 API Key：`TAVILY_API_KEY` > `TAVILY_KEY`
- 无 Key 或 API 报错或额度不足时记录日志后跳过，不中断采集
- 读取 sources 中 `search_queries` 字段构造搜索

## 去重规则

1. **URL 去重**：标准化后去重（小写 host、去 fragment、去尾斜杠）
2. **标题去重**：标题非空时做归一化标题去重；标题为空不参与标题去重（避免误伤）
3. 同一 URL 被多个源采到时，保留 `source_tier` 更高的一条，`raw.duplicate_sources` 记录被合并的源

## 关键词匹配

- 从 `config/keywords.yaml` 提取全部关键词
- 在 `title + summary + content` 中匹配
- 无关键词命中时 `matched_keywords` 为空列表，文章仍保留输出（待后续评分过滤）

## 时间窗口

`date = 2026-04-26` → 窗口：
- 开始：`2026-04-25 07:00:00 CST`
- 结束：`2026-04-26 07:00:00 CST`

`time_status` 值：
- `in_window` — published_at 在窗口内
- `old` — published_at 早于窗口
- `unknown_time` — 无法识别发布时间

## 安全约束

- ❌ 不存储任何 API Key / 密码到项目文件
- ❌ 不创建 .env 文件
- ✅ Tavily Key 仅从环境变量读取
- ✅ RSSHub 地址通过参数/环境变量/配置传入

## CLI 用法

```bash
python collect_daily_articles.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26 \
  --rsshub-base http://192.168.2.100:1200

# 详细日志
python collect_daily_articles.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26 \
  --verbose
```

## 函数用法

```python
from collect_daily_articles import collect_daily_articles

result = collect_daily_articles(
    project_root="/app/working/projects/watsons-retail-intel",
    date="2026-04-26",
    rsshub_base="http://192.168.2.100:1200",
)
print(result)
```

## 依赖

```
PyYAML
requests
feedparser
beautifulsoup4
python-dateutil
```