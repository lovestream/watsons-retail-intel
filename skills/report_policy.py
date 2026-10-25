#!/usr/bin/env python3
"""Shared truth/report policy for the Watsons event pipeline.

This module is deliberately small and dependency-free.  It is the single
place that answers three questions used by temporal validation, novelty,
scoring, the report selector and the R2-A launcher:

1. Is an event's claimed fact verified, or does it need review?
2. May an event enter the *new verified fact* pool for this report?
3. When two stages disagree about ``report_eligibility``, which value wins?

The rule is monotonic: a later stage may make an event less eligible, but a
high score or a first-seen ledger hit must never promote a date-conflicted,
hearsay-only or otherwise unverified event to ``core``.

No article bodies, credentials or network calls are used here.
"""
from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Order: smaller is more prominent, larger is more restrictive.  ``review``
# sits between tracking and reference because it is actionable (verify it),
# while both are excluded from the verified new-fact pool.
ELIGIBILITY_RANK = {
    "core": 0,
    "tracking": 1,
    "review": 2,
    "reference": 3,
    "reject": 4,
    "archive": 4,
}

HEARSAY_MARKERS = [
    "听说", "据说", "据传", "网传", "传闻", "消息人士", "知情人士",
    "内部人士", "爆料", "疑似", "或将", "有望", "可能将", "未经证实",
]

# Out-of-domain vertical signals.  A single generic word such as ``零售`` or
# ``门店`` is never enough to enter the verified new-fact pool.  Keep these
# vertical-specific and require the absence of a strong Watsons domain signal.
_OUT_OF_DOMAIN_VERTICALS = {
    "passenger_vehicle_retail": [
        "乘用车", "乘联会", "乘联分会", "汽车销量", "新能源车销量",
        "车企销量", "汽车零售", "汽车市场零售",
    ],
    "apparel_vertical": [
        "波司登", "羽绒服", "服饰品牌", "服装品牌", "鞋服品牌",
        "安踏", "李宁", "优衣库", "海澜之家", "太平鸟",
    ],
}

_STRONG_DOMAIN_SIGNALS = [
    "屈臣氏", "watsons", "即时零售", "美团闪购", "京东秒送", "京东到家",
    "淘宝闪购", "饿了么", "抖音小时达", "前置仓", "闪电仓",
    "美妆", "个护", "护肤", "彩妆", "洗护", "防晒", "面膜", "香水",
    "化妆品", "个人护理", "口腔护理", "身体护理", "男士护理",
]


def _text(event: Dict[str, Any]) -> str:
    return " ".join(
        str(event.get(k) or "")
        # source_title is deliberately excluded: an article about beauty can
        # contain an unrelated auto-retail data point, and the source title must
        # not make that event look in-domain.
        for k in ("event_title", "fact", "evidence_text")
    ).lower()


def is_out_of_domain_event(event: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether a generic retail event is outside the Watsons domain.

    The check is intentionally *not* a blanket ban on the words ``汽车`` or
    ``服饰``.  If the same item also contains a strong instant-retail or
    beauty/personal-care signal, it stays in scope.
    """
    text = _text(event)
    if any(sig.lower() in text for sig in _STRONG_DOMAIN_SIGNALS):
        return False, ""
    for label, terms in _OUT_OF_DOMAIN_VERTICALS.items():
        if any(term.lower() in text for term in terms):
            return True, label
    return False, ""


def normalize_eligibility(value: Any) -> str:
    value = str(value or "").strip().lower()
    if value in ELIGIBILITY_RANK:
        return value
    # ``reject`` is treated as archive/reference for display purposes.
    if value in ("rejected", "background"):
        return "reference"
    return "review" if value else "review"


def most_restrictive(*values: Any) -> str:
    """Pick the least-prominent (highest rank) eligibility among values."""
    normalized = [normalize_eligibility(v) for v in values if v is not None]
    if not normalized:
        return "review"
    return max(normalized, key=lambda v: ELIGIBILITY_RANK[v])


def cap_eligibility(event: Dict[str, Any], novelty_eligibility: Any) -> str:
    """Apply the monotonic eligibility contract.

    ``core`` is allowed only when the temporal stage and the novelty stage both
    allow it *and* the event is verified.  This is the guard that stops
    ``score``/``novelty`` from resetting ``review``/``reference`` to ``core``.
    """
    values: List[Any] = [
        event.get("temporal_eligibility"),
        novelty_eligibility,
        event.get("report_eligibility"),
    ]
    freshness = str(event.get("fact_freshness_status") or "")
    if freshness in ("date_conflict", "republished_old_fact"):
        values.append("reference")
    if not is_verified_event(event):
        values.append("review")
    return most_restrictive(*values)


def is_verified_event(event: Dict[str, Any]) -> bool:
    """Strict verification predicate used by report selection."""
    return (
        str(event.get("verification_status") or "") == "verified"
        and not bool(event.get("needs_verification"))
        and not bool(event.get("date_conflict"))
        and not bool(event.get("hearsay_markers"))
    )


def has_report_evidence(event: Dict[str, Any]) -> bool:
    return bool(str(event.get("fact") or "").strip()) and bool(
        str(event.get("evidence_text") or "").strip()
    ) and bool(str(event.get("source_url") or "").strip())


def is_new_or_legitimate_update(event: Dict[str, Any]) -> bool:
    return str(event.get("novelty_status") or "") in ("new_today", "updated_today")


def is_reportable_event(event: Dict[str, Any]) -> bool:
    """Whether an event can count as a *verified new fact* for this report.

    Background/reference and review/tracking events may still be displayed in
    a background or follow-up section, but they never inflate the reportable
    count and never masquerade as newly verified facts.
    """
    if not isinstance(event, dict):
        return False
    if str(event.get("priority") or "") not in ("P0", "P1", "P2"):
        return False
    if normalize_eligibility(event.get("report_eligibility")) != "core":
        return False
    if not is_verified_event(event):
        return False
    if not is_new_or_legitimate_update(event):
        return False
    if not has_report_evidence(event):
        return False
    return True


def reportable_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [ev for ev in events if is_reportable_event(ev)]


def _parse_day(value: Any) -> Optional[_date]:
    if isinstance(value, _date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    # Accept ISO timestamps, date-only values and the YYYY-MM-DD prefix.
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _day(value: Any) -> Optional[_date]:
    if isinstance(value, datetime):
        return value.date()
    return _parse_day(value)


def evaluate_verification(
    event: Dict[str, Any],
    *,
    as_of_date: Any = None,
    article: Optional[Dict[str, Any]] = None,
    article_sanity: Optional[Dict[str, Any]] = None,
    temporal: Optional[Dict[str, Any]] = None,
    freshness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify a single extracted event without pretending inferred dates are facts.

    Date semantics are kept separate:
      * ``event_occurred_at``   -- when the event actually happened
      * ``event_announced_at``  -- when it was announced/published
      * ``event_effective_at``  -- when a rule takes effect (may be future)
      * ``published_at``        -- article publication time
      * ``collected_at``/date   -- the pipeline's as-of day

    A missing or inferred event date is explicitly ``needs_verification``; it
    is retained for review/reference but cannot become a verified new fact.
    """
    event = event if isinstance(event, dict) else {}
    article = article if isinstance(article, dict) else {}
    article_sanity = article_sanity if isinstance(article_sanity, dict) else {}
    temporal = temporal if isinstance(temporal, dict) else {}
    freshness = freshness if isinstance(freshness, dict) else {}

    as_of = _day(as_of_date) or _day(event.get("collected_at")) or _day(
        event.get("published_at")
    )
    reasons: List[str] = []

    occurred = event.get("event_occurred_at") or temporal.get("event_occurred_at") or ""
    announced = event.get("event_announced_at") or temporal.get("event_announced_at") or ""
    effective = event.get("event_effective_at") or temporal.get("event_effective_at") or ""
    source_hint = str(event.get("event_time_source") or "")

    # Prefer the explicitly extracted field named by event_time_source.  An
    # announced event may still have an inferred ``event_occurred_at`` from a
    # relative content date; that inferred value must not masquerade as the
    # announcement date.
    if source_hint == "announced" and announced:
        anchor, date_source = _day(announced), "announced"
    elif source_hint == "effective" and effective:
        anchor, date_source = _day(effective), "effective"
    elif source_hint == "occurred" and occurred:
        anchor, date_source = _day(occurred), "occurred"
    elif occurred:
        anchor, date_source = _day(occurred), source_hint or "occurred"
    elif announced:
        anchor, date_source = _day(announced), source_hint or "announced"
    elif effective:
        anchor, date_source = _day(effective), source_hint or "effective"
    else:
        anchor, date_source = None, "missing"

    if anchor is None:
        published = _day(article.get("published_at")) or _day(event.get("published_at"))
        if published:
            anchor, date_source = published, "published_fallback"
            reasons.append("event_date_missing_published_fallback")
        else:
            reasons.append("event_date_missing")

    if date_source == "inferred_content_date":
        reasons.append("event_date_inferred_from_content")
    elif date_source in ("published_fallback", "collected_fallback"):
        reasons.append("event_date_not_explicit")

    if as_of:
        # Check every declared date, not only the field named by the time
        # source.  A future event_occurred_at must not be hidden merely because
        # the extractor also supplied a valid announcement date.
        seen_reasons = set(reasons)
        for label, raw_value in (("occurred", occurred), ("announced", announced), ("effective", effective)):
            day = _day(raw_value)
            if not day:
                continue
            if label != "effective" and day > as_of + timedelta(days=1):
                reason = f"event_date_after_collection:{label}:{day.isoformat()}>{as_of.isoformat()}"
                if reason not in seen_reasons:
                    reasons.append(reason)
                    seen_reasons.add(reason)
            # A "new today" story whose only dated anchor is a very old
            # announcement is usually a recap or an extraction error.  Keep it
            # for review instead of presenting it as a fresh fact.
            if label == "announced" and day < as_of - timedelta(days=30):
                freshness_value = str(
                    freshness.get("fact_freshness_status")
                    or event.get("fact_freshness_status")
                    or ""
                )
                if freshness_value in ("new_fact", "updated_fact"):
                    reason = f"event_date_before_window:announced:{day.isoformat()}<{as_of.isoformat()}"
                    if reason not in seen_reasons:
                        reasons.append(reason)
                        seen_reasons.add(reason)

    if article_sanity.get("article_time_status") == "invalid_future_date":
        reasons.append("article_published_at_in_future")
    if str(freshness.get("fact_freshness_status") or event.get("fact_freshness_status") or "") == "date_conflict":
        reasons.append("fact_freshness_date_conflict")

    text = " ".join(
        [str(event.get("fact") or ""), str(event.get("evidence_text") or "")]
    )
    hit_markers = [m for m in HEARSAY_MARKERS if m in text]
    if hit_markers:
        reasons.append("hearsay_source:%s" % ",".join(hit_markers))

    if not str(event.get("fact") or "").strip():
        reasons.append("fact_empty")
    if not str(event.get("evidence_text") or "").strip():
        reasons.append("evidence_text_empty")
    if not str(event.get("source_url") or "").strip():
        reasons.append("source_url_missing")

    # Keep an explicit upstream flag (rules extraction) sticky.
    if bool(event.get("needs_verification")) and not reasons:
        reasons.append("upstream_needs_verification")

    verified = not reasons
    return {
        "verification_status": "verified" if verified else "needs_verification",
        "verified": verified,
        "needs_verification": not verified,
        "verification_reasons": reasons,
        "verification_date_source": date_source,
        "verification_event_date": anchor.isoformat() if anchor else "",
        "verification_as_of": as_of.isoformat() if as_of else "",
        "date_conflict": any(
            r.startswith("event_date_after_collection")
            or r in ("fact_freshness_date_conflict", "article_published_at_in_future")
            for r in reasons
        ),
        "hearsay_markers": hit_markers,
    }
