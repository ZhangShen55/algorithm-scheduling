## ADDED Requirements

### Requirement: TIAS 必须支持核心模块 Cython 编译保护构建
TIAS MUST 提供核心自研模块 Cython 编译保护构建模式，用于生产镜像减少明文源码暴露。

#### Scenario: 核心模块生成编译产物
- **WHEN** 使用 TIAS 保护构建模式构建镜像
- **THEN** `tias/api`、`tias/core`、`tias/services`、`tias/schemas` 中被纳入保护范围的模块必须生成 `.so` 编译产物

#### Scenario: 核心模块明文源码移除
- **WHEN** TIAS 保护镜像构建完成
- **THEN** 被编译保护的核心模块不得在镜像内保留对应明文 `.py` 文件

#### Scenario: vendor 不默认编译
- **WHEN** TIAS 使用保护构建模式
- **THEN** `tias/vendor/DirectMHP` 不得默认纳入 Cython 编译范围，避免第三方动态导入兼容风险

#### Scenario: 普通构建保持可用
- **WHEN** 不启用 TIAS 保护构建模式
- **THEN** TIAS Docker 镜像必须仍能按普通源码方式启动，便于开发和问题排查

### Requirement: TIAS 必须提供 CPU 和 GPU Docker Compose 示例
TIAS MUST 在 `tias/docker/` 提供 CPU 或通用 compose 示例，以及独立 GPU compose 示例。

#### Scenario: 通用 compose 静态校验
- **WHEN** 执行 `docker compose -f tias/docker/docker-compose.yml config`
- **THEN** 通用 compose 配置必须能通过静态校验

#### Scenario: GPU compose 文件存在
- **WHEN** 查看 `tias/docker/`
- **THEN** 必须存在 GPU 版 compose 文件，例如 `docker-compose.gpu.yml`

#### Scenario: GPU compose 声明 GPU 资源
- **WHEN** 查看 GPU compose 配置
- **THEN** 配置必须声明 NVIDIA GPU 设备或等效 GPU 运行参数，使容器可访问 GPU

#### Scenario: GPU 前置条件说明
- **WHEN** 用户查看 TIAS Docker 部署文档
- **THEN** 文档必须说明宿主机需要安装 NVIDIA 驱动、Docker 和 NVIDIA Container Toolkit

### Requirement: TIAS 必须提供多实例部署配置示例
TIAS MUST 提供多个实例并行运行的配置和文档示例。

#### Scenario: 多实例配置文件存在
- **WHEN** 查看 `tias/docker/examples/`
- **THEN** 必须提供至少两个不同实例的 `config.toml` 示例，且 `InstanceId`、`BaseUrl` 和端口不得冲突

#### Scenario: 并发和队列配置可见
- **WHEN** 查看 TIAS 多实例配置示例
- **THEN** 必须包含 `MaxConcurrentBatches` 和 `MaxQueueSize` 配置，并用中文注释说明含义

#### Scenario: 注册 ai_quality 地址可配置
- **WHEN** 查看 TIAS 多实例配置示例
- **THEN** 必须包含 `AiQualityBaseUrl` 或等效配置，用于指定注册和心跳上报的 ai_quality API 地址

### Requirement: TIAS 必须提供完整 Docker 部署文档
TIAS MUST 在 `tias/docker/` 和运行文档中提供可直接执行的部署说明。

#### Scenario: config.toml 挂载说明
- **WHEN** 用户查看 TIAS Docker 部署文档
- **THEN** 文档必须说明如何将宿主机 TIAS 配置只读挂载到容器内 `/workspace/tias/config.toml`

#### Scenario: 模型目录挂载说明
- **WHEN** 用户查看 TIAS Docker 部署文档
- **THEN** 文档必须说明明文模型目录或加密模型目录如何只读挂载到容器内

#### Scenario: CPU 单实例启动命令
- **WHEN** 用户查看 TIAS Docker 部署文档
- **THEN** 文档必须提供 CPU 或通用单实例 `docker run` 启动命令

#### Scenario: GPU 单实例启动命令
- **WHEN** 用户查看 TIAS Docker 部署文档
- **THEN** 文档必须提供 GPU 单实例启动命令或 compose 命令

#### Scenario: 服务健康检查
- **WHEN** 用户查看 TIAS Docker 部署文档
- **THEN** 文档必须提供调用 TIAS 健康检查接口或状态接口的验证命令

### Requirement: TIAS 必须明确模型文件删除影响
TIAS MUST 在部署文档中说明模型文件挂载和删除对运行中服务的影响。

#### Scenario: 删除宿主机模型文件说明
- **WHEN** 用户查看 TIAS 模型挂载说明
- **THEN** 文档必须说明运行中进程可能因模型已加载而暂时继续工作，但服务重启、懒加载或再次读取模型时会失败

#### Scenario: 不依赖删除后继续运行
- **WHEN** 用户查看 TIAS 生产部署建议
- **THEN** 文档必须明确不得把“宿主机模型删除后当前进程仍可运行”作为生产保障

#### Scenario: 模型目录建议只读挂载
- **WHEN** TIAS 以 Docker 方式运行
- **THEN** 文档必须建议模型目录以只读方式挂载，降低误写和误删风险

### Requirement: TIAS Docker 构建必须排除敏感和无关文件
TIAS MUST 通过 Docker 构建上下文控制避免敏感文件、临时文件和大体积无关文件进入镜像。

#### Scenario: 排除明文模型
- **WHEN** 构建 TIAS 受保护镜像
- **THEN** 明文 `.pt` 模型文件不得进入镜像层

#### Scenario: 排除密钥文件
- **WHEN** Docker 构建上下文被打包
- **THEN** 模型密钥、环境私密文件和本地配置不得进入镜像

#### Scenario: 排除测试和临时数据
- **WHEN** Docker 构建上下文被打包
- **THEN** 测试视频、测试图片、缓存目录、构建临时目录不得进入生产镜像
