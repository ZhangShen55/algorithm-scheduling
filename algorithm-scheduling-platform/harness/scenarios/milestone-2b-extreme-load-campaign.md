# 里程碑 2B A 服务极限负载 Campaign

## 当前范围

- OpenSpec 变更：`run-milestone-2b-extreme-load-campaign`。
- 当前权威拓扑：七类算子、21 个算子实例、18 个 GPU 实例、3 个 CPU PPT 实例、四个平台服务和四个中间件。
- A 服务模拟器只允许访问 `control-service:18100` 和 `online-gateway-service:18103`。
- Text Analysis、`PPT_KEYWORDS` 和 `COURSE_OVERVIEW` 不属于本 Campaign。
- 本场景是现有 217 条反例、26 条压力/恢复用例和 6 项 B 级人工复核之外的附加真实负载验证，不能替代原门禁。

## 初始保护基线

- 开始分支：`codex/milestone-2b-three-gpu-deployment`。
- 开始 SHA：`3cefc915317428cf17db037ba16023b48cd59783`。
- `text_analysis/README.md`、`5{n++}`、三个算子 Docker README、运维可视化设计草稿、`ppt_slice/docker/` 和 `text_analysis/docker/` 是开始前已有的用户 dirty/untracked 内容；本变更不得覆盖、删除或提交。
- `standardize-service-file-logging` 当前为 `54/72`，`retire-text-analysis-from-scheduling-platform` 当前为 `50/62`。剩余项主要是同一新 SHA 的远端构建、真实推理、日志、七算子 release 与最终复审，因此开始 SHA 不是最终 Campaign SHA。
- 最终 Campaign SHA 只能在上述两项的当前必需实现、七算子基线和本 Campaign 实现都纳入同一 clean commit 后冻结。

机器可读基线见 `harness/baselines/milestone-2b-extreme-load-campaign-initial.json`。

## 2026-08-23 目标服务器只读盘点

- 目标：`192.168.29.11`，x86_64、80 逻辑 CPU、125 GiB 内存、Docker 26.1.4。
- Docker 当前共 50 个容器：8 个运行、42 个停止；共 475 个镜像。没有执行删除、停止、重启、重标或 prune。
- 根文件系统约 1.5 TB，剩余约 103 GB、7%；`/data/course` 与 `/data/result` 位于同一文件系统且均存在。
- 由于剩余比例低于 10%，当前已经触发 Campaign 磁盘红线。允许继续本地实现、只读盘点和精确清理 dry-run；禁止直接启动远端负载阶梯。
- GPU0/GPU1 为 RTX 4090 D，GPU2 为 RTX 3090；三卡均约 24 GiB，盘点时没有计算进程。
- 服务器 checkout 停留在 `5f973adae6a81580ecd285ee81e203275fa14ba1`，不是本地开始 SHA。
- `18100`、`18103` 对外监听；PostgreSQL、Redis、Kafka、MongoDB、`18101`、`18102` 只在回环监听，符合当前端口边界。
- 旧 21 个七算子容器当前均已停止；四个平台服务与四个中间件构成当前 8 个运行容器。

## 负载主机基线

- 主机：`zhangshendeMacBook-Pro.local`，Mac17,2、arm64、10 逻辑 CPU、32 GiB 内存。
- 主地址：`192.168.28.144`；目标服务器为另一台 x86_64 主机，二者不共享 CPU、内存或 GPU。
- 系统 Python 为 3.9.6，不作为 Campaign Python 权威；平台 `.venv` 当前为 Python 3.12.13，
  `httpx/PyYAML/websockets/aiokafka` 分别为 `0.28.1/6.0.3/17.0.1/0.14.0`。正式加压必须
  使用同一 release 预检记录的 `.venv`，不能回退到系统 Python；服务器算子镜像继续遵循已确定的
  Python 3.11 合同。
- 打开文件上限为 1,048,575；正式加压仍必须实时记录 CPU、内存、socket、文件句柄、网络和事件循环漂移，避免把负载机上限误归因于平台。

## 当前证据结论

- 已达到：工作区保护、目标机只读库存、GPU/磁盘/端口和负载主机基线；Campaign catalog、
  北向负载生成器、离线/在线/实时 ASR 请求模型、护栏、指标数据模型、聚合报告、精确故障执行器、
  常驻生命周期、迁移账本、镜像生命周期、唯一中文部署手册和本地 fail-closed 协调入口。
- 本地统一门禁为 `167 passed`，Ruff、strict Mypy（20 个源文件）、`compileall`、导入、Bash
  syntax 与 OpenSpec strict 均通过。该结果只证明静态/单元层实现，不代表任何远端负载已经发生。
- 尚未达到：最终 clean Campaign Git SHA、真实指标/SSH/媒体下载探针接入、缩小版服务运行
  Campaign、媒体源下载基线、常驻部署、镜像清理 dry-run、远端七算子新 release、阶段 0–6、
  4 小时长稳和最终清理。
- 已知质量阻断：`ASR-013` 仍为 24 个中英混合术语片段中 9 个严重错误。性能测试可以继续，但最终结论必须保持质量阻断，除非同一最终 SHA 的新证据解除它。

## 2026-08-23 真实中间件集成补充

- 新增迁移账本真实 PostgreSQL 用例，使用隔离 `_test` 数据库和基础设施 Compose；首次执行
  `0001`–`0007`，重复执行不再重放，账本版本、文件、摘要和完整 Git SHA 均逐项核验。
- PostgreSQL、Redis、Kafka 联合专项为 `94 passed`、无 skip，覆盖课程任务、幂等、Outbox、
  DAG、租约、Kafka 提交及 Orchestrator 重启恢复。测试使用唯一数据库、Redis 前缀和
  topic/group，没有把仓储层完成方法当作算子输出，也没有改动业务数据库。
- 该证据达到真实中间件集成层级；缩小版四服务/算子运行、远端媒体下载、三卡发布和 Campaign
  仍未执行，因此不能将本节解读为里程碑 2B 已交付。

## 2026-08-23 本地生产运行时收口

- 已接入显式 Stage Adapter factory、连续 metrics sidecar、媒体下载 SSH 适配器和独立
  FaceRec 原图残留适配器。远端执行默认关闭，外部 runtime TOML 与源端证据必须位于整个
  Git 工作区外、当前 UID 所有、普通单链接文件且权限精确为 `0600`。
- live case 只有在业务证据、前后护栏、连续指标 summary 和当前 case 不可变 sample 路径
  同时有效时才能通过。常规/突发采样分别不超过 5 秒和 0.5–1 秒；长课目录字节只采 before/after。
- 查询执行器覆盖 50/100/300/1000 QPS、2/5 秒抖动、无抖动惊群、大 ASR 响应大小、整数状态
  与合法单调迁移。当前 Control 北向结果没有 `claimed_at/started_at`，所以优先级领取顺序用例
  会在提交 URGENT 前阻断，不会用提交顺序冒充领取顺序。
- 本地 Campaign/部署专项为 `315 passed`；Ruff、strict Mypy、compile/import、Bash syntax、
  OpenSpec strict 和 diff check 通过。远端 11–13 阶段、原图残留真实扫描和 4 小时长稳仍未执行。
- 平台完整 `tests/` 回归为 `3073 passed, 3 skipped`。3 项 skip 均为缺少外部
  `OPERATOR_REGISTRY_TOKEN` 的 Canonical FaceRec 集成，保持未执行语义，不以其他单元测试替代。

## 本地实施入口与失败关闭边界

Campaign 元数据模板位于
`deploy/templates/extreme-load-fixtures.example.yaml`；复制到 Git 工作区外并填入外部路径、
大小、时长和 SHA-256 后，使用：

```bash
deploy/scripts/run-extreme-load-campaign create-plan \
  --release-tag "$RELEASE_TAG" \
  --git-sha "$EXPECTED_GIT_SHA" \
  --seed 260823 \
  --control-origin http://192.168.29.11:18100 \
  --gateway-origin http://192.168.29.11:18103 \
  --fixture-manifest "$EXTERNAL_FIXTURE_MANIFEST" \
  --output "$RELEASE_ROOT/campaign-plan.json"

deploy/scripts/run-extreme-load-campaign status \
  --plan "$RELEASE_ROOT/campaign-plan.json" \
  --release-root "$RELEASE_ROOT"
```

`execute-case` 默认不访问北向端点；只有显式传入 `--allow-live-execution` 才可发送 HTTP/WS。
媒体下载、远端宿主机指标、残留扫描、故障语义、混合和长稳仍明确返回 blocked，不能通过手工
写入 passed 证据绕过。计划和逐案证据均以 `0600`、当前 UID、单硬链接、不可覆盖和完整
Campaign/release/SHA/case/phase 身份校验发布。

## 安全门禁

1. 目标机磁盘恢复到警戒线以上前，不进入负载阶梯。
2. 远端生命周期和故障注入始终只有一个写入控制者。
3. 清理只能依据经审核的完整容器/镜像 ID dry-run 计划执行。
4. 禁止 `docker system prune -a`、`docker compose down -v`、删除卷、删除 `/data/result`、删除模型和改写历史 release。
5. 每一阶段必须原子发布原始证据；未执行、证据缺失或重复 ID 不得聚合为通过。
