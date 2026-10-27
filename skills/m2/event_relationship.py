#!/usr/bin/env python3
"""Lightweight event-relation and independent-source helpers.

AIHOT separates *same event*, *update*, and *related but new* instead of
folding everything into one similarity bucket.  This is a deterministic
adjunct to the existing fact_hash/cluster_key logic; it never replaces it.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Set


def _domain(url: str) -> str:
    u = str(url or "")
    if "://" in u:
        u = u.split("://", 1)[1]
    return u.split("/", 1)[0].lower()


def independent_sources(items: Iterable[Dict[str, Any]]) -> Set[str]:
    domains = {_domain(x.get("source_url") or "") for x in items}
    domains.discard("")
    return domains


def _char_bigrams(text: str) -> Set[str]:
    chars = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text)
    return {chars[i:i + 2] for i in range(len(chars) - 1)}


def relation(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    """Return same_event / update / related / unrelated."""
    if a.get("fact_hash") and a.get("fact_hash") == b.get("fact_hash"):
        return "same_event"
    if a.get("cluster_key") and a.get("cluster_key") == b.get("cluster_key"):
        return "same_event"
    ta = str(a.get("event_title") or a.get("title") or "")
    tb = str(b.get("event_title") or b.get("title") or "")
    aa = _char_bigrams(ta)
    bb = _char_bigrams(tb)
    if aa and bb:
        j = len(aa & bb) / len(aa | bb)
        if j >= 0.55:
            # Same subject reported on a new date is an update, not a duplicate.
            if str(a.get("published_at") or "") != str(b.get("published_at") or ""):
                return "update"
            return "same_event"
        if j >= 0.25:
            if str(a.get("published_at") or "") != str(b.get("published_at") or ""):
                return "update"
            return "related"
    return "unrelated"


def annotate_cluster(cluster: Dict[str, Any],
                     events: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(cluster)
    srcs = independent_sources(events)
    out["independent_source_count"] = len(srcs)
    out["independent_domains"] = sorted(srcs)[:8]
    # Separate heat from editorial priority: independent corroboration is a
    # signal, not permission to promote an unverified fact.
    out["heat_score"] = min(5, max(1, len(srcs)))
    return out
