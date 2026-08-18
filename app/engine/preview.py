"""Render a single composited preview frame for the current options.

Reuses the full ``prepare()`` pipeline, so the preview matches the real render's
composition and geometry exactly, then grabs just one frame from the output
graph. The cursor is forced off so no separate cursor layer is needed.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from . import prepare as prepare_mod
from .convert import find_binary, resolve_bundle_dir


def render_preview(
    bundle_path: str,
    work_dir: str,
    options: Optional[dict] = None,
    *,
    ffmpeg: Optional[str] = None,
    ffprobe: Optional[str] = None,
) -> str:
    """Render one preview frame and return the path to the JPEG."""
    ffmpeg = ffmpeg or find_binary("ffmpeg")
    ffprobe = ffprobe or find_binary("ffprobe")
    work_dir = os.path.abspath(os.path.expanduser(work_dir))
    os.makedirs(work_dir, exist_ok=True)

    bundle = resolve_bundle_dir(os.path.abspath(os.path.expanduser(bundle_path)), work_dir)
    # Cursor is drawn from a separate layer built only during a full convert;
    # skip it for the preview so a single prepare() pass is enough.
    opts = {**(options or {}), "cursor": "off"}
    prepare_mod.prepare(
        bundle,
        work_dir,
        os.path.join(work_dir, "preview_out.mp4"),
        opts,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
    )

    script = os.path.join(work_dir, "preview.sh")
    res = subprocess.run(["bash", script], capture_output=True, text=True, cwd=work_dir)
    out = os.path.join(work_dir, "preview.jpg")
    if res.returncode != 0 or not os.path.isfile(out):
        err = (res.stderr or res.stdout or "").strip()
        tail = "\n".join(err.splitlines()[-8:])
        raise RuntimeError(f"preview failed:\n{tail}")
    return out
