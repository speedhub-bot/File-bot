FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN mkdir -p /data /data/work && chmod -R 777 /data
ENV WORK_DIR=/data/work \
    DATABASE_URL=sqlite+aiosqlite:////data/filebot.db \
    PORT=8080 \
    HEALTH_HOST=0.0.0.0

EXPOSE 8080

# Container-level liveness check — independent of platform healthcheck config.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-8080}/health || exit 1

CMD ["python", "-m", "bot.main"]
