# Architecture Evidence Matrix

| Decision ID | Decision | Owner | Evidence command | Current verdict | Linked scenario |
| --- | --- | --- | --- | --- | --- |
| DEC-001 | Four deployable service boundaries | platform maintainers | `make service-test && .venv/bin/python -m pytest -q tests/contract/test_service_entrypoints.py tests/test_platform_compose.py` | 符合 | `harness/scenarios/runtime-closure.md` |
| DEC-002 | PostgreSQL task facts and transactional Outbox | control-service | `.venv/bin/python -m pytest -q tests/integration/test_course_repository.py tests/test_database_comments.py` | 部分符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-003 | Redis TTL registry and renewable leases | control-service | `pytest -q tests/integration/test_redis_operator_registry.py` | 部分符合 | `harness/scenarios/ppt-shared-result.md` |
| DEC-004 | Real Kafka course Publisher and Consumer loops | orchestrator-service | `.venv/bin/python -m pytest -q tests/integration` | 不符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-005 | PPT shared files, dynamic segments and one terminal callback | ppt_slice and orchestrator | `pytest -q tests/test_ppt_slice_adapter.py && conda run -n ppt_slice python -m unittest discover -s ../ppt_slice/tests -v` | 部分符合 | `harness/scenarios/ppt-shared-result.md` |
| DEC-006 | Offline adaptive visual aggregation | vision-orchestrator-service | `pytest -q tests/test_adaptive_vision_scan.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-007 | Online traffic bypasses Kafka | online-gateway-service | `pytest -q tests/test_online_gateway.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-008 | Temporary course cleanup preserves results | orchestrator-service | `pytest -q tests/test_workspace_cleanup.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-009 | Host and container Kafka listeners differ | deployment | `pytest -q tests/test_infrastructure_config.py` | 符合 | `harness/scenarios/runtime-closure.md` |
| DEC-010 | Completion requires real infrastructure evidence | platform maintainers | `pytest -q tests/test_harness_consistency.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-011 | One Worker with truthful PPT N-way capacity (component evidence) | ppt_slice and registry client | `pytest -q tests/test_operator_registry_client.py tests/test_operator_deployment_integration.py` | 符合 | `harness/scenarios/ppt-shared-result.md` |
| DEC-012 | Four-service Compose uses one network and shared roots (static evidence) | deployment | `docker compose -f deploy/docker-compose.platform.yml config --quiet` | 符合 | `harness/scenarios/runtime-closure.md` |
| DEC-013 | Four services are root-level independent projects and images exclude sibling services | platform maintainers | `pytest -q tests/test_root_service_layout.py && docker build -f ../SERVICE/docker/Dockerfile ..` | 符合 | `harness/scenarios/root-service-relocation.md` |
| DEC-014 | 方案 C 先完成 control 事实闭环，再完成不依赖真实 PPT 的 orchestrator 通用运行时 | control-service and orchestrator-service | `.venv/bin/python -m pytest -q tests/test_harness_consistency.py tests/test_database_comments.py` | 部分符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-015 | Ten scheduling tables and every physical column have Chinese PostgreSQL comments | database maintainers | `.venv/bin/python -m pytest -q tests/test_database_comments.py && .venv/bin/python scripts/check_migrations.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-016 | FaceRec/MongoDB owns face embeddings; A uses online gateway management proxy | online-gateway-service and facerec | `conda run -n facerecapi python -m pytest -q tests && pytest -q tests/test_operator_deployment_integration.py` | 部分符合 | `harness/scenarios/operator-local-runtime-validation.md` |
| DEC-017 | Registry client is an isolated Python 3.10+ wheel | operator maintainers | `pytest -q tests/test_operator_registry_wheel.py tests/test_operator_registry_client.py` | 符合 | `harness/scenarios/operator-local-runtime-validation.md` |
