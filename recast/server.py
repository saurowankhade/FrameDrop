"""HTTP server for recast.

Serves the single-page frontend and a small JSON API:
  POST /api/convert          upload a recording (.zip), start a job -> {id}
  GET  /api/jobs/{id}         poll job status
  GET  /api/jobs/{id}/download   download the finished MP4
  GET  /api/health           dependency check
"""

from __future__ import annotations

import os
import shutil

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import check_dependencies
from .jobs import JobManager

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

# Max upload size in bytes (default 4 GB; override with RECAST_MAX_UPLOAD).
MAX_UPLOAD = int(os.environ.get("RECAST_MAX_UPLOAD", 4 * 1024 * 1024 * 1024))
MAX_WORKERS = int(os.environ.get("RECAST_MAX_WORKERS", 2))

app = FastAPI(title="recast", docs_url=None, redoc_url=None)
jobs = JobManager(max_workers=MAX_WORKERS)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/health")
def health() -> JSONResponse:
    missing = check_dependencies()
    return JSONResponse({"ok": not missing, "missing": missing})


@app.post("/api/convert")
async def convert_endpoint(
    file: UploadFile,
    cursor: str = Form("auto"),
    zooms: str = Form("on"),
    webcam: str = Form("auto"),
    audio_cleanup: str = Form("loudnorm"),
) -> JSONResponse:
    name = file.filename or "recording.zip"
    options = {
        "cursor": cursor if cursor in ("auto", "on", "off") else "auto",
        "zooms": zooms if zooms in ("on", "off") else "on",
        "webcam": webcam if webcam in ("auto", "on", "off") else "auto",
        "audio_cleanup": audio_cleanup
        if audio_cleanup in ("none", "loudnorm", "voice")
        else "loudnorm",
    }
    job = jobs.create(name, options)

    size = 0
    try:
        with open(job.input_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(status_code=413, detail="Upload is too large.")
                out.write(chunk)
    except HTTPException:
        shutil.rmtree(job.work_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    jobs.start(job)
    return JSONResponse({"id": job.id})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> JSONResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JSONResponse(job.public())


@app.get("/api/jobs/{job_id}/download")
def job_download(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.state != "done" or not os.path.isfile(job.output_path):
        raise HTTPException(status_code=409, detail="The MP4 is not ready.")
    return FileResponse(
        job.output_path,
        media_type="video/mp4",
        filename=os.path.basename(job.output_path),
    )


# Static assets (style.css, app.js). Mounted last so /api and / take priority.
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
