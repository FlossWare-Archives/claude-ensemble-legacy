#!/usr/bin/env python3
"""
Test suite for Circuit Breaker pattern in Thompson Client

Tests the circuit breaker's ability to prevent cascade failures by:
1. Opening after N consecutive failures
2. Blocking requests when open
3. Attempting recovery after timeout
4. Closing on successful recovery
"""

import sys
import time
import logging
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path
root_repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_repo))
sys.path.insert(0, str(root_repo / "shared"))

from thompson_client import CircuitBreaker, CircuitState, ThompsonClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CircuitBreakerTester:
    """Test helper for circuit breaker"""

    def test_initial_state_closed(self):
        """Test that circuit breaker starts in CLOSED state"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.try_request() is True
        print("✓ Initial state is CLOSED")

    def test_open_after_threshold(self):
        """Test that circuit opens after failure threshold is reached"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1.0)

        # Record 3 failures
        for i in range(3):
            cb.record_failure()
            if i < 2:
                assert cb.state == CircuitState.CLOSED
            else:
                assert cb.state == CircuitState.OPEN

        # Should not allow requests when open
        assert cb.try_request() is False
        print("✓ Circuit opens after 3 consecutive failures")

    def test_close_on_success(self):
        """Test that circuit closes when success is recorded in CLOSED state"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1.0)

        # Record failure then success
        cb.record_failure()
        assert cb.failure_count == 1

        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED

        print("✓ Failure count resets on success")

    def test_half_open_after_timeout(self):
        """Test that circuit transitions to HALF_OPEN after timeout"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=0.5)

        # Open the circuit
        for i in range(3):
            cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert cb.try_request() is False

        # Wait for timeout
        time.sleep(0.6)

        # Should transition to HALF_OPEN
        assert cb.try_request() is True
        assert cb.state == CircuitState.HALF_OPEN
        print("✓ Circuit transitions to HALF_OPEN after timeout")

    def test_closed_from_half_open_success(self):
        """Test that HALF_OPEN -> CLOSED on successful request"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=0.5)

        # Open -> HALF_OPEN
        for i in range(3):
            cb.record_failure()
        time.sleep(0.6)
        cb.try_request()

        assert cb.state == CircuitState.HALF_OPEN

        # Record success
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        print("✓ Circuit closes from HALF_OPEN on success")

    def test_open_from_half_open_failure(self):
        """Test that HALF_OPEN -> OPEN on failed request"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=0.5)

        # Open -> HALF_OPEN
        for i in range(3):
            cb.record_failure()
        time.sleep(0.6)
        cb.try_request()

        assert cb.state == CircuitState.HALF_OPEN

        # Record failure
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        print("✓ Circuit goes back to OPEN from HALF_OPEN on failure")

    def test_blocks_requests_when_open(self):
        """Test that circuit blocks requests when open"""
        cb = CircuitBreaker(failure_threshold=2, timeout_seconds=10.0)

        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Multiple attempts should all be blocked
        for _ in range(5):
            assert cb.try_request() is False

        print("✓ Circuit blocks multiple requests when open")

    def test_get_state(self):
        """Test that get_state returns valid state info"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1.0)

        cb.record_failure()
        cb.record_failure()

        state = cb.get_state()
        assert state['state'] == 'closed'
        assert state['failure_count'] == 2
        assert state['last_failure_time'] is not None

        # Open the circuit
        cb.record_failure()
        state = cb.get_state()
        assert state['state'] == 'open'
        assert state['opened_at'] is not None

        print("✓ get_state returns valid state information")

    def test_thompson_client_integration(self):
        """Test circuit breaker integration with ThompsonClient"""
        # Create client with circuit breaker enabled
        with patch('thompson_client.socket.socket') as mock_socket_class:
            mock_socket = MagicMock()
            mock_socket_class.return_value = mock_socket

            # Simulate failures
            mock_socket.recv.side_effect = Exception("Connection failed")

            client = ThompsonClient(
                socket_path=Path('/tmp/test-thompson.sock'),
                timeout=0.1,
                enable_circuit_breaker=True
            )

            # Patch Path.exists as a method (not attribute)
            original_exists = Path.exists
            Path.exists = lambda self: True

            try:
                # Make 3 failing requests
                for i in range(3):
                    response = client.select_model("test-task")
                    assert response == 'haiku'  # Should return fallback

                # Circuit should be open now
                assert client.circuit_breaker.state == CircuitState.OPEN

                # 4th request should be blocked immediately
                response = client.select_model("test-task")
                assert response == 'haiku'

                cb_state = client.get_circuit_breaker_state()
                assert cb_state['state'] == 'open'

            finally:
                # Restore original method
                Path.exists = original_exists

        print("✓ Circuit breaker integrates with ThompsonClient")

    def test_consecutive_failures_only(self):
        """Test that only consecutive failures trigger opening"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1.0)

        # Failure, success, failure pattern
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        cb.record_success()  # Reset counter
        assert cb.failure_count == 0

        cb.record_failure()
        assert cb.failure_count == 1  # Start over

        # Need 3 consecutive, so still closed
        assert cb.state == CircuitState.CLOSED

        print("✓ Only consecutive failures count")

    def test_5_consecutive_failures(self):
        """Test the specific scenario: 5 consecutive socket failures"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=0.5)

        failures = []
        for i in range(5):
            cb.record_failure()
            failures.append({
                'attempt': i + 1,
                'state': cb.state.value,
                'can_request': cb.try_request()
            })

        # Check the progression
        assert failures[0]['state'] == 'closed'  # 1st failure
        assert failures[1]['state'] == 'closed'  # 2nd failure
        assert failures[2]['state'] == 'open'    # 3rd failure opens
        assert failures[3]['state'] == 'open'    # stays open
        assert failures[4]['state'] == 'open'    # stays open

        # After timeout, should transition to HALF_OPEN
        time.sleep(0.6)
        can_retry = cb.try_request()
        assert can_retry is True
        assert cb.state == CircuitState.HALF_OPEN

        print("✓ 5 consecutive socket failures handled correctly")

    def run_all_tests(self):
        """Run all tests"""
        print("\n=== Circuit Breaker Tests ===\n")

        tests = [
            self.test_initial_state_closed,
            self.test_open_after_threshold,
            self.test_close_on_success,
            self.test_half_open_after_timeout,
            self.test_closed_from_half_open_success,
            self.test_open_from_half_open_failure,
            self.test_blocks_requests_when_open,
            self.test_get_state,
            self.test_consecutive_failures_only,
            self.test_5_consecutive_failures,
            self.test_thompson_client_integration,
        ]

        failed = 0
        for test in tests:
            try:
                test()
            except AssertionError as e:
                print(f"✗ {test.__name__} failed: {e}")
                failed += 1
            except Exception as e:
                print(f"✗ {test.__name__} error: {e}")
                import traceback
                traceback.print_exc()
                failed += 1

        print(f"\n{'✓ All tests passed!' if failed == 0 else f'✗ {failed} test(s) failed'}\n")
        return failed == 0


if __name__ == '__main__':
    tester = CircuitBreakerTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
