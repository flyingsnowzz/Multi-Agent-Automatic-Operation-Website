FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.txt

COPY . .
RUN chmod +x docker/pipeline-entrypoint.sh

ENTRYPOINT ["docker/pipeline-entrypoint.sh"]
CMD ["python3", "scripts/run_redis_workers.py", "--feed", "--dry-run", "--feed-interval", "600", "--feed-limit", "100", "--feed-max-inflight", "300", "--scoring", "1", "--quality", "1", "--rewrite", "4", "--publish-workers", "2"]
