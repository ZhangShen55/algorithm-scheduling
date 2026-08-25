## ADDED Requirements

### Requirement: 平台必须交付唯一中文部署手册
交付物 SHALL 包含 `algorithm-scheduling-platform/deploy/算法功能调度平台部署手册.md`，并将其作为单机三 GPU 环境下七算子、四平台服务和四中间件的唯一中文部署操作入口。其他 README 和运维文档 MUST 引用该手册而不复制一套可漂移的完整部署步骤。

#### Scenario: 从部署 README 进入权威手册
- **WHEN** 部署人员从平台 README、部署 README 或单机运维手册查找首次部署步骤
- **THEN** 文档 SHALL 引导到同一份中文部署手册，而不给出相互冲突的命令顺序

#### Scenario: 当前拓扑不包含 Text Analysis
- **WHEN** 部署人员按手册盘点应构建和启动的镜像与实例
- **THEN** 手册 MUST 只列出七类当前算子、21 个算子实例和四个平台服务，不构建、启动、注册或 Smoke `text_analysis`

### Requirement: 部署手册必须覆盖完整交付生命周期
部署手册 SHALL 覆盖拓扑/端口、硬件/系统前置、Git/镜像/模型准备、`config.toml`、目录/数据卷、构建前精确清理、首次部署、数据库迁移、常驻启停/状态、A 服务 Smoke、升级、回滚、验收后退役、日志、备份边界、故障排查和验收清单。

#### Scenario: 新服务器从零开始部署
- **WHEN** 部署人员在满足硬件前置的新 x86_64 三 GPU 服务器上从头执行手册
- **THEN** 手册 SHALL 提供从目录与配置准备到基础设施、迁移、四平台、21 算子、注册和 A 服务 Smoke 的有序步骤

#### Scenario: 从已验收版本升级新 SHA
- **WHEN** 部署人员按手册将已运行版本升级到新 Git SHA
- **THEN** 手册 SHALL 要求先建立基线/保护集，再迁移、构建、启动、验收，且只在新版符合后精确退役旧版

### Requirement: 手册中的命令必须与权威实现一致
部署手册中的可执行命令 SHALL 指向仓库中的权威脚本、Compose 文件和 `start-production-stack`/`status-production-stack`/`stop-production-stack` 入口，每条命令 MUST 说明工作目录、前置参数、预期输出和失败处理。手册不得通过复制大段内部实现来形成第二套部署逻辑。

#### Scenario: 文档命令静态校验
- **WHEN** Harness 解析手册中的脚本路径、Compose 服务名、配置路径和端口
- **THEN** 所有引用 SHALL 在当前 Git SHA 存在并与权威拓扑和配置一致

#### Scenario: 按手册独立复现部署
- **WHEN** 未参与脚本实现的执行者只根据手册在 `192.168.29.11` 完成预检、常驻启动、状态查询和 A 服务 Smoke
- **THEN** 手册验证 SHALL 产生绑定当前 Git SHA 的完整成功证据，任何只能依赖口头补充才能完成的步骤都使用例失败

#### Scenario: 故障语义探针使用平台虚拟环境
- **WHEN** Fault Adapter 在目标机执行远端语义探针
- **THEN** 权威包装入口 MUST 使用平台 `.venv/bin/python` 运行探针源码并在虚拟环境缺失时失败关闭，不得依赖目标机全局 Python 临时安装 `websockets`

#### Scenario: 迁移账本引入前的完整旧库
- **WHEN** `public` 中已存在与唯一连续 `0001`–`N` 前缀完全一致的平台 schema，但迁移账本为空
- **THEN** 迁移入口 SHALL 先验证既有账本的完整结构，通过 `pg_class SHARE` 锁阻塞新 DDL 并要求已有事务/prepared transaction 已排空，再通过动态当前配置取得序列关系锁，在平台表和账本独占锁内二次核对账本 canonical 签名、访问方法、identity/serial 序列下一键/上下界/cycle/持久性与其他结构/数据不变量，只写入该前缀账本并正常执行剩余迁移；无唯一前缀、账本畸形、漂移或非 `public` 账本 MUST 失败关闭

### Requirement: 手册必须准确说明配置、凭据和数据边界
部署手册 SHALL 列出七算子和四平台服务的 `config.toml` 路径、部署必需项和可调项，明确当前部署不使用 `.env`。它 MUST 说明里程碑 2B 的 Online Gateway 使用 `max_connections=2048`、`max_keepalive_connections=512` 和有界 `pool_timeout_seconds`，该连接池只负责承接请求，业务容量最终由租约和算子实测能力决定。它 MUST 区分经用户明确批准记录的服务器登录合同与不得进入 Git/普通报告的私钥、模型密钥、人脸原图、课程媒体和外部模型 manifest。

#### Scenario: 关键在线容量配置与运行拓扑一致
- **WHEN** 部署人员按手册启动 Online Gateway 并执行 1000 个合法单图并发
- **THEN** 手册 SHALL 引导其验证运行配置为 `2048/512` 连接池且保留有界池等待，并判定无算子容量时由租约层返回 `50301`，不得把 Gateway 连接池产生的 `50000` 视为算子过载

#### Scenario: FaceRec 管理与识别使用不同路由边界
- **WHEN** 部署人员配置或验证 FaceRec 在线入口
- **THEN** 手册 SHALL 明确新增、批量新增、查询、搜索和删除固定转发到单一管理实例，`/face/recognize` 通过租约路由到三个识别实例，三个实例共享 MongoDB

#### Scenario: 临时与持久目录边界
- **WHEN** 部署人员查看媒体和结果数据留存规则
- **THEN** 手册 SHALL 明确 `/data/course/{task_id}` 是可在终态后受控清理的临时目录，`/data/result/{task_id}` 是默认保留的持久结果目录，且常规清理不得删除后者

#### Scenario: 部署前建立媒体源下载基线
- **WHEN** 部署人员准备执行离线单泳道或长课压力验证
- **THEN** 手册 SHALL 提供从 `192.168.29.11` 到 `192.168.29.12:5555` 的 1/3/10/30 并发下载基线命令、指标和判定方法，以区分媒体源/局域网、调度平台和算子瓶颈

#### Scenario: 未批准敏感值不进入手册
- **WHEN** 生成或审查部署手册
- **THEN** 手册 MUST 不包含 Deploy Key/私钥、模型解密密钥、人脸原图、课程媒体或外部可信模型 manifest 内容

### Requirement: 手册必须提供可勾选的验收与排障路径
部署手册 SHALL 提供部署前、部署后、升级后和清理后四份可勾选清单，并为常见失败给出“现象、查询命令、判定依据、受控处置、禁止操作”。验收 MUST 至少覆盖四中间件、四平台、21/21 注册、18/18 GPU、3/3 CPU PPT、7/7 Smoke、A 服务两个北向端口、日志和共享目录。

#### Scenario: 部署后完整验收
- **WHEN** 部署人员完成首次部署并执行部署后清单
- **THEN** 手册 SHALL 引导其验证同一 Git SHA 的镜像 revision、容器拓扑、GPU 归属、注册/租约、Smoke、A 服务连通和本地结果/日志路径

#### Scenario: 故障排查不使用破坏性捷径
- **WHEN** 算子不注册、租约不释放、Kafka lag 不下降、GPU 不可见、日志不生成或共享路径不可写
- **THEN** 手册 SHALL 先提供只读检查和精确恢复，不把 `docker system prune -a`、`docker compose down -v`、删除 `/data/result` 或清空数据库作为通用排障步骤

### Requirement: 部署手册必须与最终发布证据绑定
部署手册 SHALL 记录适用的拓扑版本、配置 schema、最后验证日期和最终 Git SHA/release 证据引用。任何影响目录、入口、配置、端口、拓扑、迁移、升级或清理的变更 MUST 同步更新手册并重跑文档验证。

#### Scenario: 脚本改变但手册未同步
- **WHEN** 当前 Git diff 修改了常驻入口、Compose、迁移、精确清理或配置路径，但手册没有对应更新和验证证据
- **THEN** 文档一致性门禁 SHALL 失败，当前 release 不得标记为可交付

#### Scenario: 手册绑定最终验收事实
- **WHEN** 里程碑 2B 发布交付手册
- **THEN** 手册 SHALL 引用实际通过的当前 release/Git SHA 证据，不得使用旧八算子 release 或仅本地模拟结果冒充当前七算子部署验证
