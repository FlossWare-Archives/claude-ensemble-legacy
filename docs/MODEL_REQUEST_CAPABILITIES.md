# Model request capabilities

CE constructs provider-neutral `ModelRequest` values, but a provider may not be able to honor every optional control. Callers that know the selected provider must construct requests with `providers.build_model_request(provider, ...)`. The builder validates the request against the provider's immutable `ModelCapabilities` declaration before execution. Providers also validate at their own execution boundary as defense in depth.

## Contract

- Every provider exposes a `capabilities` property returning `ModelCapabilities`.
- Capabilities declare support for system prompts, prior conversation messages, temperature overrides, max-token overrides, and timeout overrides, plus the provider's normalized defaults.
- `ModelRequest.explicit_fields` records whether the caller supplied system prompt, messages, temperature, max tokens, or timeout, even when the supplied value equals the default.
- An omitted option is normalized to the canonical default. An explicitly supplied default is still recorded as explicit, but it is compatible with a provider that uses that same default because it does not ask the provider to change behavior.
- A non-default value for an unsupported control is rejected before provider execution with `ModelCapabilityError`. Unsupported controls are never silently discarded.
- A provider must not declare support for a control unless it can actually honor that control. Test doubles should declare their capabilities explicitly; permissive fake-provider tests do not replace strict-provider composition tests.

## Current Claude Code CLI contract

The Claude Code adapter supports system prompts and request timeouts. It accepts a self-contained prompt, but does not support prior-turn `messages`, temperature overrides, or max-token overrides. Those controls may be omitted or supplied at the adapter's canonical defaults; non-default values fail closed. Collaboration, arbitration, and execution request construction use the same provider-aware builder.

The contract tests are local-only and credential-free. They cover omitted and explicit-default values, supported options, unsupported options, and strict-provider validation.
