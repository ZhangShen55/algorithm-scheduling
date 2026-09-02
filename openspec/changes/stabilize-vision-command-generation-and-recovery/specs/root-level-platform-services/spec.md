## MODIFIED Requirements

### Requirement: Docker 镜像遵守服务边界
每个服务的 Docker 构建 SHALL 只包含当前服务、必要运行依赖和公共平台分发包，不得复制其他三个服务；容器 SHALL 使用 `app.main:app` 启动。发布镜像 MUST 从目标完整 Git SHA 的明确 checkout 构建，并 MUST 包含可验证的源码 manifest；镜像声明的 revision、manifest 与镜像内实际服务和公共包源码 SHALL 一致。

#### Scenario: 独立构建服务镜像
- **WHEN** 运维人员使用文档指定的工作区构建上下文和该服务 Dockerfile 构建镜像
- **THEN** 镜像构建成功，且无需把其他服务源码复制到镜像

#### Scenario: 容器入口符合标准
- **WHEN** 检查任一服务镜像的默认启动命令
- **THEN** 命令使用 `python -m uvicorn app.main:app` 和该服务规定端口

#### Scenario: revision 与实际源码一致
- **WHEN** 部署前置检查验证运行容器的不可变 image ID、目标 Git SHA、镜像源码 manifest 和镜像内实际文件
- **THEN** revision 等于目标 Git SHA，服务及公共包文件哈希匹配目标 checkout 生成的 manifest

#### Scenario: 旧源码伪装成新 revision
- **WHEN** 镜像 revision 标签为目标 Git SHA，但镜像内任一受管源码哈希与目标 checkout 不一致
- **THEN** 部署前置检查失败且该镜像不得进入服务切换阶段

#### Scenario: 新视觉版本替换旧版本
- **WHEN** `orchestrator_service` 与 `vision_orchestrator_service` 新容器通过不可变 image ID、revision、源码 manifest、health 和 readiness 校验
- **THEN** 部署流程删除被替换的旧容器和旧镜像，并在验收证据中记录删除清单

#### Scenario: 新视觉版本尚未健康
- **WHEN** 任一新容器尚未通过源码一致性、health 或 readiness 校验
- **THEN** 部署流程不得删除唯一可回滚的旧镜像

## ADDED Requirements

### Requirement: Vision Consumer 是视觉服务必需健康循环
部署 `vision_orchestrator_service` 时，视觉命令 Consumer SHALL 作为必需后台循环参与 readiness；陈旧命令、终态重复和已落库业务失败不得使循环退出，无法保证状态一致性的基础设施故障 MUST 使 readiness 失败并保留未提交消息。

#### Scenario: 陈旧命令被安全确认
- **WHEN** Vision Consumer 识别并确认一条陈旧视觉命令
- **THEN** `/ready` 继续报告视觉命令循环运行，Consumer 继续处理后续消息

#### Scenario: 基础设施故障阻止可靠处理
- **WHEN** PostgreSQL 或 Kafka 故障导致命令身份或结果无法可靠确认
- **THEN** `/ready` 返回 not ready 和脱敏故障原因，相关 offset 不得被错误提交
