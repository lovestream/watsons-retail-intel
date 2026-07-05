# XCrawl Enrich Articles

## Purpose
Enrich newly discovered article URLs (from `source_url_monitor`) by fetching their full text content via XCrawl's scrape API. This turns bare URL+title stubs into full articles with body content, ready for the filter pipeline.

## When to Use
- After `source_url_monitor` has produced `newly_discovered_urls.json`
- Before passing articles to the filter stage
- When you need full article text for keyword matching and relevance scoring

## Architecture
```
data/raw/{date}/newly_discovered_urls.json   (input: URL stubs)
    ↓
xcrawl_enrich_articles.py
    ↓ XCrawl scrape API (7 keys round-robin, 1 credit per URL)
    ↓
data/raw/{date}/xcrawl_enriched_articles.json (output: enriched articles)
data/logs/{date}/xcrawl_enrich_articles.log
```

## Input Format
Each entry in `newly_discovered_urls.json`:
```json
{
  "article_id": "N20260502_0001",
  "title": "Article Title",
  "url": "https://...",
  "source_name": "web_36kr_newsflashes",
  "source_type": "web_monitor",
  "source_tier": 1,
  "collector": "source_url_monitor",
  "published_at": null,
  "discovered_at": "2026-05-02T20:38:00+08:00",
  "first_seen_at": "2026-05-02T20:38:00+08:00",
  "time_status": "unknown_time",
  "freshness_status": "newly_discovered",
  "summary": "",
  "content": "",
  "matched_keywords": [],
  "role": "primary_timeline",
  "category": "",
  "raw": { ... }
}
```

## Output Format
Output is compatible with `raw_articles.json` — each article has the same structure as search-collected articles:
```json
{
  "article_id": "N20260502_0001",
  "title": "Article Title",
  "url": "https://...",
  "content": "Full article text...",
  "summary": "AI summary or first 200 chars of content...",
  "published_at": "2026-05-01T10:30:00+08:00",
  "source_name": "web_36kr_newsflashes",
  "source_type": "web_monitor",
  "source_tier": 1,
  "collector": "xcrawl_enrich",
  "time_status": "in_window",
  "collected_at": "2026-05-02T21:00:00+08:00",
  "freshness_status": "newly_discovered",
  "keywords_matched": ["即时零售", "美团"],
  "tier": 1,
  "role": "primary_timeline",
  "category": "综合快讯",
  "raw_collector": "xcrawl_enrich",
  "raw": { ... }
}
```

## Key Rules
1. **7 XCrawl keys round-robin** — each key has ~1000 credits/month, 1 credit per scrape
2. **Each URL fails independently** — one failure doesn't stop the batch
3. **Output compatible with raw_articles.json** — can be merged downstream
4. **Preserve `freshness_status=newly_discovered`** — web_monitor articles stay tagged
5. **No content? Keep title+URL** — never discard just because scrape failed
6. **Rate limiting** — 1.0-2.0 second delay between requests per key
7. **Budget awareness** — track credits used per key, stop if exhausted
8. **Content cleaning** — strip excessive whitespace, truncate at 20000 chars
9. **XCrawl SDK returns dict** — not object, access via `resp["data"]["markdown"]` etc.
10. **Must specify OutputConfig formats=["markdown","html"]** — without this XCrawl only returns metadata

## Test Results (2026-05-02)
- **Input**: 125 newly_discovered URLs from source_url_monitor
- **Filtered**: 124 valid (1 skipped: short title <4 chars)
- **Test batch**: 10 articles scraped
- **Success**: 10/10, 0 failures
- **Content length**: 6K-20K chars per article
- **Key usage**: 7 keys, each used 1-2 times, round-robin working correctly
- **Key finding**: "即时零售争夺战" article successfully enriched with full text

## Usage
```bash
python xcrawl_enrich_articles.py \
    --project-root /app/working/projects/watsons-retail-intel \
    --date 2026-05-02 \
    --max-articles 50 \
    --verbose
```