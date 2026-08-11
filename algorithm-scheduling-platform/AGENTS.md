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

## 依赖归属

- PostgreSQL 是任务、节点、结果、Outbox 和审计事实的持久化权威来源。
- Redis 是算子 TTL、生命周期和可原子续期租约的实时权威来源；只有 `control-service` 可以直接连接 Redis。
- Kafka 承载课程级命令和视觉事件；在线流量不得进入 Kafka。
- `/data/course/{task_id}` 是临时目录；`/data/result/{task_id}` 是持久目录，终态清理时必须保留。
- `enabled_task_types` 声明服务支持投递的任务类型。注册容量不足时应返回状态码 30 和就绪详情，不得动态删除已支持的任务类型。

## 目录和运行方式

每个服务都必须拥有自己的 `app/`、`tests/`、`docker/Dockerfile`、`config.toml`、`requirements.txt` 和 `README.md`。TOML 字段旁必须有中文注释。配置按服务默认值、`config.toml`、环境变量的顺序解析；生产环境密钥必须通过环境变量提供。

由于 lifespan 会启动后台循环，`orchestrator-service` 和 `vision-orchestrator-service` 必须使用一个 Uvicorn worker。初始部署中的 `control-service` 和 `online-gateway-service` 也使用一个 worker；取得基于消息代理的运行证据后，再通过增加容器进行扩容。

## 禁止的捷径

- 端到端测试不得调用仓储层的完成方法来模拟 Worker 输出。
- 不能因为存在类或仅能通过健康检查的入口，就将运行时任务标记为完成。
- `orchestrator-service`、`vision-orchestrator-service` 和 `online-gateway-service` 不得直接读取 Redis 注册表；必须使用 `control-service` 提供的租约。
- 已接受的异步 PPT 租约在终态持久化之前不得释放；运行期间必须持续续租。
- 常规清理期间不得删除 `/data/result/{task_id}`。

## 验证层级

1. 静态验证：编译/导入、配置解析和路由契约。
2. 单元验证：状态机、适配器、manifest 校验和聚合。
3. 数据库/Redis 集成验证：真实 PostgreSQL/Redis 行为。
4. 消息代理集成验证：真实 Kafka 发布、消费、提交和恢复。
5. 服务运行验证：lifespan 循环、就绪状态和关闭流程。
6. 算子契约验证：通过真实租约发起 HTTP/WebSocket 调用。

所有结论必须注明实际达到的验证层级。执行 `harness/verification.md` 中的命令；运行时连接、部署方式或契约发生变化时，必须同步更新 Harness 证据。
