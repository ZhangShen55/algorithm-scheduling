## 背景

VBas 在模块加载时把人数、人脸、学生行为和教师行为四个 YOLO 模型加载到同一张 GPU。当前学生接口始终调用 `analyze_student_behavior_parallel`，一个请求内通过三个 `asyncio.to_thread` 同时执行三个模型；`/AE/SyncTasks2` 又通过 `asyncio.gather` 同时处理一个请求内的多个 Polygon。历史真实负载中，单个 VBas 进程显存由重启后的约 1 GiB 上升并驻留到约 15 GiB，随后在同卡混部条件下发生 CUDA OOM。

本变更是分阶段治理的第一步：只收敛请求内部扇出，不限制不同 HTTP 请求之间的并发。现有 `MaxConcurrentOfflineBatches`、`MaxConcurrentOnlineRequests`、`MaxQueueOnlineSize`、平台注册容量池及路由行为继续生效。

## 目标 / 非目标

**目标：**

- 默认顺序执行一个学生请求内的人数、人脸和学生行为模型。
- 默认顺序处理一个 `/AE/SyncTasks2` 请求内的 Polygon，并保持输入与输出顺序一致。
- 允许通过配置恢复两条现有并行路径，便于性能对照和紧急回退。
- 为四个模型分别配置 FP16，默认继续使用 FP32。
- 保持所有现有 VBas HTTP 契约、平台注册合同和在线/离线容量语义不变。
- 用本地自动化测试和三卡真实 GPU 测试分别验证行为正确性、显存高水位和业务响应。
- 使用已有 Docker 构建缓存生成新 VBas 镜像，并通过受控替换门禁清理旧资产。

**非目标：**

- 不增加 `GpuInferenceConcurrency` 或任何进程级 GPU 信号量。
- 不实现在线优先 GPU 队列、推理抢占或在线/离线加权调度。
- 不限制不同 HTTP 请求之间的模型推理并发。
- 不调整 `MaxConcurrentOfflineBatches`、`MaxConcurrentOnlineRequests` 或 `MaxQueueOnlineSize`。
- 不修改模型文件、检测阈值、输入尺寸、接口路径或响应结构。
- 不在本变更中决定最终 FP16 生产组合。

## 技术决策

### 1. 使用一个独立的 `[Inference]` 配置段

`vbas/config.toml` 和部署使用的 `vbas.gpu.toml` 增加以下配置，字段旁保留中文注释：

```toml
[Inference]
StudentModelsSequential = true
SyncTasks2PolygonsSequential = true
PersonUseHalf = false
FaceUseHalf = false
StudentUseHalf = false
TeacherUseHalf = false
```

配置加载继续遵循项目根 `config.toml` 和 `CONFIG_PATH` 规则。缺少 `[Inference]` 或单个字段时使用上述默认值，使旧配置文件仍能启动。选择独立配置段而不是继续使用模块常量，是为了让顺序策略和各模型精度可以独立验证且不污染 `[TIAS]` 容量语义。

### 2. 学生模型顺序开关只控制一个请求内的三个模型

`StudentModelsSequential=true` 时，一个图片依次执行人数、人脸和学生行为模型。顺序路径复用现有三个检测函数和现有结果组装逻辑，不复制后处理代码。

`StudentModelsSequential=false` 时保留当前 `asyncio.to_thread` 与 `asyncio.gather` 兼容路径。无论选择哪条路径，输出字段、对象类型、阈值、图片顺序及失败语义必须一致。

该开关不序列化两个独立 HTTP 请求。这样可以先单独测量请求内部扇出对显存的影响，避免同时引入全局并发调度后无法归因。

### 3. Polygon 顺序开关保持输入顺序

`SyncTasks2PolygonsSequential=true` 时，对 `PolygonList` 逐项 `await process_polygon(...)`，并按输入顺序追加 `PolygonResult`。空 Polygon 列表继续返回现有空结果，不增加隐式整图检测。

`SyncTasks2PolygonsSequential=false` 时保留当前 `asyncio.gather` 路径；`gather` 返回顺序仍与输入顺序一致。文件路径版兼容实现和 Base64 正式实现中所有仍可访问的推理路径必须使用同一配置语义，备份文件不作为运行时代码修改目标。

### 4. 四个模型使用独立精度配置

移除运行时代码对单一 `use_half` 常量的依赖，建立明确映射：

| 配置 | 模型调用 |
| --- | --- |
| `PersonUseHalf` | 所有人数模型 `predict` 调用 |
| `FaceUseHalf` | 所有人脸模型 `predict` 调用 |
| `StudentUseHalf` | 学生行为模型 `predict` 调用 |
| `TeacherUseHalf` | 教师行为模型 `predict` 调用 |

默认值全部为 `false`，因此本次发布默认不改变数值精度。打开某个开关只能影响对应模型，不能把一个模型的配置传给另一个模型。CPU 测试使用默认 `false`；开启 FP16 的真实有效性在支持 FP16 的 NVIDIA 环境逐模型验证。

### 5. 不以 `torch.cuda.empty_cache()` 代替生命周期修复

本阶段不在每次预测后无条件调用 `torch.cuda.empty_cache()`。频繁清空缓存可能降低吞吐，并且不能释放仍被引用的张量。测试必须分别记录进程显存、PyTorch allocated/reserved 和峰值，先确认顺序化效果；如仍存在异常增长，再提出独立的显存生命周期变更。

### 6. 镜像替换采用先构建、后验证、再清理

目标机为 `192.168.29.11`，只重建 VBas 镜像。构建沿用现有 Docker layer/build cache，不执行构建缓存清理。替换前记录三个旧容器的完整 ID、旧镜像完整 ID、当前 Git SHA 和运行配置。

新镜像构建并完成架构、revision 和配置检查后，才逐个重建三个 VBas 容器。每个新容器必须满足：

- Docker health 为 healthy；
- 正确绑定 GPU 0、1、2；
- 向 Control Service 注册并持续心跳；
- 学生、教师和人数接口真实推理成功；
- 顺序开关和四个精度开关的容器内有效值与发布配置一致；
- 三实例路由和返回合同未回归。

若任一门禁失败，保留旧镜像并使用记录的完整镜像 ID回滚。全部门禁通过后，删除被替换的旧容器残留和旧 VBas 镜像；删除必须按完整容器/镜像 ID精确执行，不使用广泛 prune，也不删除其他算子镜像或构建缓存。

## 风险 / 权衡

- [学生接口低负载单请求延迟可能上升] → 同时比较顺序与兼容并行模式的 P50/P95、吞吐和成功率，以稳定性和总成功吞吐判断效果。
- [不同 HTTP 请求仍可能并发导致显存再次上升] → 明确记录为本阶段剩余风险；通过固定负载的显存回归判断是否需要后续 `GpuInferenceConcurrency` 变更。
- [配置映射遗漏某个旧调用点] → 静态搜索所有 `half=` 和 `use_half` 使用点，并用模型替身断言四个配置互不串用。
- [顺序执行改变结果顺序] → 针对多 Polygon 和多图片建立输入/输出顺序断言，并比较现有响应模型。
- [FP16 影响检测精度] → 默认全部关闭；逐模型打开时使用固定图片对比对象数量、类别和关键框坐标，不在本变更中直接启用生产 FP16。
- [替换失败造成服务中断] → 构建期间保留旧容器运行，替换前保存完整资产账本，失败时按旧镜像完整 ID回滚。

## 迁移计划

1. 在本地实现配置模型、顺序分支和四模型精度映射。
2. 完成编译、配置默认值、单元测试、接口合同测试以及本地 CPU Smoke。
3. 提交并推送同一 Git SHA，目标机从该 SHA 构建带日期标签的新 VBas 镜像，保留构建缓存和旧运行容器。
4. 记录旧资产后，逐个替换 GPU0、GPU1、GPU2 的 VBas 容器并执行健康、注册和真实推理检查。
5. 容器重启后先逐模型预热，再用同一图片执行学生、教师、单/多 Polygon 人数以及并发混合回归，记录显存基线、峰值、驻留值、耗时和错误。
6. 全部通过后精确删除旧 VBas 容器残留和旧 VBas 镜像，保留 Docker 构建缓存。
7. 若失败，停止新负载，使用旧镜像完整 ID恢复三个 VBas 实例，并保留失败证据。

## 待确认问题

无。是否增加进程级 `GpuInferenceConcurrency` 将根据本变更部署后的显存压测结果另行决策。
