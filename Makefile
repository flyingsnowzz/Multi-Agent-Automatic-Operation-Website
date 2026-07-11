VENV ?= .venv
PYTHON ?= python3
VENV_PY := $(VENV)/bin/python

.PHONY: install run run-bg run-fg stop force-stop status logs trace run-once docker-services docker-run docker-stop docker-logs docker-health clean

$(VENV_PY):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip setuptools wheel

install: $(VENV_PY)
	$(VENV_PY) -m pip install -r requirements.txt

run: install
	scripts/langgraph_daemon.sh start $${LANGGRAPH_ARGS:-}

run-bg: install
	scripts/langgraph_daemon.sh start $${LANGGRAPH_ARGS:-}

run-fg: install
	$(VENV_PY) scripts/run_langgraph_batch.py --production $${LANGGRAPH_ARGS:-}

stop:
	scripts/langgraph_daemon.sh stop

force-stop:
	scripts/langgraph_daemon.sh force-stop

status:
	scripts/langgraph_daemon.sh status

logs:
	scripts/langgraph_daemon.sh logs

trace: install
	@test -n "$${ARTICLE_ID:-}" || (echo "Usage: make trace ARTICLE_ID=213"; exit 2)
	$(VENV_PY) scripts/trace_langgraph_article.py "$${ARTICLE_ID}" $${TRACE_ARGS:-}

run-once: install
	$(VENV_PY) scripts/run_langgraph_batch.py --feed --limit $${LANGGRAPH_BATCH_LIMIT:-30}

docker-services:
	docker compose up -d mysql

docker-run:
	docker compose up -d --build mysql pipeline

docker-stop:
	docker compose down

docker-logs:
	docker compose logs -f pipeline

docker-health:
	docker compose exec pipeline python3 -m py_compile scripts/run_langgraph_batch.py workflows/langgraph_article_pipeline.py

clean:
	rm -rf $(VENV)
