"""End-to-end conversion: recording bundle (folder or .zip) -> MP4.

Stages: extract -> prepare -> cursor -> render -> audio -> mux -> verify.
Progress is reported through an optional callback as (message, fraction 0..1).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from typing import Callable, Optional

from . import cursor as cursor_mod
from . import prepare as prepare_mod

ProgressCb = Optional[Callable[[str, float], None]]


def find_binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for loc in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
        if os.path.isfile(loc) and os.access(loc, os.X_OK):
            return loc
    return name


def check_dependencies() -> list[str]:
    """Return human-readable descriptions of any missing dependencies."""
    missing = []
    if find_binary("ffmpeg") == "ffmpeg" and not shutil.which("ffmpeg"):
        missing.append("ffmpeg is not installed or not on PATH")
    if find_binary("ffprobe") == "ffprobe" and not shutil.which("ffprobe"):
        missing.append("ffprobe is not installed or not on PATH")
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow is not installed (pip install pillow)")
    return missing


def _find_project_dir(root: str) -> Optional[str]:
    """Return the directory holding project.json under root (or root itself)."""
    if os.path.isfile(os.path.join(root, "project.json")):
        return root
    for dirpath, _dirnames, filenames in os.walk(root):
        if "project.json" in filenames:
            return dirpath
    return None


def resolve_bundle_dir(bundle_path: str, work_dir: str) -> str:
    """Return a real bundle folder. If given a .zip archive (recordings are
    packages, commonly zipped for upload), extract it and locate the project.
    """
    if os.path.isdir(bundle_path):
        project_dir = _find_project_dir(bundle_path)
        if project_dir is None:
            raise FileNotFoundError("No project.json found in the recording folder.")
        return project_dir

    if not (os.path.isfile(bundle_path) and zipfile.is_zipfile(bundle_path)):
        raise FileNotFoundError(
            "That does not look like a recording. Upload the recording folder "
            "compressed as a .zip."
        )

    extract_root = os.path.join(work_dir, "bundle")
    if os.path.isdir(extract_root):
        shutil.rmtree(extract_root, ignore_errors=True)
    os.makedirs(extract_root, exist_ok=True)
    with zipfile.ZipFile(bundle_path) as zf:
        zf.extractall(extract_root)

    project_dir = _find_project_dir(extract_root)
    if project_dir is None:
        raise FileNotFoundError(
            "The zip was extracted but contains no project.json. Make sure it is a "
            "recording package."
        )
    return project_dir


class Converter:
    """Drive the full recording -> MP4 pipeline for a single job."""

    def __init__(self, bundle_path: str, output_path: str, work_dir: str, options: Optional[dict] = None):
        self.bundle_path = os.path.abspath(os.path.expanduser(bundle_path.rstrip("/")))
        self.output_path = os.path.abspath(os.path.expanduser(output_path))
        self.work_dir = os.path.abspath(os.path.expanduser(work_dir))
        self.options = options or {}
        self.ffmpeg = find_binary("ffmpeg")
        self.ffprobe = find_binary("ffprobe")

    def run(self, progress: ProgressCb = None) -> dict:
        def update(msg: str, pct: float) -> None:
            if progress:
                progress(msg, pct)

        missing = check_dependencies()
        if missing:
            raise RuntimeError("Missing dependencies: " + "; ".join(missing))

        os.makedirs(self.work_dir, exist_ok=True)
        out_dir = os.path.dirname(self.output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        update("Reading your recording", 0.05)
        bundle = resolve_bundle_dir(self.bundle_path, self.work_dir)
        if not os.path.isfile(os.path.join(bundle, "project.json")):
            raise FileNotFoundError("Not a valid recording (missing project.json).")

        update("Preparing layout, zooms, and effects", 0.15)
        plan = prepare_mod.prepare(
            bundle,
            self.work_dir,
            self.output_path,
            self.options,
            ffmpeg=self.ffmpeg,
            ffprobe=self.ffprobe,
        )

        if plan.get("cursor") and self.options.get("cursor", "auto") != "off":
            def cursor_progress(_msg: str, frac: float) -> None:
                update("Drawing the mouse cursor and click ripples", 0.25 + frac * 0.15)

            result = cursor_mod.build_cursor_layer(
                bundle, self.work_dir, ffmpeg=self.ffmpeg, progress=cursor_progress
            )
            if result is None:
                # No usable cursor data; regenerate the plan without the cursor layer.
                opts = {**self.options, "cursor": "off"}
                plan = prepare_mod.prepare(
                    bundle, self.work_dir, self.output_path, opts,
                    ffmpeg=self.ffmpeg, ffprobe=self.ffprobe,
                )

        update("Rendering video (this is the longest step)", 0.55)
        self._run_script(os.path.join(self.work_dir, "render_full.sh"))

        update("Cleaning up and normalizing audio", 0.85)
        self._run_script(os.path.join(self.work_dir, "audio_build.sh"))

        update("Combining video and audio into your MP4", 0.95)
        self._run_script(os.path.join(self.work_dir, "mux.sh"))

        if not os.path.isfile(self.output_path):
            raise RuntimeError("Conversion finished but the output file is missing.")

        update("Done", 1.0)
        plan["output"] = self.output_path
        return plan

    def _run_script(self, script_path: str) -> None:
        if not os.path.isfile(script_path):
            raise FileNotFoundError(f"Missing generated script: {os.path.basename(script_path)}")
        res = subprocess.run(
            ["zsh", script_path],
            capture_output=True,
            text=True,
            cwd=self.work_dir,
        )
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()
            tail = "\n".join(err.splitlines()[-12:])
            raise RuntimeError(f"{os.path.basename(script_path)} failed:\n{tail}")


def convert(bundle_path: str, output_path: str, work_dir: str, options: Optional[dict] = None, progress: ProgressCb = None) -> dict:
    """Convenience wrapper: run a full conversion and return the plan."""
    return Converter(bundle_path, output_path, work_dir, options).run(progress)
