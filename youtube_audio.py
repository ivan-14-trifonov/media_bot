#!/usr/bin/env python3
"""
Скрипт для скачивания аудио с YouTube через yt-dlp.
"""

import argparse
import subprocess
import sys
from pathlib import Path


def download_audio(url: str, output_dir: str = ".", audio_format: str = "mp3") -> None:
    """
    Скачивает аудио из YouTube видео.

    Args:
        url: Ссылка на YouTube видео
        output_dir: Директория для сохранения файла
        audio_format: Формат аудио (mp3, m4a, wav, etc.)
    """
    output_template = Path(output_dir) / "%(title)s.%(ext)s"

    cmd = [
        "yt-dlp",
        "-x",  # Extract audio
        "--audio-format", audio_format,
        "-o", str(output_template),
        url
    ]

    print(f"Скачивание аудио из: {url}")
    print(f"Формат: {audio_format}")
    print(f"Директория: {output_dir}")
    print("-" * 50)

    result = subprocess.run(cmd)

    if result.returncode == 0:
        print("\n✓ Аудио успешно скачано!")
    else:
        print(f"\n✗ Ошибка при скачивании (код {result.returncode})")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Скачать аудио с YouTube"
    )
    parser.add_argument(
        "url",
        help="Ссылка на YouTube видео или плейлист"
    )
    parser.add_argument(
        "-o", "--output",
        default=".",
        help="Директория для сохранения (по умолчанию: текущая)"
    )
    parser.add_argument(
        "-f", "--format",
        default="mp3",
        choices=["mp3", "m4a", "wav", "flac", "opus"],
        help="Формат аудио (по умолчанию: mp3)"
    )

    args = parser.parse_args()

    download_audio(args.url, args.output, args.format)


if __name__ == "__main__":
    main()
