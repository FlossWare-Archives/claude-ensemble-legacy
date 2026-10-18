# Audit finding: hardcoded developer paths

**Tracking:** [GitHub issue #525](https://github.com/FlossWare/claude-ensemble/issues/525)  
**Status:** Resolved  
**Priority:** Closed after portability fixes, regression coverage, and repository-wide executable-path review.

## Resolution summary

The original audit identified developer-specific paths in runtime code, tests, documentation, and a Claude Code skill. The findings have been addressed across these merged PRs:

- [#529](https://github.com/FlossWare/claude-ensemble/pull/529): compression metrics/verdict output and test imports now resolve relative to the checkout.
- [#530](https://github.com/FlossWare/claude-ensemble/pull/530): CE Jira review skill resolves a validated deployed checkout using `CE_REPO_PATH` and known deployment defaults.
- [#531](https://github.com/FlossWare/claude-ensemble/pull/531): cost-tracking and caching documentation uses portable paths.
- [#532](https://github.com/FlossWare/claude-ensemble/pull/532): regression tests verify module-relative compression paths and test imports.
- [#533](https://github.com/FlossWare/claude-ensemble/pull/533): memory and pre-tool validator paths are configurable; session-end hooks prefer the managed CE deployment and retain explicit compatibility fallbacks.
- [#534](https://github.com/FlossWare/claude-ensemble/pull/534): GA memory and global-skills roots accept environment overrides.
- [#535](https://github.com/FlossWare/claude-ensemble/pull/535): GA and learning documentation uses portable paths.
- [#536](https://github.com/FlossWare/claude-ensemble/pull/536): legacy GA scheduler exports the selected global-skills root to child tools.

## Classification of remaining path-like references

- `install.sh`: default checkout location is a convenience; an explicit path argument overrides it.
- `scripts/dogfood-ce.sh`: `CE_REPO_DIR` overrides the default checkout location.
- `tools/discover-models.py`: `RH_TOOLS_ROOT` configures the global-skills checkout.
- `caching/memory_cache_integration.py` and GA evaluators: `CE_MEMORY_DIR` overrides the historical RH memory directory; the old layout remains only as a compatibility fallback.
- `hooks/pre-tool-validation.cjs`: `CE_PRE_TOOL_VALIDATOR_PATH` overrides the project-relative/legacy default.
- Session-end hooks: `ENSEMBLE_ROOT` can be set explicitly; the managed `~/.FlossWare/claude-ensemble/current` is preferred when present.
- `tools/install-ce-versioned.sh` references the `claude-global-skills` symlink by name to ensure the host-specific link is excluded from standalone releases. This is an intentional safety check, not a developer-home path.
- `/home/test/...` strings in memory-sync tests are synthetic test fixtures, not production paths.
- Documentation that describes old paths as historical context is intentionally retained where it explains compatibility or a previous issue.

## Verification

- CI passed for all PRs listed above, including the dedicated portable-path regression tests.
- Repository-wide executable-source scan completed; remaining path defaults are configurable or intentionally retained for backward compatibility.
- No live services, normal `~/.claude`, or host crontab were changed.
