#!/bin/bash
# Агент написания PHPUnit тестов
# Использование: ./agents/write_tests_agent.sh <TASK_ID> <BRANCH> ["директивы"]

set -e

TASK_ID=$1
BRANCH=$2
EXTRA_DIRECTIVES=$3

if [ -z "$TASK_ID" ] || [ -z "$BRANCH" ]; then
    echo "Использование: $0 <TASK_ID> <BRANCH> [\"директивы\"]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_SH="${TERMINATOR_CONFIG_SH:-$HOME/.terminator/task_agent_config.sh}"
source "$CONFIG_SH"

NOTES_DIR="${NOTES_DIR:-$HOME/.terminator/notes}"
mkdir -p "$NOTES_DIR/logs"
LOG_FILE="$NOTES_DIR/logs/${TASK_ID}_tests.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск агента тестов — задача #$TASK_ID, ветка: $BRANCH" | tee "$LOG_FILE"

export YC_PROFILE="${YC_PROFILE:-default}"
[ -n "$NO_PROXY_DOMAINS" ] && export no_proxy="$NO_PROXY_DOMAINS" && export NO_PROXY="$NO_PROXY_DOMAINS"

PROMPT=$(sed \
    -e "s/{{TASK_ID}}/$TASK_ID/g" \
    -e "s/{{BRANCH}}/$BRANCH/g" \
    -e "s|BITRIX_REST_PLACEHOLDER|$BITRIX_REST_URL|g" \
    -e "s|{{GIT_REPO}}|$GIT_REPO|g" \
    -e "s|{{KUBE_UPLOAD_SCRIPT}}|$KUBE_UPLOAD_SCRIPT|g" \
    -e "s|{{KUBE_RUN_SCRIPT}}|$KUBE_RUN_SCRIPT|g" \
    -e "s|{{NOTES_DIR}}|$NOTES_DIR|g" \
    -e "s|{{YC_PROFILE}}|$YC_PROFILE|g" \
    "$SCRIPT_DIR/write_tests_agent_prompt.md")

if [ -n "$EXTRA_DIRECTIVES" ]; then
    PROMPT="$PROMPT

---

## ⚡ Дополнительные директивы для этого запуска

$EXTRA_DIRECTIVES"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Директивы: $EXTRA_DIRECTIVES" | tee -a "$LOG_FILE"
fi

cd "$GIT_REPO"

${CLAUDE_PROXY_CMD:-claude} --print "$PROMPT" \
    --allowedTools "Bash,Read,Write,Edit,Glob,Grep,Agent,Skill" \
    --output-format stream-json --verbose \
    2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Агент завершил работу" | tee -a "$LOG_FILE"
