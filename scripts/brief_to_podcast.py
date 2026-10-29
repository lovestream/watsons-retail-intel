"""从 brief JSON 生成口播稿并合成试听 MP3（edge-tts，免费离线外唯一方案）。"""
import asyncio
import json
import subprocess
import sys

import edge_tts

VOICE = "zh-CN-XiaoxiaoNeural"


def build_script(brief: dict) -> str:
    lines = [
        "各位好，这是本周的屈臣氏经营简报。",
        brief["lead"],
        "先说本周三个变化。",
    ]
    for i, s in enumerate(brief.get("signals", []), 1):
        lines.append(f"第{i}个变化，{s['title']}。{s['what_changed']}{s['why_it_matters']}")
    comps = brief.get("competitors", [])
    if comps:
        lines.append("再看竞对。")
        for c in comps:
            lines.append(f"{c['name']}：{c['move']}{c['implication']}")
    acts = brief.get("actions", [])
    if acts:
        lines.append("本周可以做的三件事。")
        for i, a in enumerate(acts, 1):
            lines.append(f"第{i}件，{a['action']}，时间窗是{a['horizon']}。")
    watch = brief.get("watch", [])
    if watch:
        lines.append("下周要盯两件事。")
        for w in watch:
            lines.append(f"{w['item']}，看{w['metric']}。")
    lines.append("以上就是本周简报，我们下周见。")
    return "\n".join(lines)


def main():
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    script = build_script(data["brief"])
    open(sys.argv[2], "w", encoding="utf-8").write(script)
    print("script chars:", len(script))
    asyncio.run(edge_tts.Communicate(script, VOICE).save(sys.argv[3]))
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1",
                            sys.argv[3]], capture_output=True, text=True)
    print("mp3 seconds:", probe.stdout.strip() or probe.stderr[:120])


if __name__ == "__main__":
    main()
