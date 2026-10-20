# Claude Ensemble release version resolution

```bash
# Calculate the next MAJOR.MINOR label without changing Git or deployment state.
bash tools/resolve-ce-version.sh

# Also check an existing deployment's releases/ directory for disagreement.
bash tools/resolve-ce-version.sh \
  --deployment-root "$HOME/.FlossWare/claude-ensemble"
```

The resolver is intentionally read-only. It does not create a Git tag, GitHub release, deployment directory, or activate services.

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
```
