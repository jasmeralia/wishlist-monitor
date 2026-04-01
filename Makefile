VENV := .venv
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
PYLINT := $(VENV)/bin/pylint
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
