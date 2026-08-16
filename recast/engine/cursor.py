"""Render a transparent cursor-overlay video from a recording's mouse data.

Output: an RGBA video (qtrle/argb) in POINT space (display bounds W x H), CFR at
the display refresh rate, starting at t=0, containing only the animated cursor
sprite and click ripples. The caller overlays it (scaled by the point->pixel
factor) onto the display stream AFTER the fps/CFR fix and BEFORE the zoom pass,
so the cursor rides zooms exactly like the source editor.

Facts this encodes:
  * mouse coordinates are in POINTS (display bounds space), not pixels.
  * t_video = (processTimeMs - processTimeStartMs_of_input_recorder) / 1000.
  * click data holds mouseDown/mouseUp PAIRS; ripples fire on mouseDown only.
  * hotSpot / standardSize in cursors.json are point units; on-screen size is
    standardSize * config.cursorSize.
  * the recorder only logs positions while the mouse moves, so a plain lerp
    between samples is correct even across long gaps.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from typing import Callable, Optional

from PIL import Image, ImageDraw

ProgressCb = Optional[Callable[[str, float], None]]

RIPPLE_DUR, RIPPLE_R0, RIPPLE_DR = 0.45, 8.0, 37.0
RIPPLE_STROKE, RIPPLE_A0 = 3.0, 0.35
HOLD_GAP, LERP_TAIL = 0.5, 0.05


def build_cursor_layer(
    bundle: str,
    work: str,
    *,
    out: Optional[str] = None,
    scale: Optional[float] = None,
    ema: float = 0.35,
    ripples: bool = True,
    ffmpeg: str = "ffmpeg",
    progress: ProgressCb = None,
) -> Optional[str]:
    """Render the cursor overlay video. Returns the output path, or None if the
    recording has no usable mouse-move data (a valid case: cursor simply skipped).
    """
    bundle = bundle.rstrip("/")
    rec = os.path.join(bundle, "recording")
    out = out or os.path.join(work, "cursor_layer.mov")
    os.makedirs(work, exist_ok=True)

    proj = json.load(open(os.path.join(bundle, "project.json")))["json"]
    meta = json.load(open(os.path.join(rec, "metadata.json")))
    chans = {r["id"]: r for r in meta["recorders"]}
    disp = next(r for r in chans.values() if r.get("type") == "display")["sessions"][0]
    inp = next((r for r in chans.values() if r.get("type") == "input"), None)
    sess = inp["sessions"][0] if inp else disp

    off = sess["processTimeStartMs"]
    b = disp.get("bounds") or {}
    W, H = int(b.get("width", 1710)), int(b.get("height", 1107))
    fps = int(round(disp.get("displayRefreshRate") or 60))
    dur = disp.get("durationMs", 0) / 1000.0
    nframes = int(math.ceil(dur * fps)) + 1
    sprite_scale = scale if scale is not None else proj["config"].get("cursorSize", 1.5)

    cmeta = {c["id"]: c for c in json.load(open(os.path.join(rec, "cursors.json")))}
    mm = json.load(
        open(
            os.path.join(rec, sess["mouseMovesFilename"])
            if sess.get("mouseMovesFilename")
            else os.path.join(rec, "mousemoves-0.json")
        )
    )
    mc = json.load(
        open(
            os.path.join(rec, sess["mouseClicksFilename"])
            if sess.get("mouseClicksFilename")
            else os.path.join(rec, "mouseclicks-0.json")
        )
    )

    def t(ms: float) -> float:
        return max(0.0, (ms - off) / 1000.0)

    seen = {e.get("cursorId", "arrow") for e in mm + mc}
    sprites: dict = {}
    for cid in seen | {"arrow"}:
        p = os.path.join(rec, "cursors", f"{cid}.png")
        key = cid if os.path.exists(p) and cid in cmeta else "arrow"
        if key in sprites:
            continue
        im = Image.open(os.path.join(rec, "cursors", f"{key}.png")).convert("RGBA")
        c = cmeta[key]
        th = c["standardSize"]["height"] * sprite_scale
        s = th / im.height
        img = im.resize(
            (max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS
        )
        sprites[key] = {
            "img": img,
            "hx": c["hotSpot"]["x"] * sprite_scale,
            "hy": c["hotSpot"]["y"] * sprite_scale,
        }

    def skey(cid: str) -> str:
        return cid if cid in sprites else "arrow"

    moves = sorted((t(m["processTimeMs"]), float(m["x"]), float(m["y"])) for m in mm)
    cids = sorted((t(e["processTimeMs"]), e.get("cursorId", "arrow")) for e in mm + mc)
    downs = sorted(
        (t(e["processTimeMs"]), float(e["x"]), float(e["y"]))
        for e in mc
        if e.get("type") == "mouseDown"
    )
    if not moves:
        return None

    ts = [m[0] for m in moves]
    xs = [m[1] for m in moves]
    ys = [m[2] for m in moves]
    px = [0.0] * nframes
    py = [0.0] * nframes
    j = 0
    for f in range(nframes):
        tf = f / fps
        if tf <= ts[0]:
            px[f], py[f] = xs[0], ys[0]
            continue
        if tf >= ts[-1]:
            px[f], py[f] = xs[-1], ys[-1]
            continue
        while j + 1 < len(ts) and ts[j + 1] <= tf:
            j += 1
        t0v, t1v = ts[j], ts[j + 1]
        gap = t1v - t0v
        if gap <= HOLD_GAP:
            fr = (tf - t0v) / gap if gap > 0 else 0
        else:
            ls = t1v - LERP_TAIL
            fr = 0.0 if tf <= ls else min(1.0, (tf - ls) / LERP_TAIL)
        px[f] = xs[j] + (xs[j + 1] - xs[j]) * fr
        py[f] = ys[j] + (ys[j + 1] - ys[j]) * fr
    for f in range(1, nframes):
        px[f] = ema * px[f] + (1 - ema) * px[f - 1]
        py[f] = ema * py[f] + (1 - ema) * py[f - 1]

    kts = [c[0] for c in cids]
    kks = [skey(c[1]) for c in cids]
    keys = ["arrow"] * nframes
    j = 0
    for f in range(nframes):
        tf = f / fps
        while j + 1 < len(kts) and kts[j + 1] <= tf:
            j += 1
        keys[f] = kks[j] if kts[j] <= tf else kks[0]

    rip: dict = {}
    if ripples:
        for (tc, cx, cy) in downs:
            for f in range(
                max(0, math.ceil(tc * fps)),
                min(nframes - 1, math.floor((tc + RIPPLE_DUR) * fps)) + 1,
            ):
                p = (f / fps - tc) / RIPPLE_DUR
                if 0 <= p <= 1:
                    rip.setdefault(f, []).append((cx, cy, p))

    def ripple_tile(radius: float, alpha: float):
        ext = radius + RIPPLE_STROKE / 2 + 2
        size = int(math.ceil(2 * ext))
        ssf = 4
        big = Image.new("RGBA", (size * ssf, size * ssf), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        c = size * ssf / 2
        rr = (radius + RIPPLE_STROKE / 2) * ssf
        d.ellipse(
            [c - rr, c - rr, c + rr, c + rr],
            outline=(0, 0, 0, 255),
            width=max(1, round(RIPPLE_STROKE * ssf)),
        )
        tile = big.resize((size, size), Image.LANCZOS)
        r, g, bl, alp = tile.split()
        return (
            Image.merge("RGBA", (r, g, bl, alp.point(lambda v: int(v * alpha)))),
            size / 2,
        )

    def clip_paste(canvas, tile, tx, ty):
        tw, th = tile.size
        ix, iy = int(math.floor(tx)), int(math.floor(ty))
        x0, y0 = max(0, ix), max(0, iy)
        x1, y1 = min(W, ix + tw), min(H, iy + th)
        if x0 >= x1 or y0 >= y1:
            return None
        sub = (
            tile
            if (x0, y0, x1, y1) == (ix, iy, ix + tw, iy + th)
            else tile.crop((x0 - ix, y0 - iy, x1 - ix, y1 - iy))
        )
        canvas.alpha_composite(sub, dest=(x0, y0))
        return (x0, y0, x1, y1)

    def draw(canvas, f, state):
        if state.get("prev"):
            canvas.paste((0, 0, 0, 0), state["prev"])
        boxes = []
        for (cx, cy, p) in rip.get(f, ()):
            tile, offc = ripple_tile(RIPPLE_R0 + RIPPLE_DR * p, RIPPLE_A0 * (1 - p))
            bb = clip_paste(canvas, tile, cx - offc, cy - offc)
            if bb:
                boxes.append(bb)
        sp = sprites[keys[f]]
        bb = clip_paste(canvas, sp["img"], px[f] - sp["hx"], py[f] - sp["hy"])
        if bb:
            boxes.append(bb)
        state["prev"] = (
            (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            if boxes
            else None
        )

    proc = subprocess.Popen(
        [
            ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
            "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
            "-c:v", "qtrle", "-pix_fmt", "argb", out,
        ],
        stdin=subprocess.PIPE,
    )
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    state: dict = {}
    try:
        for f in range(nframes):
            draw(canvas, f, state)
            proc.stdin.write(canvas.tobytes())
            if progress and f % 500 == 0 and nframes:
                progress("Drawing the mouse cursor and click ripples", f / nframes)
    finally:
        proc.stdin.close()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"cursor overlay render failed (ffmpeg exit {rc})")
    return out
