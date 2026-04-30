# extract_events

从清洗后的文章中抽取结构化事件，输出 events_raw.json。

## 何时使用

- 过滤完成后，对 `data/cleaned/YYYY-MM-DD/cleaned_articles.json` 进行事件抽取
- 将文章转化为结构化事件列表
- LLM 不可用时自动降级为轻量规则抽取

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_root` | str | ✅ | 项目根目录 |
| `date` | str | ✅ | 日期 YYYY-MM-DD |
| `cleaned_file` | str | ❌ | 覆盖输入文件路径 |
| `output_file` | str | ❌ | 覆盖输出文件路径 |
| `use_llm` | bool | ❌ | 是否使用 LLM，默认 True |
| `max_articles` | int | ❌ | 最大处理文章数，默认全部 |

## 输出文件

| 文件 | 路径 | 说明 |
|------|------|------|
| 事件 | `data/events/YYYY-MM-DD/events_raw.json` | 结构化事件列表 |
| 拒绝文章 | `data/events/YYYY-MM-DD/events_rejected_articles.json` | 被拒绝的文章 |
| 日志 | `data/logs/YYYY-MM-DD/extract_events.log` | 运行日志 |

## 事件类型

platform_move, platform_rule, competitor_move, brand_move,
category_trend, channel_shift, consumer_scene, data_signal,
policy_signal, background_only, unclear

## 业务变量

流量、转化率、客单价、复购、价格、补贴、毛利、货盘、SKU、履约、
会员、私域、活动、投流、门店覆盖、平台资源位、组织执行、竞争格局、
品类机会、风险

## CLI

```bash
python extract_events.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26 \
  --use-llm true \
  --max-articles 20
```

## LLM 测试

```bash
python extract_events.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --test-llm
```

## 函数

```python
from skills.extract_events.extract_events import extract_events

result = extract_events(
    project_root="/app/working/projects/watsons-retail-intel",
    date="2026-04-26",
)
```