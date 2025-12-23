.PHONY: venv format format-check lint typecheck test security audit check

VENV ?= .venv
VENV_BIN := $(VENV)/bin
PYTHON ?= python3

RUFF := $(if $(wildcard $(VENV_BIN)/ruff),$(VENV_BIN)/ruff,ruff)
MYPY := $(if $(wildcard $(VENV_BIN)/mypy),$(VENV_BIN)/mypy,mypy)
PYTEST := $(if $(wildcard $(VENV_BIN)/pytest),$(VENV_BIN)/pytest,pytest)
BANDIT := $(if $(wildcard $(VENV_BIN)/bandit),$(VENV_BIN)/bandit,bandit)
PIP_AUDIT := $(if $(wildcard $(VENV_BIN)/pip-audit),$(VENV_BIN)/pip-audit,pip-audit)

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install -e ".[dev]"

format:
	$(RUFF) format .

format-check:
	$(RUFF) format --check .

lint:
	$(RUFF) check .

typecheck:
	$(MYPY) src tests

test:
	$(PYTEST) -q

security:
	$(BANDIT) -r src/ha_backend -q

audit:
	$(PIP_AUDIT) || true

check: format-check lint typecheck test security audit
