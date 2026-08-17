#!/usr/bin/env python3
"""Development entry point: python run.py  ->  http://localhost:8000"""

import os

import uvicorn

try:  # Load variables from a local .env file if python-dotenv is available.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

if __name__ == "__main__":
    host = os.environ.get("RECAST_HOST", "127.0.0.1")
    port = int(os.environ.get("RECAST_PORT", 8000))
    uvicorn.run("recast.server:app", host=host, port=port, reload=False)
