#!/bin/bash

if [ $# -lt 2 ]; then
    echo "Использование: $0 <среда> <локальный-файл> [удаленный-путь]"
    echo ""
    echo "Примеры:"
    echo "  $0 test /path/to/Deal.php"
    echo "  $0 prod /path/to/Deal.php /app/www/api/classes/Deal.php"
    echo ""
    echo "Параметры:"
    echo "  <среда>           - test/тест или prod/прот"
    echo "  <локальный-файл>  - полный путь к локальному файлу"
    echo "  [удаленный-путь]  - опциональный путь на удаленном сервере"
    exit 1
fi

ENV="$1"
LOCAL_FILE="$2"
REMOTE_PATH="$3"

if [ ! -f "$LOCAL_FILE" ]; then
    echo "Ошибка: файл '$LOCAL_FILE' не найден"
    exit 1
fi

FILENAME=$(basename "$LOCAL_FILE")

case "$ENV" in
    "test"|"тест")
        CLUSTER="${KUBE_TEST_CLUSTER:-bitrix-testing}"
        NAMESPACE="${KUBE_TEST_NAMESPACE:-default}"
        POD_PATTERN="${KUBE_TEST_POD_PATTERN:-bitrix-php-test}"
        ;;
    "prod"|"прот")
        CLUSTER="${KUBE_PROD_CLUSTER:-bitrix-production}"
        NAMESPACE="${KUBE_PROD_NAMESPACE:-default}"
        POD_PATTERN="${KUBE_PROD_POD_PATTERN:-bitrix-php-prod}"
        ;;
    *)
        echo "Неподдерживаемая среда: $ENV. Используйте 'test', 'prod', 'тест' или 'прот'"
        exit 1
        ;;
esac

DATE=$(date +%d%m%Y_%H%M%S)
LOG_FILE="${ENV}_upload_${FILENAME%.*}_${DATE}.txt"

# Yandex Cloud и Kubernetes — напрямую, без прокси
export no_proxy="*"
export NO_PROXY="*"
export http_proxy=""
export https_proxy=""

echo "Переключение на кластер: $CLUSTER"

# Определяем путь к yc
YC_BIN="${YC_BIN:-yc}"
if ! command -v "$YC_BIN" &> /dev/null; then
    echo "YC CLI не найден. Установите его командой:"
    echo "curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash"
    exit 1
fi

if ! command -v kubectl &> /dev/null; then
    echo "kubectl не установлен. Установите его:"
    echo "  Linux: curl -LO https://dl.k8s.io/release/\$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
    echo "  Mac: brew install kubectl"
    exit 1
fi

YC_PROFILE_FLAG=""
if [ -n "$YC_PROFILE" ]; then
    YC_PROFILE_FLAG="--profile $YC_PROFILE"
fi

$YC_BIN managed-kubernetes cluster get-credentials "$CLUSTER" --external --force $YC_PROFILE_FLAG

if [ $? -ne 0 ]; then
    echo "Ошибка переключения на кластер $CLUSTER"
    echo "Возможно, вам нужно:"
    echo "1. Настроить YC CLI: $YC_BIN init"
    if [ -n "$YC_PROFILE" ]; then
        echo "2. Проверить профиль: $YC_BIN config list --profile $YC_PROFILE"
    else
        echo "2. Проверить конфигурацию: $YC_BIN config list"
        echo "3. Установить профиль: export YC_PROFILE=<имя-профиля>"
    fi
    exit 1
fi

echo "Поиск подов в namespace: $NAMESPACE с шаблоном: $POD_PATTERN"
echo "Локальный файл: $LOCAL_FILE"
echo "Лог сохраняется в: $LOG_FILE"
echo "=========================================="

POD_LIST=$(kubectl get pods -n "$NAMESPACE" | grep "$POD_PATTERN" | grep "Running" | awk '{print $1}')

if [ -z "$POD_LIST" ]; then
    echo "Не найдено активных подов для среды '$ENV'"
    echo "Доступные pods:"
    kubectl get pods -n "$NAMESPACE" | grep "$POD_PATTERN" || echo "Нет подходящих pods"
    exit 1
fi

if [ -z "$REMOTE_PATH" ]; then
    echo "Поиск файла $FILENAME на удаленном сервере..."
    FIRST_POD=$(echo "$POD_LIST" | head -n 1)

    SEARCH_DIRS="/app/www/api/classes /app/www/api/controllers /app/www/api/scripts /app/www/api/cron /app/www/bitrix/modules /app/www/bitrix/components /app/www/bitrix/js /app/www/js"

    for SEARCH_DIR in $SEARCH_DIRS; do
        echo "  Поиск в $SEARCH_DIR..."
        FOUND_PATH=$(timeout 30 kubectl exec -n "$NAMESPACE" "$FIRST_POD" -- find "$SEARCH_DIR" -maxdepth 5 -name "$FILENAME" -type f 2>/dev/null | head -n 1)
        if [ -n "$FOUND_PATH" ]; then
            REMOTE_PATH="$FOUND_PATH"
            echo "Файл найден: $REMOTE_PATH"
            break
        fi
    done

    if [ -z "$REMOTE_PATH" ]; then
        echo "Ошибка: не удалось найти файл $FILENAME на удаленном сервере"
        echo "Укажите удаленный путь вручную третьим параметром"
        exit 1
    fi
fi

echo "Удаленный путь: $REMOTE_PATH"
echo "=========================================="

SUCCESS_COUNT=0
FAIL_COUNT=0
TOTAL_PODS=0

for POD_ID in $POD_LIST; do
    TOTAL_PODS=$((TOTAL_PODS + 1))
    echo "Загружаю на pod: $POD_ID"

    kubectl cp -n "$NAMESPACE" "$LOCAL_FILE" "$POD_ID:$REMOTE_PATH" 2>/dev/null

    if [ $? -eq 0 ]; then
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        echo "  ✓ Успешно загружено на $POD_ID" | tee -a "$LOG_FILE"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "  ✗ Ошибка загрузки на $POD_ID" | tee -a "$LOG_FILE"
    fi
done

echo "=========================================="
echo "Итого:"
echo "  Всего подов: $TOTAL_PODS"
echo "  Успешно: $SUCCESS_COUNT"
echo "  Ошибок: $FAIL_COUNT"
echo "Лог сохранен в: $LOG_FILE"
