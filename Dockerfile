FROM python:3.12-slim

# ffmpeg + ffprobe are required by the conversion engine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY recast ./recast
COPY web ./web

ENV FRAMEDROP_HOST=0.0.0.0 \
    FRAMEDROP_SITE_URL=https://framedrop.devtools4me.com

EXPOSE 8000

# Shell form so Render's injected $PORT is honoured (defaults to 8000 locally).
CMD uvicorn recast.server:app --host 0.0.0.0 --port ${PORT:-8000}
