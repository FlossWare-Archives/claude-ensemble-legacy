#!/bin/bash
# Claude Ensemble Toolkit Initialization
# Sourced at session start to activate all tools

# This script configures the shell environment only. Model authentication is owned by
# Claude Code; do not load credentials from files or execute background reload loops.

# Only run heavy initialization once per session
if [ -n "$ENSEMBLE_INITIALIZED" ]; then
  return 0
fi
export ENSEMBLE_INITIALIZED=1

export ENSEMBLE_ROOT="${ENSEMBLE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export ENSEMBLE_COST_LOG="${ENSEMBLE_COST_LOG:-$HOME/.claude/cost_tracking/cost.log}"
export ENSEMBLE_MEMORY_DIR="${ENSEMBLE_MEMORY_DIR:-$HOME/.claude/projects/memory}"

# Ensure memory dir exists
mkdir -p "$ENSEMBLE_MEMORY_DIR"

# Function to reload toolkit (use when ensemble-init.sh is updated)
source-session-command() {
  echo "Reloading toolkit..."
  source "$ENSEMBLE_ROOT/scripts/ensemble-init.sh"
  echo "✓ Toolkit reloaded - credentials and all updates active"
}

# Function to check for session commands (use anytime to poll for updates)
check-session-commands() {
  if [ -f "$ENSEMBLE_MEMORY_DIR/SESSION_COMMANDS.md" ]; then
    echo "📋 SESSION COMMANDS:"
    head -5 "$ENSEMBLE_MEMORY_DIR/SESSION_COMMANDS.md"
    echo ""
    if grep -q "URGENT" "$ENSEMBLE_MEMORY_DIR/SESSION_COMMANDS.md" 2>/dev/null; then
      echo "⚠️  URGENT - Run: source-session-command"
    fi
  else
    echo "✓ No pending session commands"
  fi
}

# Session messaging: transient real-time events are separate from persistent memory.
messenger_publish() {
  local topic="$1"
  local data="${2:-{}}"
  if [ -z "$topic" ]; then
    echo "Usage: messenger_publish <topic> <json-data>"
    return 1
  fi
  CLAUDE_MESSENGER_SOCKET="${CLAUDE_MESSENGER_SOCKET:-$XDG_RUNTIME_DIR/claude-messenger/claude-messenger.sock}" \
    PYTHONPATH="$ENSEMBLE_ROOT/session-messaging${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -c 'import json, sys; from messenger_client import MessengerClient; print(MessengerClient().publish(sys.argv[1], json.loads(sys.argv[2])))' "$topic" "$data"
}

messenger_subscribe() {
  local topic="$1"
  if [ -z "$topic" ]; then
    echo "Usage: messenger_subscribe <topic>"
    return 1
  fi
  CLAUDE_MESSENGER_SOCKET="${CLAUDE_MESSENGER_SOCKET:-$XDG_RUNTIME_DIR/claude-messenger/claude-messenger.sock}" \
    PYTHONPATH="$ENSEMBLE_ROOT/session-messaging${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -c 'import sys; from messenger_client import MessengerClient; [print(message, flush=True) for message in MessengerClient().subscribe(sys.argv[1])]' "$topic"
}

# Add toolkit to PATH
export PATH="$ENSEMBLE_ROOT/tools:$PATH"

# Initialize cost tracking
mkdir -p "$(dirname "$ENSEMBLE_COST_LOG")"
touch "$ENSEMBLE_COST_LOG"

# Shell functions for easy memory access
query_memory() {
  python3 "$ENSEMBLE_ROOT/tools/query-memory.py" "$@"
}

mem_list() {
  echo "=== Project Memory Files ==="
  python3 "$ENSEMBLE_ROOT/tools/query-memory.py" list
}

mem_read() {
  if [ -z "$1" ]; then
    echo "Usage: mem_read <name>"
    return 1
  fi
  python3 "$ENSEMBLE_ROOT/tools/query-memory.py" read "$1"
}

mem_search() {
  if [ -z "$1" ]; then
    echo "Usage: mem_search <keyword>"
    return 1
  fi
  python3 "$ENSEMBLE_ROOT/tools/query-memory.py" search "$1"
}

# Capture discovered patterns/tools during session (not just at startup)
discover() {
  local category="$1"
  local content="$2"

  if [ -z "$category" ] || [ -z "$content" ]; then
    echo "Usage: discover '<category>' '<what-we-learned>'"
    echo ""
    echo "Examples:"
    echo "  discover 'tool' 'Found memory-synthesis shows all insights at once'"
    echo "  discover 'pattern' 'Arbitration pattern prevents blind spots'"
    echo "  discover 'optimization' 'Caching saves 69.8% on repeated prompts'"
    return 1
  fi

  python3 << EOF
import json
import os
import sys
from pathlib import Path
from datetime import datetime

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    sys.exit(1)

sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

try:
    from memory_client import MemoryClient

    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "category": "$category",
        "discovery": "$content"
    }

    client = MemoryClient()
    if client.connect():
        client.append('session_discoveries', entry)
        print("✓ Discovery saved (searchable via mem_search)")
except:
    pass
EOF
}

# Save learning immediately (not just at session end)
save_learning() {
  local title="$1"
  local detail="$2"

  if [ -z "$title" ]; then
    echo "Usage: save_learning '<title>' '<detail>'"
    return 1
  fi

  python3 << EOF
import json
import os
import sys
from pathlib import Path
from datetime import datetime

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    sys.exit(1)

sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

try:
    from memory_client import MemoryClient

    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "title": "$title",
        "detail": "$detail"
    }

    client = MemoryClient()
    if client.connect():
        client.append('session_learnings', entry)
        print("✓ Learning saved to memory service")
    else:
        print("⚠ Memory service unavailable (will save at session end)")
except:
    print("⚠ Could not save learning")
EOF
}

# Save architecture decision immediately
save_architecture() {
  local decision="$1"

  if [ -z "$decision" ]; then
    echo "Usage: save_architecture '<decision>'"
    return 1
  fi

  python3 << EOF
import json
import os
import sys
from pathlib import Path
from datetime import datetime

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    sys.exit(1)

sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

try:
    from memory_client import MemoryClient

    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "decision": "$decision"
    }

    client = MemoryClient()
    if client.connect():
        client.append('architecture_decisions', entry)
        print("✓ Architecture decision saved")
except:
    pass
EOF
}

# Function: Log API usage to cost tracking
log_ensemble_cost() {
  local model=$1
  local input_tokens=$2
  local output_tokens=$3
  local cost=$4
  local workflow=${5:-"ensemble-work"}

  python3 << EOF
import json
from datetime import datetime

entry = {
  "timestamp": datetime.utcnow().isoformat(),
  "model": "$model",
  "provider": "anthropic",
  "input_tokens": $input_tokens,
  "output_tokens": $output_tokens,
  "total_cost_usd": $cost,
  "workflow_id": "$workflow"
}

with open("$ENSEMBLE_COST_LOG", "a") as f:
  f.write(json.dumps(entry) + "\n")
EOF
}

# Function: Get Thompson routing decision
get_ensemble_model() {
  local task_type=$1

  python3 "$ENSEMBLE_ROOT/shared/thompson_router.py" << EOF 2>/dev/null | grep "Selected:" | head -1 | awk '{print $NF}'
EOF
}

# Function: Compress prompt
compress_ensemble_prompt() {
  local prompt=$1
  python3 << EOF
from compression.compression_api import compress_prompt
result = compress_prompt("$prompt", target_reduction=0.35)
print(f"{result.text}")
EOF
}

# Connect to Memory Service (central memory authority for all sessions)
python3 "$ENSEMBLE_ROOT/memory-service/memory_client.py" 2>&1 | grep -E "✓|✗" || true

# Initialize Autonomous Learning (Thompson self-improvement)
python3 "$ENSEMBLE_ROOT/tools/autonomous-learner.py" 2>&1 | grep "✓\|✗" || true

echo "✓ Claude Ensemble Toolkit initialized"
echo "  Tools: caching, compression, cost_tracking, ga_tuning, thompson_router, arbitration"
echo "  Cost logging: $ENSEMBLE_COST_LOG"
echo "  Memory: $ENSEMBLE_MEMORY_DIR"
echo "  Model discovery: running in background"
echo "  Arbitration: multi-phase orchestrator for critical decisions"
echo "  Autonomous learning: Thompson continuously improving from real tasks"

# Auto-save reference materials to memory for all sessions
python3 << 'AUTO_CAPTURE'
import os
import sys
from pathlib import Path

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    pass
else:
    sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

    try:
        from memory_client import MemoryClient
        client = MemoryClient()
        if not client.connect():
            pass
        else:
            # 1. Quick reference / help
            help_ref = """# Claude Ensemble Quick Reference

## Code Review
- review PR#123             # 2-phase
- review -3 PR#456         # 3-phase
- meta-review PR#123       # Challenge review

## Memory
- mem_search 'keyword'               # Keyword search
- query-memory.py semantic-search    # Meaning-based
- save_learning 'title' 'detail'     # Save learning

## Insights
- memory-synthesis.py       # All insights
- memory-analytics.py all   # All metrics
- memory-alerting.py        # Anomalies
"""
            client.write('reference_quick_start', help_ref)

            # 2. Model selection guide
            model_guide = """# When to Use Each Model

Haiku 4.5: Fast & cheap
  - Code reading, navigation, simple fixes

Sonnet 5: Balanced
  - Code review, architecture, design

Opus 5.5: Strongest reasoning
  - Security, critical bugs, deep analysis

Gemini: Different perspective
  - Challenge assumptions, cross-check

Cursor: IDE-integrated
  - Interactive coding, real-time suggestions

Thompson automatically selects based on learned performance.
"""
            client.write('reference_model_selection', model_guide)

            # 3. Pattern guide
            pattern_guide = """# Multi-AI Patterns

## Arbitration (Worker/Arbiter)
Phase 1: Workers analyze, Arbiter synthesizes
Phase 2: Different workers challenge findings
Phase 3: Final arbiter makes decision

## Consensus Process
1. One model reviews, flags findings
2. Different model challenges, finds gaps
3. You (the user) decide

Use for: Security, breaking changes, critical bugs
"""
            client.write('reference_patterns', pattern_guide)

            # 4. Tools & features overview
            tools_ref = """# Available Tools

Core: Memory, Thompson Router, Learning, Arbitration
Optimization: Compression (64.6%), Caching (69.8%), GA Tuning, Cost Tracking
Analytics: Feedback loops, Alerting, Synthesis, Dashboards

Use: query-memory.py semantic-search "tool name"
Or: help tools
"""
            client.write('reference_tools_overview', tools_ref)

            # 5. Best practices
            practices = """# Best Practices

✓ Load memory at session start (automatic)
✓ Use arbiter/workers for critical decisions
✓ Review meta-reviews to catch blind spots
✓ Save learnings as they happen (save_learning)
✓ Use semantic-search for fuzzy matching
✓ Run memory-synthesis.py for insights
✓ Monitor memory-alerting.py for issues

Session data persists across restarts via memory service.
"""
            client.write('reference_best_practices', practices)

    except:
        pass
AUTO_CAPTURE

# Capture toolkit state and available commands to memory
python3 << 'TOOLKIT_STATE'
import sys
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    sys.exit(0)

sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

try:
    from memory_client import MemoryClient
    client = MemoryClient()
    if not client.connect():
        pass
    else:
        # Get current git commit
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=ensemble_root,
                capture_output=True,
                text=True,
                timeout=2
            )
            git_commit = result.stdout.strip() if result.returncode == 0 else 'unknown'
        except:
            git_commit = 'unknown'

        # List available commands
        tools_dir = Path(ensemble_root) / 'tools'
        commands = []
        if tools_dir.exists():
            for tool in sorted(tools_dir.glob('*')):
                if tool.is_symlink() or (tool.is_file() and tool.stat().st_mode & 0o111):
                    commands.append(tool.name)

        # Save toolkit state
        toolkit_state = f"""# Toolkit State

**Last Updated:** {datetime.utcnow().isoformat()}
**Git Commit:** {git_commit}

## Available Commands
{chr(10).join(f'- {cmd}' for cmd in commands if cmd != '__pycache__')}

All sessions read from: $ENSEMBLE_ROOT
Memory is auto-synced across all sessions.
"""
        client.write('toolkit_state', toolkit_state)

except:
    pass
TOOLKIT_STATE

# Auto-sync memory from git (pull latest from other machines)
if [ -f "$ENSEMBLE_ROOT/scripts/memory-sync-git.sh" ]; then
    python3 << 'GIT_PULL_MEMORY'
import subprocess
import os
from pathlib import Path

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    pass
else:
    try:
        # Pull latest memory from git (fast, only if network available)
        result = subprocess.run(
            ['git', 'pull', 'origin', 'main'],
            cwd=ensemble_root,
            capture_output=True,
            timeout=3
        )

        # Copy memory files from repo to local cache
        memory_repo = Path(ensemble_root) / 'memory'
        memory_local = Path(os.environ.get('ENSEMBLE_MEMORY_DIR', str(Path.home() / '.claude' / 'projects' / 'memory'))).expanduser()

        if memory_repo.exists():
            for md_file in memory_repo.glob('*.md'):
                dest = memory_local / md_file.name
                dest.write_text(md_file.read_text(), encoding='utf-8')
    except:
        # Offline is OK - sessions continue with local memory
        pass
GIT_PULL_MEMORY
fi

# Display available memories at session start
python3 << 'MEMORY_STATUS'
import sys
import os
from pathlib import Path

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    sys.exit(0)

sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

try:
    from memory_client import MemoryClient
    client = MemoryClient()
    if client.connect():
        memories = sorted([m for m in client.list() if m != 'MEMORY'])
        if memories:
            print("\n📚 Project Memory Available:")
            for mem in memories[:8]:
                print(f"   • {mem}")
            if len(memories) > 8:
                print(f"   ... and {len(memories) - 8} more")
            print("   Use: mem_search <keyword> | mem_read <name>")
except:
    pass
MEMORY_STATUS

