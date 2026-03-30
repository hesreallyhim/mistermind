VENV ?= venv
PYTHON ?= python3
BASH ?= bash

VENV_PYTHON := $(VENV)/bin/python
VENV_PIP := $(VENV_PYTHON) -m pip
PYTHONPATH ?= src
ENV_FILE ?= .env

.PHONY: help venv bootstrap repo-bootstrap test test-cov lint lint-fix mypy pre-commit package ci-checks release-checks ci run-engine leaderboard-build leaderboard-reset clean

help: ## Show available make targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make <target>\n\nTargets:\n"} /^[a-zA-Z0-9_.-]+:.*##/ {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create local Python virtual environment at ./venv
	$(PYTHON) -m venv $(VENV)

bootstrap: venv ## Install local package and development dependencies
	$(VENV_PIP) install --upgrade pip setuptools wheel
	$(VENV_PIP) install -e ".[dev]"

repo-bootstrap: ## Create/configure the GitHub repo, labels, variables, and secrets from $(ENV_FILE)
	ENV_FILE=$(ENV_FILE) $(BASH) scripts/bootstrap_repo.sh

test: ## Run MisterMind engine test suite
	$(VENV_PYTHON) -m pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage report
	$(VENV_PYTHON) -m pytest tests/ -v --tb=short --cov=mistermind --cov-report=term-missing

lint: ## Run ruff linter and formatter check
	$(VENV_PYTHON) -m ruff check src/ tests/
	$(VENV_PYTHON) -m ruff format --check src/ tests/

lint-fix: ## Auto-fix lint issues and format code
	$(VENV_PYTHON) -m ruff check --fix src/ tests/
	$(VENV_PYTHON) -m ruff format src/ tests/

mypy: ## Run mypy type checker
	$(VENV_PYTHON) -m mypy

pre-commit: ## Run pre-commit checks across the repository
	$(VENV_PYTHON) -m pre_commit run --all-files --config .pre-commit-config.yaml

package: ## Build source and wheel distributions
	$(VENV_PYTHON) -m build

ci-checks: lint mypy test ## Run CI validation checks (lint, type-check, test)

release-checks: ci-checks package ## Run release checks (CI validation + build artifacts)

ci: bootstrap ci-checks ## Run local CI checks (install + validation checks)

run-engine: ## Execute MisterMind engine entrypoint script
	PYTHONPATH=$(PYTHONPATH) $(VENV_PYTHON) -m mistermind

leaderboard-build: ## Rebuild leaderboard markdown/json from local data/games records
	PYTHONPATH=$(PYTHONPATH) $(VENV_PYTHON) scripts/build_leaderboards.py --games-root data/games --readme README.md --json-out data/leaderboards.json --cards-dir assets

leaderboard-reset: ## Reset leaderboard source data and regenerate empty leaderboard outputs
	rm -f data/games/*.json
	mkdir -p data/games
	PYTHONPATH=$(PYTHONPATH) $(VENV_PYTHON) scripts/build_leaderboards.py --games-root data/games --readme README.md --json-out data/leaderboards.json --cards-dir assets

clean: ## Remove local build/test artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist src/*.egg-info src/mistermind.egg-info
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
