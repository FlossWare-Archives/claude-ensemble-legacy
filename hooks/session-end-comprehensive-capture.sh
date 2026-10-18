#!/bin/bash
# Comprehensive Session End Capture Hook
#
# Auto-captures all learning categories at session end:
# 1. Session outcomes (findings, decisions, accomplishments)
# 2. Cost patterns (spending, anomalies, savings)
# 3. Thompson performance (model rankings)
# 4. Learning dataset (autonomous learner outcomes)
# 5. Architecture decisions (design choices, trade-offs)
# 6. Integration status (service health, config)

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
SESSION_ID=$(date +%s)

python3 << 'PYTHON'
import json
import os
import sys
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
        sys.exit(0)

    # 1. Session learnings (explicit learnings from conversation)
    log_file = os.getenv('SESSION_LEARNING_LOG', '.claude-session-learning.log')
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            for line in f:
                try:
                    learning = json.loads(line.strip())
                    client.append('session_learnings', learning)
                except:
                    pass
        os.remove(log_file)

    # 2. Cost patterns from cost log
    cost_log = Path.home() / '.claude' / 'cost_tracking' / 'cost.log'
    if cost_log.exists():
        try:
            costs = []
            with open(cost_log, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        costs.append(entry)
                    except:
                        pass

            if costs:
                total_cost = sum(float(c.get('total_cost_usd', 0)) for c in costs)
                models_used = set(c.get('model') for c in costs)

                summary = {
                    'timestamp': datetime.utcnow().isoformat(),
                    'session_id': os.getenv('SESSION_ID', ''),
                    'total_cost': total_cost,
                    'models_used': list(models_used),
                    'entry_count': len(costs),
                    'avg_cost_per_call': total_cost / len(costs) if costs else 0
                }

                client.append('cost_patterns', summary)
        except:
            pass

    # 3. Thompson performance (model rankings)
    try:
        thompson_file = Path(ensemble_root) / 'thompson-service' / 'router_state.json'
        if thompson_file.exists():
            with open(thompson_file, 'r') as f:
                router_state = json.load(f)

            performance = {
                'timestamp': datetime.utcnow().isoformat(),
                'session_id': os.getenv('SESSION_ID', ''),
                'model_scores': router_state.get('scores', {}),
                'model_counts': router_state.get('counts', {}),
                'best_model': max(
                    router_state.get('scores', {}).items(),
                    key=lambda x: x[1],
                    default=('unknown', 0)
                )[0]
            }

            client.append('thompson_performance', performance)
    except:
        pass

    # 4. Learning dataset (autonomous learner outcomes)
    try:
        learning_dir = Path(ensemble_root) / 'learning' / 'autonomous_outcomes'
        if learning_dir.exists():
            outcomes = []
            for outcome_file in learning_dir.glob('*.json'):
                try:
                    with open(outcome_file, 'r') as f:
                        outcome = json.load(f)
                        outcomes.append(outcome)
                except:
                    pass

            if outcomes:
                summary = {
                    'timestamp': datetime.utcnow().isoformat(),
                    'session_id': os.getenv('SESSION_ID', ''),
                    'outcome_count': len(outcomes),
                    'models_analyzed': list(set(o.get('model') for o in outcomes if 'model' in o)),
                    'average_quality': sum(o.get('quality_score', 0) for o in outcomes) / len(outcomes) if outcomes else 0
                }

                client.append('learning_dataset', summary)
    except:
        pass

    # 5. Architecture decisions (design choices, trade-offs)
    try:
        # Check if any architecture notes were captured during session
        arch_notes = os.getenv('ARCHITECTURE_NOTES', '')
        if arch_notes:
            decision = {
                'timestamp': datetime.utcnow().isoformat(),
                'session_id': os.getenv('SESSION_ID', ''),
                'note': arch_notes
            }
            client.append('architecture_decisions', decision)
    except:
        pass

    # 6. Integration status (service health, config)
    try:
        # Check systemd services
        import subprocess
        services = ['claude-memory', 'claude-thompson', 'claude-learning', 'claude-alert']
        status = {}

        for service in services:
            try:
                result = subprocess.run(
                    ['systemctl', '--user', 'is-active', f'{service}.service'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                status[service] = result.stdout.strip()
            except:
                status[service] = 'unknown'

        integration = {
            'timestamp': datetime.utcnow().isoformat(),
            'session_id': os.getenv('SESSION_ID', ''),
            'service_status': status,
            'memory_dir': str(Path.home() / '.claude' / 'projects' / 'memory'),
            'ensemble_root': ensemble_root
        }

        client.append('integration_status', integration)
    except:
        pass

except:
    pass
PYTHON
