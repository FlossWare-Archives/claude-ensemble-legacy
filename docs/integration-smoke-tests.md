# Integration smoke tests

The default CI suite is deterministic and must not depend on paid APIs, user credentials,
or third-party uptime. The following commands are explicit live checks and can incur provider
charges or send selected repository content to external providers.

## Claude Code runtime

CE's supported model execution path is the authenticated Claude Code CLI. Direct Anthropic
Messages and Google Gemini provider adapters are not part of the active runtime contract;
Gemini routing remains parked until deployment acceptance and explicit routing work are complete.

On a trusted machine where Claude Code is already installed and authenticated:

```bash
ENSEMBLE_CLAUDE_INTEGRATION=1 python -m pytest -q providers/test_claude_code_integration.py
```

Optional model override: `ENSEMBLE_CLAUDE_MODEL` (supported labels are `haiku`, `sonnet`,
and `opus`). Without the opt-in flag the test is skipped and does not invoke Claude Code.
The test requires the CLI to return exactly `READY` through CE's orchestration path.

## Grok, Perplexity, and Jules reviewer MCP

The reviewer smoke script sends the complete diff of the selected PR to each selected,
configured external reviewer. Use only a public or otherwise explicitly approved PR.
Replace the example PR number below with the approved public PR you intend to review.

```bash
ENSEMBLE_LIVE_REVIEWER_TESTS=1 \
REVIEWER_SMOKE_REPOSITORY=FlossWare/claude-ensemble \
REVIEWER_SMOKE_PR_NUMBER=345 \
python reviewer-mcp/smoke_live_reviewers.py
```

Optional `REVIEWER_SMOKE_REVIEWERS` accepts a comma-separated subset of `grok`,
`perplexity`, and `jules`. Missing credentials are reported as `not_run`; failed
provider requests return a nonzero exit code. Jules review can take several minutes and
requires the configured GitHub source and an unchanged PR head branch.

Never paste API keys into command lines or commit them. Keep credentials in the environment
or your existing secret-management mechanism.

## Anthropic prompt-cache integration

The caching validation in `caching/test_anthropic_api.py` makes real Anthropic API calls and can incur charges. Run it only when explicitly approved:

```bash
ENSEMBLE_LIVE_CACHE_TESTS=1 python caching/test_anthropic_api.py
```

The broad audit runner reports this check as `not run` unless both the opt-in flag and `ANTHROPIC_API_KEY` are present.
