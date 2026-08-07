PYTHON ?= .venv/bin/python
RUFF ?= .venv/bin/ruff

.PHONY: lint type-check test contract-test integration-test migration-check compose-check verify

lint:
	$(RUFF) check services packages scripts tests

type-check:
	$(PYTHON) -m mypy services packages scripts

test:
	$(PYTHON) -m pytest -m "not contract and not integration"

contract-test:
	$(PYTHON) -m pytest -m contract

integration-test:
	$(PYTHON) -m pytest -m integration

migration-check:
	$(PYTHON) scripts/check_migrations.py

compose-check:
	docker compose -f deploy/docker-compose.infrastructure.yml config --quiet

verify: lint type-check test contract-test migration-check compose-check
