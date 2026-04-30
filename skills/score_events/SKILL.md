# score_events — 事件评分技能

## 功能

对 `extract_events` 输出的 `events_raw.json` 中的每个事件进行
八维度评分、硬降级判定、分级排序，输出 `events_scored.json`。

## 输入

| 文件 | 路径 | 说明 |
|------|------|------|
| events_raw.json | `data/events/{date}/events_raw.json` | 事件抽取结果 |
| scoring.yaml | `config/scoring.yaml` | 评分权重、阈值、来源映射 |

## 输出

| 文件 | 路径 | 说明 |
|------|------|------|
| events_scored.json | `data/events/{date}/events_scored.json` | 评分后的事件 |
| score_events.log | `data/logs/{date}/score_events.log` | 评分日志 |

## 八维度评分 (0-5)

| 维度 | 权重 | 说明 |
|------|------|------|
| strategic_importance | 0.18 | 战略重要性 |
| watsons_relevance | 0.24 | 屈臣氏相关度 |
| impact_scope | 0.12 | 影响范围 |
| source_credibility | 0.12 | 来源可信度 |
| data_richness | 0.10 | 数据丰富度 |
| actionability | 0.14 | 可执行性 |
| time_sensitivity | 0.06 | 时效性 |
| novelty | 0.04 | 新颖度 |

## 分级阈值

| 优先级 | 加权总分 |
|--------|----------|
| P0 | ≥ 4.3 |
| P1 | ≥ 3.6 |
| P2 | ≥ 2.8 |
| ARCHIVE | < 2.8 |

## 硬降级规则

1. `confidence=low` → 最高 P2
2. `extraction_method=rule_fallback` → 最高 P2
3. `source_credibility < 2` → 最高 P2
4. `watsons_relevance < 3` → 最高 P2
5. `event_type=background_only` → 最高 P2
6. `event_type=unclear` → 最高 P2
7. `source_url` 缺失 → 直接 ARCHIVE
8. `fact` 为空 → 直接 ARCHIVE
9. `evidence_text` 为空 → 最高 P2

降级原因写入 `downgrade_reasons`。

## 排序规则

events_scored.json 按以下顺序排序：
1. priority: P0 > P1 > P2 > ARCHIVE
2. weighted_score 从高到低
3. source_credibility 从高到低
4. data_richness 从高到低
5. time_sensitivity 从高到低

## CLI

```bash
python skills/score_events/score_events.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26
```

## 依赖

- `skills/utils/llm_client.py` (共享 LLM 客户端，当前版本为纯规则评分，未使用 LLM)
- `config/scoring.yaml`

## 测试结果 (2026-04-26)

11 条事件评分结果：
- P0: 0 (无事件达到 P0 阈值 4.3)
- P1: 3 (高相关+高数据+高可执行)
- P2: 7 (含 2 条硬降级：confidence=low)
- ARCHIVE: 1 (wr=1, 弱相关)