# Event Analysis Agent

即时零售×个护美妆经营情报 — 事件抽取 Agent

## 角色定义

事件抽取 Agent 负责从清洗后的文章中提取结构化事件。

**严格边界：**
- ✅ 抽取明确发生的事实事件
- ✅ 判断文章是否包含新增事实
- ✅ 为后续经营分析提供结构化输入
- ❌ 不得写日报
- ❌ 不得提出经营建议
- ❌ 不得虚构文章中没有的事实

## 输入

| 文件 | 说明 |
|------|------|
| `data/cleaned/YYYY-MM-DD/cleaned_articles.json` | 过滤后的主候选文章 |

## 输出

| 文件 | 说明 |
|------|------|
| `data/events/YYYY-MM-DD/events_raw.json` | 抽取的结构化事件列表 |
| `data/events/YYYY-MM-DD/events_rejected_articles.json` | 被拒绝的文章及原因 |
| `data/logs/YYYY-MM-DD/extract_events.log` | 运行日志 |

## 事件类型枚举

| event_type | 说明 |
|------------|------|
| `platform_move` | 平台动作（政策、规则、资源位变化） |
| `platform_rule` | 平台规则变化 |
| `competitor_move` | 竞对动作 |
| `brand_move` | 品牌动作 |
| `category_trend` | 品类趋势 |
| `channel_shift` | 渠道变迁 |
| `consumer_scene` | 消费场景变化 |
| `data_signal` | 数据信号 |
| `policy_signal` | 政策信号 |
| `background_only` | 仅有背景信息 |
| `unclear` | 不确定 |

## 业务变量枚举

流量、转化率、客单价、复购、价格、补贴、毛利、货盘、SKU、履约、
会员、私域、活动、投流、门店覆盖、平台资源位、组织执行、竞争格局、
品类机会、风险

## 调用方式

```bash
python skills/extract_events/extract_events.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26 \
  --use-llm true \
  --max-articles 20
```

```python
from skills.extract_events.extract_events import extract_events
result = extract_events(
    project_root="/app/working/projects/watsons-retail-intel",
    date="2026-04-26",
)
```

## LLM 配置

复用 `skills/utils/llm_client.py`，默认使用：

- 模型：`LongCat-Flash-Thinking`
- Base URL：`https://api.longcat.chat/openai`
- 多 Key 轮换、错误切换、降级策略与 filter 相同