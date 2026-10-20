# Runtime support matrix

This matrix is the source of truth for CE runtime minimums and CI coverage.

| Component / purpose | Supported runtime | CI coverage |
|---|---|---|
| Core Python services, REST gateway, provider/reviewer MCP tests, hooks/config tests | Python 3.11 or newer | `service-tests.yml` and `non-ga-integration-audit.yml` run Python 3.11 |
| Genetic-algorithm tuning contracts | Python 3.12 or newer | `ga-tuning-tests.yml` runs Python 3.12 |
| JavaScript hooks, package tooling, Node-based tests | Node.js 22 or newer | `package.json` engine and JavaScript CI jobs use Node 22 |
| Installation | Python 3.11+, Node.js 22+, Git, systemd user manager, and the Claude Code CLI | `install.sh` checks runtime versions before changing user configuration |

## Runtime policy

- Python 3.11 is the minimum for the core service/test surface. GA tuning is tested on Python 3.12 and does not lower the core minimum.
- Node.js 22 is the minimum. The package engine declaration and CI use the same major-version baseline.
- CI may test a component on a newer supported runtime, but it must not silently use a runtime older than this matrix.
- The top-level `install.sh` is the source-checkout/development installer. For the isolated immutable deployment under `~/.FlossWare/claude-ensemble`, use `tools/install-ce-versioned.sh`; host staging and activation remain gated by [issue #505](https://github.com/FlossWare/claude-ensemble/issues/505).
- Raising a minimum requires updating this document, package/installer checks, and the relevant CI jobs in the same change.

## Service lifecycle without systemd

The supported manual fallback is explicit foreground execution, one terminal per service, with state and sockets rooted outside the ordinary Claude profile. See [Manual foreground service lifecycle](SERVICE_LIFECYCLE.md) for the environment variables, service startup order, shutdown sequence, and diagnostics. No helper silently daemonizes services or mutates shell startup files. This fallback does not provide systemd restart/boot supervision.

## Verification

The top-level installer checks Python and Node versions before writing to Claude configuration, registering services, or installing hooks. CI validates the same runtime families using the workflow versions listed above.
