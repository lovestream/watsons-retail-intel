"""Cross-day article duplicate gate. Read-only history; no ledger writes."""
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACK = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
         "fbclid", "gclid", "spm", "from", "source", "ref", "share"}


def canonical_url(url):
    try:
        p = urlsplit(str(url or ""))
        if not p.netloc:
            return ""
        host = p.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+", "/", p.path).rstrip("/") or "/"
        query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query)
                                 if k.lower() not in TRACK))
        return urlunsplit(("https", host, path, query, ""))
    except ValueError:
        return ""


def text_fingerprint(value):
    """Preserve numbers; different figures/real updates must not be collapsed."""
    txt = str(value or "").lower()
    txt = re.sub(r"【[^】]*】|\[[^\]]{0,18}\]", "", txt)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", txt)


def article_signature(article):
    return (
        canonical_url(article.get("url")),
        text_fingerprint(article.get("title")),
        text_fingerprint(article.get("summary") or article.get("description")),
        text_fingerprint(article.get("source_name")),
    )


def load_previous_articles(project_root, current_date, lookback_days=21):
    """Load only already-approved daily cleaned articles; omit current date."""
    try:
        end = date.fromisoformat(current_date)
    except ValueError:
        return []
    out = []
    folder = Path(project_root) / "data" / "cleaned"
    for offset in range(1, lookback_days + 1):
        day = (end - timedelta(days=offset)).isoformat()
        p = folder / day / "cleaned_articles.json"
        if not p.exists():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            items = obj if isinstance(obj, list) else obj.get("articles", [])
            for item in items:
                if isinstance(item, dict):
                    out.append((day, article_signature(item)))
        except (OSError, ValueError, TypeError):
            continue
    return out


def prior_duplicate(article, history):
    """Suppress identical story, not same topic or substantially updated claim."""
    url, title, lead, source = article_signature(article)
    if not title:
        return ""
    for day, (old_url, old_title, old_lead, old_source) in history:
        # Same article across days, or same-source mirror: duplicate.
        # Another independent publication is potential corroborating evidence
        # and must not be destroyed at the collection layer.
        if url and url == old_url and title == old_title and lead == old_lead:
            return day
        if source and source == old_source and title == old_title and lead == old_lead:
            return day
    return ""
