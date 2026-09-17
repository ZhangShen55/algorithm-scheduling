## ADDED Requirements

### Requirement: 项目必须使用标准包结构和入口
临时项目和最终 FaceRec 项目 SHALL 在项目根提供真实 `app/` Python 包，并以 `app.main:app` 作为唯一规范入口。跨包导入 MUST 使用 `app.` 绝对导入，配置和模型路径 MUST 从显式项目根解析，且 `CONFIG_PATH` SHALL 可覆盖根 `config.toml`。

#### Scenario: 从项目根启动
- **WHEN** 在项目根执行 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8003 --workers 1`
- **THEN** 导入和启动成功，不需要符号链接、临时 `PYTHONPATH` 或特殊目录名

#### Scenario: 从不同当前目录导入
- **WHEN** 测试以受支持方式从非项目根导入包并显式提供项目位置
- **THEN** 配置、模型、日志和媒体仍解析到项目根边界内

### Requirement: 平台注册与容量合同必须保留
FaceRec SHALL 继续使用共享 operator registry client，从 `[platform]` 读取注册开关、Control Service 地址、心跳间隔和声明容量，并以 `operator_code=facerec`、`capabilities=["recognize"]` 注册。Dlib worker 数量 MUST 与平台 `max_concurrent_requests` 保持独立语义。

#### Scenario: 默认本地配置启动
- **WHEN** 根配置使用默认 `registration_enabled=false`
- **THEN** 服务不连接 Control Service，仍能独立启动和执行本地推理

#### Scenario: 启用平台注册
- **WHEN** 配置提供有效 Control Service 且启用注册
- **THEN** 实例按配置上报能力、端口、readiness 和容量，并在关闭前停止接收新租约

### Requirement: GPU 严格模式不得静默回退
`[gpu].device` MUST 只接受 `cpu` 或 `cuda:N`。当 `[runtime].require_gpu=true` 时，InsightFace 与 ArcFace 都 MUST 使用所选 CUDA 设备；CUDA provider、设备索引或模型初始化失败 SHALL 使 readiness 失败，不得静默切换 CPU。显式 `cpu` 配置 SHALL 支持本地验证。

#### Scenario: 严格 GPU 配置有效
- **WHEN** `require_gpu=true`、设备为可见的 `cuda:N` 且两个推理后端均成功建立 CUDA runtime
- **THEN** 模型 readiness 成功并报告所选逻辑设备，不泄露无关设备信息

#### Scenario: 严格 GPU 配置无法使用 CUDA
- **WHEN** `require_gpu=true` 但 CUDA provider 不可用、设备越界或任一模型落到 CPU
- **THEN** 服务不得宣告 ready，且日志给出不含凭据和模型内容的失败原因

#### Scenario: 显式 CPU 本地运行
- **WHEN** `require_gpu=false` 且 `[gpu].device="cpu"`
- **THEN** 两类模型均使用 CPU provider，服务可通过本地真实推理验证

### Requirement: Readiness 必须覆盖全部关键依赖
`/ops/health` SHALL 表示进程存活，readiness SHALL 分别验证 MongoDB、所选检测器 worker、ArcFace embedding 模型和严格设备要求。FaceRec MUST NOT 以直连 Redis 作为 readiness 条件；平台注册和租约状态 SHALL 通过 Control Service 合同观测。

#### Scenario: 模型未加载完成
- **WHEN** HTTP 进程已启动但 InsightFace worker 或 ArcFace 尚未完成初始化
- **THEN** health 可返回存活而模型 readiness 保持失败，平台注册不得把实例作为可租用识别实例

#### Scenario: 平台 Redis 不对算子开放
- **WHEN** MongoDB 与模型 ready 且 FaceRec 没有平台 Redis 连接参数
- **THEN** FaceRec readiness 可成功，注册和租约由 Control Service 负责且算子不绕过服务边界

### Requirement: 日志与运行数据必须遵守平台边界
FaceRec SHALL 使用共享日志包向 stdout 和 `logs/{instance_id}/application.log` 输出 JSON Lines，默认活动文件上限 100 MiB、归档保留七天。日志 MUST NOT 包含 Base64、媒体字节、完整请求或响应体、凭据、完整人员信息、embedding 或模型内容。可变媒体 MUST 位于项目根 `media/`，且 `save_person_photo=false` 时不得保存录入人脸图片。

#### Scenario: 识别请求被记录
- **WHEN** 服务处理包含 Base64 图片和 targets 的识别请求
- **THEN** 日志只包含 request ID、路径、耗时、数量、状态和必要错误摘要，不包含图片、完整 targets 或 embedding

#### Scenario: 禁用人员照片持久化
- **WHEN** `save_person_photo=false` 且新增或批量新增人员成功
- **THEN** MongoDB 保存非空 embedding、`photo_path` 为空，并且 `media/person_photos/` 不产生该人员图片

### Requirement: 依赖和容器必须可重复构建
FaceRec SHALL 为 Python 3.10 锁定经验证兼容的 FastDeploy、InsightFace、ONNX Runtime、NumPy、OpenCV、Dlib、FastAPI 和数据库依赖组合，并 SHALL 排除未使用的 Redis client。Docker 镜像 MUST 以 `app.main:app` 启动、创建 `logs/` 和 `media/` 目录，并排除嵌套 Git、测试 cache、日志、媒体、部署 tar 和其他非运行时资产。

#### Scenario: 依赖一致性检查
- **WHEN** 在干净 Python 3.10 环境安装 requirements
- **THEN** `pip check`、关键模块导入、compileall 和测试全部成功，不出现 NumPy、OpenCV 或 ONNX Runtime 二进制 ABI 冲突

#### Scenario: 检查容器构建上下文
- **WHEN** 构建 FaceRec 镜像
- **THEN** 构建输入只包含允许的运行时源码、配置和按部署合同提供的模型，不包含 `.git`、日志、媒体、人脸原图或部署 tar

### Requirement: FaceRec 路由边界必须保持单图上游输入
FaceRec SHALL 只处理调用方提供的图片，并 SHALL NOT 新增 RTSP、视频拉流、抽帧、课程编排、跨实例聚合或数据库外写逻辑。

#### Scenario: 在线识别输入
- **WHEN** Online Gateway 调用 FaceRec 识别接口
- **THEN** FaceRec 只解析请求携带的单张图片并返回识别结果，不访问视频源或生成额外帧

### Requirement: 平台适配必须在指定服务器完成
FaceRec 的最终平台适配和验收 MUST 在 `192.168.29.11` 的既有算子平台完成，并使用工作区已批准的固定 `root` 登录合同。登录密码 MUST NOT 写入 Harness 普通证据、命令输出或新增配置文件。其他主机或本地 Compose 的结果不得替代该服务器的三实例、GPU、注册、租约和 Online Gateway 证据。

#### Scenario: 在指定平台主机完成三实例验收
- **WHEN** 最终候选部署到 `192.168.29.11`
- **THEN** `facerec-gpu0`、`facerec-gpu1`、`facerec-gpu2` 均使用预期 GPU、完成注册并能通过真实租约处理识别请求

#### Scenario: 只有本地验证结果
- **WHEN** 源码测试、本地 CPU 推理或非指定主机 GPU 推理通过但 `192.168.29.11` 尚无成功证据
- **THEN** 平台适配状态保持未完成，不得宣称 FaceRec v1.3 已完成最终替换
