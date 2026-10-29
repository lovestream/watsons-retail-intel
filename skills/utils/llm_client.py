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

主 Provider: OpenCode deepseek-v4-flash-free（4 入口轮换：3 自定义 Key + 1 内置 keyless）。
付费兜底 gpt-5.4-xhigh 仅在 ALLOW_PAID_FALLBACK=1 时启用；默认禁用，绝不烧钱。

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

# ── 主 Provider: SiliconFlow（DeepSeek 文本模型）──
# 用户 2026-10-29 明确要求：禁止 OpenCode，只允许 SiliconFlow + RightCode。
# 密钥来自环境变量 siliconflow_api_key / rightcode_bybt。
SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
SILICONFLOW_MODEL = os.environ.get("PIPELINE_LLM_MODEL",
                                   "deepseek-ai/DeepSeek-V4.1-Flash")
SILICONFLOW_KEY_ENV = "siliconflow_api_key"
LLM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ── 默认（主 Provider = OpenCode） ──
DEFAULT_MODEL = SILICONFLOW_MODEL
DEFAULT_BASE_URL = SILICONFLOW_BASE_URL
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.3
DEFAULT_TIMEOUT = 180  # 秒（thinking 模型需要更长等待）
DEFAULT_RETRIES = 2
# 瞬时错误(连接中断/超时/空响应)对每个 Key 的重试轮数。
# 偶发 RemoteDisconnected 时，多轮重试+退避通常能成功，
# 避免动辄放弃主模型转付费兜底。
TRANSIENT_ROUNDS = 2
TRANSIENT_BACKOFF_BASE = 1.5  # 秒，指数退避基数
TRANSIENT_BACKOFF_MAX = 10.0  # 秒，单次退避上限
# 主 Provider 重试的墙钟硬上限：超过后停止重试、直接走 fallback。
# 防止多 Key 反复 180s 超时+退避累积成数十分钟卡死单次调用。
MAX_RETRY_WALL_SECONDS = 300  # 5 分钟
DAILY_TOKEN_LIMIT_PER_KEY = 5_000_000

# ── 备用 Provider: RightCode（用户授权的唯一备用，主 Provider 失败时使用）──
FALLBACK_KEY_ENV = "rightcode_bybt"
FALLBACK_BASE_URL = os.environ.get("RIGHTCODE_BASE_URL",
                                   "https://right.codes/codex/v1")
FALLBACK_MODEL = os.environ.get("RIGHTCODE_MODEL", "deepseek-flash")

# ── 付费兜底总开关 ──
# 默认彻底禁用付费兜底(gpt-5.4-xhigh 烧钱)。仅当 OpenCode 全部入口不可用且你
# 明确同意花钱时，临时设 ALLOW_PAID_FALLBACK=1 启用。正常情况绝不走付费。
ALLOW_PAID_FALLBACK_ENV = "ALLOW_PAID_FALLBACK"


def _load_allow_paid_fallback() -> bool:
    """是否允许在 OpenCode 全灭时走付费兜底。默认 False(禁用)。"""
    val = os.environ.get(ALLOW_PAID_FALLBACK_ENV, "0").strip().lower()
    return val in ("1", "true", "yes", "on")


# ── 强制兜底开关 ──
# 仅当 OpenCode 全部入口不可用且你同意花钱时，临时设
# FORCE_FALLBACK_LLM=1 + ALLOW_PAID_FALLBACK=1 强制走付费 gpt。默认关闭(0)。
def _load_force_fallback() -> bool:
    val = os.environ.get("FORCE_FALLBACK_LLM", "0").strip().lower()
    return val in ("1", "true", "yes", "on")


# ===================== 环境变量加载 =====================

def _load_provider_endpoints() -> List[Dict[str, str]]:
    """加载 Provider 入口：SiliconFlow(主) + RightCode(备)。

    每个元素: {"name", "tier", "base_url", "model", "key"}。
    没有 OpenCode 入口——用户已明确禁止该渠道。
    """
    entries: List[Dict[str, str]] = []
    sf_key = (os.environ.get(SILICONFLOW_KEY_ENV, "")
              or os.environ.get("SILICONFLOW_API_KEY", "")).strip()
    if sf_key:
        entries.append({"name": "siliconflow", "tier": "primary",
                        "base_url": SILICONFLOW_BASE_URL,
                        "model": SILICONFLOW_MODEL, "key": sf_key})
    rc_key = os.environ.get(FALLBACK_KEY_ENV, "").strip()
    if rc_key:
        entries.append({"name": "rightcode", "tier": "fallback",
                        "base_url": FALLBACK_BASE_URL,
                        "model": FALLBACK_MODEL, "key": rc_key})
    return entries


def _load_opencode_keys() -> List[Dict[str, str]]:
    """已废弃：保留函数名仅为兼容旧调用，内部改为 Provider 入口。"""
    return _load_provider_endpoints()


def _deprecated_opencode_loader() -> List[Dict[str, str]]:
    """旧 OpenCode 加载逻辑（保留源码仅为历史追溯，不再被调用）。

    优先级:
    1. OPENCODE_API_KEYS 环境变量（JSON dict 或逗号分隔字符串）
    2. config/opencode_keys.json（dict {name: key} 或 list）
    3. 空列表（仅保留内置 keyless）

    末尾始终附加内置 keyless 入口（"key": ""，请求不带 Authorization 头），
    使轮换池始终有兜底入口。总入口 = 3 自定义 Key + 1 内置 keyless。
    """
    entries: List[Dict[str, str]] = []

    env_val = os.environ.get("OPENCODE_API_KEYS", "").strip()
    if env_val:
        try:
            parsed = json.loads(env_val)
            if isinstance(parsed, dict):
                items = list(parsed.items())
            else:
                items = [("env%d" % i, v) for i, v in enumerate(str(parsed).split(","))]
        except Exception:
            items = [("env%d" % i, v) for i, v in enumerate(env_val.split(","))]
        for name, key in items:
            if isinstance(key, str) and key.strip():
                entries.append({"name": str(name), "key": key.strip()})
    else:
        for candidate in (os.environ.get("OPENCODE_KEYS_FILE", "").strip(),
                          OPENCODE_KEYS_FILE):
            if not candidate:
                continue
            if os.path.exists(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        for name, key in data.items():
                            if isinstance(key, str) and key.strip():
                                entries.append({"name": str(name), "key": key.strip()})
                    elif isinstance(data, list):
                        for i, key in enumerate(data):
                            if isinstance(key, str) and key.strip():
                                entries.append({"name": "key%d" % i, "key": key})
                except Exception as e:
                    logger.warning("OpenCode 配置读取失败 %s: %s" % (candidate, e))
                break

    # 内置 keyless 入口始终保留，保证轮换池有兜底
    if not any(e["key"] == "" for e in entries):
        entries.append({"name": "opencode-keyless", "key": ""})
    return entries


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
    """基于 requests 的 OpenCode LLM 调用客户端。"""

    def __init__(self):
        # ── 主 Provider: OpenCode 轮换入口（3 Key + 1 keyless） ──
        self.endpoints: List[Dict[str, str]] = _load_provider_endpoints()
        self.keys: List[str] = [e["key"] for e in self.endpoints]  # 兼容旧字段
        self.model: str = SILICONFLOW_MODEL
        self.base_url: str = SILICONFLOW_BASE_URL
        self.timeout: int = DEFAULT_TIMEOUT
        self.max_retries: int = DEFAULT_RETRIES

        # ── 统计 ──
        self.total_calls: int = 0
        self.total_failures: int = 0
        self.token_usage: Dict[str, int] = {}
        self.error_keys: Dict[str, str] = {}  # key_masked -> last_error
        self._exhausted: Dict[str, set] = {"primary": set()}  # pool -> {idx}

        # ── 线程安全 ──
        self._lock = threading.Lock()
        self._pos: Dict[str, int] = {"primary": 0}

        # ── 付费兜底 Provider（贵，仅在 OpenCode 全部入口失败时启用） ──
        self._fallback_key = os.environ.get(FALLBACK_KEY_ENV, "").strip()
        self._fallback_url = FALLBACK_BASE_URL
        self._fallback_model = FALLBACK_MODEL
        # §20 fallback 成本限制：本进程内付费兜底调用计数（调用方可读取并设门禁）
        self._paid_fallback_calls: int = 0
        # §20: 付费兜底次数上限（0=不限制，保持旧行为；周期报告生成器会设置）
        self._max_paid_fallback_calls: int = 0

        # 付费兜底总开关：默认彻底禁用。仅当 OpenCode 全灭且你明确同意花钱时
        # 设 ALLOW_PAID_FALLBACK=1 才启用付费兜底(gpt-5.4-xhigh 烧钱)。
        # RightCode 是用户授权的唯一备用 Provider，不需要付费开关；
        # 旧开关保留仅为兼容调用方读取。
        self._allow_paid = True

        # 强制兜底：立即走付费兜底，跳过 OpenCode
        self._force_fallback = _load_force_fallback()
        if self._force_fallback and (not self._allow_paid):
            logger.warning(
                "FORCE_FALLBACK_LLM=1 但 ALLOW_PAID_FALLBACK 未开启，"
                "禁用付费强制兜底，回退正常路由")
            self._force_fallback = False
        elif self._force_fallback and self._fallback_key:
            logger.warning(
                f"付费强制兜底已开启(ALLOW_PAID_FALLBACK=1) → "
                f"所有调用直接使用 {self._fallback_model}（跳过免费池）")
        elif self._force_fallback and not self._fallback_key:
            logger.warning("FORCE_FALLBACK_LLM=1 但缺少备用 Key，回退到正常路由")
            self._force_fallback = False

        if requests is None:
            logger.error("requests 库未安装，LLM 调用不可用")

    @property
    def available(self) -> bool:
        """是否有可用的主(OpenCode) Key。付费备用 Provider 不计入可用性。"""
        return bool(self.keys) and len(self.keys) > len(self._exhausted["primary"])

    @property
    def available_keys(self) -> int:
        """可用主(OpenCode) Key 数量。"""
        return max(0, len(self.keys) - len(self._exhausted["primary"]))

    def _build_endpoint(self, base_url: str) -> str:
        """构建完整 API endpoint。"""
        base = str(base_url).rstrip('/')
        if base.endswith('/v1/chat/completions'):
            return base
        if base.endswith('/v1'):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _get_endpoint(self) -> str:
        """构建主 Provider 的完整 API endpoint（兼容旧代码）。"""
        return self._build_endpoint(self.base_url)

    def _next_entry(self, pool: str):
        """Round-robin 获取下一个入口。返回 (idx, entry) 或 (None, None)。
        pool 当前固定为 "primary"(OpenCode)。"""
        entries = self.endpoints
        exhausted = self._exhausted.setdefault(pool, set())
        n = len(entries)
        if n == 0:
            return None, None
        with self._lock:
            pos = self._pos.get(pool, 0)
            for _ in range(n):
                idx = pos % n
                pos += 1
                if idx not in exhausted:
                    self._pos[pool] = pos
                    return idx, entries[idx]
        return None, None

    def _mark_exhausted(self, pool: str, idx: int, key: str, reason: str = ""):
        """标记某池的某个入口已耗尽（402/鉴权失败等本轮不可恢复）。"""
        with self._lock:
            self._exhausted.setdefault(pool, set()).add(idx)
            self.error_keys[_mask_key(key)] = "%s:%s" % (pool, reason)
            logger.warning("池 %s 的 Key %s 已标记为耗尽: %s"
                           % (pool, _mask_key(key), reason))

    def _transient_backoff(self, failure_count: int):
        """瞬时错误后的指数退避。

        failure_count 为累计连续瞬时失败次数，退避时间随之指数增长（带上限）。
        让 OpenCode 偶发的网络抖动有时间恢复，而不是连续猛打后直接放弃。
        """
        if failure_count <= 0:
            return
        delay = min(
            TRANSIENT_BACKOFF_BASE * (2 ** (failure_count - 1)),
            TRANSIENT_BACKOFF_MAX,
        )
        time_mod.sleep(delay)

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

        路由顺序（免费优先，绝不悄悄烧钱）:
          1. OpenCode deepseek-v4-flash-free（主池，4 入口轮换: 3 Key + 1 keyless）
          2. rightcode gpt-5.4-xhigh（付费兜底，仅 ALLOW_PAID_FALLBACK=1 且
             OpenCode 全部入口失败时启用）

        Args:
            messages: OpenAI 格式消息列表
            system_prompt: 系统提示词，会插入到 messages 最前面
            response_format: "json" 或 "text"
            temperature: 温度
            max_tokens: 最大 token 数
            model: 覆盖主 Provider 的模型名（None 则使用 self.model）

        Returns:
            {
                "ok": bool,
                "content": str,
                "parsed": dict|None,
                "model": str,
                "usage": dict,
                "key_used": str,
                "error": str,
            }
        """
        # ── 实际使用模型（主 Provider） ──
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

        # ── 强制付费兜底（FORCE_FALLBACK_LLM=1 + ALLOW_PAID_FALLBACK=1） ──
        if self._force_fallback and self._fallback_key:
            fb_messages = []
            if system_prompt:
                fb_messages.append({"role": "system", "content": system_prompt})
            fb_messages.extend(messages)
            fallback_result = self._call_fallback(
                fb_messages, temperature, max_tokens, response_format)
            if fallback_result["ok"]:
                return fallback_result
            result_base["error"] = (
                f"强制兜底失败: {fallback_result.get('error','')}")
            return result_base

        # 构建 messages
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        if response_format == "json" and not system_prompt:
            full_messages.insert(0, {
                "role": "system",
                "content": "你是严格的JSON分类器，只输出JSON，不要输出任何其他文字。"
            })

        _retry_deadline = time_mod.monotonic() + MAX_RETRY_WALL_SECONDS
        last_error = ""

        # ── 阶段 1: OpenCode（主池，免费，4 入口轮换） ──
        res, err = self._try_pool(
            full_messages, response_format, temperature, max_tokens,
            "primary", _retry_deadline, model=actual_model)
        if res and res.get("ok"):
            return res
        if err:
            last_error = "主Provider(SiliconFlow): " + err

        # ── 阶段 2: 付费兜底（仅当明确允许花钱 + 未超 §20 次数上限） ──
        if self._fallback_key and self._allow_paid:
            if (self._max_paid_fallback_calls > 0 and
                    self._paid_fallback_calls >= self._max_paid_fallback_calls):
                last_error += (
                    f" | gpt兜底达到 §20 上限({self._max_paid_fallback_calls})"
                    "，停止付费调用")
            else:
                logger.warning("主 Provider 全部失败，使用付费备用 Provider(ALLOW_PAID_FALLBACK=1)")
                fallback_result = self._call_fallback(
                    full_messages, temperature, max_tokens, response_format)
                if fallback_result.get("ok"):
                    return fallback_result
                last_error += f" | gpt兜底: {fallback_result.get('error', '')}"
        elif self._fallback_key:
            last_error += " | 备用 Provider 未启用(设 ALLOW_PAID_FALLBACK=1 启用)"

        result_base["error"] = f"所有 Provider 尝试完毕。最后错误: {last_error}"
        return result_base

    def _try_pool(self, full_messages, response_format, temperature, max_tokens,
                  pool, deadline, model):
        """尝试一个 provider 池（round-robin + 瞬时重试 + 墙钟上限）。

        pool 当前固定为 "primary"(OpenCode，4 入口轮换)。
        Returns: (result_dict_or_None, last_error_str)
        """
        entries = self.endpoints
        pool_model = self.model

        if not entries:
            return None, "池为空"
        if len(entries) - len(self._exhausted.get(pool, set())) <= 0:
            return None, "全部入口已耗尽"

        use_model = model if model else pool_model
        result = {
            "ok": False, "content": "", "parsed": None,
            "model": use_model, "usage": {}, "key_used": "", "error": "",
        }
        transient_failures = 0
        last_error = ""
        max_attempts = len(entries) * TRANSIENT_ROUNDS + 1

        for attempt in range(max_attempts):
            if time_mod.monotonic() > deadline:
                last_error = (last_error or "") + f"| 重试超过墙钟上限{MAX_RETRY_WALL_SECONDS}s"
                logger.warning("池 %s: %s", pool, last_error)
                break
            idx, entry = self._next_entry(pool)
            if entry is None:
                last_error = (last_error or "") + "| 池入口耗尽"
                break

            key = entry.get("key", "")
            endpoint = self._build_endpoint(entry.get("base_url") or self.base_url)
            if entry.get("tier") == "fallback" and not model:
                use_model = entry.get("model") or FALLBACK_MODEL
            key_label = entry.get("name") or _mask_key(key)
            with self._lock:
                self.total_calls += 1

            payload = {
                "model": use_model,
                "messages": full_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            headers = {
                "Content-Type": "application/json",
                "User-Agent": LLM_USER_AGENT,
            }
            if key:
                headers["Authorization"] = f"Bearer {key}"

            try:
                resp = requests.post(endpoint, headers=headers, json=payload,
                                     timeout=self.timeout)
            except requests.exceptions.Timeout:
                last_error = f"请求超时({self.timeout}s)"
                transient_failures += 1
                logger.warning("池 %s %s 请求超时 (瞬时 %d/%d)",
                               pool, key_label, transient_failures, max_attempts)
                self._transient_backoff(transient_failures)
                continue
            except requests.exceptions.ConnectionError as e:
                last_error = f"连接错误: {str(e)[:150]}"
                transient_failures += 1
                logger.warning("池 %s %s 连接错误: %s", pool, key_label, str(e)[:100])
                self._transient_backoff(transient_failures)
                continue
            except Exception as e:
                last_error = f"未知错误: {str(e)[:150]}"
                transient_failures += 1
                logger.warning("池 %s %s 未知错误: %s", pool, key_label, e)
                self._transient_backoff(transient_failures)
                continue

            # ── HTTP 错误处理 ──
            if resp.status_code == 401 or resp.status_code == 403:
                error_msg = f"鉴权失败({resp.status_code}): {resp.text[:150]}"
                logger.warning("池 %s %s 鉴权失败: %s", pool, key_label, resp.status_code)
                self._mark_exhausted(pool, idx, key, error_msg)
                last_error = error_msg
                continue
            if resp.status_code == 404:
                error_msg = (f"Endpoint/模型名错误(404): endpoint={endpoint}, "
                             f"model={use_model}, resp={resp.text[:150]}")
                logger.error("池 %s: %s", pool, error_msg)
                result["error"] = error_msg
                return result, error_msg
            if resp.status_code == 429:
                error_msg = f"速率限制(429): {resp.text[:150]}"
                logger.warning("池 %s %s 触发速率限制", pool, key_label)
                last_error = error_msg
                time_mod.sleep(2)
                continue
            if resp.status_code == 402:
                error_msg = f"额度不足(402): {resp.text[:150]}"
                logger.warning("池 %s %s 额度不足(402)，标记耗尽", pool, key_label)
                self._mark_exhausted(pool, idx, key, error_msg)
                last_error = error_msg
                continue
            if resp.status_code >= 400:
                error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("池 %s %s 请求失败: %s", pool, key_label, error_msg)
                last_error = error_msg
                if "unsupported model" in resp.text.lower():
                    logger.warning("模型不支持，跳过整个池 %s", pool)
                    break
                continue

            # ── 成功响应 ──
            try:
                resp_json = resp.json()
            except Exception as e:
                last_error = f"响应 JSON 解析失败: {str(e)[:80]}"
                transient_failures += 1
                self._transient_backoff(transient_failures)
                continue
            content = ""
            reasoning_content = ""
            usage = {}
            choices = resp_json.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "") or ""
                reasoning_content = msg.get("reasoning_content", "") or ""
            if not content and reasoning_content:
                logger.info("content 为空，尝试从 reasoning_content 提取最终回答")
                content = reasoning_content
            usage = resp_json.get("usage", {})

            parsed = None
            if response_format == "json" and content:
                parsed = robust_json_extract(content)
                if parsed is None:
                    logger.warning("LLM 返回 JSON 解析失败, preview=%s", content[:200])

            result.update({
                "ok": bool(content.strip()),
                "content": content,
                "parsed": parsed,
                "usage": usage,
                "key_used": _mask_key(key),
                "error": "" if content.strip() else "LLM 返回内容为空",
            })
            if not content.strip():
                last_error = "LLM 返回内容为空"
                transient_failures += 1
                logger.warning("池 %s %s 返回内容为空", pool, key_label)
                self._transient_backoff(transient_failures)
                continue
            return result, ""

        result["error"] = f"池 {pool} 尝试完毕: {last_error}"
        return result, last_error

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
                # §20 fallback 成本限制：成功付费调用计数（失败不计费）
                with self._lock:
                    self._paid_fallback_calls += 1
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
            "exhausted_keys": len(self._exhausted.get("primary", set())),
            "model": self.model,
            "base_url": self.base_url,
            "endpoint": self._get_endpoint(),
            "paid_fallback_allowed": self._allow_paid,
            "paid_fallback_calls": self._paid_fallback_calls,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "token_usage": self.token_usage,
            "error_keys": dict(self.error_keys),
        }

    def get_paid_fallback_calls(self) -> int:
        """§20: 返回本进程内付费兜底成功调用次数。"""
        return self._paid_fallback_calls

    def reset_paid_fallback_calls(self) -> None:
        """§20: 重置付费兜底计数（新报告开始时调用）。"""
        with self._lock:
            self._paid_fallback_calls = 0

    def set_max_paid_fallback_calls(self, limit: int) -> None:
        """§20: 设置付费兜底次数上限（0=不限制）。"""
        with self._lock:
            self._max_paid_fallback_calls = max(0, int(limit or 0))


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