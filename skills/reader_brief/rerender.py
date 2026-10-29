#!/usr/bin/env python3
"""无需 LLM：用已保存的 brief JSON 重新渲染 HTML（改版式时用）。"""
import argparse
import json
from pathlib import Path

from skills.reader_brief import render_mobile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--period-type", required=True)
    ap.add_argument("--period-label", default="")
    ap.add_argument("--out-html", required=True)
    a = ap.parse_args()
    data = json.loads(Path(a.json).read_text(encoding="utf-8"))
    pack = {"facts": data["facts"], **data.get("pack_meta", {})}
    html = render_mobile.render(data["brief"], pack, a.period_type,
                               a.period_label)
    Path(a.out_html).write_text(html, encoding="utf-8")
    print(f"rerendered {a.out_html} {len(html)} chars")


if __name__ == "__main__":
    main()
