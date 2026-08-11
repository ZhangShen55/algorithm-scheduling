# Change Ledger

## 2026-08-06 - Runtime closure baseline

- Previous state: control and online have functional routes; orchestrator and vision entrypoints are health-only; Kafka adapters and real end-to-end evidence are absent.
- Target: four independently deployable FastAPI projects with annotated configuration, real lifespan resources and reproducible evidence.
- Contract impact: A and non-PPT operator contracts unchanged. PPT internal callback changes from Base64 per slide to shared files, atomic manifest and one terminal callback.
- Current evidence: component and PostgreSQL/Redis tests only. Broker-backed and complete service-runtime evidence remains pending.
- Remaining risk: long-running Worker loops, restart recovery, real operator images and full Compose have not yet been verified.

## 2026-08-06 - FastAPI delivery and PPT shared-result components

- Previous state: four service folders had uneven entrypoint/configuration layouts; no platform Compose existed; the platform PPT adapter still expected per-image Base64 callbacks.
- Target state: complete per-service FastAPI packages and annotated settings, a validated four-service single-machine Compose, and one platform-only PPT shared-path protocol.
- Changed files: `services/*/app`, four service `config.toml`/requirements/Dockerfiles, `deploy/docker-compose.platform.yml`, `services/orchestrator_service/ppt_slice.py`, `ppt_slice/app`, and related tests/docs.
- Contract impact: breaking internal PPT contract. Only snake_case submission, atomic `/data/result/{task_id}/ppt/manifest.json`, and one terminal metadata callback are accepted. A-facing and other operator contracts are unchanged.
- Verification: four-service structure/contract tests `33 passed`; PPT platform tests `9 passed`; PPT Conda tests `13 tests OK`; Compose config validation passed.
- Evidence tier and verdict: static/service component/operator smoke evidence is present. Broker-backed end-to-end evidence is not present.
- Remaining risks: orchestrator has not wired PPT submission/callback/reconciliation/lease components into its required runtime loop; vision and general DAG loops remain incomplete; platform images have not been built together in the final stack.

## 2026-08-07 - Root-level platform service relocation

- Previous state: four deployable services lived under `algorithm-scheduling-platform/services`, used `services.<service_name>` compatibility imports, and Docker builds copied the shared service tree.
- Target state: `control_service`, `orchestrator_service`, `vision_orchestrator_service` and `online_gateway_service` are independent workspace-root FastAPI projects; `algorithm-scheduling-platform` retains only shared packages, migrations, deployment definitions, cross-service tests and Harness.
- Changed files: four root service projects, platform packaging/tests/Compose/Makefile, root and platform AGENTS rules, design documents, Harness and the active relocation OpenSpec artifacts.
- Contract impact: HTTP/WebSocket paths, methods, fields, container ports, Kafka semantics and operator registration are unchanged. Only internal source paths, Python imports and Docker build contexts changed.
- Verification: four service suites `4/5/8/9 passed`; platform suite `192 passed`; Ruff and strict Mypy passed; three Compose files parsed; four images built with a root allowlist `.dockerignore` and returned `/health` HTTP 200; image inspection found no sibling service source; the expanded runtime/build/documentation old-path gate and strict OpenSpec validation passed.
- Evidence tier and verdict: static, unit, Compose, independent-image and service-runtime smoke evidence is complete for relocation. Broker-backed business end-to-end evidence remains outside this structural change.
- Remaining risks: the shared distribution still lives under `algorithm-scheduling-platform`; Orchestrator's FFmpeg image is large and slow to build; runtime closure work remains governed by the separate active change.

## 2026-08-07 - 方案 C 基础调度闭环与数据库说明基线

- 先前状态：开发顺序把真实 PPT 作为首条最小离线链路，但 PPT 正在独立优化；总体图没有清楚表达 control 只写 Outbox、orchestrator Publisher 从 PostgreSQL 读取后发布 Kafka 的方向；数据库迁移没有表和字段注释。
- 目标状态：一个基础阶段包含两个连续里程碑，先完成 `control-service` 的任务事实闭环，再完成 `orchestrator-service` 的通用运行时；使用契约 Stub 验证真实 PostgreSQL/Redis/Kafka，不依赖真实 PPT。10 张正式调度表及其全部字段具有中文说明。
- 变更文件：总体设计 V2、活动 OpenSpec、Harness 基础闭环场景、数据库逻辑模型、`0004_schema_comments.sql` 和迁移约束测试。
- 契约影响：A 面字段、HTTP/WebSocket 路径、算子协议和状态值不变；只调整实施顺序、完成口径和数据库元数据。
- 数据库审计：本机 `algorithm` 业务库当前无用户表；`algorithm_migration_test` 有 9 张调度测试表；`algorithm_repository_test` 有全部 10 张调度测试表；未删除、改名或修改任何现有表和数据。
- 当前证据：数据库注释迁移约束测试和迁移文件名检查已通过；在本轮新建并随后删除的临时验证库中顺序执行 `0001-0004`，得到 10 张表、92 个字段，缺失表注释和字段注释均为 0；基础 Broker 闭环尚未实现和验收。
- 证据等级与结论：DDL 静态契约符合；方案 C 的服务运行时仍为部分符合。
- 剩余风险：目标业务库尚未执行 `0001-0004`；Kafka adapter、Publisher、Consumer、Dispatcher 和契约 Stub 闭环待实现；PPT 最终内部契约仍由独立会话收口。

## 2026-08-07 - 方案 C 里程碑 1：control 事实闭环

- 先前状态：`control-service` 在应用构造期创建 Engine/Redis，真实入口未组合 PostgreSQL 算子审计，readiness 不区分存活与依赖就绪，Redis 注册、心跳和注销存在先读后写竞态。
- 目标状态：FastAPI lifespan 统一持有 PostgreSQL/Redis；课程事实与 Outbox 同事务；PostgreSQL 保存算子声明和运维事件；Redis 保存 TTL、实时生命周期和原子容量租约。
- 变更文件：`control_service/app/infrastructure/runtime.py`、`audited_operator_registry.py`、共享 Repository/Redis registry、`0005_operator_audit_and_status_comments.sql`、Control Compose/README 以及里程碑 1 测试。
- 契约影响：A 面字段、HTTP 路径、算子推理协议和状态值不变；A 面任务库故障明确为 HTTP 200 + 业务码 `50000`，注册/租约基础设施故障为 HTTP 503。新增运维历史查询 `GET /ops/operator-instances/{instance_id}/events`。注册激活规则调整为“`register` 返回 OFFLINE，首次成功心跳后 ONLINE”。
- 真实环境：`postgres:17-alpine`（PostgreSQL 17.10）与 `redis:7.4-alpine`（Redis 7.4.10）容器均为 healthy；集成测试每次创建唯一 `_test` PostgreSQL 数据库和 UUID Redis 前缀，结束后精确清理。
- 验证：里程碑 1 联合集成测试 `63 passed`；平台与 Control 完整回归 `255 passed`；其他三个服务回归分别 `5/8/9 passed`；Ruff、Mypy、compileall、迁移命名、Compose 解析和严格 OpenSpec 校验均通过，无 skipped。新增用例覆盖缺字段、未执行 `0005`、依赖故障响应、readiness 并行/总截止预算、DSN 原有 PostgreSQL options 保留、首次心跳与短暂心跳故障恢复。
- 证据等级与结论：里程碑 1 达到真实 PostgreSQL/Redis 集成和 FastAPI 运行时证据，结论为符合。方案 C 整体仍为部分符合，不得宣称完整调度闭环已完成。
- 剩余风险：里程碑 2 的 Kafka adapter、Outbox Publisher、Consumer、DAG 和契约 Stub 尚未实现；本机 `algorithm` 业务库未自动执行迁移；当前协议没有进程世代标识，同一 `instance_id` 只允许一个存活进程。同 ID 重注册会清理旧心跳和租约，未来若要支持新旧世代重叠，需单独设计世代令牌。

## 2026-08-10 - 架构图留存与里程碑证据边界

- 先前状态：总体设计 V5.4 用新的离线/在线/运维服务边界图替换了一体化组件全景图，无法在同一文档追溯此前讨论；“里程碑 1 闭环”也容易被误解为已经调用真实算子。
- 目标状态：历史总体组件图、当前服务边界图和方案 C 时序图同时保留并具有稳定编号；后续架构图只追加不覆盖。明确里程碑 1 只达到真实 FastAPI/PostgreSQL/Redis 的 control 事实闭环，里程碑 2A 使用真实 Kafka 和 HTTP 契约 Stub，2B 再接首个真实同步算子。
- 变更文件：`docs/算法功能调度平台总体设计-v2.md/.pdf`、Harness 变更记录与架构证据矩阵。
- 契约影响：A 面和现有算子业务接口不变；进一步明确平台任务状态由编排服务推进，算子不得直接写平台任务状态。
- 验证：Markdown Mermaid 代码块结构检查、PDF 渲染与逐页视觉检查。
- 证据等级与结论：文档与架构决策记录符合；里程碑 2A/2B 仍未实现。
- 剩余风险：真实 Kafka、运行中的 DAG、契约 Stub 调用及真实算子接入均待里程碑 2 验证。

## Record template

- Date and scope:
- Previous state:
- Target state:
- Changed files:
- Contract impact:
- Verification command and environment:
- Evidence tier and verdict:
- Remaining risks:
