#!/bin/bash
# Быстрый запуск прокси-клиента с автоматическим перезапуском

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/v2ray-rsp"
mkdir -p "$STATE_DIR"
RSP_LOG="$STATE_DIR/rsp.log"
RSP_READY_FILE="$STATE_DIR/rsp_ready"
rm -f "$RSP_READY_FILE"

log() {
	printf '%s %s\n' "[$(date '+%Y-%m-%d %H:%M:%S')]" "$*" | tee -a "$RSP_LOG"
}

# Хвост вывода python в журнал (и кратко — размер файла)
log_proxy_client_snapshot() {
	local label=${1:-снимок}
	local n=${2:-80}
	local lines bytes
	bytes=$(wc -c < /tmp/proxy_client.log 2>/dev/null || echo 0)
	lines=$(wc -l < /tmp/proxy_client.log 2>/dev/null || echo 0)
	log "файл /tmp/proxy_client.log: ${bytes:-0} байт, ${lines:-0} строк ($label)"
	{
		echo "[$(date '+%Y-%m-%d %H:%M:%S')] --- /tmp/proxy_client.log ($label, последние $n строк) ---"
		tail -n "$n" /tmp/proxy_client.log 2>/dev/null || echo "(файл недоступен или пуст)"
	} >> "$RSP_LOG"
}

refresh_health() {
	V2RAY_RUNNING=false
	if pgrep -f "v2ray.*run.*-config" > /dev/null; then
		V2RAY_RUNNING=true
	fi
	PORTS_OPEN=false
	if lsof -ti:10808 > /dev/null 2>&1 || lsof -ti:10809 > /dev/null 2>&1; then
		PORTS_OPEN=true
	fi
	PROXY_CLIENT_RUNNING=false
	if kill -0 "$PROXY_PID" 2>/dev/null; then
		PROXY_CLIENT_RUNNING=true
	else
		if [ "$V2RAY_RUNNING" = true ] || [ "$PORTS_OPEN" = true ]; then
			PROXY_CLIENT_RUNNING=true
		fi
	fi
	# Готов к работе приложений: есть слушающие порты или v2ray
	PROXY_READY=false
	if [ "$V2RAY_RUNNING" = true ] || [ "$PORTS_OPEN" = true ]; then
		PROXY_READY=true
	fi
}

log "--- rsp: старт start_proxy.sh (cwd=$SCRIPT_DIR) ---"

# Всегда перезапускаем прокси: сначала останавливаем, потом запускаем
if pgrep -f "proxy_client.py" > /dev/null || pgrep -f "v2ray.*run.*-config" > /dev/null; then
	log "остановка уже запущенного прокси (stop_proxy.sh)..."
	"$SCRIPT_DIR/stop_proxy.sh" > /dev/null 2>&1
	sleep 2
else
	log "работающий прокси не найден — stop_proxy не вызывается"
fi

# Временно очищаем переменные прокси перед запуском,
# чтобы proxy_client.py мог получить конфигурацию напрямую
# (proxy_client.py сам отключает прокси, но на всякий случай очищаем здесь тоже)
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY 2>/dev/null

log "запуск: PYTHONUNBUFFERED=1 python3 -u proxy_client.py $* (→ /tmp/proxy_client.log, без буфера — строки видны сразу)"
# -u + PYTHONUNBUFFERED: иначе при редиректе в файл лог «0 байт», пока python качает конфиг по HTTPS
PYTHONUNBUFFERED=1 python3 -u proxy_client.py "$@" > /tmp/proxy_client.log 2>&1 &
PROXY_PID=$!
log "proxy_client.py PID=$PROXY_PID"

# Первая пауза перед проверками
sleep 3

PYTHON_EXIT=""
if ! kill -0 "$PROXY_PID" 2>/dev/null; then
	wait "$PROXY_PID" 2>/dev/null || true
	PYTHON_EXIT=$?
	log "proxy_client.py уже завершился до проверки, код выхода: ${PYTHON_EXIT:-?}"
fi

# proxy_client сначала тянет подписку по HTTPS (в коде до ~50+ с таймаутов), потом стартует v2ray — ждём дольше.
MAX_ROUNDS=14
ROUND_DELAY=5
# Макс. время с момента старта python до последней проверки: 3 + (MAX_ROUNDS-1)*ROUND_DELAY ≈ 68 с
for round in $(seq 1 "$MAX_ROUNDS"); do
	refresh_health

	log "раунд $round/$MAX_ROUNDS: PROXY_READY=$PROXY_READY V2RAY_RUNNING=$V2RAY_RUNNING PORTS_OPEN=$PORTS_OPEN PROXY_CLIENT_RUNNING=$PROXY_CLIENT_RUNNING"

	if [ "$V2RAY_RUNNING" = true ]; then
		log "v2ray: $(pgrep -af 'v2ray.*run.*-config' 2>/dev/null | head -5)"
	fi
	if [ "$PORTS_OPEN" = true ]; then
		log "порты 10808/10809: $(lsof -i:10808 -i:10809 2>/dev/null | head -15)"
	fi

	log_proxy_client_snapshot "раунд $round" 80

	if [ "$PROXY_READY" = true ]; then
		log "готовность: порты или v2ray подтверждены на раунде $round"
		break
	fi

	if [ "$round" -lt "$MAX_ROUNDS" ]; then
		if [ "$PROXY_CLIENT_RUNNING" = true ] && [ "$PROXY_READY" != true ]; then
			log "ещё не готов: python жив — часто это долгая загрузка конфига (HTTPS), смотри /tmp/proxy_client.log"
		else
			log "ещё не готов (нет v2ray и портов 10808/10809)"
		fi
		log "пауза ${ROUND_DELAY} с перед следующей проверкой (раунд $round/$MAX_ROUNDS)..."
		sleep "$ROUND_DELAY"
		# обновить статус python после ожидания
		if ! kill -0 "$PROXY_PID" 2>/dev/null && [ -z "$PYTHON_EXIT" ]; then
			wait "$PROXY_PID" 2>/dev/null || true
			PYTHON_EXIT=$?
			log "proxy_client.py завершился во время ожидания, код: ${PYTHON_EXIT:-?}"
		fi
	fi
done

refresh_health

if [ "$PROXY_READY" != true ]; then
	log "ОШИБКА: после $MAX_ROUNDS проверок прокси не слушает порты и v2ray не найден — подключаться через прокси нельзя"
	[ -n "$PYTHON_EXIT" ] && log "код выхода python proxy_client: $PYTHON_EXIT"
	log "процессы proxy_client: $(pgrep -af proxy_client.py 2>/dev/null || echo '(нет)')"
	log "процессы v2ray: $(pgrep -af v2ray 2>/dev/null | head -8 || echo '(нет)')"
	log_proxy_client_snapshot "при ошибке" 120

	echo "Ошибка: прокси не запустился (порты 10808/10809 не поднялись)"
	echo "Подробный лог: $RSP_LOG"
	echo "Вывод python: /tmp/proxy_client.log"
	echo "--- последние строки /tmp/proxy_client.log ---"
	tail -25 /tmp/proxy_client.log 2>/dev/null || echo "Логи недоступны"
	exit 1
fi

# proxy_client после открытия портов проверяет реальный выход через прокси и только тогда создаёт rsp_ready
log "ожидание подтверждения от proxy_client (исходящий трафик → файл $RSP_READY_FILE; смотри /tmp/proxy_client.log)"
READY_OK=0
for w in $(seq 1 180); do
	if [ -f "$RSP_READY_FILE" ]; then
		READY_OK=1
		log "proxy_client подтвердил готовность (ожидание ${w} с)"
		break
	fi
	if ! kill -0 "$PROXY_PID" 2>/dev/null; then
		log "proxy_client завершился до готовности — ошибка"
		READY_OK=0
		break
	fi
	if [ "$w" -eq 1 ] || [ $((w % 10)) -eq 0 ]; then
		log "ещё проверяется выход через прокси (~${w} с) — не прерывай, если только поднял VPN; хвост лога: tail -f /tmp/proxy_client.log"
	fi
	sleep 1
done
if [ "$READY_OK" -ne 1 ]; then
	log "ОШИБКА: порты открыты, но прокси не прошёл проверку выхода (или python упал)"
	log_proxy_client_snapshot "при ошибке готовности" 120
	kill "$PROXY_PID" 2>/dev/null || true
	wait "$PROXY_PID" 2>/dev/null || true
	echo "Ошибка: прокси не подтвердил рабочий внешний трафик. См. /tmp/proxy_client.log и повторите rsp."
	echo "Подробный лог: $RSP_LOG"
	tail -30 /tmp/proxy_client.log 2>/dev/null || true
	"$SCRIPT_DIR/stop_proxy.sh" > /dev/null 2>&1
	exit 1
fi

# Устанавливаем переменные окружения для текущей оболочки
export http_proxy="http://127.0.0.1:10809"
export https_proxy="http://127.0.0.1:10809"
export HTTP_PROXY="http://127.0.0.1:10809"
export HTTPS_PROXY="http://127.0.0.1:10809"
export all_proxy="socks5://127.0.0.1:10808"
export ALL_PROXY="socks5://127.0.0.1:10808"

log "OK: прокси стартовал, порты и исходящий трафик проверены proxy_client (переменные окружения выставлены в этой оболочке)"
echo "Прокси стартовал"
echo "(журнал запусков: $RSP_LOG)"
