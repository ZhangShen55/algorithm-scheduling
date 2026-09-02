# 运维控制台后端观测接口发布验证

## 范围

本记录对应 OpenSpec 变更 `standardize-ops-console-deployment-and-observability` 的后端发布部分。
本次只构建并替换 `control-service` 和 `online-gateway-service`，不发布另一开发窗口中的运维前端、
GPU exporter，也不重启 PostgreSQL、Redis、Kafka、MongoDB、Orchestrator、Vision 或七类算子。

目标机为 `192.168.29.11`，发布提交为
`fd016a7fa7876f152a0f0e4e99feaf0fda3a6a7a`，干净发布目录为
`/root/workspace/algorithm-scheduling-release-fd016a7a`。目标机不能使用其现有 SSH 身份访问私有
GitHub 仓库，因此使用包含完整 Git 历史的 bundle 传输提交，再从 bundle 创建干净 checkout；
没有复制本地脏工作树。

## 本地门禁

- 修改文件 Ruff：通过。
- Control Service：`25 passed`。
- Online Gateway Service：`64 passed`。
- 运维接口测试：`9 passed`。
- A 服务、算子运维契约、服务入口与根目录布局相关测试：`26 passed`、`12 skipped`；跳过项仅因
  本机未启动 PostgreSQL。
- `compileall`：Control、Gateway 和共享平台包通过。
- `control_service.app.main:app`、`online_gateway_service.app.main:app`：导入通过。
- 额外运行的平台部署契约中有两个与本次无关的既有工作树失败：VBas 测试仍构造旧
  `MaxConcurrentBatches/MaxQueueSize`，当前实现要求 `MaxConcurrentOfflineBatches`。这些未提交
  文件没有进入本次镜像和提交。

## 迁移和构建

正式重启前查询 `algorithm_schema_migrations`，确认 `0001` 至 `0009` 均已应用；数据库账本中的
文件名和 SHA-256 与新 release 的九个迁移文件逐项一致，本次没有新增迁移需要执行。

构建使用 Compose BuildKit，设置
`EXPECTED_GIT_SHA=fd016a7fa7876f152a0f0e4e99feaf0fda3a6a7a`，只构建两个服务。Pip 和
BuildKit 缓存均保留，结束时 BuildKit 缓存约 `91.64 GiB`，未执行 prune。

| 服务 | 新镜像 ID | 保留标签 |
| --- | --- | --- |
| Control Service | `sha256:e2e87cf7e4e70417274d805ce76bc873c53091d9d9e7febbe355e74fd9b98135` | `algorithm-scheduling/control-service:v1.0_260902` |
| Online Gateway | `sha256:10f61ceac2af9bd10937c9ce0a18f0b79d763f7e844526f8410aa3e486ab37b7` | `algorithm-scheduling/online-gateway-service:v1.0_260902` |

两个镜像的 `org.opencontainers.image.revision` 均为本次完整 Git SHA。

## 候选容器门禁

正式切换前，使用相同镜像、配置、数据库和 Docker 网络在备用端口启动候选容器：Control 使用
`19100`，Gateway 使用 `19103`。候选阶段验证结果如下：

- Control `/ops/readiness` 的 PostgreSQL、Redis、schema 均 ready；
- `/ops/course-jobs?page=1&page_size=2&sort_by=updated_at&order=desc` 返回真实数据库分页，
  总任务数为 `13406`；
- `/ops/kafka` 返回 `status=ok`、`publisher_status=ok`、`outbox_pending=0`；
- Control `/ops/course-jobs` 和 Gateway `/metrics` 的浏览器 OPTIONS 预检均返回 HTTP 200 和
  `Access-Control-Allow-Origin: *`；
- Gateway `/metrics` 包含 `algorithm_operator_request_latency_seconds`、
  `algorithm_operator_request_errors_total` 和 `algorithm_capacity_lease_events_total`；
- 旧正式镜像和候选镜像的 OpenAPI 只读对比完全一致：Control 的 7 条 `/api/*` 路径哈希为
  `4b0016ad592586f0a353ba7eb1ae769e48e16ce753165768ed5cd06376829141`，Gateway 的 3 条
  `/online/*` 路径哈希为
  `2f9d55d0fce5d57f7c856d0358483375ab7eec5177e521a877a3980dfcda0f07`。

OpenAPI 对比只读取契约，没有提交课程任务或触发在线推理，不产生业务副作用。

## 正式切换和真实验证

候选门禁通过后依次替换正式 Control 和 Gateway。当前正式容器如下：

| 服务 | 容器 ID | 镜像 ID | 健康 | 重启次数 |
| --- | --- | --- | --- | ---: |
| Control Service | `5ebc018ddfeda82dbd02e9d5360b9b89863eecc72c7ef7c332f29346a0877465` | `sha256:e2e87cf7e4e70417274d805ce76bc873c53091d9d9e7febbe355e74fd9b98135` | healthy | 0 |
| Online Gateway | `4c1dac12923120dd258a5b215a2547cddcdce4088da6ba2e868b07b34427d17a` | `sha256:10f61ceac2af9bd10937c9ce0a18f0b79d763f7e844526f8410aa3e486ab37b7` | healthy | 0 |

正式端口验证覆盖：Control readiness、10 条任务分页、真实 `task_id` 运维详情、A 服务
`GET /api/course-jobs/{task_id}`、实例列表、实例 active leases、Kafka 聚合、Gateway metrics 和
两服务 CORS。A 服务查询返回业务码 `0`；Kafka 返回 `status=ok`、`publisher_status=ok`、
`outbox_pending=0`。Control、Orchestrator、Vision、Gateway、PostgreSQL、Redis、Kafka 和
MongoDB 在切换后均保持 healthy，两个新服务最近 200 行日志没有 `Traceback`、`ERROR` 或
`CRITICAL`。

## 旧资源清理

正式验证通过后删除两个候选容器，并删除以下旧镜像：

- Control：`sha256:7f86ac9f129c008d1dfd61206d8093640ee51b38a20ad9a4523b1b17dae5128b`，
  旧修订 `6ddbbf3aa2688d3d15905d8af7f54d5ed21c87bf`；
- Gateway：`sha256:d754ee136bed675dbd80fcd6af8608026be879d63d1fb7f28ef8dd42e954103d`，
  旧修订 `3182118d4ba3c7b47f20e0ca7f35ac2be2a4be1f`。

删除后按完整旧镜像 ID 查询均不存在；新日期标签和 BuildKit 缓存保留。Harness 未记录注册 token、
凭据、完整请求/响应正文、ASR/OCR 文本或媒体数据。

## 未完成边界

OpenSpec `6.3` 还要求在目标机验证 GPU exporter。本轮没有部署运维前端和 GPU exporter，因此只完成
了 `6.3` 中 Control/Gateway 的真实环境部分，不能将整项标记完成。GPU exporter 和前端联合验收由
对应开发窗口继续执行。
