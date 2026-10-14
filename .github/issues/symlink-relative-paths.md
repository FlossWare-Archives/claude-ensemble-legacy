# Issue: [Jules] Use relative paths for repository symlinks to ensure portability

**Status:** OPEN
**Priority:** Low

## Description
Certain repository symlinks (`tools/meta-review`, `tools/meta-meta-review`, `claude-global-skills`) point to absolute developer paths (e.g., `/home/sfloess/Development/...`). On different workstations or clone locations, these symlinks break.

## Proposed Solution
- Replace absolute symlink targets with repository-relative paths (e.g. `tools/meta-review -> review.sh`).
