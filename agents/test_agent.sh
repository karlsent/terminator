#!/bin/bash
# Запуск агента тестирования PHPUnit
# Использование: ./agents/test_agent.sh [фильтр]

set -e

FILTER=$1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_SH="${TERMINATOR_CONFIG_SH:-$HOME/.terminator/task_agent_config.sh}"
source "$CONFIG_SH"

RESULTS_DIR="${TEST_RESULTS_DIR:-$HOME/Documents/TestResult}"
mkdir -p "$RESULTS_DIR"

TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
LABEL=${FILTER:-all}
LOG_FILE="$RESULTS_DIR/${TIMESTAMP}_${LABEL}.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Запуск агента тестирования — фильтр: ${FILTER:-ВСЕ ТЕСТЫ}" | tee "$LOG_FILE"

export YC_PROFILE="${YC_PROFILE:-default}"
[ -n "$NO_PROXY_DOMAINS" ] && export no_proxy="$NO_PROXY_DOMAINS" && export NO_PROXY="$NO_PROXY_DOMAINS"

PROMPT=$(sed \
    -e "s|{{FILTER}}|${FILTER}|g" \
    -e "s|{{RESULTS_DIR}}|${RESULTS_DIR}|g" \
    -e "s|{{LOG_FILE}}|${LOG_FILE}|g" \
    -e "s|{{TIMESTAMP}}|${TIMESTAMP}|g" \
    -e "s|{{GIT_REPO}}|$GIT_REPO|g" \
    -e "s|{{KUBE_UPLOAD_SCRIPT}}|$KUBE_UPLOAD_SCRIPT|g" \
    -e "s|{{KUBE_RUN_SCRIPT}}|$KUBE_RUN_SCRIPT|g" \
    -e "s|{{YC_PROFILE}}|$YC_PROFILE|g" \
    "$SCRIPT_DIR/test_agent_prompt.md")

cd "$GIT_REPO"

${CLAUDE_PROXY_CMD:-claude} --print "$PROMPT" \
    --allowedTools "Bash,Read,Write,Glob,Grep" \
    --output-format stream-json --verbose \
    2>&1 | tee -a "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Агент завершил работу. Лог: $LOG_FILE" | tee -a "$LOG_FILE"
