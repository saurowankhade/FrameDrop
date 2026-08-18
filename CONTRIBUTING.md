# Contributing to FrameDrop

Thanks for considering a contribution. FrameDrop is a small FastAPI + Pillow +
ffmpeg app, and pull requests, bug reports, and ideas are all welcome.

## Getting set up

```bash
git clone https://github.com/saurowankhade/FrameDrop.git
cd FrameDrop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
# open http://localhost:8000
```

You'll need `ffmpeg`/`ffprobe` on your `PATH` (`brew install ffmpeg` on macOS).

## Project layout

- `app/server.py` — FastAPI app and routes
- `app/jobs.py` — job queue/state for conversions
- `app/engine/` — the actual conversion pipeline (`prepare.py`, `cursor.py`,
  `convert.py`, `preview.py`)
- `web/` — static frontend (plain HTML/CSS/JS, no build step)

## Making a change

1. Open an issue first for anything non-trivial (new feature, behavior change)
   so we can agree on the approach before you invest time.
2. Keep PRs focused — one fix or feature per PR.
3. Test against a real `.screenstudio` recording where possible; note in the
   PR description what you tested and how.
4. Match the existing code style (no framework-specific formatter is enforced
   yet; keep it readable and consistent with surrounding code).
5. Don't commit `.env`, rendered `.mp4` files, or anything under
   `framedrop-jobs/` — these are gitignored for a reason.

## Reporting bugs

Use the bug report issue template. Include your OS, Python version, ffmpeg
version, and the Screen Studio version that produced the recording — most
rendering issues trace back to a bundle-format difference.

## Reporting security issues

Please don't open a public issue for security vulnerabilities — see
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree your contributions are licensed under the project's
[MIT License](LICENSE).
