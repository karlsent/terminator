#!/bin/bash
# Запуск Claude CLI, опционально через прокси
# Если прокси не запущен и USE_PROXY=true — запускает его автоматически

USE_PROXY="${USE_PROXY:-false}"
PROXY_HTTP_PORT="${PROXY_HTTP_PORT:-10809}"
PROXY_SOCKS_PORT="${PROXY_SOCKS_PORT:-10808}"
PROXY_SUBSCRIPTION_URL="${PROXY_SUBSCRIPTION_URL:-}"
PROXY_START_SCRIPT="${PROXY_START_SCRIPT:-$HOME/.terminator/proxy/start_proxy.sh}"

# Добавить npm/local bin в PATH
[ -d "$HOME/.npm-global/bin" ] && export PATH="$HOME/.npm-global/bin:$PATH"
[ -d "$HOME/.local/bin" ]      && export PATH="$HOME/.local/bin:$PATH"

# Проверить наличие Claude CLI
if ! command -v claude &> /dev/null; then
    echo "Ошибка: Claude CLI не найден"
    echo "Установите: npm install -g @anthropic-ai/claude-code"
    exit 1
fi

# Без прокси — запуск напрямую
if [ "$USE_PROXY" = "false" ] || [ "$USE_PROXY" = "0" ]; then
    exec claude "$@"
fi

# Проверяем, запущен ли прокси
_proxy_running() {
    pgrep -f "v2ray.*run.*-config" > /dev/null 2>&1 && return 0
    lsof -ti:"$PROXY_SOCKS_PORT" > /dev/null 2>&1 && return 0
    lsof -ti:"$PROXY_HTTP_PORT" > /dev/null 2>&1 && return 0
    return 1
}

if ! _proxy_running; then
    if [ -z "$PROXY_SUBSCRIPTION_URL" ]; then
        echo "⚠ Прокси не запущен. Задайте PROXY_SUBSCRIPTION_URL и перезапустите."
        exit 1
    fi
    echo "Прокси не запущен — запускаю автоматически..."
    EXPORT_PROXY_SUBSCRIPTION_URL="$PROXY_SUBSCRIPTION_URL" \
        "$PROXY_START_SCRIPT" --url "$PROXY_SUBSCRIPTION_URL" &
    # Ждём до 30 сек пока порт откроется
    for i in $(seq 1 30); do
        sleep 1
        if _proxy_running; then
            echo "✓ Прокси запущен (${i}с)"
            break
        fi
    done
    if ! _proxy_running; then
        echo "⚠ Прокси не ответил за 30с. Проверьте ~/.local/state/v2ray-rsp/rsp.log"
        exit 1
    fi
fi

export http_proxy="http://127.0.0.1:$PROXY_HTTP_PORT"
export https_proxy="http://127.0.0.1:$PROXY_HTTP_PORT"
export HTTP_PROXY="http://127.0.0.1:$PROXY_HTTP_PORT"
export HTTPS_PROXY="http://127.0.0.1:$PROXY_HTTP_PORT"
export all_proxy="socks5://127.0.0.1:$PROXY_SOCKS_PORT"
export ALL_PROXY="socks5://127.0.0.1:$PROXY_SOCKS_PORT"

exec claude "$@"
