"""
并行处理工具模块

为 pipeline 中的 LLM 调用和 HTTP 请求提供统一的并行处理能力。

核心策略:
- 使用 concurrent.futures.ThreadPoolExecutor 实现线程并行
- LLM key 轮换: 自动分配不同的 key 给不同 worker
- 默认并发数: 6 (对应 6 个 Longcat key)
- 失败重试: 每个任务最多重试 1 次

使用方式:
    from skills.utils.parallel import parallel_llm_map, parallel_http_map
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 默认并行数 ──
DEFAULT_MAX_WORKERS = 6
DEFAULT_RETRY = 1


def parallel_llm_map(
    items: List[Any],
    process_fn: Callable,
    max_workers: int = DEFAULT_MAX_WORKERS,
    retry: int = DEFAULT_RETRY,
    desc: str = "LLM 并行处理",
    key_rotator: Optional[Any] = None,
    key_field: str = "api_key",
) -> Tuple[List[Any], Dict[str, int]]:
    """并行处理 LLM 调用。

    Args:
        items: 待处理的项目列表（文章/事件等）
        process_fn: 处理函数，签名为 (item, idx) -> result
            result 须为 dict，至少包含 "ok" 和 "data" 字段
        max_workers: 最大并行数，默认 6
        retry: 失败重试次数，默认 1
        desc: 日志描述
        key_rotator: 可选的 key 轮换器，会按 worker 分配 key
        key_field: 如果 key_rotator 存在，传给 process_fn 的 key 参数名

    Returns:
        (results, stats) 其中:
        - results: 与 items 等长的结果列表（保持原序）
        - stats: {"success": N, "failed": N, "fallback": N, "total": N}
    """
    total = len(items)
    if total == 0:
        return [], {"success": 0, "failed": 0, "fallback": 0, "total": 0}

    # 单条或极少则直接串行
    if total <= 2:
        logger.info(f"[{desc}] 仅 {total} 条，串行处理")
        results = []
        stats = {"success": 0, "failed": 0, "fallback": 0, "total": total}
        for i, item in enumerate(items):
            r = process_fn(item, i)
            if r.get("ok"):
                stats["success"] += 1
            elif r.get("fallback"):
                stats["fallback"] += 1
            else:
                stats["failed"] += 1
            results.append(r)
        return results, stats

    logger.info(f"[{desc}] 开始并行处理 {total} 条，max_workers={max_workers}")

    # 预分配 key（如果 key_rotator 可用）
    key_assignments = {}
    if key_rotator and hasattr(key_rotator, 'available_keys') and key_rotator.available_keys:
        keys = key_rotator.available_keys
        for i in range(total):
            key_assignments[i] = keys[i % len(keys)]
        logger.info(f"[{desc}] Key 分配: {len(keys)} keys, {total} tasks")

    results = [None] * total
    stats = {"success": 0, "failed": 0, "fallback": 0, "total": total}
    stats_lock = threading.Lock()

    def _process_with_retry(item, idx):
        """带重试的并行处理包装"""
        kwargs = {}
        if idx in key_assignments:
            kwargs[key_field] = key_assignments[idx]

        for attempt in range(retry + 1):
            try:
                r = process_fn(item, idx, **kwargs) if kwargs else process_fn(item, idx)
                if r is not None:
                    return r
                # None 返回值表示失败
                if attempt < retry:
                    logger.debug(f"[{desc}] item {idx} 返回 None，重试 {attempt+1}/{retry}")
                    time.sleep(0.5)
                    # 重试时换 key
                    if key_rotator and hasattr(key_rotator, 'get_key'):
                        new_key = key_rotator.get_key()
                        if new_key:
                            kwargs[key_field] = new_key
            except Exception as e:
                if attempt < retry:
                    logger.debug(f"[{desc}] item {idx} 异常: {e}，重试 {attempt+1}/{retry}")
                    time.sleep(1.0)
                else:
                    logger.warning(f"[{desc}] item {idx} 最终失败: {e}")
                    return {"ok": False, "data": None, "error": str(e)}
        return {"ok": False, "data": None, "error": "all retries exhausted"}

    # 并行执行
    effective_workers = min(max_workers, total)
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_idx = {}
        for i, item in enumerate(items):
            future = executor.submit(_process_with_retry, item, i)
            future_to_idx[future] = i

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result(timeout=120)  # 单条超时 2 分钟
                results[idx] = result
                with stats_lock:
                    if result is None:
                        stats["failed"] += 1
                    elif result.get("ok"):
                        stats["success"] += 1
                    elif result.get("fallback"):
                        stats["fallback"] += 1
                    else:
                        stats["failed"] += 1
            except Exception as e:
                logger.warning(f"[{desc}] item {idx} future 异常: {e}")
                results[idx] = {"ok": False, "data": None, "error": str(e)}
                with stats_lock:
                    stats["failed"] += 1

    elapsed = time.time() - start_time
    logger.info(
        f"[{desc}] 完成: {stats['success']} 成功, {stats['failed']} 失败, "
        f"{stats['fallback']} 兜底, 耗时 {elapsed:.1f}s (并行度={effective_workers})"
    )

    return results, stats


def parallel_http_map(
    items: List[Dict],
    request_fn: Callable,
    max_workers: int = DEFAULT_MAX_WORKERS,
    timeout: int = 30,
    desc: str = "HTTP 并行请求",
) -> Tuple[List[Any], Dict[str, int]]:
    """并行 HTTP 请求（用于 Tavily 等搜索 API）。

    Args:
        items: 请求参数列表，每个元素传给 request_fn
        request_fn: 请求函数，签名为 (item, idx) -> (result_data, error)
            result_data 是成功返回的数据
            error 是错误信息字符串，成功时为 None
        max_workers: 最大并行数，默认 6
        timeout: 单个请求超时秒数，默认 30
        desc: 日志描述

    Returns:
        (results, stats) 其中:
        - results: 与 items 等长的结果列表（保持原序）
        - stats: {"success": N, "failed": N, "total": N, "elapsed_seconds": float}
    """
    total = len(items)
    if total == 0:
        return [], {"success": 0, "failed": 0, "total": 0, "elapsed_seconds": 0}

    logger.info(f"[{desc}] 开始并行请求 {total} 条，max_workers={max_workers}")

    results = [None] * total
    stats = {"success": 0, "failed": 0, "total": total}
    stats_lock = threading.Lock()

    start_time = time.time()

    effective_workers = min(max_workers, total)

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_idx = {}
        for i, item in enumerate(items):
            future = executor.submit(request_fn, item, i)
            future_to_idx[future] = i

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result_data, error = future.result(timeout=timeout + 10)
                results[idx] = result_data
                with stats_lock:
                    if error is None:
                        stats["failed"] += 1  # error is None but result_data could be None
                    # 这里我们用 result_data 是否为空来判断
            except Exception as e:
                logger.warning(f"[{desc}] request {idx} 异常: {e}")
                results[idx] = None
                with stats_lock:
                    stats["failed"] += 1

    # 用更简单的逻辑重统计
    success_count = sum(1 for r in results if r is not None)
    stats["success"] = success_count
    stats["failed"] = total - success_count
    stats["elapsed_seconds"] = round(time.time() - start_time, 2)

    logger.info(
        f"[{desc}] 完成: {success_count}/{total} 成功, "
        f"耗时 {stats['elapsed_seconds']}s (并行度={effective_workers})"
    )

    return results, stats


def parallel_source_map(
    sources: List[Dict],
    collect_fn: Callable,
    max_workers: int = 4,
    timeout: int = 120,
    desc: str = "源采集并行",
) -> Tuple[List[Any], Dict[str, int]]:
    """并行数据源采集（用于 collect_daily_articles 的源级别并行）。

    Args:
        sources: 数据源配置列表
        collect_fn: 采集函数，签名为 (source, idx) -> (articles, stats)
        max_workers: 最大并行数，默认 4（避免被反爬）
        timeout: 单源超时秒数，默认 120
        desc: 日志描述

    Returns:
        (all_articles, source_stats_dict) 其中:
        - all_articles: 所有源的合并文章列表
        - source_stats_dict: {source_id: stats}
    """
    total = len(sources)
    if total == 0:
        return [], {}

    # 少量源串行即可
    if total <= 3:
        logger.info(f"[{desc}] 仅 {total} 个源，串行处理")
        all_articles = []
        source_stats = {}
        for i, src in enumerate(sources):
            try:
                articles, stats = collect_fn(src, i)
                all_articles.extend(articles)
                source_stats[src.get("id", src.get("name", f"src_{i}"))] = stats
            except Exception as e:
                logger.warning(f"[{desc}] 源 {src.get('id', '?')} 采集异常: {e}")
        return all_articles, source_stats

    logger.info(f"[{desc}] 开始并行采集 {total} 个源，max_workers={max_workers}")

    all_results = [None] * total
    start_time = time.time()

    effective_workers = min(max_workers, total)

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_idx = {}
        for i, src in enumerate(sources):
            future = executor.submit(collect_fn, src, i)
            future_to_idx[future] = i

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result(timeout=timeout)
                all_results[idx] = result
            except Exception as e:
                src = sources[idx]
                logger.warning(f"[{desc}] 源 {src.get('id', '?')} 超时: {e}")
                all_results[idx] = ([], {"error": str(e)})

    # 合并结果
    all_articles = []
    source_stats = {}
    for i, result in enumerate(all_results):
        if result is None:
            src = sources[i]
            source_stats[src.get("id", src.get("name", f"src_{i}"))] = {"error": "timeout"}
            continue
        articles, stats = result
        all_articles.extend(articles or [])
        src_id = sources[i].get("id", sources[i].get("name", f"src_{i}"))
        source_stats[src_id] = stats

    elapsed = time.time() - start_time
    logger.info(
        f"[{desc}] 完成: {len(all_articles)} 篇文章, "
        f"耗时 {elapsed:.1f}s (并行度={effective_workers})"
    )

    return all_articles, source_stats