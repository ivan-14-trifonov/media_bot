# Kit Runner

Универсальный запускатор инструментов с автоматическим построением пайплайнов из описания цели на естественном языке.

## Возможности

- **Пайплайны из нескольких инструментов** — опишите цель словами, и система сама построит последовательность шагов (yt-dlp → whisper → ffmpeg и т.д.)
- **LLM-планирование** — LiteLLM подбирает инструменты и параметры; при недоступности LLM работает rule-based фоллбэк
- **Resumable jobs** — прерванную задачу можно продолжить с места остановки
- **Автоматическая установка зависимостей** — команда `install` ставит недостающие инструменты через winget / pip / pipx
- **Прокси-поддержка** — SOCKS5 / HTTP прокси для сетевых инструментов
- **Debug-архивы** — сбор логов и артефактов упавшей задачи одной командой

---

## Быстрый старт

### 1. Установка зависимостей

```bash
# Python-зависимости
pip install pyyaml litellm

# Системные инструменты (нужны для пайплайнов)
# macOS
brew install yt-dlp ffmpeg openai-whisper

# Linux (Debian/Ubuntu)
sudo apt install yt-dlp ffmpeg
pip install openai-whisper

# Windows (через winget)
winget install yt-dlp.yt-dlp Gyan.FFmpeg
pip install openai-whisper
```

### 2. Конфигурация

Отредактируйте `config.yaml`:

```yaml
llm:
  provider: openai
  model: qwen3-max
  api_base: "http://192.168.1.2:3264/v1"   # ваш LLM-эндпоинт
  api_key: "dummy"                          # или задайте через OPENAI_API_KEY
```

Если LLM недоступен — система автоматически использует rule-based фоллбэк.

---

## Запуск

### CLI: Kit Runner

```bash
# Запуск по цели (goal)
python -m runner.main --goal "Скачать аудио с YouTube" \
    --input "url=https://www.youtube.com/watch?v=VIDEO_ID"

# Пошаговое выполнение с подтверждением
python -m runner.main --goal "Транскрибировать видео" \
    --input "url=https://www.youtube.com/watch?v=VIDEO_ID" \
    --step-by-step

# Список доступных инструментов
python -m runner.main --tools

# Список последних задач
python -m runner.main --list

# Возобновление прерванной задачи
python -m runner.main --resume <job_id>

# Debug упавшей задачи
python -m runner.main --debug-job <job_id>
```

#### Основные флаги

| Флаг | Описание |
|------|----------|
| `--goal`, `-g` | Цель на естественном языке |
| `--input`, `-i` | Входной параметр `key=value` (можно несколько) |
| `--step-by-step`, `-s` | Подтверждение каждого шага |
| `--resume`, `-r` | ID задачи для возобновления |
| `--list`, `-l` | Показать последние задачи |
| `--tools`, `-t` | Показать доступные инструменты |
| `--debug-job`, `-d` | Собрать debug-архив по ID задачи |
| `--config`, `-c` | Путь к config.yaml (по умолчанию `./config.yaml`) |

---

### Скрипт: youtube_audio.py

Простой скрипт для скачивания аудио с YouTube без пайплайнов.

```bash
# Скачать аудио в mp3
python youtube_audio.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Указать директорию и формат
python youtube_audio.py "https://www.youtube.com/watch?v=VIDEO_ID" \
    -o ~/Music -f flac

# Доступные форматы: mp3, m4a, wav, flac, opus
```

---

## Доступные инструменты

| Инструмент | Режимы | Описание |
|------------|--------|----------|
| **yt-dlp** | `download`, `subtitles` | Скачивание видео/аудио, субтитров |
| **whisper** | `transcribe` | Транскрибация аудио в текст |
| **ffmpeg** | `convert`, `extract_audio` | Конвертация и обработка медиа |

Манифесты находятся в папке `manifests/` и описывают команды, входы/выходы и правила валидации для каждого режима.

---

## Структура проекта

```
media_bot/
├── config.yaml              # Основная конфигурация
├── runner/                  # Ядро Kit Runner
│   ├── main.py              # CLI entry point
│   ├── executor.py          # Исполнитель шагов с retry
│   ├── pipeline.py          # LLM-планировщик пайплайнов
│   ├── installer.py         # Автоустановка инструментов
│   ├── job.py               # Модель задач и шагов
│   ├── validator.py         # Валидация результатов
│   ├── proxy.py             # Управление прокси
│   └── debug.py             # Сбор debug-архивов
├── manifests/               # Манифесты инструментов
│   ├── yt-dlp.yaml
│   ├── whisper.yaml
│   ├── ffmpeg.yaml
│   └── winget.yaml
├── youtube_audio.py         # Простой скрипт скачивания аудио
├── scenarios/               # Пользовательские сценарии
├── storage/                 # Хранилище задач и результатов
└── ui/                      # Веб-интерфейс (в разработке)
```

---

## Примеры пайплайнов

### Скачать аудио с YouTube

```bash
python -m runner.main \
    --goal "Скачать аудио с YouTube" \
    --input "url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
    --input "format=mp3"
```

### Транскрибировать видео

```bash
python -m runner.main \
    --goal "Транскрибировать видео на русский" \
    --input "url=https://www.youtube.com/watch?v=VIDEO_ID" \
    --input "language=ru"
```

Система автоматически построит пайплайн: `yt-dlp (download) → whisper (transcribe)`.

---

## Хранение данных

По умолчанию все данные хранятся в `~/.kit/`:

```
~/.kit/
├── jobs.db          # База задач
├── outputs/         # Результаты выполнения
└── logs/            # Debug-архивы
```

Пути можно изменить в `config.yaml` в секции `storage`.
