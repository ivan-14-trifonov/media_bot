# Отчёт по задаче: task_003_core_infra (Debug & Proxy Managers)

**Дата:** 2026-02-21  
**Задача:** task_003_core_infra  
**Статус:** ✅ Выполнено

---

## Цель

Реализовать два системных модуля для отказоустойчивости ядра ¡Yoy!:
1. **Proxy Manager** (Принцип 39) — автоматическое управление прокси
2. **Debug Collector** (Принцип 40) — сбор диагностических архивов при падениях

---

## 1. Proxy Manager (`runner/proxy.py`)

### Классы

#### `ProxyConfig`
Конфигурация прокси-сервера.

**Методы:**
- `from_dict(config)` — создание из словаря
- `to_url()` — преобразование в URL
- `_parse_proxy_url(url)` — парсинг URL

**Поддерживаемые типы прокси:**
- `SOCKS5`
- `SOCKS4`
- `HTTP`
- `HTTPS`

**Методы инъекции:**
- `env` — переменные окружения (HTTP_PROXY, HTTPS_PROXY)
- `param` — параметр командной строки (--proxy)
- `system` — системные настройки

#### `ProxyManager`
Управление прокси для инструментов.

**Методы:**
- `is_enabled()` — включён ли прокси
- `is_configured()` — настроен ли прокси
- `check_availability()` — проверка доступности прокси
- `get_env_vars()` — получение переменных окружения
- `get_param(format)` — получение параметра для CLI
- `inject_for_step(manifest, env)` — инъекция для шага pipeline
- `auto_detect_system_proxy()` — автоопределение системного прокси (Windows)
- `get_status()` — статус менеджера

**Проверка доступности:**
- Тестирование через curl (для SOCKS5)
- Raw socket подключение (для HTTP)
- SOCKS5 handshake (базовая поддержка)

### Конфигурация (config.yaml)

```yaml
proxy:
  enabled: true
  socks5: "socks5://127.0.0.1:10808"
  auto_detect: true
  method: env  # env|param|system
```

### Интеграция в executor.py

ProxyManager автоматически.injectит прокси при выполнении шагов:

```python
# В StepExecutor.__init__
self.proxy_manager = proxy_manager or ProxyManager()

# В _execute_command
proxy_env, proxy_params = self.proxy_manager.inject_for_step(manifest, env_vars)
if proxy_params:
    cmd = cmd[:1] + proxy_params + cmd[1:]
```

---

## 2. Debug Collector (`runner/debug.py`)

### Классы

#### `SanitizationRule`
Правило для санитизации чувствительных данных.

```python
SanitizationRule(
    pattern=r'sk-[a-zA-Z0-9]{20,}',
    replacement='[API_KEY_REDACTED]',
    description="OpenAI-style API keys",
    priority=100
)
```

#### `DebugArchive`
Информация о созданном архиве.

**Поля:**
- `path` — путь к ZIP файлу
- `job_id` — ID джобы
- `created_at` — дата создания
- `size_bytes` — размер
- `contents` — список файлов
- `sanitized_items` — количество санитизированных элементов

#### `DebugCollector`
Сбор диагностической информации.

**Методы:**
- `collect_for_job(job, step_index, manifests)` — сбор для JobCard
- `collect_from_execution_result(...)` — сбор из результатов выполнения
- `_sanitize_content(content)` — санитизация контента
- `_collect_system_info()` — сбор системной информации
- `add_sanitization_rule(rule)` — добавление правила
- `get_sanitization_stats()` — статистика правил

### Правила санитизации (по умолчанию)

1. **API ключи** (`sk-...`) → `[API_KEY_REDACTED]`
2. **JWT токены** (`Bearer ...`) → `[TOKEN_REDACTED]`
3. **Пароли в URL** (`://user:pass@host`) → `://[USER]:[PASSWORD_REDACTED]@`
4. **AWS ключи** (`AKIA...`) → `[AWS_KEY_REDACTED]`
5. **Приватные ключи** → `[PRIVATE_KEY_REDACTED]`
6. **Пароли/секреты** (`password=...`) → `password=[REDACTED]`
7. **Email адреса** → `[EMAIL_REDACTED]`

### Состав debug архива

```
debug_<job_id>_<timestamp>.zip
├── job.json                    # Состояние JobCard
├── system_info.json            # ОС, Python, переменные окружения
├── step_000_<tool>/
│   ├── step.json               # Состояние StepCard
│   ├── stdout.log              # Вывод stdout (санитизированный)
│   ├── stderr.log              # Вывод stderr (санитизированный)
│   ├── manifest.yaml           # Манифест инструмента
│   └── return_code.json        # Код возврата
├── outputs/                    # Выходные файлы (опционально)
└── collection_metadata.json    # Метаданные сбора
```

---

## 3. CLI команда `--debug-job`

### Использование

```bash
# Собрать debug архив для упавшей джобы
python -m runner.main --debug-job <job_id>

# С указанием выходной директории
python -m runner.main --debug-job <job_id> --debug-output /path/to/output
```

### Пример вывода

```
Debug archive created:
  Path: C:\Users\ant2\.kit\logs\debug_job-123_20260221_153045.zip
  Size: 0.05 MB
  Contents: 8 files
  Sanitized items: 3

Files in archive:
    job.json
    system_info.json
    step_000_ffmpeg/step.json
    step_000_ffmpeg/stdout.log
    step_000_ffmpeg/stderr.log
    step_000_ffmpeg/manifest.yaml
    step_000_ffmpeg/return_code.json
    collection_metadata.json
```

---

## 4. Интеграция в KitRunner

### Обновлённые файлы

#### `runner/__init__.py`
Экспорт новых модулей:

```python
from .proxy import ProxyManager, ProxyConfig, ProxyMethod, ProxyType, create_proxy_manager
from .debug import DebugCollector, DebugArchive, create_debug_collector, collect_debug_archive

__all__ = [
    # ... existing exports
    # Proxy
    'ProxyManager',
    'ProxyConfig',
    'ProxyMethod',
    'ProxyType',
    'create_proxy_manager',
    # Debug
    'DebugCollector',
    'DebugArchive',
    'create_debug_collector',
    'collect_debug_archive',
]
```

#### `runner/main.py`
Добавлен метод `debug_job()` и CLI аргумент `--debug-job`.

---

## 5. Тесты

### `test_core_infra.py`

**14 тестов:**

| Тест | Описание | Статус |
|------|----------|--------|
| Proxy import | Импорт модуля proxy | ✅ |
| ProxyConfig creation | Создание из dict | ✅ |
| ProxyConfig URL parsing | Парсинг URL | ✅ |
| ProxyConfig to_url | Преобразование в URL | ✅ |
| ProxyManager initialization | Инициализация | ✅ |
| ProxyManager env vars | Инъекция env vars | ✅ |
| ProxyManager param injection | Инъекция параметров | ✅ |
| ProxyManager step injection | Инъекция для шага | ✅ |
| Debug import | Импорт модуля debug | ✅ |
| DebugCollector creation | Создание | ✅ |
| Debug sanitization | Санитизация контента | ✅ |
| DebugArchive creation | Создание архива | ✅ |
| create_proxy_manager | Convenience функция | ✅ |
| create_debug_collector | Convenience функция | ✅ |

**Результат:** 14/14 тестов пройдено ✅

---

## 6. Примеры использования

### Proxy Manager

```python
from runner import ProxyManager

# Создание с конфигурацией
pm = ProxyManager({
    "enabled": True,
    "socks5": "socks5://user:pass@127.0.0.1:10808"
})

# Проверка доступности
if pm.check_availability():
    print("Proxy is reachable")

# Инъекция для шага
manifest = {"tool": "yt-dlp", "proxy": {"method": "env"}}
env_vars, extra_params = pm.inject_for_step(manifest, os.environ.copy())

# Получить статус
status = pm.get_status()
print(f"Proxy: {status['enabled']}, {status['configured']}, {status['available']}")
```

### Debug Collector

```python
from runner import DebugCollector, JobCard

# Создание коллектора
dc = DebugCollector(output_dir="./debug_archives")

# Сбор для упавшей джобы
archive = dc.collect_for_job(failed_job, step_index=2, manifests=manifests)

print(f"Archive: {archive.path}")
print(f"Size: {archive.size_mb} MB")
print(f"Sanitized items: {archive.sanitized_items}")

# Санитизация контента
safe_logs = dc._sanitize_content(raw_logs_with_secrets)
```

### CLI

```bash
# Запуск с прокси
python -m runner.main --goal "Download video" --input "url=..."

# Сбор debug архива после падения
python -m runner.main --debug-job abc12345

# Просмотр доступных команд
python -m runner.main --help
```

---

## 7. Принцип 39: Proxy Manager

**Проблема:** Многие инструменты (yt-dlp, whisper API) требуют прокси для работы в некоторых регионах. Хардкод прокси в коде приводит к проблемам при развёртывании.

**Решение:**
- Централизованное управление прокси в config.yaml
- Автоматическая инъекция на основе манифеста инструмента
- Проверка доступности перед использованием
- Поддержка различных методов инъекции (env, param, system)

---

## 8. Принцип 40: Debug Collector

**Проблема:** При падении 50+ коннекторов невозможно отладить без полной информации: состояние джобы, логи, манифест, ОС.

**Решение:**
- Автоматический сбор диагностической информации
- Санитизация чувствительных данных (токены, пароли, ключи)
- ZIP архив для удобной передачи агентам
- CLI команда для ручного сбора

**Агенты смогут:**
1. Получить архив по команде
2. Проанализировать логи без доступа к системе
3. Предложить исправление на основе полных данных

---

## 9. Deliverables Checklist

- [x] **runner/proxy.py** — Proxy Manager
  - [x] ProxyConfig с парсингом URL
  - [x] ProxyManager с проверкой доступности
  - [x] Инъекция через env vars
  - [x] Инъекция через CLI параметры
  - [x] Автоопределение системного прокси (Windows)
  
- [x] **runner/debug.py** — Debug Collector
  - [x] SanitizationRule для чувствительных данных
  - [x] DebugArchive для представления архива
  - [x] DebugCollector со сбором информации
  - [x] Санитизация логов перед упаковкой
  - [x] 7 правил санитизации по умолчанию
  
- [x] **Интеграция в executor.py**
  - [x] ProxyManager в StepExecutor
  - [x] Автоматическая инъекция для шагов
  
- [x] **CLI команда --debug-job**
  - [x] Аргумент в main.py
  - [x] Метод KitRunner.debug_job()
  - [x] Вывод информации об архиве
  
- [x] **Тесты**
  - [x] 14 тестов для proxy и debug модулей
  - [x] Все тесты проходят

---

## 10. Следующие шаги

**Шаг 2: Запуск Фабрики Инструментов (Ecosystem)**

Для параллельной разработки инструментов создать:
1. `yoy_apps/` директория для приложений
2. Структура приложения:
   - `manifest.yaml` (описание для pipeline.py)
   - `connector.py` (изолированный скрипт)
   - `prompts.yaml` (LLM промпты для нормализации)

**Карточки задач для агентов:**
- `task_005_semantic_layer` — Векторная БД в SQLite
- `task_006_ecosystem` — System Prompt для Агента-Разработчика

---

**Статус:** ✅ Задача task_003_core_infra выполнена полностью
