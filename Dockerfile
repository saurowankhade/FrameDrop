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

ENV RECAST_HOST=0.0.0.0 \
    RECAST_PORT=8000 \
    RECAST_SITE_URL=https://framedrop.app

EXPOSE 8000

CMD ["uvicorn", "recast.server:app", "--host", "0.0.0.0", "--port", "8000"]
