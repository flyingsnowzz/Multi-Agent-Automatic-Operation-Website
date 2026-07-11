# Pipeline runtime image.
#
# docker-compose.yml builds this image for the LangGraph `pipeline` service.
# MySQL runs as a separate Compose service.
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

# ENTRYPOINT runs first. It waits for MySQL, then execs CMD or the Compose
# command override.
ENTRYPOINT ["docker/pipeline-entrypoint.sh"]

# Compose normally overrides this with the same command plus LANGGRAPH_ARGS.
# Keep the fallback on the production LangGraph runner and dry-run CMS mode.
CMD ["python3", "scripts/run_langgraph_batch.py", "--production"]
