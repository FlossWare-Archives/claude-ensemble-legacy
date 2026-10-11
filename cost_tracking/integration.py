#!/usr/bin/env python3
"""
Cost Tracking Integration Module

Wires cost tracking into Thompson router and compression pipeline.
Auto-logs all API calls with context (model routing decision, compression applied, cache hit/miss).

Features:
- Decorator pattern for minimal business logic changes
- Thread-safe logging with microsecond precision
- <1% latency overhead via async I/O
- Tracks: Thompson Sampling decision, compression metrics, cache status
- Integration hooks for orchestrate_smart.py and compression pipeline

Usage:
    from cost_tracking.integration import cost_track_api_call, CostLogger

    logger = CostLogger()

    # Decorate Thompson router decision
    @cost_track_api_call(logger, "thompson_routing")
    def select_model_with_tracking(task_description):
        # existing Thompson logic
        return model_id, selection_method

    # Decorate compression call
    @cost_track_api_call(logger, "compression_pipeline")
    def compress_with_tracking(text):
        # existing compression logic
        return compressed_result
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from functools import wraps
from dataclasses import dataclass, asdict
import queue
from enum import Enum


class RoutingDecision(Enum):
    """Model routing decisions"""
    THOMPSON_SAMPLING = "thompson_sampling"
    THOMPSON_SAMPLING_UPGRADED = "thompson_sampling_upgraded"
    POSTGRES_VERIFIED = "postgres_verified"
    VERIFIED_FALLBACK = "verified_fallback"
    GA_EXPLORATION = "ga_exploration"
    AUTO_PROFILER = "auto_profiler"
    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"


@dataclass
class CompressionMetrics:
    """Track compression pipeline metrics"""
    input_size: int
    output_size: int
    reduction_percent: float
    semantic_loss: float
    compression_time_ms: float
    compression_method: str
    key_facts_preserved: int
    compression_applied: bool = True


@dataclass
class CacheMetrics:
    """Track cache system metrics"""
    is_cache_hit: bool
    cache_key: Optional[str]
    cache_source: str  # "prompt_cache", "redis", "memory"
    input_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    output_tokens: int
    cost_saved: float
    cache_hit_ratio: Optional[float] = None


@dataclass
class ThompsonDecision:
    """Track Thompson Sampling routing decision"""
    routing_decision: RoutingDecision
    selected_model: str
    alternative_models: list
    ucb_scores: list
    confidence: float
    complexity_category: Optional[str]
    preferred_stronger_model: bool
    task_type: str
    workflow_name: str


@dataclass
class APICallRecord:
    """Complete API call record with all tracking info"""
    timestamp: datetime
    call_type: str  # "routing", "compression", "cache_query", "api_request"
    request_id: str

    # Routing decision
    routing_decision: Optional[ThompsonDecision] = None

    # Compression metrics
    compression: Optional[CompressionMetrics] = None

    # Cache metrics
    cache: Optional[CacheMetrics] = None

    # API request details
    model_selected: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0
    latency_ms: float = 0.0
    success: bool = True
    error_message: Optional[str] = None

    # Context
    context_tags: Dict[str, Any] = None

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['routing_decision'] = asdict(self.routing_decision) if self.routing_decision else None
        if data['routing_decision']:
            data['routing_decision']['routing_decision'] = data['routing_decision']['routing_decision'].value
        data['compression'] = asdict(self.compression) if self.compression else None
        data['cache'] = asdict(self.cache) if self.cache else None
        return data


class ThreadSafeQueue:
    """Thread-safe logging queue with async flush"""

    def __init__(self, maxsize=10000):
        self.queue = queue.Queue(maxsize=maxsize)
        self.lock = threading.Lock()
        self.size = 0

    def put(self, item: APICallRecord, block=True):
        """Add item to queue (thread-safe)"""
        with self.lock:
            self.queue.put(item, block=block)
            self.size += 1

    def get(self, timeout=None) -> Optional[APICallRecord]:
        """Get item from queue"""
        try:
            item = self.queue.get(timeout=timeout)
            with self.lock:
                self.size -= 1
            return item
        except queue.Empty:
            return None

    def qsize(self) -> int:
        """Get current queue size"""
        with self.lock:
            return self.size


class CostLogger:
    """
    Thread-safe cost tracking logger for Thompson router and compression pipeline

    Minimal overhead (<1% latency impact) via async logging to file.
    """

    def __init__(self, log_dir: Optional[str] = None, flush_interval_sec: float = 5.0):
        """
        Initialize cost logger

        Args:
            log_dir: Directory for cost tracking logs (default: ~/.claude/cost_tracking)
            flush_interval_sec: Flush queue to disk every N seconds
        """
        self.log_dir = Path(log_dir or os.path.expanduser("~/.claude/cost_tracking"))
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Log files
        self.summary_log = self.log_dir / "cost_summary.jsonl"
        self.detailed_log = self.log_dir / "cost_detailed.jsonl"
        self.metrics_log = self.log_dir / "cost_metrics.json"

        # Thread-safe queue for async logging
        self.queue = ThreadSafeQueue(maxsize=10000)

        # Metrics tracking
        self.metrics = {
            'total_calls': 0,
            'routing_decisions': {},
            'compression_stats': {
                'avg_reduction_percent': 0,
                'avg_compression_time_ms': 0,
                'avg_semantic_loss': 0.0,
                'total_calls': 0,
            },
            'cache_stats': {
                'hit_count': 0,
                'miss_count': 0,
                'hit_ratio': 0.0,
                'total_cost_saved': 0.0,
            },
            'model_selection': {},
            'start_time': datetime.now().isoformat(),
        }
        self.metrics_lock = threading.Lock()

        # Start async flush worker
        self.flush_interval = flush_interval_sec
        self.running = True
        self.flush_thread = threading.Thread(target=self._flush_worker, daemon=True)
        self.flush_thread.start()

        # Setup file logger for error cases
        self.file_logger = self._setup_file_logger()

    def _setup_file_logger(self) -> logging.Logger:
        """Setup file logger for errors"""
        logger = logging.getLogger("cost_tracking")
        if not logger.handlers:
            handler = logging.FileHandler(self.log_dir / "errors.log")
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger

    def log_routing_decision(self, routing: ThompsonDecision, request_id: str) -> str:
        """
        Log Thompson Sampling routing decision

        Returns: request_id for correlating with API call
        """
        record = APICallRecord(
            timestamp=datetime.now(),
            call_type="routing",
            request_id=request_id,
            routing_decision=routing,
            model_selected=routing.selected_model,
            context_tags={
                'task_type': routing.task_type,
                'workflow_name': routing.workflow_name,
                'complexity': routing.complexity_category,
            }
        )
        self.queue.put(record)

        with self.metrics_lock:
            self.metrics['total_calls'] += 1
            decision_type = routing.routing_decision.value
            if decision_type not in self.metrics['routing_decisions']:
                self.metrics['routing_decisions'][decision_type] = 0
            self.metrics['routing_decisions'][decision_type] += 1

            if routing.selected_model not in self.metrics['model_selection']:
                self.metrics['model_selection'][routing.selected_model] = 0
            self.metrics['model_selection'][routing.selected_model] += 1

        return request_id

    def log_compression(self, metrics: CompressionMetrics, request_id: str):
        """Log compression pipeline metrics"""
        record = APICallRecord(
            timestamp=datetime.now(),
            call_type="compression",
            request_id=request_id,
            compression=metrics,
            latency_ms=metrics.compression_time_ms,
        )
        self.queue.put(record)

        with self.metrics_lock:
            comp_stats = self.metrics['compression_stats']
            n = comp_stats['total_calls']

            # Running average
            comp_stats['avg_reduction_percent'] = (
                (comp_stats['avg_reduction_percent'] * n + metrics.reduction_percent) / (n + 1)
            )
            comp_stats['avg_compression_time_ms'] = (
                (comp_stats['avg_compression_time_ms'] * n + metrics.compression_time_ms) / (n + 1)
            )
            comp_stats['avg_semantic_loss'] = (
                (comp_stats['avg_semantic_loss'] * n + metrics.semantic_loss) / (n + 1)
            )
            comp_stats['total_calls'] += 1

    def log_cache_operation(self, cache: CacheMetrics, request_id: str):
        """Log cache hit/miss and savings"""
        record = APICallRecord(
            timestamp=datetime.now(),
            call_type="cache_query",
            request_id=request_id,
            cache=cache,
            success=True,
        )
        self.queue.put(record)

        with self.metrics_lock:
            cache_stats = self.metrics['cache_stats']
            if cache.is_cache_hit:
                cache_stats['hit_count'] += 1
            else:
                cache_stats['miss_count'] += 1

            total = cache_stats['hit_count'] + cache_stats['miss_count']
            cache_stats['hit_ratio'] = cache_stats['hit_count'] / total if total > 0 else 0.0
            cache_stats['total_cost_saved'] += cache.cost_saved

    def log_api_call(
        self,
        request_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_cost: float,
        latency_ms: float,
        success: bool = True,
        error_message: Optional[str] = None,
    ):
        """Log API request to model provider"""
        record = APICallRecord(
            timestamp=datetime.now(),
            call_type="api_request",
            request_id=request_id,
            model_selected=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_cost=total_cost,
            latency_ms=latency_ms,
            success=success,
            error_message=error_message,
        )
        self.queue.put(record)

    def get_summary(self) -> Dict[str, Any]:
        """Get cost tracking summary"""
        with self.metrics_lock:
            metrics_copy = json.loads(json.dumps(self.metrics, default=str))
        return metrics_copy

    def _flush_worker(self):
        """Background worker that flushes queue to disk periodically"""
        batch = []
        while self.running:
            try:
                # Try to get items from queue with timeout
                item = self.queue.get(timeout=self.flush_interval)
                if item:
                    batch.append(item.to_dict())

                # Flush when batch is large enough or timeout occurs
                if len(batch) >= 100:
                    self._flush_batch(batch)
                    batch = []
            except Exception as e:
                self.file_logger.error(f"Queue worker error: {e}")

        # Final flush
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list):
        """Flush batch of records to disk"""
        if not batch:
            return

        try:
            with open(self.summary_log, 'a') as f:
                for record in batch:
                    f.write(json.dumps(record) + '\n')

            # Update metrics file
            with open(self.metrics_log, 'w') as f:
                json.dump(self.get_summary(), f, indent=2, default=str)
        except Exception as e:
            self.file_logger.error(f"Flush error: {e}")

    def stop(self):
        """Stop the logger once, wake its worker, and flush queued records."""
        if not self.running:
            if self.flush_thread.is_alive():
                self.flush_thread.join(timeout=0.1)
            return

        self.running = False
        # Wake queue.get() immediately instead of making shutdown wait for the
        # normal flush interval. If the queue is full, the worker will drain it
        # and its existing timeout remains the bounded fallback.
        try:
            self.queue.put(None, block=False)
        except queue.Full:
            pass

        self.flush_thread.join(timeout=max(1.0, self.flush_interval))
        if self.flush_thread.is_alive():
            self.file_logger.error("Cost tracking flush worker did not stop before timeout")


def cost_track_api_call(
    logger: CostLogger,
    call_type: str = "api_call",
    track_routing: bool = False,
    track_compression: bool = False,
    track_cache: bool = False,
) -> Callable:
    """
    Decorator to track API calls with minimal overhead

    Args:
        logger: CostLogger instance
        call_type: Type of call being tracked
        track_routing: If True, extract routing decision from result
        track_compression: If True, extract compression metrics from result
        track_cache: If True, extract cache metrics from result

    Returns: Decorated function

    Example:
        logger = CostLogger()

        @cost_track_api_call(logger, "thompson_routing", track_routing=True)
        def select_model(task_desc):
            # Thompson Sampling logic
            return (model_id, routing_method, routing_metadata)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Generate unique request ID
            request_id = f"{int(time.time() * 1e6)}"

            # Record start time
            start_time = time.time()

            try:
                # Call wrapped function
                result = func(*args, **kwargs)
                latency_ms = (time.time() - start_time) * 1000

                # Extract tracking info based on return type
                if track_routing and isinstance(result, tuple) and len(result) >= 2:
                    # Assume format: (model, routing_decision, [metadata])
                    model, routing_decision = result[0], result[1]
                    metadata = result[2] if len(result) > 2 else {}

                    routing = ThompsonDecision(
                        routing_decision=RoutingDecision(routing_decision),
                        selected_model=model,
                        alternative_models=metadata.get('alternatives', []),
                        ucb_scores=metadata.get('ucb_scores', []),
                        confidence=metadata.get('confidence', 0.5),
                        complexity_category=metadata.get('complexity', None),
                        preferred_stronger_model=metadata.get('upgraded', False),
                        task_type=metadata.get('task_type', 'unknown'),
                        workflow_name=metadata.get('workflow', ''),
                    )
                    logger.log_routing_decision(routing, request_id)

                elif track_compression and isinstance(result, dict):
                    # Assume result is CompressedPrompt dict
                    compression = CompressionMetrics(
                        input_size=result.get('original_tokens', 0),
                        output_size=result.get('compressed_tokens', 0),
                        reduction_percent=result.get('reduction_percent', 0),
                        semantic_loss=result.get('semantic_loss', 0),
                        compression_time_ms=latency_ms,
                        compression_method=result.get('compression_method', 'unknown'),
                        key_facts_preserved=result.get('key_facts_preserved', 0),
                    )
                    logger.log_compression(compression, request_id)

                elif track_cache and isinstance(result, dict):
                    # Assume result has cache metrics
                    cache = CacheMetrics(
                        is_cache_hit=result.get('is_cache_hit', False),
                        cache_key=result.get('cache_key'),
                        cache_source=result.get('cache_source', 'unknown'),
                        input_tokens=result.get('input_tokens', 0),
                        cache_read_tokens=result.get('cache_read_tokens', 0),
                        cache_creation_tokens=result.get('cache_creation_tokens', 0),
                        output_tokens=result.get('output_tokens', 0),
                        cost_saved=result.get('cost_saved', 0.0),
                    )
                    logger.log_cache_operation(cache, request_id)

                else:
                    # Generic API call
                    logger.log_api_call(
                        request_id=request_id,
                        model=kwargs.get('model', 'unknown'),
                        prompt_tokens=kwargs.get('prompt_tokens', 0),
                        completion_tokens=kwargs.get('completion_tokens', 0),
                        total_cost=kwargs.get('total_cost', 0.0),
                        latency_ms=latency_ms,
                        success=True,
                    )

                return result

            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                logger.log_api_call(
                    request_id=request_id,
                    model=kwargs.get('model', 'unknown'),
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_cost=0.0,
                    latency_ms=latency_ms,
                    success=False,
                    error_message=str(e),
                )
                raise

        return wrapper
    return decorator


# ============================================================================
# INTEGRATION HOOKS - Attach to existing systems
# ============================================================================

class ThompsonRouterHook:
    """Hook into Thompson Sampling router decision"""

    @staticmethod
    def instrument_select_model(logger: CostLogger, original_select_model_func: Callable) -> Callable:
        """
        Instrument Thompson router's select_model method

        Usage in orchestrate_smart.py:
            from cost_tracking.integration import ThompsonRouterHook

            hook = ThompsonRouterHook()
            orchestrator.select_model = hook.instrument_select_model(logger, orchestrator.select_model)
        """

        @wraps(original_select_model_func)
        def wrapped_select_model(task_description, task_type='general_qa', workflow_name='', complexity_info=None):
            # Call original
            result = original_select_model_func(task_description, task_type, workflow_name, complexity_info)

            # Handle both (model, method) and (model, method, metadata) formats
            if isinstance(result, tuple):
                if len(result) == 2:
                    model, routing_method = result
                    metadata = {}
                else:
                    model, routing_method, metadata = result[0], result[1], result[2] if len(result) > 2 else {}
            else:
                # Return as-is if not a tuple
                return result

            # Log with tracking
            routing = ThompsonDecision(
                routing_decision=RoutingDecision(routing_method),
                selected_model=model,
                alternative_models=metadata.get('alternatives', []),
                ucb_scores=metadata.get('ucb_scores', []),
                confidence=metadata.get('confidence', 0.5),
                complexity_category=complexity_info.get('complexity_category') if complexity_info else None,
                preferred_stronger_model='upgraded' in routing_method.lower(),
                task_type=task_type,
                workflow_name=workflow_name,
            )
            logger.log_routing_decision(routing, f"{int(time.time() * 1e6)}")

            return result

        return wrapped_select_model


class CompressionPipelineHook:
    """Hook into compression pipeline"""

    @staticmethod
    def instrument_compress_prompt(logger: CostLogger, original_compress_func: Callable) -> Callable:
        """
        Instrument compression pipeline

        Usage:
            from cost_tracking.integration import CompressionPipelineHook
            from compression.compression_api import compress_prompt

            hook = CompressionPipelineHook()
            compress_prompt = hook.instrument_compress_prompt(logger, compress_prompt)
        """

        @wraps(original_compress_func)
        def wrapped_compress(text, target_reduction=0.35, preserve_code_refs=True, min_semantic_threshold=0.3):
            start_time = time.time()
            result = original_compress_func(text, target_reduction, preserve_code_refs, min_semantic_threshold)
            compression_time = (time.time() - start_time) * 1000

            # Handle both dict and object results
            if isinstance(result, dict):
                input_tokens = result.get('original_tokens', 0)
                output_tokens = result.get('compressed_tokens', 0)
                reduction = result.get('reduction_percent', 0)
                semantic_loss = result.get('semantic_loss', 0)
                method = result.get('compression_method', 'unknown')
                facts = result.get('key_facts_preserved', 0)
            else:
                input_tokens = getattr(result, 'original_tokens', 0)
                output_tokens = getattr(result, 'compressed_tokens', 0)
                reduction = getattr(result, 'reduction_percent', 0)
                semantic_loss = getattr(result, 'semantic_loss', 0)
                method = getattr(result, 'compression_method', 'unknown')
                facts = getattr(result, 'key_facts_preserved', 0)

            # Track compression
            compression = CompressionMetrics(
                input_size=input_tokens,
                output_size=output_tokens,
                reduction_percent=reduction,
                semantic_loss=semantic_loss,
                compression_time_ms=compression_time,
                compression_method=method,
                key_facts_preserved=facts,
                compression_applied=True,
            )
            logger.log_compression(compression, f"{int(time.time() * 1e6)}")

            return result

        return wrapped_compress


class CacheSystemHook:
    """Hook into cache system (prompt caching, redis, etc)"""

    @staticmethod
    def instrument_cache_lookup(logger: CostLogger, original_lookup_func: Callable) -> Callable:
        """
        Instrument cache lookup

        Usage:
            from cost_tracking.integration import CacheSystemHook
            from cache_control import PromptCacheControl

            hook = CacheSystemHook()
            cache = PromptCacheControl()
            cache.lookup = hook.instrument_cache_lookup(logger, cache.lookup)
        """

        @wraps(original_lookup_func)
        def wrapped_lookup(*args, **kwargs):
            start_time = time.time()
            result = original_lookup_func(*args, **kwargs)
            lookup_time = (time.time() - start_time) * 1000

            # Track cache operation
            is_hit = result.get('hit', False) if isinstance(result, dict) else False
            cache = CacheMetrics(
                is_cache_hit=is_hit,
                cache_key=result.get('cache_key') if isinstance(result, dict) else None,
                cache_source=result.get('source', 'unknown') if isinstance(result, dict) else 'unknown',
                input_tokens=result.get('input_tokens', 0) if isinstance(result, dict) else 0,
                cache_read_tokens=result.get('cache_read_tokens', 0) if isinstance(result, dict) else 0,
                cache_creation_tokens=result.get('cache_creation_tokens', 0) if isinstance(result, dict) else 0,
                output_tokens=result.get('output_tokens', 0) if isinstance(result, dict) else 0,
                cost_saved=result.get('cost_saved', 0.0) if isinstance(result, dict) else 0.0,
            )
            logger.log_cache_operation(cache, f"{int(time.time() * 1e6)}")

            return result

        return wrapped_lookup


if __name__ == "__main__":
    print("Cost Tracking Integration Module\n")
    print("Use this module to track Thompson routing decisions, compression, and cache operations.\n")
    print("Example usage:")
    print("  from cost_tracking.integration import CostLogger, cost_track_api_call")
    print("  logger = CostLogger()")
    print("  @cost_track_api_call(logger, 'thompson_routing', track_routing=True)")
    print("  def select_model(task): ...")
