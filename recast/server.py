"""HTTP server for FrameDrop.

Serves the single-page frontend and a small JSON API:
  POST /api/convert          upload a recording (.zip), start a job -> {id}
  GET  /api/jobs/{id}         poll job status
  GET  /api/jobs/{id}/download   download the finished MP4
  GET  /api/health           dependency check
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger("framedrop")

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

def _env(name: str, default: str) -> str:
    """Read FRAMEDROP_<name>, falling back to the legacy RECAST_<name>."""
    return os.environ.get(f"FRAMEDROP_{name}") or os.environ.get(f"RECAST_{name}", default)


# Public site URL, used for canonical links, Open Graph tags, sitemap, and
# llms.txt. Set FRAMEDROP_SITE_URL to your real domain in production.
SITE_URL = _env("SITE_URL", "https://framedrop.devtools4me.com").rstrip("/")

# Max upload size in bytes (default 4 GB; override with FRAMEDROP_MAX_UPLOAD).
MAX_UPLOAD = int(_env("MAX_UPLOAD", str(4 * 1024 * 1024 * 1024)))
MAX_WORKERS = int(_env("MAX_WORKERS", "2"))

app = FastAPI(title="FrameDrop", docs_url=None, redoc_url=None)
jobs = JobManager(max_workers=MAX_WORKERS)


def _render(filename: str) -> str:
    """Read a web asset and substitute the {{SITE_URL}} placeholder."""
    with open(os.path.join(WEB_DIR, filename), encoding="utf-8") as f:
        return f.read().replace("{{SITE_URL}}", SITE_URL)


def _asset_version() -> str:
    """A cache-busting token derived from the static assets' modification time,
    so browsers refetch app.js / style.css whenever they change."""
    latest = 0.0
    for name in ("app.js", "style.css"):
        try:
            latest = max(latest, os.path.getmtime(os.path.join(WEB_DIR, name)))
        except OSError:
            pass
    return str(int(latest))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = _render("index.html").replace("{{ASSET_VER}}", _asset_version())
    return HTMLResponse(html)


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


def _pick(value: str, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def parse_options(
    cursor: str,
    zooms: str,
    webcam: str,
    audio_cleanup: str,
    camera_size: str,
    camera_roundness: str,
    screen_size: str,
    quality: str,
) -> dict:
    """Validate raw option values into a clean options dict for the engine."""
    return {
        "cursor": _pick(cursor, ("auto", "on", "off"), "auto"),
        "zooms": _pick(zooms, ("on", "off"), "on"),
        "webcam": _pick(webcam, ("auto", "on", "off"), "auto"),
        "audio_cleanup": _pick(audio_cleanup, ("none", "loudnorm", "voice"), "loudnorm"),
        "camera_size": _pick(camera_size, ("auto", "small", "medium", "large"), "auto"),
        "camera_roundness": _pick(
            camera_roundness, ("auto", "square", "rounded", "circle"), "auto"
        ),
        "screen_size": _pick(screen_size, ("auto", "small", "medium", "large"), "auto"),
        "quality": _pick(quality, ("high", "balanced", "small"), "balanced"),
    }


@app.post("/api/upload")
async def upload_endpoint(file: UploadFile) -> JSONResponse:
    """Receive a recording .zip and hold it for preview + conversion."""
    name = file.filename or "recording.zip"
    job = jobs.create(name)

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

    return JSONResponse({"id": job.id})


@app.get("/api/preview/{job_id}")
def preview_endpoint(
    job_id: str,
    cursor: str = "auto",
    zooms: str = "on",
    webcam: str = "auto",
    audio_cleanup: str = "loudnorm",
    camera_size: str = "auto",
    camera_roundness: str = "auto",
    screen_size: str = "auto",
    quality: str = "balanced",
) -> FileResponse:
    """Render and return a single preview frame for the current options."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    options = parse_options(
        cursor, zooms, webcam, audio_cleanup,
        camera_size, camera_roundness, screen_size, quality,
    )
    try:
        image_path = jobs.preview(job, options)
    except Exception as exc:  # surface a readable error to the UI
        # Log the full traceback server-side (Render logs) so the cause is
        # visible without having to read the 422 response body.
        logger.exception("preview failed for job %s", job_id)
        raise HTTPException(status_code=422, detail=str(exc))
    return FileResponse(
        image_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/convert")
def convert_endpoint(
    job_id: str = Form(...),
    cursor: str = Form("auto"),
    zooms: str = Form("on"),
    webcam: str = Form("auto"),
    audio_cleanup: str = Form("loudnorm"),
    camera_size: str = Form("auto"),
    camera_roundness: str = Form("auto"),
    screen_size: str = Form("auto"),
    quality: str = Form("balanced"),
) -> JSONResponse:
    """Start converting a previously uploaded recording with the given options."""
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Upload not found. Please re-upload.")
    if job.state != "queued":
        raise HTTPException(status_code=409, detail="This recording is already converting.")
    job.options = parse_options(
        cursor, zooms, webcam, audio_cleanup,
        camera_size, camera_roundness, screen_size, quality,
    )
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
