# Pipeline runtime image.
#
# docker-compose.yml builds this image for the `pipeline` service. MySQL and
# Redis run as separate Compose services; this container only runs Python code.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# Everything below runs inside the image build, not at app runtime.
# /app is the project folder inside the container.
WORKDIR /app

# System packages needed to compile/install some Python dependencies.
# --no-install-recommends keeps the image smaller.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first. Docker can cache this layer when source
# code changes but requirements.txt does not.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -r requirements.txt

# Copy the project source into the image after dependencies.
COPY . .
RUN chmod +x docker/pipeline-entrypoint.sh

# ENTRYPOINT runs first. It waits for MySQL/Redis, then execs CMD or the Compose
# command override.
ENTRYPOINT ["docker/pipeline-entrypoint.sh"]

# Compose normally overrides this with:
#   python3 scripts/run_redis_workers.py ${PIPELINE_ARGS:---dry-run}
# Keep this fallback simple and let .env control feed/worker counts.
CMD ["python3", "scripts/run_redis_workers.py", "--dry-run"]
