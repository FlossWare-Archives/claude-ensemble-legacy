#!/usr/bin/env python3
"""
Request correlation context for RH daemon services.

Provides request context with unique IDs for tracing requests through
distributed service calls, with enhanced logging and performance metrics.

Usage:
    ctx = RequestContext(caller='thompson-client', method='select_model')
    # Use ctx.request_id in logs and client calls
    # ctx.log_entry() at request start
    # ctx.log_exit(status, error_code) at request end
"""

import uuid
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class RequestContext:
    """
    Tracks a request through its lifecycle with unique correlation ID,
    timing, and status information for observability.
    """

    def __init__(self, caller: str = 'unknown', method: str = 'unknown'):
        """
        Initialize request context.

        Args:
            caller: Name of service/client making request (e.g., 'thompson-client')
            method: Method/action being called (e.g., 'select_model')
        """
        self.request_id = str(uuid.uuid4())
        self.caller = caller
        self.method = method
        self.start_time = time.time()
        self.status = None
        self.error_code = None
        self.error_message = None
        self.result = None

    @property
    def duration_ms(self) -> float:
        """Calculate elapsed duration in milliseconds"""
        return (time.time() - self.start_time) * 1000

    def log_entry(self) -> None:
        """Log request entry point"""
        logger.info(
            f"[{self.request_id}] Request from {self.caller} {self.method} started"
        )

    def log_exit(self, status: str = 'success', error_code: Optional[str] = None) -> None:
        """
        Log request completion with timing and status.

        Args:
            status: Status of request ('success', 'error', 'timeout', etc.)
            error_code: Optional error code if request failed
        """
        self.status = status
        self.error_code = error_code
        duration = self.duration_ms

        # Log slow requests
        if duration > 1000:
            logger.warning(
                f"[{self.request_id}] SLOW REQUEST: {duration:.0f}ms "
                f"(caller={self.caller}, method={self.method})"
            )

        logger.info(
            f"[{self.request_id}] Request completed in {duration:.0f}ms "
            f"with status {status}"
        )

    def log_error(self, action: str, error: Exception) -> None:
        """
        Log an error with full context.

        Args:
            action: What action was being attempted
            error: Exception that was raised
        """
        self.error_message = str(error)
        logger.error(
            f"[{self.request_id}] Failed to {action}: {error}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dict for passing to other services"""
        return {
            'request_id': self.request_id,
            'caller': self.caller,
            'method': self.method,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }

    def __str__(self) -> str:
        """String representation for logging"""
        return f"[{self.request_id}]"
