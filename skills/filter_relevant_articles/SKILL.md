# filter_relevant_articles

对采集的原始文章进行"规则过滤 + LLM 语义复核"，分为三类池。

## 何时使用

- 采集完成后，对 `data/raw/YYYY-MM-DD/raw_articles.json` 进行过滤
- 将文章分为 cleaned（主候选）、reference（参考）、rejected（丢弃）三类
- LLM 不可用时自动降级为纯规则过滤

## 输入

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_root` | str | ✅ | 项目根目录 |
| `date` | str | ✅ | 日期 YYYY-MM-DD |
| `raw_file` | str | ❌ | 覆盖输入文件路径 |
| `use_llm` | bool | ❌ | 是否使用 LLM，默认 True |
| `llm_mode` | str | ❌ | LLM 模式：`borderline_only`（默认）或 `all` |

## 输出文件

| 文件 | 路径 | 说明 |
|------|------|------|
| 主候选 | `data/cleaned/YYYY-MM-DD/cleaned_articles.json` | rule_score ≥ 6 或 LLM 推荐 main |
| 参考 | `data/cleaned/YYYY-MM-DD/reference_articles.json` |行业背景/old 但有价值 |
| 丢弃 | `data/rejected/YYYY-MM-DD/rejected_articles.json` | 无关/缺少关键字段 |
| 日志 | `data/logs/YYYY-MM-DD/filter_relevant_articles.log` | 运行日志 |

## 规则加分（合并 title + summary + content 判断）

| 类别 | 词例 | 加分 |
|------|------|------|
| 直接命中屈臣氏 | 屈臣氏、Watsons | +5 |
| 即时零售平台 | 美团闪购、京东秒送、即时零售等 | +4 |
| 美妆个护品类 | 美妆、个护、护肤、彩妆等 | +3 |
| 竞对 | 丝芙兰、万宁、调色师等 | +3 |
| B2C/To B 渠道 | 天猫官旗、京东自营等 | +2 |
| 经营变量 | 流量、转化率、SKU等 | +1 |
| source_tier=1 | | +2 |
| source_tier=2 | | +1 |
| time_status=in_window | | +2 |
| time_status=near_window | | +1 |

## 规则减分

| 类别 | 减分 |
|------|------|
| 泛科技/泛财经/泛宏观 | -3 |
| 汽车、房产、游戏、芯片、AI大模型等 | -3 |
| 招聘、公益、投诉、免责声明、导航页 | -5 |
| time_status=old | -2 |
| title/url 为空 | -5 |
| content+summary 极短且关键词空 | -2 |

## 初步决策阈值

| rule_score | rule_decision |
|------------|---------------|
| ≥ 6 | keep |
| 3–5 | review |
| < 3 | reject |

## LLM 语义复核

- 仅处理 `rule_decision=review` 的边界文章
- `source_tier` 高但 `rule_score` 低也可送入
- 模型：`longcat-flash-thinking`
- Key 轮换：6 个 Longcat Key，round-robin
- LLM 不可用时自动降级为纯规则
- 单条失败不中断全局

## CLI

```bash
python filter_relevant_articles.py \
  --project-root /app/working/projects/watsons-retail-intel \
  --date 2026-04-26 \
  --use-llm true \
  --llm-mode borderline_only
```

## 函数

```python
from skills.filter_relevant_articles.filter_relevant_articles import filter_relevant_articles

result = filter_relevant_articles(
    project_root="/app/working/projects/watsons-retail-intel",
    date="2026-04-26",
)
```