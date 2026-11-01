"""Evidence-aware date contract shared by temporal validation and reader briefs.

A parseable YYYY-MM-DD is not proof that an event occurred that day.
This module intentionally errs on the side of unknown; publication and
observation dates are different temporal roles.
"""
from datetime import date, datetime
import re
from typing import Any, Optional, Set

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DAY = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
CN_DAY = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
DAY_PRECISIONS = frozenset({"day"})  # published is a role, never a precision.
VALID_CONFIDENCE = frozenset({"high", "medium"})
FUTURE_OR_OTHER_ROLE = re.compile(
    r"计划|拟于|预计|将于|待于|预定|截至|截止|生效|实施|公告|公布|宣布|"
    r"财报|统计期|季度|测算|预测|同比|活动期间|双11期间"
)


def exact_day(raw: Any) -> Optional[str]:
    """Require a true calendar date, not a month/quarter or datetime proxy."""
    if not isinstance(raw, str) or not DAY.fullmatch(raw.strip()):
        return None
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except ValueError:
        return None


def day_candidates(text: Any, reference_year: Optional[int] = None) -> Set[str]:
    """Recognize explicit day mentions; no conversion from month or quarter."""
    if not isinstance(text, str):
        return set()
    found: Set[str] = set()
    for m in ISO_DAY.finditer(text):
        try:
            found.add(date(int(m[1]), int(m[2]), int(m[3])).isoformat())
        except ValueError:
            pass
    for m in CN_DAY.finditer(text):
        year = int(m[1]) if m[1] else reference_year
        if year is None:
            continue
        try:
            found.add(date(year, int(m[2]), int(m[3])).isoformat())
        except ValueError:
            pass
    return found


def source_quotes(event: dict) -> list:
    """Use original evidence_text. LLM-produced temporal quotes require support.

    Never treat 'occurred=YYYY-MM-DD' metadata or unbound model text as
    evidence from an external source.
    """
    original = event.get("evidence_text") or ""
    if not isinstance(original, str) or not original.strip():
        return []
    out = [original]
    value = event.get("temporal_evidence") or []
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, dict):
            item = item.get("quote") or item.get("text") or ""
        if (isinstance(item, str) and item.strip()
                and item in original
                and not re.match(r"^\s*(occurred|announced|effective)\s*=", item)):
            out.append(item)
    return out


def trustworthy_occurred_day(event: dict) -> Optional[str]:
    """Return a day only when its claimed role, precision and evidence agree.

    This is a necessary syntactic/evidence gate, not a substitute for semantic
    P03 review where a quote contains several different temporal roles.
    """
    if event.get("evidence_verified") is False:
        return None  # The quote was not found in the actual source article.
    value = exact_day(event.get("event_occurred_at") or event.get("occurred_at"))
    precision = str(event.get("event_time_precision") or "").strip().lower()
    confidence = str(event.get("event_time_confidence") or "").strip().lower()
    if not value or precision not in DAY_PRECISIONS or confidence not in VALID_CONFIDENCE:
        return None
    # Explicit date appears in a source quote, not just in generated fact title.
    quoted = source_quotes(event)
    if not quoted:
        return None
    # An unqualified "10月20日" needs a publication-year anchor rather than
    # circularly using the model's own claimed occurred year.
    publication = str(event.get("published_at") or "")[:10]
    pub_day = exact_day(publication)
    reference_year = int(pub_day[:4]) if pub_day else None
    # Prefer a genuine *event-scoped* quote when a paragraph contains dates
    # for multiple different actions. The exact source quote must contain an
    # action as well as its date; an isolated "10月20日" is not enough.
    roles = event.get("temporal_role_evidence") or []
    original = event.get("evidence_text") or ""
    for item in roles:
        if not isinstance(item, dict) or item.get("role") != "occurred":
            continue
        quote = item.get("quote") or ""
        if (not isinstance(quote, str) or not quote.strip()
                or quote not in original or len(quote.strip()) < 11):
            continue
        if exact_day(str(item.get("value") or "")) != value:
            continue
        if day_candidates(quote, reference_year) != {value}:
            continue
        if FUTURE_OR_OTHER_ROLE.search(quote):
            continue
        return value

    # No valid scoped occurrence quote: paragraph-wide date is safe only if
    # it contains exactly this day and no scheduled/announced/statistical role.
    mentioned = set().union(*(day_candidates(q, reference_year) for q in quoted))
    if mentioned != {value}:
        return None
    if any(FUTURE_OR_OTHER_ROLE.search(q) for q in quoted):
        return None
    return value


def normalize_precision(value: Any) -> str:
    val = str(value or "unknown").strip().lower()
    return val if val in {"day", "week", "month", "quarter", "year", "range", "unknown"} else "unknown"
