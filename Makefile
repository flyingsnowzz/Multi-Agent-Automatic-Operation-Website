VENV ?= .venv
PYTHON ?= python3
VENV_PY := $(VENV)/bin/python

.PHONY: install run run-once docker-services docker-run docker-stop docker-logs docker-health clean

$(VENV_PY):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip setuptools wheel

install: $(VENV_PY)
	$(VENV_PY) -m pip install -r requirements.txt

run: install
	$(VENV_PY) scripts/run_redis_workers.py --feed --dry-run

run-once: install
	$(VENV_PY) scripts/redis_fill.py
	$(VENV_PY) scripts/run_redis_workers.py --dry-run

docker-services:
	docker compose up -d mysql redis

docker-run:
	docker compose up -d --build mysql redis pipeline

docker-stop:
	docker compose down

docker-logs:
	docker compose logs -f pipeline

docker-health:
	docker compose exec pipeline python3 scripts/monitor_pipeline.py --json

clean:
	rm -rf $(VENV)
