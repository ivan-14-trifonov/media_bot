"""
Job Card Model and Storage
Handles job state persistence and SQLite database operations.
"""

import sqlite3
import json
import uuid
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from enum import Enum
from pathlib import Path


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


@dataclass
class StepCard:
    """Represents a single step in the job pipeline"""
    step_id: str
    step_name: str
    tool: str
    mode: str
    status: StepStatus = StepStatus.PENDING
    input_params: Dict[str, Any] = field(default_factory=dict)
    output_params: Dict[str, Any] = field(default_factory=dict)
    output_files: List[str] = field(default_factory=list)
    validation_result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    preview: Optional[Dict[str, Any]] = None  # duration, size, format info

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            'status': self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StepCard':
        data = data.copy()
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = StepStatus(data['status'])
        return cls(**data)


@dataclass
class JobCard:
    """Represents a complete job with all steps"""
    job_id: str
    goal: str
    input_data: Dict[str, Any]
    expected_output: List[str]
    status: JobStatus = JobStatus.PENDING
    steps: List[StepCard] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    pipeline_config: Dict[str, Any] = field(default_factory=dict)
    manifest_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            'status': self.status.value,
            'steps': [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'JobCard':
        data = data.copy()
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = JobStatus(data['status'])
        if 'steps' in data:
            data['steps'] = [StepCard.from_dict(s) for s in data['steps']]
        return cls(**data)

    def get_current_step_index(self) -> int:
        """Get index of the next step to execute"""
        for i, step in enumerate(self.steps):
            if step.status in (StepStatus.PENDING, StepStatus.RETRYING, StepStatus.RUNNING):
                return i
        return len(self.steps)

    def get_last_completed_step_index(self) -> int:
        """Get index of the last successfully completed step"""
        last_completed = -1
        for i, step in enumerate(self.steps):
            if step.status == StepStatus.COMPLETED:
                last_completed = i
            elif step.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.RETRYING):
                break
        return last_completed

    def can_resume(self) -> bool:
        """Check if job can be resumed from last successful step"""
        if self.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            return False
        return self.get_last_completed_step_index() >= 0


class JobStorage:
    """SQLite-based job storage with resumable support"""

    def __init__(self, db_path: str, outputs_dir: str):
        self.db_path = db_path
        self.outputs_dir = Path(outputs_dir).expanduser()
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    input_data TEXT NOT NULL,
                    expected_output TEXT NOT NULL,
                    status TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_message TEXT,
                    pipeline_config TEXT,
                    manifest_refs TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
            ''')

    def save_job(self, job: JobCard) -> None:
        """Save or update a job card"""
        job.updated_at = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                INSERT OR REPLACE INTO jobs 
                (job_id, goal, input_data, expected_output, status, steps, 
                 created_at, updated_at, completed_at, error_message, 
                 pipeline_config, manifest_refs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                job.job_id,
                job.goal,
                json.dumps(job.input_data),
                json.dumps(job.expected_output),
                job.status.value,
                json.dumps([s.to_dict() for s in job.steps]),
                job.created_at,
                job.updated_at,
                job.completed_at,
                job.error_message,
                json.dumps(job.pipeline_config),
                json.dumps(job.manifest_refs),
            ))

    def get_job(self, job_id: str) -> Optional[JobCard]:
        """Retrieve a job by ID"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                'SELECT * FROM jobs WHERE job_id = ?', (job_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_job(row)

    def _row_to_job(self, row: sqlite3.Row) -> JobCard:
        """Convert database row to JobCard"""
        return JobCard(
            job_id=row['job_id'],
            goal=row['goal'],
            input_data=json.loads(row['input_data']),
            expected_output=json.loads(row['expected_output']),
            status=JobStatus(row['status']),
            steps=[StepCard.from_dict(s) for s in json.loads(row['steps'])],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            completed_at=row['completed_at'],
            error_message=row['error_message'],
            pipeline_config=json.loads(row['pipeline_config'] or '{}'),
            manifest_refs=json.loads(row['manifest_refs'] or '[]'),
        )

    def list_jobs(self, limit: int = 50, status: Optional[JobStatus] = None) -> List[JobCard]:
        """List jobs with optional status filter"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    'SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?',
                    (status.value, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?',
                    (limit,)
                ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def get_resumable_jobs(self) -> List[JobCard]:
        """Get all jobs that can be resumed"""
        jobs = self.list_jobs()
        return [j for j in jobs if j.can_resume() and j.status == JobStatus.RUNNING]

    def delete_job(self, job_id: str) -> bool:
        """Delete a job and its output files"""
        job = self.get_job(job_id)
        if job is None:
            return False
        
        # Delete output files
        job_dir = self.outputs_dir / job_id
        if job_dir.exists():
            import shutil
            shutil.rmtree(job_dir)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('DELETE FROM jobs WHERE job_id = ?', (job_id,))
        return True

    def create_output_dir(self, job_id: str) -> Path:
        """Create output directory for a job"""
        job_dir = self.outputs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def save_output_file(self, job_id: str, filename: str, content: bytes) -> str:
        """Save an output file and return its path"""
        job_dir = self.create_output_dir(job_id)
        file_path = job_dir / filename
        file_path.write_bytes(content)
        return str(file_path)

    def get_output_files(self, job_id: str) -> List[Dict[str, Any]]:
        """List output files for a job"""
        job_dir = self.outputs_dir / job_id
        if not job_dir.exists():
            return []
        
        files = []
        for f in job_dir.iterdir():
            if f.is_file():
                files.append({
                    'name': f.name,
                    'path': str(f),
                    'size': f.stat().st_size,
                    'created': datetime.fromtimestamp(f.stat().st_ctime).isoformat(),
                })
        return files


def create_job(
    goal: str,
    input_data: Dict[str, Any],
    expected_output: List[str],
    steps: Optional[List[StepCard]] = None,
    pipeline_config: Optional[Dict[str, Any]] = None,
    manifest_refs: Optional[List[str]] = None,
) -> JobCard:
    """Factory function to create a new job"""
    return JobCard(
        job_id=str(uuid.uuid4()),
        goal=goal,
        input_data=input_data,
        expected_output=expected_output,
        steps=steps or [],
        pipeline_config=pipeline_config or {},
        manifest_refs=manifest_refs or [],
    )
