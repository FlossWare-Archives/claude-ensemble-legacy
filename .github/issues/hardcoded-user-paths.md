# Audit finding: hardcoded developer paths

**Tracking:** [GitHub issue #525](https://github.com/FlossWare/claude-ensemble/issues/525)  
**Status:** Open  
**Priority:** Triage runtime and test paths first; review docs separately.

## Description
Multiple source modules, test scripts, documentation, and skill definitions contain hardcoded developer paths (for example, `/home/sfloess/...`). These can fail across developer environments and checkout locations.

## Known occurrences
- `claude-code-skills/ce-jira-review/SKILL.md`
- `compression/test_critical_fixes.py`
- `compression/phase2_verification.py`
- `compression/summarizer.py`
- `cost_tracking/README.md`, `cost_tracking/INDEX.md`, `cost_tracking/AGGREGATOR_IMPLEMENTATION.md`
- `caching/README.md`

## Triage guidance
Classify each occurrence as runtime code, test, documentation example, or intentional deployment configuration. Resolve repository-local paths from the module/repository location or explicit configuration. Do not mechanically rewrite paths that intentionally identify a deployment.
