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

## 环境变量 / 配置（2026-08 起：OpenCode 唯一 provider）

**Provider（唯一）: OpenCode `https://opencode.ai/zen/v1`, model `deepseek-v4-flash-free`**
**Keys（4 入口轮换）:**
1. `OPENCODE_API_KEYS` — JSON dict 或逗号分隔字符串
2. `config/opencode_keys.json` — dict {name: key}, 已 gitignore 不入库
3. 末尾始终附内置 keyless 入口（空 key 兜底）→ 共 3 自定义 + 1 keyless = 4 入口

**Model:** 固定 `deepseek-v4-flash-free`（OPENCODE_MODEL）

**付费兜底（默认禁用，绝不烧钱）:**
- `ALLOW_PAID_FALLBACK=1` + `rightcode_key`（`https://right.codes/codex/v1` `gpt-5.4-xhigh`）才启用。

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