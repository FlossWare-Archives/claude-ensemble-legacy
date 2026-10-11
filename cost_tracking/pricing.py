"""
Pricing constants for Claude models and integration partners.
Updated: 2026-09-25
Source: official Claude pricing and integration partners
"""

CLAUDE_PRICING = {
    "haiku": {"input": 0.80, "output": 2.40, "name": "Claude Haiku 4.5"},
    "sonnet": {"input": 3.00, "output": 15.00, "name": "Claude Sonnet 4.5"},
    "opus": {"input": 15.00, "output": 45.00, "name": "Claude Opus 5"},
    "opus-4.8": {"input": 12.00, "output": 36.00, "name": "Claude Opus 4.8"},
}

GEMINI_PRICING = {
    "input": 0.075,
    "output": 0.30,
    "name": "Google Gemini",
}

GPT_PRICING = {
    "gpt-4o": {"input": 5.00, "output": 15.00, "name": "GPT-4o"},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00, "name": "GPT-4 Turbo"},
}


def _resolve_pricing(model: str) -> dict:
    model_lower = model.lower()
    if model_lower in CLAUDE_PRICING:
        return CLAUDE_PRICING[model_lower]
    if model_lower == "gemini" or model_lower.startswith("gemini-") or model_lower.startswith("models/gemini-"):
        return GEMINI_PRICING
    if model_lower.startswith("claude-"):
        if "haiku" in model_lower:
            return CLAUDE_PRICING["haiku"]
        if "sonnet" in model_lower:
            return CLAUDE_PRICING["sonnet"]
        if "opus-4.8" in model_lower:
            return CLAUDE_PRICING["opus-4.8"]
        if "opus" in model_lower:
            return CLAUDE_PRICING["opus"]
    raise ValueError(f"Unknown model: {model}. Known models: {list(CLAUDE_PRICING.keys())}")


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate an estimated cost for a supported model family."""
    pricing = _resolve_pricing(model)
    return (
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
    )


def get_model_display_name(model: str) -> str:
    """Get display name for a model."""
    model_lower = model.lower()
    if model_lower in CLAUDE_PRICING:
        return CLAUDE_PRICING[model_lower]["name"]
    if model_lower.startswith(("claude-", "gemini-", "models/gemini-")):
        return model
    return model
