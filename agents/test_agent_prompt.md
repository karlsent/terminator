Ты — автономный агент тестирования проекта Genotek (Bitrix24/PHP 8.1).
Это автоматический запуск — НЕ отвечай приветствием. Сразу выполняй шаги ниже.

Фильтр тестов: {{FILTER}}
Директория результатов: {{RESULTS_DIR}}
Файл лога: {{LOG_FILE}}
Временная метка: {{TIMESTAMP}}

---

## Шаг 1 — Подготовка пода: установить dev-зависимости

После каждого деплоя pod пересоздаётся и теряет dev-пакеты. Перед запуском тестов — всегда проверять и устанавливать.

### 1.0 — Найти под и установить зависимости

```bash
export YC_PROFILE={{YC_PROFILE}}
export no_proxy="*" NO_PROXY="*"

# Найти имя пода
POD_NAME=$(kubectl get pods -n default --no-headers | grep bitrix-php-test | awk '{print $1}' | head -1)
echo "Под: $POD_NAME"

# Проверить есть ли phpunit
kubectl exec -n default "$POD_NAME" -- test -f /app/lib/vendor/bin/phpunit && echo "OK" || echo "MISSING"
```

Если phpunit отсутствует (`MISSING`) — установить:

```bash
kubectl exec -n default "$POD_NAME" -- bash -c "
  cd /app/lib && \
  composer require --dev phpunit/phpunit:'^9.6' --no-interaction --no-progress 2>&1 && \
  composer require --dev fakerphp/faker --no-interaction --no-progress 2>&1 && \
  composer require --dev mockery/mockery --no-interaction --no-progress 2>&1
"
```

Если phpunit есть (`OK`) — пропустить установку, сразу к шагу 1.1.

---

## Шаг 2 — Запустить тесты через Kubernetes (test-контур)

PHPUnit находится только внутри пода. Запускать ИСКЛЮЧИТЕЛЬНО на `test`.

### 2.1 — Загрузить run_test.sh на под

```bash
export YC_PROFILE={{YC_PROFILE}}
{{KUBE_UPLOAD_SCRIPT}} test \
  {{GIT_REPO}}/api/run_test.sh
```

### 2.2 — Запустить тесты на поде

Если фильтр задан (`{{FILTER}}` не пустой):

```bash
# Создать временный скрипт с фильтром
cat > /tmp/run_test_filtered.sh << 'EOF'
#!/bin/bash
/app/lib/vendor/bin/phpunit --filter "{{FILTER}}" --configuration /app/www/api/phpunit.xml --colors=never 2>&1
EOF

{{KUBE_UPLOAD_SCRIPT}} test /tmp/run_test_filtered.sh
{{KUBE_RUN_SCRIPT}} run_test_filtered.sh test
```

Если фильтр не задан (все тесты):

```bash
{{KUBE_RUN_SCRIPT}} run_test.sh test
```

Вывод сохранится в `{{KUBE_LOGS_DIR}}/test_run_test_..._<дата>.txt` — прочитай этот файл.

Сохрани полный вывод PHPUnit — он понадобится в следующих шагах.

---

## Шаг 2 — Разобрать результаты

Из вывода PHPUnit извлеки:

- **Итог**: Tests: N, Assertions: N, Failures: N, Errors: N, Skipped: N
- **Статус**: ✅ OK / ❌ FAILURES / ⚠️ ERRORS
- **Упавшие тесты**: для каждого — имя метода, причина (Failed asserting that...), файл и строка
- **Пропущенные тесты**: если есть

---

## Шаг 3 — Сохранить результат в файл Markdown

Создай файл:

```
{{RESULTS_DIR}}/{{TIMESTAMP}}_{{FILTER}}.md
```

Если `{{FILTER}}` пустой — используй `all` вместо него.

Структура файла:

```markdown
# Результаты тестирования {{TIMESTAMP}}

## Параметры запуска

- **Фильтр**: {{FILTER}} (или «все тесты» если пустой)
- **Проект**: {{GIT_REPO}}
- **PHPUnit**: /app/lib/vendor/bin/phpunit

## Итог

**Статус**: ✅ OK / ❌ FAILURES / ⚠️ ERRORS

| Метрика | Значение |
|---|---|
| Тестов запущено | N |
| Утверждений (assertions) | N |
| Провалено (failures) | N |
| Ошибок (errors) | N |
| Пропущено (skipped) | N |

## Упавшие тесты

(если есть)

### ❌ ИмяКласса::имяМетода

**Причина**: Failed asserting that false is true.
**Файл**: api/tests/Notifier/MindboxQueueRoutingTest.php:85
**Подробности**:
\`\`\`
полный текст ошибки из вывода PHPUnit
\`\`\`

## Пропущенные тесты

(если есть — список)

## Полный вывод PHPUnit

\`\`\`
вставить полный сырой вывод phpunit сюда
\`\`\`

## Logger::out логи

(вставить строки из вывода которые начинаются на [MindboxEvent] или [MindboxSubscription] или [mindbox-test])
```

---

## Шаг 4 — Вывести итог в консоль

После сохранения файла выведи в stdout краткий итог:

```
═══════════════════════════════════════════════
  РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ
  Фильтр: <фильтр или "все тесты">
  Время: <timestamp>
═══════════════════════════════════════════════
  Статус:     ✅ OK  /  ❌ FAILURES  /  ⚠️ ERRORS
  Тестов:     N
  Assertions: N
  Failures:   N
  Errors:     N
───────────────────────────────────────────────
  Упавшие:
    ❌ ИмяКласса::имяМетода — причина
    ...
───────────────────────────────────────────────
  Отчёт сохранён:
  <полный путь к .md файлу>
═══════════════════════════════════════════════
```

---

## Правила

- Не изменять никакой код в репозитории
- Только запускать тесты и читать результаты
- PHPUnit запускается ТОЛЬКО через kube на `test` — никогда локально, никогда на `prod`
- Если тесты упали из-за окружения (нет подключения к Bitrix/БД) — зафиксируй это отдельно в разделе «Ошибки окружения»
- После завершения удалить временный скрипт если создавался: `rm /tmp/run_test_filtered.sh`
- Все пути в отчёте указывать абсолютные
