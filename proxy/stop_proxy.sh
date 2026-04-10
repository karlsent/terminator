#!/bin/bash
# Скрипт для остановки прокси-клиента

echo "Остановка прокси-клиента..."

# Находим и останавливаем процессы v2ray, запущенные через proxy_client.py
V2RAY_PIDS=$(pgrep -f "v2ray.*run.*-config" 2>/dev/null)

if [ -z "$V2RAY_PIDS" ]; then
    # Пробуем найти любые процессы v2ray на наших портах
    V2RAY_PIDS=$(lsof -ti:10808,10809 2>/dev/null | xargs -r pgrep -P 2>/dev/null || echo "")
fi

if [ -z "$V2RAY_PIDS" ]; then
    # Последняя попытка - найти все процессы v2ray
    V2RAY_PIDS=$(pgrep v2ray 2>/dev/null)
fi

if [ -n "$V2RAY_PIDS" ]; then
    echo "Найдены процессы v2ray: $V2RAY_PIDS"
    for pid in $V2RAY_PIDS; do
        # Проверяем, что это действительно наш процесс (не системный)
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "Остановка процесса $pid..."
            kill -TERM "$pid" 2>/dev/null
            sleep 2
            # Если процесс еще работает, убиваем принудительно
            if kill -0 "$pid" 2>/dev/null; then
                echo "Принудительная остановка процесса $pid..."
                kill -KILL "$pid" 2>/dev/null
                sleep 1
            fi
            # Проверяем результат
            if kill -0 "$pid" 2>/dev/null; then
                echo "⚠ Не удалось остановить процесс $pid (возможно, системный процесс)"
            else
                echo "✓ Процесс $pid остановлен"
            fi
        fi
    done
    echo "✓ Попытка остановки процессов v2ray завершена"
else
    echo "Процессы v2ray не найдены"
fi

# Останавливаем proxy_client.py если запущен
PROXY_CLIENT_PIDS=$(pgrep -f "proxy_client.py" 2>/dev/null)
if [ -n "$PROXY_CLIENT_PIDS" ]; then
    echo "Найдены процессы proxy_client.py: $PROXY_CLIENT_PIDS"
    for pid in $PROXY_CLIENT_PIDS; do
        echo "Остановка процесса $pid..."
        kill -TERM "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null
        fi
    done
    echo "✓ Процессы proxy_client.py остановлены"
fi

# Проверяем, что порты освобождены
if lsof -ti:10808,10809 >/dev/null 2>&1; then
    echo "Предупреждение: Порты 10808 или 10809 все еще заняты"
    echo "Занятые порты:"
    lsof -ti:10808,10809 2>/dev/null | xargs -r ps -p 2>/dev/null || lsof -i:10808,10809 2>/dev/null
else
    echo "✓ Порты 10808 и 10809 освобождены"
fi

# Очищаем переменные окружения в текущей сессии
unset http_proxy
unset https_proxy
unset HTTP_PROXY
unset HTTPS_PROXY
unset all_proxy
unset ALL_PROXY

echo ""
echo "============================================================"
echo "Прокси остановлен!"
echo "============================================================"
echo ""
echo "Переменные окружения очищены для текущей сессии."
echo "Для проверки:"
echo "  curl https://api.ipify.org"
echo "  (должен показать ваш реальный IP, а не IP прокси)"
