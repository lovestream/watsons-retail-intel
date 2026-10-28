#!/usr/bin/env python3
"""Offline acceptance check for the R3 periodic reader-first email digest.

It renders the real 2026-W43 report plus frozen monthly/yearly samples, runs
the production send function with dry_run=True (no SMTP), checks reader-safety,
and captures 360/375/390px Chromium screenshots.  No production file, ledger,
Cron, PID1 or mailbox is touched.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills.send_daily_report_email.periodic_mobile_digest import has_internal_marker
from skills.send_daily_report_email.send_daily_report_email import (
    _build_periodic_email_html,
    send_periodic_report_email,
)

PROD = Path("/app/working/projects/watsons-retail-intel")
OUT = Path("/tmp/email_ux_out")
CASES = [
    {
        "slug": "weekly-2026-W43",
        "type": "weekly",
        "name": "周报",
        "label": "2026-W43（10/19–10/25）",
        "report": Path("/tmp/e1_wt/reports/weekly/2026/2026-W43.md"),
        "audio": Path("/tmp/e1_wt/podcasts/audio/weekly-2026-W43.mp3"),
        "real": True,
    },
    {
        "slug": "monthly-2026-07",
        "type": "monthly",
        "name": "月报",
        "label": "2026年7月",
        "report": PROD / "reports/monthly/2026/2026-07.md",
        "audio": None,
        "real": True,
    },
    {
        "slug": "yearly-2026-sample",
        "type": "yearly",
        "name": "年报",
        "label": "2026年",
        "report": ROOT / "tests/fixtures/periodic/yearly_sample.md",
        "audio": None,
        "real": False,
    },
]
BANNED = [
    r"E\d{8}_\d{4}", r"\[\s*['\"]?[ABK]", r"证据级别", r"评分口径",
    r"置信度", r"事件ID", r"event[_ ]?id", r"附录", r"来源与证据",
    r"下一工作周", r"为什么下一", r"验证什么", r"sendable",
]
WIDTHS = (360, 375, 390, 600)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _visible(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _validate(html: str, slug: str) -> dict:
    visible = _visible(html)
    hits = [pat for pat in BANNED if re.search(pat, visible, re.I)]
    return {
        "slug": slug,
        "html_bytes": len(html.encode()),
        "visible_chars": len(visible),
        "internal_marker": has_internal_marker(html),
        "banned_hits": hits,
        "has_th": "<th" in html.lower(),
        "max_width_600": "max-width:600px" in html,
        "viewport": 'name="viewport"' in html,
        "http_links": len(re.findall(r'href="https?://', html)),
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
    }


def _screenshots(html_path: Path, slug: str, results: list[dict]) -> list[Path]:
    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium",
                                    args=["--no-sandbox"])
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 900},
                                    device_scale_factor=1)
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(150)
            metrics = page.evaluate("""() => {
                const vw = window.innerWidth;
                const bad = [];
                for (const el of document.querySelectorAll('body *')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && (r.right > vw + 1 || r.left < -1)) {
                        bad.push({tag: el.tagName, right: Math.round(r.right), left: Math.round(r.left)});
                    }
                }
                return {
                    viewport: vw,
                    scrollWidth: document.documentElement.scrollWidth,
                    bodyScrollWidth: document.body.scrollWidth,
                    overflow: bad.slice(0, 8),
                    height: document.documentElement.scrollHeight
                };
            }""")
            shot = OUT / "screenshots" / f"{slug}-{width}.png"
            page.screenshot(path=str(shot), full_page=True)
            results.append({"slug": slug, "width": width, **metrics})
            shots.append(shot)
            page.close()
        browser.close()
    return shots


def _contact_sheet(shots: list[Path], slug: str) -> Path:
    imgs = [Image.open(p).convert("RGB") for p in shots]
    pad, top = 12, 38
    width = sum(i.width for i in imgs) + pad * (len(imgs) + 1)
    height = max(i.height for i in imgs) + top + pad
    canvas = Image.new("RGB", (width, height), "#e8ecef")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    x = pad
    for img, width in zip(imgs, WIDTHS):
        canvas.paste(img, (x, top))
        draw.text((x + 8, 10), f"{width}px", fill="#183b45", font=font)
        x += img.width + pad
    out = OUT / f"{slug}-360-375-390.png"
    canvas.save(out, optimize=True)
    return out


def main() -> int:
    (OUT / "screenshots").mkdir(parents=True, exist_ok=True)
    manifest = {"out": str(OUT), "cases": [], "dry_runs": [], "layout": []}
    for case in CASES:
        html = _build_periodic_email_html(
            case["report"].read_text(), case["type"], case["name"], case["label"],
            has_audio=bool(case["audio"] and case["audio"].exists()),
        )
        html_path = OUT / f"{case['slug']}.html"
        html_path.write_text(html)
        val = _validate(html, case["slug"])
        val["report_path"] = str(case["report"])
        val["real_report"] = case["real"]
        val["html_path"] = str(html_path)
        manifest["cases"].append(val)
        if val["internal_marker"] or val["banned_hits"] or val["has_th"] or not val["max_width_600"]:
            raise SystemExit(f"reader-safety failure: {val}")

        # Full R3 send path, dry-run only.  Weekly uses its real MP3; the
        # monthly/yearly samples have no podcast asset, so audio is explicitly
        # disabled for those two HTML-only checks.
        kwargs = dict(
            report_type=case["type"], period_label=case["label"],
            report_path=str(case["report"]),
            audio_path=str(case["audio"]) if case["audio"] else "",
            recipient="123399974@qq.com", project_root=str(PROD),
            dry_run=True, require_audio=bool(case["audio"]),
        )
        result = send_periodic_report_email(**kwargs)
        manifest["dry_runs"].append({"slug": case["slug"], **result})
        if not result.get("ok") or not result.get("dry_run"):
            raise SystemExit(f"dry-run failure: {case['slug']}: {result}")
        if result.get("attachments", 0) > 1:
            raise SystemExit(f"too many attachments: {case['slug']}")
        if any(str(x).endswith(".md") for x in result.get("attachment_names", [])):
            raise SystemExit(f"markdown attachment leaked: {case['slug']}")

        shots = _screenshots(html_path, case["slug"], manifest["layout"])
        manifest["cases"][-1]["screenshots"] = [str(p) for p in shots]
        manifest["cases"][-1]["contact_sheet"] = str(_contact_sheet(shots[:3], case["slug"]))

    manifest["layout_ok"] = all(
        row["scrollWidth"] <= row["width"] and row["bodyScrollWidth"] <= row["width"]
        and not row["overflow"] for row in manifest["layout"]
    )
    manifest["pass"] = manifest["layout_ok"] and all(
        not c["internal_marker"] and not c["banned_hits"] and not c["has_th"]
        for c in manifest["cases"]
    ) and all(r.get("ok") and r.get("dry_run") for r in manifest["dry_runs"])
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
