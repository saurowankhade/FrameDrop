"""Build a faithful ffmpeg render plan from a recording bundle.

Reads project.json / recording/metadata.json / mouse data, then writes into the
work dir:
  bg.png screen_mask.png webcam_mask.png shadow.png     (composite assets)
  filter_full.txt                                       (filtergraph)
  render_full.sh audio_build.sh mux.sh                  (runnable steps)
  plan.json                                             (geometry, zooms, warnings)

Key invariants baked in:
  * Display captures are variable-frame-rate, so the chain ALWAYS starts with
    fps=<fps>,setpts=PTS-STARTPTS before the zoom pass, else video drifts ahead
    of audio in proportion to dropped frames.
  * zoompan (not crop) drives the animated zoom; its clock is the output frame
    index: t = on / fps.
  * The cursor layer (if any) is overlaid AFTER the fps fix and BEFORE the zoom
    pass so it rides zoom/pan exactly like the source editor.
  * Image loop inputs are capped with -t so concat graphs terminate.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter

DEFAULTS = {
    "frame": None,
    "frame_blur": 2.0,
    "frame_darken": 0.03,
    "out_width": 1920,
    "screen_frac": 0.78,
    "webcam": "auto",
    "webcam_margin": 40,
    "zooms": "on",
    "zoom_ease": 0.6,
    "cursor": "auto",
    # Screen content compresses very efficiently, so a faster preset is close to
    # visually identical while cutting render time sharply — the right trade-off
    # for a hosted service where people are waiting on the result.
    "crf": 20,
    "preset": "fast",
    "audio": "auto",
    "audio_cleanup": "loudnorm",
    # User-adjustable presets. "auto" for camera/screen means "use the value from
    # the recording's project"; the named sizes override it.
    "camera_size": "auto",
    "camera_roundness": "auto",
    "screen_size": "auto",
    "quality": "balanced",
}

# How the named option values map onto the numeric render parameters.
CAMERA_SIZES = {"small": 0.16, "medium": 0.24, "large": 0.32}
CAMERA_ROUNDNESS = {"square": 0.0, "rounded": 0.35, "circle": 1.0}
SCREEN_SIZES = {"small": 0.70, "medium": 0.80, "large": 0.90}
QUALITY = {
    "high": {"crf": 18, "preset": "medium"},
    "balanced": {"crf": 20, "preset": "fast"},
    "small": {"crf": 26, "preset": "fast"},
}


def _even(x: float) -> int:
    return int(round(x)) - (int(round(x)) % 2)


def _probe_video(path: str, ffprobe: str = "ffprobe") -> dict:
    out = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
            "-show_entries", "format=duration", "-of", "json", path,
        ],
        capture_output=True,
        text=True,
    ).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return {
        "w": s["width"],
        "h": s["height"],
        "r": s["r_frame_rate"],
        "avg": s["avg_frame_rate"],
        "dur": float(j["format"]["duration"]),
    }


def prepare(
    bundle: str,
    work: str,
    output: str,
    options: Optional[dict] = None,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict:
    """Generate render assets and shell scripts. Returns the plan dict."""
    a = {**DEFAULTS, **(options or {})}
    # Apply named presets onto the numeric render parameters.
    if a.get("quality") in QUALITY:
        a.update(QUALITY[a["quality"]])
    if a.get("screen_size") in SCREEN_SIZES:
        a["screen_frac"] = SCREEN_SIZES[a["screen_size"]]
    bundle = bundle.rstrip("/")
    rec = os.path.join(bundle, "recording")
    os.makedirs(work, exist_ok=True)
    warnings: list = []

    proj = json.load(open(os.path.join(bundle, "project.json")))["json"]
    cfg = proj["config"]
    scene = proj["scenes"][0]
    if len(proj["scenes"]) > 1:
        warnings.append("multiple scenes; rendering scenes[0] only")
    meta = json.load(open(os.path.join(rec, "metadata.json")))
    chans = {r["id"]: r for r in meta["recorders"]}

    disp_id = next(i for i, r in chans.items() if r.get("type") == "display")
    disp_sess = chans[disp_id]["sessions"][0]
    display = os.path.join(rec, disp_id + "-0.m3u8")
    dp = _probe_video(display, ffprobe)
    SRC_W, SRC_H = dp["w"], dp["h"]
    num, _, den = dp["r"].partition("/")
    FPS = int(round(int(num) / int(den or 1)))
    FPS = int(round(disp_sess.get("displayRefreshRate") or FPS)) or 60
    DUR = disp_sess.get("durationMs", dp["dur"] * 1000) / 1000.0
    bounds = disp_sess.get("bounds") or {"width": SRC_W, "height": SRC_H}
    PT2PX = SRC_W / bounds["width"]

    t0_ms = None
    for r in chans.values():
        if r.get("type") == "input":
            t0_ms = r["sessions"][0]["processTimeStartMs"]
    if t0_ms is None:
        t0_ms = disp_sess["processTimeStartMs"]

    rc = cfg.get("recordingCrop") or {"x": 0, "y": 0, "width": 1, "height": 1}
    CRX, CRY = int(rc["x"] * SRC_W), int(rc["y"] * SRC_H)
    CRW, CRH = _even(rc["width"] * SRC_W), _even(rc["height"] * SRC_H)
    cropped = (CRX, CRY, CRW, CRH) != (0, 0, SRC_W, SRC_H)

    cam_id = next(
        (i for i, r in chans.items() if r.get("type") == "camera" or "webcam" in i),
        None,
    )
    use_cam = (a["webcam"] == "on") or (
        a["webcam"] == "auto" and cam_id and not cfg.get("hideCamera")
    )
    webcam = os.path.join(rec, cam_id + "-0.m3u8") if cam_id else None
    if use_cam and not cam_id:
        warnings.append("webcam requested but no channel found; disabled")
        use_cam = False

    mic_id = next((i for i, r in chans.items() if r.get("type") == "microphone"), None)
    sys_id = next((i for i, r in chans.items() if r.get("type") == "systemAudio"), None)
    mic = os.path.join(rec, mic_id + "-0.m4a") if mic_id else None
    sysa = os.path.join(rec, sys_id + "-0.m4a") if sys_id else None
    enhanced = None
    enh_dir = os.path.join(rec, "enhanced")
    if os.path.isdir(enh_dir):
        for f in sorted(os.listdir(enh_dir)):
            if f.endswith((".m4a", ".wav", ".mp3")):
                enhanced = os.path.join(enh_dir, f)
    audio_mode = a["audio"]
    if audio_mode == "auto":
        audio_mode = "enhanced" if enhanced else "mic"
    if audio_mode == "enhanced" and not enhanced:
        warnings.append("no enhanced track; falling back to mic")
        audio_mode = "mic"
    voice_src = {"mic": mic, "enhanced": enhanced, "mic+system": mic}[audio_mode]

    use_cursor = (a["cursor"] == "on") or (
        a["cursor"] == "auto"
        and not cfg.get("hideCursor")
        and os.path.exists(os.path.join(rec, "mousemoves-0.json"))
    )

    OUT_W = _even(a["out_width"])
    OUT_H = _even(OUT_W * CRH / CRW)
    Sw = _even(OUT_W * a["screen_frac"])
    Sh = _even(Sw * CRH / CRW)
    OX, OY = _even((OUT_W - Sw) / 2), _even((OUT_H - Sh) / 2)
    Rs = max(
        int(round(cfg.get("windowBorderRadius", 12) * Sw / bounds["width"])),
        int(round(Sw * 0.014)),
    )
    sc = Sw / bounds["width"]
    SH_BLUR = cfg.get("shadowBlur", 20) * sc
    SH_ANG = math.radians(cfg.get("shadowAngle", 90))
    SH_DIST = cfg.get("shadowDistance", 25) * sc
    SH_DX, SH_DY = int(round(math.cos(SH_ANG) * SH_DIST)), int(
        round(math.sin(SH_ANG) * SH_DIST)
    )
    SH_A = 0.45 * cfg.get("shadowIntensity", 1)

    cam_size = (cfg.get("defaultLayout") or {}).get("cameraSize") or cfg.get(
        "cameraSize", 0.25
    )
    if a["camera_size"] in CAMERA_SIZES:
        cam_size = CAMERA_SIZES[a["camera_size"]]
    Cw = _even(OUT_W * cam_size)
    Ch = _even(Cw * 9 / 16)
    roundness = cfg.get("cameraRoundness", 0.2)
    if a["camera_roundness"] in CAMERA_ROUNDNESS:
        roundness = CAMERA_ROUNDNESS[a["camera_roundness"]]
    Rc = int(round(roundness * min(Cw, Ch) / 2))
    pp = cfg.get("cameraPositionPoint") or {"x": 1, "y": 1}
    M = a["webcam_margin"]
    CamX = M if pp["x"] == 0 else (OUT_W - Cw - M if pp["x"] == 1 else _even((OUT_W - Cw) / 2))
    CamY = M if pp["y"] == 0 else (OUT_H - Ch - M if pp["y"] == 1 else _even((OUT_H - Ch) / 2))

    slices = scene.get("slices") or [
        {"sourceStartMs": 0, "sourceEndMs": DUR * 1000, "timeScale": 1}
    ]
    for s in slices:
        if s.get("timeScale", 1) != 1:
            warnings.append(f"slice timeScale={s['timeScale']} unsupported; treated as 1.0")
    seg = [(s["sourceStartMs"] / 1000.0, s["sourceEndMs"] / 1000.0) for s in slices]
    out_dur = sum(b - c for c, b in seg)

    def rounded(w, h, r):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
        return m

    rounded(Sw, Sh, Rs).save(f"{work}/screen_mask.png")
    if use_cam:
        rounded(Cw, Ch, Rc).save(f"{work}/webcam_mask.png")
    sh_img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    ImageDraw.Draw(sh_img).rounded_rectangle(
        [OX + SH_DX, OY + SH_DY, OX + SH_DX + Sw - 1, OY + SH_DY + Sh - 1],
        radius=Rs,
        fill=(0, 0, 0, int(255 * SH_A)),
    )
    sh_img.filter(ImageFilter.GaussianBlur(SH_BLUR)).save(f"{work}/shadow.png")

    if a["frame"]:
        vf = (
            f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H}"
        )
        if a["frame_blur"] > 0:
            vf += f",gblur=sigma={a['frame_blur']}"
        vf += f",eq=brightness=-{a['frame_darken']}:saturation=1.05"
        subprocess.run(
            [ffmpeg, "-hide_banner", "-y", "-i", a["frame"], "-vf", vf,
             "-frames:v", "1", "-update", "1", f"{work}/bg.png"],
            check=True,
        )
        bg_desc = f"frame image {a['frame']}"
    else:
        g = cfg.get("backgroundGradient")
        if g and g.get("stops"):
            c0 = g["stops"][0]["color"].lstrip("#")
            c1 = g["stops"][-1]["color"].lstrip("#")
            x0, y0 = g["start"]["x"] * OUT_W, g["start"]["y"] * OUT_H
            x1, y1 = g["end"]["x"] * OUT_W, g["end"]["y"] * OUT_H
            src = (
                f"gradients=s={OUT_W}x{OUT_H}:c0=0x{c0}:c1=0x{c1}:"
                f"x0={x0:.0f}:y0={y0:.0f}:x1={x1:.0f}:y1={y1:.0f}:type=linear:d=1"
            )
            bg_desc = f"project gradient #{c0}->#{c1}"
        else:
            col = (cfg.get("backgroundColor") or "#222222").lstrip("#")
            src = f"color=c=0x{col}:s={OUT_W}x{OUT_H}:d=1"
            bg_desc = f"solid #{col}"
        subprocess.run(
            [ffmpeg, "-hide_banner", "-y", "-f", "lavfi", "-i", src,
             "-frames:v", "1", "-update", "1", f"{work}/bg.png"],
            check=True,
        )
        if cfg.get("backgroundType") == "system":
            warnings.append(
                f"project uses system wallpaper '{cfg.get('backgroundSystemName')}' "
                f"(not in bundle); used {bg_desc}."
            )

    clicks = []
    mc = os.path.join(rec, "mouseclicks-0.json")
    if os.path.exists(mc):
        clicks = json.load(open(mc))
    zooms = (
        [z for z in scene.get("zoomRanges", []) if not z.get("isDisabled")]
        if a["zooms"] == "on"
        else []
    )
    zrows = []
    T = f"(on/{FPS})"
    DE = a["zoom_ease"]

    def ss(arg):
        u = f"clip({arg},0,1)"
        return f"({u}*{u}*(3-2*{u}))"

    if zooms:
        e, tx, ty = [], [], []
        for z in zooms:
            za, zb, zf = z["startTime"] / 1000, z["endTime"] / 1000, z["zoom"]
            if z["type"] == "manual":
                cx = z["manualTargetPoint"]["x"] * SRC_W - CRX
                cy = z["manualTargetPoint"]["y"] * SRC_H - CRY
                n = -1
            else:
                pts = [
                    c
                    for c in clicks
                    if za - 0.2 <= (c["processTimeMs"] - t0_ms) / 1000 <= zb + 0.2
                ]
                if pts:
                    cx = sum(c["x"] for c in pts) / len(pts) * PT2PX - CRX
                    cy = sum(c["y"] for c in pts) / len(pts) * PT2PX - CRY
                else:
                    cx, cy = CRW / 2, CRH / 2
                n = len(pts)
            e.append(
                f"between({T},{za:.4f},{zb:.4f})*{zf-1:.3f}*"
                f"min({ss(f'({T}-{za:.4f})/{DE}')},{ss(f'({zb:.4f}-{T})/{DE}')})"
            )
            tx.append(f"between({T},{za:.4f},{zb:.4f})*{cx:.1f}")
            ty.append(f"between({T},{za:.4f},{zb:.4f})*{cy:.1f}")
            zrows.append(
                {
                    "id": z["id"], "start": za, "end": zb, "zoom": zf,
                    "tx": round(cx), "ty": round(cy), "nclicks": n,
                }
            )
        Z = "(1+(" + "+".join(e) + "))"
        TX = "(" + "+".join(tx) + ")"
        TY = "(" + "+".join(ty) + ")"
        zoomf = (
            f"zoompan=z='{Z}':x='clip(({TX})-(iw/{Z})/2,0,iw-iw/{Z})':"
            f"y='clip(({TY})-(ih/{Z})/2,0,ih-ih/{Z})':d=1:s={Sw}x{Sh}:fps={FPS}"
        )
    else:
        zoomf = f"scale={Sw}:{Sh}:flags=lanczos"

    inputs = [("display", display)]
    if use_cam:
        inputs.append(("webcam", webcam))
    img_start = len(inputs)
    imgs = ["bg.png", "shadow.png", "screen_mask.png"] + (
        ["webcam_mask.png"] if use_cam else []
    )
    for im in imgs:
        inputs.append(("img", f"{work}/{im}"))
    cursor_idx = None
    if use_cursor:
        cursor_idx = len(inputs)
        inputs.append(("cursor", f"{work}/cursor_layer.mov"))
    iBG, iSH, iSM = img_start, img_start + 1, img_start + 2
    iCM = img_start + 3 if use_cam else None
    iCAM = 1 if use_cam else None

    pre = f"crop={CRW}:{CRH}:{CRX}:{CRY}," if cropped else ""
    head = []
    if use_cursor:
        head.append(
            f"[{cursor_idx}:v]scale={_even(bounds['width']*PT2PX)}:"
            f"{_even(bounds['height']*PT2PX)}[curs]"
        )
        head.append(f"[0:v]{pre}fps={FPS},setpts=PTS-STARTPTS[vfix]")
        ox = -CRX if cropped else 0
        oy = -CRY if cropped else 0
        head.append(f"[vfix][curs]overlay={ox}:{oy}:eof_action=repeat:format=auto[vcur]")
        head.append(f"[vcur]{zoomf},setsar=1[scr]")
    else:
        head.append(
            f"[0:v]{pre}fps={FPS},setpts=PTS-STARTPTS,{zoomf},setsar=1[scr]"
        )

    comp = [
        f"[{iSM}:v]format=gray[sm]",
        "[scr][sm]alphamerge[scrA]",
        f"[{iBG}:v]scale={OUT_W}:{OUT_H},setsar=1[bg]",
        f"[bg][{iSH}:v]overlay=0:0[bg2]",
        f"[bg2][scrA]overlay={OX}:{OY}[c0]",
    ]
    last = "c0"
    if use_cam:
        comp += [
            f"[{iCAM}:v]scale={Cw}:{Ch}:flags=lanczos,setsar=1[cam]",
            f"[{iCM}:v]format=gray[cm]",
            "[cam][cm]alphamerge[camA]",
            f"[c0][camA]overlay={CamX}:{CamY}[comp]",
        ]
        last = "comp"

    cut = [f"[{last}]split={len(seg)}" + "".join(f"[s{i}]" for i in range(len(seg)))]
    for i, (c, b) in enumerate(seg):
        cut.append(f"[s{i}]trim={c:.6f}:{b:.6f},setpts=PTS-STARTPTS[v{i}]")
    cut.append(
        "".join(f"[v{i}]" for i in range(len(seg)))
        + f"concat=n={len(seg)}:v=1:a=0[vout]"
    )

    fg = ";\n".join(head + comp + cut)
    open(f"{work}/filter_full.txt", "w").write(fg)

    cap = int(DUR) + 5

    def input_args() -> str:
        parts = []
        for kind, path in inputs:
            if kind == "img":
                parts.append(f'-loop 1 -framerate {FPS} -t {cap} -i "{path}"')
            else:
                parts.append(f'-i "{path}"')
        return " \\\n  ".join(parts)

    open(f"{work}/render_full.sh", "w").write(
        f"""#!/bin/bash
set -e
cd "{work}"
{ffmpeg} -hide_banner -y \\
  {input_args()} \\
  -/filter_complex filter_full.txt \\
  -map "[vout]" -an \\
  -c:v libx264 -preset {a['preset']} -crf {a['crf']} -pix_fmt yuv420p -profile:v high \\
  -fps_mode cfr -r {FPS} -movflags +faststart \\
  video_only.mp4
echo RENDER_EXIT=$?
"""
    )

    # A single composited frame from the same graph, used for the live layout
    # preview. Grabbing one frame is fast; it shows background, screen size,
    # rounded corners, shadow, and the webcam bubble at its size/roundness.
    open(f"{work}/preview.sh", "w").write(
        f"""#!/bin/bash
set -e
cd "{work}"
{ffmpeg} -hide_banner -y \\
  {input_args()} \\
  -/filter_complex filter_full.txt \\
  -map "[vout]" -an -frames:v 1 -update 1 -q:v 3 preview.jpg
echo PREVIEW_EXIT=$?
"""
    )

    CLEAN = {
        "none": "",
        "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
        "voice": (
            "highpass=f=80,equalizer=f=130:t=q:w=1.1:g=-5,"
            "equalizer=f=250:t=q:w=1.3:g=-3,afftdn=nf=-28:nr=10,"
            "equalizer=f=3500:t=q:w=1.6:g=2,loudnorm=I=-16:TP=-1.5:LRA=11"
        ),
    }[a["audio_cleanup"]]
    asrc2 = f'-i "{sysa}"' if audio_mode == "mic+system" else ""
    n = len(seg)
    achain = [f"[0:a]asplit={n}" + "".join(f"[x{i}]" for i in range(n))]
    for i, (c, b) in enumerate(seg):
        achain.append(f"[x{i}]atrim={c:.6f}:{b:.6f},asetpts=PTS-STARTPTS[a{i}]")
    achain.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cut]")
    if audio_mode == "mic+system":
        achain[0] = (
            f"[0:a][1:a]amix=inputs=2:duration=first[mx];[mx]asplit={n}"
            + "".join(f"[x{i}]" for i in range(n))
        )
    tail = f"[cut]{CLEAN}[out]" if CLEAN else "[cut]anull[out]"
    achain.append(tail)
    open(f"{work}/audio_build.sh", "w").write(
        f"""#!/bin/bash
set -e
cd "{work}"
{ffmpeg} -hide_banner -y -i "{voice_src}" {asrc2} \\
  -filter_complex "{';'.join(achain)}" \\
  -map "[out]" -c:a aac -b:a 192k voice.m4a
echo AUDIO_EXIT=$?
"""
    )
    open(f"{work}/mux.sh", "w").write(
        f"""#!/bin/bash
set -e
cd "{work}"
{ffmpeg} -hide_banner -y -i video_only.mp4 -i voice.m4a \\
  -map 0:v:0 -map 1:a:0 -c copy -movflags +faststart -shortest "{output}"
echo MUX_EXIT=$?  OUTPUT="{output}"
"""
    )
    for f in ("render_full.sh", "audio_build.sh", "mux.sh", "preview.sh"):
        os.chmod(f"{work}/{f}", 0o755)

    plan = {
        "output": output,
        "work": work,
        "source": {
            "px": [SRC_W, SRC_H],
            "pt": [bounds["width"], bounds["height"]],
            "pt2px": PT2PX,
            "fps": FPS,
            "dur_s": round(DUR, 3),
            "vfr": dp["r"] != dp["avg"],
        },
        "composition": {
            "out": [OUT_W, OUT_H],
            "screen": [Sw, Sh, OX, OY, Rs],
            "webcam": [Cw, Ch, CamX, CamY, Rc] if use_cam else None,
            "background": bg_desc,
        },
        "audio": {"mode": audio_mode, "cleanup": a["audio_cleanup"], "src": voice_src},
        "cursor": use_cursor,
        "zooms": zrows,
        "slices": seg,
        "out_dur_s": round(out_dur, 2),
        "warnings": warnings,
    }
    json.dump(plan, open(f"{work}/plan.json", "w"), indent=2)
    return plan
