"""HTTP server for FrameDrop.

Serves the single-page frontend and a small JSON API:
  POST /api/convert          upload a recording (.zip), start a job -> {id}
  GET  /api/jobs/{id}         poll job status
  GET  /api/jobs/{id}/download   download the finished MP4
  GET  /api/health           dependency check
"""

from __future__ import annotations

import os
import shutil

try:  # Load variables from a local .env file if python-dotenv is available.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .engine import check_dependencies
from .jobs import JobManager

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

# Public site URL, used for canonical links, Open Graph tags, sitemap, and
# llms.txt. Set RECAST_SITE_URL to your real domain in production.
SITE_URL = os.environ.get("RECAST_SITE_URL", "https://framedrop.app").rstrip("/")

# Max upload size in bytes (default 4 GB; override with RECAST_MAX_UPLOAD).
MAX_UPLOAD = int(os.environ.get("RECAST_MAX_UPLOAD", 4 * 1024 * 1024 * 1024))
MAX_WORKERS = int(os.environ.get("RECAST_MAX_WORKERS", 2))

app = FastAPI(title="FrameDrop", docs_url=None, redoc_url=None)
jobs = JobManager(max_workers=MAX_WORKERS)


def _render(filename: str) -> str:
    """Read a web asset and substitute the {{SITE_URL}} placeholder."""
    with open(os.path.join(WEB_DIR, filename), encoding="utf-8") as f:
        return f.read().replace("{{SITE_URL}}", SITE_URL)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_render("index.html"))


@app.get("/robots.txt", response_class=Response)
def robots() -> Response:
    return Response(_render("robots.txt"), media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
def sitemap() -> Response:
    return Response(_render("sitemap.xml"), media_type="application/xml")


@app.get("/llms.txt", response_class=Response)
def llms() -> Response:
    return Response(_render("llms.txt"), media_type="text/plain")


@app.get("/manifest.webmanifest", response_class=Response)
def manifest() -> Response:
    return Response(_render("manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/favicon.svg", response_class=Response)
def favicon() -> Response:
    with open(os.path.join(WEB_DIR, "favicon.svg"), encoding="utf-8") as f:
        return Response(f.read(), media_type="image/svg+xml")


@app.get("/og-image.png")
def og_image() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "og-image.png"), media_type="image/png")


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
