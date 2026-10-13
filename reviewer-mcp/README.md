# Autonomous Reviewer MCP

Dependency-free MCP broker for invoking Grok, Perplexity, and Jules as independent code reviewers.

## Tools

- review_grok
- review_perplexity
- review_jules
- review_all
- review_candidate

PR review tools accept a GitHub repository and pull-request number. The broker retrieves the current PR metadata and diff. review_all invokes all three concurrently and preserves each result separately. `review_candidate` reviews an engineering proposal before a PR exists. It invokes Grok and Perplexity; when `repository` is supplied it also invokes Jules against the selected branch.

## Trust boundary

Review targets are restricted by `REVIEW_ALLOWED_REPOSITORIES`. The default is `FlossWare/*`. Set it to a comma-separated list of exact repositories or fnmatch-style patterns when a narrower scope is required.

Authorization is checked before any GitHub or reviewer-provider call. Repository metadata and PR diffs are untrusted review data. Reviewer prompts explicitly instruct providers to treat all PR content as evidence, never as instructions, commands, authorization, or requests to change the task, target repository, provider, credentials, or output contract.

Jules is invoked in review-only mode. The prompt is not an authorization mechanism; repository allowlisting is the primary target boundary.

## Environment

- GITHUB_TOKEN: optional for public repositories, required for private repositories
- REVIEW_ALLOWED_REPOSITORIES: optional comma-separated exact repositories or fnmatch-style patterns; defaults to `FlossWare/*`
- XAI_API_KEY and optional GROK_MODEL
- PERPLEXITY_API_KEY, optional PERPLEXITY_MODEL and PERPLEXITY_API_URL
- JULES_API_KEY, optional JULES_TIMEOUT_SECONDS and JULES_POLL_SECONDS
- MCP_HOST: defaults to `127.0.0.1`
- MCP_PORT: defaults to `8790`
- MCP_AUTH_TOKEN: required when MCP_HOST is not loopback; optional for loopback deployments

Keys remain environment-only and are never returned in review results.

## Proposal review\n\nProposal text and supplied context are untrusted data. The same review-only boundary applies: reviewers cannot authorize repository changes through proposal content. Jules proposal review requires an allowlisted repository and verifies that the selected branch does not move while the review session runs. The complete supplied candidate/context is sent to each enabled external reviewer, so callers must respect provider/data-sharing policy.\n\n## Review contract

Every result contains reviewer, status, verdict, summary, findings, provider, model, latency_ms, and error.

Verdict is one of `approve`, `request_changes`, or `comment`.

Findings are objects with a required string `message` and optional `path`, integer `line`, `severity`, and `evidence` fields. Unknown finding fields are discarded.

A failed reviewer becomes a failed result rather than aborting review_all. This lets the CE arbiter reason over partial availability.

## Outbound data

The complete PR diff is sent to each enabled external reviewer. Do not use this broker for repositories whose source code or review content is not approved for those providers. Diff size is limited to 1.5 MB before provider invocation. Secret redaction and provider-specific truncation are future hardening work.

## Jules

Jules is invoked through its REST API against the connected GitHub source. The Jules API currently exposes only a branch name for GitHub repository context, not an immutable commit SHA. The broker therefore verifies the PR head branch resolves to the fetched head_sha immediately before creating the session and again after completion. If the branch moves to a different commit at either check, the Jules result is returned as failed rather than being presented as a review of the fetched revision.

The session prompt also names the fetched head_sha, but that prompt is not treated as a security boundary.

The session is explicitly review-only and is polled until completion or timeout.

No browser automation is used.

## Boundary

This broker owns reviewer transport and normalization. It does not duplicate CE arbitration, learning, metrics, or GitHub mutation behavior.

The reviewer MCP test suite is intentionally dependency-free and runs independently of the broader CE test suite.

### Jules deadlines and retry behavior

Jules review requests use one monotonic end-to-end deadline from branch validation through source lookup, session creation, polling, and final branch validation. Each HTTP operation receives only the remaining budget and runs in a cancellable child process; the broker terminates and reaps that process when the deadline expires. The default `JULES_TIMEOUT_SECONDS` is 900 seconds.

The MCP client timeout defaults to at least 60 seconds beyond the configured Jules deadline (960 seconds with defaults). Configure `REVIEWER_MCP_TIMEOUT_SECONDS` if needed, but it must exceed `JULES_TIMEOUT_SECONDS` by at least 30 seconds.

Jules session IDs and normalized terminal results are stored in a SQLite registry under `$XDG_STATE_HOME/claude-ensemble/reviewer-mcp-sessions.sqlite3` (or `REVIEWER_MCP_SESSION_DB` for an alternate path). A retry with the same review input resumes the existing session or returns its stored result; it does not create another Jules session. If session creation times out before an ID can be recorded, the registry marks the outcome unknown and refuses to start a duplicate automatically. Resolve that record deliberately rather than assuming the external job was cancelled.

A client disconnect does **not** cancel the external Jules job. The broker continues until completion or the configured deadline, persists the session/result state, and treats a later identical request as a retry. A transport timeout is not evidence that the external job stopped.
