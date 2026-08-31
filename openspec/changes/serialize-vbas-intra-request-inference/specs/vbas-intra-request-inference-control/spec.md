## ADDED Requirements

### Requirement: VBas 必须提供请求内推理配置
VBas MUST 从根 `[Inference]` TOML 段读取 `StudentModelsSequential`、`SyncTasks2PolygonsSequential`、`PersonUseHalf`、`FaceUseHalf`、`StudentUseHalf` 和 `TeacherUseHalf`。缺少配置段或字段时，两个顺序开关 MUST 默认为 `true`，四个精度开关 MUST 默认为 `false`。本能力 MUST NOT 增加或隐式派生 `GpuInferenceConcurrency`。

#### Scenario: 旧配置缺少 Inference 段
- **WHEN** VBas 使用不含 `[Inference]` 的旧 `config.toml` 启动
- **THEN** 服务成功启动，学生模型和 SyncTasks2 Polygon 使用顺序模式，四个模型继续使用 FP32

#### Scenario: 部署配置显式声明全部字段
- **WHEN** 部署配置为六个字段提供合法布尔值
- **THEN** 容器内有效配置与文件值逐项一致，并且配置不会改变现有在线/离线容量值

### Requirement: 学生行为请求必须支持顺序模型执行
当 `StudentModelsSequential=true` 时，VBas MUST 对一张学生图片依次执行人数、人脸和学生行为模型，前一个模型完成后才能开始下一个模型。当该值为 `false` 时，VBas MUST 保留现有请求内并行兼容模式。两种模式 MUST 返回相同的 HTTP 路径、响应模型、图片顺序、对象类型和错误语义。

#### Scenario: 默认顺序处理单张学生图片
- **WHEN** `StudentModelsSequential=true` 且调用 `/ImageDetect/student/v1.0.0` 处理一张有效图片
- **THEN** 人数、人脸和学生行为模型严格按此顺序各调用一次，响应保持现有合同

#### Scenario: 多图片逐图保持模型顺序
- **WHEN** 顺序模式处理包含多张图片的学生请求
- **THEN** 每张图片内部的三个模型按规定顺序执行，并且 `DataList` 顺序与 `ImageList` 一致

#### Scenario: 显式恢复兼容并行模式
- **WHEN** `StudentModelsSequential=false` 且调用学生接口
- **THEN** 三个模型走现有并行分支，响应字段和业务结果组装规则不发生变化

### Requirement: SyncTasks2 必须支持 Polygon 顺序执行
当 `SyncTasks2PolygonsSequential=true` 时，VBas MUST 按 `AnalysisRule.AlgParams.PolygonList` 输入顺序逐个完成 Polygon 推理。当该值为 `false` 时，VBas MUST 保留现有请求内并行兼容模式。两种模式的 `PolygonResult` 顺序 MUST 与输入顺序一致。

#### Scenario: 默认顺序处理多个 Polygon
- **WHEN** `SyncTasks2PolygonsSequential=true` 且 `/AE/SyncTasks2` 收到一个包含多个 Polygon 的有效请求
- **THEN** 后一个 Polygon 只能在前一个 Polygon 完成后开始，并且响应结果顺序与请求一致

#### Scenario: 空 Polygon 列表保持兼容
- **WHEN** `/AE/SyncTasks2` 收到现有合同允许的空 Polygon 列表
- **THEN** 服务保持当前空结果语义，不隐式执行整图推理

#### Scenario: 显式恢复 Polygon 并行模式
- **WHEN** `SyncTasks2PolygonsSequential=false` 且请求包含多个 Polygon
- **THEN** 服务使用现有并行分支，并仍按输入顺序返回 `PolygonResult`

### Requirement: 四个模型必须独立选择推理精度
VBas MUST 把 `PersonUseHalf`、`FaceUseHalf`、`StudentUseHalf` 和 `TeacherUseHalf` 分别映射到人数、人脸、学生行为和教师行为模型的所有运行时 `predict` 调用。一个模型的配置 MUST NOT 改变其他模型调用的 `half` 参数。

#### Scenario: 默认保持四模型 FP32
- **WHEN** 四个精度开关均为 `false`
- **THEN** 人数、人脸、学生行为和教师行为模型的每次 `predict` 都使用 `half=false`

#### Scenario: 单独启用一个模型的 FP16
- **WHEN** 仅 `PersonUseHalf=true`，其他三个精度开关为 `false`
- **THEN** 只有所有人数模型调用使用 `half=true`，其他模型仍使用 `half=false`

#### Scenario: 所有模型配置均被覆盖
- **WHEN** 自动化测试分别切换四个精度开关
- **THEN** 每个仍可访问的正式推理路径都使用所属模型的配置，运行时代码不再依赖单一全局 `use_half`

### Requirement: 推理优化必须保持算子接口兼容
本变更 MUST NOT 修改 `/ImageDetect/student/v1.0.0`、`/ImageDetect/teacher/v1.0.0` 和 `/AE/SyncTasks2` 的路径、方法、请求字段、响应字段、默认端口或平台注册能力。在线 Gateway 和 Vision Orchestrator MUST NOT 因本变更修改调用合同。

#### Scenario: 三个接口执行回归
- **WHEN** 使用变更前兼容的请求分别调用学生、教师和人数接口
- **THEN** 三个接口均返回原响应结构，并且不出现新增必填字段

#### Scenario: 平台继续路由现有能力
- **WHEN** 三个 VBas 实例启动并注册 `student_behavior`、`teacher_behavior` 和 `person_count`
- **THEN** Control Service、Online Gateway 和 Vision Orchestrator 继续按现有容量池与路径调用实例

### Requirement: VBas 新镜像必须受控替换
目标机部署 MUST 使用现有 Docker 构建缓存构建新 VBas 镜像，并在替换前记录旧容器和旧镜像完整 ID。构建期间旧容器 MUST 继续运行；新容器通过健康、注册、GPU 绑定和真实推理门禁前，旧镜像 MUST 保留。全部门禁通过后，系统 MUST 精确移除被替换的旧 VBas 容器残留和旧 VBas 镜像，并 MUST NOT 删除其他算子镜像或构建缓存。

#### Scenario: 新镜像通过全部门禁
- **WHEN** 新镜像已构建，三个新 VBas 容器均 healthy、正确绑定三张 GPU、完成注册并通过学生/教师/人数真实推理
- **THEN** 部署记录新旧完整资产 ID，删除被替换的旧 VBas 资产，并保留构建缓存和其他服务资产

#### Scenario: 新容器验证失败
- **WHEN** 任一新 VBas 容器健康、注册、GPU 绑定、配置或真实推理检查失败
- **THEN** 部署停止清理，保留旧镜像，使用记录的旧镜像完整 ID回滚并保存失败证据

### Requirement: 显存回归必须区分基线峰值和驻留值
真实 GPU 验证 MUST 在每档测试前重启对应 VBas 容器，逐模型预热后记录基线，并对学生行为、多 Polygon 人数及混合并发分别记录 VBas 进程显存、PyTorch allocated/reserved、峰值、成功率和耗时。本阶段 MUST NOT 通过新增 GPU 推理并发限制掩盖顺序化效果。

#### Scenario: 顺序模式显存回归
- **WHEN** 三实例使用相同固定图片、请求数量和并发度执行顺序模式测试
- **THEN** 报告包含每张 GPU 的预热基线、测试峰值、任务归零后的驻留值、错误和延迟，并与变更前约 15 GiB 的历史高水位证据区分

#### Scenario: 高并发仍出现显存增长
- **WHEN** 请求内顺序化后，不同 HTTP 请求的并发仍造成显存异常增长或 OOM
- **THEN** 本变更如实记录失败和剩余风险，不临时加入未设计的 `GpuInferenceConcurrency`
