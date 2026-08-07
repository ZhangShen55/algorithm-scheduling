## ADDED Requirements

### Requirement: TIAS 必须支持启动期密钥引导
TIAS MUST 支持从只读挂载的宿主机密钥源文件生成运行期 `/dev/shm` 密钥副本，并让应用读取运行期副本。

#### Scenario: entrypoint 复制密钥到 /dev/shm
- **WHEN** TIAS secure runtime 容器启动且模型保护启用
- **THEN** entrypoint MUST 从 `/run/bootstrap-secrets/tias_model_key` 读取源密钥，并复制到 `/dev/shm/tias_model_key`

#### Scenario: 运行配置读取 /dev/shm 密钥副本
- **WHEN** TIAS 模型保护读取密钥
- **THEN** `KeyFile` MUST 指向 `/dev/shm/tias_model_key` 或等效运行期副本路径，而不是直接指向只读挂载源文件

#### Scenario: 密钥副本权限受限
- **WHEN** entrypoint 创建 `/dev/shm/tias_model_key`
- **THEN** 密钥副本权限 MUST 限制为当前运行用户可读，推荐 `0400`

#### Scenario: 缺少源密钥时启动失败
- **WHEN** `/run/bootstrap-secrets/tias_model_key` 不存在或为空
- **THEN** TIAS secure runtime 容器 MUST 启动失败，并输出简洁错误，不得继续以无密钥状态运行

### Requirement: TIAS 必须在读取后清理运行期密钥副本
TIAS MUST 在模型保护读取密钥后删除 `/dev/shm` 中的运行期密钥副本，降低长期文件暴露面。

#### Scenario: 读取密钥后删除副本
- **WHEN** 模型保护成功读取 `/dev/shm/tias_model_key`
- **THEN** 系统 MUST 删除 `/dev/shm/tias_model_key`

#### Scenario: 删除失败有日志
- **WHEN** 系统无法删除 `/dev/shm/tias_model_key`
- **THEN** 系统 MUST 记录简洁警告，但不得输出密钥内容

#### Scenario: 模型解密仍可继续
- **WHEN** 系统已读取密钥并删除 `/dev/shm/tias_model_key`
- **THEN** 后续模型解密 MUST 使用已读取的内存中密钥，不得再次依赖副本文件存在

### Requirement: TIAS 必须保留可 restart 语义
TIAS secure runtime MUST 在宿主机源密钥文件和挂载关系保留时支持 `docker restart`。

#### Scenario: 源密钥保留时 restart 成功
- **WHEN** 宿主机源密钥文件存在且容器仍挂载 `/run/bootstrap-secrets/tias_model_key`
- **THEN** 执行 `docker restart` 后 entrypoint MUST 重新复制 `/dev/shm/tias_model_key` 并允许服务重新解密模型启动

#### Scenario: 源密钥删除后 restart 失败
- **WHEN** 部署后删除宿主机源密钥文件或移除密钥挂载
- **THEN** 执行 `docker restart`、容器重建或宿主机重启后服务 MUST 因无法读取源密钥而启动失败

#### Scenario: 文档禁止将删除源密钥作为生产恢复方案
- **WHEN** 用户查看 TIAS secure runtime 部署文档
- **THEN** 文档 MUST 明确删除宿主机源密钥文件会破坏 restart/recreate/故障恢复能力，不得作为稳定生产保护方案

### Requirement: TIAS 必须清理临时明文模型文件
TIAS MUST 在模型保护开启时将加密模型解密到临时目录并在加载后清理明文模型文件。

#### Scenario: 解密目录使用 /dev/shm
- **WHEN** secure runtime 使用加密模型启动
- **THEN** `DecryptedTempRoot` SHOULD 配置为 `/dev/shm/tias-models` 或等效内存文件系统路径

#### Scenario: 临时模型加载后清理
- **WHEN** `CleanupAfterLoad=true` 且模型加载成功
- **THEN** 系统 MUST 删除临时明文模型文件

#### Scenario: 临时目录权限受限
- **WHEN** 系统创建临时模型解密目录
- **THEN** 临时目录权限 MUST 限制为当前运行用户可读写，推荐 `0700`

### Requirement: TIAS 密钥引导文档必须说明安全边界
TIAS secure runtime 文档 MUST 说明本地文件密钥方案的保护能力和限制。

#### Scenario: 不承诺防 root 权限
- **WHEN** 用户查看 secure runtime 文档
- **THEN** 文档 MUST 明确本阶段不使用 KMS/Vault，不承诺防止容器 root、宿主机 root 或 Docker socket 持有者读取挂载源、内存或运行时文件

#### Scenario: 说明源密钥保留要求
- **WHEN** 用户查看 secure runtime 文档
- **THEN** 文档 MUST 明确为了支持 `docker restart`，宿主机密钥源文件必须保留在受控路径，并用严格权限保护

#### Scenario: 说明推荐权限
- **WHEN** 用户查看 secure runtime 文档
- **THEN** 文档 MUST 建议密钥源文件只允许 root 或部署用户读取，容器挂载使用只读模式
