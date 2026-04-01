VENV := .venv
RUFF := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
MYPY := $(if $(wildcard $(VENV)/bin/mypy),$(VENV)/bin/mypy,mypy)
PYLINT := $(if $(wildcard $(VENV)/bin/pylint),$(VENV)/bin/pylint,pylint)
SRC := monitor.py core fetchers

.PHONY: lint lint-fix ruff pylint mypy

lint: ruff pylint mypy

lint-fix:
	$(RUFF) check --fix $(SRC)
	$(RUFF) format $(SRC)

ruff:
	$(RUFF) check $(SRC)

pylint:
	$(PYLINT) $(SRC)

mypy:
	$(MYPY) $(SRC)
