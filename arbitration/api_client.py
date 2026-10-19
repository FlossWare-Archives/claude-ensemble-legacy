#!/usr/bin/env python3
"""Compatibility facade over the canonical provider layer."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from operational_metrics import MetricsRecord, MetricsStore
from providers import ModelRequest, ModelResponse, ProviderRegistry

logger = logging.getLogger(__name__)


class MultiModelClient:
    """Unified direct API client backed by provider-neutral adapters."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        cost_logger: Any | None = None,
        task_name: str = "multi_model_call",
        metrics_store: MetricsStore | None = None,
    ):
        self.registry = registry or ProviderRegistry()
        self.cost_logger = cost_logger
        self.task_name = task_name
        self.metrics_store = metrics_store

    def call_model(
        self,
        model: str,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        """Call a selected model and return its text response."""
        return self.call_model_response(
            model=model,
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
        ).text

    def _record_metric(
        self,
        *,
        execution_id: str,
        started: float,
        provider: str | None,
        model: str | None,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error_type: str | None = None,
        worker: str | None = None,
        route: str | None = None,
        task_type: str | None = None,
    ) -> None:
        if self.metrics_store is None:
            return
        try:
            self.metrics_store.record(
                MetricsRecord(
                    execution_id=execution_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    service="model",
                    worker=worker,
                    provider=provider,
                    model=model,
                    route=route or model,
                    task_type=task_type or self.task_name,
                    status=status,
                    latency_ms=(time.monotonic() - started) * 1000,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=(
                        input_tokens + output_tokens
                        if input_tokens is not None and output_tokens is not None
                        else None
                    ),
                    estimated_cost=cost_usd,
                    outcome="success" if status == "success" else "failure",
                    error_type=error_type,
                )
            )
        except Exception:
            logger.exception("Unable to write operational metric")

    def call_model_response(
        self,
        *,
        model: str,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: float = 300.0,
        credential: str | None = None,
        execution_id: str | None = None,
        worker: str | None = None,
        route: str | None = None,
        task_type: str | None = None,
    ) -> ModelResponse:
        """Call a selected model and return the normalized provider response."""
        execution_id = execution_id or str(uuid4())
        started = time.monotonic()
        provider_name: str | None = None
        canonical_model: str | None = model
        try:
            _, canonical_model = self.registry.resolve_model(model)
            provider = self.registry.resolve(model)
            provider_name = getattr(provider, "name", None) or getattr(provider, "provider_name", None)
            response = provider.generate(
                ModelRequest(
                    prompt=prompt,
                    model=canonical_model,
                    system_prompt=system or None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    metadata={"credential": credential} if credential else {},
                )
            )
            provider_name = response.provider

            if self.cost_logger is not None:
                try:
                    self.cost_logger.log_call(
                        model=response.model,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        task_name=self.task_name,
                        source="provider",
                        provider=response.provider,
                        metadata={
                            "request_id": response.request_id,
                            "latency_ms": (time.monotonic() - started) * 1000,
                            "provider_metadata": response.raw_metadata,
                        },
                        cost_usd=response.cost_usd,
                    )
                except Exception:
                    # Cost telemetry is best-effort. A successful model response
                    # must not be discarded or recorded as a failed invocation
                    # because the separate cost sink is unavailable.
                    logger.exception("Unable to record model cost; preserving successful response")
            self._record_metric(
                execution_id=execution_id,
                started=started,
                provider=response.provider,
                model=response.model,
                status="success",
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
                worker=worker,
                route=route,
                task_type=task_type,
            )
            return response
        except Exception as exc:
            self._record_metric(
                execution_id=execution_id,
                started=started,
                provider=provider_name,
                model=canonical_model,
                status="failure",
                error_type=type(exc).__name__,
                worker=worker,
                route=route,
                task_type=task_type,
            )
            raise

    def call_models_parallel(
        self,
        models: list[str],
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> dict[str, str]:
        """Call multiple models in parallel (returns model -> response)."""
        import concurrent.futures

        results: dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as executor:
            futures = {
                executor.submit(
                    self.call_model,
                    model,
                    prompt,
                    system,
                    temperature,
                    max_tokens,
                ): model
                for model in models
            }
            for future in concurrent.futures.as_completed(futures):
                model = futures[future]
                try:
                    results[model] = future.result()
                except Exception as exc:
                    logger.error("Error calling %s: %s", model, exc)
                    results[model] = f"ERROR: {exc}"
        return results
