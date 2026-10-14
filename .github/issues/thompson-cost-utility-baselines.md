# Issue: [Jules] Add fixed pricing reference tables to Thompson Router cost normalization

**Status:** OPEN
**Priority:** Medium

## Description
The Thompson Router (`shared/thompson_router.py`) normalizes model costs dynamically against the 90th percentile of observed model costs (`_get_cost_normalization_factor`). When sample size is small or cost distributions shift sharply, utility calculation weights for cost can fluctuate unexpectedly.

## Proposed Solution
- Combine static baseline pricing references per provider tier with empirical 90th percentile normalization.
- Ensure cost penalties remain predictable during early sampling phases.
