#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Терминатор Cursor — кросс-платформенный инсталлятор
Поддерживает: Ubuntu/Debian, Fedora/RHEL, Arch, macOS (Intel + Apple Silicon)
"""

import os
import sys
import json
import shutil
import subprocess
import platform
import textwrap
from pathlib import Path

# ─── Константы ────────────────────────────────────────────────────────────────

TERMINATOR_DIR  = os.path.expanduser("~/.terminator")
CONFIG_FILE     = os.path.join(TERMINATOR_DIR, "config.json")
CONFIG_SH       = os.path.join(TERMINATOR_DIR, "task_agent_config.sh")
PACKAGE_DIR     = os.path.dirname(os.path.abspath(__file__))
DOMAIN          = "terminator.agent"
SERVICE_PORT    = 8765

# ─── Определение ОС ───────────────────────────────────────────────────────────

def detect_os():
    """Возвращает (os_type, distro): os_type = 'macos'|'linux', distro = 'ubuntu'|'fedora'|..."""
    if sys.platform == "darwin":
        # Определяем архитектуру Apple Silicon vs Intel
        arch = platform.machine()
        return "macos", "apple_silicon" if arch == "arm64" else "intel"

    # Linux — читаем /etc/os-release
    distro = "unknown"
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("ID="):
                    distro = line.strip().split("=", 1)[1].strip('"').lower()
                    break
    except FileNotFoundError:
        pass
    return "linux", distro


OS_TYPE, DISTRO = detect_os()
IS_MACOS  = OS_TYPE == "macos"
IS_LINUX  = OS_TYPE == "linux"
IS_FEDORA = DISTRO in ("fedora", "rhel", "centos", "rocky", "almalinux")
IS_ARCH   = DISTRO == "arch"
IS_DEBIAN = DISTRO in ("ubuntu", "debian", "linuxmint", "pop")

NGINX_CONF_DIR = (
    "/opt/homebrew/etc/nginx/servers" if (IS_MACOS and DISTRO == "apple_silicon")
    else "/usr/local/etc/nginx/servers" if IS_MACOS
    else "/etc/nginx/conf.d"
)

# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _color(text, code):
    return f"\033[{code}m{text}\033[0m"

def ok(msg):   print(_color(f"  ✅ {msg}", "32"))
def warn(msg): print(_color(f"  ⚠️  {msg}", "33"))
def info(msg): print(_color(f"  ℹ️  {msg}", "36"))
def err(msg):  print(_color(f"  ❌ {msg}", "31"))

def run(cmd, check=True, capture=False, sudo=False):
    """Запускает команду, при необходимости через sudo."""
    if sudo and IS_LINUX and os.geteuid() != 0:
        if isinstance(cmd, str):
            cmd = "sudo " + cmd
        else:
            cmd = ["sudo"] + cmd
    kwargs = {"shell": isinstance(cmd, str)}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    result = subprocess.run(cmd, **kwargs)
    if check and result.returncode != 0:
        raise RuntimeError(f"Команда завершилась с ошибкой: {cmd}")
    return result


def which(cmd):
    return shutil.which(cmd) is not None


def prompt(question, default=""):
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {question}{suffix}: ").strip()
    return answer if answer else default


def prompt_yn(question, default=True):
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"  {question} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "да")


# ─── Шаги установки ───────────────────────────────────────────────────────────

def check_python():
    print("\n📦 Проверка Python...")
    major, minor = sys.version_info[:2]
    if major < 3 or minor < 8:
        err(f"Требуется Python ≥ 3.8, текущая версия: {major}.{minor}")
        sys.exit(1)
    ok(f"Python {major}.{minor}")


def install_flask():
    print("\n📦 Установка Flask...")
    try:
        import flask
        ok(f"Flask уже установлен ({flask.__version__})")
        return
    except ImportError:
        pass
    run([sys.executable, "-m", "pip", "install", "flask", "--quiet"])
    ok("Flask установлен")


def install_node():
    print("\n📦 Node.js + npm...")
    if which("node") and which("npm"):
        result = run("node --version", capture=True, check=False)
        ok(f"Node.js {result.stdout.strip()} уже установлен")
        return

    info("Node.js не найден — устанавливаю...")
    if IS_MACOS:
        run("brew install node", sudo=False)
    elif IS_DEBIAN:
        run("curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -", sudo=False)
        run(["apt-get", "install", "-y", "nodejs"], sudo=True)
    elif IS_FEDORA:
        run(["dnf", "install", "-y", "nodejs", "npm"], sudo=True)
    elif IS_ARCH:
        run(["pacman", "-S", "--noconfirm", "nodejs", "npm"], sudo=True)
    else:
        warn("Неизвестный дистрибутив. Установите Node.js вручную: https://nodejs.org")
        return
    ok("Node.js установлен")


def install_claude_cli():
    print("\n📦 Claude CLI...")
    if which("claude"):
        ok("Claude CLI уже установлен")
        return
    # Проверяем npm bin в PATH
    npm_bin = os.path.expanduser("~/.npm-global/bin")
    if os.path.isdir(npm_bin) and npm_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = npm_bin + ":" + os.environ["PATH"]
        if which("claude"):
            ok("Claude CLI найден в ~/.npm-global/bin")
            return

    info("Устанавливаю Claude CLI...")
    try:
        run("npm install -g @anthropic-ai/claude-code", sudo=False)
        ok("Claude CLI установлен")
        print()
        warn("Первый запуск требует авторизации: запустите `claude` в терминале")
    except RuntimeError:
        warn("Не удалось установить через npm. Попробуйте: sudo npm install -g @anthropic-ai/claude-code")


def install_nginx():
    print("\n📦 nginx...")
    if IS_MACOS:
        if which("nginx"):
            ok("nginx уже установлен")
        else:
            run("brew install nginx", sudo=False)
            ok("nginx установлен")
    else:
        if which("nginx"):
            ok("nginx уже установлен")
            return
        if IS_DEBIAN:
            run(["apt-get", "install", "-y", "nginx"], sudo=True)
        elif IS_FEDORA:
            run(["dnf", "install", "-y", "nginx"], sudo=True)
        elif IS_ARCH:
            run(["pacman", "-S", "--noconfirm", "nginx"], sudo=True)
        else:
            warn("Установите nginx вручную")
            return
        ok("nginx установлен")


def install_kubectl():
    print("\n📦 kubectl...")
    if which("kubectl"):
        ok("kubectl уже установлен")
        return
    info("Устанавливаю kubectl...")
    if IS_MACOS:
        run("brew install kubectl", sudo=False)
    else:
        # Linux amd64
        arch = platform.machine()
        kube_arch = "amd64" if arch == "x86_64" else "arm64"
        run(
            f"curl -LO 'https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/{kube_arch}/kubectl' "
            f"&& chmod +x kubectl && sudo mv kubectl /usr/local/bin/kubectl",
            sudo=False,
        )
    ok("kubectl установлен")


def install_yc():
    print("\n📦 Yandex Cloud CLI (yc)...")
    if which("yc"):
        ok("yc уже установлен")
        return
    info("Устанавливаю YC CLI...")
    if IS_MACOS:
        run(
            "curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash -s -- -i $HOME/yandex-cloud -n",
            sudo=False,
        )
    else:
        run(
            "curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | bash -s -- -n",
            sudo=False,
        )
    ok("YC CLI установлен")
    warn("Для авторизации выполните: yc init")


def install_v2ray():
    print("\n📦 v2ray...")
    if which("v2ray"):
        ok("v2ray уже установлен")
        return
    info("Устанавливаю v2ray...")
    if IS_MACOS:
        run("brew install v2ray", sudo=False)
    else:
        run(
            "bash <(curl -L https://raw.githubusercontent.com/v2fly/fhs-install-v2ray/master/install-release.sh)",
            sudo=True,
        )
    ok("v2ray установлен")


# ─── Копирование файлов пакета ────────────────────────────────────────────────

def copy_package_files():
    print("\n📁 Копирование файлов пакета...")
    os.makedirs(TERMINATOR_DIR, exist_ok=True)
    os.makedirs(os.path.join(TERMINATOR_DIR, "kube"),  exist_ok=True)
    os.makedirs(os.path.join(TERMINATOR_DIR, "proxy"), exist_ok=True)

    # kube скрипты
    for fname in ("upload-to-bitrix-pods.sh", "run-bitrix-script.sh"):
        src = os.path.join(PACKAGE_DIR, "kube", fname)
        dst = os.path.join(TERMINATOR_DIR, "kube", fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
            ok(f"  kube/{fname}")
        else:
            warn(f"  kube/{fname} — файл не найден в пакете, пропускаю")

    # proxy скрипты
    for fname in ("start_proxy.sh", "stop_proxy.sh", "proxy_client.py"):
        src = os.path.join(PACKAGE_DIR, "proxy", fname)
        dst = os.path.join(TERMINATOR_DIR, "proxy", fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
            ok(f"  proxy/{fname}")
        else:
            warn(f"  proxy/{fname} — файл не найден в пакете, пропускаю")

    # claude-proxy.sh
    src = os.path.join(PACKAGE_DIR, "claude-proxy.sh")
    dst = os.path.join(TERMINATOR_DIR, "claude-proxy.sh")
    if os.path.exists(src):
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        ok("  claude-proxy.sh")

    # agents скрипты
    os.makedirs(os.path.join(TERMINATOR_DIR, "agents"), exist_ok=True)
    for fname in (
        "task_agent.sh",
        "task_agent_prompt.md",
        "test_agent.sh",
        "test_agent_prompt.md",
        "write_tests_agent.sh",
        "write_tests_agent_prompt.md",
        "code_review_agent.sh",
        "code_review_agent_prompt.md",
        "feedback_synth_agent.sh",
        "feedback_synth_agent_prompt.md",
    ):
        src = os.path.join(PACKAGE_DIR, "agents", fname)
        dst = os.path.join(TERMINATOR_DIR, "agents", fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            if fname.endswith(".sh"):
                os.chmod(dst, 0o755)
            ok(f"  agents/{fname}")
        else:
            warn(f"  agents/{fname} — файл не найден в пакете, пропускаю")

    # static файлы (логотипы тем)
    os.makedirs(os.path.join(TERMINATOR_DIR, "static"), exist_ok=True)
    for fname in ("terminator.png", "agent007.png"):
        src = os.path.join(PACKAGE_DIR, "static", fname)
        dst = os.path.join(TERMINATOR_DIR, "static", fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            ok(f"  static/{fname}")
        else:
            warn(f"  static/{fname} — файл не найден в пакете, пропускаю")


# ─── Интерактивный конфиг ─────────────────────────────────────────────────────

def collect_config():
    print("\n⚙️  Настройка конфигурации")
    print("  (Все параметры можно изменить в Settings-вкладке после запуска)\n")

    git_repo = prompt("Git репозиторий", "")
    use_proxy = prompt_yn("Использовать прокси для Claude CLI?", default=True)

    proxy_subscription_url = ""
    if use_proxy:
        proxy_subscription_url = prompt("Subscription URL прокси", "")

    bitrix_rest_url = prompt("Bitrix REST URL", "https://")
    yc_profile      = prompt("YC Profile", "default")
    test_results    = prompt("Результаты тестов", os.path.expanduser("~/Documents/TestResult"))
    code_review     = prompt("Code Review отчёты", os.path.expanduser("~/Documents/CodeReview"))

    scripts_dir = os.path.join(PACKAGE_DIR, "agents")

    cfg = {
        "git_repo":               git_repo,
        "scripts_dir":            scripts_dir,
        "claude_cmd":             "claude",
        "use_proxy":              use_proxy,
        "proxy_http_port":        10809,
        "proxy_socks_port":       10808,
        "proxy_subscription_url": proxy_subscription_url,
        "no_proxy_domains":       "",
        "proxy_start_script":     os.path.join(TERMINATOR_DIR, "proxy/start_proxy.sh"),
        "proxy_stop_script":      os.path.join(TERMINATOR_DIR, "proxy/stop_proxy.sh"),
        "notes_dir":              os.path.join(TERMINATOR_DIR, "notes"),
        "test_results_dir":       test_results,
        "code_review_dir":        code_review,
        "bitrix_rest_url":        bitrix_rest_url,
        "yc_bin":                 "yc",
        "yc_profile":             yc_profile,
        "kube_test_cluster":      "bitrix-testing",
        "kube_prod_cluster":      "bitrix-production",
        "kube_test_pod_pattern":  "bitrix-php-test",
        "kube_prod_pod_pattern":  "bitrix-php-prod",
        "kube_upload_script":     os.path.join(TERMINATOR_DIR, "kube/upload-to-bitrix-pods.sh"),
        "kube_run_script":        os.path.join(TERMINATOR_DIR, "kube/run-bitrix-script.sh"),
        "search_dirs":            "/app/www/api/classes /app/www/api/controllers /app/www/api/scripts /app/www/api/cron /app/www/bitrix/modules /app/www/bitrix/components /app/www/bitrix/js /app/www/js",
        "claude_proxy_cmd":       os.path.join(TERMINATOR_DIR, "claude-proxy.sh"),
        "port":                   SERVICE_PORT,
        "domain":                 DOMAIN,
    }
    return cfg


def save_config(cfg):
    os.makedirs(TERMINATOR_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    ok(f"config.json → {CONFIG_FILE}")
    generate_config_sh(cfg)


def generate_config_sh(cfg):
    """Генерирует task_agent_config.sh из config.json."""
    lines = [
        "# АВТОГЕНЕРИРУЕТСЯ — не редактировать вручную",
        f"# Источник: {CONFIG_FILE}",
        "",
        f'BITRIX_REST_URL="{cfg.get("bitrix_rest_url", "")}"',
        f'NOTES_DIR="{cfg.get("notes_dir", os.path.join(TERMINATOR_DIR, "notes"))}"',
        f'FEEDBACK_FILE="{os.path.join(TERMINATOR_DIR, "launcher_feedback.json")}"',
        f'EXPERIENCE_FILE="{os.path.join(TERMINATOR_DIR, "agent_experience.md")}"',
        f'GIT_REPO="{cfg.get("git_repo", "")}"',
        f'TEST_RESULTS_DIR="{cfg.get("test_results_dir", "")}"',
        f'CODE_REVIEW_DIR="{cfg.get("code_review_dir", "")}"',
        f'CLAUDE_PROXY_CMD="{cfg.get("claude_proxy_cmd", "claude")}"',
        f'USE_PROXY="{str(cfg.get("use_proxy", False)).lower()}"',
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
        f'SEARCH_DIRS="{cfg.get("search_dirs", "")}"',
    ]
    with open(CONFIG_SH, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(CONFIG_SH, 0o644)
    ok(f"task_agent_config.sh → {CONFIG_SH}")


# ─── nginx ────────────────────────────────────────────────────────────────────

NGINX_CONF_CONTENT = f"""\
server {{
    listen 80;
    server_name {DOMAIN};
    location / {{
        proxy_pass http://127.0.0.1:{SERVICE_PORT};
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }}
}}
"""


def setup_nginx(cfg):
    print("\n🌐 nginx...")
    conf_dir = NGINX_CONF_DIR
    conf_path = os.path.join(conf_dir, "terminator.conf")

    os.makedirs(conf_dir, exist_ok=True)

    # Проверяем, нужны ли права sudo
    needs_sudo = IS_LINUX and not os.access(conf_dir, os.W_OK)

    if needs_sudo:
        tmp = "/tmp/terminator.conf"
        with open(tmp, "w") as f:
            f.write(NGINX_CONF_CONTENT)
        run(["cp", tmp, conf_path], sudo=True)
    else:
        with open(conf_path, "w") as f:
            f.write(NGINX_CONF_CONTENT)

    ok(f"nginx конфиг → {conf_path}")

    # SELinux (Fedora/RHEL)
    if IS_FEDORA:
        try:
            run("setsebool -P httpd_can_network_connect 1", sudo=True)
            ok("SELinux: httpd_can_network_connect включён")
        except Exception:
            warn("Не удалось настроить SELinux — выполните вручную: sudo setsebool -P httpd_can_network_connect 1")

    # Перезапускаем nginx
    try:
        if IS_MACOS:
            run("brew services restart nginx", sudo=False)
        else:
            run("systemctl reload nginx", sudo=True)
        ok("nginx перезапущен")
    except Exception:
        warn("Не удалось перезапустить nginx — выполните вручную")


def setup_hosts():
    print("\n🌐 /etc/hosts...")
    hosts_path = "/etc/hosts"
    entry = f"127.0.0.1 {DOMAIN}"

    try:
        with open(hosts_path) as f:
            content = f.read()
    except PermissionError:
        warn(f"Нет доступа к /etc/hosts. Добавьте вручную:")
        print(f"\n    sudo sh -c 'echo \"{entry}\" >> /etc/hosts'\n")
        return

    if DOMAIN in content:
        ok(f"{DOMAIN} уже в /etc/hosts")
        return

    try:
        with open(hosts_path, "a") as f:
            f.write(f"\n{entry}\n")
        ok(f"Добавлено: {entry}")
    except PermissionError:
        warn(f"Настройте /etc/hosts вручную:")
        print(f"\n    sudo sh -c 'echo \"{entry}\" >> /etc/hosts'\n")


# ─── Автозапуск ───────────────────────────────────────────────────────────────

def setup_autostart(cfg):
    print("\n🚀 Настройка автозапуска...")
    python_bin = sys.executable
    terminator_py = os.path.join(PACKAGE_DIR, "terminator.py")
    working_dir = cfg.get("git_repo") or PACKAGE_DIR

    if IS_MACOS:
        _setup_launchagent(python_bin, terminator_py, working_dir)
    else:
        _setup_systemd(python_bin, terminator_py, working_dir)


def _setup_systemd(python_bin, terminator_py, working_dir):
    service_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(service_dir, exist_ok=True)
    service_path = os.path.join(service_dir, "terminator-cursor.service")

    content = textwrap.dedent(f"""\
        [Unit]
        Description=Терминатор Cursor Agent Launcher
        After=network.target

        [Service]
        Type=simple
        ExecStart={python_bin} {terminator_py}
        WorkingDirectory={working_dir}
        Restart=on-failure
        RestartSec=5
        Environment=PYTHONUNBUFFERED=1

        [Install]
        WantedBy=default.target
    """)

    with open(service_path, "w") as f:
        f.write(content)

    try:
        run("systemctl --user daemon-reload", sudo=False)
        run("systemctl --user enable terminator-cursor.service", sudo=False)
        run("systemctl --user start terminator-cursor.service", sudo=False)
        ok(f"systemd сервис запущен: {service_path}")
    except Exception as e:
        warn(f"Не удалось запустить сервис: {e}")
        info(f"Запустите вручную: systemctl --user start terminator-cursor.service")


def _setup_launchagent(python_bin, terminator_py, working_dir):
    agents_dir = os.path.expanduser("~/Library/LaunchAgents")
    os.makedirs(agents_dir, exist_ok=True)
    plist_path = os.path.join(agents_dir, "com.terminator.agent.plist")

    content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
            "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>com.terminator.agent</string>
            <key>ProgramArguments</key>
            <array>
                <string>{python_bin}</string>
                <string>{terminator_py}</string>
            </array>
            <key>WorkingDirectory</key>
            <string>{working_dir}</string>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{os.path.expanduser('~/.terminator/terminator.log')}</string>
            <key>StandardErrorPath</key>
            <string>{os.path.expanduser('~/.terminator/terminator.err')}</string>
        </dict>
        </plist>
    """)

    with open(plist_path, "w") as f:
        f.write(content)

    try:
        run(f"launchctl load {plist_path}", sudo=False)
        ok(f"LaunchAgent загружен: {plist_path}")
    except Exception as e:
        warn(f"Не удалось загрузить LaunchAgent: {e}")
        info(f"Запустите вручную: launchctl load {plist_path}")


# ─── Финальная проверка ───────────────────────────────────────────────────────

def check_proxy_running(cfg):
    http_port = cfg.get("proxy_http_port", 10809)
    result = run(f"lsof -ti:{http_port}", capture=True, check=False)
    return result.returncode == 0


def print_summary(cfg, steps_ok, steps_warn):
    port   = cfg.get("port", SERVICE_PORT)
    domain = cfg.get("domain", DOMAIN)

    print("\n" + "═" * 54)
    print(_color("  ✅  Терминатор установлен!", "32"))
    print("═" * 54)
    print()

    if steps_ok:
        for s in steps_ok:
            print(_color(f"  ✅ {s}", "32"))
    if steps_warn:
        for s in steps_warn:
            print(_color(f"  ⚠️  {s}", "33"))

    print()
    print("  Открыть в браузере:")
    print(_color(f"  👉  http://localhost:{port}", "36"))
    print(_color(f"  👉  http://{domain}  (если настроен nginx + /etc/hosts)", "36"))
    print()
    print("  Следующие шаги:")
    print(f"  1. Откройте http://localhost:{port} или http://{domain}")
    print("  2. Вкладка ⚙️ Настройки — проверьте пути")

    if not which("yc"):
        print("  3. yc init  — авторизация Yandex Cloud")

    if not which("claude"):
        print("  4. claude  — первый логин Claude CLI")
    elif cfg.get("use_proxy") and not check_proxy_running(cfg):
        sub_url = cfg.get("proxy_subscription_url", "")
        if sub_url:
            print(f"  3. Прокси не запущен — кнопка ▶ в Settings или:")
            print(f"     ~/.terminator/proxy/start_proxy.sh --url '{sub_url}'")
        else:
            print("  3. Укажите Subscription URL в Settings и запустите прокси")

    print("═" * 54)


# ─── main ──────────────────────────────────────────────────────────────────────

def main():
    print(_color("\n  🤖  Терминатор Cursor — Установщик", "36"))
    print(f"  ОС: {OS_TYPE} / {DISTRO}")
    print()

    steps_ok   = []
    steps_warn = []

    # --- Базовые проверки ---
    check_python()

    # --- Зависимости ---
    try:
        install_flask()
        steps_ok.append("Flask")
    except Exception as e:
        steps_warn.append(f"Flask: {e}")

    try:
        install_node()
        steps_ok.append("Node.js + npm")
    except Exception as e:
        steps_warn.append(f"Node.js: {e}")

    try:
        install_claude_cli()
        steps_ok.append("Claude CLI")
    except Exception as e:
        steps_warn.append(f"Claude CLI: {e}")

    try:
        install_nginx()
        steps_ok.append("nginx")
    except Exception as e:
        steps_warn.append(f"nginx: {e}")

    try:
        install_kubectl()
        steps_ok.append("kubectl")
    except Exception as e:
        steps_warn.append(f"kubectl: {e}")

    try:
        install_yc()
        steps_ok.append("YC CLI")
    except Exception as e:
        steps_warn.append(f"YC CLI: {e}")

    try:
        install_v2ray()
        steps_ok.append("v2ray")
    except Exception as e:
        steps_warn.append(f"v2ray: {e}")

    # --- Файлы пакета ---
    copy_package_files()

    # --- Конфиг ---
    if os.path.exists(CONFIG_FILE):
        print(f"\n⚙️  config.json уже существует: {CONFIG_FILE}")
        if prompt_yn("Перезаписать конфигурацию?", default=False):
            cfg = collect_config()
            save_config(cfg)
        else:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            info("Используется существующий config.json")
    else:
        cfg = collect_config()
        save_config(cfg)

    # --- nginx ---
    try:
        setup_nginx(cfg)
        steps_ok.append("nginx конфиг")
    except Exception as e:
        steps_warn.append(f"nginx: {e}")
        warn(f"Настройте nginx вручную: {e}")

    # --- /etc/hosts ---
    setup_hosts()

    # --- Автозапуск ---
    try:
        setup_autostart(cfg)
        steps_ok.append("Автозапуск сервиса")
    except Exception as e:
        steps_warn.append(f"Автозапуск: {e}")

    # --- Итог ---
    print_summary(cfg, steps_ok, steps_warn)


if __name__ == "__main__":
    main()
