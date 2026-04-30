# analyze_business_impact — 经营分析技能

## 功能

读取 `events_scored.json`，对每条评分事件进行屈臣氏电商经营影响分析，
输出 `events_analyzed.json`。

## 输入

| 文件 | 路径 | 说明 |
|------|------|------|
| events_scored.json | `data/events/{date}/events_scored.json` | 评分后的事件 |
| scoring.yaml | `config/scoring.yaml` | 来源/权重配置（辅助） |

## 输出

| 文件 | 路径 | 说明 |
|------|------|------|
| events_analyzed.json | `data/events/{date}/events_analyzed.json` | 经营分析后的事件 |
| analyze_business_impact.log | `data/logs/{date}/analyze_business_impact.log` | 日志 |

## 每条事件新增字段

```yaml
business_analysis:
  impact_type: opportunity|risk|watch|noise
  affected_channels: []
  affected_business_variables: []
  watsons_impact: ""
  recommended_action: ""
  action_level: immediate|test|watch|archive
  owner_hint: ""
  tracking_metrics: []
  follow_up_questions: []
  confidence: high|medium|low
  downgrade_reasons: []
```

## 硬规则约束

1. priority=ARCHIVE → action_level=archive
2. confidence=low → action_level 不得为 immediate
3. extraction_method=rule_fallback → action_level 不得为 immediate
4. event_type=unclear → action_level 不得为 immediate
5. source_credibility < 2 → action_level 不得为 immediate
6. recommended_action 为空 → action_level=watch
7. tracking_metrics 为空 → 补充 follow_up_questions 或降级为 watch

## CLI

```bash
python skills/analyze_business_impact/analyze_business_impact.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26 \
  --use-llm true

# 测试 LLM 连接
python skills/analyze_business_impact/analyze_business_impact.py --test-llm
```

## 依赖

- `skills/utils/llm_client.py`