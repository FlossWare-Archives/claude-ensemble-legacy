#!/usr/bin/env python3
"""
RH Learning Client

Session-side client for connecting to Learning Service.
Used by sessions to record task outcomes and retrieve learning summaries.
Graceful degradation if daemon is not running.
"""

import json
import os
import logging
import uuid
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_URL = os.environ.get('ENSEMBLE_LEARNING_URL', 'http://127.0.0.1:8768').rstrip('/')


class LearningClient:
    """Client for RH Learning Service"""

    def __init__(self, service_url: str = DEFAULT_SERVICE_URL, timeout: float = 3.0):
        """Initialize the REST client for the Learning service."""
        self.service_url = service_url.rstrip("/")
        self.timeout = timeout

    def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON request over HTTP and return the parsed response."""
        import urllib.error
        import urllib.request

        try:
            body = json.dumps(request).encode("utf-8")
            http_request = urllib.request.Request(
                self.service_url + "/learning",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(http_request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
            return {"ok": False, "error": "invalid_response"}
        except Exception as exc:
            logger.warning("Learning REST request failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def record_artifact(self, artifact: Dict[str, Any], request_id: str = None) -> Dict[str, Any]:
        """Submit a portable learning artifact to the canonical Learning service."""
        if request_id is None:
            request_id = str(uuid.uuid4())
        return self._send_request({
            "op": "record_artifact",
            "artifact": artifact,
            "request_id": request_id,
        })

    def process_outcome(self, task_id: str, task_type: str, model: str,
                       rating: int, tokens: int, cost: float, request_id: str = None) -> bool:
        """Record a task outcome

        Args:
            task_id: Unique task identifier
            task_type: Type of task (code-review, documentation, testing, etc.)
            model: Model used (haiku, sonnet, opus, etc.)
            rating: Quality rating (0-5)
            tokens: Tokens used
            cost: Cost in dollars
            request_id: Request correlation ID for tracing

        Returns:
            True if recorded successfully, False otherwise
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        response = self._send_request({
            'op': 'process_outcome',
            'task_id': task_id,
            'task_type': task_type,
            'model': model,
            'rating': rating,
            'tokens': tokens,
            'cost': cost,
            'request_id': request_id
        })

        if response.get('ok'):
            logger.info(f"[{request_id}] Recorded outcome: {task_id} ({model}, rating={rating})")
        else:
            logger.warning(f"[{request_id}] Failed to record outcome: {response.get('error')}")

        return response.get('ok', False)

    def get_report(self, request_id: str = None) -> Dict[str, Any]:
        """Get learning summary report

        Args:
            request_id: Request correlation ID for tracing

        Returns:
            Report dict with model/task statistics
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        response = self._send_request({'op': 'get_report', 'request_id': request_id})
        if response.get('ok'):
            logger.info(f"[{request_id}] Retrieved learning report")
            return response
        logger.warning(f"[{request_id}] Failed to get report: {response.get('error')}")
        return {'ok': False, 'error': 'Failed to get report', 'request_id': request_id}

    def get_recent_outcomes(self, days: int = 7, request_id: str = None) -> List[Dict[str, Any]]:
        """Get recent task outcomes

        Args:
            days: Number of days to look back (default 7)
            request_id: Request correlation ID for tracing

        Returns:
            List of outcome dicts
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        response = self._send_request({
            'op': 'get_recent_outcomes',
            'days': days,
            'request_id': request_id
        })
        if response.get('ok'):
            logger.info(f"[{request_id}] Retrieved {len(response.get('outcomes', []))} recent outcomes")
            return response.get('outcomes', [])
        logger.warning(f"[{request_id}] Failed to get recent outcomes: {response.get('error')}")
        return []

    def reset_learning(self, request_id: str = None) -> bool:
        """Clear all learning outcomes and priors

        Args:
            request_id: Request correlation ID for tracing

        Returns:
            True if reset successfully, False otherwise
        """
        if request_id is None:
            request_id = str(uuid.uuid4())

        response = self._send_request({'op': 'reset_learning', 'request_id': request_id})
        if response.get('ok'):
            logger.info(f"[{request_id}] Learning system reset")
        else:
            logger.warning(f"[{request_id}] Failed to reset learning: {response.get('error')}")
        return response.get('ok', False)


if __name__ == '__main__':
    # Test client
    logging.basicConfig(level=logging.INFO)
    client = LearningClient()

    # Try to connect and ping
    result = client._send_request({'op': 'ping'})
    if result.get('ok'):
        print("✓ Connected to learning service")
        report = client.get_report()
        print(f"  Report: {json.dumps(report, indent=2)}")
    else:
        print("✗ Learning service not available (will fall back to local mode)")
