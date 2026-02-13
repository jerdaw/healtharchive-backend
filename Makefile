.PHONY: venv format format-check lint precommit typecheck test-fast test-all test security audit migration-guard check check-full ci docs-serve docs-build docs-build-strict docs-refs docs-coverage docs-coverage-strict docs-check

VENV ?= .venv
VENV_BIN := $(VENV)/bin
PYTHON ?= python3

RUFF := $(if $(wildcard $(VENV_BIN)/ruff),$(VENV_BIN)/ruff,ruff)
MYPY := $(if $(wildcard $(VENV_BIN)/mypy),$(VENV_BIN)/mypy,mypy)
PYTEST := $(if $(wildcard $(VENV_BIN)/pytest),$(VENV_BIN)/pytest,pytest)
BANDIT := $(if $(wildcard $(VENV_BIN)/bandit),$(VENV_BIN)/bandit,bandit)
PIP_AUDIT := $(if $(wildcard $(VENV_BIN)/pip-audit),$(VENV_BIN)/pip-audit,pip-audit)
PRE_COMMIT := $(if $(wildcard $(VENV_BIN)/pre-commit),$(VENV_BIN)/pre-commit,pre-commit)
HAS_PYTHON := $(shell command -v python >/dev/null 2>&1 && echo 1 || echo 0)
PYTHON_FALLBACK := $(if $(filter 1,$(HAS_PYTHON)),python,$(PYTHON))
PYTHON_RUN := $(if $(wildcard $(VENV_BIN)/python3),$(VENV_BIN)/python3,$(PYTHON_FALLBACK))
MKDOCS := $(if $(wildcard $(VENV_BIN)/mkdocs),$(VENV_BIN)/mkdocs,mkdocs)
MIGRATION_GUARD_BASE ?= origin/main
MIGRATION_GUARD_HEAD ?= HEAD

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

test-fast:
	$(PYTEST) -q \
		tests/test_ci_migration_guard.py \
		tests/test_ci_schema_parity.py \
		tests/test_archive_tool_*.py \
		tests/test_cli_*.py \
		tests/test_db_models_and_seeds.py \
		tests/test_diffing.py \
		tests/test_jobs*.py \
		tests/test_ops_*.py \
		tests/test_worker.py \
		tests/test_api_health_and_sources.py

test-all:
	$(PYTEST) -q

test: test-all

coverage:
	$(PYTEST) --cov=src --cov-report=term-missing --cov-report=html

coverage-critical:
	$(PYTEST) \
		--cov=src/ha_backend/api \
		--cov=src/ha_backend/indexing \
		--cov=src/ha_backend/worker \
		--cov-fail-under=75 \
		--cov-report=term-missing:skip-covered \
		--cov-report=html:htmlcov-critical

coverage-target:
	@echo "Current coverage target: 75%"
	@echo "Path to 80%: improve indexing/pipeline.py (currently 22.55%)"
	@echo "Run 'make coverage-critical' to check"

coverage-report:
	@echo "Full coverage report: file://$(shell pwd)/htmlcov/index.html"
	@echo "Critical modules coverage: file://$(shell pwd)/htmlcov-critical/index.html"

security:
	$(BANDIT) -r src/ha_backend -q

audit:
	$(PIP_AUDIT)

migration-guard:
	$(PYTHON_RUN) scripts/ci_migration_guard.py \
		--base-ref $(MIGRATION_GUARD_BASE) \
		--head-ref $(MIGRATION_GUARD_HEAD)

docs-serve:
	PYTHONPATH=src $(PYTHON_RUN) scripts/export_openapi.py
	$(PYTHON_RUN) scripts/generate_llms_txt.py
	$(MKDOCS) serve

docs-build:
	PYTHONPATH=src $(PYTHON_RUN) scripts/export_openapi.py
	$(PYTHON_RUN) scripts/generate_llms_txt.py
	$(MKDOCS) build

docs-build-strict:
	PYTHONPATH=src $(PYTHON_RUN) scripts/export_openapi.py
	$(PYTHON_RUN) scripts/generate_llms_txt.py
	$(MKDOCS) build --strict

docs-refs:
	$(PYTHON_RUN) scripts/check_docs_references.py

docs-coverage:
	$(PYTHON_RUN) scripts/check_docs_coverage.py

docs-coverage-strict:
	$(PYTHON_RUN) scripts/check_docs_coverage.py --strict

docs-check: docs-refs docs-coverage-strict docs-build-strict

# CI guardrail: fast + reliable (should not block day-to-day development).
check: format-check lint typecheck test-fast

ci: check

# Full suite: deeper / slower / more opinionated checks (run before deploys or when tightening quality).
check-full: format-check lint typecheck test-all coverage-critical precommit security audit docs-check
