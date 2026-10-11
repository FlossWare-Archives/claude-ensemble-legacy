# Issue: [Jules] Replace deprecated `datetime.utcnow()` with timezone-aware `datetime.now(timezone.utc)`

**Status:** OPEN
**Priority:** Low

## Description
Python 3.12 deprecates `datetime.datetime.utcnow()` in favor of timezone-aware `datetime.datetime.now(datetime.timezone.utc)`. Multiple modules across `hooks/`, `ga_tuning/`, `learning-service/`, `alert_service/`, `shared/`, and `memory-service/` use `datetime.utcnow()`, raising `DeprecationWarning` during test runs and runtime execution.

## Proposed Solution
- Replace `datetime.utcnow()` calls with `datetime.now(timezone.utc)` across the codebase.
