"""Model request contracts and provider implementations.

The runtime registry currently routes production model calls through Claude Code.
Legacy direct-API adapters remain importable during the migration so older
standalone tests and integrations do not break abruptly.
"""

from .anthropic import AnthropicProvider
from .claude_code import ClaudeCodeProvider
from .google import GoogleProvider
from .model_provider import ModelProvider, ModelRequest, ModelResponse
from .registry import ProviderRegistry

__all__ = [
    "AnthropicProvider",
    "ClaudeCodeProvider",
    "GoogleProvider",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ProviderRegistry",
]
