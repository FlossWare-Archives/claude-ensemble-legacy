# Optional live tests

The default Claude Ensemble runtime invokes the installed Claude Code CLI. Default CI
and normal acceptance tests do not require direct Anthropic API credentials.

## Legacy direct-Anthropic cache validator

`caching/manual_anthropic_api_validation.py` is retained only as an explicitly
optional manual validator for the retired direct-Anthropic `messages.create()`
path. It does **not** validate the active Claude Code runtime and is not part of
required CI.

Only run it intentionally when testing that legacy integration. It may make paid
API calls and requires both `ENSEMBLE_LIVE_CACHE_TESTS=1` and
`ANTHROPIC_API_KEY`:

```bash
ENSEMBLE_LIVE_CACHE_TESTS=1 python caching/manual_anthropic_api_validation.py
```

Do not add this script to the required test inventory. For active-runtime acceptance,
test the Claude Code provider and its returned usage/cache metadata with mocked CLI
responses, without direct API keys or live model calls.
