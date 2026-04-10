#!/usr/bin/env python3
"""
Прокси-клиент для перенаправления всего трафика через прокси
Использует готовые решения: v2ray, gost, или системный прокси
"""

import requests
import re
import subprocess
import os
import sys
import signal
import json
import base64
from urllib.parse import urlparse, parse_qs, unquote
import time
import tempfile


class ProxyClient:
    def __init__(self, config_url, force_refresh=False):
        self.config_url = config_url
        self.force_refresh = force_refresh
        self.local_port = 10808  # Локальный SOCKS5 порт
        self.local_http_port = 10809  # Локальный HTTP порт
        self.v2ray_process = None
        self.proxy_config = None

    def _subscription_cache_path(self):
        return os.path.expanduser('~/.local/share/v2ray-rsp/subscription.cache')

    def _rsp_ready_path(self):
        return os.path.expanduser('~/.local/state/v2ray-rsp/rsp_ready')

    def _clear_rsp_ready(self):
        try:
            p = self._rsp_ready_path()
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass

    def _write_rsp_ready(self):
        """Сигнал для start_proxy.sh: порты слушают и исходящий трафик проверен."""
        try:
            p = self._rsp_ready_path()
            os.makedirs(os.path.dirname(p), mode=0o755, exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                f.write('ok\n')
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        except OSError as e:
            print(f"Предупреждение: не записан rsp_ready: {e}")

    def _save_subscription_cache(self, raw_text):
        """Сохранить сырой ответ подписки — чтобы rsp работал без HTTPS (другой VPN / тот же Genotek)."""
        if not raw_text or not raw_text.strip():
            return
        path = self._subscription_cache_path()
        try:
            os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(raw_text)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            print(f"✓ Кэш подписки обновлён: {path}")
        except OSError as e:
            print(f"Не удалось записать кэш подписки: {e}")
        
    def get_config_from_server(self):
        """Получить конфигурацию с сервера или из локального файла"""
        # Проверяем, является ли URL локальным файлом
        if self.config_url.startswith('file://') or os.path.exists(self.config_url):
            file_path = self.config_url.replace('file://', '') if self.config_url.startswith('file://') else self.config_url
            try:
                print(f"Чтение конфигурации из локального файла: {file_path}")
                with open(file_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                return self._decode_config_text(text)
            except Exception as e:
                print(f"Ошибка чтения локального файла: {e}")
                return None

        # Сначала кэш подписки — без HTTPS (Genotek и др., где URL долго висит)
        if not self.force_refresh:
            path = self._subscription_cache_path()
            if os.path.isfile(path):
                try:
                    st = os.path.getsize(path)
                    if st > 50:
                        with open(path, 'r', encoding='utf-8') as f:
                            text = f.read()
                        if text.strip():
                            print(f"Используется кэш подписки ({st} байт), HTTPS не вызывается.")
                            print("  Обновить с сервера: rsp --refresh")
                            return self._decode_config_text(text)
                except OSError as e:
                    print(f"Кэш не прочитан ({e}), запрашиваем по URL...")
        
        # Пробуем получить конфигурацию с сервера с повторными попытками
        # ВАЖНО: Отключаем использование прокси при получении конфигурации,
        # чтобы избежать циклической зависимости (прокси еще не запущен)
        max_retries = 3
        retry_delay = 2
        
        # Сохраняем текущие переменные прокси (если есть)
        saved_proxy_vars = {}
        proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 
                     'all_proxy', 'ALL_PROXY']
        for var in proxy_vars:
            if var in os.environ:
                saved_proxy_vars[var] = os.environ[var]
                del os.environ[var]  # Временно удаляем, чтобы не использовать прокси
        
        try:
            for attempt in range(max_retries):
                try:
                    session = requests.Session()
                    session.verify = False
                    # Явно отключаем прокси для этой сессии
                    session.proxies = {
                        'http': None,
                        'https': None,
                    }
                    requests.packages.urllib3.disable_warnings()
                    
                    parsed = urlparse(self.config_url)
                    url_clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    
                    if attempt > 0:
                        print(f"Повторная попытка {attempt + 1}/{max_retries}...")
                        time.sleep(retry_delay * attempt)
                    
                    # Увеличиваем таймаут для повторных попыток
                    timeout = 10 + (attempt * 5)
                    response = session.get(url_clean, timeout=timeout, proxies={'http': None, 'https': None})
                    response.raise_for_status()
                    text = response.text
                    decoded = self._decode_config_text(text)
                    self._save_subscription_cache(text)
                    return decoded
                
                except requests.exceptions.ProxyError as e:
                    print(f"Ошибка прокси при получении конфигурации (попытка {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        continue
                    # Если прокси не работает, пробуем без него или через другой способ
                    print("Пробуем альтернативные способы получения конфигурации...")
                    return self._try_alternative_config_sources()
                    
                except requests.exceptions.ConnectionError as e:
                    print(f"Ошибка подключения (попытка {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        continue
                    print("Не удалось подключиться к серверу. Проверьте:")
                    print("  1. Доступность сервера")
                    print("  2. Интернет-соединение")
                    print("  3. Используйте локальный файл: --url file:///path/to/config.txt")
                    return self._try_alternative_config_sources()
                    
                except Exception as e:
                    print(f"Ошибка получения конфигурации (попытка {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        continue
                    print("Пробуем локальный кэш и файлы (режим без доступа к URL)...")
                    return self._try_alternative_config_sources()
        finally:
            # Восстанавливаем переменные прокси
            for var, value in saved_proxy_vars.items():
                os.environ[var] = value
        
        return self._try_alternative_config_sources()
    
    def _decode_config_text(self, text):
        """Декодировать текст конфигурации (base64 и т.д.)"""
        if not text:
            return text
            
        # Пробуем декодировать base64 если нужно
        # Проверяем, начинается ли текст с base64-подобного формата
        if text and not text.startswith('vless://') and not text.startswith('vmess://'):
            try:
                # Пробуем декодировать base64 построчно
                decoded_lines = []
                for line in text.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        try:
                            # Добавляем padding если нужно
                            padding = len(line) % 4
                            if padding:
                                line += '=' * (4 - padding)
                            decoded = base64.b64decode(line).decode('utf-8')
                            decoded_lines.append(decoded)
                        except:
                            decoded_lines.append(line)
                if decoded_lines:
                    text = '\n'.join(decoded_lines)
            except:
                pass
        
        return text
    
    def _try_alternative_config_sources(self):
        """Попробовать альтернативные источники конфигурации"""
        # Сначала ручной override, затем автокэш после успешной загрузки по URL
        local_paths = [
            os.path.expanduser('~/.proxy_config.txt'),
            os.path.expanduser('~/.proxy_config'),
            self._subscription_cache_path(),
            '/tmp/proxy_config.txt',
            os.path.join(os.path.dirname(__file__), 'proxy_config.txt'),
        ]
        
        for path in local_paths:
            if os.path.exists(path):
                try:
                    print(f"Найден локальный файл конфигурации: {path}")
                    with open(path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    return self._decode_config_text(text)
                except Exception as e:
                    print(f"Ошибка чтения {path}: {e}")
                    continue
        
        print("\nСовет: Создайте локальный файл конфигурации:")
        print("  echo 'vless://...' > ~/.proxy_config.txt")
        print("  python3 proxy_client.py --url file://~/.proxy_config.txt")
        print("  Либо один раз запустите rsp при доступном URL — подписка сохранится в:")
        print(f"    {self._subscription_cache_path()}")
        return None
    
    def parse_vless_url(self, url):
        """Парсинг vless:// URL"""
        try:
            # Формат: vless://uuid@host:port?params#remark
            match = re.match(r'vless://([^@]+)@([^:]+):(\d+)(\?[^#]+)?(?:#(.+))?', url)
            if match:
                uuid, host, port, params_str, remark = match.groups()
                params = {}
                if params_str:
                    params = parse_qs(params_str[1:])  # Убираем '?'
                    params = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
                
                return {
                    'protocol': 'vless',
                    'uuid': uuid,
                    'host': host,
                    'port': int(port),
                    'params': params,
                    'remark': remark or ''
                }
        except Exception as e:
            print(f"Ошибка парсинга vless URL: {e}")
        return None
    
    def parse_vmess_url(self, url):
        """Парсинг vmess:// URL"""
        try:
            # Формат: vmess://base64_json
            if url.startswith('vmess://'):
                base64_str = url[8:]
                # Добавляем padding если нужно
                padding = len(base64_str) % 4
                if padding:
                    base64_str += '=' * (4 - padding)
                
                decoded = base64.urlsafe_b64decode(base64_str)
                config = json.loads(decoded)
                
                return {
                    'protocol': 'vmess',
                    'ps': config.get('ps', ''),
                    'add': config.get('add', ''),
                    'port': int(config.get('port', 0)),
                    'id': config.get('id', ''),
                    'aid': int(config.get('aid', 0)),
                    'scy': config.get('scy', 'auto'),
                    'net': config.get('net', 'tcp'),
                    'type': config.get('type', 'none'),
                    'host': config.get('host', ''),
                    'path': config.get('path', ''),
                    'tls': config.get('tls', 'none'),
                    'sni': config.get('sni', ''),
                }
        except Exception as e:
            print(f"Ошибка парсинга vmess URL: {e}")
        return None
    
    def extract_proxy_configs(self, text):
        """Извлечь конфигурации прокси из текста"""
        configs = []
        
        # Декодируем URL-encoded текст
        try:
            decoded_text = unquote(text)
        except:
            decoded_text = text
        
        # Ищем vless:// ссылки (могут быть на отдельных строках)
        vless_pattern = r'vless://[^\s\n]+'
        for match in re.finditer(vless_pattern, decoded_text):
            url = match.group()
            config = self.parse_vless_url(url)
            if config:
                configs.append(config)
        
        # Ищем vmess:// ссылки
        vmess_pattern = r'vmess://[A-Za-z0-9+/=]+'
        for match in re.finditer(vmess_pattern, decoded_text):
            url = match.group()
            config = self.parse_vmess_url(url)
            if config:
                configs.append(config)
        
        # Если не нашли, пробуем поискать по строкам
        if not configs:
            for line in decoded_text.split('\n'):
                line = line.strip()
                if line.startswith('vless://'):
                    config = self.parse_vless_url(line)
                    if config:
                        configs.append(config)
                elif line.startswith('vmess://'):
                    config = self.parse_vmess_url(line)
                    if config:
                        configs.append(config)
        
        return configs
    
    def create_v2ray_config(self, proxy_config):
        """Создать конфигурацию для v2ray"""
        if proxy_config['protocol'] == 'vless':
            return self._create_vless_v2ray_config(proxy_config)
        elif proxy_config['protocol'] == 'vmess':
            return self._create_vmess_v2ray_config(proxy_config)
        return None
    
    def _create_vless_v2ray_config(self, config):
        """Создать v2ray конфигурацию для vless"""
        params = config.get('params', {})
        security = params.get('security', 'tls')
        sni = params.get('sni', config['host'])
        flow = params.get('flow', '')
        
        v2ray_config = {
            "log": {
                "loglevel": "warning"
            },
            "inbounds": [
                {
                    "port": self.local_port,
                    "protocol": "socks",
                    "settings": {
                        "auth": "noauth",
                        "udp": True
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls"]
                    }
                },
                {
                    "port": self.local_http_port,
                    "protocol": "http",
                    "settings": {
                        "allowTransparent": False
                    }
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": config['host'],
                                "port": config['port'],
                                "users": [
                                    {
                                        "id": config['uuid'],
                                        "encryption": "none",
                                        "flow": flow
                                    }
                                ]
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": params.get('type', 'tcp'),
                        "security": security,
                        "tlsSettings": {
                            "serverName": sni,
                            "allowInsecure": True
                        }
                    }
                }
            ]
        }
        
        # Настройка для разных типов сетей
        network = params.get('type', 'tcp')
        if network == 'ws':
            v2ray_config["outbounds"][0]["streamSettings"]["wsSettings"] = {
                "path": params.get('path', '/'),
                "headers": {
                    "Host": params.get('host', config['host'])
                }
            }
        elif network == 'grpc':
            v2ray_config["outbounds"][0]["streamSettings"]["grpcSettings"] = {
                "serviceName": params.get('serviceName', '')
            }
        
        return v2ray_config
    
    def _create_vmess_v2ray_config(self, config):
        """Создать v2ray конфигурацию для vmess"""
        v2ray_config = {
            "log": {
                "loglevel": "warning"
            },
            "inbounds": [
                {
                    "port": self.local_port,
                    "protocol": "socks",
                    "settings": {
                        "auth": "noauth",
                        "udp": True
                    },
                    "sniffing": {
                        "enabled": True,
                        "destOverride": ["http", "tls"]
                    }
                },
                {
                    "port": self.local_http_port,
                    "protocol": "http",
                    "settings": {
                        "allowTransparent": False
                    }
                }
            ],
            "outbounds": [
                {
                    "protocol": "vmess",
                    "settings": {
                        "vnext": [
                            {
                                "address": config['add'],
                                "port": config['port'],
                                "users": [
                                    {
                                        "id": config['id'],
                                        "alterId": config['aid'],
                                        "security": config['scy']
                                    }
                                ]
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": config['net'],
                        "security": config['tls'] if config['tls'] != 'none' else None
                    }
                }
            ]
        }
        
        if config['tls'] != 'none':
            v2ray_config["outbounds"][0]["streamSettings"]["tlsSettings"] = {
                "serverName": config.get('sni', config['add']),
                "allowInsecure": True
            }

        if config['net'] == 'ws':
            v2ray_config["outbounds"][0]["streamSettings"]["wsSettings"] = {
                "path": config.get('path', '/'),
                "headers": {
                    "Host": config.get('host', config['add'])
                }
            }
        elif config['net'] == 'grpc':
            v2ray_config["outbounds"][0]["streamSettings"]["grpcSettings"] = {
                "serviceName": config.get('path', '')
            }
        
        return v2ray_config
    
    def start_v2ray(self, config):
        """Запустить v2ray с конфигурацией"""
        # Создаем временный файл конфигурации
        config_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump(config, config_file, indent=2)
        config_file.close()
        
        try:
            # Проверяем наличие v2ray
            v2ray_path = None
            for path in ['/usr/local/bin/v2ray', '/usr/bin/v2ray', 'v2ray']:
                try:
                    result = subprocess.run(['which', path.split('/')[-1]], 
                                          capture_output=True, text=True)
                    if result.returncode == 0:
                        v2ray_path = result.stdout.strip()
                        break
                except:
                    continue
            
            if not v2ray_path:
                # Пробуем найти v2ray напрямую
                try:
                    result = subprocess.run(['v2ray', 'version'], 
                                 capture_output=True, check=True, timeout=2)
                    v2ray_path = 'v2ray'
                except:
                    # Пробуем без проверки
                    v2ray_path = 'v2ray'
            
            if not v2ray_path:
                print("Ошибка: v2ray не найден. Установите v2ray:")
                print("  curl -L https://raw.githubusercontent.com/v2fly/fhs-install-v2ray/master/install-release.sh | bash")
                return False
            
            print(f"Запуск v2ray на портах SOCKS5:{self.local_port}, HTTP:{self.local_http_port}...")
            
            # Запускаем v2ray
            # v2ray использует формат: v2ray run -config <file>
            self.v2ray_process = subprocess.Popen(
                [v2ray_path, 'run', '-config', config_file.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Ждем немного для запуска
            time.sleep(2)
            
            if self.v2ray_process.poll() is None:
                print(f"✓ v2ray запущен (PID: {self.v2ray_process.pid})")
                print(f"✓ Локальный SOCKS5 прокси: 127.0.0.1:{self.local_port}")
                print(f"✓ Локальный HTTP прокси: 127.0.0.1:{self.local_http_port}")
                return True
            else:
                stdout, stderr = self.v2ray_process.communicate()
                print(f"Ошибка запуска v2ray:")
                print(stderr.decode())
                return False
                
        except Exception as e:
            print(f"Ошибка запуска v2ray: {e}")
            return False
        finally:
            # Удаляем временный файл после запуска
            try:
                os.unlink(config_file.name)
            except:
                pass
    
    def setup_system_proxy(self):
        """Настроить системный прокси"""
        proxy_url = f"http://127.0.0.1:{self.local_http_port}"
        
        print("\n" + "="*60)
        print("НАСТРОЙКА СИСТЕМНОГО ПРОКСИ")
        print("="*60)
        print(f"\nДля браузера (Chrome/Firefox):")
        print(f"  HTTP прокси: {proxy_url}")
        print(f"  SOCKS5 прокси: 127.0.0.1:{self.local_port}")
        
        print(f"\nДля терминала (временно):")
        print(f"  export http_proxy={proxy_url}")
        print(f"  export https_proxy={proxy_url}")
        print(f"  export all_proxy=socks5://127.0.0.1:{self.local_port}")
        
        print(f"\nИли используйте proxychains:")
        print(f"  proxychains curl https://api.ipify.org")
        
        print(f"\nДля проверки IP:")
        print(f"  curl --proxy {proxy_url} https://api.ipify.org")
        print(f"  curl --socks5 127.0.0.1:{self.local_port} https://api.ipify.org")
        
        # Автоматическая настройка через переменные окружения
        os.environ['http_proxy'] = proxy_url
        os.environ['https_proxy'] = proxy_url
        os.environ['all_proxy'] = f'socks5://127.0.0.1:{self.local_port}'
        print(f"\n✓ Переменные окружения установлены для текущей сессии")
    
    def _probe_ip_through_proxy(self, timeout=10):
        """Проверка выхода через локальный HTTP-прокси на нескольких сервисах.
        Порядок: сначала cloudflare/ifconfig — при одном только Genotek до ipify часто не доезжает."""
        proxies = {
            'http': f'http://127.0.0.1:{self.local_http_port}',
            'https': f'http://127.0.0.1:{self.local_http_port}',
        }
        requests.packages.urllib3.disable_warnings()
        try:
            r = requests.get(
                'https://cloudflare.com/cdn-cgi/trace',
                proxies=proxies, timeout=timeout, verify=False)
            if r.status_code == 200:
                for line in r.text.splitlines():
                    if line.startswith('ip='):
                        ip = line.split('=', 1)[1].strip()
                        if ip and re.match(r'^[\d.]+', ip):
                            return ip
        except Exception:
            pass
        try:
            r = requests.get(
                'https://ifconfig.me/ip',
                proxies=proxies, timeout=timeout, verify=False)
            if r.status_code == 200:
                t = (r.text or '').strip()[:128]
                m = re.match(r'^([\d.]+)', t)
                if m:
                    return m.group(1)
        except Exception:
            pass
        try:
            r = requests.get(
                'https://api.ipify.org?format=json',
                proxies=proxies, timeout=timeout, verify=False)
            if r.status_code == 200 and 'ip' in r.json():
                return r.json()['ip']
        except Exception:
            pass
        return None
    
    def check_ip(self):
        """Проверить текущий IP"""
        try:
            print("\nПроверка IP через прокси...")
            ip = self._probe_ip_through_proxy()
            if ip:
                print(f"✓ Ваш IP через прокси: {ip}")
                return ip
            print("Ошибка проверки IP: ни cloudflare trace, ни ifconfig.me, ни ipify не ответили через прокси")
            return None
        except Exception as e:
            print(f"Ошибка проверки IP: {e}")
            return None
    
    def check_ip_silent(self, timeout=10):
        """Проверить текущий IP без вывода сообщений (реальный выход через прокси)."""
        return self._probe_ip_through_proxy(timeout)
    
    def stop(self):
        """Остановить прокси"""
        if self.v2ray_process:
            print("\nОстановка v2ray...")
            self.v2ray_process.terminate()
            try:
                self.v2ray_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.v2ray_process.kill()
            print("✓ v2ray остановлен")
        
        # Убираем переменные окружения
        for var in ['http_proxy', 'https_proxy', 'all_proxy']:
            os.environ.pop(var, None)
    
    def _will_use_subscription_cache(self):
        """Удалённый URL и есть непустой кэш, без --refresh."""
        if self.force_refresh:
            return False
        if self.config_url.startswith('file://') or os.path.exists(self.config_url):
            return False
        path = self._subscription_cache_path()
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 50
        except OSError:
            return False

    def run(self):
        """Запустить прокси-клиент"""
        self._clear_rsp_ready()
        print("Получение конфигурации...")
        if not self.config_url.startswith('file://') and not os.path.exists(self.config_url):
            if self._will_use_subscription_cache():
                print("(есть кэш подписки — запрос по URL не делаем; обновить: --refresh)")
            else:
                print(f"URL: {self.config_url}")
        
        config_text = self.get_config_from_server()
        
        if not config_text:
            print("\n" + "="*60)
            print("ОШИБКА: Не удалось получить конфигурацию")
            print("="*60)
            print("\nВозможные решения:")
            print("  1. Проверьте интернет-соединение")
            print("  2. Проверьте доступность сервера конфигурации")
            print("  3. Используйте локальный файл:")
            print("     python3 proxy_client.py --url file:///path/to/config.txt")
            print("  4. Сохраните конфигурацию в ~/.proxy_config.txt")
            return False
        
        print("✓ Конфигурация получена")
        print("Парсинг конфигураций прокси...")
        configs = self.extract_proxy_configs(config_text)
        
        if not configs:
            print("\n" + "="*60)
            print("ОШИБКА: Не найдено конфигураций прокси")
            print("="*60)
            print("\nПроверьте формат конфигурации.")
            print("Ожидаются ссылки вида:")
            print("  vless://...")
            print("  vmess://...")
            if config_text:
                print(f"\nПолученный текст (первые 200 символов):")
                print(config_text[:200])
            return False
        
        print(f"Найдено конфигураций: {len(configs)}")
        
        # Пробуем разные конфигурации пока не найдем рабочую
        # Пропускаем httpupgrade и другие неподдерживаемые типы
        supported_types = ['tcp', 'ws', 'grpc', 'http']
        working_config = None
        
        # Фильтруем конфигурации по поддерживаемым типам
        valid_configs = []
        for i, proxy_config in enumerate(configs):
            network_type = proxy_config.get('params', {}).get('type', 'tcp')
            if proxy_config['protocol'] == 'vmess':
                network_type = proxy_config.get('net', 'tcp')
            
            # Пропускаем неподдерживаемые типы
            if network_type not in supported_types and network_type != 'httpupgrade':
                continue
            
            if network_type == 'httpupgrade':
                continue
            
            valid_configs.append((i, proxy_config, network_type))
        
        if not valid_configs:
            print("Не найдено поддерживаемых конфигураций")
            return False
        
        print(f"Найдено {len(valid_configs)} поддерживаемых конфигураций")
        
        # Пробуем конфигурации по порядку
        for idx, (original_idx, proxy_config, network_type) in enumerate(valid_configs):
            print(f"\nПробуем конфигурацию {idx+1}/{len(valid_configs)} (оригинальная #{original_idx+1}): {proxy_config.get('remark', proxy_config['protocol'])} (тип: {network_type})")
            
            # Создаем конфигурацию для v2ray
            v2ray_config = self.create_v2ray_config(proxy_config)
            
            if not v2ray_config:
                print("Не удалось создать конфигурацию для v2ray")
                continue
            
            # Запускаем v2ray
            if self.start_v2ray(v2ray_config):
                # Порты могут слушаться, но цепочка до апстрима ещё не готова (Genotek и т.д.)
                print("✓ v2ray слушает локальные порты — проверяем исходящий трафик...")
                egress_ok = False
                skip_eg = os.environ.get('RSP_SKIP_EGRESS', '').strip().lower() in ('1', 'true', 'yes', 'on')
                if skip_eg:
                    egress_ok = True
                    print("RSP_SKIP_EGRESS: внешнюю HTTPS-проверку (ipify/cloudflare) пропускаем.")
                    print("  Нужно, если только Genotek и до проверочных сайтов не доезжает, а прокси реально нужен.")
                    print("  Без этого флага снова полная проверка выхода.")
                else:
                    # Несколько коротких попыток: при Genotek первый выход может задержаться
                    for egress_try in range(1, 7):
                        if egress_try > 1:
                            time.sleep(1.5)
                        if self.check_ip_silent(timeout=10):
                            egress_ok = True
                            print(f"✓ Исходящий трафик через прокси подтверждён (попытка {egress_try}/6)")
                            break
                        print(f"  Выход через прокси пока не отвечает, попытка {egress_try}/6...")
                if not egress_ok:
                    print("  Эта конфигурация не прошла проверку выхода — пробуем следующую.")
                    if self.v2ray_process:
                        self.v2ray_process.terminate()
                        try:
                            self.v2ray_process.wait(timeout=3)
                        except Exception:
                            self.v2ray_process.kill()
                        self.v2ray_process = None
                    continue
                print(f"✓ Конфигурация запущена успешно")
                working_config = proxy_config
                break
            else:
                # Останавливаем неудачный процесс если был запущен
                if self.v2ray_process:
                    self.v2ray_process.terminate()
                    try:
                        self.v2ray_process.wait(timeout=2)
                    except:
                        self.v2ray_process.kill()
                    self.v2ray_process = None
        
        if not working_config:
            print("\n" + "="*60)
            print("ОШИБКА: Ни одна конфигурация не дала рабочий прокси")
            print("="*60)
            print("\nВозможные причины:")
            print("  1. v2ray не установлен или не найден в PATH")
            print("  2. Конфигурации неверны или устарели")
            print("  3. Нет исходящего трафика до сервера прокси (VPN/маршрут) — порты слушаются, но выход не работает")
            print("  4. Повторите rsp через минуту или смените VPN/сеть")
            print("\nПроверьте:")
            print("  - Установлен ли v2ray: v2ray version")
            print("  - Доступность серверов прокси")
            print("  - Корректность конфигураций")
            return False
        
        self._write_rsp_ready()
        
        # Настраиваем системный прокси
        self.setup_system_proxy()
        
        # Проверяем IP
        self.check_ip()
        
        print("\n" + "="*60)
        print("Прокси активен! Нажмите Ctrl+C для остановки")
        print("="*60)
        
        # Обработка сигнала для корректной остановки
        def signal_handler(sig, frame):
            self.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Держим процесс запущенным
        try:
            self.v2ray_process.wait()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Прокси-клиент для перенаправления всего трафика',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Примеры использования:
  # Использовать URL по умолчанию
  python3 proxy_client.py
  
  # Использовать локальный файл
  python3 proxy_client.py --url file:///path/to/config.txt
  
  # Использовать файл напрямую (без file://)
  python3 proxy_client.py --url ~/.proxy_config.txt
  
  # Использовать другой URL
  python3 proxy_client.py --url https://example.com/config

  # Заново скачать подписку (игнорировать ~/.local/share/v2ray-rsp/subscription.cache)
  python3 proxy_client.py --refresh

  # Без проверки выхода на внешние HTTPS (как RSP_SKIP_EGRESS=1) — удобно при одном только Genotek
  python3 proxy_client.py --skip-egress
        ''')
    parser.add_argument('--url',
                       default=os.environ.get('PROXY_SUBSCRIPTION_URL', ''),
                       help='URL конфигурации или путь к локальному файлу (можно использовать file:// или прямой путь)')
    parser.add_argument('--refresh', action='store_true',
                       help='Не использовать кэш подписки, скачать конфиг по URL заново')
    parser.add_argument('--skip-egress', action='store_true',
                       help='Пропустить HTTPS-проверку выхода (или задайте RSP_SKIP_EGRESS=1)')
    parser.add_argument('--socks-port', type=int, default=10808,
                       help='Локальный SOCKS5 порт (по умолчанию: 10808)')
    parser.add_argument('--http-port', type=int, default=10809,
                       help='Локальный HTTP порт (по умолчанию: 10809)')
    
    args = parser.parse_args()
    if args.skip_egress:
        os.environ['RSP_SKIP_EGRESS'] = '1'
    
    client = ProxyClient(args.url, force_refresh=args.refresh)
    client.local_port = args.socks_port
    client.local_http_port = args.http_port
    
    if client.run() is False:
        sys.exit(1)


if __name__ == '__main__':
    main()
