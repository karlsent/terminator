#!/usr/bin/env python3
"""
Терминатор — веб-интерфейс для запуска и мониторинга Claude-агентов.
Запуск: python3 terminator.py
Открыть: http://terminator.agent  или  http://localhost:8765
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import uuid
from datetime import datetime
from queue import Empty, Queue

from flask import Flask, Response, jsonify, render_template_string, request, send_file

# ─── Пути ─────────────────────────────────────────────────────────────────────

PROGRAM_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR    = os.path.expanduser("~/.terminator")
CONFIG_FILE   = os.path.join(CONFIG_DIR, "config.json")
CONFIG_SH     = os.path.join(CONFIG_DIR, "task_agent_config.sh")
HISTORY_FILE  = os.path.join(CONFIG_DIR, "history.json")
TERMINATOR_IMAGE  = os.path.join(PROGRAM_DIR, "static", "terminator.png")
AGENT007_IMAGE    = os.path.join(PROGRAM_DIR, "static", "agent007.png")
FEEDBACK_FILE     = os.path.join(CONFIG_DIR, "launcher_feedback.json")
EXPERIENCE_FILE   = os.path.join(CONFIG_DIR, "agent_experience.md")

app = Flask(__name__)

# ─── Конфиг ───────────────────────────────────────────────────────────────────

CONFIG_DEFAULTS = {
    # Основное
    "git_repo":               "",
    "scripts_dir":            os.path.join(PROGRAM_DIR, "agents"),
    # Claude CLI
    "claude_cmd":             "claude",
    "use_proxy":              True,
    "proxy_http_port":        10809,
    "proxy_socks_port":       10808,
    "no_proxy_domains":       "",
    "proxy_subscription_url": "",
    "proxy_start_script":     os.path.join(CONFIG_DIR, "proxy", "start_proxy.sh"),
    "proxy_stop_script":      os.path.join(CONFIG_DIR, "proxy", "stop_proxy.sh"),
    # Отчёты и хранилище
    "notes_dir":              os.path.join(CONFIG_DIR, "notes"),
    "test_results_dir":       os.path.expanduser("~/Documents/TestResult"),
    "code_review_dir":        os.path.expanduser("~/Documents/CodeReview"),
    # Bitrix
    "bitrix_rest_url":        "",
    # Kubernetes
    "yc_bin":                 "yc",
    "yc_profile":             "default",
    "kube_test_cluster":      "bitrix-testing",
    "kube_prod_cluster":      "bitrix-production",
    "kube_test_pod_pattern":  "bitrix-php-test",
    "kube_prod_pod_pattern":  "bitrix-php-prod",
    "kube_upload_script":     os.path.join(CONFIG_DIR, "kube", "upload-to-bitrix-pods.sh"),
    "kube_run_script":        os.path.join(CONFIG_DIR, "kube", "run-bitrix-script.sh"),
    "search_dirs":            "/app/www/api/classes /app/www/api/controllers /app/www/api/scripts /app/www/api/cron /app/www/bitrix/modules /app/www/bitrix/components /app/www/bitrix/js /app/www/js",
    # Сервер
    "port":                   8765,
    "domain":                 "terminator.agent",
}

_config_cache = None
_config_lock  = threading.Lock()

def load_config():
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        cfg = dict(CONFIG_DEFAULTS)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    cfg.update(json.load(f))
            except Exception:
                pass
        _config_cache = cfg
        return cfg

def save_config(new_cfg):
    global _config_cache
    os.makedirs(CONFIG_DIR, exist_ok=True)
    merged = dict(CONFIG_DEFAULTS)
    merged.update(new_cfg)
    with _config_lock:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        _config_cache = merged
    _generate_task_agent_config_sh(merged)
    return merged

def _generate_task_agent_config_sh(cfg):
    """Генерирует task_agent_config.sh из config.json — его sourcят все agent .sh скрипты."""
    scripts_dir = cfg.get("scripts_dir", os.path.join(PROGRAM_DIR, "agents"))
    proxy_cmd   = os.path.join(CONFIG_DIR, "claude-proxy.sh")
    kube_logs   = os.path.dirname(cfg.get("kube_upload_script", ""))

    lines = [
        "# АВТОГЕНЕРИРУЕТСЯ из ~/.terminator/config.json — не редактировать вручную",
        f'BITRIX_REST_URL="{cfg.get("bitrix_rest_url", "")}"',
        f'NOTES_DIR="{cfg.get("notes_dir", os.path.join(CONFIG_DIR, "notes"))}"',
        f'FEEDBACK_FILE="{FEEDBACK_FILE}"',
        f'EXPERIENCE_FILE="{EXPERIENCE_FILE}"',
        f'GIT_REPO="{cfg.get("git_repo", "")}"',
        f'TEST_RESULTS_DIR="{cfg.get("test_results_dir", "")}"',
        f'CODE_REVIEW_DIR="{cfg.get("code_review_dir", "")}"',
        f'CLAUDE_PROXY_CMD="{proxy_cmd}"',
        f'USE_PROXY="{str(cfg.get("use_proxy", True)).lower()}"',
        f'PROXY_HTTP_PORT="{cfg.get("proxy_http_port", 10809)}"',
        f'PROXY_SOCKS_PORT="{cfg.get("proxy_socks_port", 10808)}"',
        f'PROXY_SUBSCRIPTION_URL="{cfg.get("proxy_subscription_url", "")}"',
        f'PROXY_START_SCRIPT="{cfg.get("proxy_start_script", "")}"',
        f'PROXY_STOP_SCRIPT="{cfg.get("proxy_stop_script", "")}"',
        f'NO_PROXY_DOMAINS="{cfg.get("no_proxy_domains", "")}"',
        f'YC_PROFILE="{cfg.get("yc_profile", "default")}"',
        f'YC_BIN="{cfg.get("yc_bin", "yc")}"',
        f'KUBE_TEST_CLUSTER="{cfg.get("kube_test_cluster", "bitrix-testing")}"',
        f'KUBE_PROD_CLUSTER="{cfg.get("kube_prod_cluster", "bitrix-production")}"',
        f'KUBE_TEST_POD_PATTERN="{cfg.get("kube_test_pod_pattern", "bitrix-php-test")}"',
        f'KUBE_PROD_POD_PATTERN="{cfg.get("kube_prod_pod_pattern", "bitrix-php-prod")}"',
        f'KUBE_UPLOAD_SCRIPT="{cfg.get("kube_upload_script", "")}"',
        f'KUBE_RUN_SCRIPT="{cfg.get("kube_run_script", "")}"',
        f'KUBE_LOGS_DIR="{kube_logs}"',
        f'SEARCH_DIRS="{cfg.get("search_dirs", "")}"',
    ]
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_SH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Копируем claude-proxy.sh в ~/.terminator/ если ещё нет
    dst = os.path.join(CONFIG_DIR, "claude-proxy.sh")
    src = os.path.join(PROGRAM_DIR, "claude-proxy.sh")
    if not os.path.exists(dst) and os.path.exists(src):
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)

# ─── Агенты ───────────────────────────────────────────────────────────────────

AGENTS = {
    "task": {
        "name": "Решала",
        "icon": "🤖",
        "description": "Загружает задачу из Bitrix24, анализирует кодовую базу, создаёт git-ветку, пишет изменения и делает коммит.",
        "params": [
            {"name": "task_id",   "label": "Номер задачи (или несколько через запятую)",
             "placeholder": "2663187 или 2663187,2663188", "required": True},
            {"name": "directives","label": "Директивы",
             "placeholder": "только анализ, не пиши код",  "required": False},
        ],
        "hints": ["только анализ, не пиши код", "без коммита", "только объясни задачу"],
        "script": "task_agent.sh",
        "editable": [
            {"label": "Промпт", "file": "task_agent_prompt.md"},
            {"label": "Скрипт", "file": "task_agent.sh"},
        ],
    },
    "test": {
        "name": "Шмонщик",
        "icon": "🧪",
        "description": "Запускает PHPUnit тесты на test-контуре Kubernetes. Подключается к поду, запускает тесты, выводит результаты.",
        "params": [
            {"name": "filter", "label": "Фильтр (класс или метод)",
             "placeholder": "MindboxQueueRoutingTest (пусто = все тесты)", "required": False},
        ],
        "hints": ["MindboxQueueRoutingTest", "testSendEventDirectCall", "MindboxTest"],
        "script": "test_agent.sh",
        "editable": [
            {"label": "Промпт", "file": "test_agent_prompt.md"},
            {"label": "Скрипт", "file": "test_agent.sh"},
        ],
    },
    "write_tests": {
        "name": "Писарь",
        "icon": "🧬",
        "description": "По ветке и задаче смотрит что изменилось в коде, пишет PHPUnit тесты, запускает на kube test.",
        "params": [
            {"name": "task_id",   "label": "Номер задачи",   "placeholder": "2663187",              "required": True},
            {"name": "branch",    "label": "Ветка",          "placeholder": "develop_promo_settings","required": True},
            {"name": "directives","label": "Директивы",      "placeholder": "только unit-тесты",     "required": False},
        ],
        "hints": ["только unit-тесты", "без запуска на kube", "только для новых методов"],
        "script": "write_tests_agent.sh",
        "editable": [
            {"label": "Промпт", "file": "write_tests_agent_prompt.md"},
            {"label": "Скрипт", "file": "write_tests_agent.sh"},
        ],
    },
    "code_review": {
        "name": "Авторитет",
        "icon": "🔍",
        "description": "Code review изменений в ветке: корректность, безопасность, N+1, паттерны Bitrix D7. Создаёт структурированный отчёт.",
        "params": [
            {"name": "branch",    "label": "Ветка для ревью","placeholder": "develop_promo_settings","required": True},
            {"name": "task_id",   "label": "Номер задачи (для контекста)","placeholder": "2663187", "required": False},
            {"name": "directives","label": "Директивы",      "placeholder": "акцент на безопасность","required": False},
        ],
        "hints": ["акцент на безопасность", "акцент на производительность", "только критические"],
        "script": "code_review_agent.sh",
        "editable": [
            {"label": "Промпт", "file": "code_review_agent_prompt.md"},
            {"label": "Скрипт", "file": "code_review_agent.sh"},
        ],
    },
    "experience_synth": {
        "name": "Синтезатор",
        "icon": "🎹",
        "description": "Собирает фидбеки из истории, анализирует паттерны успехов и ошибок, обновляет базу опыта агентов.",
        "params": [
            {"name": "directives", "label": "Дополнительные указания",
             "placeholder": "акцент на ошибки последней недели", "required": False},
        ],
        "hints": ["обнови только раздел ошибок", "сфокусируйся на git-паттернах"],
        "script": "feedback_synth_agent.sh",
        "editable": [
            {"label": "Промпт синтеза",  "file": "feedback_synth_agent_prompt.md"},
            {"label": "База опыта",      "file_config_key": "experience_file"},
        ],
    },
}

# Агенты для UI (без синтезатора — он запускается отдельной кнопкой в Истории)
AGENTS_UI = {k: v for k, v in AGENTS.items() if k != "experience_synth"}

# ─── История ──────────────────────────────────────────────────────────────────

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def append_history(entry):
    h = load_history()
    h.insert(0, entry)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(h[:500], f, ensure_ascii=False, indent=2)

# ─── Фидбек ───────────────────────────────────────────────────────────────────

def load_feedback():
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_feedback(run_id, data):
    fb = load_feedback()
    if run_id not in fb:
        fb[run_id] = {}
    fb[run_id].update(data)
    if "processed" not in fb[run_id]:
        fb[run_id]["processed"] = False
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(fb, f, ensure_ascii=False, indent=2)

def count_unprocessed_feedback():
    fb = load_feedback()
    return sum(1 for v in fb.values() if not v.get("processed", False))

# ─── Форматирование stream-json → HTML ────────────────────────────────────────

def _esc(text):
    return str(text).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def _trunc(text, n=120):
    text = str(text).replace("\n", " ").strip()
    return text[:n] + "…" if len(text) > n else text

def _detect_stage(tool_name, tool_input):
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if re.search(r"tasks\.task\.get.*taskId", cmd) and "PARENT_ID" not in cmd:
            return "📋", "Загрузка задачи"
        if "task.commentitem.getlist" in cmd:  return "💬", "Загрузка комментариев"
        if "PARENT_ID" in cmd and "tasks.task.list" in cmd: return "🔽", "Загрузка подзадач"
        if "archiveLink" in cmd or "files.zip" in cmd:      return "📎", "Скачивание архива"
        if "unzip" in cmd:    return "📂", "Распаковка файлов"
        if "disk.file.get" in cmd: return "📎", "Получение файла"
        if "git checkout develop" in cmd: return "🔀", "git checkout develop"
        if "git pull" in cmd:             return "⬇️", "git pull"
        if "git checkout -b" in cmd:
            m = re.search(r"checkout -b (\S+)", cmd)
            return "🌿", f"git branch {m.group(1) if m else ''}"
        if "git checkout" in cmd: return "🌿", "git checkout"
        if "git add" in cmd:      return "📦", "git add"
        if "git commit" in cmd:
            m = re.search(r'-m ["\'](.+?)["\']', cmd)
            msg = _trunc(m.group(1), 60) if m else ""
            return "✅", f"git commit: {msg}"
        if "git push" in cmd:             return "☁️", "git push"
        if "upload-to-bitrix-pods" in cmd: return "☁️", "Kube: загрузка скрипта"
        if "run-bitrix-script" in cmd:    return "⚙️", "Kube: запуск скрипта"
        if "phpunit" in cmd.lower():      return "🧪", "PHPUnit: запуск тестов"
        if "curl" in cmd:  return "🌐", "HTTP запрос"
        if "ls " in cmd or "find " in cmd: return "📁", "Файловая система"
        if "rm " in cmd:   return "🗑️", "Удаление файлов"
        return "💻", f"Bash: {_trunc(cmd.split(chr(10))[0].strip(), 60)}"
    if tool_name == "Read":  return "📖", f"Read: {os.path.basename(tool_input.get('file_path',''))}"
    if tool_name == "Write": return "✏️",  f"Write: {os.path.basename(tool_input.get('file_path',''))}"
    if tool_name == "Edit":  return "🖊️",  f"Edit: {os.path.basename(tool_input.get('file_path',''))}"
    if tool_name == "Glob":  return "🔍", f"Glob: {tool_input.get('pattern','')}"
    if tool_name == "Grep":  return "🔍", f"Grep: {tool_input.get('pattern','')}"
    if tool_name == "Agent": return "🤖", "Субагент"
    if tool_name == "Skill": return "🎯", f"Скилл: /{tool_input.get('skill','')}"
    if tool_name == "TodoWrite": return "📝", "Обновление задач"
    return "🔧", tool_name

def format_event_html(event):
    t = event.get("type")
    parts = []

    if t == "system" and event.get("subtype") == "init":
        model = _esc(event.get("model", "?"))
        ts    = datetime.now().strftime("%H:%M:%S")
        parts.append('<div class="log-sep"></div>')
        parts.append(f'<div class="log-init">🚀 Агент запущен <span class="dim">[{ts}] {model}</span></div>')
        return "".join(parts)

    if t == "assistant":
        for block in event.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text":
                for line in block.get("text","").strip().splitlines():
                    line = line.strip()
                    if not line: continue
                    if line.startswith(("[FATAL]","[FAIL]")):
                        parts.append(f'<div class="log-line log-error">{_esc(line)}</div>')
                    elif line.startswith("[OK]"):
                        parts.append(f'<div class="log-line log-ok">{_esc(line)}</div>')
                    elif line.startswith("[INFO]"):
                        parts.append(f'<div class="log-line log-info">{_esc(line)}</div>')
                    elif line.startswith(("✅","Тип:","Ветка:","Коммит:")):
                        parts.append(f'<div class="log-line log-success">{_esc(line)}</div>')
                    elif line.startswith("⚠️"):
                        parts.append(f'<div class="log-line log-warn">{_esc(line)}</div>')
                    elif line.startswith("#"):
                        parts.append(f'<div class="log-line log-heading">{_esc(line)}</div>')
                    else:
                        parts.append(f'<div class="log-line">{_esc(line)}</div>')
            elif btype == "tool_use":
                tool_name  = block.get("name","")
                tool_input = block.get("input",{})
                icon, label = _detect_stage(tool_name, tool_input)
                detail = ""
                if tool_name == "Bash":
                    cmd   = tool_input.get("command","")
                    lines = [ln.strip() for ln in cmd.strip().splitlines()
                             if ln.strip() and not ln.strip().startswith("export")]
                    detail = _trunc(" && ".join(lines) if lines else cmd.strip(), 100)
                elif tool_name in ("Read","Write","Edit"):
                    detail = tool_input.get("file_path","")
                elif tool_name in ("Grep","Glob"):
                    detail = f"{tool_input.get('pattern','')}  {tool_input.get('path', tool_input.get('glob',''))}".strip()
                detail_html = f'<span class="tool-detail">{_esc(detail)}</span>' if detail else ""
                parts.append(f'<div class="log-tool"><span class="tool-icon">{icon}</span>'
                              f'<span class="tool-label">{_esc(label)}</span>{detail_html}</div>')

    if t == "user":
        for block in event.get("message",{}).get("content",[]):
            if block.get("type") == "tool_result":
                raw = block.get("content","")
                if isinstance(raw, list):
                    raw = " ".join(b.get("text","") for b in raw if b.get("type")=="text")
                preview = _trunc(str(raw).strip(), 100)
                if preview:
                    is_err = any(w in preview.lower() for w in ["error","ошибка","exception","fatal"])
                    cls = "log-result-err" if is_err else "log-result"
                    parts.append(f'<div class="{cls}">↳ {_esc(preview)}</div>')

    if t == "result":
        cost        = event.get("total_cost_usd", 0)
        duration_ms = event.get("duration_ms", 0)
        subtype     = event.get("subtype","")
        status_html = '<span class="log-ok">✅ Завершено успешно</span>' \
                      if subtype == "success" else f'<span class="log-error">❌ {_esc(subtype)}</span>'
        meta = []
        if cost:        meta.append(f"💰 ${cost:.4f}")
        if duration_ms: meta.append(f"⏱ {duration_ms/1000:.1f}с")
        meta_html = f'<span class="dim">{_esc(" · ".join(meta))}</span>' if meta else ""
        parts.append('<div class="log-sep"></div>')
        parts.append(f'<div class="log-result-final">{status_html} {meta_html}</div>')

    return "".join(parts)

# ─── Активные запуски ─────────────────────────────────────────────────────────

runs      = {}
runs_lock = threading.Lock()

def _run_agent_thread(run_id, agent_key, params):
    cfg    = load_config()
    agent  = AGENTS[agent_key]
    scripts_dir = cfg.get("scripts_dir", os.path.join(PROGRAM_DIR, "agents"))
    script_path = os.path.join(scripts_dir, agent["script"])

    cmd = ["bash", script_path]
    if agent_key == "task":
        cmd.append(params.get("task_id",""))
        if params.get("directives","").strip(): cmd.append(params["directives"])
    elif agent_key == "test":
        if params.get("filter","").strip(): cmd.append(params["filter"])
    elif agent_key == "write_tests":
        cmd += [params.get("task_id",""), params.get("branch","")]
        if params.get("directives","").strip(): cmd.append(params["directives"])
    elif agent_key == "code_review":
        cmd.append(params.get("branch",""))
        if params.get("task_id","").strip(): cmd.append(params["task_id"])
        if params.get("directives","").strip(): cmd.append(params["directives"])
    elif agent_key == "experience_synth":
        if params.get("directives","").strip(): cmd.append(params["directives"])

    env = os.environ.copy()
    env["YC_PROFILE"]           = cfg.get("yc_profile","default")
    env["TERMINATOR_CONFIG_SH"] = CONFIG_SH

    with runs_lock:
        run_info = runs[run_id]
        run_info["info"]["started_at"] = datetime.now().isoformat()

    q      = run_info["queue"]
    cost   = 0.0
    duration_ms = 0
    status = "running"

    notes_dir = cfg.get("notes_dir", os.path.join(CONFIG_DIR, "notes"))
    log_dir   = os.path.join(notes_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    session_log_path = os.path.join(log_dir, f"{run_id}.log")

    git_repo = cfg.get("git_repo", "")
    cwd = git_repo if git_repo and os.path.isdir(git_repo) else PROGRAM_DIR

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=cwd, env=env, text=True, bufsize=1, start_new_session=True)
        with runs_lock:
            runs[run_id]["proc"] = proc

        with open(session_log_path, "w", encoding="utf-8") as log_f:
            for raw_line in proc.stdout:
                raw_line = raw_line.rstrip("\n")
                log_f.write(raw_line + "\n")
                log_f.flush()
                if not raw_line: continue
                try:
                    event = json.loads(raw_line)
                    html  = format_event_html(event)
                    if html: q.put({"html": html})
                    if event.get("type") == "result":
                        cost        = event.get("total_cost_usd", 0)
                        duration_ms = event.get("duration_ms", 0)
                        status      = event.get("subtype", "success")
                except (json.JSONDecodeError, ValueError):
                    if raw_line.strip():
                        q.put({"html": f'<div class="log-plain dim">{_esc(raw_line)}</div>'})

        proc.wait()
        if proc.returncode not in (0, -15) and status == "running":
            status = "error"
        elif status == "running":
            status = "success"

    except Exception as e:
        status = "error"
        q.put({"html": f'<div class="log-error">Ошибка запуска: {_esc(str(e))}</div>'})

    finally:
        with runs_lock:
            runs[run_id]["status"] = status

        q.put({"done": True, "status": status, "cost": cost,
               "duration_s": round(duration_ms/1000, 1) if duration_ms else None})

        note_path = None
        _notes_dir = cfg.get("notes_dir", os.path.join(CONFIG_DIR, "notes"))
        if agent_key in ("task","write_tests") and _notes_dir and os.path.isdir(_notes_dir):
            first_id = str(params.get("task_id","")).split(",")[0].strip()
            if first_id:
                for fname in sorted(os.listdir(_notes_dir)):
                    if fname.startswith(first_id) and fname.endswith(".md") and "logs" not in fname:
                        note_path = os.path.join(_notes_dir, fname)
                        break

        entry = {
            "id": run_id, "agent": agent_key, "agent_name": agent["name"],
            "params": params,
            "started_at":  run_info["info"].get("started_at"),
            "finished_at": datetime.now().isoformat(),
            "status":  status,
            "cost_usd": cost,
            "duration_s": round(duration_ms/1000,1) if duration_ms else None,
            "note_path": note_path,
            "note_name": os.path.basename(note_path) if note_path else None,
            "session_log_path": session_log_path if os.path.exists(session_log_path) else None,
        }
        append_history(entry)

# ─── Flask routes ─────────────────────────────────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    if os.path.exists(TERMINATOR_IMAGE):
        return send_file(TERMINATOR_IMAGE, mimetype="image/png")
    return "", 404

@app.route("/icon")
def icon():
    if os.path.exists(TERMINATOR_IMAGE):
        return send_file(TERMINATOR_IMAGE, mimetype="image/png")
    return "", 404

@app.route("/")
def index():
    cfg = load_config()
    agents_for_js = {k: {f: v[f] for f in ("name","icon","description","params","hints","editable")
                         if f in v}
                     for k, v in AGENTS_UI.items()}
    is_configured = bool(cfg.get("git_repo") or cfg.get("bitrix_rest_url"))
    return render_template_string(HTML_TEMPLATE,
                                  agents_json=json.dumps(agents_for_js, ensure_ascii=False),
                                  agents=agents_for_js,
                                  config=cfg,
                                  is_configured=is_configured)

@app.route("/run", methods=["POST"])
def run_endpoint():
    data      = request.json or {}
    agent_key = data.get("agent")
    params    = data.get("params", {})
    if agent_key not in AGENTS:
        return jsonify({"error": "Unknown agent"}), 400
    run_id = str(uuid.uuid4())[:8]
    with runs_lock:
        runs[run_id] = {"proc": None, "queue": Queue(), "status": "starting",
                        "info": {"agent": agent_key, "params": params}}
    t = threading.Thread(target=_run_agent_thread, args=(run_id, agent_key, params), daemon=True)
    t.start()
    return jsonify({"run_id": run_id})

@app.route("/stream/<run_id>")
def stream_endpoint(run_id):
    def generate():
        if run_id not in runs:
            yield 'data: {"error":"not found"}\n\n'; return
        q = runs[run_id]["queue"]
        while True:
            try:
                item = q.get(timeout=20)
                if item.get("done"):
                    yield f"data: {json.dumps({'done':True,'status':item['status'],'cost':item.get('cost'),'duration_s':item.get('duration_s')})}\n\n"
                    break
                elif "html" in item:
                    yield f"data: {json.dumps({'html':item['html']})}\n\n"
            except Empty:
                yield 'data: {"heartbeat":true}\n\n'
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

@app.route("/stop/<run_id>", methods=["POST"])
def stop_endpoint(run_id):
    with runs_lock:
        run = runs.get(run_id)
    if run and run.get("proc"):
        try:    os.killpg(run["proc"].pid, signal.SIGTERM)
        except Exception:
            try:  run["proc"].terminate()
            except Exception: pass
    return jsonify({"ok": True})

@app.route("/icon-007")
def icon_007():
    if os.path.exists(AGENT007_IMAGE):
        return send_file(AGENT007_IMAGE, mimetype="image/png")
    return "", 404

@app.route("/history")
def history_endpoint():
    h  = load_history()
    fb = load_feedback()
    for entry in h:
        run_id = entry.get("id","")
        if run_id in fb:
            entry["rating"]  = fb[run_id].get("rating")
            entry["comment"] = fb[run_id].get("comment","")
    return jsonify(h)

@app.route("/history-log/<run_id>")
def history_log_endpoint(run_id):
    entry = next((e for e in load_history() if e.get("id") == run_id), None)
    if not entry: return jsonify({"error":"not found"}), 404
    log_path = entry.get("session_log_path")
    if not log_path or not os.path.exists(log_path): return jsonify({"error":"log not found"}), 404
    parts = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line: continue
            try:
                html = format_event_html(json.loads(line))
                if html: parts.append(html)
            except (json.JSONDecodeError, ValueError):
                if line.strip(): parts.append(f'<div class="log-plain dim">{_esc(line)}</div>')
    return jsonify({"html": "".join(parts)})

@app.route("/note")
def get_note():
    path = request.args.get("path","")
    cfg  = load_config()
    notes_dir = cfg.get("notes_dir", os.path.join(CONFIG_DIR, "notes"))
    # Проверяем что файл находится внутри notes_dir
    norm_path     = os.path.realpath(path)
    norm_notes_dir = os.path.realpath(notes_dir)
    if not norm_path.startswith(norm_notes_dir):
        return jsonify({"error": "forbidden"}), 403
    if not os.path.exists(norm_path):
        return jsonify({"error": "not found"}), 404
    with open(norm_path, encoding="utf-8") as f:
        return jsonify({"text": f.read(), "name": os.path.basename(norm_path)})

@app.route("/feedback", methods=["POST"])
def feedback_endpoint():
    data   = request.json or {}
    run_id = data.get("run_id","")
    if not run_id:
        return jsonify({"error": "run_id required"}), 400
    save_feedback(run_id, {k: v for k, v in data.items() if k != "run_id"})
    return jsonify({"ok": True})

@app.route("/feedback/status")
def feedback_status():
    return jsonify({"unprocessed": count_unprocessed_feedback()})

@app.route("/open-experience", methods=["POST"])
def open_experience():
    if not os.path.exists(EXPERIENCE_FILE):
        return jsonify({"error": "not found"}), 404
    with open(EXPERIENCE_FILE, encoding="utf-8") as f:
        return jsonify({"text": f.read()})

@app.route("/history-result/<run_id>")
def history_result(run_id):
    entry = next((e for e in load_history() if e.get("id") == run_id), None)
    if not entry: return jsonify({"error":"not found"}), 404
    log_path = entry.get("session_log_path")
    if not log_path or not os.path.exists(log_path):
        return jsonify({"error":"log not found"}), 404
    # Собираем весь текстовый вывод агента из лога
    result_lines = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line: continue
            try:
                event = json.loads(line)
                if event.get("type") == "assistant":
                    for block in event.get("message",{}).get("content",[]):
                        if block.get("type") == "text":
                            result_lines.append(block.get("text",""))
            except (json.JSONDecodeError, ValueError):
                pass
    return jsonify({"text": "\n\n".join(result_lines)})

@app.route("/proxy/status")
def proxy_status():
    cfg      = load_config()
    use_proxy = cfg.get("use_proxy", True)
    if not use_proxy:
        return jsonify({"running": True, "process": False, "port": False, "disabled": True})
    port  = cfg.get("proxy_http_port", 10809)
    sport = cfg.get("proxy_socks_port", 10808)
    proc_ok = subprocess.run(["pgrep", "-f", "v2ray.*run.*-config"], capture_output=True).returncode == 0
    port_ok = False
    try:
        s = socket.socket(); s.settimeout(0.5); s.connect(("127.0.0.1", port)); s.close(); port_ok = True
    except Exception: pass
    return jsonify({"running": proc_ok and port_ok, "process": proc_ok, "port": port_ok, "disabled": False})

@app.route("/proxy/start", methods=["POST"])
def proxy_start():
    cfg = load_config()
    script = cfg.get("proxy_start_script","")
    url    = cfg.get("proxy_subscription_url","")
    if not script or not os.path.exists(script):
        return jsonify({"error": "proxy_start_script не найден: " + script}), 400
    env = os.environ.copy()
    if url: env["PROXY_SUBSCRIPTION_URL"] = url
    subprocess.Popen(["bash", script, "--url", url] if url else ["bash", script],
                     env=env, start_new_session=True)
    return jsonify({"ok": True, "message": "Прокси запускается..."})

@app.route("/proxy/stop", methods=["POST"])
def proxy_stop():
    cfg    = load_config()
    script = cfg.get("proxy_stop_script","")
    if script and os.path.exists(script):
        subprocess.Popen(["bash", script], start_new_session=True)
    else:
        subprocess.run(["pkill", "-f", "v2ray.*run.*-config"], capture_output=True)
        subprocess.run(["pkill", "-f", "proxy_client.py"],     capture_output=True)
    return jsonify({"ok": True})

@app.route("/settings", methods=["GET"])
def get_settings():
    return jsonify(load_config())

@app.route("/settings", methods=["POST"])
def post_settings():
    data = request.json or {}
    # Приводим типы
    for key in ("proxy_http_port","proxy_socks_port","port"):
        if key in data:
            try: data[key] = int(data[key])
            except (ValueError, TypeError): pass
    if "use_proxy" in data:
        data["use_proxy"] = str(data["use_proxy"]).lower() in ("true","1","yes","on")
    cfg = save_config(data)
    return jsonify({"ok": True, "config": cfg})

@app.route("/agent/<name>/file")
def get_agent_file(name):
    cfg   = load_config()
    sdir  = cfg.get("scripts_dir", os.path.join(PROGRAM_DIR, "agents"))
    fname = request.args.get("f","")
    if ".." in fname:
        return jsonify({"error":"forbidden"}), 403
    path = os.path.normpath(os.path.join(sdir, fname))
    # Для experience_synth: agent_experience.md хранится в CONFIG_DIR
    if not os.path.exists(path) and name == "experience_synth" and fname == "agent_experience.md":
        path = EXPERIENCE_FILE
    if not path.startswith(sdir) and path != EXPERIENCE_FILE:
        return jsonify({"error":"forbidden"}), 403
    if not os.path.exists(path):
        return jsonify({"content": "", "file": fname})
    with open(path, encoding="utf-8") as f:
        return jsonify({"content": f.read(), "file": fname})

@app.route("/agent/<name>/file", methods=["POST"])
def save_agent_file(name):
    cfg     = load_config()
    sdir    = cfg.get("scripts_dir", os.path.join(PROGRAM_DIR, "agents"))
    data    = request.json or {}
    fname   = data.get("f","")
    content = data.get("content","")
    if ".." in fname:
        return jsonify({"error":"forbidden"}), 403
    path = os.path.normpath(os.path.join(sdir, fname))
    # Для experience_synth: agent_experience.md сохраняем в CONFIG_DIR
    if name == "experience_synth" and fname == "agent_experience.md":
        path = EXPERIENCE_FILE
    if not path.startswith(sdir) and path != EXPERIENCE_FILE:
        return jsonify({"error":"forbidden"}), 403
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return jsonify({"ok": True})

# ─── HTML шаблон ──────────────────────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Терминатор</title>
<link rel="icon" type="image/png" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@400;600&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&display=swap">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.1/github-markdown-dark.min.css" crossorigin>
<script>document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || '007');</script>
<style>
:root, [data-theme="007"] {
  --bg:#08080b; --bg2:#0e0e14; --bg3:#16161e;
  --border:#242430; --border-hi:rgba(201,168,76,0.35);
  --text:#f0ede5; --text2:#68645c;
  --accent:#c9a84c; --accent2:#e8cb79; --accent-glow:rgba(201,168,76,0.22);
  --green:#3d9970; --red:#a83232; --yellow:#c9a84c; --cyan:#d4af37;
  --font-head:'Cormorant Garamond',Georgia,serif;
  --font-body:'Inter',system-ui,sans-serif;
  --font-mono:'JetBrains Mono','Fira Code',monospace;
}
[data-theme="terminator"] {
  --bg:#080808; --bg2:#0f0f0f; --bg3:#151515;
  --border:#280000; --border-hi:rgba(204,0,0,0.5);
  --text:#e8e0e0; --text2:#604040;
  --accent:#cc2200; --accent2:#ff4422; --accent-glow:rgba(204,34,0,0.22);
  --green:#00bb44; --red:#cc2200; --yellow:#ff6600; --cyan:#00ffcc;
  --font-head:'Inter',system-ui,sans-serif;
  --font-body:'Inter',system-ui,sans-serif;
  --font-mono:'JetBrains Mono','Fira Code',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font-body);font-size:14px;min-height:100vh}
[data-theme="terminator"] body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:9000;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.07) 2px,rgba(0,0,0,0.07) 4px)}
/* Header */
header{display:flex;align-items:center;justify-content:space-between;padding:0 24px;height:58px;border-bottom:1px solid var(--border);background:var(--bg2);position:sticky;top:0;z-index:100}
[data-theme="007"] header{border-bottom-color:transparent;box-shadow:0 1px 0 var(--border-hi)}
[data-theme="terminator"] header{border-bottom-color:var(--border-hi);box-shadow:0 2px 24px rgba(204,34,0,0.08)}
.header-left{display:flex;align-items:center;gap:12px}
.header-right{display:flex;align-items:center;gap:12px}
.header-title{font-family:var(--font-head);font-size:20px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase}
[data-theme="007"] .header-title{background:linear-gradient(135deg,#f0ede5 30%,var(--accent));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
[data-theme="terminator"] .header-title{color:var(--accent);text-shadow:0 0 24px rgba(204,34,0,0.55);font-size:16px;letter-spacing:0.12em}
/* Theme switcher */
.theme-switcher{display:flex;align-items:center;gap:2px;background:var(--bg3);border:1px solid var(--border);border-radius:20px;padding:3px}
.theme-btn{padding:4px 13px;border-radius:16px;border:none;background:none;color:var(--text2);cursor:pointer;font-size:11px;font-weight:700;letter-spacing:0.07em;font-family:var(--font-mono);transition:all 0.2s}
.theme-btn:hover{color:var(--text)}
.theme-btn.active{background:var(--accent);color:#000}
/* Proxy badge */
.proxy-badge{display:flex;align-items:center;gap:6px;font-size:11px;font-family:var(--font-mono);color:var(--text2);cursor:pointer;padding:5px 14px;border:1px solid var(--border);border-radius:20px;transition:all 0.2s}
.proxy-badge:hover{border-color:var(--accent);color:var(--text)}
.proxy-dot{width:6px;height:6px;border-radius:50%;background:var(--text2);transition:background 0.3s}
.proxy-dot.ok{background:var(--green);box-shadow:0 0 6px var(--green)}
.proxy-dot.fail{background:var(--red)}
.proxy-dot.disabled{background:var(--text2)}
/* Nav */
nav{display:flex;border-bottom:1px solid var(--border);background:var(--bg2);padding:0 24px}
.tab-btn{padding:14px 20px;background:none;border:none;color:var(--text2);cursor:pointer;font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;border-bottom:2px solid transparent;transition:color 0.2s,border-color 0.2s;font-family:var(--font-body)}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
/* Tabs */
.tab{display:none;padding:28px 24px;max-width:960px;margin:0 auto}
.tab.tab-full{max-width:none;width:100%;margin:0;padding:28px 24px 40px}
.tab.active{display:block}
/* Agent selector */
.agent-selector{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:28px}
.agent-btn{padding:16px 20px;border:1px solid var(--border);background:var(--bg2);color:var(--text2);cursor:pointer;text-align:left;transition:all 0.2s;position:relative;overflow:hidden}
[data-theme="007"] .agent-btn{border-radius:0;clip-path:polygon(14px 0%,100% 0%,calc(100% - 14px) 100%,0% 100%)}
[data-theme="007"] .agent-btn::after{content:'';position:absolute;top:0;left:0;right:0;height:1px;background:linear-gradient(90deg,transparent 5%,var(--accent) 50%,transparent 95%);opacity:0;transition:opacity 0.3s}
[data-theme="007"] .agent-btn:hover::after,[data-theme="007"] .agent-btn.active::after{opacity:1}
[data-theme="terminator"] .agent-btn{border-radius:0}
[data-theme="terminator"] .agent-btn.active{box-shadow:inset 0 0 30px rgba(204,34,0,0.06),0 0 12px rgba(204,34,0,0.08)}
.agent-btn:hover{border-color:var(--accent);color:var(--text)}
.agent-btn.active{border-color:var(--accent);background:rgba(255,255,255,0.025);color:var(--text)}
.agent-btn .aname{font-weight:600;font-size:14px;margin-bottom:5px;color:var(--text)}
.agent-btn.active .aname{color:var(--accent)}
[data-theme="terminator"] .agent-btn.active .aname{text-shadow:0 0 12px var(--accent-glow)}
.agent-btn .adesc{font-size:12px;color:var(--text2);line-height:1.55}
/* Forms */
.form-group{margin-bottom:16px}
label{display:block;margin-bottom:6px;color:var(--text2);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em}
[data-theme="007"] label{color:var(--accent);opacity:0.6}
[data-theme="terminator"] label{color:var(--accent);opacity:0.5}
input[type=text],input[type=number]{width:100%;padding:9px 14px;background:var(--bg2);border:1px solid var(--border);border-radius:0;color:var(--text);font-size:14px;font-family:var(--font-body);outline:none;transition:border-color 0.2s,box-shadow 0.2s}
input[type=text]:focus,input[type=number]:focus{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent-glow)}
.checkbox-row{display:flex;align-items:center;gap:8px;padding:8px 0}
.checkbox-row input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}
select{width:100%;padding:9px 14px;background:var(--bg2);border:1px solid var(--border);border-radius:0;color:var(--text);font-size:14px;font-family:var(--font-body);outline:none;transition:border-color 0.2s;cursor:pointer}
select:focus{border-color:var(--accent)}
/* Chips */
.hints-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.hint-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:var(--text2);margin-bottom:6px}
.chip{padding:3px 14px;background:var(--bg3);border:1px solid var(--border);color:var(--text2);cursor:pointer;font-size:11px;transition:all 0.15s;white-space:nowrap;border-radius:0}
[data-theme="007"] .chip{clip-path:polygon(6px 0%,100% 0%,calc(100% - 6px) 100%,0% 100%)}
.chip:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-glow)}
/* Buttons */
.btn{padding:9px 20px;border:none;cursor:pointer;font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;font-family:var(--font-body);transition:all 0.2s;border-radius:0}
.btn:hover{opacity:0.85}
.btn:disabled{opacity:0.3;cursor:not-allowed}
[data-theme="007"] .btn-primary{background:linear-gradient(135deg,#b8962e,var(--accent2));color:#0a0a0a;box-shadow:0 2px 14px rgba(201,168,76,0.28)}
[data-theme="007"] .btn-primary:hover:not(:disabled){box-shadow:0 4px 22px rgba(201,168,76,0.45);transform:translateY(-1px)}
[data-theme="terminator"] .btn-primary{background:transparent;border:1px solid var(--accent);color:var(--accent)}
[data-theme="terminator"] .btn-primary:hover:not(:disabled){background:var(--accent);color:#000;box-shadow:0 0 16px rgba(204,34,0,0.4)}
[data-theme="007"] .btn-danger{background:transparent;border:1px solid var(--red);color:var(--red)}
[data-theme="007"] .btn-danger:hover:not(:disabled){background:var(--red);color:#fff}
[data-theme="terminator"] .btn-danger{background:transparent;border:1px solid var(--red);color:var(--red)}
[data-theme="terminator"] .btn-danger:hover:not(:disabled){background:var(--red);color:#000;box-shadow:0 0 14px rgba(204,34,0,0.4)}
.btn-ghost{background:var(--bg3);color:var(--text);border:1px solid var(--border)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-sm{padding:4px 12px;font-size:11px}
.launch-row{display:flex;align-items:center;gap:12px;margin-top:8px}
#run-status{font-size:13px}
/* Log */
#activity-panel{margin-top:28px}
.activity-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em;color:var(--text2);margin-bottom:10px}
[data-theme="007"] .activity-title{color:var(--accent);opacity:0.55}
[data-theme="terminator"] .activity-title{color:var(--accent);opacity:0.6}
#log{background:var(--bg2);border:1px solid var(--border);border-left:2px solid var(--border-hi);padding:16px 18px;height:520px;overflow-y:auto;font-family:var(--font-mono);font-size:12px;line-height:1.65}
[data-theme="terminator"] #log{box-shadow:inset 0 0 40px rgba(0,0,0,0.4)}
#log::-webkit-scrollbar{width:4px}
#log::-webkit-scrollbar-track{background:transparent}
#log::-webkit-scrollbar-thumb{background:var(--border)}
.log-sep{border-top:1px solid var(--border);margin:10px 0}
.log-init{color:var(--accent);font-weight:600;padding:2px 0}
.log-plain{color:var(--text2)}
.log-line{color:var(--text);padding:1px 0}
.log-ok{color:var(--green)}
.log-info{color:var(--cyan)}
.log-error{color:var(--red)}
.log-warn{color:var(--yellow)}
.log-success{color:var(--green);font-weight:600}
[data-theme="terminator"] .log-ok,[data-theme="terminator"] .log-success{color:#b84444}
.log-heading{color:var(--text);font-weight:700;margin-top:4px}
.log-result{color:var(--text2);font-size:11px;padding-left:12px}
.log-result-err{color:var(--red);font-size:11px;padding-left:12px}
.log-result-final{padding:4px 0;font-weight:600}
.log-tool{display:flex;align-items:baseline;gap:7px;padding:3px 0;margin-top:1px}
.tool-icon{font-size:13px}
.tool-label{color:var(--accent);font-weight:600;font-size:12px;flex-shrink:0}
.tool-detail{color:var(--text2);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:620px}
.dim{color:var(--text2);font-weight:normal;font-size:11px}
/* History toolbar */
.history-toolbar{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:20px}
.history-toolbar h2{font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;margin:0;color:var(--text2)}
[data-theme="007"] .history-toolbar h2{color:var(--accent);opacity:0.55}
[data-theme="terminator"] .history-toolbar h2{color:var(--accent);opacity:0.5}
.history-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.btn-experience{padding:10px 18px;border:none;cursor:pointer;font-size:11px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;transition:transform 0.15s,box-shadow 0.15s;display:inline-flex;align-items:center;gap:10px;font-family:var(--font-body)}
[data-theme="007"] .btn-experience{border-radius:0;background:linear-gradient(135deg,#2d6a4a 0%,#8a6828 100%);color:#f0ede5;box-shadow:0 2px 14px rgba(201,168,76,0.15)}
[data-theme="007"] .btn-experience:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 4px 20px rgba(201,168,76,0.25)}
[data-theme="terminator"] .btn-experience{border-radius:0;background:transparent;border:1px solid var(--accent);color:var(--accent)}
[data-theme="terminator"] .btn-experience:hover:not(:disabled){background:var(--accent);color:#000;box-shadow:0 0 16px rgba(204,34,0,0.4);transform:translateY(-1px)}
.btn-experience:disabled{opacity:0.4;cursor:not-allowed;transform:none}
.btn-experience--urgent{animation:btnUrgent 2s ease-in-out infinite}
@keyframes btnUrgent{0%,100%{filter:brightness(1)}50%{filter:brightness(1.12)}}
.feedback-badge{display:none;min-width:20px;height:20px;padding:0 6px;border-radius:10px;background:var(--red);color:#fff;font-size:11px;font-weight:800;align-items:center;justify-content:center;line-height:20px;box-shadow:0 0 10px var(--accent-glow)}
.feedback-badge.show{display:inline-flex}
.history-directives-input{flex:1;min-width:200px;max-width:520px;padding:8px 14px;background:var(--bg2);border:1px solid var(--border);border-radius:0;color:var(--text);font-size:13px;font-family:var(--font-body);outline:none;transition:border-color 0.2s}
.history-directives-input:focus{border-color:var(--accent)}
.history-colleague-files{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:16px;font-size:12px;color:var(--text2)}
.history-filter-bar{display:none;flex-wrap:wrap;align-items:flex-end;gap:14px 20px;margin-bottom:18px;padding:14px 18px;background:var(--bg2);border:1px solid var(--border);border-left:2px solid var(--border-hi)}
.history-filter-bar.visible{display:flex}
.history-filter-bar .filter-group{display:flex;flex-direction:column;gap:5px}
.history-filter-bar label{font-size:10px}
.history-filter-bar input[type=text],.history-filter-bar select{min-width:220px}
.history-filter-meta{font-size:12px;color:var(--text2);align-self:center;margin-left:auto}
#save-row-experience{margin-top:10px;align-items:center;gap:12px}
.btn-link-file{padding:7px 14px;border:1px solid var(--border);border-radius:0;background:var(--bg3);color:var(--accent);font-size:12px;cursor:pointer;font-family:var(--font-body);transition:all 0.15s}
.btn-link-file:hover{border-color:var(--accent);color:var(--text)}
#history-content{overflow-x:auto;width:100%}
#history-wrap{width:100%}
#history-content table{min-width:1100px}
table{width:100%;border-collapse:collapse}
/* Звёзды */
.star-row{display:inline-flex;gap:2px;align-items:center;vertical-align:middle}
.star-btn{background:none;border:none;cursor:pointer;font-size:14px;line-height:1;padding:2px;opacity:0.22;color:var(--text2);transition:opacity 0.15s,transform 0.1s}
.star-btn:hover{opacity:0.8;transform:scale(1.15)}
.star-btn.on{opacity:1;color:var(--accent);text-shadow:0 0 8px var(--accent-glow)}
.star-btn.dim{opacity:0.12}
.btn-feedback{padding:4px 10px;border:1px solid var(--border);border-radius:0;background:var(--bg3);color:var(--accent);font-size:11px;font-weight:600;letter-spacing:0.04em;cursor:pointer;white-space:nowrap;transition:all 0.15s;font-family:var(--font-body)}
.btn-feedback:hover{border-color:var(--accent);background:var(--accent-glow)}
.btn-feedback.has-text{border-color:var(--green);color:var(--green)}
/* Modal */
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.82);z-index:200;align-items:center;justify-content:center;padding:20px;backdrop-filter:blur(6px)}
.modal-overlay.show{display:flex}
.modal-box{width:100%;max-width:520px;background:var(--bg2);border:1px solid var(--border);border-top:2px solid var(--accent);padding:26px 28px;box-shadow:0 28px 70px rgba(0,0,0,0.65);border-radius:0}
.modal-box h3{margin-bottom:7px;color:var(--text);font-family:var(--font-head);font-size:20px}
[data-theme="terminator"] .modal-box h3{font-size:15px;letter-spacing:0.05em}
.modal-box .modal-sub{font-size:12px;color:var(--text2);margin-bottom:14px;line-height:1.5}
#feedback-ta{width:100%;min-height:160px;background:var(--bg);border:1px solid var(--border);border-radius:0;color:var(--text);padding:12px 14px;font-size:13px;font-family:var(--font-body);line-height:1.55;resize:vertical;outline:none;transition:border-color 0.2s}
#feedback-ta:focus{border-color:var(--accent)}
.modal-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:16px}
#history-synth-panel{display:none;margin-top:16px;background:var(--bg2);border:1px solid var(--border);border-left:2px solid var(--border-hi);padding:14px 16px}
#history-synth-panel.active{display:block}
#log-history-synth{max-height:280px;overflow-y:auto;font-family:var(--font-mono);font-size:11px;line-height:1.55;margin-top:10px;padding:10px;background:var(--bg);border:1px solid var(--border)}
/* Table */
th{text-align:left;padding:10px 14px;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;font-weight:700;border-bottom:1px solid var(--border);white-space:nowrap;color:var(--text2)}
[data-theme="007"] th{color:var(--accent);opacity:0.55;border-bottom-color:var(--border-hi)}
[data-theme="terminator"] th{color:var(--accent);border-bottom-color:var(--border-hi)}
td{padding:10px 14px;border-bottom:1px solid var(--border);font-size:13px;vertical-align:top}
.hist-row{cursor:default}
[data-theme="007"] .hist-row:hover td{background:rgba(201,168,76,0.025)}
[data-theme="terminator"] .hist-row:hover td{background:rgba(204,34,0,0.025)}
.log-expand-row td{padding:0!important;background:var(--bg)!important;border-bottom:1px solid var(--accent)}
.log-expand-box{background:var(--bg);border-left:2px solid var(--accent);font-family:var(--font-mono);font-size:11px;max-height:420px;overflow-y:auto;padding:12px 18px;line-height:1.6}
.log-expand-box::-webkit-scrollbar{width:4px}
.log-expand-box::-webkit-scrollbar-thumb{background:var(--border)}
.note-toolbar{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.note-pre{margin:0;white-space:pre-wrap;word-break:break-word;font-family:var(--font-mono);font-size:11px;line-height:1.55;color:var(--text)}
.note-md.markdown-body{box-sizing:border-box;min-width:200px;max-width:100%;padding:18px 22px!important;background:var(--bg)!important;color:var(--text)!important;border:1px solid var(--border);border-left:2px solid var(--border-hi);font-size:14px;line-height:1.62}
.note-md.markdown-body h1,.note-md.markdown-body h2,.note-md.markdown-body h3{border-color:var(--border)}
.note-md.markdown-body pre,.note-md.markdown-body code{background:var(--bg3)!important;color:var(--text)!important;border:1px solid var(--border)}
.note-md.markdown-body a{color:var(--accent)!important}
.note-md.markdown-body blockquote{border-left-color:var(--accent);color:var(--text2)}
.log-toggle{background:none;border:1px solid var(--border);color:var(--accent);cursor:pointer;font-size:11px;font-family:var(--font-mono);padding:2px 8px;transition:all 0.15s;white-space:nowrap}
.log-toggle:hover{border-color:var(--accent);background:var(--accent-glow)}
.st-success{color:var(--green)}
.st-error{color:var(--red)}
[data-theme="terminator"] .st-success{color:#b84444}
.st-running{color:var(--yellow)}
.st-stopped{color:var(--text2)}
.params-cell{color:var(--text2);font-size:12px;max-width:280px}
.obs-link{background:none;border:none;color:var(--cyan);cursor:pointer;font-size:12px;text-align:left;padding:0;text-decoration:underline;font-family:var(--font-body)}
.obs-link:hover{color:var(--accent)}
/* Editor */
.editor-header{display:flex;align-items:center;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.file-btn{padding:6px 14px;border:1px solid var(--border);border-radius:0;background:var(--bg2);color:var(--text2);cursor:pointer;font-size:12px;font-weight:500;font-family:var(--font-body);transition:all 0.15s}
.file-btn:hover{border-color:var(--accent);color:var(--text)}
.file-btn.active{border-color:var(--accent);color:var(--accent);background:var(--accent-glow)}
.file-label{color:var(--text2);font-size:12px;margin-left:auto}
#editor-ta{width:100%;height:620px;background:var(--bg2);border:1px solid var(--border);border-left:2px solid var(--border-hi);color:var(--text);padding:16px 18px;font-family:var(--font-mono);font-size:12px;line-height:1.6;resize:vertical;outline:none;transition:border-color 0.2s}
#editor-ta:focus{border-color:var(--accent)}
.save-ok{font-size:12px;color:var(--green);opacity:0;transition:opacity 0.3s;margin-left:6px}
.save-ok.show{opacity:1}
/* Settings */
.settings-section{margin-bottom:28px}
.settings-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:var(--accent);margin-bottom:14px;padding-bottom:6px;border-bottom:1px solid var(--border)}
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.settings-grid.single{grid-template-columns:1fr}
.settings-note{font-size:11px;color:var(--yellow);margin-top:8px}
.proxy-controls{display:flex;gap:8px;margin-top:12px}
/* Misc */
.empty{color:var(--text2);text-align:center;padding:60px 20px;font-size:14px;letter-spacing:0.05em}
.spinner{width:12px;height:12px;border:1.5px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin 0.75s linear infinite;display:inline-block;vertical-align:middle;margin-right:5px}
@keyframes spin{to{transform:rotate(360deg)}}
/* Onboarding */
.onboard-box{background:var(--bg2);border:1px solid var(--accent);border-radius:0;padding:32px;max-width:560px;margin:40px auto;text-align:center}
.onboard-box h2{font-size:20px;margin-bottom:12px;font-family:var(--font-head)}
.onboard-box p{color:var(--text2);font-size:14px;line-height:1.6;margin-bottom:20px}
</style>
</head>
<body>

<header>
  <div class="header-left">
    <img id="header-logo" src="/icon" style="width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid var(--accent)">
    <span id="header-title-text" class="header-title">Терминатор</span>
  </div>
  <div class="header-right">
    <div class="theme-switcher">
      <button class="theme-btn" id="theme-007-btn" onclick="setTheme('007')">007</button>
      <button class="theme-btn" id="theme-term-btn" onclick="setTheme('terminator')">T-800</button>
    </div>
    <div class="proxy-badge" onclick="checkProxy()" title="Нажми для проверки">
      <div class="proxy-dot" id="proxy-dot"></div>
      <span id="proxy-label">прокси</span>
    </div>
  </div>
</header>

<nav>
  <button class="tab-btn active" onclick="showTab('launch',this)">Запуск</button>
  <button class="tab-btn" onclick="showTab('history',this)">История</button>
  <button class="tab-btn" onclick="showTab('agents',this)">Агенты</button>
  <button class="tab-btn" onclick="showTab('settings',this)">Настройки</button>
</nav>

{% if not is_configured %}
<div class="tab active" id="tab-launch">
  <div class="onboard-box">
    <h2>👋 Добро пожаловать в Терминатор</h2>
    <p>Перед первым запуском заполните настройки: укажите git-репозиторий, папку агентов, Bitrix REST URL и другие пути.</p>
    <button class="btn btn-primary" onclick="showTab('settings', document.querySelectorAll('.tab-btn')[3])">⚙️ Открыть настройки</button>
  </div>
</div>
{% else %}
<!-- Запуск -->
<div class="tab active" id="tab-launch">
  <div class="agent-selector" id="agent-selector">
    {% for key, agent in agents.items() %}
    <button type="button" class="agent-btn" id="abtn-{{ key }}" onclick="selectAgent('{{ key }}')">
      <div class="aname">{{ agent.icon }} {{ agent.name }}</div>
      <div class="adesc">{{ agent.description }}</div>
    </button>
    {% endfor %}
  </div>

  <form id="launch-form" onsubmit="return launchAgent(event)">
    <div id="agent-params"></div>
    <div id="hints-block" style="margin-bottom:16px"></div>
    <div class="launch-row">
      <button type="submit" class="btn btn-primary" id="run-btn">▶ Запустить</button>
      <button type="button" class="btn btn-danger" id="stop-btn" onclick="stopAgent()" style="display:none">⏹ Стоп</button>
      <div id="run-status"></div>
    </div>
  </form>

  <div id="activity-panel" style="display:none; margin-top:24px">
    <div class="activity-title">Активность агента</div>
    <div id="log"></div>
  </div>
</div>
{% endif %}

<!-- История -->
<div class="tab tab-full" id="tab-history">
  <div id="history-wrap">
    <div class="history-toolbar">
      <h2>История запусков</h2>
    </div>
    <div class="history-actions" style="width:100%;display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:8px">
      <button type="button" class="btn-experience" id="btn-collect-experience" onclick="runExperienceSynth()">
        <span class="feedback-badge" id="feedback-unprocessed-badge">0</span>
        <span id="experience-btn-label">🎹 Синтезатор — собрать опыт</span>
      </button>
      <input type="text" class="history-directives-input" id="experience-directives" placeholder="Доп. указания для синтеза (необязательно)" autocomplete="off">
      <button type="button" class="btn-link-file" onclick="openExperienceFile()">Открыть базу опыта</button>
    </div>
    <div class="history-colleague-files">
      <span id="synth-files-label">Файлы «Синтезатора» (редактор ниже):</span>
      <button type="button" class="file-btn" onclick="loadColleagueFile('feedback_synth_agent_prompt.md')" id="synth-btn-prompt">Промпт синтеза</button>
      <button type="button" class="file-btn" onclick="loadColleagueFile('agent_experience.md')" id="synth-btn-base">База опыта</button>
    </div>
    <div id="history-synth-panel">
      <div class="activity-title" id="synth-panel-title" style="margin:0">Синтез базы опыта</div>
      <div id="log-history-synth"></div>
    </div>
    <div class="history-filter-bar" id="history-filter-bar">
      <div class="filter-group">
        <label for="hist-filter-q">Поиск</label>
        <input type="text" id="hist-filter-q" placeholder="Номер задачи, ветка, run id…" autocomplete="off">
      </div>
      <div class="filter-group">
        <label for="hist-filter-agent">Агент</label>
        <select id="hist-filter-agent"><option value="">Все агенты</option></select>
      </div>
      <button type="button" class="btn btn-ghost btn-sm" id="hist-filter-clear">Сбросить</button>
      <span class="history-filter-meta" id="history-filter-meta"></span>
    </div>
    <div id="history-content"><div class="empty">Загрузка...</div></div>
  </div>
</div>

<div class="modal-overlay" id="feedback-modal" onclick="if(event.target===this) closeFeedbackModal()">
  <div class="modal-box" onclick="event.stopPropagation()">
    <h3>Фидбек по запуску</h3>
    <p class="modal-sub" id="feedback-modal-sub">Опишите, что сработало плохо или хорошо.</p>
    <textarea id="feedback-ta" placeholder="Замечания, пожелания, контекст для следующих запусков…" spellcheck="true"></textarea>
    <div class="modal-actions">
      <button type="button" class="btn btn-ghost" onclick="closeFeedbackModal()">Отмена</button>
      <button type="button" class="btn btn-primary" onclick="saveFeedbackModal()">Сохранить</button>
    </div>
  </div>
</div>

<!-- Агенты (редактор) -->
<div class="tab" id="tab-agents">
  <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
    {% for key, agent in agents.items() %}
    <button type="button" class="file-btn" id="eagent-{{ key }}" onclick="selectEditorAgent('{{ key }}')">{{ agent.icon }} {{ agent.name }}</button>
    {% endfor %}
  </div>
  {% for key, agent in agents.items() %}
  <div id="efiles-{{ key }}" style="display:none;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px">
    {% for f in agent.editable %}{% if f.get('file') %}{% set fid = key + '__' + f.file %}
    <button class="file-btn" id="fb-{{ fid }}" onclick="loadFile('{{ key }}','{{ f.file }}','{{ fid }}')">{{ f.label }}</button>
    {% endif %}{% endfor %}
    <span style="flex:1"></span>
    <span class="file-label" id="file-label-{{ key }}"></span>
    <button class="btn btn-primary" onclick="saveFile()" id="save-btn-{{ key }}" disabled>Сохранить</button>
    <span class="save-ok" id="save-ok-{{ key }}">Сохранено!</span>
  </div>
  {% endfor %}
  <textarea id="editor-ta" placeholder="Сначала выберите агента, затем файл..." spellcheck="false"></textarea>
  <div id="save-row-experience" style="display:none;flex-wrap:wrap">
    <span class="file-label" id="file-label-experience_synth"></span>
    <button type="button" class="btn btn-primary" id="save-btn-experience_synth" onclick="saveFile()">Сохранить</button>
    <span class="save-ok" id="save-ok-experience_synth">Сохранено!</span>
  </div>
</div>

<!-- Настройки -->
<div class="tab" id="tab-settings">
<form onsubmit="return saveSettings(event)">

  <div class="settings-section">
    <div class="settings-title">Основное</div>
    <div class="settings-grid">
      <div class="form-group">
        <label>Git репозиторий</label>
        <input type="text" id="s-git_repo" value="{{ config.git_repo }}" placeholder="/path/to/repo">
      </div>
      <div class="form-group">
        <label>Папка агентов (скрипты + промпты)</label>
        <input type="text" id="s-scripts_dir" value="{{ config.scripts_dir }}" placeholder="/path/to/agents">
      </div>
    </div>
  </div>

  <div class="settings-section">
    <div class="settings-title">Claude CLI</div>
    <div class="settings-grid">
      <div class="form-group">
        <label>Команда (claude или путь к скрипту)</label>
        <input type="text" id="s-claude_cmd" value="{{ config.claude_cmd }}" placeholder="claude">
      </div>
    </div>
    <div class="checkbox-row">
      <input type="checkbox" id="s-use_proxy" {% if config.use_proxy %}checked{% endif %}>
      <label for="s-use_proxy" style="text-transform:none;font-size:14px;letter-spacing:0;opacity:1">Использовать прокси</label>
    </div>
    <div class="settings-grid" style="margin-top:8px">
      <div class="form-group">
        <label>HTTP-порт прокси</label>
        <input type="number" id="s-proxy_http_port" value="{{ config.proxy_http_port }}">
      </div>
      <div class="form-group">
        <label>SOCKS5-порт прокси</label>
        <input type="number" id="s-proxy_socks_port" value="{{ config.proxy_socks_port }}">
      </div>
      <div class="form-group">
        <label>Subscription URL</label>
        <input type="text" id="s-proxy_subscription_url" value="{{ config.proxy_subscription_url }}" placeholder="https://.../#username">
      </div>
      <div class="form-group">
        <label>Bypass-домены (no_proxy)</label>
        <input type="text" id="s-no_proxy_domains" value="{{ config.no_proxy_domains }}" placeholder="testbitrix.genotek.ru,*.genotek.ru">
      </div>
      <div class="form-group">
        <label>Скрипт запуска прокси</label>
        <input type="text" id="s-proxy_start_script" value="{{ config.proxy_start_script }}">
      </div>
      <div class="form-group">
        <label>Скрипт остановки прокси</label>
        <input type="text" id="s-proxy_stop_script" value="{{ config.proxy_stop_script }}">
      </div>
    </div>
    <div class="proxy-controls">
      <button type="button" class="btn btn-ghost btn-sm" onclick="startProxy()">▶ Запустить прокси</button>
      <button type="button" class="btn btn-ghost btn-sm" onclick="stopProxy()">⏹ Остановить прокси</button>
      <span id="proxy-action-status" style="font-size:12px;color:var(--text2);margin-left:6px"></span>
    </div>
  </div>

  <div class="settings-section">
    <div class="settings-title">Хранилище</div>
    <div class="settings-grid">
      <div class="form-group">
        <label>Заметки агентов (notes_dir)</label>
        <input type="text" id="s-notes_dir" value="{{ config.get('notes_dir', '') }}" placeholder="~/.terminator/notes">
      </div>
      <div class="form-group">
        <label>Результаты тестов</label>
        <input type="text" id="s-test_results_dir" value="{{ config.test_results_dir }}" placeholder="~/Documents/TestResult">
      </div>
      <div class="form-group">
        <label>Code Review отчёты</label>
        <input type="text" id="s-code_review_dir" value="{{ config.code_review_dir }}" placeholder="~/Documents/CodeReview">
      </div>
    </div>
  </div>

  <div class="settings-section">
    <div class="settings-title">Bitrix</div>
    <div class="settings-grid single">
      <div class="form-group">
        <label>REST URL</label>
        <input type="text" id="s-bitrix_rest_url" value="{{ config.bitrix_rest_url }}" placeholder="https://bitrix.example.ru/rest/123/token">
      </div>
    </div>
  </div>

  <div class="settings-section">
    <div class="settings-title">Kubernetes</div>
    <div class="settings-grid">
      <div class="form-group">
        <label>YC CLI (путь или yc)</label>
        <input type="text" id="s-yc_bin" value="{{ config.yc_bin }}" placeholder="yc">
      </div>
      <div class="form-group">
        <label>YC Profile</label>
        <input type="text" id="s-yc_profile" value="{{ config.yc_profile }}" placeholder="default">
      </div>
      <div class="form-group">
        <label>Test-кластер</label>
        <input type="text" id="s-kube_test_cluster" value="{{ config.kube_test_cluster }}">
      </div>
      <div class="form-group">
        <label>Prod-кластер</label>
        <input type="text" id="s-kube_prod_cluster" value="{{ config.kube_prod_cluster }}">
      </div>
      <div class="form-group">
        <label>Test pod pattern</label>
        <input type="text" id="s-kube_test_pod_pattern" value="{{ config.kube_test_pod_pattern }}">
      </div>
      <div class="form-group">
        <label>Prod pod pattern</label>
        <input type="text" id="s-kube_prod_pod_pattern" value="{{ config.kube_prod_pod_pattern }}">
      </div>
      <div class="form-group">
        <label>Скрипт upload</label>
        <input type="text" id="s-kube_upload_script" value="{{ config.kube_upload_script }}">
      </div>
      <div class="form-group">
        <label>Скрипт run</label>
        <input type="text" id="s-kube_run_script" value="{{ config.kube_run_script }}">
      </div>
      <div class="form-group full-width">
        <label>Директории поиска файлов (search_dirs, через пробел)</label>
        <input type="text" id="s-search_dirs" value="{{ config.get('search_dirs', '') }}" placeholder="/app/www/api/classes /app/www/api/controllers ...">
      </div>
    </div>
  </div>

  <div class="settings-section">
    <div class="settings-title">Сервер</div>
    <div class="settings-grid">
      <div class="form-group">
        <label>Порт</label>
        <input type="number" id="s-port" value="{{ config.port }}">
      </div>
      <div class="form-group">
        <label>Домен</label>
        <input type="text" id="s-domain" value="{{ config.domain }}">
      </div>
    </div>
    <div class="settings-note">⚠️ Изменение порта / домена требует рестарта сервиса и nginx</div>
  </div>

  <div style="display:flex;align-items:center;gap:12px;margin-top:8px">
    <button type="submit" class="btn btn-primary">Сохранить настройки</button>
    <span id="settings-ok" class="save-ok">Сохранено!</span>
  </div>
</form>
</div>

<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js" crossorigin></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.1.7/dist/purify.min.js" crossorigin></script>
<script>
const AGENTS = {{ agents_json | safe }};
let currentAgent = null;
let currentRunId = null;
let evtSrc = null;

// ── Имена агентов по теме ────────────────────────────────────────────────────
const AGENT_THEME_NAMES = {
  '007': {
    task:             { icon: '🕵️', name: 'Оперативник' },
    test:             { icon: '🎯', name: 'Контроль'     },
    write_tests:      { icon: '🔬', name: 'Ветвь-Q'      },
    code_review:      { icon: '🧩', name: 'Аналитика'    },
    experience_synth: { icon: '🎹', name: 'Досье'         },
  },
  'terminator': {
    task:             { icon: '🤖', name: 'Терминатор'   },
    test:             { icon: '🧪', name: 'Сканер'        },
    write_tests:      { icon: '🧬', name: 'Генератор'     },
    code_review:      { icon: '🔍', name: 'Процессор'     },
    experience_synth: { icon: '🎹', name: 'Синтезатор'    },
  },
};

// ── Тема ────────────────────────────────────────────────────────────────────
function setTheme(name) {
  document.documentElement.setAttribute('data-theme', name);
  localStorage.setItem('theme', name);
  document.getElementById('theme-007-btn').classList.toggle('active', name === '007');
  document.getElementById('theme-term-btn').classList.toggle('active', name === 'terminator');
  const logo  = document.getElementById('header-logo');
  const title = document.getElementById('header-title-text');
  if (logo)  logo.src          = name === '007' ? '/icon-007' : '/icon';
  if (title) title.textContent = name === '007' ? 'Агент 007' : 'Терминатор';
  document.title = name === '007' ? 'Агент 007' : 'Терминатор';
  let fav = document.querySelector("link[rel='icon']");
  if (!fav) { fav = document.createElement('link'); fav.rel = 'icon'; fav.type = 'image/png'; document.head.appendChild(fav); }
  fav.href = (name === '007' ? '/icon-007' : '/icon') + '?t=' + Date.now();
  const agentNames = AGENT_THEME_NAMES[name] || AGENT_THEME_NAMES['terminator'];
  Object.entries(agentNames).forEach(([key, data]) => {
    const btn = document.getElementById('abtn-' + key);
    if (btn) { const el = btn.querySelector('.aname'); if (el) el.textContent = data.icon + ' ' + data.name; }
    const ebtn = document.getElementById('eagent-' + key);
    if (ebtn) ebtn.textContent = data.icon + ' ' + data.name;
    HISTORY_AGENT_NAMES[key] = data.name;
  });
  const synthName = agentNames['experience_synth'].name;
  const synthIcon = agentNames['experience_synth'].icon;
  const expLabel   = document.getElementById('experience-btn-label');
  const filesLabel = document.getElementById('synth-files-label');
  const panelTitle = document.getElementById('synth-panel-title');
  const btnPrompt  = document.getElementById('synth-btn-prompt');
  const btnBase    = document.getElementById('synth-btn-base');
  if (expLabel)   expLabel.textContent   = synthIcon + ' ' + synthName + ' — собрать опыт';
  if (filesLabel) filesLabel.textContent = 'Файлы «' + synthName + '» (редактор ниже):';
  if (panelTitle) panelTitle.textContent = synthName + ' — запуск';
  if (btnPrompt)  btnPrompt.textContent  = name === '007' ? 'Промпт агента' : 'Промпт синтеза';
  if (btnBase)    btnBase.textContent    = name === '007' ? 'База данных'   : 'База опыта';
  if (historyDataCache) renderHistoryTable();
}

// ── Инициализация ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  {% if is_configured %}
  const first = Object.keys(AGENTS)[0];
  if (first) selectAgent(first);
  {% endif %}
  checkProxy();
  setInterval(checkProxy, 30000);
  refreshFeedbackBadge();
  const savedTheme = localStorage.getItem('theme') || '007';
  setTheme(savedTheme);
});

// ── Табы ────────────────────────────────────────────────────────────────────
function showTab(name, btn) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (btn) btn.classList.add('active');
  if (name === 'history') { loadHistory(); refreshFeedbackBadge(); }
}

// ── Прокси ──────────────────────────────────────────────────────────────────
async function checkProxy() {
  const dot = document.getElementById('proxy-dot');
  const lbl = document.getElementById('proxy-label');
  dot.className = 'proxy-dot'; lbl.textContent = '...';
  try {
    const d = await fetch('/proxy/status').then(r => r.json());
    if (d.disabled) {
      dot.className = 'proxy-dot disabled'; lbl.textContent = 'прокси откл.';
    } else if (d.running) {
      dot.className = 'proxy-dot ok'; lbl.textContent = 'прокси ✓';
    } else {
      dot.className = 'proxy-dot fail';
      lbl.textContent = d.process ? 'порт закрыт' : 'прокси не запущен';
    }
  } catch { dot.className = 'proxy-dot fail'; lbl.textContent = 'нет ответа'; }
}

// ── Выбор агента ────────────────────────────────────────────────────────────
function selectAgent(key) {
  currentAgent = key;
  document.querySelectorAll('.agent-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('abtn-' + key);
  if (btn) btn.classList.add('active');
  renderParams(key); renderHints(key);
}

function renderParams(key) {
  const a = AGENTS[key];
  document.getElementById('agent-params').innerHTML = a.params.map(p =>
    `<div class="form-group">
      <label>${p.label}${p.required ? ' *' : ''}</label>
      <input type="text" id="p-${p.name}" placeholder="${p.placeholder||''}" ${p.required ? 'required' : ''}>
    </div>`
  ).join('');
}

function renderHints(key) {
  const a = AGENTS[key]; const el = document.getElementById('hints-block');
  if (!a.hints || !a.hints.length) { el.innerHTML = ''; return; }
  const dirParam = a.params && a.params.find(p => p.name === 'directives');
  const filterParam = a.params && a.params.find(p => p.name === 'filter');
  const target = dirParam ? 'directives' : (filterParam ? 'filter' : 'directives');
  el.innerHTML = `<div class="hint-label">Подсказки</div>
    <div class="hints-row">${a.hints.map(h =>
      `<span class="chip" onclick="applyHint('${target}','${h.replace(/'/g,"\\'")}'">${h}</span>`
    ).join('')}</div>`;
}

function applyHint(name, val) { const el = document.getElementById('p-' + name); if (el) el.value = val; }

// ── Запуск ──────────────────────────────────────────────────────────────────
async function launchAgent(e) {
  e.preventDefault(); if (!currentAgent) return false;
  const a = AGENTS[currentAgent]; const params = {};
  a.params.forEach(p => { const el = document.getElementById('p-' + p.name); if (el) params[p.name] = el.value.trim(); });
  document.getElementById('log').innerHTML = '';
  document.getElementById('activity-panel').style.display = 'block';
  document.getElementById('run-btn').disabled = true;
  document.getElementById('stop-btn').style.display = 'inline-block';
  setStatus('<span class="spinner"></span> Запуск...');
  try {
    const resp = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({agent:currentAgent, params})});
    const data = await resp.json();
    currentRunId = data.run_id;
    startStream(currentRunId);
  } catch (err) { appendHtml(`<div class="log-error">Ошибка: ${err.message}</div>`); resetBtns(); }
  return false;
}

function startStream(runId) {
  if (evtSrc) evtSrc.close();
  evtSrc = new EventSource('/stream/' + runId);
  evtSrc.onmessage = e => {
    const d = JSON.parse(e.data);
    if (d.heartbeat) return;
    if (d.done) {
      evtSrc.close(); evtSrc = null;
      const isT800 = document.documentElement.getAttribute('data-theme') === 'terminator';
      const okMark = isT800 ? '▣' : '✅';
      const icon = d.status === 'success' ? `<span class="log-ok">${okMark} Готово</span>` : `<span class="log-error">❌ ${d.status}</span>`;
      const meta = [d.cost ? '💰 $' + d.cost.toFixed(4) : '', d.duration_s ? '⏱ ' + d.duration_s + 'с' : ''].filter(Boolean).join(' · ');
      setStatus(`${icon} <span class="dim">${meta}</span>`);
      resetBtns(); return;
    }
    if (d.html) appendHtml(d.html);
  };
  evtSrc.onerror = () => { if (evtSrc) { evtSrc.close(); evtSrc = null; } setStatus('<span class="log-error">Соединение прервано</span>'); resetBtns(); };
}

function stopAgent() { if (currentRunId) fetch('/stop/' + currentRunId, {method:'POST'}); if (evtSrc) { evtSrc.close(); evtSrc = null; } setStatus('<span class="dim">Остановлено</span>'); resetBtns(); }
function appendHtml(html) { const log = document.getElementById('log'); log.insertAdjacentHTML('beforeend', html); log.scrollTop = log.scrollHeight; }
function setStatus(html) { document.getElementById('run-status').innerHTML = html; }
function resetBtns() { document.getElementById('run-btn').disabled = false; document.getElementById('stop-btn').style.display = 'none'; }

// ── История ─────────────────────────────────────────────────────────────────
function starRowHtml(runId, rating) {
  const r = rating ? Number(rating) : 0;
  let h = '<span class="star-row" onclick="event.stopPropagation()">';
  for (let i = 1; i <= 5; i++) {
    const on = i <= r ? ' on' : '';
    h += `<button type="button" class="star-btn${on}" data-n="${i}" onclick="setStarRating('${runId}', ${i})">★</button>`;
  }
  return h + '</span>';
}

async function setStarRating(runId, n) {
  try {
    await fetch('/feedback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({run_id: runId, rating: n})});
    await loadHistory();
  } catch (e) { console.error(e); }
}

let feedbackModalRunId = null;
const feedbackTextCache = {};

function openFeedbackModal(runId) {
  feedbackModalRunId = runId;
  document.getElementById('feedback-ta').value = feedbackTextCache[runId] || '';
  document.getElementById('feedback-modal-sub').textContent = 'Run ID: ' + runId;
  document.getElementById('feedback-modal').classList.add('show');
  setTimeout(() => document.getElementById('feedback-ta').focus(), 100);
}

function closeFeedbackModal() {
  feedbackModalRunId = null;
  document.getElementById('feedback-modal').classList.remove('show');
}

async function saveFeedbackModal() {
  if (!feedbackModalRunId) return;
  const text = document.getElementById('feedback-ta').value.trim();
  try {
    await fetch('/feedback', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({run_id: feedbackModalRunId, feedback: text})});
    feedbackTextCache[feedbackModalRunId] = text;
    closeFeedbackModal();
    await loadHistory();
  } catch (e) { alert('Ошибка сохранения'); }
}

async function openExperienceFile() {
  try {
    const d = await fetch('/open-experience', {method:'POST'}).then(r => r.json());
    if (d.text) {
      const navBtns = document.querySelectorAll('nav .tab-btn');
      const agentsTabBtn = navBtns.length >= 3 ? navBtns[2] : null;
      showTab('agents', agentsTabBtn);
      editorAgent = 'experience_synth';
      editorFile = 'agent_experience.md';
      document.getElementById('editor-ta').value = d.text;
      document.getElementById('editor-ta').placeholder = 'agent_experience.md';
      const rowEx = document.getElementById('save-row-experience');
      if (rowEx) rowEx.style.display = 'flex';
      const lbl = document.getElementById('file-label-experience_synth');
      if (lbl) lbl.textContent = 'agent_experience.md';
    }
  } catch (e) {}
}

let expEvtSrc = null;

async function refreshFeedbackBadge() {
  try {
    const d = await fetch('/feedback/status').then(r => r.json());
    const n = Number(d.unprocessed) || 0;
    const badge = document.getElementById('feedback-unprocessed-badge');
    const btn = document.getElementById('btn-collect-experience');
    if (badge) { badge.textContent = n; badge.classList.toggle('show', n > 0); }
    if (btn) btn.classList.toggle('btn-experience--urgent', n > 0);
  } catch (e) {}
}

async function runExperienceSynth() {
  const btn = document.getElementById('btn-collect-experience');
  const panel = document.getElementById('history-synth-panel');
  const logEl = document.getElementById('log-history-synth');
  const dirEl = document.getElementById('experience-directives');
  const directives = dirEl ? dirEl.value.trim() : '';
  if (btn) btn.disabled = true;
  panel.classList.add('active');
  logEl.innerHTML = '<div class="dim">Запуск Синтезатора…</div>';
  try {
    const resp = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({agent:'experience_synth', params:{directives}})});
    const data = await resp.json();
    if (!data.run_id) { logEl.innerHTML = '<div class="log-error">Не удалось запустить</div>'; if (btn) btn.disabled = false; return; }
    if (expEvtSrc) expEvtSrc.close();
    expEvtSrc = new EventSource('/stream/' + data.run_id);
    expEvtSrc.onmessage = ev => {
      const d = JSON.parse(ev.data);
      if (d.heartbeat) return;
      if (d.done) {
        expEvtSrc.close(); expEvtSrc = null;
        const ok = d.status === 'success';
        const isT800s = document.documentElement.getAttribute('data-theme') === 'terminator';
        const okMarkS = isT800s ? '▣' : '✅';
        appendHtmlSynth(`<div class="log-result-final">${ok ? `<span class="log-ok">${okMarkS} Готово</span>` : '<span class="log-error">❌ ' + d.status + '</span>'} <span class="dim">${d.cost ? '💰 $' + Number(d.cost).toFixed(4) : ''}</span></div>`);
        if (btn) btn.disabled = false;
        loadHistory(); refreshFeedbackBadge(); return;
      }
      if (d.html) appendHtmlSynth(d.html);
    };
    expEvtSrc.onerror = () => {
      if (expEvtSrc) { expEvtSrc.close(); expEvtSrc = null; }
      appendHtmlSynth('<div class="log-error">Соединение прервано</div>');
      if (btn) btn.disabled = false; refreshFeedbackBadge();
    };
  } catch (err) { logEl.innerHTML = '<div class="log-error">' + err.message + '</div>'; if (btn) btn.disabled = false; }
}

function appendHtmlSynth(html) {
  const log = document.getElementById('log-history-synth');
  log.insertAdjacentHTML('beforeend', html); log.scrollTop = log.scrollHeight;
}

let historyDataCache = null;
let histFilterDebounce = null;

const HISTORY_AGENT_NAMES = {
  task: 'Решала', test: 'Шмонщик', write_tests: 'Писарь',
  code_review: 'Авторитет', experience_synth: 'Синтезатор'
};
function historyAgentLabel(key) { return HISTORY_AGENT_NAMES[key] || key || '—'; }

function rowMatchesHistoryFilters(e, qRaw, agentKey) {
  if (agentKey && e.agent !== agentKey) return false;
  const q = (qRaw || '').trim().toLowerCase();
  if (!q) return true;
  const hay = [e.id, e.agent, e.agent_name,
    e.params && e.params.task_id, e.params && e.params.branch,
    e.params && e.params.filter, e.params && e.params.directives,
    JSON.stringify(e.params || {})].filter(Boolean).join(' ').toLowerCase();
  return hay.indexOf(q) !== -1;
}

function populateHistoryAgentSelect(data) {
  const sel = document.getElementById('hist-filter-agent');
  if (!sel) return;
  const cur = sel.value; const seen = new Set();
  data.forEach(e => { if (e.agent) seen.add(e.agent); });
  let opts = '<option value="">Все агенты</option>';
  Array.from(seen).sort().forEach(k => { opts += `<option value="${k}">${historyAgentLabel(k)} (${k})</option>`; });
  sel.innerHTML = opts;
  if (cur && seen.has(cur)) sel.value = cur;
}

function buildHistoryTableHtml(data) {
  let rows = '';
  data.forEach(e => {
    feedbackTextCache[e.id] = e.feedback || '';
    const noteCell = e.note_path
      ? `<button class="obs-link" onclick="event.stopPropagation();openNote(${JSON.stringify(e.note_path)})" title="${e.note_path}">📝 ${e.note_name}</button>` : '—';
    const resultCell = e.session_log_path
      ? `<button type="button" class="log-toggle" id="resulttgl-${e.id}" onclick="event.stopPropagation();toggleResult('${e.id}')">▶ результат</button>` : '—';
    const logCell = e.session_log_path
      ? `<button type="button" class="log-toggle" id="logtgl-${e.id}" onclick="event.stopPropagation();toggleLog('${e.id}')">▶ лог</button>` : '—';
    const hasFb = (e.feedback && e.feedback.length > 0) ? ' has-text' : '';
    const fbPreview = (e.feedback && e.feedback.length) ? 'изменить' : 'фидбек';
    rows += `<tr class="hist-row">
      <td style="white-space:nowrap">${fmtDate(e.started_at)}</td>
      <td style="white-space:nowrap">${e.agent_name||e.agent}</td>
      <td class="params-cell">${fmtParams(e.params)}</td>
      <td class="st-${e.status}" style="white-space:nowrap">${stIcon(e.status)} ${e.status}</td>
      <td style="white-space:nowrap">${e.cost_usd?'$'+Number(e.cost_usd).toFixed(4):'—'}</td>
      <td style="white-space:nowrap">${e.duration_s!=null?e.duration_s+'с':'—'}</td>
      <td>${noteCell}</td>
      <td>${resultCell}</td>
      <td>${logCell}</td>
      <td style="white-space:nowrap">${starRowHtml(e.id, e.rating)}</td>
      <td style="white-space:nowrap"><button type="button" class="btn-feedback${hasFb}" onclick="event.stopPropagation();openFeedbackModal('${e.id}')">${fbPreview}</button></td>
    </tr>
    <tr id="logrow-${e.id}" class="log-expand-row" style="display:none">
      <td colspan="11"><div class="log-expand-box" id="logbox-${e.id}"><div class="dim" style="padding:4px">Загрузка...</div></div></td>
    </tr>
    <tr id="resultrow-${e.id}" class="log-expand-row" style="display:none">
      <td colspan="11"><div class="log-expand-box" id="resultbox-${e.id}"><div class="dim" style="padding:4px">Загрузка...</div></div></td>
    </tr>`;
  });
  return `<table><thead><tr>
    <th>Дата</th><th>Агент</th><th>Параметры</th><th>Статус</th>
    <th>Стоимость</th><th>Время</th><th>Заметка</th><th>Результат</th>
    <th>Лог</th><th>Оценка</th><th>Фидбек</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

function renderHistoryTable() {
  const el = document.getElementById('history-content');
  const meta = document.getElementById('history-filter-meta');
  if (!historyDataCache || !historyDataCache.length) {
    el.innerHTML = '<div class="empty">История пуста</div>'; if (meta) meta.textContent = ''; return;
  }
  const q = document.getElementById('hist-filter-q') ? document.getElementById('hist-filter-q').value : '';
  const agentKey = document.getElementById('hist-filter-agent') ? document.getElementById('hist-filter-agent').value : '';
  const filtered = historyDataCache.filter(e => rowMatchesHistoryFilters(e, q, agentKey));
  if (!filtered.length) {
    el.innerHTML = '<div class="empty">Нет записей по фильтру</div>';
    if (meta) meta.textContent = 'Показано 0 из ' + historyDataCache.length; return;
  }
  el.innerHTML = buildHistoryTableHtml(filtered);
  if (meta) meta.textContent = 'Показано ' + filtered.length + ' из ' + historyDataCache.length;
}

function bindHistoryFiltersOnce() {
  if (window._histFilterBound) return; window._histFilterBound = true;
  const iq = document.getElementById('hist-filter-q');
  const ia = document.getElementById('hist-filter-agent');
  const clr = document.getElementById('hist-filter-clear');
  if (iq) iq.addEventListener('input', () => { clearTimeout(histFilterDebounce); histFilterDebounce = setTimeout(renderHistoryTable, 300); });
  if (ia) ia.addEventListener('change', renderHistoryTable);
  if (clr) clr.addEventListener('click', () => { if (iq) iq.value = ''; if (ia) ia.value = ''; renderHistoryTable(); });
}

async function loadHistory() {
  const el = document.getElementById('history-content');
  const fbar = document.getElementById('history-filter-bar');
  try {
    const data = await fetch('/history').then(r => r.json());
    historyDataCache = data;
    bindHistoryFiltersOnce();
    if (!data.length) {
      el.innerHTML = '<div class="empty">История пуста</div>';
      if (fbar) fbar.classList.remove('visible'); refreshFeedbackBadge(); return;
    }
    if (fbar) fbar.classList.add('visible');
    populateHistoryAgentSelect(data);
    renderHistoryTable(); refreshFeedbackBadge();
  } catch { el.innerHTML = '<div class="empty">Ошибка загрузки</div>'; if (fbar) fbar.classList.remove('visible'); }
}

function escapeHtmlJs(s) { const t = document.createElement('div'); t.textContent = s == null ? '' : String(s); return t.innerHTML; }

function renderMarkdownToSafeHtml(text) {
  const raw = text == null ? '' : String(text);
  if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
    return '<pre class="note-pre">' + escapeHtmlJs(raw) + '</pre>';
  }
  try {
    const html = marked.parse(raw, {gfm: true, breaks: true});
    return '<div class="markdown-body note-md">' + DOMPurify.sanitize(html) + '</div>';
  } catch (e) { return '<pre class="note-pre">' + escapeHtmlJs(raw) + '</pre>'; }
}

async function toggleLog(runId) {
  const logRow = document.getElementById('logrow-' + runId);
  const tgl = document.getElementById('logtgl-' + runId);
  if (!logRow) return;
  if (logRow.style.display !== 'none') { logRow.style.display = 'none'; if (tgl) tgl.textContent = '▶ лог'; return; }
  logRow.style.display = ''; if (tgl) tgl.textContent = '▼ лог';
  const box = document.getElementById('logbox-' + runId);
  if (!box || box.dataset.loaded) return;
  box.dataset.loaded = '1';
  try { const d = await fetch('/history-log/' + runId).then(r => r.json()); box.innerHTML = d.html || '<div class="dim" style="padding:4px">Лог пуст</div>'; }
  catch { box.innerHTML = '<div class="log-error" style="padding:4px">Ошибка загрузки лога</div>'; }
}

async function toggleResult(runId) {
  const resultRow = document.getElementById('resultrow-' + runId);
  const tgl = document.getElementById('resulttgl-' + runId);
  if (!resultRow || !tgl) return;
  if (resultRow.style.display !== 'none') { resultRow.style.display = 'none'; tgl.textContent = '▶ результат'; return; }
  resultRow.style.display = ''; tgl.textContent = '▼ результат';
  const box = document.getElementById('resultbox-' + runId);
  if (!box || box.dataset.loaded) return;
  box.dataset.loaded = '1';
  try {
    const r = await fetch('/history-result/' + runId);
    const d = await r.json();
    if (!r.ok || d.error) { box.innerHTML = '<div class="log-error" style="padding:4px">' + escapeHtmlJs(d.error || 'нет результата') + '</div>'; return; }
    box.innerHTML = '<div class="note-toolbar"><span class="dim">Итог агента</span></div>' + renderMarkdownToSafeHtml(d.text);
  } catch { box.innerHTML = '<div class="log-error" style="padding:4px">Ошибка загрузки</div>'; }
}

async function openNote(path) {
  try {
    const r = await fetch('/note?path=' + encodeURIComponent(path));
    const d = await r.json();
    if (!r.ok || d.error) return;
    const navBtns = document.querySelectorAll('nav .tab-btn');
    const agentsTabBtn = navBtns.length >= 3 ? navBtns[2] : null;
    showTab('agents', agentsTabBtn);
    editorAgent = 'experience_synth';
    editorFile = d.name || path;
    document.getElementById('editor-ta').value = d.text;
    document.getElementById('editor-ta').placeholder = 'Редактирование: ' + editorFile;
    const rowEx = document.getElementById('save-row-experience');
    if (rowEx) rowEx.style.display = 'flex';
    const lbl = document.getElementById('file-label-experience_synth');
    if (lbl) lbl.textContent = editorFile;
  } catch (e) {}
}

function fmtDate(iso) {
  if (!iso) return '—'; const d = new Date(iso);
  return d.toLocaleDateString('ru') + ' ' + d.toLocaleTimeString('ru', {hour:'2-digit',minute:'2-digit'});
}
function fmtParams(p) { if (!p) return '—'; return Object.entries(p).filter(([,v]) => v).map(([k,v]) => `<b>${k}:</b> ${v}`).join(', ') || '—'; }
function stIcon(s) {
  const isT800 = document.documentElement.getAttribute('data-theme') === 'terminator';
  const ok = isT800 ? '▣' : '✅';
  return {success: ok, error: '❌', stopped: '⏹', running: '⏳'}[s] || '';
}

// ── Редактор ─────────────────────────────────────────────────────────────────
let editorAgent = null, editorFile = null;

function selectEditorAgent(key) {
  const rowEx = document.getElementById('save-row-experience');
  if (rowEx) rowEx.style.display = 'none';
  document.querySelectorAll('[id^="eagent-"]').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('eagent-' + key); if (btn) btn.classList.add('active');
  document.querySelectorAll('[id^="efiles-"]').forEach(d => d.style.display = 'none');
  const filesDiv = document.getElementById('efiles-' + key); if (filesDiv) filesDiv.style.display = 'flex';
  editorAgent = key; editorFile = null;
  document.getElementById('editor-ta').value = '';
  document.getElementById('editor-ta').placeholder = 'Выберите файл для редактирования...';
}

const COLLEAGUE_KEY = 'experience_synth';

async function loadColleagueFile(fname) {
  const navBtns = document.querySelectorAll('nav .tab-btn');
  const agentsTabBtn = navBtns.length >= 3 ? navBtns[2] : null;
  showTab('agents', agentsTabBtn);
  document.querySelectorAll('[id^="eagent-"]').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('[id^="efiles-"]').forEach(d => d.style.display = 'none');
  editorAgent = COLLEAGUE_KEY; editorFile = fname;
  const rowEx = document.getElementById('save-row-experience');
  if (rowEx) rowEx.style.display = 'flex';
  const lbl = document.getElementById('file-label-experience_synth');
  if (lbl) lbl.textContent = fname;
  const saveBtn = document.getElementById('save-btn-experience_synth');
  if (saveBtn) saveBtn.disabled = false;
  try {
    const d = await fetch(`/agent/${COLLEAGUE_KEY}/file?f=${encodeURIComponent(fname)}`).then(r => r.json());
    document.getElementById('editor-ta').value = d.content || '';
  } catch { document.getElementById('editor-ta').value = '(ошибка загрузки)'; }
}

async function loadFile(agentKey, fname, btnId) {
  const filesDiv = document.getElementById('efiles-' + agentKey);
  if (filesDiv) filesDiv.querySelectorAll('.file-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('fb-' + btnId); if (btn) btn.classList.add('active');
  editorAgent = agentKey; editorFile = fname;
  const lbl = document.getElementById('file-label-' + agentKey); if (lbl) lbl.textContent = fname;
  const saveBtn = document.getElementById('save-btn-' + agentKey); if (saveBtn) saveBtn.disabled = false;
  try {
    const d = await fetch(`/agent/${agentKey}/file?f=${encodeURIComponent(fname)}`).then(r => r.json());
    document.getElementById('editor-ta').value = d.content || '';
  } catch { document.getElementById('editor-ta').value = '(ошибка загрузки)'; }
}

async function saveFile() {
  if (!editorFile || !editorAgent) return;
  const content = document.getElementById('editor-ta').value;
  try {
    await fetch(`/agent/${editorAgent}/file`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({f:editorFile, content})});
    const msg = document.getElementById('save-ok-' + editorAgent);
    if (msg) { msg.classList.add('show'); setTimeout(() => msg.classList.remove('show'), 2000); }
  } catch { alert('Ошибка сохранения'); }
}

document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); if (editorFile) saveFile(); } });

// ── Настройки ─────────────────────────────────────────────────────────────────
const SETTING_KEYS = ['git_repo','scripts_dir','claude_cmd','use_proxy',
  'proxy_http_port','proxy_socks_port','proxy_subscription_url','no_proxy_domains',
  'proxy_start_script','proxy_stop_script',
  'notes_dir','test_results_dir','code_review_dir',
  'bitrix_rest_url','yc_bin','yc_profile',
  'kube_test_cluster','kube_prod_cluster','kube_test_pod_pattern','kube_prod_pod_pattern',
  'kube_upload_script','kube_run_script','search_dirs','port','domain'];

async function saveSettings(e) {
  e.preventDefault();
  const data = {};
  SETTING_KEYS.forEach(k => {
    const el = document.getElementById('s-' + k);
    if (!el) return;
    data[k] = el.type === 'checkbox' ? el.checked : el.value;
  });
  try {
    await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    const ok = document.getElementById('settings-ok'); ok.classList.add('show'); setTimeout(() => ok.classList.remove('show'), 2500);
    checkProxy();
  } catch { alert('Ошибка сохранения настроек'); }
  return false;
}

async function startProxy() {
  const st = document.getElementById('proxy-action-status');
  st.textContent = 'Запускаю...';
  try {
    const d = await fetch('/proxy/start', {method:'POST'}).then(r => r.json());
    st.textContent = d.message || 'Запущено'; setTimeout(() => { st.textContent = ''; checkProxy(); }, 5000);
  } catch { st.textContent = 'Ошибка'; }
}

async function stopProxy() {
  const st = document.getElementById('proxy-action-status');
  st.textContent = 'Останавливаю...';
  try {
    await fetch('/proxy/stop', {method:'POST'});
    st.textContent = 'Остановлено'; setTimeout(() => { st.textContent = ''; checkProxy(); }, 2000);
  } catch { st.textContent = 'Ошибка'; }
}
</script>
</body>
</html>"""

# ─── Запуск ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = load_config()
    port   = cfg.get("port", 8765)
    domain = cfg.get("domain", "terminator.agent")

    # Первый запуск — генерировать task_agent_config.sh
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_SH):
        _generate_task_agent_config_sh(cfg)

    print(f"Терминатор запущен: http://localhost:{port}  /  http://{domain}")
    print(f"Конфиг:    {CONFIG_FILE}")
    print(f"Агенты:    {cfg.get('scripts_dir', os.path.join(PROGRAM_DIR, 'agents'))}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
