# FrameDrop: Free ScreenStudio to MP4 Converter (Open Source)

**FrameDrop** is a free, open-source, self-hostable web app that converts a
**ScreenStudio (`.screenstudio`) recording into an MP4**. It adds your
background, rounded window, drop shadow, auto-zoom, cursor and click ripples,
webcam bubble, and normalized audio. No account, no watermark, no tracking.

> Upload a `.zip` of your `.screenstudio` recording and download a rendered MP4.

- **One page, no build step.** Plain HTML/CSS/JS frontend, FastAPI backend.
- **Faithful render.** Reproduces the editor's composition and zoom timing.
- **Privacy-friendly.** Uploads are processed server-side and deleted after conversion.
- **SEO-ready.** Ships `robots.txt`, `sitemap.xml`, `llms.txt`, a web manifest,
  Open Graph / Twitter cards, and JSON-LD structured data.
- **Open source (MIT).**

## Table of contents

- [Requirements](#requirements)
- [Quick start (clone & install)](#quick-start-clone--install)
- [Run with Docker](#run-with-docker)
- [Configuration](#configuration)
- [SEO](#seo)
- [How a user makes the upload](#how-a-user-makes-the-upload)
- [API](#api)
- [How it works](#how-it-works)
- [Notes and limits](#notes-and-limits)
- [License](#license)

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe` on your `PATH`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `apt install ffmpeg`

## Quick start (clone & install)

```bash
# 1. Clone the repository (replace with your fork/repo URL)
git clone https://github.com/youruser/framedrop.git
cd framedrop

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment (optional but recommended)
cp .env.example .env               # then edit .env and set RECAST_SITE_URL

# 5. Run it
python run.py
# open http://localhost:8000
```

Variables in `.env` are loaded automatically. See [Configuration](#configuration).

## Run with Docker

```bash
docker build -t framedrop .
docker run --rm -p 8000:8000 \
  -e RECAST_SITE_URL=https://yourdomain.com \
  framedrop
# open http://localhost:8000
```

The image installs `ffmpeg` for you.

## Configuration

Copy `.env.example` to `.env` and adjust. All variables are optional; defaults
are shown below.

| Env var              | Default                 | Meaning                                                        |
| -------------------- | ----------------------- | -------------------------------------------------------------- |
| `RECAST_HOST`        | `127.0.0.1`             | Bind address (`0.0.0.0` to expose / in Docker).                |
| `RECAST_PORT`        | `8000`                  | Port to listen on.                                             |
| `RECAST_SITE_URL`    | `https://framedrop.app` | Public URL (no trailing slash) for canonical/OG/sitemap/llms.  |
| `RECAST_MAX_UPLOAD`  | `4294967296` (4 GiB)    | Max upload size in bytes.                                       |
| `RECAST_MAX_WORKERS` | `2`                     | Concurrent conversions. Scale to your CPU cores.               |

> **Set `RECAST_SITE_URL` to your real domain in production.** It is substituted
> into `robots.txt`, `sitemap.xml`, `llms.txt`, and every canonical / Open Graph
> tag so search engines and social previews use the correct absolute URLs.

## SEO

FrameDrop is search- and share-ready out of the box. The following are served at
the site root with correct content types:

| Path                    | What it is                                                        |
| ----------------------- | ---------------------------------------------------------------- |
| `/robots.txt`           | Crawl rules; allows AI crawlers; links the sitemap.              |
| `/sitemap.xml`          | XML sitemap.                                                     |
| `/llms.txt`             | Plain-text summary for LLMs / AI assistants.                     |
| `/manifest.webmanifest` | PWA / installability metadata.                                   |
| `/favicon.svg`          | Site icon.                                                       |
| `/og-image.png`         | 1200×630 social share card.                                      |

The homepage `<head>` also includes a meta description, keywords, canonical
link, Open Graph and Twitter Card tags, and JSON-LD structured data
(`SoftwareApplication` + `FAQPage`). All absolute URLs come from
`RECAST_SITE_URL`, so set it correctly before you deploy.

## How a user makes the upload

A recording is a folder that macOS shows as a single file, so it must be zipped
before a browser can upload it:

1. Find the recording in Finder (it ends in `.screenstudio`).
2. Right-click it → **Compress**.
3. Drop the resulting `.zip` onto the page.

## API

| Method | Path                      | Purpose                                 |
| ------ | ------------------------- | --------------------------------------- |
| POST   | `/api/convert`            | Upload a `.zip`, start a job → `{ id }` |
| GET    | `/api/jobs/{id}`          | Poll job status and progress            |
| GET    | `/api/jobs/{id}/download` | Download the finished MP4               |
| GET    | `/api/health`             | Dependency check                        |

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

MIT. See [LICENSE](LICENSE).
