VENV := .venv
PYTHON := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python)
RUFF := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
MYPY := $(if $(wildcard $(VENV)/bin/mypy),$(VENV)/bin/mypy,mypy)
PYLINT := $(if $(wildcard $(VENV)/bin/pylint),$(VENV)/bin/pylint,pylint)
SRC := monitor.py core fetchers
TESTS := tests

.PHONY: lint lintfix ruff ruff-format pylint mypy test

lint: ruff-format ruff pylint mypy

lintfix:
	$(RUFF) check --fix $(SRC) $(TESTS)
	$(RUFF) format $(SRC) $(TESTS)

ruff:
	$(RUFF) check $(SRC) $(TESTS)

ruff-format:
	$(RUFF) format --check $(SRC) $(TESTS)

pylint:
	$(PYLINT) $(SRC)

mypy:
	$(MYPY) $(SRC)

test:
	$(PYTHON) -m pytest --cov --cov-report=term-missing --cov-report=xml $(TESTS)
