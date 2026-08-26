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

## 2026-08-25 - `23364ffb` 旧 SHA 部署手册独立复现

- 未使用实施者口头命令，按手册在 `192.168.29.11` 复验 clean detached
  `23364ffb7849e3f68eda56135bcb74ceadb27851`、`x86_64`、Docker/Compose、NVIDIA runtime、
  三张 GPU 和约 240 GiB 根盘可用空间。同 SHA 按手册 9.1 先跑 status；结果为
  `PASS`，四中间件、四平台、21/21 注册、18 GPU、3 CPU 和零活跃租约均符合，
  因此不重复调用 start，也不从运行容器反提取 registry token。
- A 服务 18100 使用媒体源可达的 433 MiB PPT 真实视频，任务
  `deploy-smoke-ppt-23364ffb7849` 由 `10 -> 50 -> 60`；18103 Online Gateway OCR 三案为
  `ONLINE-OCR-001=0`、`002=40001`、`003=40001`。
- 独立执行发现并修正四个手册可重放缺口：不再默认旧 `v1.0_260812`
  release/model 路径；明确真实 token 必须由批准 secret 通道注入；把已跟踪的非敏感
  OCR 图安全复制到 Git 外固定路径；为 PPT 终态增加 `0600` 快照，并在已有
  Online Smoke 证据时校验 SHA/release 后复用而不删除重建。手册专项为
  `11 passed`，`git diff --check` 通过。
- 证据位于远程
  `deploy/reports/milestone-2b/releases/v1.0_260825/23364ffb7849e3f68eda56135bcb74ceadb27851/`：
  `production/production-stack.json` (`27dc80f6...`)、
  `production/production-stack-status.json` (`4b854805...`)、
  `production/a-service-ppt-smoke.json` (`4b83f61a...`) 和
  `online/online-ocr.json` (`81cdd630...`)，均为非 symlink `0600` 文件。
- 本轮完成后 Campaign Docker metrics 修正将产生新 SHA，所以这些证据只作为
  `23364ffb` 旧 SHA 复现事实，不勾选 OpenSpec 11.7；新最终 SHA 仍须重建并重放。

## 2026-08-25 - 阶段 0 首次执行阻断与指标探针修复

- 首次 `BASE-ONLINE-VBAS` 没有发出业务负载，前置采样因 Docker metrics 解析失败而
  STOP；原 `base-online-vbas.json` 是 write-once 失败证据，继续保留且不在同一
  Campaign 路径重写。
- 逐项真实探测定位到唯一失败面为 Docker。目标机 `docker stats` 返回
  `126.1MiB`、`254.2MiB` 等已舍入的人类可读值，旧解析器错误地要求乘以单位后必须
  精确为整数 byte；其他 load host、target host、GPU、Kafka、Control 和 Gateway 探针
  均通过。
- 修复后 Docker 内存值按显示精度取最近整数 byte；TaskGroup 失败只对外报告安全探针名，
  不再只留下 `ExceptionGroup`，也不泄露远端命令输出。真实汇总采样的前后护栏均为
  `CLEAR`，3 个样本包含 29 个容器、3 张 GPU、可证明的队列/Outbox/Kafka lag。
- 新实现必须先提交形成新 SHA，并按该 SHA 重建 11 镜像及重放 11.3–11.7。之后为
  阶段 0 创建新的受控 Campaign attempt；不得复用或覆盖旧失败 case 文件。

## 2026-08-25 - `e91f5b21` 当前发布与阶段 0 在线定位子集

- 当前发布为 `v1.0_260825`，完整 SHA 为
  `e91f5b21cb458983f8ab1eea2518e33579f4836d`。远端 release 根为
  `deploy/reports/milestone-2b/releases/v1.0_260825/e91f5b21cb458983f8ab1eea2518e33579f4836d/`。
- 11.3–11.7 已在同一 SHA 完成：11 个互异 `amd64` 镜像的 revision 均精确匹配；常驻状态为
  `PASS`，包含 4 个中间件、4 个平台服务、21 个算子、18 个 GPU 实例、3 个 CPU PPT 实例、
  21/21 注册和零活跃租约；7/7 算子 Smoke 通过。端口证据恰有 29 个宿主机端口，仅
  `18100/18103` 对外，其余 27 个绑定回环。独立手册复现还得到 PPT 任务
  `deploy-smoke-ppt-e91f5b21cb45` 终态 `60` 和 Online OCR 业务码 `0/40001/40001`。
- 独立执行者只按手册 9.1 操作；因同 SHA 的 status 已为 `PASS`，复用已校验的
  `production/production-stack.json`，没有重复执行 start。复现过程不依赖口头补充，未发现
  真实命令漂移或缺失步骤，因此 11.7 在当前 SHA 下完成。
- 关键远端证据文件均为 root 所有、`0600`、单硬链接：
  `build/release-images.inspect.json`（`6ac2aa34...`）、
  `production/production-stack-status.json`（`e12410fb...`）、
  `preflight/port-boundary.json`（`070f3567...`）和
  `smoke/cases.json`（`bd714358...`）。这些文件支持 11.3–11.7，但不补足 11.1 的媒体源资源证据。
- 新 Campaign attempt 为
  `deploy/reports/milestone-2b-load/v1.0_260825/e91f5b21cb458983f8ab1eea2518e33579f4836d/attempts/phase0-online-e91f5b21cb45-20260825001147/`。
  五个定位用例的真实北向结果为：

| 用例 | 结果 | 单请求/会话实测 | 租约与实例证据 | 指标样本 |
| --- | --- | --- | --- | ---: |
| `BASE-ONLINE-VBAS` | `passed` | `0.181751` 秒 | 获取/释放各 1，`vbas-gpu0` 请求增量 1 | 2 |
| `BASE-ONLINE-FACE` | `passed` | `0.069775` 秒 | 获取/释放各 1，`facerec-gpu0` 请求增量 1 | 2 |
| `BASE-ONLINE-SCREEN-DET` | `passed` | `0.246660` 秒 | 获取/释放各 1，`screen-det-gpu0` 请求增量 1 | 2 |
| `BASE-ONLINE-OCR` | `passed` | `0.139112` 秒 | 获取/释放各 1，`ocr-gpu0` 请求增量 1 | 2 |
| `BASE-ASR-WS` | `passed` | `464.222339` 秒；2294 块，零失败、零缺失终态 | 获取/释放各 1、拒绝 0 | 93 |

- 五案前后护栏均为 `CLEAR`，运行时汇总均为 `passed`；29 个容器重启增量全为 0，宿主机
  OOM 增量、Kafka lag 和 Outbox pending 均为 0。上述证据只完成五个阶段 0 在线定位用例，
  不等于完整阶段 0 或 OpenSpec 12.1 完成。
- frozen manifest 中 T/S/P 三条 URL 的 Range 探针均返回 `206` 且长度精确匹配。目标机
  1/3/10/30 并发修正复跑共 `44/44` 成功，累计成功载荷 `37,788,131,032` B、目标入站
  `40,954,896,306` B；payload 吞吐从约 `116.97` 降至 `112.99` MB/s，四档最大建连耗时为
  `2.29/2.93/4.11/14.74` ms。修正证据为
  `preflight/media-download-baseline-partial-rerun1.json`（`aed4c897...`）。首份
  `6a7b34f1...` partial 保持原样，修正证据只 supersede 当时的下载和稳定 404 解释。
- `192.168.29.12:5555` 当前仍没有受信的源端 CPU、内存、发送网络和连接数遥测；四项均为
  `NOT_COLLECTED`。因此下载归因状态仍为 `BLOCKED`，OpenSpec 4.9、11.1、四个正式
  `BASE-MEDIA-DOWNLOAD-*` 和 `PHASE-0-COMPLETE` 继续阻断；不得以目标端下载或五个在线通过
  结果进入阶段 1，也不得发布完整阶段 0 符合结论。

## 2026-08-25 - 源端遥测补齐与 PPT 轮询中断事实

- 同一 attempt 随后的正式执行已取得 `192.168.29.12` 源端遥测，证据标识为
  `source-fileserver-media-download-rerun2-20260825T103743+0800`，采集时间为
  `2026-08-25T10:43:14.167033+08:00`。CPU、内存、发送网络和连接数四项均嵌入四份
  `BASE-MEDIA-DOWNLOAD-*` 规范 case 证据；此前 `NOT_COLLECTED` 的 partial 文件是当时事实，
  继续原样保留，不回写也不删除。
- `BASE-MEDIA-DOWNLOAD-1/3/10/30` 四案全部为 `passed`，请求数分别为 `1/3/10/30`，
  合计 `44/44` 成功、零失败。规范证据位于当前 attempt 的
  `campaign/phase-0-baseline/base-media-download-{1,3,10,30}.json`。
- Runner 随后提交 `BASE-OFFLINE-PPT`，任务 ID 为
  `load-campaign-v1-0_260825-e91f5b21cb458983f8ab1eea2518e33579f4-72934bede6903f60-base-offline-ppt-0-b89649ed404a37c2`。
  只读北向终态复核得到 `PPT=60`，`PPT_SLICE=60`、`PPT_OCR=60`；固定返回的未请求
  `ASR/TEACHER_BEHAVIOR/STUDENT_BEHAVIOR` 槽位均为 `0`。PPT 业务实际执行成功，但旧轮询器把
  四个固定槽位一起要求为终态，因而没有结束该 case。
- 中断时没有发布规范 `campaign/phase-0-baseline/base-offline-ppt.json`。已保留
  `campaign/runtime-metrics/BASE-OFFLINE-PPT/00000001.json` 至 `00000053.json` 共 53 个部分
  运行指标样本，时间范围为 `2026-08-25T03:02:59.635483Z` 至
  `2026-08-25T03:07:39.361109Z`，首末护栏均为 `CLEAR`。中断事实另存为 attempt 根目录的
  `attempt-interruption-offline-polling.json`；未重跑正式 case，也未覆盖既有证据。
- `BASE-OFFLINE-ASR`、`BASE-OFFLINE-TEACHER` 和 `BASE-OFFLINE-STUDENT` 尚未开始。轮询修复现只
  评价非零请求槽位，仍把 `60` 判为成功、`70/80` 判为失败，并要求至少存在一个请求槽位；
  新增聚焦用例 `6 passed`，`test_execution.py` 为 `18 passed`，完整
  `tests/extreme_load` 为 `375 passed`，Ruff、strict Mypy、`compileall` 和
  `git diff --check` 均通过。由于 PPT 规范 case 证据缺失且另三项离线基线未开始，
  `PHASE-0-COMPLETE` 仍不得声明完成。

## 2026-08-25 - `2154c40` 阶段 0 诊断、运行时修复与新短媒体

- 诊断 attempt 为 `phase0-rerun-2154c40cbe03-20260825122117`，绑定完整 SHA
  `2154c40cbe03b7cef7a8d24caa62bea119d94b9c`、seed `2608252220`。四个正式媒体下载 case
  全部通过；30 并发为 `30/30` 成功、接收约 27.08 GB、聚合吞吐约 115.85 MB/s，前后护栏
  均为 `CLEAR`。首次容器内短媒体预检因 Compose 插值缺失失败，原证据保留；专用重试预检
  通过，三轮 T/S/P 首块均可读。
- `BASE-OFFLINE-PPT` 在 32.27 秒进入失败终态，规范 case SHA-256 为
  `45dc663ce32c044cdaa5b01c9efa9497590efcc52cba6aa01531cb4013a12be9`。只读北向查询显示
  `PPT_SLICE=70`、原因“接收网络码流帧异常”，`PPT_OCR=20`。真实处理已读取 151 帧、形成
  5 个 observation 和 1 张切片；根因是正常 EOF 未唤醒消费者、EOF 与队列超时混用，以及
  恰好 5 帧时使用了错误的严格大于阈值。
- `BASE-OFFLINE-ASR` 在 2.06 秒进入失败终态，规范 case SHA-256 为
  `53c72d8ec1b17dff76b09fa004a6bc8c0cddc209ca68cd8919e76ad3813ecd4b`。算子 HTTP 成功返回，
  业务码为 `4008`“音频文件为空或未检测到任何人声”；旧 5 秒教师 fixture 不具备有效授课
  语音，因此不能作为 ASR 质量或容量结论。
- 教师视觉任务停留在 `TEACHER_BEHAVIOR_ANALYSIS=50`，Vision Orchestrator 随后 unhealthy。
  5 秒视频在 `4.999s` 没有生成帧，异常未写失败终态而直接结束消费循环；此前 5% 进度晚于
  失败事实到达时还会触发 Orchestrator 状态冲突。现场保持 29/29 容器运行、28/29 healthy、
  21/21 注册、18 个 GPU 进程归属正确，零 OOM、零重启、零活跃租约。
- PPT 现用独立正常 EOF/错误/取消信号立即唤醒消费者，并将成功条件收敛为
  `processed_frames >= min_frames_ok`。视觉采样增加 0.5 秒末端裕量；确定性分析异常写失败
  终态、聚合后提交命令，容量不足仍重试；Orchestrator 幂等忽略完成/失败/取消节点的滞后
  进度。PPT 全量 `104 passed`，Vision 全量 `38 passed`，Orchestrator 全量 `63 passed`。
- 新短 T/S/P 位于 Git 工作区外，取同一节 `0912` 课程 `360–410s`，三路均为 50.040 秒、
  1080p/25fps、H.264 High/yuv420p、AAC，首帧关键帧、完整解码和 `4.999s` 抽帧通过。
  T/S/P SHA-256 分别为
  `4b63885bcefb15cd3bdf9dec52c267b6b50bf63a58c4e9a1c93ff3dc76eff4e4`、
  `b9819f5aef0fb2b193daef7d6213ea982f25436623692fbd4538bdf9f571e440`、
  `f91ef623f0a62de6acdb5f578ac15b1afd9fe4574a079433c1d240da3dcfd775`；manifest SHA-256 为
  `51ee3f8c1244fa08dc1566b6ff5f43fec35845adcce65d644e7568ba082ecedb`。`.12` 源文件只读
  `stat`/`sha256sum` 与 manifest 完全一致；`.11` 宿主机及 Orchestrator 容器读取三路首块均为
  HTTP `206` 且摘要一致。教师音频非静音探针后，又通过北向 ASR-only 任务取得平台业务码
  `0`、任务/节点状态 `60` 和 `23` 个 segments；未输出或持久化完整转写文本。
- 视觉普通 `ValueError`、`TypeError`、`KeyError`、`FileNotFoundError` 现按单任务失败终结，
  进度落库或视觉事件发布异常仍失败关闭；聚焦消费续跑与基础设施隔离测试通过。Vision 全量
  更新为 `44 passed`，平台完整门禁为 `3223 passed, 3 skipped`，三个 skip 均因本机没有
  canonical FaceRec GPU 容器。
- 原 attempt 和失败任务保持只读，`12.1` 继续未完成。上述修复必须提交为新 SHA，11 个镜像
  同 revision 重建并恢复 29/29 healthy 后，以新 seed、Campaign ID 和 attempt 从阶段 0 重跑。

## 2026-08-25 - `0ebaa126` 发布闭环与阶段 0 重跑准入

- 当前发布为 `v1.0_260825`，完整 SHA 为
  `0ebaa126f69e3993487c503c11b42e681cad12cd`。远端 release 根为
  `deploy/reports/milestone-2b/releases/v1.0_260825/0ebaa126f69e3993487c503c11b42e681cad12cd/`。
- 11 个新镜像均为互异 `amd64` 完整 ID、revision 精确一致，并通过项目根 `logs/`、模型
  哨兵文件和平台镜像禁止模型目录核验。常驻切换后为 29/29 healthy、21/21 注册、零租约；
  三张 GPU 各有 6 个 `nvidia-smi` 进程，18 个 PID/cgroup 各自映射唯一容器；3 个 PPT
  CPU 实例无 GPU 请求。
- PPT cpu0/cpu1/cpu2 使用冻结 454 MB 视频逐实例执行终态回调与 manifest Smoke，三案均通过；
  随后唯一一次 full Smoke 的 ASR Offline、ASR Online、OCR、VBas、FaceRec、ScreenDet 和
  PPT 七案均为非 mock 通过。北向仅开放 `18100/18103`，其余 27 个端口继续绑定回环。
- 切换前已有 PostgreSQL dump 可读性备份；切换后、Smoke 前另建立同批次 PostgreSQL 与
  `/data/result` 成对备份，未覆盖已有文件。当前剩余空间约 242 GiB/16%，仍高于 15% 和
  150 GiB 警戒线。
- `.12` 当前只读复核确认 `fileserver` 使用 `nginx:latest`，将 `/data/filemanage` 只读绑定到
  `/usr/share/nginx/html`，发布 `5555 -> 80`；三条 50.040 秒 fixture 的完整大小与 SHA 和
  外部 manifest 一致。登录密码不进入 Git、Harness、普通报告或命令证据。
- OpenSpec 11.8 已完成。下一步必须使用新的 seed、Campaign ID 和 write-once attempt 从阶段 0
  重跑；旧 `e91f5b21`/`2154c40` attempt 只保留历史事实，不补足当前 12.1。

## 2026-08-25 - `0ebaa126` 阶段 0 全量通过

- 新 attempt 为 `phase0-rerun-0ebaa126f69e-20260825144344`，seed 为 `2608252300`，Campaign ID
  为 `campaign-v1-0_260825-0ebaa126f69e3993487c503c11b42e681cad-c0f622b339eca6c5`。计划与
  每个 case 均为 write-once，没有复制或覆盖旧 SHA 证据。
- `.12` fileserver 只读 calibration 使用 420 秒、2 秒间隔，得到 210 个连续样本；1/3/10/30
  下载 44/44 成功，严格源端证据峰值为 CPU 2.33%、内存 32.98%、发送 123.10 MB/s、
  30 个服务连接。正式四档再次 44/44 成功，吞吐保持约 115 MB/s，说明后续离线容量结论
  必须区分媒体源/局域网和平台/GPU。
- `BASE-OFFLINE-PPT/ASR/TEACHER/STUDENT` 全部业务通过，固定未请求槽位不再阻塞单泳道；
  `BASE-ONLINE-VBAS/FACE/SCREEN-DET/OCR` 全部通过真实 Gateway 租约路由。实时 ASR 发送
  2294 个真实节拍音频块，464.12 秒后通过，零失败会话、零缺失终态。
- 阶段 0 共 13 个业务 case 加 `PHASE-0-COMPLETE`，14/14 为 `passed`，所有前后护栏均为
  `CLEAR`，运行指标没有 OOM、容器重启、持续 Kafka lag、Outbox 堆积或租约泄漏结论。
- OpenSpec 12.1 完成；下一步只可按警戒线从阶段 1 的单泳道/长课阶梯继续，不能用阶段 0
  单请求吞吐直接声明稳定容量。

## 2026-08-25 - catalog 阶段 1 的 PPT 唯一提交 100/300 档诊断与重跑边界

同一 `0ebaa126` attempt 的两项真实执行事实如下。规范文件保持 write-once，不回写旧结论：

| 用例 | 业务终态 | 耗时 | 运行时峰值 | 全过程护栏 | 结论 |
| --- | --- | ---: | --- | --- | --- |
| `OFF-UNIQUE-PPT-100` | 100/100 成功 | 72.487953s | 队列 179、Outbox 20、Kafka lag 0 | 9 个 `CLEAR` | 真实业务通过事实 |
| `OFF-UNIQUE-PPT-300` | 300/300 成功 | 686.763116s | 旧口径队列 550、Outbox 220、Kafka lag 14 | 16 个 `CLEAR`、1 个 `STOP` | 规范结论无效，当前 attempt 阻断 |

- PPT-300 第 16 样本显示 `orchestrator-service` 容器不健康但未重启；精确重启唯一容器后，
  已接受任务继续排空，第 17 样本恢复 `CLEAR`、旧口径队列为 7、Kafka lag 为 0。恢复证明
  现场可继续排空，不会撤销已经发生的 `STOP`。
- 根因是 PPT 节点刚写入 `RUNNING`、异步算子身份尚未写入进度时，对账循环把短暂状态当作
  “PPT 运行中节点缺少持久化任务身份”并退出；旧运行时汇总随后又只取末帧，错误发布
  `passed/CLEAR`。
- 末帧队列 7 不是仍在处理的工作。只读 PostgreSQL 联表显示唯一组合为
  `node.status=20/task_type.status=70/count=7`，必须保留历史节点但从活动队列排除。
- `OFF-UNIQUE-PPT-100/300` 在 catalog 中位于 `phase-1-offline`，但业务语义属于 OpenSpec 12.3
  的 100/300/1000 唯一提交；它们只是 12.3 的部分诊断，不能把 12.3 标记完成。OpenSpec 12.2
  要求的四条单泳道和 3/6/12/24/36 长课阶梯在该 attempt 中尚未执行，因此 12.2 保持未执行。
- 新实现必须确定性恢复未落库 PPT 身份、让最高护栏等级在整个窗口内粘滞、让活动队列
  同时过滤终态父任务，并在长课请求产生前计算投影空间。第 16 个样本的 `STOP` 使当前旧
  attempt 整体失效并阻断所有更高执行；修复后必须新 SHA、11 镜像同 revision、新
  seed/Campaign/attempt，并从阶段 0 重跑。

## 2026-08-25 - 负向超时媒体的独立探针边界

- `192.168.29.12:5555` 的现有 `fileserver`、只读课程目录和容器配置保持不变。
- 独立 `campaign-slow-media` 容器只绑定 `.12:5556`，`/healthz` 立即 200，`/timeout.mp4`
  延迟 5 秒后返回 504；2 秒 Range 预探测真实超时，避免把 404 或连接失败伪装为超时。
- 容器不挂载课程目录，使用只读根文件系统、非 root 用户、全部 capability drop、0.5 CPU、
  256 MiB 内存、256 PID 和受限容器日志；容器 label 与完整 ID 是唯一清理身份。
- 新 Campaign plan 固化 `not_found_url=http://192.168.29.12:5555/missing-404.mp4` 和
  `timeout_url=http://192.168.29.12:5556/timeout.mp4`。慢探针只解除阶段 2 fixture 前置，不能
  补足当前新 SHA 尚未执行的阶段 0/1。

## 2026-08-25 - 负向用例失败关闭补强

- 超时 fixture 先验证同 origin 的 `/healthz=200/ok`，再只接受 `ReadTimeout`；连接、写入、
  连接池超时及快速 HTTP 响应均零请求阻断。
- 异步负向任务完成后重新查询课程事实，要求请求中的对应任务类型为 `70`，且该任务类型下
  至少一个节点为 `70`；整体课程失败不再被当作节点归因证据。
- 规范结果继续要求最终活动队列、Outbox、Kafka lag 和全部容量租约归零。该实现只为新 SHA
  的阶段 2 建立可执行门禁，不回写旧 attempt，也不表示 12.3 已完成。

## 2026-08-25 - 阶段 1 单泳道与长课顺序门禁补齐

- 只读审计确认旧 catalog 的阶段 1 直接从 `OFF-UNIQUE-*-100/300/1000` 开始，这组用例属于
  OpenSpec 12.3；阶段 0 的 `BASE-OFFLINE-*` 也不能跨阶段复用，因此 12.2 缺少四个独立
  单泳道用例。
- 新 catalog schema 3 增加 `OFF-LANE-PPT/ASR/TEACHER/STUDENT`，均使用短 T/S/P fixture、
  只提交一个对应任务类型，并显式依赖 `PHASE-0-COMPLETE`。
- `OFF-LONG-COURSE-3` 同时依赖四条单泳道；`6/12/24/36` 依次依赖上一档。任一低档未通过、
  护栏到达警戒线或产生 write-once 失败证据时，高档保持 blocked，不能跳级执行。
- 本地聚焦验证为 `88 passed`，Ruff 和 strict Mypy 通过。该结果只补齐 12.2 的执行入口，
  不代表远端发布、阶段 0 重跑或 12.2 已完成。

## 2026-08-25 - `7efb2a0` 阶段 1 长课指标瞬时阻断

- `phase0-rerun-7efb2a02e964-20260825103601` 的阶段 0 为 `14/14 passed`；阶段 1 四条独立
  单泳道和 `OFF-LONG-COURSE-3` 均通过。`OFF-LONG-COURSE-6` 的 6/6 课程四泳道也进入成功
  终态，最终活动队列、Outbox、Kafka lag 和租约为零，29 个容器零重启。
- 该长课用例前 59 个运行时样本连续 `CLEAR`。第 59 个样本仍有活动队列 15；随后采样中的
  `/ops/queues` 返回 200，但 Kafka 在同一秒记录 4.5 秒控制器心跳超时，旧 lag 探针串行的
  Kafka CLI 超过 5 秒命令上限。采样器按 fail-close 锁存 `运行时指标采集失败: control`；
  8 分钟后的第 60 个成功收尾样本证明队列已归零，但不能覆盖已发生的 `STOP`。
- 原 blocked case、59 个 CLEAR 样本、第 60 个锁存 STOP 样本和 Kafka/Control 日志事实保持
  只读。OpenSpec 12.2 继续未完成，不用业务成功终态改写规范结论。
- 修复边界是减少探针自身负载并保留 fail-close：每个采样只执行一次 Kafka
  `--describe --all-groups`，精确汇总 `algorithm-orchestrator`、
  `algorithm-orchestrator-visual-events`、`vision-orchestrator`，CLI 瞬时失败默认最多尝试
  2 次、间隔 0.25 秒。Kafka 从 Control HTTP 中拆为独立 `kafka_lag` 采集面，独立超时默认
  20 秒、合法范围 15–30 秒，其他探针仍保持 5 秒。全部失败或组/分区不可证明仍锁存 `STOP`；
  脱敏失败事件写入 case 的 `failures/` 子目录，由 outcome 的 `failure_evidence` 单独列出，不能
  混入成功样本 `sample_evidence`。修复必须经新 SHA、11 镜像同 revision 和全新 write-once
  attempt 从阶段 0 重跑。
- 本地实现验证为 Campaign/适配器聚焦 `526 passed`、Ruff 通过、strict Mypy 23 个源文件通过、
  compileall 通过、OpenSpec strict 通过及 `git diff --check` 通过。回归同时证明单次瞬时失败
  会在第二次成功后继续采样，连续两次失败仍抛出脱敏 `ProbeError` 并由运行时护栏锁存 STOP。

## 2026-08-25 - `.12` 媒体源与负载机边界复核

- 通过用户本轮提供的连接方式完成只读审计，未修改 `.12` 的文件、容器或配置，凭据未写入
  Git、Harness、普通报告或命令证据。主机为 40 核、46 GiB 内存，根盘约 860 GiB 可用；
  5555 只读媒体服务和 5556 受控慢响应探针均在运行，冻结短 T/S/P fixture 的 Range 总长度
  与 Campaign manifest 一致。
- `.12` 当前 shell soft `nofile=1024`，主网卡 RX drop 计数在观察窗口继续增长；该主机还承载
  既有 ASR/PPT 业务容器。当前只把它作为媒体源、慢响应反例端点和源端辅助遥测，不把它作为
  1000 图片并发或 150 路实时 ASR 的权威负载 worker，避免把源机文件句柄或网卡上限误归因
  到 `.11` 调度平台。
- 当前代码复审后的聚焦测试为 `89 passed`，完整 `tests/extreme_load` 为 `412 passed`；Ruff、
  strict Mypy、compileall 和 `git diff --check` 通过。`.12` 接入补足测试输入与源端观测能力，
  不改变旧 `OFF-LONG-COURSE-6` 的 blocked 结论，也不直接完成 OpenSpec 12.2。

## 2026-08-25 - `28e74d7` 生产栈闭环与 Fault Adapter 预执行阻断

- 当前候选完整 SHA 为 `28e74d7a0422d35d612571f515e4e45f9e555b65`。目标机同 revision
  的七算子和四平台镜像为 `11/11`，常驻栈为 `29/29 healthy`、`21/21` 注册、18 个 GPU
  算子进程和 3 个 CPU PPT 实例；三张 GPU 各承载六类算子，最终算子直接 Smoke 为 `7/7`
  通过。租约、队列、Outbox 和容器 restart 均为零。
- 新计划使用 seed `2026082503`、Campaign ID
  `campaign-v1-0_260825-28e74d7a0422d35d612571f515e4e45f9e55-a3d28b435e021c7d`，attempt
  为 `full-campaign-28e74d7a0422-20260825202700`，catalog 共 172 个 case。该 attempt 只完成
  `BASE-MEDIA-DOWNLOAD-1/3/10`，三案分别为 `1/1`、`3/3`、`10/10` 通过，前后护栏均为
  `CLEAR`。
- `BASE-MEDIA-DOWNLOAD-30`、四条离线基线、四条在线图片基线、实时 ASR 基线、
  `PHASE-0-COMPLETE` 以及阶段 1–6 均未启动。它们必须记为未执行，不能由前三档下载或旧 SHA
  的阶段 0 证据补足。
- 启动后续 case 前的只读审计发现两项结构性问题：旧 Fault Adapter 不识别
  `<tag>/<sha>/attempts/<attempt-id>` 形式的 attempt root；同一
  `delegated_lock_holder_pid/path` 又被要求同时通过 Mac 本地和 `.11` 远端验证。两台主机不
  共享 PID/锁文件系统，因此阶段 5 在旧配置语义下必然阻断。执行器据此自然停止，没有继续
  运行 30 档或业务流量，也没有覆盖已有 case。
- 修复合同固定为：Fault Adapter 严格支持 attempt root，并显式兼容 direct release root；
  Mac 侧通过专用 `_LocalCampaignLockGuard` 获取并在每个 fault case 全程持有当前 attempt 根
  下的 `.campaign-fault.lock`。锁为当前用户所有的 `0600` 单链接，内容绑定 schema、Campaign
  ID 和 attempt root，每次动作验证目录、inode、权限及 mtime/ctime；delegated PID/path 只
  表示 `.11` canonical 锁，每次远端 Docker 动作同时执行本地 lock probe 和 semantic probe
  SSH challenge。结果以 `local_release_layout=attempt|legacy_direct` 和
  `maintenance_lock_binding=local_attempt_and_remote_canonical` 公开实际绑定。修复完成后必须
  形成新 SHA、重建/inspect 11 个镜像、恢复完整常驻拓扑，并以新 seed、Campaign ID 和 attempt
  从阶段 0 重跑。当前 attempt 继续只读保留。
- 实现后的 Fault Adapter 聚焦测试为 `37 passed`，故障计划、远端语义探针与生产适配器组合
  为 `121 passed`，完整 Campaign 测试为 `420 passed`；平台权威全量回归为
  `3274 passed, 3 skipped`。Ruff、strict Mypy、compileall、OpenSpec strict、Harness 一致性和
  `git diff --check` 均通过。3 个 skip 仅因本机未运行 canonical `facerec-gpu0`，不以模拟结果
  补足；上述本地结果只完成 10.19，不替代新 SHA 的 11 镜像和远端阶段 0–6。

## 2026-08-25 - `4dc40757` PPT 终态并发竞态阻断

- 当前 attempt 为 `full-campaign-4dc40757-20260825214026`。阶段 0 为 `14/14 passed`；
  阶段 1 的 `OFF-LANE-PPT/ASR/TEACHER/STUDENT` 四案全部通过，前后护栏均为
  `CLEAR`。
- `OFF-UNIQUE-PPT-100` 精确提交 100 个新任务后，现场状态为完成 33、运行 15、
  待处理 52；活动队列为 PPT 待处理 52、OCR 待处理 15、OCR 等待前置 52，
  Outbox 为 0，21 个实例均报告 `inflight=0`。
- 第 33 份运行时样本在 `2026-08-25T14:12:28.071934Z` 锁存 `STOP`，原因为
  `orchestrator-service` 不健康。其 SHA-256 为
  `1bc4e81e0b63758b89c2bfad5c74bf1bd56068fe6b2044b413f8eec86ef128e2`；前 32 份样本为
  `CLEAR`。中断后没有生成 `off-unique-ppt-100.json`，不补写、不伪造通过。
- Orchestrator 就绪详情显示五个后台循环均已停止，`ppt_reconcile` 根因为
  `节点状态不允许从 60 转换到 60`。PPT 回调和对账并发读到 `RUNNING`，一方先持久
  完成，后到一方触发严格状态机；容器未重启，但运行时按失败关闭停止所有循环。
- 已立即停止新请求，未重启或修改远端容器，未进入 300 档及任何后续 case。
  `.12` 仅以已验证的密钥连接采集源端 CPU、内存、网络与连接数；新增登录凭据未写入
  Git、Harness、报告或 runtime 配置。
- `.12:5555` 只读盘点共有 38 组完整 T/S/P 真实 MP4，其中 37 组为约 47/48/55/90 分钟
  长课，冻结短 fixture 仍为同课 50.04 秒 `0912-360-410-{T,S,P}.mp4`。从 `.11`
  执行 32 并发、每请求 1 MiB Range 探针为 `32/32` 成功。源端网卡为 1 Gbps，
  Nginx access log 已约 2.2 GiB 且未观测到轮转；后续容量结论必须区分媒体源网络上限，
  长稳期间还要观测源端日志增长，不将其归因为 `.11` 平台或算子故障。
- 修复只放行并发后与回调一致的同终态；竞争后为冲突终态时仍失败关闭。当前
  attempt 整体阻断，修复必须形成新 SHA、重建 11 个同 revision 镜像，再以新 seed、
  Campaign ID 和 write-once attempt 从阶段 0 重跑。

## 2026-08-26 - `da1f5e37` Campaign runner 中断与实时 ASR 终态收敛

- 当前 attempt 为 `full-campaign-da1f5e37-20260825234811`。阶段 0、四条阶段 1 单泳道、
  PPT 100/300/1000 和 ASR 100 均已发布 `passed` 规范结果。
- `OFF-UNIQUE-ASR-300` 已开始并生成 74 份运行时样本；最后样本护栏为 `CLEAR`，活动队列
  176、Outbox 0、Kafka lag 0，29 个容器健康且零重启。随后 Campaign runner 进程消失，
  `phase1-runner.log` 只有 `START`，没有规范 case 结果、`END` 或退出码。
- 平台未随 runner 中断：已接受任务继续自然排空。`2026-08-26 01:09:59`、
  `01:10:59`、`01:12:10 +08:00` 三次只读样本均为活动队列 0、Outbox 0、三个必需
  Kafka 消费组总 lag 0、21 实例活跃租约合计 0、Orchestrator ready；最后两份
  有效样本间隔超过 30 秒。PostgreSQL 只读核对显示该批 300 条课程任务类型和 300 条
  ASR 节点均为 `status=60`。这些事实只能证明平台排空，不得补写
  `OFF-UNIQUE-ASR-300` 通过。整个 attempt 作为执行器中断只读保留，后续 case 不在其中续写。
- 下一次执行使用脱离交互终端生命周期的持久 runner，记录 PID、日志路径、逐案 START/END 和
  退出码。任一 case 缺少规范结果或 runner 异常消失时，当前 attempt 立即阻断。
- 实时 ASR 执行器只在成功会话至少收到一条 `finished=true` 时判定存在终态；收到中间消息但
  没有终态仍计入 `missing_final_message_count`。该规则同时收紧阶段 0/3 独立用例和
  阶段 4/6 混合、长稳适配器。消息摘要和终态消息计数分别进入脱敏证据，不写字幕原文。
- `da1f5e37` 的阶段 0 结果只有 2294 个消息摘要，没有 `finished_message_count`；
  因此无法按新门禁证明 `BASE-ASR-WS` 终态，当前 OpenSpec 12.1 重新标为待新 SHA 验证。

## 2026-08-26 - `5a5760ef` 实时 ASR 分块违约与完整语句证据收敛

- `full-campaign-5a5760ef-20260826015800` 由父命令退出后的运行环境回收，仅保留
  `sequence_started` 和首案 `case_started`；无规范 case 结果和退出码，按执行器中断只读冻结。
- `full-campaign-5a5760ef-20260826021000` 使用独立 `tmux` 持久 runner，媒体下载四档、PPT、
  离线 ASR、教师/学生行为及四类在线图片共前 12 案全部通过。`BASE-ASR-WS` 为业务失败，
  `sent_chunks=2294`、`message_digest_count=2294`、`finished_message_count=0`、
  `missing_final_message_count=1`、`failed_session_count=0`；94 份运行指标和前后护栏均为
  `CLEAR`，租约获取/释放各 1，队列、Outbox、Kafka lag 和最终租约均归零。runner 正常写出
  `sequence_ended exit_code=1`，没有执行 `PHASE-0-COMPLETE`，该 attempt 不续写。
- 根因是独立与 mixed/soak Campaign 都把 16 kHz mono int16 WAV 按 `0.2s/3200 samples`
  发送，而算子稳定合同是 `0.48s/7680 samples/15360 bytes`。Gateway 原样透传，不是根因。
- `.11` 同一 WAV 前 12 秒的只读对照：旧分块 60 条响应均为空、完整语句 0；权威分块无尾
  静音为 25 条响应、24 条非空、完整语句 0；权威分块加 6 个静音块为 31 条响应、25 条非空、
  `finished=true` 1。三次会话关闭后租约均释放。
- Campaign 共享构造器固定权威 PCM/分块，末帧补齐，追加 6 个有界静音块并有界等待。
  `finished=true` 只作为完整语句证据，不表示 WebSocket EOS；不修改 ASR 或 Gateway 协议。
- 下一步必须形成新 SHA、重建同 revision 11 镜像、恢复完整拓扑，再以新 seed、Campaign ID
  和 write-once attempt 从阶段 0 重跑；两个 `5a5760ef` attempt 及此前历史 attempt 均保持只读。
- 提交前复审还发现 receiver 异常会被 `return_exceptions=True` 吞掉、相对
  `sleep(0.48)` 会累计发送和调度延迟、收到 `50301` 后可能继续发块三个阻断。
  修复后只忽略 runner 主动取消 receiver 产生的取消异常；已自行结束的 receiver
  异常归类为 `connection_failure`，容量消息仍最终归类为 `overload` 并停止后续
  媒体/静音块。
- 发送节拍改为单调绝对 deadline，记录媒体时长、发送耗时、实时因子和最大正漂移；
  默认漂移超过一个 `0.48s` 周期时停发并归类为负载机限制。每个会话还校验
  `sent_chunk_count = sent_media_chunk_count + sent_tail_silence_chunk_count`；这些实时证据
  透传到独立用例；mixed/soak 共用同一 runner 和计数正确性门禁，并同样输出会话数、计划/
  实发媒体时长、发送耗时、最大实时因子和最大正调度漂移。
- `.12` 新连接信息只用于 SSH 核验，凭据不写入 Git、Harness 或报告。只读复核确认
  `:5555` 仍有 38 组完整 T/S/P、114 个可解析 MP4，冻结短课的大小和 SHA-256 与基线一致；
  从 `.11` 执行 32 并发、每路 1 MiB Range 为 `32/32` 返回 206，用时 0.439 秒。
  `:5556/healthz` 为 200，`/timeout.mp4` 约 5 秒后为 504。
- `.12` 宿主 shell `nofile=1024`，网卡历史 RX drop 超过 340 万且 5 秒窗口仍有增量；
  因此它只作媒体源、慢响应反例端点和源端遥测，不作权威高并发负载机。该主机上的
  旧 ASR/PPT 容器不属于被测拓扑，Campaign 不得停止它们；阶段 0 前重新记录 RX drop
  基线，后续结论持续分离源端/网络与 `.11` 平台容量。

## 2026-08-26 - `c4fece8` PPT 1000 业务成功与 SSH 指标瞬时阻断

- 当前 SHA `c4fece820609da845fa361a12a352a7536211b15` 已完成 Stage45：29/29 healthy、
  21/21 注册、18/18 GPU 真实进程、3/3 CPU PPT 与 7/7 算子 Smoke。新 Campaign 计划共
  172 案、171 案必测，唯一可选项为 `SOAK-8H-OPTIONAL`。
- 固定 attempt `full-campaign-c4fece82-20260826042342` 的阶段 0 为 14/14 passed；阶段 1
  四条单泳道、PPT 100 和 PPT 300 均通过。`OFF-UNIQUE-PPT-1000` 的 1000/1000 北向请求
  成功，耗时 2601.19 秒，停止后任务队列、Outbox、三个 Kafka consumer group lag 和租约
  均归零，29 个容器仍健康。
- 同一 case 在 `2026-08-25T22:00:29Z` 的一个采样内同时出现 `gpu` 与 `target_host`
  `ProbeError`，两份失败证据均为 `attempts=1`。失败前样本 372 和最终样本 373 均保持
  18 个 GPU 进程、无不健康容器、无 OOM 增量；最终样本只因失败已经锁存而为 STOP。
- `.11` 只读审计显示 05:55–06:05 的 sshd、secure、kernel 和 system journal 没有
  MaxStartups、监听队列溢出、限流、重启、OOM、网卡中断或 GPU Xid。故障前目标机约
  95.3 GB 可用内存，容器 CPU 合计约为 80 核中的 5.42 核。现有脱敏失败 JSON 不含
  SSH 子原因，只能证明客户端到 sshd 认证前阶段的瞬时失败，不能断言退出码 255、5 秒
  命令超时或客户端瞬时资源错误中的哪一种。
- 372 个样本期间共有 4,888 次成功 SSH 认证，约每样本 13 次。当前收敛为：Kafka lag
  保留独立 `20s/2 次/0.25s`；其他只读面使用单次 5 秒超时、同采样最多 2 次、间隔 0.25 秒。
  首次失败、第二次恢复不产生 failure evidence；两次都失败时记录 `attempts=2` 并永久锁存
  STOP。SSH 连接复用或合并远端快照保留为后续优化，不作为此次 Campaign 中途的大改。
- Fault Adapter 的远端语义探针改由 `deploy/scripts/extreme-load-fault-probe` 使用平台
  `.venv/bin/python` 启动，部署手册的 metrics factory 同步修正为 `metrics_factory`。
  当前 attempt 保持只读，不能把业务成功补写为规范通过；修复形成新 SHA 后必须重建并
  inspect 11 个镜像，以新 seed、Campaign ID 和 write-once attempt 从阶段 0 重跑。

## 2026-08-26 - `4fd4fa1` Stage45 注册证据检查点冲突

- `4fd4fa118e7f3cb446a50d0c1176cbd5bdd1c52a` 已在 `.11` 构建并 inspect 七算子和四平台
  共 11 个 `amd64` 同 revision 镜像，常驻栈达到 29/29 healthy、21/21 注册、18 GPU 和
  3 CPU PPT。
- Stage45 自然运行到终点：18/18 GPU 实例真实推理、停止、残留检查、重启和重新注册均
  `PASS`；`ppt-slice-cpu0/cpu1/cpu2` 真实处理均 `PASS`；正式 `smoke/cases.json` 为
  ASR Offline、ASR Online、OCR、VBas、FaceRec、ScreenDet、PPT Slice 7/7 通过。结束时
  29/29 无 unhealthy、GPU 进程 18、根盘剩余约 244 GiB。
- 最终标记为 `CODEX_STAGE45_COMPLETE failures=1`、进程退出码 1。唯一失败是
  `full-operator-preflight`：常驻启动已写入 `registration/operator-registration.json`，
  GPU 恢复后的第二次 full 报告因时间和心跳变化具有不同字节，write-once 正确拒绝覆盖。
  当前 release 全部证据保持只读，不能把 7/7 Smoke 反向补写成发布通过，也没有创建新的
  Campaign attempt。
- 修复不放宽 write-once，也不提供任意文件后缀。首次 full 继续生成 canonical 文件；
  Stage45 固定使用 `--evidence-checkpoint stage45-post-recovery`，生成
  `registration/operator-registration-stage45-post-recovery.json`。checkpoint 只允许显式
  `--full`，未知、缺值、重复、profile、instance 和缩写在 Docker/HTTP 前失败。
- 常驻启动计划与最终聚合继续只认 canonical `operator-registration.json`；恢复后文件只是
  补充证据，不能替代 canonical。修复形成新 SHA 后必须重建/inspect 11 个镜像、重跑完整
  Stage45，并以新 seed、Campaign ID 和 write-once attempt 从阶段 0 开始。
- `.12` 的新增 SSH 凭据仅用于媒体源、慢响应端点和源端遥测核验，没有写入 Git、Harness、
  release 或普通日志。只读复核确认 SSH key、`:5555/course/` 和 `:5556/healthz` 正常；主机
  40 CPU、46 GiB 内存、约 30 GiB 可用，根盘约 860 GiB 可用。交互 shell `nofile` soft 为
  1024，但两个媒体容器主进程的 soft/hard 均为 1073741816，不存在当前服务侧句柄上限。
  `eno1` RX dropped 在 3 秒内从 3,439,459 增至 3,439,469，因此 `.12` 仍不作为权威高并发
  负载机，后续用例必须同时记录源端 RX drop 前后差值。
