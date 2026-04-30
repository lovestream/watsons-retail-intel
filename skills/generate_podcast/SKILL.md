# generate_podcast — 日报转口播稿 + 音频播客

## 功能

读取最终日报，生成适合通勤收听的口播稿（Markdown）+ MP3 音频。

**不是朗读日报，而是转化为自然口播。**

## 核心原则

1. **不直接朗读日报** — 转成口播语气
2. **不新增日报外事实** — 所有内容来自原始日报
3. **不新增日报外数据** — 不编造数字和事件
4. **保留经营判断** — 弱化 event_id、Markdown 符号和复杂层级
5. **口播稿字数**:
   - 正常日报: 1800-2500 中文字符
   - 无信号日报: 200-300 中文字符（不硬凑，诚实简短）
6. **无信号日报也有口播版**（极简版）
7. **音频失败不影响日报发送**

## LLM 处理

- 优先: `LongCat-Flash-Chat`
- Fallback: `LongCat-Flash-Lite`
- 最终 Fallback: 规则模板
- 模型路由: `config/model_router.yaml` 中 `generate_podcast` 条目

## voice 选项

| voice | 说明 |
|-------|------|
| `zh-CN-XiaoxiaoNeural` | **默认**，女声，温暖 |
| `zh-CN-YunxiNeural` | 男声，专业 |
| `zh-CN-YunjianNeural` | 男声，新闻 |
| `zh-CN-XiaoyiNeural` | 女声，活泼 |

## 用法

```bash
# 默认（今天，默认语音）
python skills/generate_podcast/generate_podcast.py \
    --project-root /app/working/projects/watsons-retail-intel

# 指定日期和语音
python skills/generate_podcast/generate_podcast.py \
    --project-root . --date 2026-04-26 \
    --voice zh-CN-YunxiNeural

# 指定日报文件
python skills/generate_podcast/generate_podcast.py \
    --project-root . --date 2026-04-26 \
    --report-file reports/daily/2026/04/2026-04-26.md

# 不用 LLM，规则模式
python skills/generate_podcast/generate_podcast.py \
    --project-root . --use-llm false
```

## 输入

| 文件 | 路径 | 说明 |
|------|------|------|
| 最终日报 | `reports/daily/YYYY/MM/YYYY-MM-DD.md` | 首选 |
| 备选 | `data/reports/YYYY-MM-DD/final_report_*.md` | 兜底 |
| 无信号日报 | `data/reports/YYYY-MM-DD/daily_report_*_no_signal.md` | 自动检测 |
| Pipeline统计 | `data/runs/YYYY-MM-DD/run_manifest.json` | 采集数据 |

自动检测日报类型（正常/无信号）。

## 输出

| 文件 | 路径 |
|------|------|
| 口播稿 | `podcasts/scripts/YYYY-MM-DD.md` |
| 音频 | `podcasts/audio/YYYY-MM-DD.mp3` |
| 日志 | `data/logs/YYYY-MM-DD/generate_podcast.log` |

## 返回值

```json
{
  "ok": true,
  "date": "2026-04-26",
  "report_file": "reports/daily/2026/04/2026-04-26.md",
  "script_file": "podcasts/scripts/2026-04-26.md",
  "audio_file": "podcasts/audio/2026-04-26.mp3",
  "script_length": 2150,
  "audio_exists": true,
  "audio_size_bytes": 95432,
  "is_no_signal": false,
  "voice": "zh-CN-XiaoxiaoNeural",
  "rate": "+0%",
  "llm_used": true,
  "tts_success": true,
  "errors": []
}
```

## 口播稿结构

### 正常日报（1800-2500字）
1. 开场白 + 一句话结论
2. 今日重点信号（≤3条）
3. 平台动态解读
4. 竞对与品牌动作
5. 品类与场景机会
6. 对屈臣氏的经营提示
7. 今日唯一建议动作
8. 明日追踪清单 + 结束语

### 无信号日报（200-300字）
1. 一句话结论：今天无高质量新增信号
2. 采集数据和筛选结果
3. 明日关注方向
4. 结束语

**无信号口播不硬凑字数，诚实简短。**