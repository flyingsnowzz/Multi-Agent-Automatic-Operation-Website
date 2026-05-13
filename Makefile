VENV ?= .venv
PYTHON ?= python3
VENV_PY := $(VENV)/bin/python

.PHONY: install run clean

$(VENV_PY):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip setuptools wheel

install: $(VENV_PY)
	$(VENV_PY) -m pip install -r requirements.txt

run: install
	$(VENV_PY) main.py

clean:
	rm -rf $(VENV)
