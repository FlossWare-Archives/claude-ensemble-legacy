#!/bin/bash
# Claude Ensemble - Installation Script
#
# Usage (automatic download + install):
#   curl -fsSL https://raw.githubusercontent.com/FlossWare/claude-ensemble/main/install.sh | bash
#
# Or with custom path (for local development):
#   ./install.sh /path/to/claude-ensemble
#
# With no argument, install into the default checkout location.
# With an argument, use that exact checkout/path.

set -Eeuo pipefail

if [ "$#" -eq 0 ]; then
    # This installer operates on the source checkout containing the script.
    # Use tools/install-ce-versioned.sh for an immutable standalone deployment.
    REPO_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    echo "Using source checkout for development install: $REPO_PATH"
    echo "For standalone deployment, use tools/install-ce-versioned.sh."
else
    REPO_PATH="$1"
fi

# Auto-clone if the selected repository path does not exist.
if [ ! -d "$REPO_PATH" ]; then
    echo "Cloning claude-ensemble repository..."
    mkdir -p "$(dirname "$REPO_PATH")"
    git clone https://github.com/FlossWare/claude-ensemble.git "$REPO_PATH"
fi

REPO_PATH="$(cd "$REPO_PATH" && pwd)"
CLAUDE_HOME="${ENSEMBLE_CLAUDE_CODE_CONFIG_DIR:-$HOME/.claude}"
case "$CLAUDE_HOME" in "~") CLAUDE_HOME="$HOME" ;; "~/"*) CLAUDE_HOME="$HOME/${CLAUDE_HOME#\~/}" ;; esac
MEMORY_SERVICE_DIR="$REPO_PATH/memory-service"

# Fail before changing user configuration when a required runtime is missing.
# Claude Code is intentionally a prerequisite; this installer does not install it.
for required in git python3 node systemctl; do
    if ! command -v "$required" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: $required" >&2
        exit 1
    fi
done
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "ERROR: Python 3.11 or newer is required; found $(python3 --version 2>&1)." >&2
    exit 1
fi
if ! node -e 'const [major] = process.versions.node.split(".").map(Number); process.exit(major >= 22 ? 0 : 1)'; then
    echo "ERROR: Node.js 22 or newer is required; found $(node --version 2>&1)." >&2
    exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: Claude Code CLI was not found on PATH. Install Claude Code first, then rerun install.sh." >&2
    exit 1
fi
if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "ERROR: systemd user manager is unavailable. Log into a systemd user session and rerun install.sh." >&2
    exit 1
fi
for required_path in \
    "$REPO_PATH/memory-service/install.sh" \
    "$REPO_PATH/thompson-service/install.sh" \
    "$REPO_PATH/learning-service/install.sh" \
    "$REPO_PATH/graph-service/install.sh" \
    "$REPO_PATH/server/install.sh" \
    "$REPO_PATH/server/claude-ensemble.service.template" \
    "$REPO_PATH/tools/claude-config/lib/json_tool.py"; do
    if [ ! -f "$required_path" ]; then
        echo "ERROR: Required CE installation file is missing: $required_path" >&2
        exit 1
    fi
done

# Snapshot the selected profile, existing CE deployment state, and managed units
# before the first configuration or service mutation. If backup fails, stop here.
bash "$REPO_PATH/tools/backup-ce-state.sh" "pre-runtime-install" "${ENSEMBLE_INSTALL_ROOT:-$HOME/.FlossWare/claude-ensemble}" "$CLAUDE_HOME"

echo "================================================"
echo "  Claude Ensemble Toolkit Installer"
echo "================================================"
echo ""
echo "Repository:  $REPO_PATH"
echo "Claude profile: $CLAUDE_HOME"
echo ""

# Step 1: Create ~/.claude directories
echo "1. Setting up Claude Code profile directories at $CLAUDE_HOME..."
mkdir -p "$CLAUDE_HOME"/{hooks,projects/memory,cost_tracking}
echo "   ✓ Created directories"

# Step 2: Install hooks as independent deployment files.
# Existing differing files are deliberately preserved. This top-level installer
# does not silently overwrite user edits; to update CE-managed hook content,
# use tools/claude-config/install.sh (or its documented force/update path).
echo ""
echo "2. Installing hooks..."
if [ -d "$REPO_PATH/hooks" ]; then
    for hook_file in "$REPO_PATH/hooks"/*.js "$REPO_PATH/hooks"/*.sh; do
        if [ -f "$hook_file" ]; then
            hook_name=$(basename "$hook_file")
            hook_link="$CLAUDE_HOME/hooks/$hook_name"
            if [ ! -e "$hook_link" ] && [ ! -L "$hook_link" ]; then
                install -m 700 "$hook_file" "$hook_link"
                echo "   ✓ Installed $hook_name"
            elif [ -L "$hook_link" ] && [ "$(readlink -f "$hook_link")" = "$(readlink -f "$hook_file")" ]; then
                # Do not chmod a repository symlink target: that would mutate the
                # checkout. Replace this recognized deployment symlink with an
                # independent executable copy instead.
                rm -- "$hook_link"
                install -m 700 "$hook_file" "$hook_link"
                echo "   ✓ Replaced repository symlink with executable deployment copy: $hook_name"
            elif [ -f "$hook_link" ] && cmp -s "$hook_file" "$hook_link"; then
                # Keep identical user contents, repairing only the deployment mode.
                chmod 700 "$hook_link"
                echo "   ✓ $hook_name is current; executable permissions verified"
            else
                if [ ! -x "$hook_link" ]; then
                    echo "ERROR: Preserved hook is not executable: $hook_link" >&2
                    echo "       Make it executable (chmod 700 '$hook_link') or review its contents before rerunning install.sh." >&2
                    exit 1
                fi
                echo "   ! Preserved existing $hook_link; repository version differs."
                echo "     Review manually before replacing it."
            fi
        fi
    done
fi

# Step 3: Setup settings.json (NOT symlinked, user-customizable)
echo ""
echo "3. Setting up settings.json..."
SETTINGS_FILE="$CLAUDE_HOME/settings.json"
if [ ! -f "$SETTINGS_FILE" ]; then
    # Do not seed machine-specific or vendor-specific settings from the legacy
    # template. Start neutral; CE adds only its managed hook registrations below.
    printf '{}\n' > "$SETTINGS_FILE"
    echo "   ✓ Created minimal $SETTINGS_FILE; preserving neutral Claude Code defaults"
else
    echo "   ✓ $SETTINGS_FILE already exists (keeping existing)"
fi

# Normalize the CE-managed memory hook without replacing unrelated user hooks.
# This also removes duplicate CE registrations left by older installers.
CLAUDE_CONFIG_TOOL="$REPO_PATH/tools/claude-config/lib/json_tool.py"
if [ -f "$SETTINGS_FILE" ] && [ -f "$CLAUDE_CONFIG_TOOL" ] && [ -f "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" ]; then
    python3 "$CLAUDE_CONFIG_TOOL" install-hook \
        "$SETTINGS_FILE" \
        "$CLAUDE_HOME/hooks/memory-search-on-prompt.js" \
        "$REPO_PATH/hooks/memory-search-on-prompt.js"
    echo "   ✓ Normalized CE UserPromptSubmit memory hook"
fi

# Register the canonical idempotent SessionEnd capture alongside prompt retrieval.
# The JSON tool preserves unrelated user hooks and normalizes only the CE-owned
# session-end registration.
if [ -f "$SETTINGS_FILE" ] && [ -f "$CLAUDE_CONFIG_TOOL" ] && [ -f "$CLAUDE_HOME/hooks/session-end-memory-capture.js" ]; then
    python3 "$CLAUDE_CONFIG_TOOL" install-session-end-hook \
        "$SETTINGS_FILE" \
        "$CLAUDE_HOME/hooks/session-end-memory-capture.js" \
        "$REPO_PATH/hooks/session-end-memory-capture.js"
    echo "   ✓ Normalized CE SessionEnd memory capture hook"
fi

# Install managed runtime assets as independent files, never repository symlinks.
# This allows a standalone deployment to remain usable when the developer's
# checkout is moved, cleaned, or updated. User-owned paths are preserved.
install_managed_copy() {
    local destination="$1" source="$2" label="$3" source_mode
    [ -f "$source" ] || return 0
    if [ -L "$destination" ]; then
        local resolved_target target_repo source_relative target_relative
        resolved_target="$(readlink -f "$destination" 2>/dev/null || true)"
        target_repo=""
        source_relative="${source#"$REPO_PATH"/}"
        if [ -n "$resolved_target" ]; then
            target_repo="$(git -C "$(dirname -- "$resolved_target")" rev-parse --show-toplevel 2>/dev/null || true)"
        fi
        target_relative=""
        if [ -n "$target_repo" ] && [[ "$resolved_target" == "$target_repo/"* ]]; then
            target_relative="${resolved_target#"$target_repo"/}"
        fi
        if [ "$resolved_target" = "$(readlink -f "$source")" ] || { [ -n "$target_repo" ] && [ "$target_relative" = "$source_relative" ]; }; then
            rm -- "$destination"
            source_mode="$(stat -c '%a' "$source")"
            install -m "$source_mode" -- "$source" "$destination"
            echo "   ✓ Replaced repository symlink with independent copy: $label"
        else
            echo "   ! Preserved user-managed symlink at $destination."
        fi
    elif [ -e "$destination" ]; then
        if [ -f "$destination" ] && cmp -s -- "$source" "$destination"; then
            source_mode="$(stat -c '%a' "$source")"
            chmod "$source_mode" "$destination"
            echo "   ✓ $label is current; independent copy verified"
        else
            echo "   ! Preserved existing $destination; repository version differs."
        fi
    else
        source_mode="$(stat -c '%a' "$source")"
        install -m "$source_mode" -- "$source" "$destination"
        echo "   ✓ Installed independent copy: $label"
    fi
}

# Step 4: Install toolkit initialization and configuration links
echo ""
echo "4. Installing toolkit scripts..."
INIT_SOURCE="$REPO_PATH/scripts/ensemble-init.sh"
CONFIG_SOURCE="$REPO_PATH/scripts/config.sh"
install_managed_copy "$CLAUDE_HOME/ensemble-init.sh" "$INIT_SOURCE" "ensemble-init.sh"
install_managed_copy "$CLAUDE_HOME/config.sh" "$CONFIG_SOURCE" "config.sh"

# Step 4c: Install ensemble practices guide
echo ""
echo "4c. Installing ensemble practices guide..."
CLAUDE_ENSEMBLE_SOURCE="$REPO_PATH/CLAUDE.ENSEMBLE.md"
install_managed_copy "$CLAUDE_HOME/CLAUDE.ENSEMBLE.md" "$CLAUDE_ENSEMBLE_SOURCE" "CLAUDE.ENSEMBLE.md"
if [ -f "$CLAUDE_ENSEMBLE_SOURCE" ]; then
    echo "   ℹ Users can create their own $CLAUDE_HOME/CLAUDE.md with additional practices"
fi

# Step 4d: Install the CE Jira-backed review skill. Preserve custom skill files
# and symlinked skill locations rather than overwriting or following them.
echo ""
echo "4d. Installing CE Jira review skill..."
CE_SKILL_INSTALLER="$REPO_PATH/tools/install-ce-jira-review-skill.sh"
CE_SKILL_SOURCE="$REPO_PATH/claude-code-skills/ce-jira-review/SKILL.md"
CE_SKILL_DEST="$CLAUDE_HOME/skills/ce-jira-review/SKILL.md"
if [ -L "$CLAUDE_HOME/skills" ] || [ -L "$CLAUDE_HOME/skills/ce-jira-review" ]; then
    echo "   ! Preserved symlinked skills location; install CE Jira review skill manually after reviewing the target."
elif [ -e "$CE_SKILL_DEST" ] || [ -L "$CE_SKILL_DEST" ]; then
    if [ -f "$CE_SKILL_DEST" ] && cmp -s "$CE_SKILL_SOURCE" "$CE_SKILL_DEST"; then
        ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" bash "$CE_SKILL_INSTALLER"
    else
        echo "   ! Preserved existing custom skill at $CE_SKILL_DEST; no content was changed."
    fi
elif [ -f "$CE_SKILL_INSTALLER" ] && [ -f "$CE_SKILL_SOURCE" ]; then
    ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" bash "$CE_SKILL_INSTALLER"
else
    echo "ERROR: CE Jira review skill installer or source is missing." >&2
    exit 1
fi

# Step 4e: Install the general CE code review skill (no Jira ticket required).
echo ""
echo "4e. Installing CE code review skill..."
CE_REVIEW_INSTALLER="$REPO_PATH/tools/install-ce-review-skill.sh"
CE_REVIEW_SOURCE="$REPO_PATH/claude-code-skills/ce-review/SKILL.md"
CE_REVIEW_DEST="$CLAUDE_HOME/skills/ce-review/SKILL.md"
if [ -L "$CLAUDE_HOME/skills" ] || [ -L "$CLAUDE_HOME/skills/ce-review" ]; then
    echo "   ! Preserved symlinked skills location; install /ce-review manually after reviewing the target."
elif [ -e "$CE_REVIEW_DEST" ] || [ -L "$CE_REVIEW_DEST" ]; then
    if [ -f "$CE_REVIEW_DEST" ] && [ ! -L "$CE_REVIEW_DEST" ] && cmp -s "$CE_REVIEW_SOURCE" "$CE_REVIEW_DEST"; then
        ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" bash "$CE_REVIEW_INSTALLER"
    else
        echo "   ! Preserved existing custom skill at $CE_REVIEW_DEST; no content was changed."
    fi
elif [ -f "$CE_REVIEW_INSTALLER" ] && [ -f "$CE_REVIEW_SOURCE" ]; then
    ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" bash "$CE_REVIEW_INSTALLER"
else
    echo "ERROR: CE code review skill installer or source is missing." >&2
    exit 1
fi

# Step 4f: Install the complete CE slash-command skill set. Individual installers
# preserve custom skills; conflicts are reported and skipped without aborting the
# remainder of the install.
echo ""
echo "4f. Installing CE slash-command skills..."
CE_SKILLS_INSTALLER="$REPO_PATH/tools/install-ce-skills.sh"
if [ -f "$CE_SKILLS_INSTALLER" ]; then
    ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CLAUDE_HOME" CLAUDE_CONFIG_DIR="$CLAUDE_HOME" bash "$CE_SKILLS_INSTALLER"
else
    echo "ERROR: CE skills installer is missing: $CE_SKILLS_INSTALLER" >&2
    exit 1
fi

# Step 5: Install GA parameter evolution link
echo ""
echo "5. Installing GA parameter evolution..."
GA_SOURCE="$REPO_PATH/ga_tuning/parameter_evolution.md"
install_managed_copy "$CLAUDE_HOME/ga_parameter_evolution.md" "$GA_SOURCE" "ga_parameter_evolution.md"

# Step 6: Install memory service client link
echo ""
echo "6. Installing memory service client..."
MEMORY_SOURCE="$MEMORY_SERVICE_DIR/memory_client.py"
install_managed_copy "$CLAUDE_HOME/memory-client.py" "$MEMORY_SOURCE" "memory-client.py"

# Step 7: Install and start systemd user services
# Each service owns its own unit installation and lifecycle. Calling the
# individual installers here keeps the top-level installer consistent with
# direct service installation and ensures newly added services are actually
# enabled and started rather than merely rendered to disk.
echo ""
echo "7. Installing and starting systemd user services..."
SERVICE_INSTALLERS=(
    "memory-service/install.sh"
    "thompson-service/install.sh"
    "learning-service/install.sh"
    "alert_service/install.sh"
    "session-messaging/install.sh"
    "graph-service/install.sh"
    "server/install.sh"
)

for SERVICE_INSTALLER in "${SERVICE_INSTALLERS[@]}"; do
    if [ ! -x "$REPO_PATH/$SERVICE_INSTALLER" ]; then
        echo "   ✗ Missing service installer: $REPO_PATH/$SERVICE_INSTALLER" >&2
        exit 1
    fi
    echo "   → Installing $SERVICE_INSTALLER"
    bash "$REPO_PATH/$SERVICE_INSTALLER"
done

# Verify the entire managed service set unless the standalone installer has
# explicitly deferred startup until the full installation and symlink audit finish.
if [[ "${ENSEMBLE_DEFER_SERVICE_START:-0}" != "1" ]]; then
    for unit in \
        claude-memory.service \
        claude-thompson.service \
        claude-learning.service \
        claude-alert.service \
        claude-messenger.service \
        claude-graph.service \
        claude-ensemble.service; do
        if ! systemctl --user is-active --quiet "$unit"; then
            echo "ERROR: Required CE service is not active: $unit" >&2
            echo "       Inspect with: journalctl --user -u $unit -n 80 --no-pager" >&2
            exit 1
        fi
        echo "   ✓ Verified $unit"
    done
else
    echo "   ✓ Service startup deferred until installation and symlink audit complete"
fi

# Step 8: Setup .mcp.json if not exists
echo ""
echo "8. Setting up MCP configuration..."
MCP_CONFIG="$CLAUDE_HOME/.mcp.json"
if [ ! -f "$MCP_CONFIG" ]; then
    cat > "$MCP_CONFIG" << 'EOF'
{
  "mcpServers": {}
}
EOF
    echo "   ✓ Created .mcp.json (configure MCP servers as needed)"
else
    echo "   ✓ .mcp.json already exists"
fi

# Step 9: Setup model configuration
echo ""
echo "9. Setting up model configuration..."
MODEL_CONFIG="$CLAUDE_HOME/toolkit-models.yaml"
if [ ! -f "$MODEL_CONFIG" ]; then
    if [ -f "$REPO_PATH/toolkit-models.yaml.default" ]; then
        cp "$REPO_PATH/toolkit-models.yaml.default" "$MODEL_CONFIG"
        echo "   ✓ Created $MODEL_CONFIG from template"
    else
        echo "   ⚠ Model config template not found in repo"
    fi
else
    echo "   ✓ $MODEL_CONFIG already exists"
fi

# Step 10: Setup credentials file
# A managed release must not create or mutate a global credentials file outside
# its deployment. Its service units optionally read shared/config/environment;
# credential provisioning is deliberately separate from release activation.
if [[ "${ENSEMBLE_STANDALONE_INSTALL:-0}" == "1" ]]; then
    if [[ -z "${ENSEMBLE_INSTALL_ROOT:-}" ]]; then
        echo "ERROR: Standalone install requires ENSEMBLE_INSTALL_ROOT; refusing global credential fallback." >&2
        exit 1
    fi
    CREDENTIALS_ENV="${ENSEMBLE_INSTALL_ROOT%/}/shared/config/environment"
    echo "   ℹ Standalone deployment: not creating ~/.FlossWare/secrets.env."
    echo "   ℹ Optional managed service environment file: $CREDENTIALS_ENV"
    echo "   ℹ Provision credentials separately; this installer does not copy or create secrets."
else
    # Compatibility for legacy development installs only. Managed standalone
    # deployments must use their deployment-owned configuration/credential flow.
    SECRETS_FILE="$HOME/.FlossWare/secrets.env"
    mkdir -p "$(dirname "$SECRETS_FILE")"
    if [ ! -f "$SECRETS_FILE" ]; then
        cat > "$SECRETS_FILE" << 'EOF'
# Claude Ensemble API Credentials
# Source this file in your shell or set these in your environment
#
# export ANTHROPIC_API_KEY="your-key-here"
# export GOOGLE_API_KEY="your-key-here"
# export CURSOR_API_KEY="your-key-here"
EOF
        echo "   ✓ Created $SECRETS_FILE"
        echo "   ℹ Edit it with your API keys:"
        echo "     - ANTHROPIC_API_KEY (for Claude models)"
        echo "     - GOOGLE_API_KEY (for Gemini)"
        echo "     - CURSOR_API_KEY (for Cursor-compatible providers)"
    else
        echo "   ✓ $SECRETS_FILE already exists"
    fi
fi

# Step 11: Add tools to PATH (shell init)
echo ""
echo "11. Adding tools to PATH..."
if [[ "${ENSEMBLE_STANDALONE_INSTALL:-0}" == "1" ]]; then
    echo "   ℹ Standalone deployment: leaving shell startup files unchanged."
fi
SHELL_RC=""
if [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
elif [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
fi

if [ -n "$SHELL_RC" ] && [[ "${ENSEMBLE_STANDALONE_INSTALL:-0}" != "1" ]]; then
    EXPORT_LINE="export PATH=\"$REPO_PATH/tools:\$PATH\""
    # Use fixed-string matching for the current checkout path. Do not treat
    # any historical claude-ensemble path as equivalent: after a checkout move,
    # the active tools directory must be added rather than silently skipped.
    if ! grep -Fq -- "$REPO_PATH/tools" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# Claude Ensemble Tools" >> "$SHELL_RC"
        echo "$EXPORT_LINE" >> "$SHELL_RC"
        echo "   ✓ Added tools to PATH in $SHELL_RC"
    else
        echo "   ✓ Tools already in PATH"
    fi
fi

echo ""
echo "================================================"
echo "  Configuration"
echo "================================================"
echo ""

# Never block a non-interactive install (for example, curl | bash).
if [ -t 0 ] && [ -t 1 ]; then
    read -r -p "Would you like to configure which tools/features to enable? [y/N]: " -n 1 REPLY
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        bash "$CONFIG_SOURCE"
    else
        echo "   ℹ You can configure anytime by running: $CLAUDE_HOME/config.sh"
    fi
else
    echo "   ℹ Non-interactive install: keeping default feature configuration."
fi

echo ""
echo "================================================"
echo "  Installation Complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "1. Edit $CLAUDE_HOME/.mcp.json with your credentials"
echo "2. Ensure API tokens are set in environment"
echo "3. Exit and restart your terminal"
echo "4. Run: $CLAUDE_HOME/ensemble-init.sh (should be automatic at shell start)"
echo "5. To reconfigure tools/features: $CLAUDE_HOME/config.sh"
echo ""
echo "Verify installation:"
echo "  cost-dashboard.py (view cost tracking)"
echo "  ga-tuning-dashboard.py (view GA parameters)"
echo ""
echo "Documentation: $REPO_PATH/README.md"
echo ""
