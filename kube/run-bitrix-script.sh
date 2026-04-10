#!/bin/bash

if [ $# -lt 2 ]; then
    echo "Использование: $0 <имя-скрипта> <среда> [дополнительные параметры...]"
    echo "Примеры:"
    echo "  $0 execute_addmessagehold.php test"
    echo "  $0 execute_addmessagehold.php prod param1 param2"
    exit 1
fi

SCRIPT_NAME="$1"
ENV="$2"
shift 2
SCRIPT_ARGS="$*"

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

# Yandex Cloud и Kubernetes — напрямую, без прокси
export no_proxy="*"
export NO_PROXY="*"
export http_proxy=""
export https_proxy=""

echo "Переключение на кластер: $CLUSTER"

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
    echo "1. Настроить YC CLI: $YC_BIN init"
    if [ -n "$YC_PROFILE" ]; then
        echo "2. Проверить профиль: $YC_BIN config list --profile $YC_PROFILE"
    else
        echo "2. Установить профиль: export YC_PROFILE=<имя-профиля>"
    fi
    exit 1
fi

echo "Поиск pod в namespace: $NAMESPACE"

POD_LIST=$(kubectl get pods -n "$NAMESPACE" | grep "$POD_PATTERN" | grep "Running" | awk '{print $1}')
POD_COUNT=$(echo "$POD_LIST" | grep -c . 2>/dev/null || echo "0")

if [ -z "$POD_LIST" ] || [ "$POD_COUNT" -eq 0 ]; then
    echo "Не найден подходящий pod в namespace '$NAMESPACE' для среды '$ENV'"
    kubectl get pods -n "$NAMESPACE" | grep "$POD_PATTERN" || echo "Нет подходящих pods"
    exit 1
fi

POD_NAME=$(echo "$POD_LIST" | head -n 1)

echo "=========================================="
echo "Найдено активных подов: $POD_COUNT"
echo "Запуск будет выполнен на поде: $POD_NAME"
echo "=========================================="

echo "Поиск файла $SCRIPT_NAME в /app/www/api/scripts и /app/www/api/cron..."
SCRIPT_PATH=$(kubectl exec -n "$NAMESPACE" "$POD_NAME" -- find /app/www/api/scripts /app/www/api/cron -name "$SCRIPT_NAME" -type f 2>/dev/null | head -n 1)

if [ -z "$SCRIPT_PATH" ]; then
    echo "Файл $SCRIPT_NAME не найден в /app/www/api/scripts/ и /app/www/api/cron/"
    echo "Доступные .php файлы в /app/www/api/scripts/:"
    kubectl exec -n "$NAMESPACE" "$POD_NAME" -- find /app/www/api/scripts/ -name "*.php" -type f 2>/dev/null | head -n 10 || echo "Не найдено"
    echo "Доступные .php файлы в /app/www/api/cron/:"
    kubectl exec -n "$NAMESPACE" "$POD_NAME" -- find /app/www/api/cron/ -name "*.php" -type f 2>/dev/null | head -n 10 || echo "Не найдено"
    exit 1
fi

echo "Найден файл: $SCRIPT_PATH"
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")

echo "=========================================="
echo "Запуск скрипта на поде: $POD_NAME"
echo "Скрипт: $SCRIPT_PATH"
echo "Среда: $ENV"
echo "Лог: ${ENV}_${SCRIPT_NAME%.*}_$DATE.txt"
echo "=========================================="
echo ""

if [ -z "$SCRIPT_ARGS" ]; then
    kubectl exec -n "$NAMESPACE" -i "$POD_NAME" -- sh -c "cd $SCRIPT_DIR && php $SCRIPT_NAME" | tee "${ENV}_${SCRIPT_NAME%.*}_$DATE.txt"
else
    kubectl exec -n "$NAMESPACE" -i "$POD_NAME" -- sh -c "cd $SCRIPT_DIR && php $SCRIPT_NAME $SCRIPT_ARGS" | tee "${ENV}_${SCRIPT_NAME%.*}_$DATE.txt"
fi

echo ""
echo "=========================================="
echo "Завершено! Под: $POD_NAME, среда: $ENV"
echo "Лог: ${ENV}_${SCRIPT_NAME%.*}_$DATE.txt"
echo "=========================================="
