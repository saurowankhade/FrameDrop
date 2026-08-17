#!/usr/bin/env python3
"""Development entry point: python run.py  ->  http://localhost:8000"""

import os

import uvicorn

try:  # Load variables from a local .env file if python-dotenv is available.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

def _env(name: str, default: str | None = None) -> str | None:
    """Read FRAMEDROP_<name>, falling back to the legacy RECAST_<name>."""
    return os.environ.get(f"FRAMEDROP_{name}") or os.environ.get(f"RECAST_{name}", default)


if __name__ == "__main__":
    host = _env("HOST", "127.0.0.1")
    # Hosts like Render inject their own PORT; honour it when our vars are unset.
    port = int(_env("PORT") or os.environ.get("PORT", "8000"))
    uvicorn.run("recast.server:app", host=host, port=port, reload=False)
