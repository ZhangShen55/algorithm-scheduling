## 1. 后端发布契约确认

- [x] 1.1 为 Control Service 增加或补齐 `/ops/course-jobs`、`/ops/course-jobs/{task_id}`、`/ops/kafka` 和 `/ops/operator-instances/{instance_id}/active-leases` 的接口契约测试，覆盖默认分页、排序、404、降级和数据库不可用响应
- [x] 1.2 检查并更新 Control Service 发布镜像、README 和部署 Smoke，使远端部署包含现有 `/ops/course-jobs`、`/ops/kafka` 实现；确认数据库迁移先于服务重启执行
- [x] 1.3 为 online-gateway-service 的 `/metrics` 增加或补齐观测指标格式检查，确认现有 `algorithm_operator_request_*` 和 `algorithm_capacity_lease_events_total` 可被控制台解析；为 gpu_metrics_exporter 增加 `/gpu` 快照检查
- [x] 1.4 验证 A 服务既有 `/api/course-jobs`、在线 HTTP/WebSocket 路由和七类算子注册/推理契约未发生变化；本阶段不加入写接口或 Docker 控制

## 2. 前端项目重命名和真实数据状态

- [x] 2.1 将 `ops-console/` 源码目录迁移为 `algorithm-scheduling-ops-console/`，同步 `package.json`、lockfile、README、标题、localStorage key 和文档/脚本引用，并保留用户已有源文件内容
- [ ] 2.2 清理项目交付边界中的 `node_modules/`、`dist/` 和测试输出，补充 `.dockerignore`，确保源代码项目只保留构建所需资产
- [x] 2.3 调整数据加载状态：默认实时模式首屏显示加载态，真实接口失败时显示可重试错误和数据来源，不自动把演示快照作为实时结果；演示模式必须显式启用并持续标识
- [x] 2.4 保留并校验 Control Service / gateway-online 的协议、IP、端口配置，默认使用 `/control` 和 `/gateway`，支持保存、恢复默认、测试读取和刷新周期校验
- [x] 2.5 为接口请求增加页面切换/重复刷新时的取消或过期保护，避免旧响应覆盖当前配置和视图状态

## 3. 实例清单和任务追踪

- [x] 3.1 将“实例容量对比”从导航和独立主面板移除，把容量汇总、容量使用率、心跳在途和有效租约整合进“实例清单”
- [x] 3.2 为实例清单实现算子类型、生命周期、模型就绪、设备/GPU、是否有活动任务筛选及容量/心跳/实例 ID 排序，确保筛选后汇总和表格一致
- [x] 3.3 扩展实例详情抽屉，展示 active lease 的 `task_id`、`work_type`、`node_id`、`item_id`、`work_id`、`source_service`、上下文状态、获取时间和过期时间；空 `task_id` 显示在线请求而不是伪造课程任务
- [x] 3.4 支持从实例详情的 `task_id` 进入任务详情，并处理实例租约刷新、无租约、接口 404 和 Control Service 不可用状态
- [x] 3.5 确认任务追踪默认请求 `page=1&page_size=10&sort_by=updated_at&order=desc`，支持 10/20/50/100 分页、服务端排序、页码跳转、独立滚动和 `task_id` 详情查询
- [ ] 3.6 为总览、实例、网关、系统状态和任务追踪补充真实接口响应映射与空态/错误态测试，保证不混淆课程任务和在线请求

## 4. ECharts 和实时观测

- [x] 4.1 保留 ECharts 的按需加载，调整总览、网关页面展示队列趋势、请求/错误速率和 P95，并标注浏览器会话采样窗口
- [x] 4.2 用相邻 `/metrics` 采样时间差计算请求速率和错误速率，处理首次采样、计数回退、刷新周期变化和无数据场景
- [x] 4.3 校验总览/实例/网关/系统默认 10 秒刷新、实例详情 active lease 默认 5 秒刷新，以及关闭自动刷新后不再产生定时请求

## 5. Docker 静态部署

- [x] 5.1 在 `algorithm-scheduling-ops-console/` 增加多阶段 `Dockerfile`，使用 `npm ci` 和 `npm run build` 构建，运行阶段仅提供静态页面
- [x] 5.2 增加控制台 `docker-compose.yml`，提供端口映射、重启策略和静态站点健康检查，不引入 Nginx 反向代理
- [x] 5.3 增加 gpu_metrics_exporter Dockerfile、Compose 服务和 README，使用 GPU 运行时读取整机显卡并提供 CORS 的 `/gpu`
- [x] 5.4 更新 Control Service、online-gateway-service 和 GPU exporter 的跨域说明，确保直接填写 IP/端口时 OPTIONS 预检可用
- [x] 5.5 更新控制台 README，说明本地开发、镜像构建、Compose 启停、后端接口版本要求、真实数据验证、CORS/404/连接失败排查和只读边界

## 6. 集成验证和交付

- [x] 6.1 执行前端 `npm ci`、`npm run build`，检查无 TypeScript、Vite 和未解析资源错误
- [ ] 6.2 执行控制台和 GPU exporter Docker build 及 Compose config/启动验证，确认首页、直连 CORS、GPU `/health` 和 `/gpu` 可用
- [ ] 6.3 使用 `192.168.29.11` 上真实 Control Service、online-gateway-service 和 GPU exporter 验证健康、实例、任务分页、task_id 详情、实例 active lease、Kafka 聚合、网关指标和 GPU 快照，记录服务版本不一致时的可诊断错误
  - 2026-09-02 已完成 Control Service 和 online-gateway-service 的同版本构建、候选门禁、正式发布、CORS、任务/实例/Kafka/网关指标及 A 服务兼容验证；GPU exporter 尚未部署，因此本项保持未完成。证据见 `algorithm-scheduling-platform/harness/scenarios/ops-console-backend-observability-deployment-20260902.md`。
- [ ] 6.4 在桌面和移动视口检查实例清单、详情抽屉、任务分页、图表和配置抽屉无重叠/溢出，并验证四套视觉风格只改变样式不改变数据和接口行为
- [ ] 6.5 全局搜索并修正旧 `ops-console` 路径引用，确认 A 服务接口、平台部署文档和 OpenSpec 工件没有因重命名产生错误路径
