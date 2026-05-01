# OpenSpine — developer convenience targets.
# Read this file as the manifest of "things you can do locally".

.PHONY: help install dev up down logs reset-db migrate revision run worker test lint typecheck format check fix clean

PYTHON ?= python3.12
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python
UVICORN := $(VENV)/bin/uvicorn
ALEMBIC := $(VENV)/bin/alembic
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
PRECOMMIT := $(VENV)/bin/pre-commit

help:
	@echo "OpenSpine — make targets"
	@echo ""
	@echo "  install     Create venv and install runtime + dev dependencies"
	@echo "  dev         Install in editable mode with dev extras"
	@echo "  up          Bring up the local stack (Postgres, Redis, Qdrant, Ollama)"
	@echo "  down        Tear down the local stack (preserves volumes)"
	@echo "  logs        Tail logs from the local stack"
	@echo "  reset-db    Drop and recreate the openspine database"
	@echo "  migrate     Run Alembic migrations to head"
	@echo "  revision    Create a new Alembic revision (use MSG=...)"
	@echo "  run         Run the FastAPI app with auto-reload"
	@echo "  worker      Run the embedding worker"
	@echo "  test        Run the test suite"
	@echo "  lint        Run ruff lint"
	@echo "  typecheck   Run mypy"
	@echo "  format      Format code with ruff"
	@echo "  check       lint + typecheck + test"
	@echo "  fix         ruff --fix + format"
	@echo "  clean       Remove venv and caches"

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/python
	$(PIP) install -e ".[dev]"

dev: install
	$(PRECOMMIT) install --hook-type commit-msg --hook-type pre-commit || true

up:
	docker compose up -d
	@echo "Waiting for Postgres…"
	@until docker exec openspine-postgres pg_isready -U openspine >/dev/null 2>&1; do sleep 1; done
	@echo "Stack is up. Pull the embedding model with: docker exec openspine-ollama ollama pull qwen2.5:1.5b"

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

reset-db:
	docker exec -i openspine-postgres psql -U openspine -d postgres -c "DROP DATABASE IF EXISTS openspine;"
	docker exec -i openspine-postgres psql -U openspine -d postgres -c "CREATE DATABASE openspine OWNER openspine;"
	$(MAKE) migrate

migrate:
	$(ALEMBIC) upgrade head

revision:
	@if [ -z "$(MSG)" ]; then echo "Usage: make revision MSG='describe the change'"; exit 1; fi
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

run:
	$(UVICORN) openspine.main:app --reload --host 0.0.0.0 --port 8000

worker:
	$(PY) -m openspine.workers.embedding

test:
	$(PYTEST) -v

lint:
	$(RUFF) check src tests

typecheck:
	$(MYPY) src

format:
	$(RUFF) format src tests

check: lint typecheck test

fix:
	$(RUFF) check --fix src tests
	$(RUFF) format src tests

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
