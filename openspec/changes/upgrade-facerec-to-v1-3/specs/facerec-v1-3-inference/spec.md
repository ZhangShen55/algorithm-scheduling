## ADDED Requirements

### Requirement: v1.3 检测与特征模型必须完整加载
FaceRec SHALL 支持以 InsightFace `buffalo_l` 检测和对齐人脸，并 SHALL 继续使用 `ms1mv3_arcface_r100.onnx` 生成 512 维 embedding。运行时 MUST 从项目根 `ai_models/` 解析 ArcFace、Dlib 68 点及 `models/buffalo_l/` 下五个 ONNX 文件，且不得依赖当前工作目录。

#### Scenario: 完整模型启动成功
- **WHEN** 七个必需模型位于规定相对路径且配置选择 InsightFace
- **THEN** FaceRec 成功初始化检测与 embedding 模型，模型 readiness 为成功，并能处理真实图片

#### Scenario: 任一必需模型缺失
- **WHEN** 配置选择 InsightFace但任一必需模型不存在、为空或无法加载
- **THEN** 模型 readiness 保持失败，并明确记录缺失的相对路径而不输出模型内容

### Requirement: 单图识别必须支持多人脸
`POST /recognize` SHALL 对整张上游图片中全部满足阈值和尺寸要求的人脸执行检测、对齐、embedding 和匹配。系统 MUST NOT 自行拉取视频、接入 RTSP 或执行抽帧。

#### Scenario: 单图包含多张有效人脸
- **WHEN** 请求图片中包含两张或更多满足条件的人脸
- **THEN** 系统处理全部有效人脸，将人员匹配按 number 去重并按相似度排序写入既有 `match` 列表，同时保持既有响应字段

#### Scenario: 不扩展旧请求模型
- **WHEN** 对升级前后的 `/recognize` OpenAPI 请求模型执行字段比较
- **THEN** 请求字段仍只有既有 `photo`、`targets` 和 `threshold`，不会引入上游 `points`/ROI 字段

### Requirement: 识别 HTTP 合同必须保持兼容
升级 SHALL 保持 `/recognize`、`/recognize/batch`、`/persons`、`/persons/batch`、`/persons/search`、`/persons/delete` 和 `/ops/*` 的既有路径、方法、请求字段、响应字段、业务状态码和默认端口。响应 MUST 继续使用 `status_code`、`has_face`、`bbox`、`match` 等当前字段，不得以 `statusCode`、`hasFace` 或 `bboxs` 替换。

#### Scenario: 对比升级前后 OpenAPI
- **WHEN** 对升级前后的 FastAPI OpenAPI 文档执行路径、方法和模型字段比较
- **THEN** 所有既有合同均保持兼容，且不会出现替代现有字段的上游驼峰字段

#### Scenario: 批量识别保持单人多帧语义
- **WHEN** 调用方向 `/recognize/batch` 提交多帧图片
- **THEN** 系统继续按单人多帧聚合返回既有结构，不把该接口改成单图多人脸的新合同

### Requirement: 旧人员 embedding 必须保持可读可匹配
升级 SHALL 读取现有 MongoDB 中合法的 512 维 float32 embedding，不得因版本升级自动删除、重写或批量重算人员记录。使用同一 ArcFace 模型产生的新查询 embedding MUST 通过既有阈值和候选规则完成匹配。

#### Scenario: 使用升级前人员库识别
- **WHEN** MongoDB 中存在升级前保存的合法 embedding 且提交对应人员的真实图片
- **THEN** 升级版成功读取旧记录并返回可接受的匹配结果，数据库记录不会被迁移写操作改变

#### Scenario: 遇到损坏 embedding
- **WHEN** 候选记录缺少 embedding、维度错误、包含非有限值或二进制格式无效
- **THEN** 系统跳过该候选、记录不含 embedding 内容的原因，并继续处理其他合法候选

### Requirement: MongoDB 必须保持唯一人员事实来源
FaceRec SHALL 从 MongoDB 读取和写入人员事实及 embedding，MUST NOT 直接连接平台 Redis 或移植上游 Redis embedding cache。人员新增、更新和删除成功后，三个 FaceRec 实例的后续识别 SHALL 通过共享 MongoDB 观察到一致事实。

#### Scenario: 三实例读取共享人员事实
- **WHEN** 管理实例成功新增或更新人员且三个 FaceRec 实例随后执行识别
- **THEN** 三个实例都从共享 MongoDB 观察到最新合法 embedding，不依赖进程外 Redis cache

#### Scenario: 删除人员后所有实例不可再命中
- **WHEN** 人员删除已成功提交 MongoDB
- **THEN** 三个 FaceRec 实例的后续识别均不得返回该人员，且无需清理算子侧 Redis key

#### Scenario: 检查平台 Redis 连接边界
- **WHEN** 对 FaceRec 配置、依赖、源码和远端连接执行检查
- **THEN** FaceRec 不包含人员 cache 的 Redis 连接，平台注册与租约仍只通过 Control Service 完成

### Requirement: 真实推理必须覆盖关键识别路径
验证 SHALL 使用项目 fixture 和完整模型执行真实的录入与识别，不得仅以 mock 或模型文件存在性代替推理证据。结果 SHALL 覆盖有脸、无人脸、多人脸、命中、未命中和 batch 聚合路径。

#### Scenario: 真实图片闭环
- **WHEN** 在 `facerecapi` Python 3.10 环境中录入 `tests/data/常泽宇.png` 对应人员并再次识别该图片
- **THEN** 系统生成非空 512 维 embedding、返回命中结果，且 `save_person_photo=false` 时不写入人员原图

#### Scenario: 多人脸 fixture 推理
- **WHEN** 使用可重复的多人脸 fixture 调用 `/recognize`
- **THEN** 至少两张有效人脸进入检测和匹配流程，响应仍满足既有合同
