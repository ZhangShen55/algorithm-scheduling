PYTHON ?= .venv/bin/python
RUFF ?= .venv/bin/ruff

.PHONY: lint type-check test service-test contract-test integration-test migration-check compose-check verify

lint:
	$(RUFF) check packages scripts tests ../control_service/app ../control_service/tests ../orchestrator_service/app ../orchestrator_service/tests ../vision_orchestrator_service/app ../vision_orchestrator_service/tests ../online_gateway_service/app ../online_gateway_service/tests

type-check:
	MYPYPATH=$(CURDIR) $(PYTHON) -m mypy packages scripts ../control_service/app ../orchestrator_service/app ../vision_orchestrator_service/app ../online_gateway_service/app

test:
	$(PYTHON) -m pytest -m "not contract and not integration"

service-test:
	cd ../control_service && $(CURDIR)/$(PYTHON) -m pytest -q
	cd ../orchestrator_service && $(CURDIR)/$(PYTHON) -m pytest -q
	cd ../vision_orchestrator_service && $(CURDIR)/$(PYTHON) -m pytest -q
	cd ../online_gateway_service && $(CURDIR)/$(PYTHON) -m pytest -q

contract-test:
	$(PYTHON) -m pytest -m contract

integration-test:
	$(PYTHON) -m pytest -m integration

migration-check:
	$(PYTHON) scripts/check_migrations.py

compose-check:
	docker compose -f deploy/docker-compose.infrastructure.yml config --quiet
	docker compose -f deploy/docker-compose.operators.yml config --quiet
	docker compose -f deploy/docker-compose.platform.yml config --quiet

verify: lint type-check test service-test contract-test migration-check compose-check
