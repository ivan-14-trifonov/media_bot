"""
Web UI for YouTube audio downloader.
FastAPI application that wraps the runner infrastructure.
"""

import os
import sys
import uuid
import shutil
import asyncio
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from runner.job import JobStorage, JobStatus, StepStatus, create_job
from runner.executor import StepExecutor, ExecutionStatus
from runner.pipeline import PipelineBuilder

app = FastAPI(title="Media Bot - YouTube Audio Downloader")

# Paths
BASE_DIR = Path(os.environ.get("KIT_BASE_DIR", "~/.kit")).expanduser()
DB_PATH = BASE_DIR / "jobs.db"
OUTPUTS_DIR = BASE_DIR / "outputs"
MANIFESTS_DIR = project_root / "manifests"

# In-memory task tracking
tasks: dict = {}


def load_manifests() -> dict:
    """Load all manifests from directory."""
    manifests = {}
    if MANIFESTS_DIR.exists():
        for manifest_file in MANIFESTS_DIR.glob("*.yaml"):
            try:
                import yaml
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = yaml.safe_load(f)
                    tool_name = manifest.get("tool", manifest_file.stem)
                    manifests[tool_name] = manifest
            except Exception:
                pass
    return manifests


def get_storage() -> JobStorage:
    """Get job storage instance."""
    return JobStorage(str(DB_PATH), str(OUTPUTS_DIR))


def get_executor() -> StepExecutor:
    """Get step executor instance."""
    manifests = load_manifests()
    return StepExecutor(
        max_retries=3,
        base_delay=1.0,
        max_delay=30.0,
        timeout=3600,
        manifests=manifests,
    )


def download_audio(task_id: str, url: str):
    """Background task: download audio from YouTube URL."""
    manifests = load_manifests()
    storage = get_storage()
    executor = get_executor()

    # Build pipeline for audio download
    pipeline_builder = PipelineBuilder(
        manifests_dir=str(MANIFESTS_DIR),
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        api_key_value="dummy",
        api_base=os.environ.get("LLM_API_BASE", "http://192.168.1.2:3264/v1"),
        detect_shortcuts=True,
    )

    goal = "Скачать аудио с YouTube"
    input_data = {"url": url}

    try:
        plan = pipeline_builder.build_pipeline(goal, input_data)
        job = pipeline_builder.create_job_from_plan(goal, input_data, plan)
        # Override job_id with task_id for easy tracking
        job.job_id = task_id

        storage.save_job(job)
        job_dir = storage.create_output_dir(task_id)

        # Execute steps
        job.status = JobStatus.RUNNING
        storage.save_job(job)

        previous_outputs = {}
        for i, step in enumerate(job.steps):
            step.status = StepStatus.RUNNING
            step.started_at = datetime.now(timezone.utc).isoformat()
            storage.save_job(job)

            result = executor.execute_step(step, job_dir, previous_outputs, input_data)
            step.completed_at = datetime.now(timezone.utc).isoformat()
            step.duration_seconds = result.duration_seconds

            if result.status == ExecutionStatus.SUCCESS:
                step.status = StepStatus.COMPLETED
                step.output_params = result.output_params
                step.output_files = result.output_files
                for key, value in result.output_params.items():
                    previous_outputs[key] = value
            else:
                step.status = StepStatus.FAILED
                step.error_message = result.error_message
                job.status = JobStatus.FAILED
                job.error_message = result.error_message
                storage.save_job(job)
                tasks[task_id] = {
                    "status": "failed",
                    "error": result.error_message,
                    "job_id": task_id,
                }
                return

            storage.save_job(job)

        # Check completion
        all_completed = all(s.status == StepStatus.COMPLETED for s in job.steps)
        if all_completed:
            job.status = JobStatus.COMPLETED
            job.completed_at = datetime.now(timezone.utc).isoformat()
            storage.save_job(job)

            # Find the output file
            output_file = None
            for step in job.steps:
                if step.output_files:
                    output_file = step.output_files[0]
                    break

            tasks[task_id] = {
                "status": "completed",
                "job_id": task_id,
                "output_file": output_file,
                "output_filename": Path(output_file).name if output_file else None,
            }
        else:
            job.status = JobStatus.FAILED
            storage.save_job(job)
            tasks[task_id] = {
                "status": "failed",
                "error": "Some steps failed",
                "job_id": task_id,
            }

    except Exception as e:
        tasks[task_id] = {
            "status": "failed",
            "error": str(e),
            "job_id": task_id,
        }


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main page."""
    static_path = Path(__file__).parent / "static"
    index_file = static_path / "index.html"
    return FileResponse(str(index_file))


@app.post("/download")
async def start_download(url: str = Form(...)):
    """Start a new audio download task."""
    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {"status": "pending", "job_id": task_id}

    # Run download in background
    asyncio.create_task(asyncio.to_thread(download_audio, task_id, url))

    return JSONResponse({"task_id": task_id})


@app.get("/status/{task_id}")
async def get_status(task_id: str):
    """Get task status."""
    task = tasks.get(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return JSONResponse(task)


@app.get("/download/{task_id}")
async def download_file(task_id: str):
    """Download the completed file."""
    task = tasks.get(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    if task.get("status") != "completed":
        return JSONResponse({"error": "File not ready"}, status_code=400)

    output_file = task.get("output_file")
    if not output_file or not Path(output_file).exists():
        return JSONResponse({"error": "File not found"}, status_code=404)

    filename = task.get("output_filename", "audio.mp3")
    return FileResponse(
        path=output_file,
        filename=filename,
        media_type="audio/mpeg",
    )


@app.get("/tasks")
async def list_tasks():
    """List all tasks."""
    return JSONResponse(tasks)


# Mount static files
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
