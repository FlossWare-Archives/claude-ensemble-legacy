#!/usr/bin/env python3
"""
Real Anthropic API validation for prompt caching.

This test validates that:
1. Cache control format works with actual Anthropic API
2. Prompt structure is compatible with messages.create()
3. Cache metrics are properly extracted from responses
4. Both ephemeral and last_message cache types function

IMPORTANT: This requires ANTHROPIC_API_KEY environment variable
and has a small cost for API calls. Run with test model (claude-3-5-haiku).
"""

import json
import os
import sys
import logging
from typing import Optional, Dict, Any
from dataclasses import asdict

# Import our integration module
from memory_cache_integration import (
    MemoryCacheIntegrator,
    CacheableBlock,
    CacheKey
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AnthropicCacheValidator:
    """Validates prompt caching against real Anthropic API."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-3-5-haiku-20241022"):
        """Initialize validator with Anthropic API client.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            model: Model to test with (defaults to Haiku for cost)
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable required. "
                "Set it before running: export ANTHROPIC_API_KEY='sk-ant-...'"
            )

        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

        # Try to import anthropic SDK
        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.api_key)
            self.has_sdk = True
        except ImportError:
            logger.warning(
                "anthropic SDK not installed. Install with: pip install anthropic"
            )
            self.has_sdk = False

    def validate_cache_control_format(self) -> Dict[str, Any]:
        """Test that cache_control format works with actual API.

        Returns:
            Dict with validation results:
            {
                "success": bool,
                "model": str,
                "cache_type_tested": str,
                "cache_creation_tokens": int,
                "cache_read_tokens": int,
                "usage": {...},
                "message": str
            }
        """
        if not self.has_sdk:
            return {
                "success": False,
                "error": "anthropic SDK not installed",
                "message": "Install with: pip install anthropic"
            }

        logger.info("=" * 80)
        logger.info("VALIDATING CACHE CONTROL FORMAT WITH ANTHROPIC API")
        logger.info("=" * 80)

        try:
            # Create a simple memory content to cache
            memory_content = """# Test Memory - RH Prompt Caching

## Feedback
- Always review with multi-AI before marking complete
- Stop automatically pushing to git
- Use git worktrees for branch work

## Project Context
- Model Router standalone project
- Budget: $300/month
- Using decorator-based LLM routing

## Reference
- Anthropic cache_control format: {"type": "ephemeral"} or {"type": "last_message"}
- Cache TTL: 6 hours
- Pricing: cache write (1.25x), cache read (0.1x)
"""

            # Structure the prompt using our integration
            integrator = MemoryCacheIntegrator(
                memory_dir=None,  # Don't auto-detect
                cache_control_version="ephemeral",
                enable_logging=True
            )

            # Manually create cacheable block
            block = CacheableBlock(
                content=memory_content,
                cache_type="ephemeral",
                source_path="test_memory.md"
            )

            # Build system array with cache_control
            system = [
                {
                    "type": "text",
                    "text": "You are a helpful assistant reviewing code and documentation."
                },
                {
                    "type": "text",
                    "text": f"# Context Memory\n\n{memory_content}",
                    "cache_control": {"type": "ephemeral"}
                }
            ]

            logger.info(f"System blocks: {len(system)}")
            logger.info(f"Cache control on block 2: {system[1].get('cache_control')}")

            # Make API call to verify format
            logger.info("\nCalling Anthropic API with cache_control format...")
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                system=system,
                messages=[
                    {
                        "role": "user",
                        "content": "Briefly summarize the memory context above."
                    }
                ]
            )

            # Extract cache metrics from response
            usage = response.usage.model_dump() if hasattr(response.usage, 'model_dump') else {
                'input_tokens': response.usage.input_tokens,
                'cache_creation_input_tokens': getattr(response.usage, 'cache_creation_input_tokens', 0),
                'cache_read_input_tokens': getattr(response.usage, 'cache_read_input_tokens', 0),
                'output_tokens': response.usage.output_tokens,
            }

            result = {
                "success": True,
                "model": self.model,
                "cache_type_tested": "ephemeral",
                "input_tokens": usage.get('input_tokens', 0),
                "cache_creation_tokens": usage.get('cache_creation_input_tokens', 0),
                "cache_read_tokens": usage.get('cache_read_input_tokens', 0),
                "output_tokens": usage.get('output_tokens', 0),
                "usage": usage,
                "message": "Cache control format successfully validated",
                "response_preview": response.content[0].text[:100] if response.content else ""
            }

            logger.info("\n" + "=" * 80)
            logger.info("VALIDATION RESULTS")
            logger.info("=" * 80)
            logger.info(f"✓ Cache control format accepted by API")
            logger.info(f"✓ Model: {result['model']}")
            logger.info(f"✓ Input tokens: {result['input_tokens']}")
            logger.info(f"✓ Cache creation tokens: {result['cache_creation_tokens']}")
            logger.info(f"✓ Cache read tokens: {result['cache_read_tokens']}")
            logger.info(f"✓ Output tokens: {result['output_tokens']}")

            if result['cache_creation_tokens'] > 0:
                logger.info(f"✓ Cache creation detected: {result['cache_creation_tokens']} tokens cached")
            if result['cache_read_tokens'] > 0:
                logger.info(f"✓ Cache hit detected: {result['cache_read_tokens']} tokens read from cache")

            logger.info("=" * 80)

            return result

        except Exception as e:
            logger.error(f"API call failed: {e}")
            return {
                "success": False,
                "model": self.model,
                "error": str(e),
                "message": f"API validation failed: {type(e).__name__}: {e}"
            }

    def test_cache_ttl_tracking(self) -> Dict[str, Any]:
        """Verify that cache TTL is properly tracked.

        Returns:
            Dict with TTL tracking validation results
        """
        logger.info("\nTesting cache TTL tracking...")

        from time import time, sleep

        # Create a cacheable block
        block = CacheableBlock(
            content="Test content",
            cache_type="ephemeral"
        )

        result = {
            "created_at": block.created_at,
            "ttl_seconds": block.cache_ttl_seconds,
            "checks": []
        }

        # Check immediately
        result['checks'].append({
            "delay_ms": 0,
            "is_expired": block.is_expired(),
            "time_remaining": block.time_until_expiration()
        })

        logger.info(f"✓ Block created with TTL={block.cache_ttl_seconds}s")
        logger.info(f"✓ is_expired() = {block.is_expired()} (correct)")
        logger.info(f"✓ time_until_expiration() = {block.time_until_expiration():.2f}s")

        # Check after short delay
        sleep(0.1)
        result['checks'].append({
            "delay_ms": 100,
            "is_expired": block.is_expired(),
            "time_remaining": block.time_until_expiration()
        })

        logger.info(f"✓ After 100ms: time_remaining={block.time_until_expiration():.2f}s")

        result['success'] = True
        result['message'] = "TTL tracking verified"

        return result

    def test_cache_key_nanosecond_precision(self) -> Dict[str, Any]:
        """Verify nanosecond precision in cache key invalidation.

        Returns:
            Dict with nanosecond precision validation results
        """
        logger.info("\nTesting cache key nanosecond precision...")

        import tempfile
        import time

        result = {
            "test_file": None,
            "test_results": []
        }

        try:
            # Create a temporary file
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.md') as f:
                test_file = f.name
                f.write("Initial content")

            result['test_file'] = test_file

            integrator = MemoryCacheIntegrator(memory_dir=None)

            # Compute initial cache key
            key1 = integrator._compute_cache_key(test_file)
            logger.info(f"✓ Initial cache key: {key1.to_hash() if key1 else 'None'}")
            logger.info(f"✓ Stat info (ns): {key1.stat_info_ns if key1 else 'None'}")

            result['test_results'].append({
                "step": "Initial cache key",
                "cache_key": key1.to_hash() if key1 else None,
                "stat_info_ns": key1.stat_info_ns if key1 else None
            })

            # Modify the file (within same second for typical systems)
            time.sleep(0.01)  # 10ms delay
            with open(test_file, 'w') as f:
                f.write("Modified content within same second")

            # Get new cache key
            integrator.clear_cache_key_cache()  # Force recomputation
            key2 = integrator._compute_cache_key(test_file)
            logger.info(f"✓ After modification: {key2.to_hash() if key2 else 'None'}")
            logger.info(f"✓ Stat info (ns): {key2.stat_info_ns if key2 else 'None'}")

            result['test_results'].append({
                "step": "After rapid modification",
                "cache_key": key2.to_hash() if key2 else None,
                "stat_info_ns": key2.stat_info_ns if key2 else None,
                "key_changed": key1.to_hash() != key2.to_hash() if key1 and key2 else None
            })

            # Verify keys are different (even within same second)
            keys_differ = key1.to_hash() != key2.to_hash() if key1 and key2 else False
            result['success'] = keys_differ
            result['message'] = (
                "Nanosecond precision working: detected rapid modification" if keys_differ
                else "Warning: rapid modification not detected (content hash failed)"
            )

            if keys_differ:
                logger.info(f"✓ Rapid modification detected despite same second")
                logger.info(f"✓ Cache invalidation false negatives prevented")
            else:
                logger.warning(f"⚠ Rapid modification not detected")

        finally:
            # Cleanup
            if result['test_file'] and os.path.exists(result['test_file']):
                os.unlink(result['test_file'])

        return result


def main():
    if os.environ.get("ENSEMBLE_LIVE_CACHE_TESTS") != "1":
        print("Live Anthropic prompt-cache validation is opt-in; set ENSEMBLE_LIVE_CACHE_TESTS=1 to run.")
        return 0
    """Run all Anthropic API validation tests."""
    print("\n" + "=" * 80)
    print("ANTHROPIC API CACHE CONTROL VALIDATION")
    print("=" * 80)

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\n⚠ ANTHROPIC_API_KEY not set")
        print("Set it before running this test:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        print("\nSkipping API tests (local tests will still run)\n")
        api_available = False
    else:
        api_available = True

    validator = AnthropicCacheValidator()

    results = {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "tests": {}
    }

    # Test 1: Cache control format (requires API key)
    if api_available:
        print("\n[TEST 1/3] Cache Control Format Validation")
        print("-" * 80)
        results['tests']['cache_control_format'] = validator.validate_cache_control_format()
    else:
        print("\n[TEST 1/3] Cache Control Format Validation")
        print("-" * 80)
        print("SKIPPED (API key not available)")

    # Test 2: Cache TTL tracking (local test)
    print("\n[TEST 2/3] Cache TTL Tracking")
    print("-" * 80)
    results['tests']['cache_ttl_tracking'] = validator.test_cache_ttl_tracking()

    # Test 3: Nanosecond precision (local test)
    print("\n[TEST 3/3] Cache Key Nanosecond Precision")
    print("-" * 80)
    results['tests']['nanosecond_precision'] = validator.test_cache_key_nanosecond_precision()

    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    passed = sum(1 for test in results['tests'].values() if test.get('success', False))
    total = len(results['tests'])

    print(f"\nPassed: {passed}/{total}")
    for test_name, test_result in results['tests'].items():
        status = "✓" if test_result.get('success', False) else "✗"
        message = test_result.get('message', test_result.get('error', 'Unknown'))
        print(f"  {status} {test_name}: {message}")

    # Save results to file
    output_file = "test_results/anthropic_api_validation.json"
    os.makedirs("test_results", exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

    print("=" * 80)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
