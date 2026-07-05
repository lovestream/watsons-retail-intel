# merge_raw_articles — 合并原始文章技能

## 功能
将多个采集来源的原始文章合并为统一的 `raw_articles_all.json`，供 filter 步骤使用。

合并输入（按优先级）：
1. `data/raw/{date}/raw_articles.json` — 主采集（RSSHub + RSS + Web Monitor）
2. `data/raw/{date}/source_url_monitor.json` 或 `newly_discovered_urls.json` — Web Monitor 新发现
3. `data/raw/{date}/broad_search_urls.json` — 广泛搜索发现
4. `data/raw/{date}/xcrawl_enriched_articles.json` — XCrawl 抓取正文后的富文章
5. `data/raw/{date}/tavily_gap_articles.json` — Tavily 补搜

合并规则：
- URL 为去重主键（normalized）
- 先到先得：先出现的文章保留，后续相同 URL 的文章丢弃
- 保留每条文章的 `collector` 和 `source_type` 字段，用于来源统计
- 合并后输出 `raw_articles_all.json`，同时输出来源统计

## 输入
- 上述 5 个 JSON 文件（全部可选，缺失则跳过）

## 输出
- `data/raw/{date}/raw_articles_all.json` — 合并后的所有文章
- `data/logs/{date}/merge_raw_articles.log` — 运行日志

## 用法
```bash
python skills/merge_raw_articles/merge_raw_articles.py \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-05-02
```

## 来源统计
日志中输出各采集来源贡献：
- rsshub_count
- rss_count
- web_count
- source_url_monitor_count
- broad_search_count
- xcrawl_enriched_count
- tavily_gap_count
- total_count (去重后)