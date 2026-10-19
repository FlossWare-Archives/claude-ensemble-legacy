# Credentials Setup Guide

Claude Ensemble can load credentials from a local file during its supported session initialization. Credential loading is **not universal across every shell, resumed session, or tool subprocess**. It depends on how CE is installed and initialized, and on whether the caller passes the relevant environment variables to child processes.

## Store credentials locally

The default file is `~/.FlossWare/secrets.env`. Keep it outside source repositories and use a secret manager or an organization-approved credential delivery mechanism where available.

Example format:

```bash
export GITLAB_TOKEN='replace-with-your-token'
export GOOGLE_API_KEY='replace-with-your-key'
export JIRA_API_TOKEN='replace-with-your-token'
export SONAR_TOKEN='replace-with-your-token'
```

Create the file with restrictive permissions:

```bash
install -d -m 700 "$HOME/.FlossWare"
( umask 077; touch "$HOME/.FlossWare/secrets.env" )
chmod 600 "$HOME/.FlossWare/secrets.env"
```

Edit the file with your preferred editor. Do not paste real credentials into chat, issue comments, logs, terminal transcripts, or tool output.

**Important:** `chmod 600` limits ordinary filesystem access; it does not prevent a process that can read the file from exposing its contents. `.gitignore` prevents some accidental additions only. It does not remove a secret already committed, prevent forced adds, or stop credentials from leaking through command output, logs, shell tracing, or tool transcripts. Treat any exposed credential as compromised and rotate it.

## Supported CE session initialization

The current `scripts/ensemble-init.sh` reads `ENSEMBLE_CREDENTIALS_FILE` (default: `~/.FlossWare/secrets.env`) when that script is sourced. Its parser accepts exported assignments with literal values, optionally single- or double-quoted. It does not execute the file as shell code. Unsupported lines are ignored with a warning.

For example, these are supported:

```bash
export SERVICE_TOKEN='literal-value'
export OTHER_TOKEN=another-literal-value
```

Do not rely on shell expressions, command substitutions, variable references, or multi-line values in this file. Use a secret manager or another approved mechanism for credentials requiring more complex handling.

This behavior applies only when CE's session initialization script is actually sourced. It does **not** establish that every Claude Code installation, resumed session, non-interactive shell, or arbitrary subprocess automatically receives the credentials. The repository does not universally install the `~/.bashrc`, `~/.zshrc`, `~/.bashenv`, `BASH_ENV`, or `~/.claude/hooks/user-prompt-submit.sh` configuration described in older versions of this guide. Configure shell startup or Claude Code hooks only when your specific installation documents and requires them, and review what data they can expose.

## Verify availability without revealing values

Check only whether a variable is set and non-empty. These examples intentionally print no credential value:

```bash
if [[ -n "${GITLAB_TOKEN:-}" ]]; then
  printf '%s\n' 'GITLAB_TOKEN is set'
else
  printf '%s\n' 'GITLAB_TOKEN is not set'
fi
```

To test whether a child Bash process inherited the variable without printing it:

```bash
bash -c '[[ -n "${GITLAB_TOKEN:-}" ]]'
status=$?
if [[ "$status" -eq 0 ]]; then
  printf '%s\n' 'GITLAB_TOKEN is available to child Bash'
else
  printf '%s\n' 'GITLAB_TOKEN is unavailable to child Bash'
fi
unset status
```

A successful check proves only that the variable is present in that process. It does not validate the token with its provider or prove every tool/session can access it.

## Troubleshooting safely

- Check file existence and permissions without reading its contents:
  ```bash
  test -r "${ENSEMBLE_CREDENTIALS_FILE:-$HOME/.FlossWare/secrets.env}" && echo 'credentials file is readable'
  stat -c '%a %n' "${ENSEMBLE_CREDENTIALS_FILE:-$HOME/.FlossWare/secrets.env}" 2>/dev/null || stat -f '%Lp %N' "${ENSEMBLE_CREDENTIALS_FILE:-$HOME/.FlossWare/secrets.env}"
  ```
- Check whether a variable is present using the presence-only snippet above. Do not use `echo $TOKEN`, `printenv TOKEN`, `env`, or other commands that display credential values.
- If CE's initialization parser reports an unsupported entry, simplify that entry to a literal exported assignment or use an approved secret manager.
- Never use `cat`, `head`, shell tracing (`set -x`), or debug logs to display the credentials file.
- Restart or reinitialize only the session/profile whose documented setup loads the credentials. Do not assume a prompt hook or shell startup file exists merely because another installation uses one.

## Multiple machines

Create or provision credentials separately on each machine using an approved secret manager, enterprise credential store, or secure transfer process. Do **not** synchronize secrets through Git, even in a private repository and even when `.gitignore` excludes the file. If a credential has entered Git history, removing the current file is not sufficient: revoke/rotate the credential and follow the repository's secret-incident procedure.

## See also

- `scripts/ensemble-init.sh` — CE session initialization and literal-assignment credential parser.
- `CLAUDE.ENSEMBLE.md` — Main project guidance.
