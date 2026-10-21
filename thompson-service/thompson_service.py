#!/usr/bin/env python3
"""
Claude Ensemble Thompson Router Service Daemon

Central Thompson Sampling authority for model selection across Claude Code sessions.
Runs as a loopback HTTP/JSON service and owns its persisted sampling state.
"""

import json
import logging
import socket
import sys
import os
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Optional, Any
import uuid
import numpy as np
from scipy.stats import beta as beta_dist

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.request_context import RequestContext
from shared.validators import Validators
from shared.runtime_config import log_dir, socket_path, thompson_state_file
from shared.service_http import start_service_http

LOG_DIR = log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'claude-thompson.log')
    ]
)
logger = logging.getLogger(__name__)

SOCKET_PATH = socket_path('ENSEMBLE_THOMPSON_SOCKET', '/tmp/claude-thompson.sock')
HTTP_PORT = int(os.environ.get('ENSEMBLE_THOMPSON_HTTP_PORT', '8769'))
STATE_FILE = thompson_state_file()


class ModelStats:
    """Statistics for a single model"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self.successes = 0
        self.failures = 0
        self.total_cost = 0.0
        self.total_tokens = 0
        self.calls = 0
        self.last_updated = datetime.now(timezone.utc).isoformat()

    @property
    def success_rate(self) -> float:
        """Success rate for Beta distribution prior"""
        if self.calls == 0:
            return 0.5  # Neutral prior
        return self.successes / self.calls

    @property
    def avg_cost(self) -> float:
        """Average cost per call"""
        if self.calls == 0:
            return 0.0
        return self.total_cost / self.calls

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict"""
        return {
            'model_name': self.model_name,
            'successes': self.successes,
            'failures': self.failures,
            'total_cost': self.total_cost,
            'total_tokens': self.total_tokens,
            'calls': self.calls,
            'last_updated': self.last_updated
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ModelStats':
        """Deserialize from dict"""
        model = cls(data['model_name'])
        model.successes = data.get('successes', 0)
        model.failures = data.get('failures', 0)
        model.total_cost = data.get('total_cost', 0.0)
        model.total_tokens = data.get('total_tokens', 0)
        model.calls = data.get('calls', 0)
        model.last_updated = data.get('last_updated', datetime.now(timezone.utc).isoformat())
        return model


class ThompsonState:
    """Manages Thompson Sampling state with file persistence"""

    def __init__(self, state_file: Path):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.models: Dict[str, ModelStats] = {}
        self.last_updated = datetime.now(timezone.utc).isoformat()
        self._load()

    def _load(self):
        """Load state from file"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)

                self.last_updated = data.get('last_updated', datetime.now(timezone.utc).isoformat())

                # Load model stats
                for model_data in data.get('models', {}).values():
                    stats = ModelStats.from_dict(model_data)
                    self.models[stats.model_name] = stats

                logger.info(f"Loaded state for {len(self.models)} models")
            except Exception as e:
                logger.error(f"Error loading state: {e}")
                self.models = {}
        else:
            logger.info(f"State file not found, starting fresh: {self.state_file}")

    def save(self):
        """Save state to file with atomic write"""
        try:
            self.last_updated = datetime.now(timezone.utc).isoformat()

            data = {
                'last_updated': self.last_updated,
                'models': {
                    model_name: stats.to_dict()
                    for model_name, stats in self.models.items()
                }
            }

            # Atomic write: temp file + rename
            with tempfile.NamedTemporaryFile(mode='w', dir=self.state_file.parent, delete=False) as tmp:
                json.dump(data, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())  # Force to disk
                os.replace(tmp.name, self.state_file)  # Atomic rename

            logger.info(f"Saved state for {len(self.models)} models")
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def get_or_create_model(self, model_name: str) -> ModelStats:
        """Get or create stats for a model"""
        if model_name not in self.models:
            self.models[model_name] = ModelStats(model_name)
        return self.models[model_name]

    def select_model(self, task_type: str, required_capability: float = 0.5, max_cost: float = float('inf')) -> str:
        """
        Select best model using Thompson Sampling.

        Args:
            task_type: Type of task (for future capability matrix integration)
            required_capability: Minimum capability required (0-1)
            max_cost: Maximum cost threshold per call

        Returns:
            Selected model name
        """
        if not self.models:
            logger.warning("No models loaded, returning fallback 'haiku'")
            return 'haiku'

        # Filter models by cost constraint
        candidates = {
            name: stats
            for name, stats in self.models.items()
            if stats.avg_cost <= max_cost or stats.calls == 0  # Allow untested models
        }

        if not candidates:
            logger.warning(f"No models within cost threshold {max_cost}, using cheapest")
            candidates = {
                min(self.models.items(), key=lambda x: x[1].avg_cost)[0]:
                self.models[min(self.models.items(), key=lambda x: x[1].avg_cost)[0]]
            }

        # Sample from Beta posteriors (Thompson Sampling)
        best_model = None
        best_sample = -1

        for model_name, stats in candidates.items():
            # Beta(alpha, beta) where alpha = successes + 1, beta = failures + 1
            alpha = stats.successes + 1
            beta = stats.failures + 1
            sample = np.random.beta(alpha, beta)

            if sample > best_sample:
                best_sample = sample
                best_model = model_name

        logger.info(f"Selected model: {best_model} (sample={best_sample:.4f}, task={task_type})")
        return best_model

    def record_outcome(self, model_name: str, task_type: str, success: bool, cost: float, tokens: int) -> bool:
        """
        Record outcome of a model call.

        Args:
            model_name: Model that was used
            task_type: Type of task
            success: Whether the task succeeded
            cost: Cost of the call
            tokens: Tokens used

        Returns:
            True if recorded successfully
        """
        try:
            stats = self.get_or_create_model(model_name)

            if success:
                stats.successes += 1
            else:
                stats.failures += 1

            stats.total_cost += cost
            stats.total_tokens += tokens
            stats.calls += 1
            stats.last_updated = datetime.now(timezone.utc).isoformat()

            self.save()
            logger.info(f"Recorded outcome for {model_name}: success={success}, cost={cost:.4f}")
            return True
        except Exception as e:
            logger.error(f"Error recording outcome: {e}")
            return False

    def reset(self, model_name: str) -> bool:
        """Reset history for a model"""
        try:
            if model_name in self.models:
                self.models[model_name] = ModelStats(model_name)
                self.save()
                logger.info(f"Reset model: {model_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error resetting model: {e}")
            return False

    def get_state(self) -> Dict[str, Any]:
        """Get full state snapshot"""
        return {
            'last_updated': self.last_updated,
            'models': {
                name: stats.to_dict()
                for name, stats in self.models.items()
            }
        }


class ThompsonService:
    """Thompson Router Service Daemon"""

    def __init__(self, socket_path: Path, state_file: Path):
        self.socket_path = Path(socket_path)
        self.state = ThompsonState(state_file)
        self.socket = None

    def start(self):
        """Run the Thompson service over loopback HTTP/JSON only."""
        logger.info("Starting RH Thompson Router Service")
        self.http_server = start_service_http(
            "thompson",
            HTTP_PORT,
            lambda raw: self._process_request(
                raw, RequestContext(caller="thompson-rest", method="http")
            ),
            "action",
        )
        try:
            # The HTTP adapter owns its serving thread. Keep this process alive
            # until shutdown; do not create or accept Unix-domain socket clients.
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Shutting down")
        finally:
            self.stop()

    def stop(self):
        """Stop the REST service cleanly."""
        server = getattr(self, "http_server", None)
        if server is not None:
            server.shutdown()
            server.server_close()
        logger.info("Thompson service stopped")

    def _process_request(self, request: str, ctx: RequestContext) -> str:
        """Process a request, return JSON response"""
        try:
            req_data = json.loads(request)
            action = req_data.get('action')

            # Validate request fields
            is_valid, error_msg = Validators.validate_thompson_request(req_data, action)
            if not is_valid:
                logger.warning(f"{ctx} Validation error: {error_msg}")
                ctx.log_error('validation', Exception(error_msg))
                return json.dumps({'ok': False, 'error': error_msg, 'request_id': ctx.request_id})

            if action == 'select_model':
                task_type = req_data.get('task_type', 'unknown')
                required_capability = req_data.get('required_capability', 0.5)
                max_cost = req_data.get('max_cost', float('inf'))

                model = self.state.select_model(task_type, required_capability, max_cost)
                logger.info(f"{ctx} Model selected: {model}")
                return json.dumps({'ok': True, 'model': model, 'request_id': ctx.request_id})

            elif action == 'record_outcome':
                model = req_data.get('model')
                task_type = req_data.get('task_type', 'unknown')
                success = req_data.get('success', False)
                cost = req_data.get('cost', 0.0)
                tokens = req_data.get('tokens', 0)

                logger.info(f"{ctx} Recording outcome: {model} (success={success}, cost=${cost:.4f})")
                ok = self.state.record_outcome(model, task_type, success, cost, tokens)
                return json.dumps({'ok': ok, 'request_id': ctx.request_id})

            elif action == 'get_state':
                state = self.state.get_state()
                logger.info(f"{ctx} Returning state snapshot")
                return json.dumps({'ok': True, 'state': state, 'request_id': ctx.request_id})

            elif action == 'reset':
                model = req_data.get('model')
                logger.info(f"{ctx} Resetting model: {model}")
                ok = self.state.reset(model)
                return json.dumps({'ok': ok, 'request_id': ctx.request_id})

            elif action == 'ping':
                return json.dumps({'ok': True, 'message': 'pong', 'request_id': ctx.request_id})

            else:
                ctx.log_error('process_request', Exception(f'Unknown action: {action}'))
                return json.dumps({'ok': False, 'error': f'Unknown action: {action}', 'request_id': ctx.request_id})

        except json.JSONDecodeError as e:
            ctx.log_error('json_decode', e)
            return json.dumps({'ok': False, 'error': 'Invalid JSON', 'request_id': ctx.request_id})
        except Exception as e:
            ctx.log_error('process_request', e)
            return json.dumps({'ok': False, 'error': str(e), 'request_id': ctx.request_id})


if __name__ == '__main__':
    service = ThompsonService(SOCKET_PATH, STATE_FILE)
    service.start()
