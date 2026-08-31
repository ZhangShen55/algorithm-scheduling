## 1. 配置与回归基线

- [x] 1.1 为 `[Inference]` 缺省值、显式布尔值和 `CONFIG_PATH` 覆盖补充配置加载测试，断言两个顺序开关默认为 `true`、四个精度开关默认为 `false`
- [x] 1.2 为学生三模型调用顺序、并行兼容分支、SyncTasks2 多 Polygon 顺序及输出顺序建立失败优先的单元测试
- [x] 1.3 为四个 `UseHalf` 字段建立模型替身测试，覆盖人数模型的所有正式调用路径以及人脸、学生、教师模型路径，并断言配置互不串用
- [x] 1.4 保存变更前接口路径、请求/响应结构、单请求调用顺序和历史约 15 GiB 显存高水位的脱敏基线证据

## 2. 推理配置实现

- [x] 2.1 在 VBas Settings 中增加结构化 `[Inference]` 配置及默认值，保持旧 `config.toml` 可直接启动
- [x] 2.2 在 `vbas/config.toml` 中增加六个字段和中文注释，不增加 `GpuInferenceConcurrency` 或隐式并发默认值
- [x] 2.3 在 `algorithm-scheduling-platform/deploy/config/operators/vbas.gpu.toml` 中同步六个生产部署字段和中文注释
- [x] 2.4 更新 VBas README 的配置说明、重启生效边界和“只控制请求内部、不限制跨请求并发”的剩余风险

## 3. 请求内顺序执行

- [x] 3.1 让学生行为接口根据 `StudentModelsSequential` 选择顺序或现有并行分支，顺序模式严格按人数、人脸、学生行为执行
- [x] 3.2 确保多图片学生请求逐图保持三模型顺序，且两种模式的 `DataList`、对象类型、阈值和失败语义兼容
- [x] 3.3 让 Base64 `/AE/SyncTasks2` 根据 `SyncTasks2PolygonsSequential` 选择逐 Polygon 或现有并行分支，并保持 `PolygonResult` 输入顺序
- [x] 3.4 对仍可访问的文件路径兼容实现应用相同 Polygon 配置语义，不修改备份文件或恢复已移除路由

## 4. 四模型独立精度

- [x] 4.1 使用 `PersonUseHalf` 替换人数模型所有正式 `predict` 调用中的全局 `use_half`
- [x] 4.2 使用 `FaceUseHalf` 替换人脸模型所有正式 `predict` 调用中的全局 `use_half`
- [x] 4.3 分别使用 `StudentUseHalf` 和 `TeacherUseHalf` 控制学生与教师行为模型，并移除运行时代码对单一全局 `use_half` 的依赖
- [x] 4.4 静态检索并复核所有 `half=`、`use_half` 和四个模型 `predict` 调用，确认没有遗漏、交叉映射或无条件 FP16

## 5. 本地验证

- [x] 5.1 在 `jy-tias` 环境执行 `python -m compileall -q app scripts tests`、完整 VBas pytest 和 `pip check`
- [x] 5.2 使用默认配置启动 `app.main:app`，验证 `/AE/Health`、平台注册相关路由和配置元数据不回归
- [x] 5.3 使用固定图片分别真实调用学生、教师和 `/AE/SyncTasks2` 单/多 Polygon 接口，确认请求响应合同和结果顺序
- [x] 5.4 分别用两个顺序开关的 true/false 组合执行自动化回归，确认兼容并行分支仍可使用
- [x] 5.5 验证四个 `UseHalf=false` 时结果与当前 FP32 基线兼容；逐模型 FP16 的精度结论留到 NVIDIA 环境验证
- [x] 5.6 执行 OpenSpec 严格校验并确认本次差异不包含 `GpuInferenceConcurrency`、在线优先队列或容量参数调整

## 6. Git 与远端构建准备

- [x] 6.1 复核工作区，只纳管本变更代码、测试、配置、文档和 OpenSpec/Harness 文件，不混入既有用户改动
- [x] 6.2 使用中文规范提交并推送同一目标 Git SHA，记录完整 SHA、分支和远端状态
- [x] 6.3 在 `192.168.29.11` 记录三个旧 VBas 容器完整 ID、旧镜像完整 ID、GPU 绑定、配置摘要、健康和注册状态
- [x] 6.4 确认三个旧 VBas 实例无运行中批次或租约，并保留其他六类算子、四个平台服务和中间件不变
- [x] 6.5 检查目标机 Docker 构建缓存、磁盘、基础镜像、模型和发布源码 SHA，禁止为本次构建执行 buildx prune 或其他缓存清理

## 7. 新镜像构建与受控替换

- [x] 7.1 使用现有 Docker layer/build cache 构建带当前日期标签的新 VBas x86_64 镜像，并写入目标 Git SHA/revision
- [x] 7.2 在停止旧容器前检查新镜像完整 ID、架构、revision、入口、模型文件和六个配置字段
- [x] 7.3 逐个重建 GPU0、GPU1、GPU2 的 VBas 容器，每次替换后确认对应 GPU 绑定、单 worker 和 Docker health，再继续下一个实例
- [x] 7.4 确认三个新实例均向 Control Service 注册并持续心跳，能力仍为 `student_behavior`、`teacher_behavior` 和 `person_count`
- [x] 7.5 进入每个容器核对 `[Inference]` 六个有效值，确认没有 `GpuInferenceConcurrency`，且现有在线/离线容量值未被改变

## 8. 三卡真实 GPU 验证

- [x] 8.1 每轮测试前重启对应 VBas 容器，逐模型预热并记录每张 GPU 的 VBas 进程显存及 PyTorch allocated/reserved 基线
- [x] 8.2 使用固定图片在三个实例分别执行学生和教师真实推理，记录结果摘要、成功率、P50/P95 和显存峰值/驻留值
- [x] 8.3 使用 `frame_000068.jpg` 执行 `/AE/SyncTasks2` 单 Polygon 与多 Polygon 真实推理，验证顺序、响应兼容和显存变化
- [x] 8.4 执行离线师生行为、在线人数和两者混合的固定并发回归，确认三实例均获得真实工作且平台路由不回归
- [x] 8.5 等待请求、租约和队列归零后记录驻留显存，与变更前约 15 GiB 历史高水位区分，任何 OOM 或显存异常增长均如实判失败
- [x] 8.6 对 `PersonUseHalf`、`FaceUseHalf`、`StudentUseHalf`、`TeacherUseHalf` 逐个做 NVIDIA 有效性和固定图片结果对比，默认发布配置保持全部为 false

## 9. 验收、回滚与清理

- [x] 9.1 汇总本地测试、三容器健康、三实例注册、GPU/PID 绑定、真实推理、接口合同、耗时和显存证据并写入中文 Harness
- [x] 9.2 若任一门禁失败，停止新负载，保留旧镜像并使用账本中的旧镜像完整 ID回滚三个 VBas 实例，不执行旧资产清理（本次门禁全部通过，未触发回滚）
- [x] 9.3 仅在全部门禁通过后，按完整 ID移除被替换的旧 VBas 容器残留和旧 VBas 镜像，保留新镜像、其他服务资产和 Docker 构建缓存
- [x] 9.4 清理后再次验证三个新 VBas 容器 healthy、三实例注册、GPU 绑定和学生/教师/人数 Smoke，并记录最终镜像与容器完整 ID
- [x] 9.5 更新 OpenSpec 任务状态、最终测试结论和剩余风险；跨 HTTP 请求显存仍异常时另建 `GpuInferenceConcurrency` 变更，不在本变更临时追加
