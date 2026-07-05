# Source URL Monitor

## Purpose
Monitor list pages (栏目页、频道页) of configured sources, discover newly published article URLs by comparing against a historical URL ledger. This is a **complement** to search-based collection (XCrawl/Tavily) — it catches new articles that search engines may not yet have indexed.

## When to Use
- As part of the daily collection pipeline, BEFORE search-based collection
- When you need to catch the latest articles from specific sites as soon as they appear
- When RSS feeds are unreliable or incomplete
- When you want to avoid dependency on search engine freshness

## Architecture
```
config/sources.yaml (monitor config)
    ↓
source_url_monitor.py
    ↓ fetch list_urls → extract links → canonicalize → compare seen_urls.jsonl
    ↓
data/raw/{date}/newly_discovered_urls.json   (new URLs found today)
data/source_ledger/seen_urls.jsonl            (cumulative ledger)
data/logs/{date}/source_url_monitor.log

Site Adapters:
  Kr36Parser    — 36kr SSR data extraction (newsflashes + category pages)
  LatepostParser — 晚点LatePost article link extraction
  JiemianParser — 界面新闻 article link extraction
  EbrunParser   — 亿邦动力 link extraction (JS-rendered, limited)
  GenericParser — Fallback: HTML <a> extraction with include/exclude patterns
```

## Anti-Crawl Measures
- Random delays between requests (2-5s between sources, 1.5-3s between list pages)
- Realistic browser headers (Accept, Accept-Language, Sec-Fetch-*)
- Anti-crawl detection: validates response size and checks for captcha/challenge pages
- Retry on blocked pages (up to 2 retries with exponential backoff)
- Session reuse for connection pooling

## Config Format (in sources.yaml)
Add a `monitor` section to any source:

```yaml
- id: web_36kr_newsflashes
  name: 36kr快讯(直连)
  method: web_monitor
  monitor:
    enabled: true
    list_urls:
      - "https://36kr.com/newsflashes"
    link_include_patterns:
      - "/p/"
    link_exclude_patterns:
      - "/tag/"
      - "/user/"
      - "/login"
  category: 即时零售
  role: primary_timeline
  source_type: web_monitor
  tier: tier1_core
  source_tier: 1
  daily_priority: 100
```

## Key Concepts
- **URL Ledger**: `seen_urls.jsonl` tracks every URL ever seen with first-seen timestamp
- **freshness_status**: `newly_discovered` (first time seen) vs `seen_before`
- **URL Canonicalization**: Strip fragments, sort query params, remove tracking params (utm_*, from, spm, etc.), lowercase host, remove www prefix, strip trailing /
- **No published_at guessing**: We only record `discovered_at`, not fake publish dates
- **Site Parsers**: Each site may need a custom parser (SSR extraction, specific URL patterns). If no parser matches, GenericParser uses include/exclude patterns from config.

## Output Format
Each newly discovered URL becomes an article stub:
```json
{
  "article_id": "N20260502_0001",
  "title": "Article Title from link text",
  "url": "https://...",
  "source_name": "web_36kr_newsflashes",
  "source_type": "web_monitor",
  "source_tier": 1,
  "collector": "source_url_monitor",
  "published_at": null,
  "discovered_at": "2026-05-02T07:00:00+08:00",
  "first_seen_at": "2026-05-02T07:00:00+08:00",
  "time_status": "unknown_time",
  "freshness_status": "newly_discovered",
  "summary": "",
  "content": "",
  "matched_keywords": [],
  "role": "primary_timeline",
  "category": "即时零售",
  "raw": {
    "list_page_url": "https://36kr.com/newsflashes",
    "link_text": "Article Title",
    "content_hash": "sha256:..."
  }
}
```

## Rules
1. First time a URL appears → `freshness_status: newly_discovered`
2. Even without `published_at`, do NOT classify as `old` — mark as `unknown_time`
3. New URLs are candidates for downstream content extraction (XCrawl scrape or direct fetch)
4. Seen URLs are never duplicated in today's output
5. URL canonicalization MUST happen before ledger comparison
6. Each source reports: `total_links`, `new_links`, `seen_links`, `failed_links`, `duration`, `errors`

## Current Sources (5 enabled)
1. **web_36kr_newsflashes** — 36kr快讯 + 科技板块 (Kr36Parser, SSR data)
2. **web_36kr_more** — 36kr消费 + 企业服务板块 (Kr36Parser, SSR data)
3. **web_latepost** — 晚点LatePost首页 (LatepostParser)
4. **web_latepost_more** — 晚点LatePost更多栏目 (LatepostParser)
5. **web_jiemian_business** — 界面新闻首页 (JiemianParser)

## Known Limitations
- **虎嗅**: 严重JS渲染+阿里云WAF，无法直接抓取。已从monitor源列表中移除。
- **亿邦动力**: JS反爬（返回空壳重定向），无法直接抓取。已从monitor源列表中移除。
- **36kr**: 可能触发反爬验证码（返回极小HTML+sec_sdk_build脚本）。重试机制可缓解但不能根治。