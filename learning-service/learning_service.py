#!/usr/bin/env python3
"""
Claude Ensemble Learning Service Daemon

Central learning authority for autonomous task outcome recording and analysis.
Runs as a loopback HTTP/JSON service; the daemon owns all state.
Integrates with Thompson router for model performance tracking.
"""

import json
import logging
import socket
import sys
import os
import tempfile
import time
import hashlib
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import threading
import traceback
import uuid

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.request_context import RequestContext
from shared.validators import Validators
from shared.runtime_config import learning_dir, log_dir, socket_path
from shared.service_http import start_service_http
from shared.operational_memory import OperationalMemoryWriter
from learning.portable_artifacts import LearningArtifact, LearningArtifactStore

LOG_DIR = log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / 'claude-learning.log')
    ]
)
logger = logging.getLogger(__name__)

LEARNING_DIR = learning_dir()
SOCKET_PATH = socket_path('ENSEMBLE_LEARNING_SOCKET', '/tmp/claude-learning.sock')
HTTP_PORT = int(os.environ.get('ENSEMBLE_LEARNING_HTTP_PORT', '8768'))



class AutonomousLearningSystem:
    """Load and manage the autonomous learning state"""

    def __init__(self, learning_dir: Path):
        self.learning_dir = Path(learning_dir)
        self.learning_dir.mkdir(parents=True, exist_ok=True)
        self.outcomes_dir = self.learning_dir / 'autonomous_outcomes'
        self.priors_dir = self.learning_dir / 'autonomous_priors'
        self.outcomes_dir.mkdir(parents=True, exist_ok=True)
        self.priors_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoint_lock = threading.Lock()

    def record_outcome(self, task_id: str, task_type: str, model: str,
                      rating: int, tokens: int, cost: float) -> bool:
        """Record a task outcome to disk with atomic write"""
        try:
            outcome = {
                'task_id': task_id,
                'task_type': task_type,
                'model': model,
                'rating': rating,
                'tokens': tokens,
                'cost': cost,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            outcome['payload_sha256'] = self._payload_digest(outcome)

            # Write to outcomes directory (one file per task identity)
            outcome_file = self.outcomes_dir / f"{hashlib.sha256(task_id.encode('utf-8')).hexdigest()}.json"

            if outcome_file.exists():
                logger.info(f"Outcome already persisted: {task_id}")
                return True

            # Atomic write: temp file + rename
            with tempfile.NamedTemporaryFile(mode='w', dir=self.outcomes_dir, delete=False) as tmp:
                json.dump(outcome, tmp, indent=2)
                tmp.flush()
                os.fsync(tmp.fileno())  # Force to disk
                os.replace(tmp.name, outcome_file)  # Atomic rename

            logger.info(f"Recorded outcome: {task_id} ({model}, rating={rating})")
            return True
        except Exception as e:
            logger.error(f"Error recording outcome: {e}")
            return False

    @staticmethod
    def _payload_digest(outcome: Dict[str, Any]) -> str:
        payload = {
            key: outcome.get(key)
            for key in ('task_id', 'task_type', 'model', 'rating', 'tokens', 'cost')
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

    def get_outcome(self, task_id: str) -> Optional[Dict[str, Any]]:
        outcome_file = self.outcomes_dir / f"{hashlib.sha256(task_id.encode('utf-8')).hexdigest()}.json"
        if not outcome_file.exists():
            return None
        try:
            with outcome_file.open('r', encoding='utf-8') as handle:
                outcome = json.load(handle)
            if outcome.get('task_id') != task_id:
                return None
            return outcome
        except Exception as e:
            logger.error(f"Error reading outcome {task_id}: {e}")
            return None

    @classmethod
    def payload_matches(cls, outcome: Dict[str, Any], task_id: str,
                        task_type: str, model: str, rating: int,
                        tokens: int, cost: float) -> bool:
        incoming = {
            'task_id': task_id,
            'task_type': task_type,
            'model': model,
            'rating': rating,
            'tokens': tokens,
            'cost': cost,
        }
        expected_digest = cls._payload_digest(incoming)
        stored_digest = outcome.get('payload_sha256')
        computed_stored_digest = cls._payload_digest(outcome)
        if 'payload_sha256' in outcome and stored_digest != computed_stored_digest:
            return False
        return computed_stored_digest == expected_digest

    @classmethod
    def payload_integrity_valid(cls, outcome: Dict[str, Any]) -> bool:
        """Validate a persisted digest when present; allow verifiable legacy records."""
        stored_digest = outcome.get('payload_sha256')
        return 'payload_sha256' not in outcome or stored_digest == cls._payload_digest(outcome)

    @property
    def checkpoint_path(self) -> Path:
        return self.learning_dir / 'ingestion_checkpoint.json'

    def is_processed(self, task_id: str) -> bool:
        if not self.checkpoint_path.exists():
            return False
        with self.checkpoint_path.open('r', encoding='utf-8') as handle:
            checkpoint = json.load(handle)
        return task_id in checkpoint

    def mark_processed(self, task_id: str) -> None:
        """Record a completed task without losing concurrent checkpoint updates."""
        with self._checkpoint_lock:
            checkpoint = {}
            if self.checkpoint_path.exists():
                with self.checkpoint_path.open('r', encoding='utf-8') as handle:
                    checkpoint = json.load(handle)
            checkpoint[task_id] = datetime.now(timezone.utc).isoformat()
            with tempfile.NamedTemporaryFile(
                mode='w', dir=self.learning_dir, delete=False, encoding='utf-8'
            ) as tmp:
                json.dump(checkpoint, tmp, indent=2, sort_keys=True)
                tmp.flush()
                os.fsync(tmp.fileno())
                os.replace(tmp.name, self.checkpoint_path)

    def get_recent_outcomes(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get outcomes from the last N days"""
        try:
            outcomes = []
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)

            for outcome_file in self.outcomes_dir.glob('*.json'):
                try:
                    with open(outcome_file, 'r') as f:
                        outcome = json.load(f)

                    outcome_time = datetime.fromisoformat(outcome.get('timestamp', ''))
                    if outcome_time.tzinfo is None:
                        outcome_time = outcome_time.replace(tzinfo=timezone.utc)
                    if outcome_time >= cutoff_time:
                        outcomes.append(outcome)
                except Exception as e:
                    logger.debug(f"Error reading {outcome_file}: {e}")

            # Sort by timestamp, most recent first
            outcomes.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            return outcomes
        except Exception as e:
            logger.error(f"Error getting recent outcomes: {e}")
            return []

    def generate_report(self) -> Dict[str, Any]:
        """Generate a learning summary report"""
        try:
            recent = self.get_recent_outcomes(days=30)

            if not recent:
                return {
                    'ok': True,
                    'total_outcomes': 0,
                    'report': 'No outcomes recorded yet'
                }

            # Aggregate by model and task type
            by_model = {}
            by_task_type = {}

            for outcome in recent:
                model = outcome.get('model', 'unknown')
                task_type = outcome.get('task_type', 'unknown')
                rating = outcome.get('rating', 0)
                cost = outcome.get('cost', 0.0)

                if model not in by_model:
                    by_model[model] = {'count': 0, 'total_rating': 0, 'total_cost': 0.0}
                by_model[model]['count'] += 1
                by_model[model]['total_rating'] += rating
                by_model[model]['total_cost'] += cost

                if task_type not in by_task_type:
                    by_task_type[task_type] = {'count': 0, 'total_rating': 0}
                by_task_type[task_type]['count'] += 1
                by_task_type[task_type]['total_rating'] += rating

            # Calculate averages
            model_summary = {}
            for model, stats in by_model.items():
                model_summary[model] = {
                    'count': stats['count'],
                    'avg_rating': round(stats['total_rating'] / stats['count'], 2),
                    'total_cost': round(stats['total_cost'], 4)
                }

            task_summary = {}
            for task_type, stats in by_task_type.items():
                task_summary[task_type] = {
                    'count': stats['count'],
                    'avg_rating': round(stats['total_rating'] / stats['count'], 2)
                }

            return {
                'ok': True,
                'total_outcomes': len(recent),
                'date_range': '30 days',
                'by_model': model_summary,
                'by_task_type': task_summary
            }
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {'ok': False, 'error': str(e)}

    def reset_learning(self) -> bool:
        """Clear all outcomes and priors"""
        try:
            # Remove all files in outcomes and priors directories
            for f in self.outcomes_dir.glob('*.json'):
                f.unlink()
            for f in self.priors_dir.glob('*.json'):
                f.unlink()
            if self.checkpoint_path.exists():
                self.checkpoint_path.unlink()
            logger.info("Reset learning system (cleared all outcomes, priors, and checkpoint)")
            return True
        except Exception as e:
            logger.error(f"Error resetting learning: {e}")
            return False


class LearningService:
    """Learning service daemon - single-threaded, listens on Unix socket"""

    def __init__(
        self,
        socket_path: Path,
        learning_dir: Path,
        operational_memory: OperationalMemoryWriter | None = None,
    ):
        self.socket_path = Path(socket_path)
        self.system = AutonomousLearningSystem(learning_dir)
        self.socket = None
        self.thompson_client = None
        self.operational_memory = operational_memory
        self.artifact_store = LearningArtifactStore(self.system.learning_dir / "portable_artifacts.jsonl")
        self._task_locks: Dict[str, threading.Lock] = {}
        self._task_locks_guard = threading.Lock()

        # Try to import Thompson client for integration
        try:
            from shared.thompson_client import ThompsonClient
            self.thompson_client = ThompsonClient()
            logger.info("Initialized Thompson client for learning integration")
        except Exception as e:
            logger.warning(f"Thompson client not available (optional): {e}")

    def start(self):
        """Run the Learning service over loopback HTTP/JSON only."""
        logger.info("Starting RH Learning Service")
        self.http_server = start_service_http(
            "learning", HTTP_PORT, self._process_request, "op"
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
        logger.info("Learning service stopped")

    def _task_ingestion_lock(self, task_id: str) -> threading.Lock:
        """Serialize one task's full ingestion transaction in this process.

        The daemon is currently single-threaded, but this also protects direct
        concurrent callers. Cross-process and crash-safe exactly-once Thompson
        delivery still requires an external transaction/idempotency mechanism.
        """
        with self._task_locks_guard:
            lock = self._task_locks.get(task_id)
            if lock is None:
                lock = threading.Lock()
                self._task_locks[task_id] = lock
            return lock

    def _record_outcome_fallback(self, model: str, task_type: str, rating: int, cost: float, tokens: int) -> bool:
        """
        Fallback method when Thompson circuit breaker is open.
        Uses cached outcomes or heuristic approaches.

        Returns:
            True if fallback succeeded, False otherwise
        """
        try:
            # Get recent outcomes for this model/task to use as heuristic
            recent = self.system.get_recent_outcomes(days=7)

            # Filter for same model and task type
            matching_outcomes = [
                o for o in recent
                if o.get('model') == model and o.get('task_type') == task_type
            ]

            if matching_outcomes:
                # Use average rating from recent outcomes as hint for Thompson
                avg_rating = sum(o.get('rating', 0) for o in matching_outcomes) / len(matching_outcomes)
                logger.info(
                    f"Fallback: Using cached outcome for {model}/{task_type} "
                    f"(avg_rating={avg_rating}, recent_count={len(matching_outcomes)})"
                )
            else:
                logger.info(f"Fallback: No cached outcomes for {model}/{task_type}, using heuristic")

            # Log the outcome locally for future reference
            logger.warning(f"Fallback: Thompson update not performed for {model}/{task_type} rating={rating}")
            return False

        except Exception as e:
            logger.error(f"Fallback outcome recording failed: {e}")
            return False

    def _handle_client(self, conn: socket.socket):
        """Handle single client request (synchronous, single-threaded) with correlation logging"""
        ctx = RequestContext(caller='learning-client', method='unknown')
        ctx.log_entry()

        try:
            conn.settimeout(5.0)

            # Read request (ends with newline)
            data = b''
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b'\n' in data:
                    break

            request_str = data.decode('utf-8').strip()
            if not request_str:
                return

            # Update method from request
            try:
                req_data = json.loads(request_str)
                ctx.method = req_data.get('op', 'unknown')
            except:
                pass

            response = self._process_request(request_str, ctx)

            # Send response
            conn.sendall((response + '\n').encode('utf-8'))
            # A request can be handled correctly at the transport layer while its
            # business operation fails (for example, Memory returns HTTP 401).
            # Reflect an explicit negative service acknowledgement in request logs.
            try:
                response_data = json.loads(response)
            except (TypeError, json.JSONDecodeError):
                response_data = None
            if isinstance(response_data, dict) and response_data.get('ok') is False:
                ctx.log_exit(
                    status='error',
                    error_code=str(response_data.get('error') or 'operation_failed'),
                )
            else:
                ctx.log_exit(status='success')
        except socket.timeout:
            logger.debug(f"{ctx} Client timeout")
            ctx.log_exit(status='timeout', error_code='timeout')
            try:
                conn.sendall(b'{"ok": false, "error": "timeout"}\n')
            except:
                pass
        except Exception as e:
            logger.error(f"{ctx} Client error: {e}\n{traceback.format_exc()}")
            ctx.log_error('handle_client', e)
            ctx.log_exit(status='error', error_code='client_error')
            try:
                conn.sendall(b'{"ok": false, "error": "server error"}\n')
            except:
                pass
        finally:
            try:
                conn.close()
            except:
                pass

    def _process_request(self, request: str, ctx: RequestContext = None) -> str:
        """Process a request, return JSON response"""
        if ctx is None:
            ctx = RequestContext(caller='learning-service', method='unknown')

        try:
            req_data = json.loads(request)
            operation = req_data.get('op')

            # Validate request fields
            is_valid, error_msg = Validators.validate_learning_request(req_data, operation)
            if not is_valid:
                logger.warning(f"{ctx} Validation error: {error_msg}")
                return json.dumps({'ok': False, 'error': error_msg, 'request_id': ctx.request_id})

            if operation == 'process_outcome':
                task_id = req_data.get('task_id')
                task_type = req_data.get('task_type')
                model = req_data.get('model')
                rating = req_data.get('rating')
                tokens = req_data.get('tokens')
                cost = req_data.get('cost')

                logger.info(f"{ctx} Processing outcome: {task_id} ({model}, rating={rating}, cost=${cost:.4f})")
                with self._task_ingestion_lock(task_id):
                    # Verify the durable payload before trusting the checkpoint.
                    # Otherwise a reused task ID or corrupted record could be
                    # acknowledged as an idempotent retry without validation.
                    processed = self.system.is_processed(task_id)
                    persisted = self.system.get_outcome(task_id)
                    if persisted is None:
                        if processed:
                            return json.dumps({
                                'ok': False,
                                'error': 'processed task has no readable persisted outcome',
                                'integrity_error': True,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id
                            })
                        success = self.system.record_outcome(
                            task_id, task_type, model, rating, tokens, cost
                        )
                        if not success:
                            return json.dumps({
                                'ok': False,
                                'thompson': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id
                            })
                        effective = self.system.get_outcome(task_id)
                        if effective is None:
                            return json.dumps({
                                'ok': False,
                                'thompson': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id
                            })
                    else:
                        if not self.system.payload_integrity_valid(persisted):
                            return json.dumps({
                                'ok': False,
                                'error': 'stored outcome failed integrity validation',
                                'integrity_error': True,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id
                            })
                        if not self.system.payload_matches(
                            persisted, task_id, task_type, model, rating, tokens, cost
                        ):
                            return json.dumps({
                                'ok': False,
                                'error': 'task_id already exists with a different outcome payload',
                                'conflict': True,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id
                            })
                        if processed:
                            return json.dumps({
                                'ok': True, 'duplicate': True, 'request_id': ctx.request_id
                            })
                        effective = persisted

                    effective_task_type = effective['task_type']
                    effective_model = effective['model']
                    effective_rating = effective['rating']
                    effective_tokens = effective['tokens']
                    effective_cost = effective['cost']

                    memory_recorded = False
                    if self.operational_memory is not None:
                        memory_recorded = self.operational_memory.write_event(
                            event_id=task_id,
                            event_type='learning.outcome',
                            source='learning-service',
                            payload={
                                'task_id': task_id,
                                'task_type': effective_task_type,
                                'model': effective_model,
                                'rating': effective_rating,
                                'success': effective_rating >= 3,
                                'tokens': effective_tokens,
                                'cost': effective_cost,
                                'timestamp': effective.get('timestamp'),
                            },
                        )
                        if not memory_recorded:
                            return json.dumps({
                                'ok': False,
                                'memory': False,
                                'thompson': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id,
                            })

                    thompson_updated = False
                    if self.thompson_client:
                        circuit_state = None
                        if hasattr(self.thompson_client, 'get_circuit_breaker_state'):
                            circuit_state = self.thompson_client.get_circuit_breaker_state()

                        if circuit_state and circuit_state.get('state') == 'open':
                            logger.warning(
                                f"{ctx} Thompson circuit breaker is OPEN, using fallback outcome recording"
                            )
                            fallback_success = self._record_outcome_fallback(
                                effective_model, effective_task_type,
                                effective_rating, effective_cost, effective_tokens
                            )
                            return json.dumps({
                                'ok': False,
                                'thompson': fallback_success,
                                'checkpoint_advanced': False,
                                'circuit_breaker': 'open',
                                'request_id': ctx.request_id
                            })

                        try:
                            logger.info(f"{ctx} Updating Thompson router for {effective_model}")
                            if not self.thompson_client.record_outcome(
                                model=effective_model,
                                task_type=effective_task_type,
                                success=(effective_rating >= 3),
                                cost=effective_cost,
                                tokens=effective_tokens
                            ):
                                return json.dumps({
                                    'ok': False,
                                    'thompson': False,
                                    'checkpoint_advanced': False,
                                    'request_id': ctx.request_id
                                })
                            thompson_updated = True
                        except Exception as e:
                            logger.warning(f"{ctx} Failed to update Thompson: {e}")
                            return json.dumps({
                                'ok': False,
                                'thompson': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id
                            })

                    if self.operational_memory is not None and thompson_updated:
                        try:
                            state = self.thompson_client.get_state()
                        except Exception as exc:
                            logger.warning(f"{ctx} Failed to snapshot Thompson state: {exc}")
                            return json.dumps({
                                'ok': False,
                                'memory': memory_recorded,
                                'thompson': True,
                                'thompson_state_memory': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id,
                            })
                        if state is None:
                            logger.warning(f"{ctx} Thompson state snapshot returned no state")
                            return json.dumps({
                                'ok': False,
                                'memory': memory_recorded,
                                'thompson': True,
                                'thompson_state_memory': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id,
                            })
                        try:
                            snapshot_recorded = self.operational_memory.write_event(
                                event_id=f'thompson-state:{task_id}',
                                event_type='thompson.state',
                                source='learning-service',
                                payload={'state': state},
                            )
                        except Exception as exc:
                            logger.warning(f"{ctx} Failed to persist Thompson state snapshot: {exc}")
                            return json.dumps({
                                'ok': False,
                                'memory': memory_recorded,
                                'thompson': True,
                                'thompson_state_memory': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id,
                            })
                        if not snapshot_recorded:
                            logger.warning(f"{ctx} Thompson state snapshot was not persisted")
                            return json.dumps({
                                'ok': False,
                                'memory': memory_recorded,
                                'thompson': True,
                                'thompson_state_memory': False,
                                'checkpoint_advanced': False,
                                'request_id': ctx.request_id,
                            })

                    self.system.mark_processed(task_id)
                    return json.dumps({
                        'ok': True,
                        'memory': memory_recorded,
                        'thompson': thompson_updated,
                        'checkpoint_advanced': True,
                        'request_id': ctx.request_id
                    })
            elif operation == 'record_artifact':
                artifact = LearningArtifact.from_dict(req_data.get('artifact'))
                run_id = artifact.payload.get('run_id')
                if not isinstance(run_id, str) or not run_id.strip():
                    return json.dumps({'ok': False, 'error': 'artifact.payload.run_id is required', 'request_id': ctx.request_id})

                artifact_status = self.artifact_store.record_once(
                    artifact, f"{artifact.artifact_type}:{run_id}"
                )
                memory_recorded = False
                # Deliberately retry the same deterministic Memory event even when
                # the local artifact store reports "duplicate": a prior attempt may
                # have persisted the artifact but failed before Memory acknowledged it.
                # Operational Memory must deduplicate by this stable event_id.
                if self.operational_memory is not None:
                    memory_recorded = self.operational_memory.write_event(
                        event_id=f"learning-artifact:{artifact.artifact_type}:{run_id}",
                        event_type='learning.artifact',
                        source='learning-service',
                        payload=artifact.to_dict(),
                    )
                if not memory_recorded:
                    return json.dumps({'ok': False, 'memory': False, 'artifact_status': artifact_status, 'knowledge_promotion': 'not_attempted', 'request_id': ctx.request_id})
                return json.dumps({'ok': True, 'memory': True, 'artifact_status': artifact_status, 'knowledge_promotion': 'not_attempted', 'request_id': ctx.request_id})

            elif operation == 'get_report':
                logger.info(f"{ctx} Generating learning report")
                report = self.system.generate_report()
                report['request_id'] = ctx.request_id
                return json.dumps(report)

            elif operation == 'get_recent_outcomes':
                days = req_data.get('days', 7)
                logger.info(f"{ctx} Fetching recent outcomes (days={days})")
                outcomes = self.system.get_recent_outcomes(days)
                return json.dumps({'ok': True, 'outcomes': outcomes, 'request_id': ctx.request_id})

            elif operation == 'reset_learning':
                logger.info(f"{ctx} Resetting learning system")
                success = self.system.reset_learning()
                return json.dumps({'ok': success, 'request_id': ctx.request_id})

            elif operation == 'ping':
                return json.dumps({'ok': True, 'message': 'pong', 'request_id': ctx.request_id})

            else:
                ctx.log_error('process_request', Exception(f'Unknown operation: {operation}'))
                return json.dumps({'ok': False, 'error': f'Unknown operation: {operation}', 'request_id': ctx.request_id})

        except json.JSONDecodeError as e:
            ctx.log_error('json_decode', e)
            return json.dumps({'ok': False, 'error': 'Invalid JSON', 'request_id': ctx.request_id})
        except Exception as e:
            logger.error(f"{ctx} Request error: {e}\n{traceback.format_exc()}")
            ctx.log_error('process_request', e)
            return json.dumps({'ok': False, 'error': str(e), 'request_id': ctx.request_id})


if __name__ == '__main__':
    service = LearningService(
        SOCKET_PATH,
        LEARNING_DIR,
        operational_memory=OperationalMemoryWriter(),
    )
    service.start()
