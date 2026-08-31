## 为什么

VBas 当前会在单个学生行为请求内并行执行人数、人脸和学生行为模型，并在单个 `/AE/SyncTasks2` 请求内并行处理多个 Polygon；历史高并发后单个 VBas 进程显存从约 1 GiB 增长并驻留到约 15 GiB，最终与同卡其他算子共同触发 CUDA OOM。现阶段需要先消除请求内部的推理扇出，并通过配置保留可回退和逐模型验证能力，再决定是否引入进程级 GPU 推理并发限制。

## 变更内容

- 在 `vbas/config.toml` 新增 `[Inference]` 配置段及中文注释。
- 新增 `StudentModelsSequential`，默认按人数、人脸、学生行为的顺序处理一个学生请求。
- 新增 `SyncTasks2PolygonsSequential`，默认按请求顺序逐个处理 `/AE/SyncTasks2` 中的 Polygon。
- 将现有全局 FP32/FP16 开关拆分为 `PersonUseHalf`、`FaceUseHalf`、`StudentUseHalf` 和 `TeacherUseHalf`，默认均为 `false`，保持现有 FP32 行为。
- 保持 VBas 现有 HTTP 路径、请求字段、响应字段、状态码、在线/离线容量配置和平台注册合同不变。
- 增加顺序/兼容模式、四模型精度映射、接口合同以及 GPU 显存回归测试。
- 在 `192.168.29.11` 使用构建缓存重新构建 VBas 镜像；新容器健康、注册和真实推理验证通过后，才移除被替换的旧 VBas 容器及旧镜像。
- 明确本次不增加 `GpuInferenceConcurrency`、在线优先 GPU 队列或跨 HTTP 请求的进程级推理限流。

## 能力

### 新增能力

- `vbas-intra-request-inference-control`：规定 VBas 请求内部的可配置顺序推理、四模型独立 FP16 开关、兼容性验证和受控镜像替换行为。

### 修改能力

无。

## 影响

- 代码：`vbas/app/core/settings.py`、学生行为推理服务、教师行为推理服务和 `SyncTasks2` 服务。
- 配置：`vbas/config.toml` 及部署时使用的 VBas 配置覆盖。
- 测试：VBas 单元测试、接口契约测试、CPU 可执行测试和 `192.168.29.11` 三实例真实 GPU 回归。
- 部署：仅重新构建和替换三个 VBas 容器；其他六类算子、四个平台服务和中间件不因本变更重建。
- API：无北向或算子 HTTP/WebSocket 合同变更。
