#!/usr/bin/env python3
"""
Test suite for RH Learning Service and Client
"""

import sys
import json
import hashlib
import time
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from learning.learning_client import LearningClient


class LearningServiceTester:
    """Test helper for learning service"""

    def __init__(self):
        self.socket_path = Path('/tmp/claude-learning-test.sock')
        self.service_process = None
        self.temp_dir = None

    def start_test_service(self):
        """Start a test instance of the learning service"""
        # Create temp directory for test data
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self.temp_dir.name)

        # Start service with custom socket and data dir
        service_script = str(Path(__file__).parent / 'learning_service.py')
        project_root = str(Path(__file__).parent.parent)

        # Create a wrapper script that uses our test socket
        wrapper = f"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, '{project_root}')

# Import from learning_service.py directly
import importlib.util
spec = importlib.util.spec_from_file_location("learning_service", "{service_script}")
module = importlib.util.module_from_spec(spec)
sys.modules["learning_service"] = module
spec.loader.exec_module(module)

service = module.LearningService(
    socket_path=Path('{self.socket_path}'),
    learning_dir=Path('{temp_path / "learning"}')
)
service.thompson_client = None
service.start()
"""

        print(f"Starting test service on {self.socket_path}")
        self.service_process = subprocess.Popen(
            [sys.executable, '-c', wrapper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # Wait for socket to appear
        for i in range(50):  # 5 seconds max
            if self.socket_path.exists():
                print("✓ Service started")
                time.sleep(0.5)  # Extra stability
                return True
            time.sleep(0.1)

        # If failed, print stderr for debugging
        if self.service_process.poll() is not None:
            _, stderr = self.service_process.communicate()
            if stderr:
                print(f"Service error: {stderr.decode()}")

        print("✗ Service failed to start")
        return False

    def stop_test_service(self):
        """Stop the test service"""
        if self.service_process:
            self.service_process.terminate()
            try:
                self.service_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.service_process.kill()

        if self.socket_path.exists():
            self.socket_path.unlink()

        if self.temp_dir:
            self.temp_dir.cleanup()

        print("✓ Service stopped")

    def test_basic_ping(self):
        """Test basic ping"""
        client = LearningClient(socket_path=self.socket_path)
        response = client._send_request({'op': 'ping'})
        assert response.get('ok'), "Ping failed"
        print("✓ Ping test passed")

    def test_process_outcome(self):
        """Test recording an outcome"""
        client = LearningClient(socket_path=self.socket_path)

        success = client.process_outcome(
            task_id='test_001',
            task_type='code-review',
            model='haiku',
            rating=4,
            tokens=1200,
            cost=0.005
        )
        assert success, "Failed to process outcome"
        print("✓ Process outcome test passed")

    def test_get_report(self):
        """Test getting a report"""
        client = LearningClient(socket_path=self.socket_path)

        # Record some outcomes first
        for i in range(3):
            client.process_outcome(
                task_id=f'test_{i:03d}',
                task_type='documentation',
                model='haiku',
                rating=5,
                tokens=500,
                cost=0.002
            )

        report = client.get_report()
        assert report.get('ok'), "Failed to get report"
        assert report.get('total_outcomes', 0) >= 3, "Report missing outcomes"
        print(f"✓ Get report test passed (outcomes: {report.get('total_outcomes')})")

    def test_get_recent_outcomes(self):
        """Test getting recent outcomes"""
        client = LearningClient(socket_path=self.socket_path)

        # Record outcomes
        for i in range(2):
            client.process_outcome(
                task_id=f'recent_{i:03d}',
                task_type='testing',
                model='sonnet',
                rating=3,
                tokens=2000,
                cost=0.01
            )

        outcomes = client.get_recent_outcomes(days=7)
        assert len(outcomes) >= 2, "Failed to get recent outcomes"
        print(f"✓ Get recent outcomes test passed (outcomes: {len(outcomes)})")

    def test_multiple_models(self):
        """Test tracking multiple models"""
        client = LearningClient(socket_path=self.socket_path)

        models = ['haiku', 'sonnet', 'opus']
        for model in models:
            client.process_outcome(
                task_id=f'model_test_{model}',
                task_type='refactoring',
                model=model,
                rating=4,
                tokens=1500,
                cost=0.01 if model == 'haiku' else 0.05
            )

        report = client.get_report()
        assert report.get('ok'), "Failed to get report"
        by_model = report.get('by_model', {})
        assert len(by_model) >= 3, f"Expected 3+ models in report, got {len(by_model)}"
        print(f"✓ Multiple models test passed (models: {list(by_model.keys())})")

    def test_idempotent_replay(self):
        client = LearningClient(socket_path=self.socket_path)
        payload = {
            'op': 'process_outcome',
            'task_id': 'idempotent_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        }
        first = client._send_request(payload)
        second = client._send_request(payload)
        assert first.get('ok'), first
        assert first.get('thompson') is False
        assert second.get('ok'), second
        assert second.get('duplicate') is True
        outcomes = client.get_recent_outcomes(days=7)
        assert sum(o.get('task_id') == 'idempotent_001' for o in outcomes) == 1
        print("✓ Idempotent replay test passed")

    def test_checkpoint_survives_restart(self):
        client = LearningClient(socket_path=self.socket_path)
        payload = {
            'op': 'process_outcome',
            'task_id': 'restart_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        }
        response = client._send_request(payload)
        assert response.get('ok'), response
        self.service_process.terminate()
        self.service_process.wait(timeout=2)

        learning_dir = Path(self.temp_dir.name) / 'learning'
        assert (learning_dir / 'ingestion_checkpoint.json').exists()

        service_script = str(Path(__file__).parent / 'learning_service.py')
        project_root = str(Path(__file__).parent.parent)
        wrapper = f"""
import sys
from pathlib import Path
sys.path.insert(0, '{project_root}')
import importlib.util
spec = importlib.util.spec_from_file_location("learning_service", "{service_script}")
module = importlib.util.module_from_spec(spec)
sys.modules["learning_service"] = module
spec.loader.exec_module(module)
service = module.LearningService(
    socket_path=Path('{self.socket_path}'),
    learning_dir=Path('{learning_dir}')
)
service.thompson_client = None
service.start()
"""
        self.service_process = subprocess.Popen(
            [sys.executable, '-c', wrapper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        response = None
        for _ in range(50):
            if self.socket_path.exists():
                response = client._send_request(payload)
                if response.get('ok'):
                    break
            time.sleep(0.1)
        assert response is not None
        assert response.get('ok'), response
        assert response.get('duplicate') is True
        print("✓ Checkpoint survives service restart")

    def test_checkpoint_does_not_advance_on_thompson_failure(self):
        from learning_service import LearningService
        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'failure-learning'
        )

        class FailedThompson:
            def get_circuit_breaker_state(self):
                return {'state': 'closed'}

            def record_outcome(self, **kwargs):
                return False

        service.thompson_client = FailedThompson()
        response = json.loads(service._process_request(json.dumps({
            'op': 'process_outcome',
            'task_id': 'thompson_failure_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        })))
        assert response['ok'] is False
        assert response['checkpoint_advanced'] is False
        assert not service.system.is_processed('thompson_failure_001')
        print("✓ Failed learning does not advance checkpoint")

    def test_checkpoint_does_not_advance_on_thompson_exception(self):
        from learning_service import LearningService
        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'exception-learning'
        )

        class RaisingThompson:
            def get_circuit_breaker_state(self):
                return {'state': 'closed'}

            def record_outcome(self, **kwargs):
                raise RuntimeError('temporary Thompson failure')

        service.thompson_client = RaisingThompson()
        response = json.loads(service._process_request(json.dumps({
            'op': 'process_outcome',
            'task_id': 'thompson_exception_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        })))
        assert response['ok'] is False
        assert response['thompson'] is False
        assert response['checkpoint_advanced'] is False
        assert not service.system.is_processed('thompson_exception_001')
        print("✓ Thompson exception does not advance checkpoint")

    def test_same_task_concurrent_ingestion_updates_thompson_once(self):
        from learning_service import LearningService

        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'concurrent-learning'
        )

        class CountingThompson:
            def __init__(self):
                self.calls = 0
                self.lock = threading.Lock()

            def get_circuit_breaker_state(self):
                return {'state': 'closed'}

            def record_outcome(self, **kwargs):
                with self.lock:
                    self.calls += 1
                time.sleep(0.05)
                return True

        thompson = CountingThompson()
        service.thompson_client = thompson
        payload = {
            'op': 'process_outcome',
            'task_id': 'concurrent_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        }

        responses = []
        barrier = threading.Barrier(2)

        def submit():
            barrier.wait()
            responses.append(json.loads(service._process_request(json.dumps(payload))))

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert thompson.calls == 1
        assert sum(response.get('ok') is True for response in responses) == 2
        assert sum(response.get('duplicate') is True for response in responses) == 1
        print("✓ Concurrent identical ingestion updates Thompson once")

    def test_different_tasks_concurrent_ingestion_preserves_checkpoint(self):
        from learning_service import LearningService

        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'concurrent-checkpoint-learning'
        )

        class CountingThompson:
            def __init__(self):
                self.calls = 0
                self.lock = threading.Lock()

            def get_circuit_breaker_state(self):
                return {'state': 'closed'}

            def record_outcome(self, **kwargs):
                with self.lock:
                    self.calls += 1
                time.sleep(0.05)
                return True

        thompson = CountingThompson()
        service.thompson_client = thompson
        barrier = threading.Barrier(2)
        responses = []

        def submit(task_id):
            payload = {
                'op': 'process_outcome',
                'task_id': task_id,
                'task_type': 'testing',
                'model': 'haiku',
                'rating': 4,
                'tokens': 1000,
                'cost': 0.005,
            }
            barrier.wait()
            responses.append(
                json.loads(service._process_request(json.dumps(payload)))
            )

        threads = [
            threading.Thread(target=submit, args=('checkpoint_concurrent_a',)),
            threading.Thread(target=submit, args=('checkpoint_concurrent_b',)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(response.get('ok') is True for response in responses)
        assert thompson.calls == 2

        checkpoint = json.loads(service.system.checkpoint_path.read_text(encoding='utf-8'))
        assert set(checkpoint) == {
            'checkpoint_concurrent_a',
            'checkpoint_concurrent_b',
        }

        restarted = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'concurrent-checkpoint-learning'
        )
        restarted.thompson_client = None
        assert restarted.system.is_processed('checkpoint_concurrent_a')
        assert restarted.system.is_processed('checkpoint_concurrent_b')
        print("✓ Concurrent different-task ingestion preserves checkpoint across restart")

    def test_same_task_different_payload_is_rejected(self):
        from learning_service import LearningService

        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'conflict-learning'
        )

        class FailedThompson:
            def get_circuit_breaker_state(self):
                return {'state': 'closed'}

            def record_outcome(self, **kwargs):
                return False

        service.thompson_client = FailedThompson()

        first = {
            'op': 'process_outcome',
            'task_id': 'conflict_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        }
        second = dict(first, rating=2)

        first_response = json.loads(service._process_request(json.dumps(first)))
        second_response = json.loads(service._process_request(json.dumps(second)))

        assert first_response['ok'] is False
        assert first_response['checkpoint_advanced'] is False
        assert second_response['ok'] is False
        assert second_response['conflict'] is True
        assert not service.system.is_processed('conflict_001')
        print("✓ Same task ID with different payload is rejected")

    def test_processed_task_conflict_and_corrupt_digest_are_rejected(self):
        from learning_service import LearningService

        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'processed-integrity-learning'
        )
        service.thompson_client = None
        payload = {
            'op': 'process_outcome',
            'task_id': 'processed_integrity_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        }

        first = json.loads(service._process_request(json.dumps(payload)))
        assert first['ok'] is True, first
        assert first['checkpoint_advanced'] is True, first
        checkpoint_before = service.system.checkpoint_path.read_text(encoding='utf-8')

        conflicting = dict(payload, rating=1)
        conflict = json.loads(service._process_request(json.dumps(conflicting)))
        assert conflict['ok'] is False, conflict
        assert conflict['conflict'] is True, conflict
        assert service.system.checkpoint_path.read_text(encoding='utf-8') == checkpoint_before

        outcome_path = service.system.outcomes_dir / (
            hashlib.sha256(payload['task_id'].encode('utf-8')).hexdigest() + '.json'
        )
        stored = json.loads(outcome_path.read_text(encoding='utf-8'))
        stored['payload_sha256'] = '0' * 64
        outcome_path.write_text(json.dumps(stored), encoding='utf-8')

        corrupt = json.loads(service._process_request(json.dumps(payload)))
        assert corrupt['ok'] is False, corrupt
        assert corrupt['integrity_error'] is True, corrupt
        assert service.system.checkpoint_path.read_text(encoding='utf-8') == checkpoint_before
        print("✓ Processed task conflicts and corrupted outcome digests are rejected")

    def test_retry_uses_persisted_payload_for_thompson(self):
        from learning_service import LearningService

        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'persisted-payload-learning'
        )

        class RecordingThompson:
            def __init__(self):
                self.calls = []

            def get_circuit_breaker_state(self):
                return {'state': 'closed'}

            def record_outcome(self, **kwargs):
                self.calls.append(kwargs)
                return True

        thompson = RecordingThompson()
        service.thompson_client = thompson

        payload = {
            'op': 'process_outcome',
            'task_id': 'persisted_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        }
        assert service.system.record_outcome(
            payload['task_id'],
            payload['task_type'],
            payload['model'],
            payload['rating'],
            payload['tokens'],
            payload['cost']
        )

        retry_response = json.loads(service._process_request(json.dumps(payload)))

        assert retry_response['ok'] is True
        assert retry_response['thompson'] is True
        assert retry_response['checkpoint_advanced'] is True
        assert len(thompson.calls) == 1
        assert thompson.calls[0]['model'] == 'haiku'
        assert thompson.calls[0]['task_type'] == 'testing'
        assert thompson.calls[0]['success'] is True
        print("✓ Persisted outcome remains the source of truth")

    def test_checkpoint_does_not_advance_on_record_failure(self):
        from learning_service import LearningService
        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'record-failure-learning'
        )
        service.thompson_client = None
        service.system.record_outcome = lambda *args, **kwargs: False

        response = json.loads(service._process_request(json.dumps({
            'op': 'process_outcome',
            'task_id': 'record_failure_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        })))
        assert response['ok'] is False
        assert response['thompson'] is False
        assert response['checkpoint_advanced'] is False
        assert not service.system.is_processed('record_failure_001')
        print("✓ Failed outcome recording does not advance checkpoint")

    def test_operational_outcome_is_written_to_memory(self):
        from learning_service import LearningService

        class RecordingMemory:
            def __init__(self):
                self.events = []

            def write_event(self, event_id, event_type, source, payload):
                self.events.append((event_id, event_type, source, payload))
                return True

        memory = RecordingMemory()
        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'operational-memory-learning',
            operational_memory=memory,
        )
        service.thompson_client = None

        response = json.loads(service._process_request(json.dumps({
            'op': 'process_outcome',
            'task_id': 'memory_outcome_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        })))

        assert response['ok'] is True
        assert response['memory'] is True
        assert response['checkpoint_advanced'] is True
        assert len(memory.events) == 1
        event_id, event_type, source, payload = memory.events[0]
        assert event_id == 'memory_outcome_001'
        assert event_type == 'learning.outcome'
        assert source == 'learning-service'
        assert payload['model'] == 'haiku'
        assert payload['success'] is True
        print("✓ Learning outcome persisted to operational Memory")

    def test_operational_memory_failure_blocks_checkpoint(self):
        from learning_service import LearningService

        class FailedMemory:
            def write_event(self, *args, **kwargs):
                return False

        service = LearningService(
            self.socket_path,
            Path(self.temp_dir.name) / 'operational-memory-failure',
            operational_memory=FailedMemory(),
        )
        service.thompson_client = None

        response = json.loads(service._process_request(json.dumps({
            'op': 'process_outcome',
            'task_id': 'memory_failure_001',
            'task_type': 'testing',
            'model': 'haiku',
            'rating': 4,
            'tokens': 1000,
            'cost': 0.005,
        })))

        assert response['ok'] is False
        assert response['memory'] is False
        assert response['checkpoint_advanced'] is False
        assert not service.system.is_processed('memory_failure_001')
        print("✓ Memory failure leaves outcome eligible for retry")

    def test_reset_learning(self):
        """Test resetting learning system"""
        client = LearningClient(socket_path=self.socket_path)

        # Record outcome
        client.process_outcome(
            task_id='reset_test',
            task_type='testing',
            model='haiku',
            rating=4,
            tokens=1000,
            cost=0.005
        )

        # Get initial count
        report_before = client.get_report()
        count_before = report_before.get('total_outcomes', 0)

        # Reset
        success = client.reset_learning()
        assert success, "Failed to reset learning"

        # Check count after reset
        report_after = client.get_report()
        count_after = report_after.get('total_outcomes', 0)

        assert count_after == 0, f"Reset failed: still have {count_after} outcomes"
        print("✓ Reset learning test passed")

    def run_all_tests(self):
        """Run all tests"""
        print("\n=== RH Learning Service Tests ===\n")

        try:
            if not self.start_test_service():
                return False

            self.test_basic_ping()
            self.test_process_outcome()
            self.test_get_report()
            self.test_get_recent_outcomes()
            self.test_multiple_models()
            self.test_idempotent_replay()
            self.test_checkpoint_survives_restart()
            self.test_checkpoint_does_not_advance_on_thompson_failure()
            self.test_checkpoint_does_not_advance_on_thompson_exception()
            self.test_checkpoint_does_not_advance_on_record_failure()
            self.test_same_task_concurrent_ingestion_updates_thompson_once()
            self.test_different_tasks_concurrent_ingestion_preserves_checkpoint()
            self.test_same_task_different_payload_is_rejected()
            self.test_processed_task_conflict_and_corrupt_digest_are_rejected()
            self.test_retry_uses_persisted_payload_for_thompson()
            self.test_operational_outcome_is_written_to_memory()
            self.test_operational_memory_failure_blocks_checkpoint()
            self.test_reset_learning()

            print("\n✓ All tests passed!\n")
            return True

        except AssertionError as e:
            print(f"\n✗ Test failed: {e}\n")
            return False
        except Exception as e:
            print(f"\n✗ Unexpected error: {e}\n")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self.stop_test_service()


if __name__ == '__main__':
    tester = LearningServiceTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)
