# Claude Ensemble release version resolution

```bash
# Calculate the next MAJOR.MINOR label without changing Git or deployment state.
bash tools/resolve-ce-version.sh

# Also check an existing deployment's releases/ directory for disagreement.
bash tools/resolve-ce-version.sh \
  --deployment-root "$HOME/.FlossWare/claude-ensemble"
```

The resolver is intentionally read-only. It does not create a Git tag, GitHub release, deployment directory, or activate services. When the candidate has been reviewed and the main checkout is ready, tag creation is a separate explicit operation.

## Rules

- Release tags use the exact `MAJOR.MINOR` form, such as `0.1` and `0.2`.
- With no version tags, the first candidate is `0.1`.
- Otherwise, the resolver selects the numerically greatest version and increments its minor component. Thus `0.9` resolves to `0.10`, not `0.9.1`.
- Numeric dotted tags with any other component count (for example `1.2.3`) are rejected as ambiguous rather than silently ignored.
- With `--deployment-root`, release directories must be real directories, each numeric release directory must have a matching Git tag (except the reserved legacy snapshot `0.0`), and an existing candidate causes a hard failure.
- A conflict requires manual reconciliation. The resolver never guesses, repairs state, tags a commit, or activates a release.

Run the regression suite with:

```bash
bash -n tools/resolve-ce-version.sh
bash -n tests/test_resolve_ce_version.sh
bash tests/test_resolve_ce_version.sh
bash -n tools/create-ce-release-tag.sh
bash tests/test_create_ce_release_tag.sh
```

## Explicit local tag creation

Run the separate tag command only from a clean `main` checkout:

```bash
bash tools/create-ce-release-tag.sh
```

It re-runs the resolver (optionally checking a deployment root), creates the next `MAJOR.MINOR` version as an annotated local Git tag, and verifies that the tag points to the current `HEAD`. It refuses dirty or non-`main` checkouts, a `main` branch that is not exactly synchronized with its configured upstream, and ambiguous version state. It does **not** push the tag, create a GitHub release, stage a deployment, or activate services. Review and push the tag through the repository's normal release process as a separate deliberate action.
