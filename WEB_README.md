# Media Bot Web UI

Веб-интерфейс для скачивания аудио с YouTube.

## Быстрый старт

```bash
docker compose up -d
```

Откройте http://localhost:7700

## Без Docker

```bash
pip install -r web/requirements.txt
pip install yt-dlp
uvicorn web.app:app --host 0.0.0.0 --port 7700
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `KIT_BASE_DIR` | Директория для данных | `/root/.kit` |
| `LLM_API_BASE` | URL LLM API (для генерации пайплайна) | `http://192.168.1.2:3264/v1` |
