# Architecture Evidence Matrix

| Decision ID | Decision | Owner | Evidence command | Current verdict | Linked scenario |
| --- | --- | --- | --- | --- | --- |
| DEC-001 | Four deployable service boundaries | platform maintainers | `make service-test && .venv/bin/python -m pytest -q tests/contract/test_service_entrypoints.py tests/test_platform_compose.py` | 符合 | `harness/scenarios/runtime-closure.md` |
| DEC-002 | PostgreSQL task facts and transactional Outbox | control-service | `.venv/bin/python -m pytest -q tests/integration/test_course_repository.py tests/integration/test_control_service_foundation.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-003 | Redis TTL registry, first-heartbeat activation and renewable leases | control-service | `.venv/bin/python -m pytest -q tests/integration/test_redis_operator_registry.py tests/integration/test_control_service_foundation.py tests/test_operator_registry_client.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-004 | Real Kafka course Publisher and Consumer loops | orchestrator-service | `.venv/bin/python -m pytest -q -rs tests/integration/test_kafka_runtime.py tests/integration/test_milestone_2a_runtime.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-005 | PPT shared files, dynamic segments and one terminal callback | ppt_slice and orchestrator | `pytest -q tests/test_ppt_slice_adapter.py && conda run -n ppt_slice python -m unittest discover -s ../ppt_slice/tests -v` | 部分符合 | `harness/scenarios/ppt-shared-result.md` |
| DEC-006 | Offline adaptive visual aggregation | vision-orchestrator-service | `pytest -q tests/test_adaptive_vision_scan.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-007 | Online traffic bypasses Kafka | online-gateway-service | `pytest -q tests/test_online_gateway.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-008 | Temporary course cleanup preserves results | orchestrator-service | `pytest -q tests/test_workspace_cleanup.py` | 部分符合 | `harness/scenarios/runtime-closure.md` |
| DEC-009 | Host and container Kafka listeners differ | deployment | `pytest -q tests/test_infrastructure_config.py` | 符合 | `harness/scenarios/runtime-closure.md` |
| DEC-010 | Completion requires real infrastructure evidence | platform maintainers | `.venv/bin/python scripts/run_milestone_2a.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-011 | One Worker with truthful PPT N-way capacity (component evidence) | ppt_slice and registry client | `pytest -q tests/test_operator_registry_client.py tests/test_operator_deployment_integration.py` | 符合 | `harness/scenarios/ppt-shared-result.md` |
| DEC-012 | Four-service Compose uses one network and shared roots (static evidence) | deployment | `docker compose -f deploy/docker-compose.platform.yml config --quiet` | 符合 | `harness/scenarios/runtime-closure.md` |
| DEC-013 | Four services are root-level independent projects and images exclude sibling services | platform maintainers | `pytest -q tests/test_root_service_layout.py && docker build -f ../SERVICE/docker/Dockerfile ..` | 符合 | `harness/scenarios/root-service-relocation.md` |
| DEC-014 | 方案 C 先完成 control 事实闭环，再完成不依赖真实算子的 orchestrator 通用运行时 | control-service and orchestrator-service | `.venv/bin/python scripts/run_milestone_2a.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-015 | Ten scheduling tables, expected columns, Chinese comments and milestone-1 index/status semantics are readiness-gated | database maintainers | `.venv/bin/python -m pytest -q tests/test_database_comments.py tests/integration/test_control_service_foundation.py && .venv/bin/python scripts/check_migrations.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-016 | PostgreSQL operator audit and Redis realtime registry stay separated | control-service | `.venv/bin/python -m pytest -q tests/integration/test_operator_audit_repository.py tests/integration/test_redis_operator_registry.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-017 | 已确认的架构图使用稳定编号并只追加、不覆盖历史图 | platform maintainers | `rg -n ARCH-001 ../docs/算法功能调度平台总体设计-v2.md && rg -n ARCH-002 ../docs/算法功能调度平台总体设计-v2.md && rg -n SEQ-001 ../docs/算法功能调度平台总体设计-v2.md` | 符合 | `harness/change-ledger.md` |
| DEC-018 | 同步算子不直接汇报课程节点状态；编排服务根据调用事实推进平台状态 | orchestrator-service and operator maintainers | `.venv/bin/python -m pytest -q -rs tests/integration/test_milestone_2a_runtime.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-019 | FaceRec/MongoDB owns face embeddings; A uses online gateway management proxy | online-gateway-service and facerec | `conda run -n facerecapi python -m pytest -q tests && pytest -q tests/test_operator_deployment_integration.py` | 部分符合 | `harness/scenarios/operator-local-runtime-validation.md` |
| DEC-020 | Registry client is an isolated Python 3.10+ wheel and all operator requirements pin version 0.1.0 | operator maintainers | `pytest -q tests/test_operator_registry_wheel.py tests/test_operator_registry_client.py tests/test_operator_deployment_integration.py` | 符合 | `harness/scenarios/operator-local-runtime-validation.md` |
| DEC-021 | 里程碑 2A 的重复 Kafka 消息、重复发布和 orchestrator 重启保持 DAG 幂等并恢复已提交 offset | orchestrator-service | `.venv/bin/python -m pytest -q -rs tests/integration/test_milestone_2a_runtime.py` | 符合 | `harness/scenarios/foundation-scheduling-closure.md` |
| DEC-022 | 里程碑 2B 三卡部署必须以真实 x86_64/NVIDIA、模型、24 实例注册和算子推理证据为准 | deployment maintainers | `python3 deploy/scripts/run_milestone_2b_8a3.py` | 符合 | `harness/scenarios/milestone-2b-deploy.md` |
| DEC-023 | OCR 单一 CPU/NVIDIA GPU Dockerfile 支持源码/Cython 构建；正式 AMD64 Cython 镜像以 tar 离线交付，并以 RTX 3090 实测参数同步平台副本 | ocr maintainers | `(cd ../ocr && conda run -n ocr-v6 python -m pytest -q tests && rg -n '13.468 QPS' docs/ocr-v6-rtx3090-benchmark.md && rg -n '8201d923' docs/ocr-v6-rtx3090-benchmark.md) && .venv/bin/python -m pytest -q tests/test_harness_consistency.py` | 符合 | `harness/scenarios/ocr-optional-cython-build-and-sync.md` |
| DEC-024 | 2B 按 FaceRec、PPT/ASR、视觉、在线、243 条总验收的依赖顺序关闭，最终不允许未执行用例 | platform maintainers | `.venv/bin/python -m pytest -q tests/test_harness_consistency.py && openspec validate close-platform-runtime-and-harness-gaps --strict` | 待验证 | `harness/scenarios/milestone-2b-business-lanes-closure.md` |
| DEC-025 | 八算子统一 TOML/Compose 配置归属和正整数容量；Redis 活跃租约成为分发权威并可归属任务；同步 HTTP 跨 TTL 续租；在线单图 OCR 与离线 OCR 共享容量和 72/50 MiB 图片边界；2B 新镜像通过门禁后精确清理旧平台/算子镜像 | platform and operator maintainers | `PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest -q tests/test_operator_registry_client.py tests/test_redis_operator_registry_unit.py tests/integration/test_redis_operator_registry.py tests/test_operator_registry_api.py tests/test_operations_api.py tests/test_node_dispatcher.py tests/test_ppt_text_pipeline.py tests/test_vbas_batch_client.py tests/test_online_gateway.py tests/test_operator_deployment_integration.py tests/test_milestone_2b_operator_configs.py tests/test_harness_consistency.py` | 待验证 | `harness/scenarios/unified-operator-capacity-leases-and-online-ocr.md` |

DEC-022 已由 release `v1.0_260812/1aa5da672f75adfa7aea5f767bc91e9ac4889cce`
关闭：FaceRec 三实例、18 个 GPU 实例、24 实例注册、6 个 CPU 实例、8/8 full Smoke 和
93/93 deployment 用例均取得真实通过证据，且清理、恢复和双终态为零。该结论只覆盖三卡
部署阶段。DEC-024 的完成门槛仍是后续 PPT/ASR、视觉、在线业务泳道和全部 243 条用例均有
运行证据且失败数为零，因此继续保持“待验证”。

DEC-025 当前只有严格通过的 OpenSpec 规划和 Harness 验收合同，没有实现、真实 Redis、算子、
跨服务、24 实例运行或精确旧镜像清理证据，因此保持“待验证”，且不得从已有 8A.3 部署证据
推导为已符合。上表定向测试也不能替代八算子真实推理、Compose 展开核验和新 SHA 远端门禁。
