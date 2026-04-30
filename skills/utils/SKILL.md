# skills/utils/llm_client — LLM 调用工具

基于 `requests` 实现，避免 OpenAI SDK 版本兼容问题。

## 何时使用

任何 Skill 需要调用 LLM（语义判断、文本生成、事件抽取等）时，
import 并使用 `LLMClient`。

## 用法

```python
from skills.utils.llm_client import get_llm_client, check_llm_config, test_llm_connection

client = get_llm_client()

result = client.chat(
    messages=[{"role": "user", "content": "..."}],
    system_prompt="你是一个内容相关性判断专家。",
    response_format="json",  # 期望 JSON 输出
)
# result = {"ok": True, "content": "...", "parsed": dict, "key_used": "ab**yz", ...}
# result = {"ok": False, "error": "..."}

# 安全配置检查（不输出 Key 原文）
config = check_llm_config()
# {"keys_found": True, "key_count": 6, "base_url": "...", "model": "...", ...}

# 连通性测试
test_result = test_llm_connection()
# {"llm_config_ok": True, "api_reachable": True, "parsed_json_ok": True, ...}
```

## 环境变量优先级

**Keys:**
1. `LONGCAT_API_KEYS` — 逗号分隔
2. `LONGCAT_API_KEY` — 单 Key
3. `longcat` / `longcat1` ~ `longcat5` — 6 个独立变量
4. `LLM_API_KEYS` — 逗号分隔
5. `LLM_API_KEY` — 单 Key

**Base URL:**
1. `LONGCAT_BASE_URL`
2. `LLM_BASE_URL`
3. `LLM_API_URL`
4. 默认值: `https://api.longcat.chat/openai`

**Model:**
1. `LONGCAT_MODEL`
2. `LLM_MODEL`
3. 默认值: `LongCat-Flash-Thinking`

## Key 轮换

- 多 Key round-robin 轮换
- 401/403 → 标记 Key 耗尽，切换下一个 Key
- 429 → 简单退避后重试
- 404 → 直接返回错误（endpoint/模型名问题）
- 所有 Key 耗尽 → 返回 ok=False，调用方降级

## 降级策略

- Key 不可用 → 切换下一个 Key
- 所有 Key 不可用 → 返回 ok=False
- 单次调用超时 → 记录日志，尝试下一个 Key
- JSON 解析失败 → `robust_json_extract()` 尝试3层提取
- 所有 Key 失败 → 调用方应降级为纯规则模式