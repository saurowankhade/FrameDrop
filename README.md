# recast

Turn a screen recording into a shareable MP4, from a simple web page.

recast is a small, self-hostable website. A user uploads their recording (a
`.screenstudio` package, zipped), and the server renders a faithful MP4 —
background, rounded window, shadow, animated zooms, cursor and click ripples,
webcam bubble, and normalized audio — then hands back a download.

- **One page, no build step.** Plain HTML/CSS/JS frontend, FastAPI backend.
- **Faithful render.** Reproduces the editor's composition and zoom timing.
- **Minimal, theme-aware UI.** No accounts, no tracking.
- **Open source (MIT).**

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe` on your `PATH`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `apt install ffmpeg`

## Run locally

```bash
pip install -r requirements.txt
python run.py
# open http://localhost:8000
```

## Run with Docker

```bash
docker build -t recast .
docker run --rm -p 8000:8000 recast
# open http://localhost:8000
```

The image installs `ffmpeg` for you.

## How a user makes the upload

A recording is a folder that macOS shows as a single file, so it must be zipped
before a browser can upload it:

1. Find the recording in Finder (it ends in `.screenstudio`).
2. Right-click it → **Compress**.
3. Drop the resulting `.zip` onto the page.

## API

| Method | Path                        | Purpose                                  |
| ------ | --------------------------- | ---------------------------------------- |
| POST   | `/api/convert`              | Upload a `.zip`, start a job → `{ id }`  |
| GET    | `/api/jobs/{id}`            | Poll job status and progress             |
| GET    | `/api/jobs/{id}/download`   | Download the finished MP4                |
| GET    | `/api/health`               | Dependency check                         |

## Configuration

| Env var               | Default | Meaning                                   |
| --------------------- | ------- | ----------------------------------------- |
| `RECAST_HOST`         | `127.0.0.1` | Bind address (`0.0.0.0` in Docker)    |
| `RECAST_PORT`         | `8000`  | Port                                      |
| `RECAST_MAX_UPLOAD`   | `4 GiB` | Max upload size in bytes                  |
| `RECAST_MAX_WORKERS`  | `2`     | Concurrent conversions                    |

## How it works

```
upload .zip → extract → prepare (assets + filtergraph)
            → cursor overlay → render video → build audio → mux → MP4
```

Rendering happens with `ffmpeg`; composition assets (masks, shadow, background)
are drawn with Pillow. Uploaded archives and intermediates are deleted after a
job finishes; the MP4 is kept for one hour and then swept.

## Notes and limits

- The first scene of a project is rendered. Speed ramps (`timeScale ≠ 1`) are
  treated as `1.0`.
- macOS "system wallpaper" backgrounds aren't shipped inside a recording, so the
  project gradient/color is used instead.
- Large recordings mean large uploads. For a public deployment, put a size limit
  and a reverse proxy in front, and scale `RECAST_MAX_WORKERS` to your CPU.

## License

MIT — see [LICENSE](LICENSE).
