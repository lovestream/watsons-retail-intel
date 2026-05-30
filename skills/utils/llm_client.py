#!/usr/bin/env python3
"""
llm_client.py — 共享 LLM 调用客户端（requests 实现）

支持:
- 多 Key round-robin 轮换
- Key 失败自动切换下一个 Key
- 429 / 401 / 403 / 404 错误处理
- 所有 Key 耗尽返回 ok=False，调用方降级
- JSON 响应自robust解析（含 Markdown 代码块提取）
- 用量统计
- 安全配置检查（不输出 Key 原文）

环境变量优先级:
  Keys:   LONGCAT_API_KEYS > LONGCAT_API_KEY > longcat~longcat5 > LLM_API_KEYS > LLM_API_KEY
  URL:    LONGCAT_BASE_URL > LLM_BASE_URL > LLM_API_URL > 默认 https://api.longcat.chat/openai
  Model:  LONGCAT_MODEL > LLM_MODEL > 默认 LongCat-Flash-Thinking

用法:
    from skills.utils.llm_client import get_llm_client, check_llm_config

    client = get_llm_client()
    result = client.chat(
        messages=[{"role": "user", "content": "..."}],
        system_prompt="你是专家。",
        response_format="json",
    )
"""

import json
import logging
import os
import re
import threading
import time as time_mod
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

logger = logging.getLogger(__name__)

# ===================== 默认配置 =====================

DEFAULT_MODEL = "LongCat-Flash-Thinking"
DEFAULT_BASE_URL = "https://api.longcat.chat/openai"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.3
DEFAULT_TIMEOUT = 180  # 秒（thinking 模型需要更长等待）
DEFAULT_RETRIES = 2
DAILY_TOKEN_LIMIT_PER_KEY = 5_000_000

# ── 备用 Provider 配置 ──
FALLBACK_KEY_ENV = "rightcode_key"
FALLBACK_BASE_URL = "https://right.codes/codex/v1"
FALLBACK_MODEL = "gpt-5.4-xhigh"


# ===================== 环境变量加载 =====================

def _load_keys_from_env() -> List[str]:
    """从环境变量加载 API Key，支持多种格式。
    
    优先级:
    1. LONGCAT_API_KEYS (逗号分隔)
    2. LONGCAT_API_KEY (单 Key)
    3. longcat / longcat1 ~ longcat5
    4. LLM_API_KEYS (逗号分隔)
    5. LLM_API_KEY (单 Key)
    """
    keys: List[str] = []

    # 1. LONGCAT_API_KEYS (逗号分隔)
    comma_keys = os.environ.get("LONGCAT_API_KEYS", "").strip()
    if comma_keys:
        for k in comma_keys.split(","):
            k = k.strip()
            if k:
                keys.append(k)
        if keys:
            return keys

    # 2. LONGCAT_API_KEY (单 Key)
    single_key = os.environ.get("LONGCAT_API_KEY", "").strip()
    if single_key:
        return [single_key]

    # 3. longcat / longcat1 ~ longcat5
    for var in ["longcat", "longcat1", "longcat2", "longcat3", "longcat4", "longcat5"]:
        val = os.environ.get(var, "").strip()
        if val:
            keys.append(val)
    if keys:
        return keys

    # 4. LLM_API_KEYS (逗号分隔)
    llm_keys = os.environ.get("LLM_API_KEYS", "").strip()
    if llm_keys:
        result = []
        for k in llm_keys.split(","):
            k = k.strip()
            if k:
                result.append(k)
        if result:
            return result

    # 5. LLM_API_KEY (单 Key)
    fallback = os.environ.get("LLM_API_KEY", "").strip()
    if fallback:
        return [fallback]

    return []


def _load_model_from_env() -> str:
    """优先级: LONGCAT_MODEL > LLM_MODEL > 默认值"""
    return (
        os.environ.get("LONGCAT_MODEL", "").strip()
        or os.environ.get("LLM_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def _load_base_url_from_env() -> str:
    """优先级: LONGCAT_BASE_URL > LLM_BASE_URL > LLM_API_URL > 默认值"""
    url = (
        os.environ.get("LONGCAT_BASE_URL", "").strip()
        or os.environ.get("LLM_BASE_URL", "").strip()
        or os.environ.get("LLM_API_URL", "").strip()
    )
    return url if url else DEFAULT_BASE_URL


def _mask_key(key: str) -> str:
    """安全掩码: 只显示前2位和后2位。"""
    if len(key) <= 4:
        return "****"
    return f"{key[:2]}{'*' * (len(key) - 4)}{key[-2:]}"


# ===================== JSON 解析 =====================

def robust_json_extract(text: str) -> Optional[dict]:
    """从 LLM 返回文本中健壮提取 JSON。
    
    处理场景:
    1. 纯 JSON 字符串
    2. ```json ... ``` Markdown 代码块
    3. thinking 模型中推理文本末尾包含 JSON
    4. 思考文本中间穿插 JSON
    
    Returns:
        dict if found, None if not
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. 直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. 提取 ```json ... ``` 代码块
    json_block_pattern = re.compile(r'```json\s*(\{.*?\})\s*```', re.DOTALL)
    m = json_block_pattern.search(text)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. 提取最后一个完整 JSON 对象（thinking 模型常见：
    #    推理文本结束后输出JSON，所以取最后出现的 { ... }）
    #    从后往前找最深的嵌套 { }
    last_brace = text.rfind('}')
    if last_brace != -1:
        # 从最后一个 } 往前找匹配的 {
        depth = 0
        for i in range(last_brace, -1, -1):
            if text[i] == '}':
                depth += 1
            elif text[i] == '{':
                depth -= 1
                if depth == 0:
                    # 找到匹配的 {
                    candidate = text[i:last_brace + 1]
                    try:
                        result = json.loads(candidate)
                        if isinstance(result, dict):
                            return result
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break

    # 4. 兜底：第一个 { 到最后一个 } 之间
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            result = json.loads(text[first_brace:last_brace + 1])
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # 5. 截断 JSON 修复：thinking 模型经常因为 token 不够
    #    导致 JSON 被截断（缺少闭合括号）
    #    策略：从截断点向前找最后一个完整的事件对象，闭合外层大括号
    first_brace = text.find('{')
    if first_brace != -1:
        json_fragment = text[first_brace:]
        # 尝试修复截断的 JSON
        repaired = _repair_truncated_json(json_fragment)
        if repaired is not None:
            return repaired

    return None


def _repair_truncated_json(fragment: str) -> Optional[dict]:
    """尝试修复被截断的 JSON 字符串。
    
    thinking 模型因 max_tokens 限制经常截断 JSON 输出。
    策略：
    1. 找到最后一个完整的事件对象 {...}
    2. 关闭所有未闭合的数组和大括号
    3. 尝试解析
    """
    if not fragment.strip().startswith('{'):
        return None

    # 策略1: 如果包含 "events": [...，找到最后一个完整 } 的事件
    # 然后手动闭合
    events_match = re.search(r'"events"\s*:\s*\[', fragment)
    if not events_match:
        # 简单截断修复：补全括号
        for closing in [']}', '}}', '}']:
            try:
                result = json.loads(fragment + closing)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    # 策略2: 在 events 数组中，找到最后一个完整的 { } 对
    # 即事件对象的闭合
    last_complete_event_end = -1
    event_depth = 0
    in_string = False
    escape_next = False
    
    # 从 events 数组开始扫描
    events_start = events_match.end()
    i = events_start
    
    while i < len(fragment):
        ch = fragment[i]
        
        if escape_next:
            escape_next = False
            i += 1
            continue
        
        if ch == '\\' and in_string:
            escape_next = True
            i += 1
            continue
        
        if ch == '"' and not escape_next:
            in_string = not in_string
            i += 1
            continue
        
        if in_string:
            i += 1
            continue
        
        if ch == '{':
            event_depth += 1
        elif ch == '}':
            event_depth -= 1
            if event_depth == 0:
                # 找到一个完整的事件对象
                last_complete_event_end = i
        
        i += 1
    
    if last_complete_event_end > 0:
        # 截取到最后一个完整事件对象的末尾
        # 格式: { "article_id": ..., "events": [ {...}, {...} 
        # 需要闭合: ] 和 }
        truncated = fragment[:last_complete_event_end + 1]
        
        # 计算需要补多少层大括号/方括号
        # 简单策略：闭合 events 数组和外层对象
        closings = [']}']
        # 也试试更多层的
        for extra in ['', '}', ']}']:
            for close in [']}']:
                try:
                    candidate = truncated + extra + close
                    result = json.loads(candidate)
                    if isinstance(result, dict):
                        # 删除截断后不完整的事件
                        events = result.get("events", [])
                        if isinstance(events, list):
                            result["events"] = [e for e in events if isinstance(e, dict) and e.get("fact")]
                        return result
                except (json.JSONDecodeError, ValueError):
                    continue
    
    # 策略3: 暴力补全括号
    for closing in [']}}', ']}', '}}', '}']:
        try:
            result = json.loads(fragment + closing)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    return None


# ===================== LLM 客户端 =====================

class LLMClient:
    """基于 requests 的 LongCat LLM 调用客户端。"""

    def __init__(self):
        self.keys: List[str] = _load_keys_from_env()
        self.model: str = _load_model_from_env()
        self.base_url: str = _load_base_url_from_env()
        self.current_index: int = 0
        self.timeout: int = DEFAULT_TIMEOUT
        self.max_retries: int = DEFAULT_RETRIES

        # 统计
        self.total_calls: int = 0
        self.total_failures: int = 0
        self.token_usage: Dict[str, int] = {}
        self.error_keys: Dict[str, str] = {}  # key_masked -> last_error
        self.exhausted_keys: set = set()  # indices of exhausted keys

        # 线程安全
        self._lock = threading.Lock()

        # 备用 Provider（所有主 Key 失败时兜底）
        self._fallback_key = os.environ.get(FALLBACK_KEY_ENV, "").strip()
        self._fallback_url = FALLBACK_BASE_URL
        self._fallback_model = FALLBACK_MODEL

        if requests is None:
            logger.error("requests 库未安装，LLM 调用不可用")

    @property
    def available(self) -> bool:
        """是否有可用的 Key（含备用 Provider）。"""
        primary_ok = bool(self.keys) and len(self.keys) > len(self.exhausted_keys)
        return primary_ok or bool(self._fallback_key)

    @property
    def available_keys(self) -> int:
        """可用 Key 数量。"""
        return len(self.keys) - len(self.exhausted_keys)

    def _get_endpoint(self) -> str:
        """构建完整 API endpoint。"""
        base = self.base_url.rstrip('/')
        # 如果已经包含 /v1/chat/completions，不再拼接
        if base.endswith('/v1/chat/completions'):
            return base
        # 如果以 /v1 结尾，拼接 chat/completions
        if base.endswith('/v1'):
            return f"{base}/chat/completions"
        # 否则拼接 /v1/chat/completions
        return f"{base}/v1/chat/completions"

    def _get_next_key(self) -> Optional[str]:
        """Round-robin 获取下一个 Key。"""
        if not self.keys:
            return None
        
        with self._lock:
            # 尝试找一个未耗尽的 Key
            attempts = 0
            while attempts < len(self.keys):
                key = self.keys[self.current_index % len(self.keys)]
                idx = self.current_index % len(self.keys)
                self.current_index += 1
                
                if idx not in self.exhausted_keys:
                    return key
                attempts += 1
            
            return None  # 所有 Key 都耗尽

    def _mark_key_exhausted(self, key: str, reason: str = ""):
        """标记 Key 已耗尽。"""
        with self._lock:
            for i, k in enumerate(self.keys):
                if k == key:
                    self.exhausted_keys.add(i)
                    self.error_keys[_mask_key(key)] = reason
                    logger.warning(f"Key {_mask_key(key)} 已标记为耗尽: {reason}")
                    break

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str = "",
        response_format: str = "text",
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """调用 LLM，返回标准结果 dict。
        
        Args:
            messages: OpenAI 格式消息列表
            system_prompt: 系统提示词，会插入到 messages 最前面
            response_format: "json" 或 "text"
            temperature: 温度
            max_tokens: 最大 token 数
            model: 覆盖默认模型名（None 则使用 self.model）
            
        Returns:
            {
                "ok": bool,
                "content": str,          # 原始返回文本
                "parsed": dict|None,     # 解析后的 JSON (response_format=json 时)
                "model": str,
                "usage": dict,
                "key_used": str,          # 掩码后的 Key
                "error": str,             # 错误信息
            }
        """
        # ── 实际使用模型 ──
        actual_model = model if model else self.model
        if model and model != self.model:
            logger.info(f"模型覆盖: self.model={self.model} → 实际使用={actual_model}")

        result_base = {
            "ok": False,
            "content": "",
            "parsed": None,
            "model": actual_model,
            "usage": {},
            "key_used": "",
            "error": "",
        }

        if requests is None:
            result_base["error"] = "requests 库未安装"
            return result_base

        if not self.available:
            # 主 Key 全部耗尽，尝试备用
            if self._fallback_key:
                logger.info("主 Key 全部不可用，直接使用备用 Provider")
                # 需要先构建 messages
                fb_messages = []
                if system_prompt:
                    fb_messages.append({"role": "system", "content": system_prompt})
                fb_messages.extend(messages)
                fallback_result = self._call_fallback(
                    fb_messages, temperature, max_tokens, response_format)
                if fallback_result["ok"]:
                    return fallback_result
            result_base["error"] = "无可用 API Key（含备用）"
            return result_base

        # 构建 messages
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # 如果 response_format=json，在 system_prompt 末尾追加 JSON 提示
        if response_format == "json" and system_prompt:
            # 不重复追加，如果 system_prompt 已包含 JSON 提示则跳过
            pass
        elif response_format == "json" and not system_prompt:
            full_messages.insert(0, {
                "role": "system",
                "content": "你是严格的JSON分类器，只输出JSON，不要输出任何其他文字。"
            })

        # 构建请求体
        payload = {
            "model": actual_model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        endpoint = self._get_endpoint()

        # 尝试调用 (round-robin 重试)
        last_error = ""
        max_attempts = len(self.keys) + 1  # 最多试所有 Key 各一次
        
        for attempt in range(max_attempts):
            key = self._get_next_key()
            if key is None:
                last_error = f"所有 Key 已耗尽。最后错误: {last_error}"
                break  # 跳出循环，走 fallback 逻辑

            with self._lock:
                self.total_calls += 1

            try:
                resp = requests.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                # ── HTTP 错误处理 ──
                if resp.status_code == 401 or resp.status_code == 403:
                    error_msg = f"鉴权失败({resp.status_code}): {resp.text[:200]}"
                    logger.warning(f"Key {_mask_key(key)} 鉴权失败: {resp.status_code}")
                    self._mark_key_exhausted(key, error_msg)
                    last_error = error_msg
                    continue  # 换下一个 Key

                if resp.status_code == 404:
                    error_msg = (f"Endpoint 或模型名错误(404): "
                                 f"endpoint={endpoint}, model={actual_model}, "
                                 f"response={resp.text[:200]}")
                    logger.error(error_msg)
                    # 404 说明 endpoint 或模型名有误，所有 Key 都会遇到，直接返回
                    result_base["error"] = error_msg
                    return result_base

                if resp.status_code == 429:
                    error_msg = f"速率限制(429): {resp.text[:200]}"
                    logger.warning(f"Key {_mask_key(key)} 触发速率限制")
                    # 429 不标记 Key 为耗尽，等一会重试
                    last_error = error_msg
                    time_mod.sleep(2)  # 简单退避
                    continue

                if resp.status_code >= 400:
                    error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    logger.warning(f"Key {_mask_key(key)} 请求失败: {error_msg}")
                    last_error = error_msg
                    # "Unsupported model" 说明模型名有误，所有 Key 都会遇到
                    if "unsupported model" in resp.text.lower():
                        logger.warning("模型不支持，跳过所有主 Key")
                        break
                    continue

                # ── 成功响应 ──
                resp_json = resp.json()
                content = ""
                reasoning_content = ""
                usage = {}

                # 提取内容 — 支持 thinking 模型
                # LongCat-Flash-Thinking 等推理模型返回两个字段：
                #   content: 最终回答（可能为空）
                #   reasoning_content: 思考过程（含推理步骤，可能包含最终JSON）
                choices = resp_json.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "") or ""
                    reasoning_content = msg.get("reasoning_content", "") or ""

                # 如果 content 为空但 reasoning_content 有内容，
                # 尝试从 reasoning_content 中提取 JSON（thinking 模型行为）
                if not content and reasoning_content:
                    logger.info("content 为空，尝试从 reasoning_content 提取最终回答")
                    content = reasoning_content

                # 提取 usage
                usage = resp_json.get("usage", {})

                # 解析 JSON (如果要求)
                parsed = None
                if response_format == "json" and content:
                    parsed = robust_json_extract(content)
                    if parsed is None:
                        logger.warning(
                            f"LLM 返回 JSON 解析失败, "
                            f"content_preview={content[:200]}"
                        )

                result_base.update({
                    "ok": bool(content.strip()),
                    "content": content,
                    "parsed": parsed,
                    "usage": usage,
                    "key_used": _mask_key(key),
                    "error": "" if content.strip() else "LLM 返回内容为空",
                })
                # 内容为空时尝试下一个 key 或 fallback
                if not content.strip():
                    last_error = "LLM 返回内容为空"
                    logger.warning(f"Key {_mask_key(key)} 返回内容为空，尝试下一个")
                    continue
                return result_base

            except requests.exceptions.Timeout:
                last_error = f"请求超时({self.timeout}s)"
                logger.warning(f"Key {_mask_key(key)} 请求超时")
                continue

            except requests.exceptions.ConnectionError as e:
                last_error = f"连接错误: {str(e)[:200]}"
                logger.warning(f"Key {_mask_key(key)} 连接错误: {e}")
                # 连接错误可能是 endpoint 不可达，所有 Key 都会失败
                # 但还是试下一个 Key 以防万一
                continue

            except json.JSONDecodeError as e:
                last_error = f"响应 JSON 解析失败: {str(e)[:100]}"
                logger.warning(f"Key {_mask_key(key)} 响应 JSON 解析失败: {e}")
                continue

            except Exception as e:
                last_error = f"未知错误: {str(e)[:200]}"
                logger.warning(f"Key {_mask_key(key)} 未知错误: {e}")
                continue

        # 所有 Key 都失败 — 尝试备用 Provider
        if self._fallback_key:
            logger.info(f"主 Provider 全部失败，尝试备用 Provider: {FALLBACK_BASE_URL}")
            fallback_result = self._call_fallback(
                full_messages, temperature, max_tokens, response_format)
            if fallback_result["ok"]:
                return fallback_result
            last_error = f"备用也失败: {fallback_result.get('error', '')}"

        result_base["error"] = f"所有 Key 尝试完毕。最后错误: {last_error}"
        return result_base

    def _call_fallback(self, messages, temperature, max_tokens,
                       response_format) -> Dict[str, Any]:
        """调用备用 Provider（rightcode）。"""
        result = {
            "ok": False, "content": "", "parsed": None,
            "model": self._fallback_model, "usage": {},
            "key_used": f"fallback:{_mask_key(self._fallback_key)}",
            "error": "",
        }
        base = self._fallback_url.rstrip("/")
        if base.endswith("/v1"):
            endpoint = f"{base}/chat/completions"
        else:
            endpoint = f"{base}/v1/chat/completions"

        payload = {
            "model": self._fallback_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        try:
            resp = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self._fallback_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                result["error"] = f"Fallback HTTP {resp.status_code}: {resp.text[:200]}"
                return result

            resp_json = resp.json()
            choices = resp_json.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "") or ""
                # fallback 模型可能也有 reasoning
                if not content:
                    content = msg.get("reasoning_content", "") or ""
            else:
                content = ""

            usage = resp_json.get("usage", {})
            parsed = None
            if response_format == "json" and content:
                parsed = robust_json_extract(content)

            result.update({
                "ok": bool(content.strip()),
                "content": content,
                "parsed": parsed,
                "usage": usage,
            })
            if content.strip():
                logger.info(f"备用 Provider 调用成功: {len(content)} 字符")
            return result

        except Exception as e:
            result["error"] = f"Fallback 异常: {str(e)[:200]}"
            return result

    def get_status(self) -> Dict[str, Any]:
        """返回客户端状态。"""
        return {
            "available": self.available,
            "available_keys": self.available_keys,
            "total_keys": len(self.keys),
            "exhausted_keys": len(self.exhausted_keys),
            "model": self.model,
            "base_url": self.base_url,
            "endpoint": self._get_endpoint(),
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "token_usage": self.token_usage,
            "error_keys": dict(self.error_keys),
        }


# ===================== 单例 =====================

_llm_client_instance: Optional[LLMClient] = None
_llm_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端单例。"""
    global _llm_client_instance
    with _llm_client_lock:
        if _llm_client_instance is None:
            _llm_client_instance = LLMClient()
        return _llm_client_instance


def reset_llm_client():
    """重置单例（测试用）。"""
    global _llm_client_instance
    with _llm_client_lock:
        _llm_client_instance = None


# ===================== 配置检查 =====================

def check_llm_config() -> Dict[str, Any]:
    """安全检查 LLM 配置，不输出 Key 原文。
    
    Returns:
        {
            "keys_found": bool,
            "key_count": int,
            "base_url": str,
            "model": str,
            "endpoint": str,
            "key_masks": List[str],
        }
    """
    client = get_llm_client()
    return {
        "keys_found": bool(client.keys),
        "key_count": len(client.keys),
        "base_url": client.base_url,
        "model": client.model,
        "endpoint": client._get_endpoint(),
        "key_masks": [_mask_key(k) for k in client.keys],
    }


def test_llm_connection() -> Dict[str, Any]:
    """向 LLM 发送最小测试请求，验证连通性。
    
    Returns:
        {
            "llm_config_ok": bool,
            "api_reachable": bool,
            "model": str,
            "parsed_json_ok": bool,
            "error_message": str,
        }
    """
    client = get_llm_client()
    
    result = {
        "llm_config_ok": False,
        "api_reachable": False,
        "model": client.model,
        "parsed_json_ok": False,
        "error_message": "",
    }

    # 1. 检查配置
    if not client.keys:
        result["error_message"] = "未找到任何 API Key"
        return result
    
    result["llm_config_ok"] = True

    # 2. 尝试调用
    try:
        resp = client.chat(
            messages=[{
                "role": "user",
                "content": '请只输出：{"ok": true, "label": "test"}'
            }],
            system_prompt="你是严格的JSON分类器，只输出JSON。",
            response_format="json",
            temperature=0.1,
            max_tokens=800,
        )

        if resp.get("ok"):
            result["api_reachable"] = True
            
            parsed = resp.get("parsed")
            if parsed and isinstance(parsed, dict):
                result["parsed_json_ok"] = parsed.get("ok") == True
                if not result["parsed_json_ok"]:
                    result["error_message"] = f"JSON 解析成功但内容不含 ok=true，返回: {json.dumps(parsed, ensure_ascii=False)[:200]}"
            else:
                content_preview = resp.get("content", "")[:200]
                result["error_message"] = f"JSON 解析失败，原始内容: {content_preview}"
        else:
            result["error_message"] = resp.get("error", "未知错误")

    except Exception as e:
        result["error_message"] = f"调用异常: {str(e)[:200]}"

    return result