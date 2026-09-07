## 1. 配置与数据契约

- [x] 1.1 在 GPU Exporter 配置中定义必填 GPU_EXPORTER_HOST_ID，并为 /gpu 响应增加 host_id 字段及固定内网 IPv4 校验
- [x] 1.2 在运维控制台 TypeScript 类型中定义 GPU 节点配置、逐节点读取状态和 host_id+gpu_index 关联键
- [x] 1.3 定义旧 gpuBaseUrl 到单节点列表的幂等迁移规则，以及多节点下禁止缺失 host_id 兼容映射的规则
- [x] 1.4 盘点七类算子对 PLATFORM_INSTANCE_ID、PLATFORM_SERVICE_URL、PLATFORM_INSTANCE_LABELS 的现有透传，记录无需修改项并为缺失项建立最小修复

## 2. GPU Exporter 主机身份

- [x] 2.1 修改 gpu_metrics_exporter，使健康诊断和 /gpu 返回显式 host_id，同时保持现有 GPU 指标字段兼容
- [x] 2.2 增加 host_id 缺失、格式非法、NVML 正常和 NVML 失败的 Exporter 自动化测试
- [x] 2.3 更新 GPU Exporter Docker、Compose 示例和 README，说明每台 GPU 主机独立部署、端口发布与 CORS 配置

## 3. 控制台多数据源配置

- [x] 3.1 将单个 GPU Exporter 地址配置改为可增删的节点列表，提供服务器 IP、显示名称、端口/URL模板、测试读取和恢复默认值
- [x] 3.2 实现旧 gpuBaseUrl 的幂等迁移、重复 host_id 校验、配置与响应 host_id 一致性校验
- [x] 3.3 保留 GPU 刷新周期配置并支持最低 1 秒，确保刷新状态使用固定空间或非布局提示而不引起页面跳动
- [x] 3.4 为节点配置保存、恢复、旧配置迁移、重复 IP 和身份不一致补充前端测试

## 4. 多主机采集与局部降级

- [x] 4.1 并行请求所有启用的 /gpu 数据源，为每个节点实现独立超时、错误、最近成功时间和最近成功快照
- [x] 4.2 防止同一 GPU 节点请求跨刷新周期重叠，并确保卸载、改址和手动刷新时正确取消或忽略过期响应
- [x] 4.3 实现单节点失败不清空其他 GPU、实例、课程任务、网关和系统状态数据的局部降级与自动恢复
- [x] 4.4 增加双节点同编号 GPU、单节点超时、节点恢复、过期响应和部分成功的 API 与组件测试

## 5. 主机、GPU 与算子实例关联展示

- [x] 5.1 规范化实例 labels.host_id 与 labels.gpu，并严格使用 host_id+gpu_index 关联 GPU，禁止解析实例名、容器名或 service_url
- [x] 5.2 在实例清单增加服务器和 GPU 列以及主机、算子筛选，在 GPU 区域按主机分组并展示每张卡上的实例
- [x] 5.3 实现“未部署算子”“未标记主机”“未标记 GPU”“GPU 标签无效”“兼容映射”和节点身份错误等中文状态
- [x] 5.4 仅在单 GPU 节点时允许旧实例按 gpu 兼容关联，增加第二节点后立即停止该映射
- [x] 5.5 增加精确关联、多主机 GPU 0 不冲突、空算子主机、缺失/非法标签和主机筛选的组件测试

## 6. 多机算子与 NFS 部署

- [x] 6.1 增加平台主机和远程 GPU 主机部署模板，显式配置全局唯一 PLATFORM_INSTANCE_ID、跨机可达 PLATFORM_SERVICE_URL 及同时含 host_id、gpu 的 PLATFORM_INSTANCE_LABELS
- [x] 6.2 更新 Control Service 部署配置示例，使每个远程 instance_id 的 trusted_service_urls 与注册 URL 完全一致
- [x] 6.3 增加跨机网络预检，从实际调用方验证算子 service_url，并拒绝仅 Docker 单机网络可达的地址
- [x] 6.4 增加平台主机导出 /data/course、/data/result 以及远程主机同路径挂载的 NFS 部署示例
- [x] 6.5 实现 NFS 预检，覆盖 UID/GID、root_squash、创建、写入、fsync、同文件系统 rename、跨机可见、删除和 /data/result 保护
- [x] 6.6 更新部署手册，说明 PostgreSQL、Redis、Kafka 可独立分机配置，NFS 容量只由指定存储观测端统计一次

## 7. 验证与 Harness

- [x] 7.1 运行 GPU Exporter 测试、运维控制台单元/组件测试、TypeScript 检查和生产构建
- [x] 7.2 通过 root@192.168.29.12:22 连接第二台真实 GPU 服务器，在不影响既有媒体源服务的前提下验证不同 host_id、重复 GPU 编号、空算子主机、浏览器 CORS、1 秒刷新和局部故障恢复；认证凭据不得写入仓库或 Harness
- [x] 7.3 用浏览器截图和交互证据验证多主机分组、主机筛选、实例清单拓扑列、中文异常状态和无刷新跳动
- [ ] 7.4 在至少一台远程 GPU 主机部署真实算子，验证注册、trusted_service_urls、租约、跨机调用、NFS 输入、结果回写及 GPU 关联
- [x] 7.5 将配置快照、命令、接口响应、测试输出和回滚结果写入 Harness，并明确区分空主机观测证据与真实远程算子证据
- [x] 7.6 复核 A 服务接口、算子 HTTP/WebSocket 契约、Kafka envelope、数据库结构、Redis Key 和容量租约均无变更
