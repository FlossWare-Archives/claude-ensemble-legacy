#!/bin/bash
# GA Tuning Scheduler - Runs weekly to optimize RH tool parameters
# No API calls needed - all local evaluation

RH_TOOLS_ROOT="${RH_TOOLS_ROOT:-${CE_GLOBAL_SKILLS_ROOT:-$HOME/Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills}}"
export RH_TOOLS_ROOT
GA_RESULTS_DIR="$RH_TOOLS_ROOT/ga_tuning/results"
LOG_FILE="$HOME/.claude/ga_tuning_schedule.log"

# Create results directory
mkdir -p "$GA_RESULTS_DIR"

# Run GA tuning
echo "[$(date)] Starting GA tuning..." >> "$LOG_FILE"

cd "$RH_TOOLS_ROOT" || exit 1

# Discover latest available models
echo "[$(date)] Discovering available models..." >> "$LOG_FILE"
python3 tools/discover-models.py >> "$LOG_FILE" 2>&1

# Run GA with default config
echo "[$(date)] Running GA tuning..." >> "$LOG_FILE"
cd "$RH_TOOLS_ROOT/ga_tuning" || exit 1
python3 ga_tuner.py >> "$LOG_FILE" 2>&1

if [ $? -eq 0 ]; then
  echo "[$(date)] ✓ GA tuning completed successfully" >> "$LOG_FILE"

  # Extract and apply all optimized parameters
  echo "[$(date)] Extracting GA parameters..." >> "$LOG_FILE"
  python3 extract_and_apply_parameters.py >> "$LOG_FILE" 2>&1

  if [ $? -eq 0 ]; then
    echo "[$(date)] ✓ Parameters extracted and applied to settings.json" >> "$LOG_FILE"
    echo "[$(date)] ✓ Parameter evolution logged" >> "$LOG_FILE"
  else
    echo "[$(date)] ⚠ Parameter extraction failed" >> "$LOG_FILE"
  fi

else
  echo "[$(date)] ✗ GA tuning failed" >> "$LOG_FILE"
  exit 1
fi
