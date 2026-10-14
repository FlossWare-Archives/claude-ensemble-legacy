# Model Providers

Claude Ensemble exposes a small provider-neutral ModelProvider contract.

## Direct API providers

AnthropicProvider and GoogleProvider invoke their public model APIs directly
and normalize responses into ModelResponse. They use the standard library HTTP
client, so provider support does not add an SDK dependency.

Credentials are read from:

- ANTHROPIC_API_KEY
- GOOGLE_API_KEY

Requests are bounded by ModelRequest.timeout as an overall wall-clock deadline.
The blocking standard-library transport runs in a short-lived child process; if
the deadline expires, the parent terminates and reaps that process so network
I/O does not continue in an abandoned worker thread. Process startup and OS-level
termination cleanup can add a small scheduling tolerance. Provider-specific HTTP
failures are surfaced as actionable exceptions and are not converted into fake
successful responses.

ModelRequest keeps \`messages\` as prior conversation turns and always appends
\`prompt\` as the current/final user turn. This makes a request with both fields
self-contained and prevents the current prompt from being silently discarded.
Temperature is provider-neutral and must be in the inclusive range 0 through 2.

Provider responses populate ModelResponse.latency_ms with the elapsed provider
request time. The compatibility client may also record its own wall-clock
latency for cost tracking.

## Claude Code

ClaudeCodeProvider remains available for the Claude Code CLI workflow. It
deliberately treats Claude Code as the authentication/runtime boundary rather
than reading Claude Code credentials itself.

## Selecting providers

ProviderRegistry maps Claude model names to Anthropic and Gemini model names
to Google. The short Anthropic aliases \`haiku\`, \`sonnet\`, and \`opus\` are
canonicalized to API model IDs before invocation. The defaults can be overridden
with \`CLAUDE_HAIKU_MODEL\`, \`CLAUDE_SONNET_MODEL\`, and \`CLAUDE_OPUS_MODEL\`.

Default provider adapters are constructed lazily when a model requiring them
is resolved. Explicitly supplied providers are never replaced or eagerly
constructed.

Additional providers can be explicitly registered without changing the
request/response contract.

The compatibility facade in arbitration/api_client.py keeps the existing
MultiModelClient.call_model() API while exposing call_model_response() for
normalized usage and metadata.

The old Cursor direct-API path is intentionally removed. Cursor is not a
provider in the current MultiModelClient/provider registry; adding another
provider requires an explicit ModelProvider implementation and registration.

## Smoke test

With credentials configured, a real call can be exercised without modifying
the test suite:

    python -c 'from arbitration.api_client import MultiModelClient; print(MultiModelClient().call_model("gemini-2.5-flash", "Reply with one word."))'

Use the Anthropic model name appropriate to the account for an Anthropic smoke
test. Tests use mocked HTTP responses and never require live credentials.


## Multi-account credentials

The provider layer uses a shared credential pool. The default source remains
environment variables:

- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`

Additional accounts can be supplied without a YAML file using numbered/account
variables such as `ANTHROPIC_API_KEY_PERSONAL_1` and
`ANTHROPIC_API_KEY_PERSONAL_2`. Account names are normalized to
`personal-1` and `personal-2`.

For a more robust personal multi-account setup, set
`ENSEMBLE_CREDENTIALS_FILE` to a YAML file with provider/account entries.
The file should be protected with mode 0600. Example:

```yaml
anthropic:
  personal-1:
    api_key: "..."
  personal-2:
    api_key: "..."
```

The REST boundary exposes this capability through `/api/v1/models`,
`/api/v1/models/credentials`, and `POST /api/v1/models/invoke`. A request
may optionally specify `credential`; otherwise the pool rotates available
credentials. Failed automatic selections enter a short cooldown. Credential
values are never returned by the status endpoints.

Environment and YAML credentials can coexist. Environment credentials remain
the simple default, while the YAML store supports independent personal
accounts without putting secrets in source-controlled configuration.
