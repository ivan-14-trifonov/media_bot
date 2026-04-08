#!/usr/bin/env python3
"""
Flask веб-приложение для транскрибирования аудиофайлов через OpenAI Whisper.
"""

import os
import uuid
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, abort
from werkzeug.utils import secure_filename

from transcribe import transcribe_with_progress

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
app.config["RESULT_FOLDER"] = os.path.join(os.path.dirname(__file__), "results")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max

ALLOWED_EXTENSIONS = {"mp3", "wav", "m4a", "ogg", "flac", "aac", "wma", "opus"}

os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["RESULT_FOLDER"], exist_ok=True)

# Хранилище задач: {task_id: {"status": "pending|processing|done|error", "result_path": ..., "error": ...}}
tasks = {}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def process_task(task_id, input_path, output_path, model_name, language, chunk_seconds):
    """Фоновая обработка задачи."""
    try:
        tasks[task_id]["status"] = "processing"
        text = transcribe_with_progress(
            input_path,
            model_name=model_name,
            chunk_seconds=chunk_seconds,
            language=language if language else None,
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        tasks[task_id]["status"] = "done"
        tasks[task_id]["result_path"] = output_path
    except Exception as e:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["error"] = str(e)
    finally:
        # Удаляем входной файл после обработки
        try:
            os.remove(input_path)
        except Exception:
            pass


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "Файл не загружен"}), 400

    file = request.files["audio"]
    if file.filename == "":
        return jsonify({"error": "Файл не выбран"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": f"Неподдерживаемый формат. Допустимые: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

    model_name = request.form.get("model", "small")
    language = request.form.get("language", "").strip() or None
    chunk_seconds = int(request.form.get("chunk_seconds", 30))

    task_id = uuid.uuid4().hex
    original_filename = secure_filename(file.filename)
    ext = Path(original_filename).suffix
    input_filename = f"{task_id}{ext}"
    output_filename = f"{task_id}.txt"

    input_path = os.path.join(app.config["UPLOAD_FOLDER"], input_filename)
    output_path = os.path.join(app.config["RESULT_FOLDER"], output_filename)

    file.save(input_path)

    tasks[task_id] = {
        "status": "pending",
        "result_path": None,
        "error": None,
        "original_filename": Path(original_filename).stem,
    }

    thread = threading.Thread(
        target=process_task,
        args=(task_id, input_path, output_path, model_name, language, chunk_seconds),
    )
    thread.daemon = True
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/status/<task_id>")
def status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({"error": "Задача не найдена"}), 404
    return jsonify({"status": task["status"], "error": task.get("error")})


@app.route("/download/<task_id>")
def download(task_id):
    task = tasks.get(task_id)
    if not task or task["status"] != "done" or not task["result_path"]:
        abort(404)
    return send_file(
        task["result_path"],
        as_attachment=True,
        download_name=f"{task['original_filename']}.txt",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
