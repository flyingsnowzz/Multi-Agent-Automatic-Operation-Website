VENV ?= .venv
PYTHON ?= python3
VENV_PY := $(VENV)/bin/python

.PHONY: install run run-once run-redis-legacy docker-services docker-run docker-run-redis-legacy docker-stop docker-logs docker-health clean

$(VENV_PY):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip setuptools wheel

install: $(VENV_PY)
	$(VENV_PY) -m pip install -r requirements.txt

run: install
	$(VENV_PY) scripts/run_langgraph_batch.py --production

run-once: install
	$(VENV_PY) scripts/run_langgraph_batch.py --feed --limit $${LANGGRAPH_BATCH_LIMIT:-30}

run-redis-legacy: install
	$(VENV_PY) legacy/redis_pipeline/run_redis_workers.py --feed --dry-run

docker-services:
	docker compose up -d mysql

docker-run:
	docker compose up -d --build mysql pipeline

docker-run-redis-legacy:
	docker compose --profile redis-legacy up -d --build mysql redis redis-pipeline

docker-stop:
	docker compose down

docker-logs:
	docker compose logs -f pipeline

docker-health:
	docker compose exec pipeline python3 -m py_compile scripts/run_langgraph_batch.py workflows/langgraph_article_pipeline.py

clean:
	rm -rf $(VENV)
