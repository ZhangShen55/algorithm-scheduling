# 算法调度平台工作指南

本文件约束 `algorithm-scheduling-platform/` 下的公共包、数据库迁移、部署定义、跨服务测试和 Harness。四个可部署服务项目位于工作区根目录，与本目录平级；以下服务边界同样作为这些根目录项目的长期架构规则。

## 服务边界

| 服务 | 负责内容 | 不得负责 |
| --- | --- | --- |
| `control-service` | 面向 A 服务的课程接口、PostgreSQL 任务事实/Outbox、Redis 注册表、生命周期和租约 | 媒体下载、模型调用、Kafka 消费 |
| `orchestrator-service` | Outbox 发布、离线 DAG、媒体准备、通用节点执行、PPT 终态回调 | 在线请求、自适应视觉决策 |
| `vision-orchestrator-service` | 离线教师/学生抽帧、自适应 VBas 轮次、聚合和证据 | RTSP 在线接入、算子注册管理权 |
| `online-gateway-service` | 在线 Base64 请求路由和实时 ASR WebSocket 会话粘性 | Kafka、离线任务创建、视频下载 |

四个服务必须保持为相互独立的进程和容器。离线执行同时需要 `control-service` 和 `orchestrator-service`；视觉服务和在线服务仍然可以独立选择是否部署。

## 稳定契约

- 必须原样保留 A 服务字段 `task_id`、`task_types`、`teacher_video_path`、`student_video_path`、`slides_video_path`、`front_points`、`back_point`、`student_count` 和 `asr_options`。
- 必须保留四种任务类型 `PPT`、`ASR`、`TEACHER_BEHAVIOR` 和 `STUDENT_BEHAVIOR`，以及整数节点状态。
- 必须保留算子编码 `vbas`；不得在新的平台契约中重新引入 `tias`。
- 在线图像请求包含上游传入的 Base64 图像。不得向在线网关增加流接入或抽帧功能。
- PPT 是已批准的内部破坏性契约变更：共享文件位于 `/data/result/{task_id}/ppt`，使用原子 manifest，只发送一次终态回调，不再发送 Base64 幻灯片回调。
- PPT 提交使用规范字段 `video_path`。Orchestrator 输出准备完成的绝对本地路径；算子同时接受远程 URL，并仅将旧字段 `uri` 保留为兼容输入。
- Kafka 消息只能包含标识符、路径和元数据，不得包含媒体字节。
- 当前新任务 DAG 固定为 `PPT_SLICE -> PPT_OCR` 和 `ASR_TRANSCRIPTION`。不得为新任务创建
  `PPT_KEYWORDS` 或 `COURSE_OVERVIEW`；历史任务和结果中的退役节点仍须原样可查询。
- 当前可注册算子集合固定为 ASR Online、ASR Offline、FaceRec、OCR、ScreenDet、PPT Slice 和
  VBas 七类。`text_analysis` 只允许作为 PostgreSQL 历史审计字符串读取，不得进入当前注册、
  路由、租约、构建、Smoke 或部署权威。

## 依赖归属

- PostgreSQL 是任务、节点、结果、Outbox 和审计事实的持久化权威来源。
- Redis 是算子 TTL、生命周期和可原子续期租约的实时权威来源；只有 `control-service` 可以直接连接 Redis。
- Kafka 承载课程级命令和视觉事件；在线流量不得进入 Kafka。
- `/data/course/{task_id}` 是临时目录；`/data/result/{task_id}` 是持久目录，终态清理时必须保留。
- `enabled_task_types` 声明服务支持投递的任务类型。注册容量不足时应返回状态码 30 和就绪详情，不得动态删除已支持的任务类型。

## 目录和运行方式

每个服务都必须拥有自己的 `app/`、`tests/`、`docker/Dockerfile`、`config.toml`、`requirements.txt` 和 `README.md`。TOML 字段旁必须有中文注释。配置按服务默认值、`config.toml`、环境变量的顺序解析。

## 日志合同

七个当前算子和四个平台服务统一写入项目根 `logs/{instance_id}/application.log`，同时输出
stdout；默认单文件上限 100 MiB、归档保留 7 日，字段由各自根 `config.toml` 的 `[logging]`
配置。镜像必须预创建 `logs/`，未挂载宿主机目录时也必须能在容器内查看日志；需要跨重建
持久化时才显式加载 `deploy/docker-compose.logs.yml`，并为每个实例使用独立宿主机目录。

日志只能保留受控标识、大小、耗时、状态和节点上下文，不得写入 Base64、媒体字节、完整
请求/响应、凭据、完整 ASR/OCR 文本或 embedding。`text_analysis/` 是非平台项目，排除在
日志实现、镜像和部署之外。日志改造新增的轮转、过期清理、脱敏、handler 去重和路径边界
必须有简洁中文原因注释，但不得改变任务、租约、推理或进程拓扑。

当前里程碑 2B 部署不使用 `.env`。用户已批准把部署模板、服务器登录合同和受控服务默认值提交到 Git；当前固定登录合同是 `root@192.168.29.11:22`，密码为 `kedacom_123`。该批准是明确例外，不表示模型解密密钥、Deploy Key/私钥、人脸原图、课程媒体、大型 fixture 或外部可信模型 manifest 可以进入 Git、Markdown、报告或镜像上下文。以后增加凭据时必须先判断是否属于已批准例外，不能笼统套用“所有敏感值只允许环境变量”或“所有密码都不得写入 Git”的旧规则。

`.env` 与 `.venv` 含义不同：前者是本里程碑不使用的部署配置文件，后者是 Harness Python 运行环境。里程碑 2B 的 clean clone 必须先准备项目 `.venv`，从 `pyproject.toml` 安装基础依赖，验证 `httpx`、PyYAML、`websockets` 和 `aiokafka` 可导入，并在任何 preflight 或 Smoke 前把 Python/依赖版本原子写入当前 release 的 `preflight` 证据。

由于 lifespan 会启动后台循环，`orchestrator-service` 和 `vision-orchestrator-service` 必须使用一个 Uvicorn worker。初始部署中的 `control-service` 和 `online-gateway-service` 也使用一个 worker；取得基于消息代理的运行证据后，再通过增加容器进行扩容。

## 禁止的捷径

- 端到端测试不得调用仓储层的完成方法来模拟 Worker 输出。
- 不能因为存在类或仅能通过健康检查的入口，就将运行时任务标记为完成。
- `orchestrator-service`、`vision-orchestrator-service` 和 `online-gateway-service` 不得直接读取 Redis 注册表；必须使用 `control-service` 提供的租约。
- 已接受的异步 PPT 租约在终态持久化之前不得释放；运行期间必须持续续租。
- 常规清理期间不得删除 `/data/result/{task_id}`。

## 里程碑 2B 部署合同

- 当前拓扑权威为 7 类算子、21 个实例、18 个 GPU 实例、3 个 CPU PPT Slice 实例和 14 个
  配置解析进程。发布证据必须包含 7/7 算子 Smoke、217 条反例、26 条压力/恢复用例和 6 项
  B 级人工复核，且全部绑定同一最终 Git SHA。
- A/远程主机只访问 `control-service:18100` 和 `online-gateway-service:18103`。`18101`、`18102`、PostgreSQL `5432`、Kafka `9092`、Redis `6379`、MongoDB `27017` 和全部 21 个算子宿主机端口必须绑定 `127.0.0.1`；容器间继续使用 `algorithm-platform` 网络和服务名。
- Kafka 同时提供 `EXTERNAL://:9092` 与 `INTERNAL://:29092`，分别广播 `EXTERNAL://127.0.0.1:9092` 与 `INTERNAL://kafka:29092`。容器不得使用宿主机广播地址。
- 发布构建必须显式传入完整 `EXPECTED_GIT_SHA`。四个平台运行容器通过 `preflight runtime --git-sha SHA` 校验最终镜像 revision；每个算子 profile 以及全 21 实例分别通过 `preflight operators --profile PROFILE --git-sha SHA` 和 `preflight operators --full --git-sha SHA` 校验。Smoke 的 `--git-sha` 只标记报告归属，不替代镜像 attestation。
- 旧八算子、24 实例 release 是不可改写的历史证据，只能用于追溯当时事实；它不能补足当前
  七算子发布的任何缺失门禁，也不能被重标或复制成当前通过证据。
- 外部 `model-assets.manifest.json` 是交付可信基线。部署阶段只能执行 `stage-model-assets` 和 `verify-model-assets`，不得运行生成器覆盖基线；OCR 镜像内派生 manifest 仅供运行时校验，不是第二个交付权威。
- canonical 2B 场景不得对 platform/infrastructure 执行 `down`，不得宽泛停止预存业务。host preflight 和 snapshot/pause 前必须通过 `O_NOFOLLOW`、UID、`0600`、单链接和 inode 校验获取同 release tag 共享的非阻塞锁，并持有到阶段 6 唯一 restore 成功。fresh host preflight 强制空 `AUTHORIZED_OCCUPIED_ENDPOINTS`；续跑只从权威 platform/operator Compose 配置和经身份、running、端口映射核验的容器 Docker inspect 实际绑定精确派生“监听地址+端口”授权端点，preflight 逐条核对 `ss` 监听，旧纯数字端口授权不生效。首次发布只按同一账本暂停用户明确允许的原 `ocr-v6-amd`；同 SHA 续跑复用已有完整本地账本。换 SHA 续跑必须显式给出同 `REPORT_ROOT`/release tag 的立即前驱 `PREVIOUS_RELEASE_ROOT`。前驱仍有 active snapshot/paused 时，可通过 provenance 继承更早的权威账本，当前 release 不得重新 snapshot/pause 或复制可变 paused ledger；前驱已经成功 restore 时，只有在严格验证 `0600` 单链接 snapshot、唯一 `0400` 单链接终态 audit、无残留 archive metadata 及被选容器当前恢复事实后，当前新 SHA 才可开启全新的 snapshot/pause 事务。两种路径均不得改写旧 release。
- baseline/current/new 容器 ID 账本必须经同目录临时文件、排序、完整 ID/`docker inspect` 与 Compose 身份校验和原子替换发布。同 SHA 已有完整 baseline/new 时保留 baseline 并刷新 new，只有一份时 fail closed。换 SHA 的算子账本来源不得假定等于立即前驱：只读 resolver 必须从 `PREVIOUS_RELEASE_ROOT` 开始，遇到最近的完整 baseline/new 对即返回；无账本时优先沿严格校验的 `0400` maintenance provenance `source_release_root` 回溯。若候选是合法 direct maintenance、尚无完整算子账本且存在当前 UID 所有、单链接、`0400` 的 predecessor marker，允许沿 marker 的同 tag 前驱继续寻找；缺 marker、partial、环或最终无完整账本祖先均 fail closed。该解析不得改写当前或祖先 marker/provenance；只有在 `current - resolved baseline` 与 resolved new 精确一致后才原子继承 baseline 并立即刷新 new。每次 profile `up` 无论成功、失败或 partial-up 都要先刷新账本再返回原状态；账本刷新失败时禁止 cleanup，待 Docker 恢复后基于 baseline 重新刷新。清理只停止本轮记录的新增算子容器，不删除容器，然后恢复原业务。禁止 prune、`down -v`、删除卷和删除 `/data/result`。
- active snapshot 中获准选择的 `ocr-v6-amd` 原本 running 时，paused ledger 必须恰有一条可信 `stopped` 记录；原本不是 running 时允许 paused ledger 为空，但当前 Docker binding 必须与 snapshot 完全一致。不得把原本 running 的空 ledger 当作可续跑事务。
- Canonical 控制器组装的连续 Bash 程序不得通过可被 preflight、探针或第三方子进程继承并消费的 stdin 执行；应使用 `bash -c` 或等价受控脚本文件。阶段完成必须同时出现规定的显式终态标记，不能把中间 preflight 的零退出码当作完整成功。

## 验证层级

1. 静态验证：编译/导入、配置解析和路由契约。
2. 单元验证：状态机、适配器、manifest 校验和聚合。
3. 数据库/Redis 集成验证：真实 PostgreSQL/Redis 行为。
4. 消息代理集成验证：真实 Kafka 发布、消费、提交和恢复。
5. 服务运行验证：lifespan 循环、就绪状态和关闭流程。
6. 算子契约验证：通过真实租约发起 HTTP/WebSocket 调用。

所有结论必须注明实际达到的验证层级。执行 `harness/verification.md` 中的命令；运行时连接、部署方式或契约发生变化时，必须同步更新 Harness 证据。
