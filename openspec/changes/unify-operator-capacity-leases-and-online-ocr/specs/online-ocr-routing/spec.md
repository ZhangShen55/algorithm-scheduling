## ADDED Requirements

### Requirement: Online Gateway 提供单图 OCR 接口
Online Gateway SHALL 提供 `POST /api/online/ocr/recognize`，接收一张由上游提供的 Base64 图片，并 SHALL NOT 拉取视频流、执行抽帧或创建课程任务。

#### Scenario: 提交合法单图请求
- **WHEN** 上游提交一个包含合法 `image` 的请求
- **THEN** 网关 SHALL 同步调用一个已租赁的 OCR 实例并用现有 `BusinessResponse` 返回结果

#### Scenario: 请求不包含合法图片
- **WHEN** `image` 缺失、为空、Base64 无效或超过网关配置的正文/解码大小限制
- **THEN** 网关 SHALL 在申请租约前返回业务码 `40001` 且 SHALL NOT 调用 OCR 算子

### Requirement: 在线图片入口执行统一大小限制
Online Gateway SHALL 从根 `config.toml` 读取 `body.max_bytes=75497472` 和 `base64.max_decoded_bytes=52428800`，并 SHALL 在申请算子租约前对在线图片请求实际执行 72 MiB 正文和单图 Base64 解码后 50 MiB 限制。OCR 算子根配置和受控部署配置 SHALL 使用 `ocr.image_max_bytes=52428800`，使在线 OCR 与 PPT OCR 共享相同的单图文件上限。

#### Scenario: 请求体超过 72 MiB
- **WHEN** 任一 Online Gateway 在线图片请求的 HTTP 正文超过 `75497472` 字节
- **THEN** 网关 SHALL 在完整业务处理和租约申请前拒绝请求，且 SHALL NOT 把超限正文转发给算子

#### Scenario: Base64 解码后超过 50 MiB
- **WHEN** 在线图片完成 Base64 解码后的文件内容超过 `52428800` 字节
- **THEN** 网关 SHALL 返回参数错误且 SHALL NOT 申请算子租约

#### Scenario: PPT OCR 绕过在线网关调用 OCR
- **WHEN** Orchestrator 将 PPT 切片直接提交给 OCR 算子且单图超过 `52428800` 字节
- **THEN** OCR 算子 SHALL 使用自身 `image_max_bytes` 拒绝该图片，且 SHALL NOT 依赖 Online Gateway 才执行限制

### Requirement: 公式识别默认关闭且可显式开启
单图 OCR 请求的 `enable_formula` SHALL 为可选严格布尔值，省略时 SHALL 等于 `false`；`image_id` SHALL 可选，省略时网关 SHALL 生成请求内唯一标识。

#### Scenario: 省略公式开关
- **WHEN** 上游只提交 `image` 或同时提交 `image_id` 而未提交 `enable_formula`
- **THEN** 网关转发给 OCR 的 `enable_formula` SHALL 为 `false`

#### Scenario: 显式开启公式识别
- **WHEN** 上游提交 `enable_formula=true`
- **THEN** 网关 SHALL 将 `true` 原样传给 OCR 且 SHALL NOT 在网关内执行公式识别

#### Scenario: 省略图片标识
- **WHEN** 上游未提交 `image_id`
- **THEN** 网关 SHALL 生成非空标识，并在 OCR 响应的 `key` 中返回可对应本次图片的标识

### Requirement: 网关适配现有 OCR 算子契约
Online Gateway SHALL 把单图请求转换为 OCR 现有 `/ocr/prediction` 所需的单元素 `key` 和 `value` 数组及 `enable_formula`，并 SHALL NOT 修改 OCR 算子的路径、请求模型或响应模型。

#### Scenario: 转发一张图片
- **WHEN** 网关准备调用选中的 OCR 实例
- **THEN** 请求 SHALL 使用 `key=[image_id]`、`value=[image]` 和解析后的 `enable_formula`，数组长度 SHALL 恰好为一

#### Scenario: OCR 成功返回
- **WHEN** OCR 返回合法响应对象
- **THEN** 网关的 `BusinessResponse.data` SHALL 保留 `key`、`value`、`formula_results`、`err_no` 和 `err_msg` 的算子语义

### Requirement: 每个在线 OCR 请求独立使用共享租约
每个单图在线 OCR 请求 SHALL 在调用前申请一个带在线工作上下文的 `ocr` 租约，在收到完整响应或失败后释放；在线请求与 PPT 离线图片 SHALL 平等竞争同一 OCR 实例池，SHALL NOT 设置来源保留槽位或固定实例偏好。

#### Scenario: 在线和离线同时请求 OCR
- **WHEN** Online Gateway 和 Orchestrator 同时申请 `ocr` 容量
- **THEN** 两者 SHALL 使用相同候选实例、共享容量和原子分配规则，任一来源 SHALL NOT 获得预留配额

#### Scenario: 实例选择偏向排序靠前节点
- **WHEN** 当前确定性分配算法优先使用 `ocr-gpu0` 且该实例仍有容量
- **THEN** 该选择 SHALL 被允许，平等共享 SHALL NOT 被解释为必须轮询实例

### Requirement: 在线 OCR 不排队并使用现有业务错误风格
Online Gateway SHALL 将 OCR 容量不足直接映射为业务码 `50301`，将 OCR HTTP、响应格式或调用异常映射为 `50000`，并保持在线接口 HTTP `200` 的业务响应风格。

#### Scenario: 所有 OCR 实例无可用租约
- **WHEN** Control Service 的内部租约请求返回容量不可用
- **THEN** 网关 SHALL 立即向上游返回业务码 `50301`，且 SHALL NOT 在 Control Service 或网关建立等待队列

#### Scenario: OCR 上游调用失败
- **WHEN** 已取得租约但 OCR 实例超时、返回非成功 HTTP 状态或响应不是合法对象
- **THEN** 网关 SHALL 释放租约并返回业务码 `50000`

### Requirement: 既有在线与 PPT 链路保持不变
新增 OCR 路由 SHALL NOT 改变现有 VBas、FaceRec、ScreenDet、ASR Online 路由，也 SHALL NOT 改变 PPT Slice、逐图 OCR、逐图关键词和 PostgreSQL 结果持久化契约。

#### Scenario: 对比网关和离线链路回归
- **WHEN** 新 OCR 路由完成后执行现有在线路由测试和 PPT OCR/关键词跨服务测试
- **THEN** 既有请求响应、租约释放、共享路径和单项结果身份 SHALL 保持兼容
