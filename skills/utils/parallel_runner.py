"""
parallel_runner.py — 通用并行处理工具

提供 ThreadPoolExecutor 封装，保证:
- 输出顺序与输入一致（按 index 排序）
- 单任务失败不影响整体
- 可配并发数、超时、日志
- 线程安全的计数器

用法:
    from skills.utils.parallel_runner import parallel_map

    results = parallel_map(
        items=my_list,
        process_fn=my_func,        # (item, idx) -> result
        max_workers=4,
        desc="采集源",
    )
    # results[i] 与 my_list[i] 一一对应
"""

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def parallel_map(
    items: Sequence[Any],
    process_fn: Callable,
    max_workers: int = 4,
    timeout: int = 120,
    desc: str = "parallel",
    continue_on_error: bool = True,
    return_errors: bool = True,
) -> Tuple[List[Optional[Any]], Dict[str, int]]:
    """并行执行 process_fn，返回与 items 等长的有序结果列表。

    Args:
        items:       待处理的项列表
        process_fn:  处理函数，签名为 (item, idx) -> result
                     - item: items 中的元素
                     - idx:  元素在 items 中的索引
        max_workers: 最大并发线程数
        timeout:     单个任务超时秒数
        desc:        日志描述前缀
        continue_on_error: 单项失败时不中断整体
        return_errors: 失败项是否在 error 字段返回错误信息

    Returns:
        (results, stats)
        - results: 与 items 等长的结果列表，失败项为 None
        - stats: {"total": N, "success": N, "failed": N, "elapsed_seconds": float}
    """
    total = len(items)
    results: List[Optional[Any]] = [None] * total
    success_count = 0
    failed_count = 0
    _lock = threading.Lock()
    start_time = time.time()

    if total == 0:
        return results, {"total": 0, "success": 0, "failed": 0, "elapsed_seconds": 0}

    # 少量任务直接串行
    if total <= 2 or max_workers <= 1:
        logger.info(f"[{desc}] {total} 项，串行处理")
        for i, item in enumerate(items):
            try:
                results[i] = process_fn(item, i)
                success_count += 1
            except Exception as e:
                failed_count += 1
                logger.warning(f"[{desc}] item {i} 失败: {e}")
                if not continue_on_error:
                    raise
                results[i] = None
        elapsed = time.time() - start_time
        stats = {
            "total": total, "success": success_count, "failed": failed_count,
            "elapsed_seconds": round(elapsed, 2),
        }
        logger.info(f"[{desc}] 串行完成: {success_count}/{total} 成功, 耗时 {elapsed:.1f}s")
        return results, stats

    effective_workers = min(max_workers, total)
    logger.info(f"[{desc}] 并行处理 {total} 项, max_workers={effective_workers}")

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_map = {}
        for i, item in enumerate(items):
            future = executor.submit(process_fn, item, i)
            future_map[future] = i

        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                result = future.result(timeout=timeout)
                results[idx] = result
                with _lock:
                    success_count += 1
            except Exception as e:
                with _lock:
                    failed_count += 1
                logger.warning(f"[{desc}] item {idx} 异常: {e}")
                if not continue_on_error:
                    # 取消剩余任务
                    for f in future_map:
                        f.cancel()
                    raise
                results[idx] = None

    elapsed = time.time() - start_time
    stats = {
        "total": total, "success": success_count, "failed": failed_count,
        "elapsed_seconds": round(elapsed, 2),
    }
    logger.info(
        f"[{desc}] 完成: {success_count}/{total} 成功, "
        f"{failed_count} 失败, 耗时 {elapsed:.1f}s (并发度={effective_workers})"
    )
    return results, stats


def keyed_parallel_map(
    items: Sequence[Any],
    process_fn: Callable,
    key_list: List[str],
    max_workers: int = 3,
    max_concurrent_per_key: int = 1,
    timeout: int = 120,
    desc: str = "keyed_parallel",
) -> Tuple[List[Optional[Any]], Dict[str, int]]:
    """基于 key 分组的并行执行，保证同一 key 串行执行。

    场景: Tavily 搜索 — 多个 query 并行跑, 但同一个 API Key 同时只有 1 个请求。

    Args:
        items:               待处理项列表
        process_fn:          处理函数 (item, idx, api_key) -> result
        key_list:           与 items 等长的 key 列表（每项对应一个 key）
        max_workers:         最大总并发线程数
        max_concurrent_per_key: 每个 key 的最大并发数（1=串行）
        timeout:             单任务超时秒
        desc:                日志描述

    Returns:
        (results, stats)
        - results: 与 items 等长，保序
        - stats: {"total": N, "success": N, "failed": N, "elapsed_seconds": float}
    """
    total = len(items)
    results: List[Optional[Any]] = [None] * total
    success_count = 0
    failed_count = 0
    _lock = threading.Lock()
    _key_semas: Dict[str, threading.Semaphore] = {}
    start_time = time.time()

    if total == 0:
        return results, {"total": 0, "success": 0, "failed": 0, "elapsed_seconds": 0}

    # 为每个 key 创建信号量
    for key in set(key_list):
        _key_semas[key] = threading.Semaphore(max_concurrent_per_key)

    # 按 key 分组索引，确保同一 key 的任务串行排队
    # 但不同 key 之间可以并行
    effective_workers = min(max_workers, total)
    logger.info(
        f"[{desc}] 并行处理 {total} 项, max_workers={effective_workers}, "
        f"per_key_limit={max_concurrent_per_key}, keys={len(set(key_list))}"
    )

    def _wrapped(item, idx):
        """带 key 信号量控制的执行包装"""
        key = key_list[idx]
        sem = _key_semas.get(key)
        if sem:
            sem.acquire()
        try:
            return process_fn(item, idx, key)
        finally:
            if sem:
                sem.release()

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_map = {}
        for i, item in enumerate(items):
            future = executor.submit(_wrapped, item, i)
            future_map[future] = i

        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                result = future.result(timeout=timeout)
                results[idx] = result
                with _lock:
                    success_count += 1
            except Exception as e:
                with _lock:
                    failed_count += 1
                logger.warning(f"[{desc}] item {idx} 异常: {e}")
                results[idx] = None

    elapsed = time.time() - start_time
    stats = {
        "total": total, "success": success_count, "failed": failed_count,
        "elapsed_seconds": round(elapsed, 2),
    }
    logger.info(
        f"[{desc}] 完成: {success_count}/{total} 成功, "
        f"{failed_count} 失败, 耗时 {elapsed:.1f}s"
    )
    return results, stats


def batch_parallel_map(
    items_by_batch: Dict[str, List[Tuple[int, Any]]],
    process_fn: Callable,
    batch_workers: Dict[str, int],
    batch_timeouts: Optional[Dict[str, int]] = None,
    desc: str = "batch_parallel",
    continue_on_error: bool = True,
) -> Tuple[List[Optional[Any]], Dict[str, int]]:
    """按批次分组并行执行，每批独立并发度。

    场景: analyze_business_impact — ARCHIVE 跳过，P2/Lite 批量，P1/Chat 稳定，P0 低并发。

    Args:
        items_by_batch: {batch_name: [(original_index, item), ...]}
        process_fn:     (item, idx, batch_name) -> result
        batch_workers:  {batch_name: max_workers}，0 = 跳过
        batch_timeouts: {batch_name: timeout_seconds}（可选，默认 120）
        desc:           日志描述前缀
        continue_on_error: 单项失败不中断整体

    Returns:
        (results, stats)
        - results: 与 total_items 等长有序结果（total = sum(len(v) for v in items_by_batch)）
        - stats: {"total": N, "success": N, "failed": N, "elapsed_seconds": float,
                   "by_batch": {name: {"total": N, "success": N, "failed": N}}}
    """
    batch_timeouts = batch_timeouts or {}
    total_items = sum(len(v) for v in items_by_batch.values())
    results: List[Optional[Any]] = [None] * total_items
    stats_by_batch: Dict[str, Dict[str, int]] = {}
    success_total = 0
    failed_total = 0
    _lock = threading.Lock()
    start_time = time.time()

    if total_items == 0:
        return results, {"total": 0, "success": 0, "failed": 0,
                          "elapsed_seconds": 0, "by_batch": {}}

    for batch_name, batch_items in items_by_batch.items():
        workers = batch_workers.get(batch_name, 4)
        timeout = batch_timeouts.get(batch_name, 120)
        batch_success = 0
        batch_failed = 0

        if workers == 0 or len(batch_items) == 0:
            logger.info(f"[{desc}] 批次 {batch_name}: 跳过 (workers={workers}, items={len(batch_items)})")
            stats_by_batch[batch_name] = {"total": 0, "success": 0, "failed": 0}
            continue

        logger.info(f"[{desc}] 批次 {batch_name}: {len(batch_items)} 项, workers={workers}")

        def _process_wrapper(item_tuple, _batch_name=batch_name):
            """包装 process_fn，传入 batch_name"""
            orig_idx, item = item_tuple
            return process_fn(item, orig_idx, _batch_name), orig_idx

        if len(batch_items) <= 2 or workers <= 1:
            # 串行处理
            for item_tuple in batch_items:
                orig_idx, item = item_tuple
                try:
                    result = process_fn(item, orig_idx, batch_name)
                    results[orig_idx] = result
                    batch_success += 1
                except Exception as e:
                    batch_failed += 1
                    logger.warning(f"[{desc}] 批次 {batch_name} item {orig_idx} 失败: {e}")
                    if not continue_on_error:
                        raise
                    results[orig_idx] = None
        else:
            effective_workers = min(workers, len(batch_items))
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                future_map = {}
                for item_tuple in batch_items:
                    orig_idx = item_tuple[0]
                    future = executor.submit(
                        lambda it, bn=batch_name: (process_fn(it[1], it[0], bn), it[0]),
                        item_tuple,
                    )
                    future_map[future] = orig_idx

                for future in as_completed(future_map):
                    orig_idx = future_map[future]
                    try:
                        result, _ = future.result(timeout=timeout)
                        results[orig_idx] = result
                        batch_success += 1
                    except Exception as e:
                        batch_failed += 1
                        logger.warning(f"[{desc}] 批次 {batch_name} item {orig_idx} 异常: {e}")
                        if not continue_on_error:
                            for f in future_map:
                                f.cancel()
                            raise
                        results[orig_idx] = None

        with _lock:
            success_total += batch_success
            failed_total += batch_failed

        stats_by_batch[batch_name] = {
            "total": len(batch_items),
            "success": batch_success,
            "failed": batch_failed,
        }
        logger.info(
            f"[{desc}] 批次 {batch_name} 完成: {batch_success}/{len(batch_items)} 成功, "
            f"{batch_failed} 失败"
        )

    elapsed = time.time() - start_time
    stats = {
        "total": total_items,
        "success": success_total,
        "failed": failed_total,
        "elapsed_seconds": round(elapsed, 2),
        "by_batch": stats_by_batch,
    }
    logger.info(
        f"[{desc}] 全部完成: {success_total}/{total_items} 成功, "
        f"{failed_total} 失败, 耗时 {elapsed:.1f}s"
    )
    return results, stats


# ── Checkpoint 读写工具 ──

def save_checkpoint(
    checkpoint_path: str,
    item_id: str,
    result: dict,
) -> None:
    """追加一行 checkpoint 记录（JSONL 格式）。

    Args:
        checkpoint_path: checkpoint 文件路径
        item_id:          文章/事件 ID
        result:           处理结果 dict
    """
    import json
    line = json.dumps({
        "item_id": item_id,
        "result": result,
        "timestamp": time.time(),
    }, ensure_ascii=False)
    with open(checkpoint_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_checkpoint(
    checkpoint_path: str,
) -> Dict[str, dict]:
    """加载已有的 checkpoint 记录，返回 {item_id: result} 映射。

    Args:
        checkpoint_path: checkpoint 文件路径

    Returns:
        dict: {item_id: result}，文件不存在则返回空 dict
    """
    import json
    import os

    if not os.path.exists(checkpoint_path):
        return {}

    completed = {}
    try:
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    item_id = record.get("item_id", "")
                    result = record.get("result", {})
                    if item_id:
                        completed[item_id] = result
                except json.JSONDecodeError:
                    logger.warning(f"checkpoint 第 {line_num} 行 JSON 解析失败，跳过")
    except Exception as e:
        logger.warning(f"加载 checkpoint 失败: {e}")
    return completed


def clear_checkpoint(checkpoint_path: str) -> None:
    """清空 checkpoint 文件（新 pipeline run 开始时调用）。"""
    import os
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)
        logger.info(f"checkpoint 已清除: {checkpoint_path}")


def load_parallel_config(project_root: str) -> dict:
    """加载并行配置文件。

    Args:
        project_root: 项目根目录

    Returns:
        dict: parallel.yaml 配置，文件不存在则返回默认值
    """
    import os
    import yaml

    config_path = os.path.join(project_root, "config", "parallel.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        return config

    # 返回内置默认值
    return {
        "defaults": {
            "enabled": True,
            "max_workers": 6,
            "single_timeout": 120,
            "retry": 0,
        },
        "collect_daily_articles": {
            "source_parallel": {
                "enabled": True,
                "rsshub_workers": 6,
                "rss_workers": 4,
                "web_workers": 2,
                "tavily_workers": 6,
                "total_workers_cap": 0,
                "source_timeout": 120,
                "continue_on_source_error": True,
            },
            "tavily_query_parallel": {
                "enabled": True,
                "max_workers": 6,
                "max_concurrent_per_key": 1,
            },
        },
        "tavily_gap_search": {
            "query_parallel": {
                "enabled": True,
                "max_workers": 3,
                "max_concurrent_per_key": 1,
                "query_timeout": 30,
                "respect_daily_budget": True,
                "continue_on_query_error": True,
            },
        },
    }