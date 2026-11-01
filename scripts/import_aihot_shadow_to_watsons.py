#!/usr/bin/env python3
"""Stage AIHOT articles in the Watsons *shadow* pipeline's native input path.

No network, LLM, Cron, ledger, or delivery action. The production worktree
must never be supplied as --shadow-root. This script stops at input material.
"""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from skills.filter_relevant_articles.business_scope import decide


def _atomic_json(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".aihot-shadow-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def prepare(bridge_dir, shadow_root, day, *, replace=False):
    date.fromisoformat(day)
    bridge_dir = Path(bridge_dir).resolve()
    shadow_root = Path(shadow_root).resolve()
    if not any("shadow" in part.lower() for part in shadow_root.parts):
        raise ValueError("Refusing to write outside a clearly named shadow worktree")
    if shadow_root == PROJECT_ROOT or PROJECT_ROOT in shadow_root.parents:
        # Running under the real source tree makes accidental state mixing easy.
        raise ValueError("Shadow worktree must be independent of the current repo checkout")
    if not (shadow_root / "skills" / "filter_relevant_articles").is_dir():
        raise ValueError("Shadow target must be a full Watsons checkout with skills/filter_relevant_articles")
    if bridge_dir == shadow_root or shadow_root in bridge_dir.parents:
        raise ValueError("Bridge staging dir must be separate from Watsons shadow worktree")

    raw = json.loads((bridge_dir / "raw_articles_all.json").read_text(encoding="utf-8"))
    manifest = json.loads((bridge_dir / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("articles"), list):
        raise ValueError("Missing bridge articles array")
    if not raw.get("metadata", {}).get("shadow") or raw.get("metadata", {}).get("provider") != "AIHOT":
        raise ValueError("Unverified bridge shadow input")
    if manifest.get("schema") != "aihot-raw-bridge-v1":
        raise ValueError("Unsupported bridge schema")
    if str(manifest.get("as_of")) != day or str(raw.get("metadata", {}).get("as_of")) != day:
        raise ValueError("Bridge business day does not match destination day")
    if len(raw["articles"]) != manifest.get("article_count"):
        raise ValueError("Bridge article count mismatch")
    ids = [x.get("article_id") for x in raw["articles"] if isinstance(x, dict)]
    if len(ids) != len(raw["articles"]) or len(ids) != len(set(ids)):
        raise ValueError("Duplicate or malformed article ids in normalized export")
    if any(not str(ident).startswith("AH_") for ident in ids):
        raise ValueError("Input contains non-AIHOT material")

    dest = shadow_root / "data" / "raw" / day / "raw_articles_all.json"
    if dest.exists() and not replace:
        raise FileExistsError("Shadow input exists; require explicit --replace-shadow to overwrite")
    _atomic_json(dest, raw)

    scope_counts = {"main": 0, "reference": 0, "reject": 0}
    for article in raw["articles"]:
        result = decide(article)
        scope_counts[result["decision"]] += 1
    result = {
        "schema": "watsons-aihot-shadow-import-v1",
        "as_of": day, "source_bridge_manifest": str(bridge_dir / "manifest.json"),
        "target": str(dest), "article_count": len(raw["articles"]),
        "scope_preview_counts": scope_counts,
        "input_sha256": hashlib.sha256(
            json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "llm_calls": 0, "production_data_touched": False,
        "next_step": "Only run existing P01/P02 pipeline on this independent shadow Watsons worktree after checking LLM costs.",
    }
    _atomic_json(dest.parent / "aihot_shadow_import_manifest.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bridge-dir", required=True)
    p.add_argument("--shadow-root", required=True)
    p.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    p.add_argument("--replace-shadow", action="store_true")
    a = p.parse_args()
    try:
        result = prepare(a.bridge_dir, a.shadow_root, a.as_of, replace=a.replace_shadow)
    except (ValueError, FileNotFoundError, FileExistsError, json.JSONDecodeError) as e:
        p.error(str(e))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
