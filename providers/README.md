# Model Providers

Claude Ensemble uses a provider-neutral `ModelProvider` contract and explicit
model-to-provider routing.

## Supported runtimes

- **Claude models** (`haiku`, `sonnet`, `opus`, and explicit `claude-*`
  model IDs) run through the installed Claude Code CLI. The CLI auth status and
  response metadata must match `ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER`, which
  defaults to `vertex`. A mismatch fails closed before the prompt is sent.
- **Google Gemini** (`gemini` and `gemini-*`) uses the Google Generative
  Language API and `GOOGLE_API_KEY`. It is disabled by default because it sends
  request content to Google. Enable it only for workloads approved to send data
  to Google by setting `ENSEMBLE_ALLOW_GOOGLE_MODEL_API=true` in the service
  environment. Do not enable it for restricted/internal-code workloads.

There is no implicit fallback between these providers. A Gemini request cannot
run on Sonnet, and an unknown model label fails clearly. Legacy labels such as
`gpt-4`, `cursor`, and `ollama-local` are rejected until their real adapters
are implemented and configured.

## Credentials

Google credentials can be supplied with `GOOGLE_API_KEY`, numbered environment
variables, or `ENSEMBLE_CREDENTIALS_FILE`. Credential status endpoints return
account names and states only, never secret values. Protect credential files
with mode 0600.

## Contracts and deadlines

All adapters implement `ModelProvider.generate(ModelRequest)` and return a
normalized `ModelResponse`. Provider HTTP calls use the standard-library
transport with an overall deadline and process cleanup. The compatibility
facade in `arbitration/api_client.py` retains `MultiModelClient`.

## Safety boundary

Provider support does not mean every workload may use every provider. Keep the
Claude Code expected-upstream setting aligned with the deployment policy, and
keep Google API routing disabled unless the data owner has approved that
destination. External reviewer services are a separate configuration surface.


### Per-request external-data authorization

The `ENSEMBLE_ALLOW_GOOGLE_MODEL_API=true` service flag alone does not authorize sending prompts to Google. A trusted internal caller must also pass `allow_external_data=True` for that specific `MultiModelClient.call_model_response` call. The public REST `/api/v1/models/invoke` endpoint does not accept client-supplied authorization flags. Keep Google disabled for restricted/internal-code workloads.
