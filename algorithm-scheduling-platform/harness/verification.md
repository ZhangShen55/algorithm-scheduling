# Verification Commands

Use the project `.venv` from the platform root.

```bash
.venv/bin/python -m compileall -q packages services
.venv/bin/pytest -q tests/test_harness_consistency.py
.venv/bin/pytest -q tests/test_infrastructure_config.py
.venv/bin/pytest -q tests/contract
.venv/bin/pytest -q tests
.venv/bin/ruff check packages services tests
.venv/bin/mypy packages services
docker compose -f deploy/docker-compose.infrastructure.yml config --quiet
docker compose -f deploy/docker-compose.platform.yml config --quiet
python -m pytest -q services/control_service/tests services/orchestrator_service/tests services/vision_orchestrator_service/tests services/online_gateway_service/tests
python -m pytest -q tests/test_ppt_slice_adapter.py tests/test_platform_compose.py
conda run -n ppt_slice python -m unittest discover -s ../ppt_slice/tests -v
```

Integration and runtime commands must record infrastructure versions and container status. A skipped integration test is not passing evidence. Full end-to-end evidence must show Kafka offsets, Worker-produced database state, operator HTTP/WebSocket traffic and filesystem results.
