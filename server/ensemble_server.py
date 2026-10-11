#!/usr/bin/env python3
"""Canonical Ensemble REST boundary.

The gateway is the single client-facing REST server. Services remain
independently deployable and are integrated through HTTP/JSON only.
"""
from __future__ import annotations
import json
import hmac
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from capability_health import CapabilityHealthView
from experiments import Experiment, ExperimentStore, evaluate as evaluate_experiment
from decision_provenance import DecisionProvenanceStore, DecisionRecord
from operational_metrics import MetricsRecord, MetricsStore
from policy import Policy, evaluate
from collaboration import CollaborationOrchestrator
from collaboration.reviewer import MCPReviewer
from shared.operational_memory import OperationalMemoryWriter

LOG = logging.getLogger(__name__)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
API_PREFIX = "/api/v1"
DEFAULT_SERVICE_URLS = {
    "graph": "http://127.0.0.1:8766",
    "memory": "http://127.0.0.1:8767",
    "learning": None,
    "arbitration": None,
    "thompson": None,
}

MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024
MAX_FORWARD_HOPS = 4
DEFAULT_COLLAB_SOLVERS = ("sonnet", "haiku")
DEFAULT_COLLAB_ARBITER = "opus"
DEFAULT_COLLAB_REVIEWERS: tuple[str, ...] = ()
DEFAULT_COLLAB_MAX_ROUNDS = 3
DEFAULT_COLLAB_MAX_SOLVER_CALLS = 6
DEFAULT_COLLAB_MAX_REVIEW_CALLS = 18
DEFAULT_COLLAB_MAX_ARBITER_CALLS = 6
FORWARD_MARKER = "X-Ensemble-Forwarded"
FORWARD_SAFE_HEADERS = {"content-type", "accept", "x-request-id", "x-correlation-id"}
HEALTH_CACHE_TTL_SECONDS = float(os.environ.get("ENSEMBLE_HEALTH_CACHE_TTL", "30"))
_health_cache: dict[str, tuple[float, bool]] = {}
_health_cache_lock = threading.Lock()

def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > 16 * 1024 * 1024:
        raise ValueError("request body must be between 1 byte and 16 MiB")
    value = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value

def _env_csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _env_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


class AuthenticationRequired(PermissionError):
    """Raised when collaboration credentials are missing or invalid."""


def _require_collaboration_auth(handler: BaseHTTPRequestHandler) -> None:
    expected = os.environ.get("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "")
    if not expected:
        raise ServiceUnavailable(
            "collaboration endpoint is disabled until ENSEMBLE_COLLABORATION_AUTH_TOKEN is configured"
        )
    supplied = handler.headers.get("Authorization", "")
    scheme, _, token = supplied.partition(" ")
    if scheme.lower() != "bearer" or not token or not hmac.compare_digest(token, expected):
        raise AuthenticationRequired("invalid collaboration authorization")


def _send(handler: BaseHTTPRequestHandler, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
    body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    if headers:
        for name, value in headers.items():
            handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

class ServiceUnavailable(RuntimeError):
    pass

def _request_body(handler: BaseHTTPRequestHandler) -> bytes:
    raw_length = handler.headers.get("Content-Length")
    if raw_length is None:
        raise ValueError("Content-Length is required")
    try:
        length = int(raw_length)
    except ValueError as exc:
        raise ValueError("Content-Length must be an integer") from exc
    if length <= 0 or length > MAX_REQUEST_BODY_BYTES:
        raise ValueError("request body must be between 1 byte and 16 MiB")
    return handler.rfile.read(length)

def _target_is_local_gateway(base: str, handler: BaseHTTPRequestHandler) -> bool:
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname or ""
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if host not in local_hosts:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return port == handler.server.server_port


def _ensemble_target(base: str, timeout: float = 2.0) -> bool:
    """Return True when *base* is another Claude Ensemble gateway.

    Classification is cached briefly because the health probe is only needed
    to distinguish a concrete service from another Ensemble gateway. A failed
    forward invalidates the cached classification so topology changes recover
    without waiting for the TTL.
    """
    now = time.monotonic()
    with _health_cache_lock:
        cached = _health_cache.get(base)
        if cached is not None and now - cached[0] < HEALTH_CACHE_TTL_SECONDS:
            return cached[1]

    probe = urllib.request.Request(base.rstrip("/") + "/api/v1/health", method="GET")
    try:
        with urllib.request.urlopen(probe, timeout=timeout) as response:
            if response.status != HTTPStatus.OK:
                result = False
            else:
                payload = json.loads(response.read().decode("utf-8"))
                result = payload.get("ok") is True and payload.get("service") == "claude-ensemble"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        result = False

    with _health_cache_lock:
        _health_cache[base] = (time.monotonic(), result)
    return result


def _invalidate_ensemble_target(base: str) -> None:
    with _health_cache_lock:
        _health_cache.pop(base, None)


def _gateway_identity(handler: BaseHTTPRequestHandler) -> str:
    host, port = handler.server.server_address[:2]
    return f"{host}:{port}"


def _forward_chain(handler: BaseHTTPRequestHandler) -> list[str]:
    raw = handler.headers.get(FORWARD_MARKER)
    if not raw:
        return []
    hops = [item.strip() for item in raw.split(",") if item.strip()]
    if len(hops) > MAX_FORWARD_HOPS:
        raise ServiceUnavailable("maximum Ensemble forwarding hops exceeded")
    if any("," in item for item in hops):
        raise ServiceUnavailable("invalid Ensemble forwarding marker")
    return hops


def _forward(
    base: str | None,
    method: str,
    path: str,
    body: bytes | None,
    handler: BaseHTTPRequestHandler,
) -> tuple[int, bytes]:
    if not base:
        raise ServiceUnavailable("service is not configured")
    if _target_is_local_gateway(base, handler):
        raise ServiceUnavailable("service URL points back to the local Ensemble gateway")

    chain = _forward_chain(handler)
    identity = _gateway_identity(handler)
    if identity in chain:
        raise ServiceUnavailable("Ensemble forwarding cycle detected")
    next_chain = chain + [identity]
    if len(next_chain) > MAX_FORWARD_HOPS:
        raise ServiceUnavailable("maximum Ensemble forwarding hops exceeded")

    # A configured URL may name either the concrete service itself (the normal
    # local case) or another Ensemble instance. The latter is discovered from
    # its health endpoint so callers keep exactly the same public REST path.
    remote_ensemble = _ensemble_target(base)
    target_path = f"{API_PREFIX}{path}" if remote_ensemble else path
    request = urllib.request.Request(base.rstrip("/") + target_path, data=body, method=method)
    for name, value in handler.headers.items():
        if name.lower() in FORWARD_SAFE_HEADERS:
            request.add_header(name, value)
    if remote_ensemble:
        request.add_header(FORWARD_MARKER, ", ".join(next_chain))
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        _invalidate_ensemble_target(base)
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _invalidate_ensemble_target(base)
        raise ServiceUnavailable(f"service unavailable: {exc}") from exc

class EnsembleApplication:
    """Single REST boundary over independently owned services."""
    def __init__(self, graph_url: str | None = None, memory_url: str | None = None):
        self.service_urls = {
            service: os.environ.get(f"ENSEMBLE_{service.upper()}_URL", default)
            for service, default in DEFAULT_SERVICE_URLS.items()
        }
        if graph_url is not None:
            self.service_urls["graph"] = graph_url
        if memory_url is not None:
            self.service_urls["memory"] = memory_url

        learning_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "learning"))
        if learning_dir not in sys.path:
            sys.path.insert(0, learning_dir)
        from learning.decision_support_api import DecisionSupportAPI
        from arbitration.api_client import MultiModelClient
        metrics_path = os.environ.get("ENSEMBLE_METRICS_FILE")
        self.metrics = MetricsStore(metrics_path)
        self.models = MultiModelClient(metrics_store=self.metrics)
        self.policy = Policy()
        self.capability_health = CapabilityHealthView(self.models.registry)
        provenance_path = os.environ.get("ENSEMBLE_DECISION_PROVENANCE_FILE")
        self.provenance = DecisionProvenanceStore(provenance_path)
        self.memory_writer = OperationalMemoryWriter()
        experiment_path = os.environ.get("ENSEMBLE_EXPERIMENT_FILE")
        self.experiments = ExperimentStore(experiment_path)
        self.decision = DecisionSupportAPI(
            graph_service_url=self.service_urls["graph"],
            memory_service_url=self.service_urls["memory"],
        )

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlsplit(handler.path)
        if not parsed.path.startswith(API_PREFIX + "/"):
            _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        route = parsed.path[len(API_PREFIX) + 1:]
        parts = route.split("/", 1)
        service = parts[0]
        remainder = "/" + parts[1] if len(parts) == 2 else "/"
        if service == "health":
            _send(handler, HTTPStatus.OK, {"ok": True, "service": "claude-ensemble"})
            return
        if service == "models":
            self._handle_models(handler, remainder)
            return
        if service == "collaboration":
            self._handle_collaboration(handler, remainder)
            return
        if service == "capabilities":
            self._handle_capabilities(handler, remainder)
            return
        if service == "policy":
            self._handle_policy(handler, remainder)
            return
        if service == "experiments":
            self._handle_experiments(handler, remainder)
            return
        if service == "metrics":
            self._handle_metrics(handler, remainder)
            return
        try:
            if service == "decision":
                self._handle_decision(handler, remainder)
                return
            if service not in self.service_urls:
                _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "service not found"})
                return
            body = None
            if handler.command in {"POST", "PUT", "PATCH"}:
                body = _request_body(handler)
            service_path = f"/{service}{remainder}" if remainder != "/" else f"/{service}"
            if parsed.query:
                service_path += f"?{parsed.query}"
            status, response = _forward(self.service_urls[service], handler.command, service_path, body, handler)
            handler.send_response(status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(response)))
            handler.end_headers()
            handler.wfile.write(response)
        except ValueError as exc:
            _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except ServiceUnavailable as exc:
            _send(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        except Exception:
            LOG.exception("REST request failed")
            _send(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "internal server error"})

    def _handle_capabilities(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if handler.command == "GET" and path == "/":
            _send(handler, HTTPStatus.OK, {"ok": True, **self.capability_health.capabilities()})
            return
        if handler.command == "GET" and path == "/health":
            _send(handler, HTTPStatus.OK, {"ok": True, **self.capability_health.health()})
            return
        _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "capability endpoint not found"})

    def _handle_policy(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if handler.command == "GET" and path == "/":
            _send(handler, HTTPStatus.OK, {"ok": True, "policy": self.policy.to_dict()})
            return
        if handler.command == "POST" and path == "/evaluate":
            try:
                body = _json_body(handler)
                request = body.get("request", body.get("input", {}))
                policy_data = body.get("policy")
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                if policy_data is not None and not isinstance(policy_data, dict):
                    raise ValueError("policy must be a JSON object")
                policy = self.policy if policy_data is None else Policy.from_dict(policy_data)
                result = evaluate(request, policy).to_dict()
                _send(handler, HTTPStatus.OK, result)
            except ValueError as exc:
                _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": str(exc)})
            return
        _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "policy endpoint not found"})

    def _handle_metrics(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if handler.command == "GET" and path == "/":
            _send(handler, HTTPStatus.OK, {"ok": True, "metrics": [record.to_dict() for record in self.metrics.read()]})
            return
        if handler.command == "GET" and path == "/aggregate":
            _send(handler, HTTPStatus.OK, {"ok": True, "aggregate": self.metrics.aggregate()})
            return
        if handler.command == "POST" and path == "/":
            try:
                body = _json_body(handler)
                record = MetricsRecord.from_dict(body.get("metric", body))
                self.metrics.record(record)
                _send(handler, HTTPStatus.CREATED, {"ok": True, "metric": record.to_dict()})
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": str(exc)})
            return
        _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "metrics endpoint not found"})

    def _handle_experiments(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if handler.command == "POST" and path == "/evaluate":
            try:
                body = _json_body(handler)
                experiment = Experiment.from_dict(body.get("experiment", body))
                result = evaluate_experiment(experiment)
                self.experiments.record(result)
                _send(handler, HTTPStatus.OK, {"ok": True, "result": result.to_dict()})
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": str(exc)})
            return
        if handler.command == "GET" and path.startswith("/"):
            experiment_id = path[len("/"):]
            if experiment_id:
                result = self.experiments.get(experiment_id)
                if result is None:
                    _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error_code": "not_found", "error": "experiment result not found"})
                else:
                    _send(handler, HTTPStatus.OK, {"ok": True, "result": result.to_dict()})
                return
        _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "experiment endpoint not found"})

    def _handle_collaboration(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if handler.command != "POST" or path != "/run":
            _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "collaboration endpoint not found"})
            return
        try:
            _require_collaboration_auth(handler)
            body = _json_body(handler)
            configured_solvers = _env_csv("ENSEMBLE_COLLABORATION_SOLVERS", DEFAULT_COLLAB_SOLVERS)
            configured_arbiter = os.environ.get("ENSEMBLE_COLLABORATION_ARBITER", DEFAULT_COLLAB_ARBITER)
            configured_reviewers = _env_csv("ENSEMBLE_COLLABORATION_REVIEWERS", DEFAULT_COLLAB_REVIEWERS)
            allow_external_data = os.environ.get("ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA", "").lower() == "true"
            max_rounds_cap = _env_positive_int("ENSEMBLE_COLLABORATION_MAX_ROUNDS", DEFAULT_COLLAB_MAX_ROUNDS)
            max_solver_cap = _env_positive_int("ENSEMBLE_COLLABORATION_MAX_SOLVER_CALLS", DEFAULT_COLLAB_MAX_SOLVER_CALLS)
            max_review_cap = _env_positive_int("ENSEMBLE_COLLABORATION_MAX_REVIEW_CALLS", DEFAULT_COLLAB_MAX_REVIEW_CALLS)
            max_arbiter_cap = _env_positive_int("ENSEMBLE_COLLABORATION_MAX_ARBITER_CALLS", DEFAULT_COLLAB_MAX_ARBITER_CALLS)

            task = body.get("task")
            solvers = body.get("solvers", list(configured_solvers))
            arbiter = body.get("arbiter", configured_arbiter)
            reviewers = body.get("reviewers", list(configured_reviewers))
            constraints = body.get("constraints", [])
            context = body.get("context", "")
            max_rounds = body.get("max_rounds", max_rounds_cap)
            max_solver_calls = body.get("max_solver_calls", max_solver_cap)
            max_review_calls = body.get("max_review_calls", max_review_cap)
            max_arbiter_calls = body.get("max_arbiter_calls", max_arbiter_cap)
            if not isinstance(task, str) or not task.strip():
                raise ValueError("task is required")
            if not isinstance(solvers, list) or not solvers or not all(isinstance(x, str) and x for x in solvers):
                raise ValueError("solvers must be a non-empty array of model names")
            if any(name not in configured_solvers for name in solvers):
                raise PermissionError("requested solver is not allowed by server collaboration policy")
            if not isinstance(arbiter, str) or not arbiter:
                raise ValueError("arbiter must be a model name")
            if arbiter != configured_arbiter:
                raise PermissionError("requested arbiter is not allowed by server collaboration policy")
            if not isinstance(reviewers, list) or not all(isinstance(x, str) for x in reviewers):
                raise ValueError("reviewers must be an array of reviewer names")
            if any(name not in configured_reviewers for name in reviewers):
                raise PermissionError("requested reviewer is not allowed by server collaboration policy")
            if reviewers and not allow_external_data:
                raise PermissionError(
                    "external reviewers are disabled until ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA=true"
                )
            if not isinstance(constraints, list) or not all(isinstance(x, str) for x in constraints):
                raise ValueError("constraints must be an array of strings")
            if not isinstance(context, str):
                raise ValueError("context must be a string")
            if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1 or max_rounds > max_rounds_cap:
                raise ValueError(f"max_rounds must be between 1 and {max_rounds_cap}")
            for name, value, cap in (
                ("max_solver_calls", max_solver_calls, max_solver_cap),
                ("max_review_calls", max_review_calls, max_review_cap),
                ("max_arbiter_calls", max_arbiter_calls, max_arbiter_cap),
            ):
                if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > cap:
                    raise ValueError(f"{name} must be between 1 and {cap}")

            solver_providers = {model: self.models.registry.resolve(model) for model in solvers}
            arbiter_provider = self.models.registry.resolve(arbiter)
            reviewer_adapters = {name: MCPReviewer(name) for name in reviewers}
            loop = CollaborationOrchestrator(
                task,
                solvers=solver_providers,
                arbiter=arbiter_provider,
                reviewers=reviewer_adapters,
                constraints=constraints,
                max_rounds=max_rounds,
                max_solver_calls=max_solver_calls,
                max_review_calls=max_review_calls,
                max_arbiter_calls=max_arbiter_calls,
            )
            result = loop.run(context=context)

            # Knowledge persistence is best effort. By default it stores only bounded,
            # structured collaboration outcomes. Full proposal/review/adjudication text is
            # deployment-owned and requires explicit opt-in because model output can contain
            # user-supplied workspace data or secrets.
            execution_id = handler.headers.get("X-Request-ID") or str(uuid.uuid4())
            selected = result.selected_candidate
            persist_full_text = os.environ.get(
                "ENSEMBLE_COLLABORATION_PERSIST_FULL_TEXT", ""
            ).lower() == "true"
            safe_reviews = [
                {
                    "candidate_id": review.candidate_id,
                    "reviewer": review.reviewer,
                    "status": review.status,
                    "verdict": review.verdict,
                    "provider": review.provider,
                    "model": review.model,
                }
                for review in result.state.reviews
            ]
            memory_record = {
                "status": result.status,
                "selected_candidate": (
                    {
                        "candidate_id": selected.candidate_id,
                        "model": selected.model,
                        "round": selected.round,
                    }
                    if selected else None
                ),
                "adjudication": {
                    "selected_candidate": result.adjudication.get("selected_candidate"),
                    "complete": result.adjudication.get("complete"),
                    "human_decision_required": result.adjudication.get("human_decision_required"),
                },
                "review_count": len(result.state.reviews),
                "candidate_count": len(result.state.candidates),
                "rounds": result.state.round,
                "reviews": safe_reviews,
            }
            if persist_full_text:
                if selected is not None:
                    memory_record["selected_candidate"]["proposal"] = selected.proposal
                memory_record["adjudication"] = dict(result.adjudication)
                memory_record["reviews"] = [
                    {
                        **review,
                        "summary": original.summary,
                        "findings": list(original.findings),
                    }
                    for review, original in zip(safe_reviews, result.state.reviews)
                ]

            knowledge_persisted = False
            try:
                knowledge_persisted = bool(self.memory_writer.write_event(
                    event_id=execution_id,
                    event_type="collaboration.result",
                    source="collaboration-orchestrator",
                    payload=memory_record,
                ))
            except Exception:
                LOG.exception("collaboration Knowledge persistence failed")

            provenance_persisted = False
            if selected is not None:
                try:
                    self.provenance.record(
                        DecisionRecord.create(
                            execution_id=execution_id,
                            decision_type="collaboration.adjudication",
                            selected=selected.candidate_id,
                            alternatives=[
                                candidate.candidate_id
                                for candidate in result.state.candidates
                                if candidate.candidate_id != selected.candidate_id
                            ],
                            strategy={
                                "name": "collaboration-orchestrator",
                                "version": "1",
                                "algorithm": "evidence-based-adjudication",
                            },
                            evidence=[
                                {
                                    "source": "collaboration",
                                    "metric": "review_count",
                                    "value": len(result.state.reviews),
                                },
                                {
                                    "source": "collaboration",
                                    "metric": "round_count",
                                    "value": result.state.round,
                                },
                            ],
                        )
                    )
                    provenance_persisted = True
                except Exception:
                    LOG.exception("collaboration decision provenance persistence failed")

            _send(handler, HTTPStatus.OK, {
                "ok": True,
                "execution_id": execution_id,
                "knowledge_persisted": knowledge_persisted,
                "provenance_persisted": provenance_persisted,
                "status": result.status,
                "selected_candidate": (
                    {
                        "candidate_id": result.selected_candidate.candidate_id,
                        "model": result.selected_candidate.model,
                        "round": result.selected_candidate.round,
                        "proposal": result.selected_candidate.proposal,
                    }
                    if result.selected_candidate else None
                ),
                "adjudication": dict(result.adjudication),
                "state": result.state.snapshot(),
            })
        except AuthenticationRequired as exc:
            _send(
                handler,
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error_code": "unauthorized", "error": str(exc)},
                {"WWW-Authenticate": "Bearer"},
            )
        except PermissionError as exc:
            _send(handler, HTTPStatus.FORBIDDEN, {"ok": False, "error_code": "forbidden", "error": str(exc)})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": str(exc)})
        except ServiceUnavailable as exc:
            _send(handler, HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error_code": "service_unavailable", "error": str(exc)})
        except Exception as exc:
            _send(handler, HTTPStatus.BAD_GATEWAY, {"ok": False, "error_code": "collaboration_failed", "error": str(exc)})

    def _handle_models(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if handler.command == "GET" and path == "/":
            _send(handler, HTTPStatus.OK, {
                "ok": True,
                "models": ["haiku", "sonnet", "opus", "gemini-2.5-flash"],
                "credentials": {
                    provider: self.models.registry.credential_status(provider)
                    for provider in ("anthropic", "google")
                },
            })
            return
        if handler.command == "GET" and path == "/credentials":
            _send(handler, HTTPStatus.OK, {
                "ok": True,
                "credentials": {
                    provider: self.models.registry.credential_status(provider)
                    for provider in ("anthropic", "google")
                },
            })
            return
        if handler.command == "POST" and path == "/invoke":
            try:
                body = _json_body(handler)
                model = body.get("model")
                prompt = body.get("prompt")
                if not isinstance(model, str) or not model:
                    raise ValueError("model is required")
                if not isinstance(prompt, str) or not prompt:
                    raise ValueError("prompt is required")
                response = self.models.call_model_response(
                    model=model,
                    prompt=prompt,
                    system=body.get("system", "") if isinstance(body.get("system", ""), str) else "",
                    temperature=body.get("temperature", 0.7),
                    max_tokens=body.get("max_tokens", 2000),
                    timeout=body.get("timeout", 300.0),
                    credential=body.get("credential"),
                )
                _send(handler, HTTPStatus.OK, {
                    "ok": True, "provider": response.provider, "model": response.model,
                    "text": response.text, "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens, "request_id": response.request_id,
                    "latency_ms": response.latency_ms, "cost_usd": response.cost_usd,
                })
            except ValueError as exc:
                _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": str(exc)})
            except Exception as exc:
                _send(handler, HTTPStatus.BAD_GATEWAY, {"ok": False, "error_code": "model_unavailable", "error": str(exc)})
            return
        _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "model endpoint not found"})

    def _handle_decision(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if path == "/provenance" and handler.command == "POST":
            try:
                body = _json_body(handler)
                record = DecisionRecord.create(
                    execution_id=body.get("execution_id"),
                    decision_type=body.get("decision_type"),
                    selected=body.get("selected"),
                    alternatives=body.get("alternatives"),
                    policy=body.get("policy"),
                    strategy=body.get("strategy"),
                    evidence=body.get("evidence"),
                )
                self.provenance.record(record)
                _send(handler, HTTPStatus.CREATED, {"ok": True, "decision": record.to_dict()})
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": str(exc)})
            return
        if path.startswith("/provenance/") and handler.command == "GET":
            decision_id = path[len("/provenance/"):]
            record = self.provenance.get(decision_id)
            if record is None:
                _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error_code": "not_found", "error": "decision provenance not found"})
                return
            _send(handler, HTTPStatus.OK, {"ok": True, "decision": record.to_dict()})
            return
        body = _json_body(handler) if handler.command == "POST" else {}
        routes = {
            "/recommend": self.decision.handle_recommend,
            "/advisor/models": self.decision.handle_recommend_models,
            "/advisor/phases": self.decision.handle_recommend_phases,
            "/advisor/cost": self.decision.handle_estimate_cost,
            "/analytics/best-models": self.decision.handle_best_models,
            "/analytics/tradeoff": self.decision.handle_cost_quality_tradeoff,
            "/analytics/failure": self.decision.handle_failure_analysis,
            "/analytics/scope-costs": lambda _: self.decision.handle_scope_costs(),
            "/query/semantic": self.decision.handle_semantic_search,
            "/query/graph": self.decision.handle_graph_traversal,
            "/query/patterns": self.decision.handle_model_patterns,
            "/query/problems": lambda _: self.decision.handle_problematic_tasks(),
            "/query/outliers": self.decision.handle_cost_outliers,
            "/query/trend": self.decision.handle_trend,
        }
        fn = routes.get(path)
        if fn is None:
            _send(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "decision endpoint not found"})
            return
        get_routes = {"/analytics/scope-costs", "/query/problems", "/query/outliers"}
        if handler.command == "GET" and path not in get_routes:
            _send(handler, HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "method not allowed"})
            return
        if handler.command == "POST" and path in get_routes:
            _send(handler, HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": "method not allowed"})
            return
        required = {
            "/recommend": ("task_type",), "/advisor/models": ("task_type",),
            "/advisor/phases": ("task_type", "scope"), "/advisor/cost": ("models", "task_type"),
            "/analytics/best-models": ("task_type",), "/analytics/tradeoff": ("task_type",),
            "/analytics/failure": ("task_type",), "/query/semantic": ("query",),
            "/query/graph": ("start",), "/query/patterns": ("model",), "/query/trend": ("task_type",),
        }
        missing = [key for key in required.get(path, ()) if not body.get(key)]
        if missing:
            _send(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error_code": "invalid_request", "error": f"missing required field(s): {', '.join(missing)}"})
            return
        result = fn(body)
        error_code = result.get("error_code")
        status = HTTPStatus.OK
        if not result.get("ok"):
            status = {
                "invalid_request": HTTPStatus.BAD_REQUEST,
                "dependency_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
                "no_data": HTTPStatus.UNPROCESSABLE_ENTITY,
                "internal_error": HTTPStatus.INTERNAL_SERVER_ERROR,
            }.get(error_code, HTTPStatus.INTERNAL_SERVER_ERROR)
        _send(handler, status, result)

def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, **kwargs: Any) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Ensemble REST server must bind to loopback")
    server = ThreadingHTTPServer((host, port), EnsembleRequestHandler)
    server.application = EnsembleApplication(**kwargs)  # type: ignore[attr-defined]
    return server

class EnsembleRequestHandler(BaseHTTPRequestHandler):
    server_version = "ClaudeEnsembleREST/1"
    def do_GET(self): self.server.application.handle(self)  # type: ignore[attr-defined]
    def do_POST(self): self.server.application.handle(self)  # type: ignore[attr-defined]
    def do_PUT(self): self.server.application.handle(self)  # type: ignore[attr-defined]
    def do_PATCH(self): self.server.application.handle(self)  # type: ignore[attr-defined]
    def do_DELETE(self): self.server.application.handle(self)  # type: ignore[attr-defined]
    def log_message(self, fmt: str, *args: Any) -> None: LOG.info(fmt, *args)

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = create_server(os.environ.get("ENSEMBLE_HTTP_HOST", DEFAULT_HOST),
                           int(os.environ.get("ENSEMBLE_HTTP_PORT", str(DEFAULT_PORT))))
    LOG.info("Claude Ensemble REST gateway listening on http://%s:%s", *server.server_address)
    try: server.serve_forever()
    finally: server.server_close()

if __name__ == "__main__":
    main()
