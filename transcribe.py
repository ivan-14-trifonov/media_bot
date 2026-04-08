#!/usr/bin/env python3
"""
transcribe.py
Простой CLI для транскрибирования mp3 в .txt с использованием OpenAI Whisper (локально).
Если пакет "whisper" не установлен — выдаст подсказку как установить.
"""

import argparse
import os
import sys
import shutil
import warnings
import subprocess
# Подавляем предупреждение о FP16 на CPU, которое генерирует whisper
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")
import math
import tempfile
import time


def ensure_ffmpeg_available():
    """Ищет ffmpeg в PATH, npm префиксах и стандартных местах. Если найден — возвращает полный путь к бинару.
    Если не найден — возвращает None и главный код должен выдать понятную ошибку.
    """
    # 1) пробуем стандартный PATH
    p = shutil.which("ffmpeg")
    if p:
        return p

    # 2) ищем в популярных системных местах
    common = ["/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/bin/ffmpeg"]
    for c in common:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c

    # 3) попробуем npm префиксы и ~/.npm-global
    candidates = []
    candidates.append(os.path.join(os.getcwd(), "node_modules", ".bin", "ffmpeg"))
    npm_prefix = None
    try:
        npm_prefix = subprocess.check_output(["npm", "prefix", "-g"], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        npm_prefix = None

    if npm_prefix:
        candidates.append(os.path.join(npm_prefix, "bin", "ffmpeg"))

    # ~/.npm-global/bin
    candidates.append(os.path.expanduser("~/.npm-global/bin/ffmpeg"))

    for p in candidates:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            # добавим папку в PATH, чтобы subprocess мог найти ffmpeg без полного пути
            dirp = os.path.dirname(p)
            os.environ["PATH"] = dirp + os.pathsep + os.environ.get("PATH", "")
            return p

    # не найден
    raise RuntimeError(
        "ffmpeg не найден в PATH. Установите его через npm (@ffmpeg-installer) в пользовательский префикс или добавьте системный ffmpeg в PATH.\n"
        "Пример установки (npm-only, без sudo):\n"
        "  mkdir -p ~/.npm-global\n"
        "  npm config set prefix '~/.npm-global'\n"
        "  export PATH=\"$HOME/.npm-global/bin:$PATH\"\n"
        "  echo 'export PATH=\"$HOME/.npm-global/bin:$PATH\"' >> ~/.zshrc\n"
        "  npm install -g @ffmpeg-installer/ffmpeg\n"
    )


def transcribe_local(input_path, model_name="small", language=None):
    try:
        import whisper
    except Exception as e:
        raise RuntimeError(
            "Пакет 'whisper' не найден. Установите его: pip install -r requirements.txt и убедитесь, что ffmpeg доступен."
        )
    # Убедимся, что ffmpeg доступен
    ffmpeg_path = ensure_ffmpeg_available()

    # Подавляем предупреждение о том, что FP16 не поддерживается на CPU — это не фатальная ошибка
    warnings.filterwarnings(
        "ignore", message="FP16 is not supported on CPU; using FP32 instead"
    )

    # Выберем устройство явно: CUDA если доступно, иначе CPU
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"

    model = whisper.load_model(model_name, device=device)
    # Явно отключаем FP16 на CPU — приводим модель к float32
    if device == "cpu":
        try:
            import torch
            model.to(torch.float32)
        except Exception:
            try:
                model.float()
            except Exception:
                pass

    # По умолчанию используем прямую транскрипцию одного файла
    # Вызов из main может попросить чанковую транскрипцию с прогрессом
    kwargs = {}
    if language:
        kwargs["language"] = language
    result = model.transcribe(input_path, **kwargs)
    return result.get("text", "")


def transcribe_with_progress(input_path, model_name="small", chunk_seconds=30, language=None):
    """Разбивает аудио на чанки через ffmpeg, транскрибирует по-чанково и печатает прогресс в процентах."""
    try:
        import whisper
    except Exception:
        raise RuntimeError("Пакет 'whisper' не найден. Установите его: pip install -r requirements.txt")

    ffmpeg_path = ensure_ffmpeg_available()

    # узнаём длительность через ffprobe
    try:
        dur_s = float(
            subprocess.check_output([
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                input_path,
            ], stderr=subprocess.DEVNULL, text=True).strip()
        )
    except Exception as e:
        raise RuntimeError(f"Не удалось определить длительность файла через ffprobe: {e}")

    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"

    model = whisper.load_model(model_name, device=device)
    if device == "cpu":
        try:
            import torch
            model.to(torch.float32)
        except Exception:
            try:
                model.float()
            except Exception:
                pass

    # создаём временную директорию
    full_text = []
    chunk_seconds = max(1, int(chunk_seconds))
    chunk_count = math.ceil(dur_s / chunk_seconds)

    kwargs = {}
    if language:
        kwargs["language"] = language

    with tempfile.TemporaryDirectory() as tmpdir:
        processed = 0.0
        for i in range(chunk_count):
            start = i * chunk_seconds
            length = min(chunk_seconds, max(0, dur_s - start))
            out_file = os.path.join(tmpdir, f"chunk_{i:04d}.wav")

            ffmpeg_cmd = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(start),
                "-t",
                str(length),
                "-i",
                input_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-y",
                out_file,
            ]

            try:
                subprocess.check_call(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                raise RuntimeError(f"ffmpeg failed to create chunk {i}: {e}")

            # транскрибируем чанк
            res = model.transcribe(out_file, **kwargs)
            text = res.get("text", "")
            full_text.append(text)

            processed += length
            percent = min(100.0, processed / dur_s * 100.0) if dur_s > 0 else 100.0
            print(f"Progress: {percent:.1f}% ({i+1}/{chunk_count})", end="\r", flush=True)
            # небольшая пауза, чтобы прогресс читался в консоли
            time.sleep(0.01)

        print()  # завершение строки прогресса

    return "\n".join(full_text)


def main():
    parser = argparse.ArgumentParser(description="Транскрибирует mp3 в .txt (через локальную модель Whisper)")
    parser.add_argument("input", help="Входной mp3-файл")
    parser.add_argument("-o", "--output", help="Файл вывода (.txt). По умолчанию: тот же путь с расширением .txt")
    parser.add_argument("-m", "--model", default="small", help="Имя модели Whisper (tiny, base, small, medium, large)")
    parser.add_argument("-l", "--language", default=None, help="Язык аудио (например, ru для русского, en для английского). По умолчанию определяется автоматически.")
    parser.add_argument("--progress", action="store_true", help="Показывать прогресс транскрипции в процентах (разбивает аудио на чанки)")
    parser.add_argument("--chunk-seconds", type=int, default=30, help="Длина чанка в секундах при использовании --progress")
    args = parser.parse_args()

    input_path = args.input
    if not os.path.isfile(input_path):
        print(f"Файл не найден: {input_path}")
        sys.exit(2)

    out_path = args.output or os.path.splitext(input_path)[0] + ".txt"

    try:
        if args.progress:
            text = transcribe_with_progress(input_path, model_name=args.model, chunk_seconds=args.chunk_seconds, language=args.language)
        else:
            text = transcribe_local(input_path, model_name=args.model, language=args.language)
    except RuntimeError as e:
        print(str(e))
        sys.exit(1)
    except Exception as e:
        print(f"Ошибка при транскрибировании: {e}")
        sys.exit(1)

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print(f"Не удалось записать файл: {e}")
        sys.exit(1)

    print(f"Готово. Транскрипт сохранён в: {out_path}")


if __name__ == "__main__":
    main()
