"""
skills.utils — 共享工具包

llm_client: LLM 调用客户端，支持多 Key 轮换、降级、JSON 解析。
"""

from .llm_client import LLMClient, get_llm_client