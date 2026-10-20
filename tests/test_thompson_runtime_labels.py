"""Ensure Thompson routing only emits model labels supported by the active runtime."""

from __future__ import annotations

from providers.registry import ProviderRegistry
from shared.thompson_router import RHDisseminatorRouter


class FirstCandidateRouter:
    def select_model(self, candidates: list[str]) -> str:
        return candidates[0]


def test_every_thompson_task_candidate_is_registry_accepted() -> None:
    registry = ProviderRegistry()
    for task_type, config in RHDisseminatorRouter.TASK_ROUTING.items():
        assert config["models"], task_type
        for model in config["models"]:
            registry.resolve_model(model)


def test_every_thompson_task_selection_is_registry_accepted() -> None:
    registry = ProviderRegistry()
    router = RHDisseminatorRouter(state_tracker=None, router=FirstCandidateRouter())
    for task_type in RHDisseminatorRouter.TASK_ROUTING:
        selected = router.select_model_for_task(task_type)
        registry.resolve_model(selected)


def test_retired_provider_labels_are_absent_from_routing_and_demo_priors() -> None:
    import inspect
    import shared.thompson_router as module

    source = inspect.getsource(module)
    for retired in ("cursor", "gemini", "gpt-4", "ollama-local"):
        assert retired not in source.lower()
