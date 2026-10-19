# Model Runtime Configuration

Claude Ensemble currently uses one model runtime: the installed Claude Code
CLI. The previous multi-provider implementation is preserved on
`archive/pre-provider-simplification-2026-10-14` for reference.

## Runtime model labels

Use Claude Code model aliases in workflow configuration:

- `haiku`
- `sonnet`
- `opus`

Unsupported labels such as `gemini`, `cursor`, `gpt-4`, and
`ollama-local` are rejected with an `Unsupported model label` error. CE does
not silently map them to `sonnet` or invoke those providers. Replace retired
labels with `haiku`, `sonnet`, or `opus` in configuration.

The REST model inventory exposes only the three supported aliases. Credential
status is owned by Claude Code; CE does not rotate API keys or maintain a
multi-provider credential pool in its active runtime.

## Authentication and policy

Claude Code owns authentication. CE does not copy or rewrite `~/.claude`
credentials as part of model routing. Before a request is sent, CE checks
`claude auth status` against `ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER`, then
validates the provider metadata returned by the CLI.

The default expected provider is `vertex`. For Red Hat internal code, keep
the expected provider set to `vertex`; do not route internal code through
Anthropic or another upstream. If Claude Code reports a different upstream, CE
fails closed rather than sending the prompt.

## Systems retained

This simplification changes model invocation only. It does not remove:

- REST gateway and service boundaries
- Memory, Learning, Thompson sampling, or GA tuning
- Graph, Alert, and Messenger services
- Worker/arbiter orchestration, collaboration, and reviewer MCP
- Metrics, cost logging, feedback capture, and operational diagnostics

There is no active multi-provider compatibility layer. Callers must use the
current Claude Code runtime contract; retired `MultiModelClient`,
`ModelRequest`, and `ModelResponse` compatibility surfaces must not be
reintroduced merely to preserve old callers.
