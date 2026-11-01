#!/usr/bin/env python3
"""Offline preview: AIHOT raw articles -> Watsons scope, without LLM or production writes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from skills.filter_relevant_articles.business_scope import decide


def preview(data):
    if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
        raise ValueError("AIHOT shadow input must contain an articles list")
    unique = {}
    for item in data["articles"]:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("article_id") or "")
        if not aid:
            continue
        # Stable AIHOT article id; a new revision replaces the old representation.
        old = unique.get(aid)
        rev = int((item.get("raw") or {}).get("aihot_revision") or 1)
        old_rev = int((old or {}).get("raw", {}).get("aihot_revision") or 0)
        if old is None or rev > old_rev:
            unique[aid] = item
    results = []
    for aid, article in sorted(unique.items()):
        scope = decide(article)
        results.append({
            "article_id": aid, "source_id": (article.get("raw") or {}).get("aihot_source_id"),
            "title": article.get("title"), "scope_decision": scope["decision"],
            "scope_reason": scope["reason"],
            "original_body_available": bool(article.get("content")),
            "published_at": article.get("published_at") or "",
            "source_url": article.get("url") or "",
        })
    return {
        "schema": "watsons-aihot-scope-preview-v1",
        "raw_articles": len(data["articles"]),
        "stable_articles": len(unique),
        "counts": dict(Counter(x["scope_decision"] for x in results)),
        "llm_calls": 0,
        "records": results,
        "note": "This is only the deterministic article-level scope gate, not full event extraction or report acceptance.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_json", help="Bridge output raw_articles_all.json (shadow)")
    p.add_argument("--output", help="Optional shadow preview JSON file")
    a = p.parse_args()
    data = json.loads(Path(a.input_json).read_text(encoding="utf-8"))
    result = preview(data)
    if a.output:
        target = Path(a.output).resolve()
        if not any("shadow" in t for t in target.parts):
            p.error("output must be an isolated shadow path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
