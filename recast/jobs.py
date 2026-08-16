"""In-memory job manager with a background worker thread pool.

Each uploaded recording becomes a Job that runs the conversion in a worker
thread. Progress is stored on the job and polled by the frontend. Jobs and their
temp files are cleaned up after a retention window.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from .engine import convert

# States: queued -> running -> done | error
RETENTION_SECONDS = 60 * 60  # keep finished jobs (and their MP4) for one hour


@dataclass
class Job:
    id: str
    name: str
    work_dir: str
    input_path: str
    output_path: str
    options: dict = field(default_factory=dict)
    state: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "warnings": self.warnings,
            "download_ready": self.state == "done" and os.path.isfile(self.output_path),
        }


class JobManager:
    def __init__(self, root: Optional[str] = None, max_workers: int = 2):
        self.root = root or os.path.join(tempfile.gettempdir(), "recast-jobs")
        os.makedirs(self.root, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, name: str, options: Optional[dict] = None) -> Job:
        job_id = uuid.uuid4().hex[:12]
        work_dir = os.path.join(self.root, job_id)
        os.makedirs(work_dir, exist_ok=True)
        safe = _safe_stem(name)
        job = Job(
            id=job_id,
            name=name,
            work_dir=work_dir,
            input_path=os.path.join(work_dir, "upload.zip"),
            output_path=os.path.join(work_dir, f"{safe}.mp4"),
            options=options or {},
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def start(self, job: Job) -> None:
        self._pool.submit(self._run, job)

    def get(self, job_id: str) -> Optional[Job]:
        self._sweep()
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job: Job) -> None:
        def progress(msg: str, frac: float) -> None:
            job.message = msg
            job.progress = max(0.0, min(1.0, frac))

        job.state = "running"
        job.message = "Starting"
        try:
            plan = convert(
                job.input_path,
                job.output_path,
                job.work_dir,
                options=job.options,
                progress=progress,
            )
            job.warnings = plan.get("warnings", [])
            job.state = "done"
            job.progress = 1.0
            job.message = "Done"
        except Exception as exc:  # surface a readable error to the UI
            job.state = "error"
            job.error = str(exc)
            job.message = "Conversion failed"
        finally:
            job.finished_at = time.time()
            # The uploaded archive and intermediates are large; keep only the MP4.
            _cleanup_intermediates(job)

    def _sweep(self) -> None:
        now = time.time()
        expired = []
        with self._lock:
            for jid, job in list(self._jobs.items()):
                if job.finished_at and now - job.finished_at > RETENTION_SECONDS:
                    expired.append(jid)
                    del self._jobs[jid]
        for jid in expired:
            shutil.rmtree(os.path.join(self.root, jid), ignore_errors=True)


def _safe_stem(name: str) -> str:
    base = os.path.basename(name or "recording")
    for ext in (".screenstudio.zip", ".zip", ".screenstudio"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    keep = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in base).strip("-")
    return keep or "recording"


def _cleanup_intermediates(job: Job) -> None:
    """Delete everything in the work dir except the finished MP4."""
    keep = os.path.abspath(job.output_path)
    try:
        for entry in os.listdir(job.work_dir):
            path = os.path.join(job.work_dir, entry)
            if os.path.abspath(path) == keep:
                continue
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
    except FileNotFoundError:
        pass
