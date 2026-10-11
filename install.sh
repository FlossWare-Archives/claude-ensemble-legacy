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
    # Prefer the current FlossWare checkout layout, while retaining the
    # historical path for existing installations that still use it.
    if [ -d "${HOME}/Development/github/FlossWare/claude-ensemble" ]; then
        REPO_PATH="${HOME}/Development/github/FlossWare/claude-ensemble"
    elif [ -d "${HOME}/Development/FlossWare/claude-ensemble" ]; then
        REPO_PATH="${HOME}/Development/FlossWare/claude-ensemble"
    else
        REPO_PATH="${HOME}/Development/github/FlossWare/claude-ensemble"
    fi
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
CLAUDE_HOME="$HOME/.claude"
MEMORY_SERVICE_DIR="$REPO_PATH/memory-service"

# Fail before changing user configuration when a required runtime is missing.
# Claude Code is intentionally a prerequisite; this installer does not install it.
for required in git python3 systemctl; do
    if ! command -v "$required" >/dev/null 2>&1; then
        echo "ERROR: Required command not found: $required" >&2
        exit 1
    fi
done
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
    "$REPO_PATH/alert_service/install.sh" \
    "$REPO_PATH/session-messaging/install.sh" \
    "$REPO_PATH/graph-service/install.sh" \
    "$REPO_PATH/server/install.sh" \
    "$REPO_PATH/server/claude-ensemble.service.template" \
    "$REPO_PATH/tools/claude-config/lib/json_tool.py"; do
    if [ ! -f "$required_path" ]; then
        echo "ERROR: Required CE installation file is missing: $required_path" >&2
        exit 1
    fi
done

echo "================================================"
echo "  Claude Ensemble Toolkit Installer"
echo "================================================"
echo ""
echo "Repository:  $REPO_PATH"
echo "Claude Home: $CLAUDE_HOME"
echo ""

# Step 1: Create ~/.claude directories
echo "1. Setting up ~/.claude directories..."
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
        "~/.claude/hooks/memory-search-on-prompt.js" \
        "$REPO_PATH/hooks/memory-search-on-prompt.js"
    echo "   ✓ Normalized CE UserPromptSubmit memory hook"
fi

# Step 4: Symlink toolkit initialization script
echo ""
echo "4. Installing toolkit init script..."
INIT_LINK="$CLAUDE_HOME/ensemble-init.sh"
INIT_SOURCE="$REPO_PATH/scripts/ensemble-init.sh"
if [ -f "$INIT_SOURCE" ]; then
    rm -f "$INIT_LINK" 2>/dev/null || true
    ln -s "$INIT_SOURCE" "$INIT_LINK"
    echo "   ✓ Installed ensemble-init.sh"
fi

# Step 4b: Symlink config script
CONFIG_LINK="$CLAUDE_HOME/config.sh"
CONFIG_SOURCE="$REPO_PATH/scripts/config.sh"
if [ -f "$CONFIG_SOURCE" ]; then
    rm -f "$CONFIG_LINK" 2>/dev/null || true
    ln -s "$CONFIG_SOURCE" "$CONFIG_LINK"
    echo "   ✓ Installed config.sh"
fi

# Step 4c: Symlink CLAUDE.ENSEMBLE.md (ensemble practices guide)
echo ""
echo "4c. Installing ensemble practices guide..."
CLAUDE_ENSEMBLE_LINK="$CLAUDE_HOME/CLAUDE.ENSEMBLE.md"
CLAUDE_ENSEMBLE_SOURCE="$REPO_PATH/CLAUDE.ENSEMBLE.md"
if [ -f "$CLAUDE_ENSEMBLE_SOURCE" ]; then
    rm -f "$CLAUDE_ENSEMBLE_LINK" 2>/dev/null || true
    ln -s "$CLAUDE_ENSEMBLE_SOURCE" "$CLAUDE_ENSEMBLE_LINK"
    echo "   ✓ Installed CLAUDE.ENSEMBLE.md (ensemble practices)"
    echo "   ℹ Users can create their own ~/.claude/CLAUDE.md with additional practices"
fi

# Step 5: Symlink GA parameter evolution
echo ""
echo "5. Installing GA parameter evolution..."
GA_LINK="$CLAUDE_HOME/ga_parameter_evolution.md"
GA_SOURCE="$REPO_PATH/ga_tuning/parameter_evolution.md"
if [ -f "$GA_SOURCE" ]; then
    rm -f "$GA_LINK" 2>/dev/null || true
    ln -s "$GA_SOURCE" "$GA_LINK"
    echo "   ✓ Installed ga_parameter_evolution.md"
fi

# Step 6: Symlink memory service client
echo ""
echo "6. Installing memory service client..."
MEMORY_CLIENT="$CLAUDE_HOME/memory-client.py"
MEMORY_SOURCE="$MEMORY_SERVICE_DIR/memory_client.py"
if [ -f "$MEMORY_SOURCE" ]; then
    rm -f "$MEMORY_CLIENT" 2>/dev/null || true
    ln -s "$MEMORY_SOURCE" "$MEMORY_CLIENT"
    echo "   ✓ Installed memory-client.py"
fi

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

# Verify the entire managed service set, including Messenger's installer which
# reports status but historically did not fail when the unit stayed inactive.
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

# Step 8: Setup .mcp.json if not exists
echo ""
echo "8. Setting up MCP configuration..."
MCP_CONFIG="$HOME/.mcp.json"
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
MODEL_CONFIG="$HOME/.claude/toolkit-models.yaml"
if [ ! -f "$MODEL_CONFIG" ]; then
    if [ -f "$REPO_PATH/.toolkit-models.yaml.default" ]; then
        cp "$REPO_PATH/.toolkit-models.yaml.default" "$MODEL_CONFIG"
        echo "   ✓ Created $MODEL_CONFIG from template"
    else
        echo "   ⚠ Model config template not found in repo"
    fi
else
    echo "   ✓ $MODEL_CONFIG already exists"
fi

# Step 10: Setup credentials file
echo ""
echo "10. Setting up credentials..."
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
    echo "     - CURSOR_API_KEY (for JetBrains Cursor)"
else
    echo "   ✓ $SECRETS_FILE already exists"
fi

# Step 11: Add tools to PATH (shell init)
echo ""
echo "11. Adding tools to PATH..."
SHELL_RC=""
if [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
elif [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
fi

if [ -n "$SHELL_RC" ]; then
    EXPORT_LINE="export PATH=\"$REPO_PATH/tools:\$PATH\""
    if ! grep -q "claude-ensemble/tools" "$SHELL_RC" 2>/dev/null; then
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
        echo "   ℹ You can configure anytime by running: ~/.claude/config.sh"
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
echo "1. Edit ~/.mcp.json with your credentials"
echo "2. Ensure API tokens are set in environment"
echo "3. Exit and restart your terminal"
echo "4. Run: ensemble-init.sh (should be automatic at shell start)"
echo "5. To reconfigure tools/features: ~/.claude/config.sh"
echo ""
echo "Verify installation:"
echo "  cost-dashboard.py (view cost tracking)"
echo "  ga-tuning-dashboard.py (view GA parameters)"
echo ""
echo "Documentation: $REPO_PATH/README.md"
echo ""
