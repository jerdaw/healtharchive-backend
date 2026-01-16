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
PYTHON_RUN := $(if $(wildcard $(VENV_BIN)/python3),$(VENV_BIN)/python3,$(PYTHON))
MKDOCS := $(if $(wildcard $(VENV_BIN)/mkdocs),$(VENV_BIN)/mkdocs,mkdocs)

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
	PYTHONPATH=src $(PYTHON_RUN) scripts/export_openapi.py
	$(PYTHON_RUN) scripts/generate_llms_txt.py
	$(MKDOCS) serve

docs-build:
	PYTHONPATH=src $(PYTHON_RUN) scripts/export_openapi.py
	$(PYTHON_RUN) scripts/generate_llms_txt.py
	$(MKDOCS) build --strict

check: format-check lint precommit typecheck test security audit docs-build
