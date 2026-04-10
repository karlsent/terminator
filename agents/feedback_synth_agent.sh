#!/bin/bash
# Агент «Синтезатор»: собирает фидбеки и обновляет базу опыта агентов.
# Пути берутся из переменной окружения TERMINATOR_CONFIG_SH, которую устанавливает terminator.py.
# Использование: ./agents/feedback_synth_agent.sh ["доп. указания"]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# TERMINATOR_CONFIG_SH устанавливается Python-сервером при запуске агента
if [ -n "$TERMINATOR_CONFIG_SH" ] && [ -f "$TERMINATOR_CONFIG_SH" ]; then
    source "$TERMINATOR_CONFIG_SH"
else
    # Фолбэк: ищем config в стандартном месте
    CONFIG_SH="$HOME/.terminator/task_agent_config.sh"
    if [ -f "$CONFIG_SH" ]; then
        source "$CONFIG_SH"
    fi
fi

EXTRA_DIRECTIVES="$1"

# Пути из конфига (NOTES_DIR, FEEDBACK_FILE, EXPERIENCE_FILE заданы в task_agent_config.sh)
FEEDBACK_JSON="${FEEDBACK_FILE:-$HOME/.terminator/launcher_feedback.json}"
EXPERIENCE_MD="${EXPERIENCE_FILE:-$HOME/.terminator/agent_experience.md}"

mkdir -p "${NOTES_DIR:-$HOME/.terminator/notes}"

TMP_PROMPT="$(mktemp)"
{
  cat "$SCRIPT_DIR/feedback_synth_agent_prompt.md"
  echo ""
  echo "---"
  echo ""
  echo "## Текущий файл базы опыта (\`agent_experience.md\`)"
  echo ""
  echo '```markdown'
  if [ -f "$EXPERIENCE_MD" ]; then cat "$EXPERIENCE_MD"; fi
  echo '```'
  echo ""
  echo "---"
  echo ""
  echo "## Все записи фидбека"
  echo ""
  echo '```json'
  if [ -f "$FEEDBACK_JSON" ]; then cat "$FEEDBACK_JSON"; else echo "{}"; fi
  echo '```'
  if [ -n "$EXTRA_DIRECTIVES" ]; then
    echo ""
    echo "---"
    echo ""
    echo "## Дополнительные указания для этого прогона"
    echo ""
    echo "$EXTRA_DIRECTIVES"
  fi
} > "$TMP_PROMPT"

PROMPT=$(cat "$TMP_PROMPT")
rm -f "$TMP_PROMPT"

# Перейти в git-репозиторий если задан, иначе в домашний каталог
if [ -n "$GIT_REPO" ] && [ -d "$GIT_REPO" ]; then
    cd "$GIT_REPO"
fi

# Proxy env если нужно
if [ -n "$NO_PROXY_DOMAINS" ]; then
    export no_proxy="$NO_PROXY_DOMAINS"
    export NO_PROXY="$NO_PROXY_DOMAINS"
fi

# Запуск Claude
CLAUDE_CMD="${CLAUDE_PROXY_CMD:-claude}"
"$CLAUDE_CMD" --print "$PROMPT" \
    --allowedTools "Read" \
    --output-format stream-json --verbose \
    2>&1
