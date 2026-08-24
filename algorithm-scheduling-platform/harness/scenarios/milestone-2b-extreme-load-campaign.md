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

## 2026-08-23 日志与七算子依赖基线核对

- `standardize-service-file-logging` 和 `retire-text-analysis-from-scheduling-platform` 仍是保留远端任务的
  active 变更，不将它们误报为已归档或远端全部验收。
- Campaign 基线祖先提交已包含日志主实现 `56d42f5`、11 份根配置修正
  `5a31ebd`、Text Analysis 退役主实现 `7cbfaf4` 及其后续七算子收敛修正。
- 当前 Git 树的 11 份根 `config.toml` 均声明 `logs/{instance_id}/application.log`、
  100 MiB、7 日和 stdout/file；11 份 Dockerfile 均预建 `logs/`。
- `operator-topology.json` 为 7 类、21 实例、18 GPU、3 CPU PPT 和 14 个配置解析进程；
  当前 Compose、镜像与 endpoint 权威不包含 `text_analysis`。
- 聚焦静态合同验证为 `46 passed`，两个受影响 active change 均通过 strict validate。
  任务 1.2 因此完成；最终发布仍必须使用本 Campaign 后续产生的 clean 完整 SHA。

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

## 2026-08-24 构建前清理执行结论

- clean detached SHA `4acc7c44dab8a3eb639c9cfe87f1da971ac6f47b` 下的精确镜像计划已执行，
  396 个经审核的悬空镜像全部删除，Docker 镜像库存由 475 降至 79。
- 清理结果账本为 `PASS`，原 8 个平台/中间件容器仍 8/8 healthy，三张 GPU 与 NVIDIA Runtime
  仍可用；持久数据、模型、卷、Git 和历史证据未被触碰。
- 根盘可用空间仍为 `110115663872` 字节（约 102.6 GiB/6.8%），未达到 15% 且 150 GiB 的
  警戒线。BuildKit 仍持有 234 GB private cache 和 74.19 GB shared cache，合计声明可回收
  308.2 GB。
- 因此本场景继续失败关闭：11.2 不完成、11.3 不启动。缓存不属于已审核镜像 ID 计划，未获得
  新的受控清理边界前不得通过宽泛 prune 绕过。

## 2026-08-24 缓存清理授权与解除结论

- 用户随后逐次批准 `docker buildx prune --all --force --keep-storage 100GB`，仅授权删除可重建
  BuildKit 缓存。执行前后证据均位于同一远端 release 的 `cleanup/` 且权限为 `0600`。
- Build Cache 总可回收从 308.2 GB 降至 162 GB；根盘可用空间达到 231.98 GiB/15.35%，同时
  满足 150 GiB 和 15% 警戒线。
- 缓存操作前后 76 个普通镜像完整 ID、8 个运行容器完全一致，8/8 容器 healthy；当前 11 个
  发布镜像、11 个回滚镜像和 3 个基础镜像零缺失，NVIDIA Runtime 容器可见 3/3 GPU。
- 原计划中仅因停止容器引用而保护的 VLLM 镜像及两个无关镜像在缓存操作开始前已由外部状态
  变化移除；清理前后清单证明不是本命令所致。该漂移不回滚，但必须保留在最终风险记录中。
- 任务 11.2 完成；只有新 clean Git SHA 同步到目标机后才允许开始 11.3。

## 2026-08-24 七算子构建部分完成与护栏停止

- 目标机同步到 clean SHA `0e11d3d70fd43d49f43dac44a6f8eec97f3782a1` 后启动七算子构建。
  ASR Offline 成功生成 `amd64` 镜像
  `sha256:23091a1b326309e56acf37a43a1470896d77f35d3f5be10e10fc992ce4930cb6`，revision label
  与该 SHA 一致。
- 单个 ASR Offline 构建使根盘从 231.98 GiB/15.35% 降至约 218.46 GiB/14.46%。构建入口在
  下一镜像开始前按 `MIN_ROOT_FREE_GIB=227` 失败关闭，日志终态为
  `root disk has 229075452 KiB free; 227 GiB required`；第二个镜像没有开始。
- 当前只有 1/11 目标镜像属于新 revision，其余目标仍是旧 revision。旧 ASR Offline 镜像仍由
  停止容器引用并按完整 ID 保留；原 8 个运行容器保持 healthy，无 OOM/Xid，也无残留构建进程。
- 11.3 保持未完成。达到新的受控空间方案前，不降低门禁、不启动部分新栈，也不把旧镜像重标
  为新 revision；再次清理 BuildKit 缓存仍须遵守逐次明确授权。

## 2026-08-25 二次缓存清理与当前 11 镜像构建结论

- 用户再次逐次批准固定 BuildKit 命令。执行前后普通镜像完整 ID 均为 78 个且逐字节一致，8 个
  运行容器、Runtime 和 3 个 GPU 同样一致；命令退出码为零，根盘从约 218.45 GiB 恢复到约
  237.2 GiB，没有删除镜像、容器、卷、模型、Git、结果或历史证据。
- FaceRec CUDA 11.8 基础镜像经本地完整 tag inspect 证明已存在；早先的“本地缺失”是把外部
  manifest 查询超时与截断镜像列表误作本地事实，已纠正。OCR 基础镜像下载和构建也持续推进，
  没有触发“基础镜像无法下载或停滞”的人工提供边界。
- SHA `22717cf7abb584bb1891d86c89e215729ee48955` 下七算子统一构建和四平台逐项构建全部通过。
  11 个目标镜像均为 `amd64`、revision 精确匹配、完整 ID 互异，容器内 `logs/` 均存在；六个
  模型算子的资产目录非空。Text Analysis 未进入矩阵、构建或验证。
- 旧 11 个回滚镜像和 3 个基础镜像均继续存在，8 个原运行容器全程保持 healthy；没有 OOM、
  NVIDIA Xid 或残留构建进程。终态约 228.49 GiB/15.12%，只高于 227 GiB 门禁约 1.49 GiB。
- 11.3 完成；本结论不包含 21 实例启动、18 GPU 进程、3 CPU PPT、注册、租约或 7/7 Smoke，
  这些仍属于 11.4 及后续任务。

## 2026-08-25 旧库迁移账本采纳场景

- 首次常驻启动在重放 `0001` 时与既有表冲突，且失败发生在平台替换和算子启动之前。
  现场保持 8 个原容器 healthy，新的 21 个算子实例未部分启动。
- 当前远端 schema 是完整 v6 形态、v7 退役注释未应用；实施路径因此收敛为“严格采纳
  `0001`–`0006`，再通过普通前向迁移执行 `0007`”。
- 本地真实 PostgreSQL 使用随机 `_test` 库验证空库、v6/v7、账本结构、表访问方法、
  序列并发锁、账本锁内二次校验、新/旧并发 DDL 顺序、并发 `setval()`、identity 序列位置/上界/持久性和其他结构/数据漂移共 22 个用例，
  全部通过且无 skip；部署/Harness 聚焦套件 31 个用例通过，静态和 OpenSpec 严格门禁通过。
- 该结论只完成 8.7 实现与本地集成闭环。远端业务库备份、实际前缀采纳、v7 应用、新 SHA
  的 11 镜像重建和 11.4 常驻栈启动仍是下一步，不得写成远端部署已通过。
- 远端追加只读核对 6 个 owned identity 序列：5 个非空表均满足下一生成值严格大于
  `MAX(id)`，`visual_fallback_values` 为空且序列仍未调用。本查询未修改账本、序列或业务行。

## 2026-08-23 目标机 Git 准备合同补强

- 远端已证明默认 SSH 身份无法访问 GitHub；部署 Git 操作必须显式使用
  `/root/.ssh/algorithm-scheduling-github-deploy`，并同时启用 `IdentitiesOnly=yes` 和
  `StrictHostKeyChecking=yes`。该路径只是 Git 外的预置密钥引用，私钥内容未进入文档或证据。
- 部署手册现同时覆盖首次 clone 和已有 checkout 的 fetch，之后必须 detached checkout
  到经批准的 40 位 SHA，并在切换前后检查 tracked/untracked 工作树为空；现有
  `origin` 必须与批准仓库一致，fetch 直接指向批准 SHA。
- 任一 Git 身份、host key、fetch、SHA 或 clean-worktree 检查失败都停止发布；手册明确
  使用 `set -euo pipefail` 和禁止破坏性 reset/clean 来保证该边界。原子新目录 checkout
  继续由 `checkout-release`/`DEP-020` 维护；固定生产目录使用本手册的 bootstrap/更新流程。
  该补强不代表远端 11.x 发布已通过。

## 2026-08-23 目标机 Git 同步与 NVIDIA Runtime 恢复

- 目标机 checkout 的原 `origin` 是历史本地 bundle。为不删除历史获取来源，已将它保留为
  `bootstrap-bundle`，并将 `origin` 精确设为已批准的 GitHub 仓库。使用 Git 外 `0600`
  Deploy Key 按完整 SHA 获取后，目标机曾 clean detached checkout 到
  `1aebadd43189aaba8545a042f530f04d734e0a9f`。该 SHA 是部署手册修正提交，不是最终发布 SHA。
- NVIDIA Container Runtime 1.13.5 的二进制已安装，但 Docker daemon 原配置只注册
  `runsc`。在用户已批准暂停现有业务容器的边界内，先将原 `/etc/docker/daemon.json`
  保存为 `/root/workspace/docker-daemon.pre-nvidia-runtime.260823.json`（root 所有、`0600`、
  单硬链接），再使用 `nvidia-ctk runtime configure --runtime=docker` 配置并重启 Docker。
- Docker 重启共约 119 秒。除原 8 个平台/中间件容器外，5 个与本平台无关但带
  自动重启策略的容器也被 Docker 启动。依据重启前已记录的 8 个完整 ID，对差集中的
  5 个额外容器逐一按完整 ID 停止；未删除容器、镜像或数据卷。终态恢复为原
  8 个容器运行且 8/8 healthy。
- `docker info` 现同时包含 `nvidia`、`runc`、`runsc` 和 containerd runc runtime；使用已有
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7` 执行一次性容器探针，容器内
  `nvidia-smi` 返回 GPU 0/1/2 及三个不同 UUID。
- 复审同时发现 host preflight 的空工作树判定会掩盖 `git status` 自身失败。实现现改为
  先采集状态且对非零退出显式失败，新增 fake Git 回归。在该修复提交、目标机再同步和
  完整 preflight 通过前，任务 11.1 保持未完成。

## 2026-08-25 新 SHA 镜像重建磁盘门禁结论

- 目标机 clean detached checkout 到迁移修复 SHA
  `2548fcecbbc41d27c2e382552afdde1ec6d6856b` 后重新执行七算子权威构建入口。
- ASR Offline 完成模型、上下文、wheel 和 Docker 构建，目标镜像为
  `sha256:9026d12123ee7aac1ea7bbf5f178f4fdd1a78a0b64aa1d434bdceda580865a82`，
  `amd64` 与 revision 均符合。其余 10 个目标尚未开始，当前只达到 1/11。
- 构建入口在第二个镜像前报告
  `root disk has 232505476 KiB free; 227 GiB required` 并失败关闭；退出后可用空间约
  221.74 GiB，仍不满足门禁。原 8 个运行容器 healthy，旧发布/回滚镜像完整，无 OOM、Xid
  或残留构建进程。
- 本场景因此把 11.3 退回未完成并阻止 11.4。只允许继续只读诊断和本地实现；再次清理
  BuildKit 缓存必须取得新的逐次明确授权，不能复用此前授权、降低门禁或启动部分新栈。

## 2026-08-25 失败 release 精确退役结果

- 当前 release 的 prebuild 计划保护 49 个当前、回滚、基础、allowlist 和容器引用镜像；
  dry-run 摘要为 `967aff08573dfb4715280ec683e6c2d5b7dde56e9aad03dc409a9b29ac8b660b`。
- 审核后的三个候选均无容器引用，分别属于旧失败构建 `0e11d3d7...` 和已退役失败 release
  `ecadb0cb...`。执行结果为 `PASS`，候选完整 ID 均已删除，8 个原运行容器仍 healthy。
- 镜像账本估算可回收 28.285 GB，实际只释放约 7.253 GB；其余大层继续受 BuildKit cache
  引用。根盘回到约 228.49 GiB，但仍只有约 1.49 GiB 构建裕量。
- 因此本场景没有重新完成 11.3，也没有进入 11.4；未执行 BuildKit prune。后续必须使用
  包含本地功能补强的新最终 SHA，并为每次缓存清理重新取得明确授权。

## 2026-08-25 在线图片与 FaceRec Campaign 冻结前补强

- Online Gateway 已将出站 HTTP 连接池实际接线为 `2048/512`，校验有界池等待；
  人脸管理五个入口继续固定单一 FaceRec 实例，只有识别入口通过租约路由三实例。
- 四个在线图片入口在申请租约前校验 Base64 语法、Data URI 媒体类型、解码后大小和
  图片可解码性；完整图片解码移入线程执行，VBas 不再重复解码。常规、49 MiB、
  超过 50 MiB、非法 Base64、非图片 Data URI、非图片字节和截断图片均已进入 Campaign；
  拒绝请求必须观测为零租约、零算子调用。
- FaceRec 一致性和容量压力分开：人物事实检查以 30 并发执行，不在声明总容量
  384 上用 500/1000 并发制造预期过载；真实识别容量仍由独立 1–1000 在线图片阶梯测量。
- 500/1000/5000 三档改为可重放的嵌套编号集，利用当前 FaceRec 单个/批量接口的
  upsert 语义防止跨档重复积累。删除后未命中按真实算子业务码 `252` 或不含已删 number
  判定；正常识别允许真实 top3 候选但不允许重复事实。三实例证据要求三个固定
  FaceRec 实例均有正请求增量，不依赖随机路由恰好均匀，其他算子的零增量键不会造成误失败。
- `save_person_photo=false` 证据现同时核验三个 FaceRec 运行配置、MongoDB embedding/图片字段、
  FaceRec 与 Online Gateway 日志、容器目录和持久目录，仅发布聚合计数，不把人脸底图写入报告。
- 本地验证为 `269 passed` 的 `tests/extreme_load`、`49 passed` 的 Online Gateway 测试、
  Ruff、strict Mypy 和 `compileall` 通过。这只完成 OpenSpec 5.5、5.8、5.9 的实现层；
  `192.168.29.11` 的四类图片、人脸库和原图残留真实执行仍属于未完成的 12.4。

## 2026-08-25 - `b7d5c4a` 常驻栈与独立手册复现

- 目标机 clean detached SHA 为 `b7d5c4a2a8bba6bacbd6414b7162abb0d427beff`。四中间件、四平台、
  21 个算子实例均 healthy；21/21 注册为 `ONLINE/model_ready`，18/18 GPU PID/cgroup
  与 GPU0/1/2 精确对应，3 个 CPU PPT 无 GPU 请求，租约前后均为 0。
- 算子直连 7/7 Smoke 通过；PPT 使用 433 MiB 真实 fixture 生成切片和终态 manifest。
  Online Gateway OCR 的正常请求与 50 MiB 解码上限均正确，但旧 Smoke 客户端在网关已根据
  `Content-Length` 提前拒绝后仍上传 72 MiB 正文，导致 `Broken pipe`。修复后第三案只发
  超限声明头，真实远端结果为 `ONLINE-OCR-001=0`、`002=40001`、`003=40001`。
- 未依赖实施者上下文的独立复现证明：对外只有 `18100/18103`可达，其余 27 个宿主机
  端口只绑定回环；新生成的 runtime/operators preflight、inventory、GPU PID/cgroup、注册、
  租约和配置证据均通过，旧 canonical Smoke 的 10 个文件哈希和元数据保持不变。
- 独立复现同时发现两个手册缺口：原 `status-production-stack` 会被启动阶段
  `OPERATOR_REGISTRY_TOKEN` 插值阻断；手册写死的 `course/P.mp4` 返回 404，使 PPT 任务
  最终为 70。前者收敛为只读 `status/stop` 使用不写入容器的 Compose 解析占位值，
  `start` 仍强制显式 token；后者改为必填 `PPT_SMOKE_URL`、range 可达预检和轮询至
  `PPT=60`。
- 本节证据根位于远程 release 的 `independent-validation/independent-11_5-11_7-20260824T194820Z/`。
  它支持 `b7d5c4a` 的 11.5/11.6 事实，但因手册缺口和后续实现已改变 Git SHA，11.3–11.7
  仍要在新最终 SHA 重建、复现后才可最终勾选。

## 2026-08-25 - Campaign 生产故障见证与本地门禁收口

- 新增生产 `mixed/soak/fault` 适配器。故障动作严格绑定当前 Campaign 维护锁、Compose
  project/service 和 64 位容器 ID，按 `stop -> 业务见证 -> start -> 恢复见证` 串行执行；
  即使故障后维护锁丢失，也只允许补偿启动本轮已成功停止的精确容器 ID。
- 七算子和三组 GPU 的恢复见证使用真实 `operator-topology.json` 容量与生产 Redis 排序语义。
  在线图片请求使用唯一 `X-Trace-ID`，在请求存活期间经 Control active-leases 精确绑定
  `work_context.trace_id` 和目标实例；OCR GPU2 的最大见证宽度为 `513`，仍受已批准的
  `1000` 并发硬上限约束，不用背景 metrics 冒充当前请求。
- 四平台恢复分别验证 Control 的 21/21 故障窗口后心跳、Orchestrator 的任务级
  Outbox/Kafka/单一 DAG、Vision 的窗口内运行节点恢复为 `60` 且产生唯一非空结果摘要、
  Gateway 的真实在线 OCR HTTP 与 ASR WebSocket 中断重连。Kafka 不要求持续背景提交时
  全局 Outbox 为零；Redis 验证故障期当前请求 `50301`、旧租约回收、21/21 注册、新请求
  acquire/release 和请求后容量回到基线。
- 最终本地故障聚焦为 `111 passed`，Campaign/部署专项为 `432 passed`；Ruff、strict Mypy
  和 `compileall` 通过。平台完整回归为 `3214 passed, 3 skipped`，3 项仍是缺少外部
  Canonical FaceRec Token/容器的既知未执行项；独立终审无 P0/P1/P2。
- 本节只证明实现、失败关闭和本地模拟边界。最终 SHA 的真实 Docker 故障 Campaign、
  4 小时长稳、217 条反例、26 条压力/恢复和 6 项 B 级复核尚未执行；媒体源
  `192.168.29.12` 的 CPU、内存、网络和连接数证据仍不可获取，所以 4.9/11.1 继续阻断，
  不得越过阶段 0 发布“全部符合”。
