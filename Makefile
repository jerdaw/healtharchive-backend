.PHONY: venv format format-check lint precommit typecheck test security audit check docs-serve docs-build

VENV ?= .venv
VENV_BIN := $(VENV)/bin
PYTHON ?= python3

RUFF := $(if $(wildcard $(VENV_BIN)/ruff),$(VENV_BIN)/ruff,ruff)
MYPY := $(if $(wildcard $(VENV_BIN)/mypy),$(VENV_BIN)/mypy,mypy)
PYTEST := $(if $(wildcard $(VENV_BIN)/pytest),$(VENV_BIN)/pytest,pytest)
BANDIT := $(if $(wildcard $(VENV_BIN)/bandit),$(VENV_BIN)/bandit,bandit)
PIP_AUDIT := $(if $(wildcard $(VENV_BIN)/pip-audit),$(VENV_BIN)/pip-audit,pip-audit)
PRE_COMMIT := $(if $(wildcard $(VENV_BIN)/pre-commit),$(VENV_BIN)/pre-commit,pre-commit)

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install -e ".[dev]"

format:
	$(RUFF) format .

format-check:
	$(RUFF) format --check .

lint:
	$(RUFF) check .


precommit:
	$(PRE_COMMIT) run --all-files

typecheck:
	$(MYPY) src tests

test:
	$(PYTEST) -q

security:
	$(BANDIT) -r src/ha_backend -q

audit:
	$(PIP_AUDIT) || true

docs-serve:
	PYTHONPATH=src $(VENV_BIN)/python3 scripts/export_openapi.py
	$(VENV_BIN)/python3 scripts/generate_llms_txt.py
	$(VENV_BIN)/mkdocs serve

docs-build:
	PYTHONPATH=src $(VENV_BIN)/python3 scripts/export_openapi.py
	$(VENV_BIN)/python3 scripts/generate_llms_txt.py
	$(VENV_BIN)/mkdocs build --strict

check: format-check lint precommit typecheck test security audit docs-build
