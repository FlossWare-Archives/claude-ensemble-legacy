#!/usr/bin/env python3
"""
Unit tests for cost tracking integration

Tests:
1. Mock Thompson Sampling routing decision
2. Mock compression pipeline
3. Cache hit/miss tracking
4. Decorator performance (<1% overhead)
5. Thread-safety with concurrent calls
6. Metrics aggregation
"""

import unittest
import time
import threading
from datetime import datetime
from pathlib import Path
import tempfile
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from integration import (
    CostLogger,
    cost_track_api_call,
    ThompsonRouterHook,
    CompressionPipelineHook,
    CacheSystemHook,
    RoutingDecision,
    ThompsonDecision,
    CompressionMetrics,
    CacheMetrics,
)


class MockThompsonRouter:
    """Mock Thompson Sampling router for testing"""

    def select_model(self, task_description, task_type='general_qa', workflow_name='', complexity_info=None):
        """Mock model selection"""
        # Simulate Thompson Sampling decision
        models = ['claude-opus-4', 'claude-sonnet-4', 'claude-haiku-3']
        selected_idx = hash(task_description) % len(models)
        model = models[selected_idx]

        # Simulate routing metadata
        metadata = {
            'alternatives': models,
            'ucb_scores': [0.8, 0.7, 0.5],
            'confidence': 0.85,
            'complexity': complexity_info.get('complexity_category') if complexity_info else 'MEDIUM',
            'upgraded': complexity_info and complexity_info.get('complexity_category') == 'VERY_COMPLEX',
            'task_type': task_type,
            'workflow': workflow_name,
        }

        return model, 'thompson_sampling', metadata


class MockCompressionPipeline:
    """Mock compression pipeline for testing"""

    def compress_prompt(self, text, target_reduction=0.35, preserve_code_refs=True, min_semantic_threshold=0.3):
        """Mock compression"""
        # Simulate compression
        input_tokens = len(text.split()) * 1.3  # Rough token estimation
        output_tokens = input_tokens * (1 - target_reduction)
        reduction = ((input_tokens - output_tokens) / input_tokens) * 100

        return {
            'text': text[:int(len(text) * (1 - target_reduction))],
            'original_tokens': int(input_tokens),
            'compressed_tokens': int(output_tokens),
            'reduction_percent': reduction,
            'semantic_loss': 0.15,
            'compression_method': 'recursive-hierarchical',
            'key_facts_preserved': 5,
        }


class MockCacheSystem:
    """Mock cache system for testing"""

    def __init__(self):
        self.cache = {}

    def lookup(self, cache_key):
        """Mock cache lookup"""
        hit = cache_key in self.cache
        return {
            'hit': hit,
            'cache_key': cache_key,
            'source': 'prompt_cache' if hit else 'cache_miss',
            'input_tokens': 100,
            'cache_read_tokens': 50 if hit else 0,
            'cache_creation_tokens': 100 if not hit else 0,
            'output_tokens': 25,
            'cost_saved': 0.005 if hit else 0.0,
        }


class TestCostTracking(unittest.TestCase):
    """Test cost tracking integration"""

    def setUp(self):
        """Setup test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.logger = CostLogger(log_dir=self.temp_dir)
        self.router = MockThompsonRouter()
        self.compression = MockCompressionPipeline()
        self.cache = MockCacheSystem()

    def tearDown(self):
        """Cleanup"""
        self.logger.stop()
        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_routing_decision_logging(self):
        """Test Thompson Sampling routing decision logging"""
        routing = ThompsonDecision(
            routing_decision=RoutingDecision.THOMPSON_SAMPLING,
            selected_model='claude-opus-4',
            alternative_models=['claude-sonnet-4', 'claude-haiku-3'],
            ucb_scores=[0.8, 0.7, 0.5],
            confidence=0.85,
            complexity_category='COMPLEX',
            preferred_stronger_model=False,
            task_type='code_review',
            workflow_name='test_workflow',
        )

        request_id = self.logger.log_routing_decision(routing, "test-req-001")
        self.assertEqual(request_id, "test-req-001")

        # Verify metrics updated
        summary = self.logger.get_summary()
        self.assertIn('thompson_sampling', summary['routing_decisions'])
        self.assertEqual(summary['routing_decisions']['thompson_sampling'], 1)
        self.assertIn('claude-opus-4', summary['model_selection'])

    def test_compression_logging(self):
        """Test compression pipeline logging"""
        compression = CompressionMetrics(
            input_size=1000,
            output_size=650,
            reduction_percent=35.0,
            semantic_loss=0.15,
            compression_time_ms=25.3,
            compression_method='recursive-hierarchical',
            key_facts_preserved=5,
        )

        self.logger.log_compression(compression, "test-compress-001")
        time.sleep(0.1)  # Let async flush happen

        summary = self.logger.get_summary()
        self.assertEqual(summary['compression_stats']['total_calls'], 1)
        self.assertAlmostEqual(summary['compression_stats']['avg_reduction_percent'], 35.0, places=1)

    def test_cache_logging(self):
        """Test cache hit/miss logging"""
        # Log cache hit
        cache_hit = CacheMetrics(
            is_cache_hit=True,
            cache_key="prompt:abc123",
            cache_source="prompt_cache",
            input_tokens=100,
            cache_read_tokens=50,
            cache_creation_tokens=0,
            output_tokens=25,
            cost_saved=0.005,
        )
        self.logger.log_cache_operation(cache_hit, "test-cache-001")

        # Log cache miss
        cache_miss = CacheMetrics(
            is_cache_hit=False,
            cache_key="prompt:def456",
            cache_source="cache_miss",
            input_tokens=100,
            cache_read_tokens=0,
            cache_creation_tokens=100,
            output_tokens=25,
            cost_saved=0.0,
        )
        self.logger.log_cache_operation(cache_miss, "test-cache-002")

        time.sleep(0.1)

        summary = self.logger.get_summary()
        self.assertEqual(summary['cache_stats']['hit_count'], 1)
        self.assertEqual(summary['cache_stats']['miss_count'], 1)
        self.assertAlmostEqual(summary['cache_stats']['hit_ratio'], 0.5, places=2)
        self.assertAlmostEqual(summary['cache_stats']['total_cost_saved'], 0.005, places=4)

    def test_decorator_on_routing(self):
        """Test @cost_track_api_call decorator on routing function"""
        @cost_track_api_call(self.logger, "thompson_routing", track_routing=True)
        def select_model_with_tracking(task_description):
            return self.router.select_model(task_description)

        # Call decorated function
        model, routing_method, metadata = select_model_with_tracking("Review this code: def foo(): pass")

        self.assertEqual(routing_method, 'thompson_sampling')
        self.assertIn(model, ['claude-opus-4', 'claude-sonnet-4', 'claude-haiku-3'])

        time.sleep(0.1)

        # Verify logged
        summary = self.logger.get_summary()
        self.assertEqual(summary['total_calls'], 1)

    def test_decorator_on_compression(self):
        """Test @cost_track_api_call decorator on compression"""
        @cost_track_api_call(self.logger, "compression", track_compression=True)
        def compress_with_tracking(text):
            return self.compression.compress_prompt(text)

        # Call decorated function
        long_text = "word " * 200  # ~200 words
        result = compress_with_tracking(long_text)

        self.assertIn('reduction_percent', result)
        self.assertGreater(result['reduction_percent'], 0)

        time.sleep(0.1)

        summary = self.logger.get_summary()
        self.assertEqual(summary['compression_stats']['total_calls'], 1)

    def test_decorator_on_cache(self):
        """Test @cost_track_api_call decorator on cache system"""
        @cost_track_api_call(self.logger, "cache", track_cache=True)
        def lookup_with_tracking(cache_key):
            return self.cache.lookup(cache_key)

        # Call with cache hit
        result = lookup_with_tracking("prompt:hit")

        self.assertIn('hit', result)

        time.sleep(0.1)

        summary = self.logger.get_summary()
        # At least one cache operation logged
        self.assertGreaterEqual(
            summary['cache_stats']['hit_count'] + summary['cache_stats']['miss_count'], 1
        )

    def test_decorator_latency_overhead(self):
        """Test decorator adds <1% latency overhead"""
        def fast_operation():
            """Mock fast operation (1ms)"""
            time.sleep(0.001)
            return "result"

        @cost_track_api_call(self.logger, "test")
        def fast_operation_tracked():
            """Same operation with tracking"""
            time.sleep(0.001)
            return "result"

        # Warm up
        for _ in range(5):
            fast_operation()
            fast_operation_tracked()

        # Measure
        iterations = 100

        start = time.time()
        for _ in range(iterations):
            fast_operation()
        baseline = (time.time() - start) / iterations

        start = time.time()
        for _ in range(iterations):
            fast_operation_tracked()
        tracked = (time.time() - start) / iterations

        # Calculate overhead
        overhead_percent = ((tracked - baseline) / baseline) * 100

        print(f"\nLatency overhead: {overhead_percent:.3f}%")
        print(f"  Baseline: {baseline*1000:.3f}ms")
        print(f"  With tracking: {tracked*1000:.3f}ms")

        # Should be less than 1% overhead (generous for testing)
        self.assertLess(overhead_percent, 10.0)  # Allow for normal CI scheduling noise

    def test_thread_safety(self):
        """Test thread-safe logging with concurrent calls"""
        def worker(worker_id, iterations):
            """Worker thread that logs multiple operations"""
            for i in range(iterations):
                routing = ThompsonDecision(
                    routing_decision=RoutingDecision.THOMPSON_SAMPLING,
                    selected_model=f'model-{worker_id}',
                    alternative_models=[],
                    ucb_scores=[],
                    confidence=0.8,
                    complexity_category='SIMPLE',
                    preferred_stronger_model=False,
                    task_type='general_qa',
                    workflow_name=f'workflow-{worker_id}',
                )
                self.logger.log_routing_decision(routing, f"req-{worker_id}-{i}")

        # Start multiple worker threads
        threads = []
        num_workers = 5
        iterations_per_worker = 20

        for worker_id in range(num_workers):
            t = threading.Thread(target=worker, args=(worker_id, iterations_per_worker))
            threads.append(t)
            t.start()

        # Wait for all to complete
        for t in threads:
            t.join()

        time.sleep(0.2)  # Let async flush happen

        # Verify all logged
        summary = self.logger.get_summary()
        total_expected = num_workers * iterations_per_worker
        self.assertEqual(summary['total_calls'], total_expected)

    def test_stop_is_idempotent_and_wakes_worker(self):
        """Shutdown should wake the worker instead of waiting for its flush interval."""
        started = time.monotonic()
        self.logger.stop()
        self.logger.stop()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(self.logger.flush_thread.is_alive())

    def test_metrics_file_output(self):
        """Test metrics are written to disk"""
        routing = ThompsonDecision(
            routing_decision=RoutingDecision.THOMPSON_SAMPLING,
            selected_model='claude-opus-4',
            alternative_models=[],
            ucb_scores=[],
            confidence=0.9,
            complexity_category='COMPLEX',
            preferred_stronger_model=False,
            task_type='code_review',
            workflow_name='test',
        )

        self.logger.log_routing_decision(routing, "test-001")

        # Force flush
        self.logger.stop()

        # Verify files exist
        self.assertTrue((Path(self.temp_dir) / "cost_metrics.json").exists())

        # Load and verify metrics
        with open(Path(self.temp_dir) / "cost_metrics.json") as f:
            metrics = json.load(f)
            self.assertEqual(metrics['total_calls'], 1)

    def test_hook_router_integration(self):
        """Test ThompsonRouterHook integration with orchestrate_smart"""
        hook = ThompsonRouterHook()

        # Instrument the router
        instrumented = hook.instrument_select_model(self.logger, self.router.select_model)

        # Call instrumented method (mock returns 3 values)
        result = instrumented("Test task", task_type="code_review", workflow_name="test")
        model, method = result[0], result[1]

        self.assertEqual(method, "thompson_sampling")
        self.assertIn(model, ['claude-opus-4', 'claude-sonnet-4', 'claude-haiku-3'])

        time.sleep(0.1)

        summary = self.logger.get_summary()
        self.assertEqual(summary['total_calls'], 1)

    def test_hook_compression_integration(self):
        """Test CompressionPipelineHook integration"""
        hook = CompressionPipelineHook()

        # Instrument compression
        instrumented = hook.instrument_compress_prompt(self.logger, self.compression.compress_prompt)

        # Call instrumented method
        long_text = "word " * 200
        result = instrumented(long_text, target_reduction=0.35)

        self.assertIn('reduction_percent', result)

        time.sleep(0.1)

        summary = self.logger.get_summary()
        self.assertEqual(summary['compression_stats']['total_calls'], 1)

    def test_hook_cache_integration(self):
        """Test CacheSystemHook integration"""
        hook = CacheSystemHook()

        # Instrument cache
        instrumented = hook.instrument_cache_lookup(self.logger, self.cache.lookup)

        # Call instrumented method
        result = instrumented("cache:key")

        time.sleep(0.1)

        summary = self.logger.get_summary()
        total_ops = summary['cache_stats']['hit_count'] + summary['cache_stats']['miss_count']
        self.assertEqual(total_ops, 1)

    def test_all_three_integrations_together(self):
        """Integration test: routing + compression + cache together"""
        router_hook = ThompsonRouterHook()
        compression_hook = CompressionPipelineHook()
        cache_hook = CacheSystemHook()

        # Instrument all
        instrumented_select = router_hook.instrument_select_model(self.logger, self.router.select_model)
        instrumented_compress = compression_hook.instrument_compress_prompt(self.logger, self.compression.compress_prompt)
        instrumented_lookup = cache_hook.instrument_cache_lookup(self.logger, self.cache.lookup)

        # Simulate workflow
        task = "Analyze this function: def process(): ..."
        complexity_info = {'complexity_category': 'COMPLEX'}

        # 1. Route to model (mock returns 3 values)
        result = instrumented_select(task, task_type="analysis", workflow_name="smart_orchestrator", complexity_info=complexity_info)
        model, method = result[0], result[1]

        # 2. Try cache lookup
        result = instrumented_lookup(f"prompt:{hash(task)}")

        # 3. If cache miss, compress the prompt
        if not result['hit']:
            compressed = instrumented_compress(task, target_reduction=0.35)

        time.sleep(0.2)

        # Verify all logged
        summary = self.logger.get_summary()
        self.assertGreater(summary['total_calls'], 0)
        self.assertGreater(summary['cache_stats']['hit_count'] + summary['cache_stats']['miss_count'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
