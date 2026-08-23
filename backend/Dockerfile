# SCI-PATH Learning Path Engine
# Build from this folder:
#   docker build -t sci-path-lpe .
# Run:
#   docker run --rm -p 8000:8000 --env-file .env -v lpe-data:/app/data sci-path-lpe

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CORS_ORIGINS=http://localhost:3000

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# Runtime persistence: Chroma, SQLite libraries, AR media
RUN mkdir -p /app/data/chroma /app/data/ar_media /app/data/textbooks

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
