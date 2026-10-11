"""End-to-end execution workflow coverage across real local service boundaries.

The test deliberately uses real Thompson, Memory, Learning, and Graph service
implementations and their public IPC/HTTP clients. The model provider is a deterministic local
provider adapter so CI does not require external credentials or a hosted model.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from arbitration.orchestrator import ArbitrationOrchestrator, TaskType
from execution import ExecutionContext, ExecutionEngine, ExecutionStatus
from execution.nodes import ModelExecution
from providers.model_provider import ModelProvider, ModelRequest, ModelResponse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "memory-service"))
sys.path.insert(0, str(ROOT / "learning"))
sys.path.insert(0, str(ROOT / "learning-service"))
sys.path.insert(0, str(ROOT / "shared"))

from shared import thompson_client as thompson_client_module
from learning_client import LearningClient
from memory_client import MemoryClient
from learning_service import LearningService
from memory_service import MemoryService


def _load_thompson_service():
    spec = importlib.util.spec_from_file_location(
        "claude_ensemble_thompson_service",
        ROOT / "thompson-service" / "thompson_service.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_graph_service():
    spec = importlib.util.spec_from_file_location(
        "claude_ensemble_graph_service",
        ROOT / "graph-service" / "graph_service.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DeterministicWorkflowProvider(ModelProvider):
    """A real ModelProvider implementation with deterministic local behavior."""

    name = "deterministic-local"

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        model = request.model or "default"
        if model == "arbiter":
            text = json.dumps(
                {
                    "adjudicated_result": "The worker evidence supports the execution result.",
                    "selected_worker": "worker-a",
                    "rationale": "The result is directly supported by the supplied execution context.",
                    "supporting_evidence": ["execution completed successfully"],
                    "rejected_alternatives": ["worker-b"],
                    "next_phase_questions": [],
                }
            )
        else:
            text = f"{model} verified objective: execution workflow completed."
        return ModelResponse(
            provider=self.name,
            model=model,
            text=text,
            input_tokens=len(request.prompt.split()),
            output_tokens=len(text.split()),
            cost_usd=0.0,
        )


def _wait_for(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    pytest.fail("timed out waiting for local service readiness")


def _json_request(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        request = urllib.request.Request(url)
    else:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    with urllib.request.urlopen(request, timeout=3.0) as response:
        return json.loads(response.read().decode("utf-8"))


def test_end_to_end_execution_workflow_persists_across_service_boundaries(tmp_path: Path, monkeypatch) -> None:
    """Exercise request -> execution -> arbitration -> learning -> Memory/Graph."""

    thompson_socket = tmp_path / "thompson.sock"
    thompson_state = tmp_path / "thompson-state.json"
    memory_socket = tmp_path / "memory.sock"
    memory_dir = tmp_path / "memory"
    learning_socket = tmp_path / "learning.sock"
    learning_dir = tmp_path / "learning"
    graph_store = tmp_path / "graph.json"

    thompson_service = None
    thompson_thread = None
    memory_service = None
    memory_thread = None
    learning_service = None
    learning_thread = None
    graph_server = None
    graph_thread = None

    try:
        thompson_module = _load_thompson_service()
        original_thompson_client = thompson_client_module.ThompsonClient

        class TemporaryThompsonClient(original_thompson_client):
            def __init__(self, *args, **kwargs):
                kwargs["socket_path"] = thompson_socket
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(thompson_client_module, "ThompsonClient", TemporaryThompsonClient)
        thompson_service = thompson_module.ThompsonService(thompson_socket, thompson_state)
        thompson_thread = threading.Thread(target=thompson_service.start, daemon=True)
        thompson_thread.start()
        thompson_client = TemporaryThompsonClient(
            socket_path=thompson_socket, enable_circuit_breaker=False
        )
        _wait_for(lambda: thompson_socket.exists())
        _wait_for(lambda: thompson_client.get_state() is not None)

        memory_service = MemoryService(memory_socket, memory_dir)
        memory_thread = threading.Thread(target=memory_service.start, daemon=True)
        memory_thread.start()

        learning_service = LearningService(learning_socket, learning_dir)
        learning_thread = threading.Thread(target=learning_service.start, daemon=True)
        learning_thread.start()

        graph_module = _load_graph_service()
        graph_server = graph_module.create_server("127.0.0.1", 0, graph_store)
        graph_thread = threading.Thread(target=graph_server.serve_forever, daemon=True)
        graph_thread.start()

        memory_client = MemoryClient(memory_socket)
        learning_client = LearningClient(learning_socket)

        _wait_for(lambda: memory_client.connect())
        _wait_for(lambda: learning_socket.exists())
        graph_url = f"http://127.0.0.1:{graph_server.server_port}"
        _wait_for(lambda: _json_request(f"{graph_url}/health").get("ok") is True)

        provider = DeterministicWorkflowProvider()
        context = ExecutionContext(
            request_id="e2e-136",
            objective="verify the execution workflow",
            artifact={"name": "e2e-artifact", "value": "known"},
            requirements=("produce a successful result",),
            evidence=("local deterministic evidence",),
            constraints=("no external network",),
        )

        execution_node = ModelExecution(
            execution_id="e2e-136.execution",
            stage="solve",
            worker_id="worker-a",
            provider=provider,
            model="worker-a",
            prompt="Verify the execution workflow.",
        )

        execution_result = ExecutionEngine(
            memory_client=memory_client,
            memory_name="e2e_136",
        ).execute(execution_node, context)

        assert execution_result.status is ExecutionStatus.SUCCESS
        assert "worker-a verified objective" in execution_result.output

        serialized_context = json.dumps(context.to_dict(), sort_keys=True)
        execution_evidence = (
            f"Verify the execution workflow result.\n\n"
            f"Execution output: {execution_result.output}\n"
            f"Execution metadata: execution_id={execution_result.execution_id}; "
            f"status={execution_result.status.value}\n"
            f"Original execution context: {serialized_context}"
        )
        arbitration = ArbitrationOrchestrator(
            TaskType.DESIGN_VALIDATION,
            execution_evidence,
            providers={
                "worker-a": provider,
                "worker-b": provider,
                "arbiter": provider,
            },
        )
        arbitration.context_manager.load_files([ROOT / "execution" / "context.py"])
        arbitration.add_phase(
            ["worker-a", "worker-b"],
            "arbiter",
            "Evaluate the execution result and supplied source evidence.",
        )
        arbitration_request_start = len(provider.requests)
        final_result = arbitration.run()

        assert final_result == "The worker evidence supports the execution result."
        assert len(arbitration.results) == 1
        assert all(result.succeeded for result in arbitration.results[0][0])
        worker_requests = [
            request.prompt
            for request in provider.requests[arbitration_request_start:]
            if request.model in {"worker-a", "worker-b"}
        ]
        assert len(worker_requests) == 2
        for prompt in worker_requests:
            assert execution_result.output in prompt
            assert serialized_context in prompt

        task_id = "e2e-136"
        assert learning_client.process_outcome(
            task_id=task_id,
            task_type="design_validation",
            model="arbiter",
            rating=1,
            tokens=execution_result.output_tokens,
            cost=0.0,
        )
        _wait_for(lambda: (learning_dir / "autonomous_outcomes").exists())
        outcome_files = list((learning_dir / "autonomous_outcomes").glob("*.json"))
        assert len(outcome_files) == 1
        assert json.loads(outcome_files[0].read_text())["task_id"] == task_id

        # Learning may acknowledge an outcome even if its optional Thompson
        # integration has degraded. Verify both the Thompson service response
        # and its on-disk state: get_state() alone only proves an in-memory update.
        # Learning maps ratings below 3 to Thompson failures, so rating=1 must
        # record a failure rather than a success.
        def thompson_recorded_outcome() -> bool:
            state = thompson_client.get_state()
            stats = state.get("models", {}).get("arbiter") if state else None
            return bool(stats and stats.get("calls", 0) >= 1 and stats.get("failures", 0) >= 1)

        _wait_for(thompson_recorded_outcome)
        thompson_stats = thompson_client.get_state()["models"]["arbiter"]
        assert thompson_stats["calls"] >= 1
        assert thompson_stats["failures"] >= 1
        assert thompson_stats.get("successes", 0) == 0

        def thompson_state_persisted() -> bool:
            try:
                persisted = json.loads(thompson_state.read_text())
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                return False
            stats = persisted.get("models", {}).get("arbiter", {})
            return (
                stats.get("calls", 0) >= 1
                and stats.get("failures", 0) >= 1
                and stats.get("successes", 0) == 0
            )

        _wait_for(thompson_state_persisted)
        persisted_thompson_stats = json.loads(thompson_state.read_text())["models"]["arbiter"]
        assert persisted_thompson_stats["calls"] >= 1
        assert persisted_thompson_stats["failures"] >= 1
        assert persisted_thompson_stats.get("successes", 0) == 0

        memory_entries = memory_client.entries("e2e_136")
        assert memory_entries
        persisted_context = memory_entries[-1]["execution_context"]
        assert persisted_context["request_id"] == "e2e-136"
        assert persisted_context["artifact"] == {"name": "e2e-artifact", "value": "known"}

        request_node = _json_request(
            f"{graph_url}/graph/add-node",
            {
                "type": "execution",
                "properties": {
                    "request_id": context.request_id,
                    "execution_id": execution_result.execution_id,
                    "status": execution_result.status.value,
                },
            },
        )
        learning_node = _json_request(
            f"{graph_url}/graph/add-node",
            {
                "type": "learning",
                "properties": {
                    "task_id": task_id,
                    "model": "arbiter",
                    "rating": 1,
                    "adjudicated_result": final_result,
                },
            },
        )
        assert request_node["ok"] and learning_node["ok"]

        edge = _json_request(
            f"{graph_url}/graph/add-edge",
            {
                "source": request_node["node"]["id"],
                "target": learning_node["node"]["id"],
                "type": "produced-learning",
                "properties": {"request_id": context.request_id},
            },
        )
        assert edge["ok"]

        traversal = _json_request(
            f"{graph_url}/graph/traverse",
            {"start": request_node["node"]["id"], "direction": "out", "max_depth": 1},
        )
        assert traversal["ok"]
        traversed_learning = traversal["results"][0]["node"]
        assert traversed_learning["properties"]["task_id"] == task_id
        assert traversed_learning["properties"]["adjudicated_result"] == final_result

    finally:
        if graph_server is not None:
            if graph_thread is not None and graph_thread.is_alive():
                graph_server.shutdown()
            graph_server.server_close()
        if graph_thread is not None:
            graph_thread.join(timeout=2.0)
        if learning_service is not None:
            learning_service.stop()
        if memory_service is not None:
            memory_service.stop()
        if thompson_service is not None:
            thompson_service.stop()
        if learning_thread is not None:
            learning_thread.join(timeout=2.0)
        if memory_thread is not None:
            memory_thread.join(timeout=2.0)
        if thompson_thread is not None:
            thompson_thread.join(timeout=2.0)
