## ADDED Requirements

### Requirement: 前端项目使用规范名称和布局

前端项目 SHALL 位于 `algorithm-scheduling-ops-console/`，使用 TypeScript、React、Vite 和锁定的 `package-lock.json`，并包含 `src/`、`public/`、`Dockerfile`、`.dockerignore`、Compose 文件和部署 README。`node_modules/`、`dist/` 和测试报告不得作为源代码交付内容；Nginx/BFF 不属于本阶段必需资产。

#### Scenario: 检查项目结构
- **WHEN** 开发者或发布脚本检查控制台项目
- **THEN** 项目名称、必需部署资产和构建配置均位于 `algorithm-scheduling-ops-console/`，不存在依赖旧目录名才能运行的路径

### Requirement: Docker 镜像可复现构建

控制台 SHALL 使用多阶段 Dockerfile：构建阶段执行 `npm ci` 和 `npm run build`，运行阶段使用轻量静态文件服务托管构建产物；运行镜像只需包含构建产物和静态服务运行所需文件，不得包含后端服务源码。

#### Scenario: 构建生产镜像
- **WHEN** 运维人员在工作区按 README 指定上下文执行 Docker build
- **THEN** 镜像成功生成并可提供前端首页，构建使用 lockfile 而不是重新解析依赖版本

### Requirement: 控制台直接访问内部观测服务

生产 Compose SHALL 提供一个轻量静态页面容器；页面 SHALL 支持通过浏览器配置 Control Service、online-gateway-service 和 gpu_metrics_exporter 的协议、IP 与端口直接访问。三个内部观测服务 SHALL 支持无凭据的 GET/OPTIONS CORS 请求。

#### Scenario: 直连真实后端
- **WHEN** 页面配置 `http://192.168.29.11:18100`、`http://192.168.29.11:18103` 和 `http://192.168.29.11:9400`
- **THEN** 浏览器可以读取三个服务的只读接口，且预检请求不会返回 `405`

#### Scenario: 地址变更
- **WHEN** 运维人员在页面配置中修改任一观测服务的 IP 或端口
- **THEN** 前端静态资源无需重新编译即可使用新地址

### Requirement: 部署健康检查不伪装后端健康

控制台容器 SHALL 提供静态站点健康检查；后端连接失败 SHALL 在页面中呈现为观测失败，而不得让控制台容器健康状态代表 Control Service 或 online-gateway-service 已就绪。

#### Scenario: 静态站点可用但后端不可用
- **WHEN** 静态服务进程和首页正常，而任一上游服务停止
- **THEN** 容器静态健康检查可以通过，但页面显示对应后端读取失败和重试入口

### Requirement: 部署文档覆盖真实接入

控制台 README SHALL 说明构建、启动、停止、上游地址配置、默认同源路径、直连开发模式、后端接口版本要求和浏览器看到 CORS/404 时的排查顺序，并 SHALL 明确控制台只读且不控制 Docker。

#### Scenario: 按文档启动控制台
- **WHEN** 运维人员按照 README 设置两个上游并启动 Compose
- **THEN** 能够访问控制台首页，并可验证 Control Service `/health`、`/ops/course-jobs` 和网关 `/metrics`
