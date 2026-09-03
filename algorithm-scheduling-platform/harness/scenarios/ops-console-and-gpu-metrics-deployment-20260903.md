# 运维控制台与 GPU 指标容器发布验证

## 范围

本记录对应 OpenSpec 变更 `standardize-ops-console-deployment-and-observability` 的前端与
GPU exporter 发布部分。目标机为 `192.168.29.11`。本次只新增或替换
`algorithm-scheduling-ops-console` 和 `gpu-metrics-exporter`，没有重启 Control Service、
online-gateway-service、Orchestrator、Vision、基础设施或七类算子。

本次前端发布使用的源文件摘要为
`996c09ba7382ed98ab4e558d230026d25e469d98f80d87fee50772f792102edd`，前端最终发布目录为
`/root/workspace/algorithm-ops-console-release-996c09ba7382`。GPU exporter 使用已完成候选
门禁的发布摘要
`2ace5aa2fe7979e73f2cd6307031264435aa4ae0ff7ec3e1c7e49ce6c62d0939`，其发布目录为
`/root/workspace/algorithm-ops-console-release-2ace5aa2fe79`。传输内容排除了
`node_modules/`、`dist/`、`.vite/`、`__pycache__/` 和 `.ruff_cache/`。

## 本地门禁

- 前端 `npm run build` 通过，TypeScript、Vite 和静态资源解析无错误；最终构建包含按需拆分的
  ECharts chunk。
- 前端和 GPU Compose `docker compose config` 通过。
- GPU exporter `compileall` 与 Ruff 通过。
- 新前端项目中的 `node_modules/`、`dist/`、`.vite/` 和旧 `ops-console/` Vite 缓存已从交付
  工作树清理；`.dockerignore` 同时排除这些生成内容。
- 全局旧路径检查没有发现依赖 `ops-console/` 才能运行的引用。浏览器配置使用新的
  `algorithm-scheduling-ops-console-*` key，同时只读兼容旧 key，避免已有配置丢失。

## 构建与候选门禁

目标机为 `x86_64`、Docker `26.1.4`，具备 NVIDIA Container Runtime。正式端口 `5174` 和
`9400` 在发布前均未占用。前端通过 build args 预置以下首次访问地址，页面内配置仍可覆盖，
无需重新编译：

```text
Control Service: http://192.168.29.11:18100
Gateway:         http://192.168.29.11:18103
GPU exporter:    http://192.168.29.11:9400
```

GPU exporter 首次构建在 Docker 默认网络下载 `nvidia-ml-py` 时超时，没有启动候选或正式容器；
使用宿主网络重试后成功。候选容器使用 `15174` 和 `19400`，验证首页、实际 JS/CSS 资源、三个
预置地址、GPU `/health`、`/gpu`、`/metrics` 和 OPTIONS CORS。GPU 快照识别到索引
`0/1/2`，型号为两张 RTX 4090 D 和一张 RTX 3090，显存、利用率、温度、功耗和进程数均可读。
候选门禁结束后候选容器已删除。

浏览器候选门禁发现并修复了两个移动导航问题：菜单按钮原先没有打开侧栏，关闭按钮又会被
通用图标样式在桌面显示。修复后移动菜单可打开、关闭和通过页面切换自动收起，桌面不显示
移动关闭按钮。

## 正式容器

| 服务 | 镜像标签 | 镜像 ID | 容器 ID | 健康 | 重启次数 |
| --- | --- | --- | --- | --- | ---: |
| Ops Console | `algorithm-scheduling/ops-console:v0.1_260903_996c09ba` | `sha256:4587ca1cd6efdbe5e364e601d126a4b0c7ca512b6e94bc8113f3adceb52aaae2` | `beb38f6c53afd87c74f8f30a37c2fe065a249ae8575e970ff97f2bf328a3f97e` | healthy | 0 |
| GPU exporter | `algorithm-scheduling/gpu-metrics-exporter:v0.1_260903_2ace5aa2` | `sha256:f4e149853c8f8320d8a56dc88808052a7f17f497f0c7dd188096b55804a919a0` | `538bcbd2ce3542373a4988efe36957d6e92c37de0bba806ef23df5ce5b1e3d51` | healthy | 0 |

正式访问入口：

- 控制台：`http://192.168.29.11:5174/`
- GPU JSON：`http://192.168.29.11:9400/gpu`
- GPU Prometheus：`http://192.168.29.11:9400/metrics`

两个端口均监听 `0.0.0.0`。GPU 容器的 Docker DeviceRequest 为 NVIDIA、`Count=-1`、
capability `gpu`，只读取全部 GPU，不挂载 Docker socket，也不改变算子单卡隔离。

## 真实数据联合验收

从目标机和局域网工作站完成以下只读验证：

- Control `/ops/readiness` 返回 ready，PostgreSQL、Redis 和 schema 检查存在；
- 实例和容量快照各返回 21 条，覆盖七类当前算子；
- 任务列表默认 `page=1&page_size=10&sort_by=updated_at&order=desc`，总数为 `13406`，首
  页更新时间严格降序；真实 `task_id` 详情返回四种任务类型；
- A 服务 `GET /api/course-jobs/{task_id}` 仍返回 HTTP 200 和业务码 `0`；
- 21 个实例的 active lease 接口全部返回合法响应，本次采样时活动租约为 0，因此只验证空态，
  没有伪造实例正在处理任务的证据；
- `/ops/kafka` 返回 `status=ok`、`publisher_status=ok`、`outbox_pending=0` 和
  `consumer_lag=0`；
- Gateway `/metrics` 包含请求延迟、请求错误和容量租约事件指标；
- Control、Gateway、GPU exporter 的浏览器 OPTIONS 均通过，`Access-Control-Allow-Origin: *`；
- GPU `/gpu` 持续返回三张卡，`/metrics` 包含 `algorithm_gpu_utilization_percent`；
- Control、Gateway、Orchestrator、Vision、PostgreSQL、Redis、Kafka、MongoDB 和两个新增
  容器均保持 healthy；新增容器最近 200 行日志无 `Traceback`、`ERROR` 或 `CRITICAL`。

## 页面验收

桌面视口检查了运行总览、实例清单、实例详情、任务追踪、网关和系统状态。实例按 OCR 筛选后
显示 3 条；任务列表可切换到每页 20 条、进入第 2 页且内容变化，点击真实任务后显示四个任务
类型。连接抽屉显示三个正式地址和 `10/5/5` 秒刷新周期，“测试读取”返回 21 个实时实例。
网关 ECharts canvas 尺寸有效，页面及两个抽屉没有横向溢出，浏览器控制台无错误或警告。

此前工业控制台、夜间指挥舱、数据终端、企业运维四套风格逐项切换后均保持 21 个实例、实时数据
来源和当前页面不变。移动视口 `390x844` 验证了侧栏打开/关闭、实例和任务页面切换、实例详情
抽屉及表格局部滚动；页面和抽屉没有整体横向溢出。

2026-09-03 后续页面调整已在正式容器复核：刷新时不再插入会改变页面高度的读取提示行；桌面侧栏支持收缩；页面仅保留“日间模式”和“深色模式”；总览不再显示 Task ID 查询；任务追踪增加“课程任务”和“查询课程任务”二级分区；Kafka 流程、网关请求分布、平台就绪检查和移动布局均通过构建与静态资源验收。三个刷新周期的最小配置值已调整为 1 秒。

## 回滚与保留

Compose resolved 配置和切换前后容器信息保存在对应远端 release 目录。前一版前端镜像暂时
保留用于回滚；没有执行 Docker prune，没有删除平台镜像、数据卷、模型、日志或构建缓存。
