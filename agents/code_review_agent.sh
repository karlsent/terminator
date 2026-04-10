#!/bin/bash
# Агент code review изменений в ветке
# Использование: ./agents/code_review_agent.sh <BRANCH> [TASK_ID] ["директивы"]

set -e

BRANCH=$1
TASK_ID=${2:-""}
EXTRA_DIRECTIVES=$3

if [ -z "$BRANCH" ]; then
    echo "Использование: $0 <BRANCH> [TASK_ID] [\"директивы\"]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_SH="${TERMINATOR_CONFIG_SH:-$HOME/.terminator/task_agent_config.sh}"
source "$CONFIG_SH"

RESULTS_DIR="${CODE_REVIEW_DIR:-$HOME/Documents/CodeReview}"
mkdir -p "$RESULTS_DIR"

TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
LOG_FILE="$RESULTS_DIR/${TIMESTAMP}_${BRANCH}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск code review — ветка: $BRANCH${TASK_ID:+, задача #$TASK_ID}" | tee "$LOG_FILE"

export YC_PROFILE="${YC_PROFILE:-default}"
[ -n "$NO_PROXY_DOMAINS" ] && export no_proxy="$NO_PROXY_DOMAINS" && export NO_PROXY="$NO_PROXY_DOMAINS"

PROMPT=$(sed \
    -e "s/{{BRANCH}}/$BRANCH/g" \
    -e "s/{{TASK_ID}}/$TASK_ID/g" \
    -e "s|BITRIX_REST_PLACEHOLDER|$BITRIX_REST_URL|g" \
    -e "s|{{RESULTS_DIR}}|$RESULTS_DIR|g" \
    -e "s/{{TIMESTAMP}}/$TIMESTAMP/g" \
    -e "s|{{GIT_REPO}}|$GIT_REPO|g" \
    -e "s|{{NOTES_DIR}}|${NOTES_DIR:-$HOME/.terminator/notes}|g" \
    -e "s|{{ATTACHMENTS_DIR}}|${NOTES_DIR:-$HOME/.terminator/notes}/attachments|g" \
    -e "s|{{YC_PROFILE}}|$YC_PROFILE|g" \
    "$SCRIPT_DIR/code_review_agent_prompt.md")

if [ -n "$EXTRA_DIRECTIVES" ]; then
    PROMPT="$PROMPT

---

## ⚡ Дополнительные директивы для этого запуска

$EXTRA_DIRECTIVES"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Директивы: $EXTRA_DIRECTIVES" | tee -a "$LOG_FILE"
fi

cd "$GIT_REPO"

${CLAUDE_PROXY_CMD:-claude} --print "$PROMPT" \
    --allowedTools "Bash,Read,Write,Glob,Grep,Agent" \
    --output-format stream-json --verbose \
    2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Code review завершён. Лог: $LOG_FILE" | tee -a "$LOG_FILE"
