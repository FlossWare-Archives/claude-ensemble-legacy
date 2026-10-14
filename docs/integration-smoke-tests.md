# Integration smoke tests

The default CI suite is deterministic and must not depend on paid APIs, user credentials,
or third-party uptime. The following commands are explicit live checks and can incur provider
charges or send selected repository content to external providers.

## Anthropic and Google model providers

On a trusted machine with credentials already configured in the environment:

```bash
ENSEMBLE_LIVE_PROVIDER_TESTS=1 python -m pytest -q providers/test_live_api_providers.py
```

Optional model overrides:

- `ENSEMBLE_LIVE_ANTHROPIC_MODEL`
- `ENSEMBLE_LIVE_GOOGLE_MODEL`

Without the opt-in flag, tests are skipped and no network request is made. With opt-in but
without a provider key, that provider is reported as skipped due to missing credentials.
An authenticated request must return the exact expected response and valid token usage to pass.

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
