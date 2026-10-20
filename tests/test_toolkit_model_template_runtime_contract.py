"""The legacy toolkit model template must match the active runtime contract."""

from pathlib import Path


def test_default_toolkit_template_only_advertises_claude_code_models() -> None:
    template = (Path(__file__).resolve().parents[1] / "toolkit-models.yaml.default").read_text(
        encoding="utf-8"
    ).lower()
    for retired in ("gemini", "cursor", "gpt-4", "ollama", "provider: anthropic", "provider: google"):
        assert retired not in template
    for alias in ("haiku", "sonnet", "opus"):
        assert alias in template
    assert "not read by the active claude ensemble runtime" in template
