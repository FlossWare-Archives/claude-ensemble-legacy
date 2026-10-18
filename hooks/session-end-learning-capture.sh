#!/bin/bash
# Session End Learning Capture Hook
#
# Triggered when session ends - captures insights learned during conversation
# and automatically saves them to the memory service.
#
# The memory service handles indexing, deduplication, and persistence.

if [ -z "${ENSEMBLE_ROOT:-}" ]; then
    if [ -L "$HOME/.claude/ensemble-init.sh" ]; then
        ENSEMBLE_ROOT="$(cd "$(dirname "$(readlink -f "$HOME/.claude/ensemble-init.sh")")/.." && pwd)"
    elif [ -d "$HOME/.FlossWare/claude-ensemble/current/memory-service" ]; then
        ENSEMBLE_ROOT="$HOME/.FlossWare/claude-ensemble/current"
    elif [ -d "$HOME/Development/github/FlossWare/claude-ensemble/memory-service" ]; then
        # Compatibility fallback for existing source-checkout installs.
        ENSEMBLE_ROOT="$HOME/Development/github/FlossWare/claude-ensemble"
    else
        ENSEMBLE_ROOT=""
    fi
fi
SESSION_LEARNING_LOG="${SESSION_LEARNING_LOG:-.claude-session-learning.log}"

# If session captured any learnings, save them to memory service
if [ -f "$SESSION_LEARNING_LOG" ]; then
    # Read learnings from session log
    python3 << 'PYTHON'
import json
import os
import sys
from pathlib import Path

ensemble_root = os.getenv('ENSEMBLE_ROOT', '')
if not ensemble_root:
    sys.exit(0)

sys.path.insert(0, str(Path(ensemble_root) / 'memory-service'))

try:
    from memory_client import MemoryClient

    log_file = os.getenv('SESSION_LEARNING_LOG', '.claude-session-learning.log')
    if not os.path.exists(log_file):
        sys.exit(0)

    client = MemoryClient()
    if not client.connect():
        sys.exit(0)

    # Read session learnings
    with open(log_file, 'r') as f:
        for line in f:
            try:
                learning = json.loads(line.strip())
                # Append to session_learnings memory file
                client.append('session_learnings', learning)
            except:
                pass

    # Clean up session log
    os.remove(log_file)

except:
    pass
PYTHON
fi
