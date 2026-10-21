#!/usr/bin/env python3
"""
RH Thompson Router Client

Session-side client for connecting to Thompson Router Service.
Used by claude-ensemble and hooks to select models and record outcomes.
Gracefully degrades if daemon is down.
"""

import json
import logging
import os
import urllib.error
import urllib.request
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional, Any


logger = logging.getLogger(__name__)

DEFAULT_SERVICE_URL = os.environ.get('ENSEMBLE_THOMPSON_URL', 'http://127.0.0.1:8769').rstrip('/')


class CircuitState(Enum):
    """States for circuit breaker pattern"""
    CLOSED = "closed"          # Normal operation, allow requests
    OPEN = "open"              # Too many failures, reject requests
    HALF_OPEN = "half_open"    # Testing if service recovered


class CircuitBreaker:
    """
    Circuit breaker for preventing cascade failures.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests rejected with fallback
    - HALF_OPEN: Testing recovery, allow one request

    Transitions:
    - CLOSED -> OPEN: After failure_threshold consecutive failures
    - OPEN -> HALF_OPEN: After timeout (default 30s)
    - HALF_OPEN -> CLOSED: If test request succeeds
    - HALF_OPEN -> OPEN: If test request fails
    """

    def __init__(self, failure_threshold: int = 3, timeout_seconds: float = 30.0):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of consecutive failures before opening
            timeout_seconds: Time to wait in OPEN state before trying HALF_OPEN
        """
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.opened_at = None

    def record_success(self) -> None:
        """Record a successful request"""
        if self.state == CircuitState.HALF_OPEN:
            # Recovered successfully
            logger.info("Circuit breaker: HALF_OPEN -> CLOSED (recovery successful)")
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.opened_at = None
        elif self.state == CircuitState.CLOSED:
            # Normal success, keep track
            self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request"""
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.state == CircuitState.CLOSED and self.failure_count >= self.failure_threshold:
            # Too many consecutive failures, open the circuit
            logger.warning(f"Circuit breaker: CLOSED -> OPEN ({self.failure_count} consecutive failures)")
            self.state = CircuitState.OPEN
            self.opened_at = datetime.now(timezone.utc)
        elif self.state == CircuitState.HALF_OPEN:
            # Failed to recover, go back to OPEN
            logger.warning("Circuit breaker: HALF_OPEN -> OPEN (recovery failed)")
            self.state = CircuitState.OPEN
            self.opened_at = datetime.now(timezone.utc)
            self.failure_count = 1  # Reset for next recovery attempt

    def try_request(self) -> bool:
        """
        Check if a request should be allowed.

        Returns:
            True if request should be attempted, False if circuit is open
        """
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if self.opened_at is None:
                return False

            elapsed = (datetime.now(timezone.utc) - self.opened_at).total_seconds()
            if elapsed >= self.timeout_seconds:
                # Timeout elapsed, try to recover
                logger.info("Circuit breaker: OPEN -> HALF_OPEN (timeout elapsed, testing recovery)")
                self.state = CircuitState.HALF_OPEN
                self.failure_count = 0
                return True

            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow the test request
            return True

        return False

    def is_open(self) -> bool:
        """Check if circuit is currently open"""
        return self.state == CircuitState.OPEN

    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state for debugging"""
        return {
            'state': self.state.value,
            'failure_count': self.failure_count,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'last_failure_time': self.last_failure_time.isoformat() if self.last_failure_time else None
        }


class ThompsonClient:
    """Client for the Claude Ensemble Thompson router service."""

    def __init__(self, timeout: float = 2.0, enable_circuit_breaker: bool = True,
                 service_url: str | None = None):
        self.service_url = (service_url or DEFAULT_SERVICE_URL).rstrip('/')
        self.timeout = timeout
        self.connected = False
        self.circuit_breaker = None

        if enable_circuit_breaker:
            self.circuit_breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=30.0)

    def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to service, return parsed response"""
        # Check circuit breaker before attempting request
        if self.circuit_breaker and not self.circuit_breaker.try_request():
            logger.warning(f"Thompson service circuit breaker is OPEN, request blocked")
            return {'ok': False, 'error': 'circuit_breaker_open'}

        try:
            data = json.dumps(request).encode('utf-8')
            http_request = urllib.request.Request(
                self.service_url + '/thompson', data=data, method='POST',
                headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            )
            try:
                with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read().decode('utf-8'))

            if not isinstance(payload, dict) or payload.get('ok') is not True:
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()
                return payload if isinstance(payload, dict) else {'ok': False, 'error': 'invalid_response'}
            if self.circuit_breaker:
                self.circuit_breaker.record_success()
            return payload
        except TimeoutError:
            logger.warning("Thompson service timeout")
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            return {'ok': False, 'error': 'timeout'}
        except Exception as e:
            logger.warning(f"Thompson service error: {e}")
            if self.circuit_breaker:
                self.circuit_breaker.record_failure()
            return {'ok': False, 'error': str(e)}

    def select_model(self, task_type: str, required_capability: float = 0.5, max_cost: float = float('inf'), request_id: str = None) -> str:
        """
        Select best model using Thompson Sampling.

        Args:
            task_type: Type of task (e.g., 'code-review', 'refactoring')
            required_capability: Minimum capability required (0-1)
            max_cost: Maximum cost threshold per call
            request_id: Request correlation ID for tracing

        Returns:
            Selected model name (falls back to 'haiku' if service unavailable)
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        response = self._send_request({
            'action': 'select_model',
            'task_type': task_type,
            'required_capability': required_capability,
            'max_cost': max_cost,
            'request_id': request_id
        })

        if response.get('ok'):
            logger.info(f"[{request_id}] Selected model: {response.get('model', 'haiku')}")
            return response.get('model', 'haiku')
        else:
            logger.warning(f"[{request_id}] Thompson service unavailable, falling back to haiku")
            return 'haiku'

    def record_outcome(self, model: str, task_type: str, success: bool, cost: float, tokens: int = 0, request_id: str = None) -> bool:
        """
        Record outcome of a model call.

        Args:
            model: Model that was used
            task_type: Type of task
            success: Whether the task succeeded
            cost: Cost of the call (in dollars)
            tokens: Tokens used (optional)
            request_id: Request correlation ID for tracing

        Returns:
            True if recorded successfully, False if service unavailable
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        response = self._send_request({
            'action': 'record_outcome',
            'model': model,
            'task_type': task_type,
            'success': success,
            'cost': cost,
            'tokens': tokens,
            'request_id': request_id
        })

        if not response.get('ok'):
            logger.warning(f"[{request_id}] Failed to record outcome: {response.get('error')}")
            return False

        logger.info(f"[{request_id}] Recorded outcome for {model}: success={success}, cost=${cost:.4f}")
        return True

    def get_state(self) -> Optional[Dict[str, Any]]:
        """
        Get full state snapshot from service.

        Returns:
            State dict with model stats, or None if service unavailable
        """
        response = self._send_request({'action': 'get_state'})

        if response.get('ok'):
            return response.get('state')
        else:
            logger.warning(f"Failed to get state: {response.get('error')}")
            return None

    def reset(self, model: str) -> bool:
        """
        Reset history for a model.

        Args:
            model: Model name to reset

        Returns:
            True if reset successfully, False if service unavailable
        """
        response = self._send_request({
            'action': 'reset',
            'model': model
        })

        if not response.get('ok'):
            logger.warning(f"Failed to reset model: {response.get('error')}")
            return False

        return True

    def ping(self) -> bool:
        """Check if service is running"""
        response = self._send_request({'action': 'ping'})
        return response.get('ok', False)

    def get_circuit_breaker_state(self) -> Optional[Dict[str, Any]]:
        """Get circuit breaker state for debugging"""
        if self.circuit_breaker:
            return self.circuit_breaker.get_state()
        return None


if __name__ == '__main__':
    # Test client
    logging.basicConfig(level=logging.INFO)
    client = ThompsonClient()

    if client.ping():
        print("✓ Connected to Thompson service")
        model = client.select_model("test-task")
        print(f"  Selected model: {model}")
    else:
        print("✗ Thompson service not available (will use fallback)")

    # Show circuit breaker state
    cb_state = client.get_circuit_breaker_state()
    if cb_state:
        print(f"Circuit breaker state: {cb_state}")
