"""Provider-neutral model request capability contract.

Capabilities describe which request controls a provider can honor. A request's
explicit_fields records caller intent even when the caller explicitly chooses a
default value; an explicit default is compatible with a provider that uses that
same default, while non-default unsupported controls fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model_provider import ModelProvider, ModelRequest


class ModelCapabilityError(ValueError):
    """A request asks a provider to honor a control it cannot represent."""


@dataclass(frozen=True)
class ModelCapabilities:
    """Declared controls for one provider implementation."""

    provider: str
    supports_system_prompt: bool = False
    supports_messages: bool = False
    supports_temperature_override: bool = False
    supports_max_tokens_override: bool = False
    supports_timeout_override: bool = False
    default_temperature: float = 0.7
    default_max_tokens: int = 2000
    default_timeout: float = 300.0

    def validate(self, request: ModelRequest) -> None:
        """Reject unsupported non-default controls before provider execution."""
        if request.system_prompt is not None and not self.supports_system_prompt:
            raise ModelCapabilityError(
                f"{self.provider} does not support system_prompt"
            )
        if request.messages and not self.supports_messages:
            raise ModelCapabilityError(
                f"{self.provider} does not support prior conversation messages; "
                "send a self-contained prompt"
            )
        if (
            request.temperature != self.default_temperature
            and not self.supports_temperature_override
        ):
            raise ModelCapabilityError(
                f"{self.provider} does not support temperature overrides"
            )
        if (
            request.max_tokens != self.default_max_tokens
            and not self.supports_max_tokens_override
        ):
            raise ModelCapabilityError(
                f"{self.provider} does not support max_tokens overrides"
            )
        if (
            request.timeout != self.default_timeout
            and not self.supports_timeout_override
        ):
            raise ModelCapabilityError(
                f"{self.provider} does not support timeout overrides"
            )


def build_model_request(provider: ModelProvider, **kwargs: Any) -> ModelRequest:
    """Construct and validate a request against the selected provider contract."""
    request = ModelRequest(**kwargs)
    capabilities = getattr(provider, "capabilities", None)
    if not isinstance(capabilities, ModelCapabilities):
        raise ModelCapabilityError(
            f"{getattr(provider, 'name', type(provider).__name__)} has no "
            "declared ModelCapabilities contract"
        )
    capabilities.validate(request)
    return request
