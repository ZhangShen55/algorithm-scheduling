# 运维控制台压测存储阻塞修复与源码纳管核对（2026-09-07）

## 范围与结论

- 本记录对应 OpenSpec 变更
  `standardize-ops-console-deployment-and-observability`，核对压测期间控制台多项数据
  同时加载失败的根因、修复发布身份和 GPU 指标采集器源码纳管状态。
- 压测故障修复为
  `e4a26415f01778517cd98e5f549b5edf8cf057fb`；GPU 指标采集器和 Compose 的补录提交为
  `4bd506113c7689906f3960a209042aeecd784653`。
- 两个提交都在分支 `codex/milestone-2b-three-gpu-deployment`。本记录发布前，
  `e4a2641` 已在远程历史中；`4bd5061` 由本轮单独提交，不夹带工作区其他改动。
- 本记录不表示多服务器 NFS 已实现或验收。当前 NFS 仅为设计讨论，没有对应的
  实现型 OpenSpec 变更。

## 故障根因与修复

压测后 `/data/course` 和 `/data/result` 文件数量增长，旧 `/ops/storage` 会在请求线程
递归扫描两个目录。现场该接口曾超过 20 秒未返回，Control Service CPU 曾约为
`138%`。前端总览又使用同一组 `Promise.all`，存储请求超时会连带实例、队列、Kafka、
网关和 GPU 等已成功的观测数据一起进入失败态。

`e4a2641` 实施以下修复：

- `GET /ops/storage` 默认只使用 `shutil.disk_usage()` 读取文件系统总量、已用量和可用量，
  `directory_bytes=null`；
- 只有人工显式请求 `GET /ops/storage?include_directory_bytes=true` 才递归统计目录字节；
- 前端存储与网关指标请求增加 3 秒超时和局部降级，子项失败不再清空其他
  已成功数据。

该修复不改变 A 服务 `/api/course-jobs` 契约、算子 HTTP/WebSocket 契约、Kafka envelope、
数据库结构或已有端口。

## 本地验证

2026-09-07 在当前工作区执行：

```bash
algorithm-scheduling-platform/.venv/bin/python -m pytest -q \
  algorithm-scheduling-platform/tests/test_operations_api.py
cd algorithm-scheduling-ops-console
npm test -- --run
npm run build
cd ..
algorithm-scheduling-platform/.venv/bin/python -m compileall -q gpu_metrics_exporter
algorithm-scheduling-platform/.venv/bin/ruff check gpu_metrics_exporter/app.py
docker compose \
  -f algorithm-scheduling-platform/deploy/docker-compose.gpu-metrics.yml \
  config --quiet
openspec validate standardize-ops-console-deployment-and-observability --strict
```

结果：Operations API `13 passed`；前端 `6 passed`，TypeScript/Vite 生产构建通过；GPU Exporter
compileall、Ruff 和 Compose 解析通过；OpenSpec 严格校验通过。

## 远程运行身份

2026-09-07 对 `192.168.29.11` 进行只读复核：

| 服务 | 镜像 | 镜像 ID | 容器 ID | revision | 状态 |
| --- | --- | --- | --- | --- | --- |
| Control Service | `algorithm-scheduling/control-service:v1.2_260903_e4a2641` | `sha256:c39d6b0e2a761341fd2c8e4e2e060faf8d7f860870cba1da11f5931f3bd23804` | `e0f355d65302675718b0c26cc8f31828bca99067bed0c043bfa2aa82448674d2` | `e4a26415f01778517cd98e5f549b5edf8cf057fb` | `running/healthy`，重启 0 |
| Ops Console | `algorithm-scheduling/ops-console:v0.3_260903_e4a2641` | `sha256:5bedaedb859d5f19cfd8a467eb60d58193382180a05e88fd2adeea1af51344aa` | `eb684a23102b5bffd84e4a5718999f062067fcc0b5e0a6ef9c6063223a56ced9` | `e4a26415f01778517cd98e5f549b5edf8cf057fb` | `running/healthy`，重启 0 |
| GPU Exporter | `algorithm-scheduling/gpu-metrics-exporter:v0.1_260903_2ace5aa2` | `sha256:f4e149853c8f8320d8a56dc88808052a7f17f497f0c7dd188096b55804a919a0` | `538bcbd2ce3542373a4988efe36957d6e92c37de0bba806ef23df5ce5b1e3d51` | 旧镜像未写入 OCI revision | `running/healthy`，重启 0 |

GPU Exporter 本地 `app.py` 与远程容器 `/app/app.py` 的 SHA-256 均为
`3e94a160e4c833b8c67ab49469c7c0e104707fd226d9789cab1770bf6417991c`；本地 `requirements.txt` 与远程
`/tmp/requirements.txt` 的 SHA-256 均为
`d659ffce9e5e40bc784e794bdd540b8957fce8d7c1f25102249c6dde3d1f72c7`。这证明 `4bd5061` 纳管的运行源码
与当前远程容器一致；本轮不重建或替换该容器。

## 远程接口复核

- `GET /ops/storage?include_directory_bytes=false` 返回 HTTP 200，本次墙钟约
  `0.012s`；`course` 和 `result` 均为 `directory_bytes=null`。
- 两个目录返回相同的文件系统数据：总量 `1622228520960`、已用
  `1328765882368`、可用 `210982490112` 字节。这类信息等价于在 Control 容器可见挂载上
  执行 `df`，不是 `du` 的目录逻辑大小。
- `/ops/operator-instances` 返回 21 个真实实例；`/ops/readiness` 的 PostgreSQL、Redis
  和 Schema 均就绪。
- `/ops/kafka` 返回 `status=ok`、`publisher_status=ok`、`outbox_pending=0` 和
  `consumer_lag=0`。
- 任务列表默认按 `updated_at desc` 返回最新真实课程；GPU `/gpu` 返回两张
  RTX 4090 D 和一张 RTX 3090。
- 从控制台 Origin 发起标准 OPTIONS 预检，Control 返回 HTTP 200，GPU Exporter 返回
  HTTP 204，两者均返回 `Access-Control-Allow-Origin: *`。
- 控制台 `http://192.168.29.11:5174/` 返回 HTTP 200。

## OpenSpec 与保护边界

- `enhance-ops-task-observability-and-kafka-events` 为 `52/52`，已完成。
- `standardize-ops-console-deployment-and-observability` 在补录完成事实后为
  `27/28`。唯一未完成项是 3.6：补齐所有页面真实响应映射与空态/错误态测试。
  本记录不虚假将其标记完成。
- 本轮只读检查远程容器，未重启或替换 Control、前端、GPU Exporter、算子、
  Orchestrator、Vision、Gateway、PostgreSQL、Redis、Kafka 或 MongoDB。
- 未执行 prune、未删除容器/镜像/卷/模型/媒体，未删除 `/data/result`。
