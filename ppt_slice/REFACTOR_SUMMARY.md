# PPT Slice 项目重构与演进总结

## 文档定位

本文记录 `ppt_slice` 从旧脚本式实现迁移为 FastAPI 算子，以及后续接入算法调度平台、共享结果目录、持续动态区间检测和项目级 Harness 的主要演进。

- 当前使用方式、接口示例和完整配置以 `README.md` 为准。
- 长期工程约束和兼容边界以 `AGENTS.md` 为准。
- 动态检测需求、设计和任务状态以 `openspec/changes/detect-ppt-video-playback-segments/` 为准。
- 每轮算法验证、失败证据和真实语料结果以 `harness/` 为准。

本文不替代上述文件，也不记录每一次阈值调整的完整流水。

## 第一阶段：FastAPI 项目结构重构

### 时间

2026-04-17

### 主要目标

旧实现以脚本和 `extract_ppt/` 目录为主。第一阶段将其整理为可独立部署的 FastAPI 算子，建立明确的 API、配置、模型、服务和日志边界。

### 完成内容

- 应用入口统一为 `app.main:app`。
- API 路由、请求模型、任务模型、任务管理、图像比较和视频处理分层。
- 使用 `pydantic-settings` 管理类型化配置和环境变量覆盖。
- 使用轮转日志文件，并保留控制台输出。
- 固定依赖版本，使用 PyAV 读取视频。
- 提供健康检查、版本查询、任务提交和取消接口。

这一阶段奠定了当前算子的基础结构。旧 `extract_ppt/` 代码不再属于运行时实现。

## 第二阶段：调度平台内部算子契约

### 时间

2026-08-06

### 部署边界

- 服务入口：`app.main:app`
- 默认端口：`9001`
- Conda 环境：`ppt_slice`
- Uvicorn Worker：`1`
- 算子内部并发：由 `task.max_concurrent_tasks` 控制，默认支持 15 个任务
- 正式启动命令：

```bash
conda run -n ppt_slice python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 9001 \
  --workers 1
```

旧文档曾出现的 `9002` 只是临时验证端口，不是正式默认端口。项目也不再依赖旧的 `start.sh` 或特定当前工作目录。

### 接口兼容边界

- 保留 `POST /LocalVideoPPTSliceTasks/v1.0.0`。
- 规范视频输入字段为 `video_path`，支持远程 URL 和绝对本地路径；旧 `uri` 只作为兼容输入。
- 保留任务取消和版本查询路径。
- 内部请求统一使用 snake_case 字段。
- PPT 算子只接受视频 URL 或平台可访问的视频路径，不自行承担课程任务编排。
- `task_id` 和 `operator_task_id` 必须经过路径安全校验。
- 一个任务只发送一次终态元数据回调，不逐图回调，不在回调中传递 Base64 图片。

远程 URL 由 PyAV 直接流式解码，不保存源 MP4。本地绝对路径原地读取，不复制或删除源文件；相对路径因依赖运行目录而被拒绝。

### 共享结果契约

算子将长期保留的 PPT 图片和 manifest 写入平台共享结果目录：

```text
{result_root}/{task_id}/ppt/
├── manifest.json
└── slices/
    ├── ppt-0001-f4-t3s.jpg
    └── ppt-0002-f436-t440s.jpg
```

- JPEG 和 `manifest.json` 都通过同目录 `.part` 文件加原子替换发布。
- `count` 只统计最终发布的 PPT 图片。
- `f` 表示采样帧序号。
- `t...s` 表示视频时间秒数，人工回看视频时以该字段定位。
- `result_root` 默认是项目根目录下的 `shared_results/`，生产环境通常映射为 `/data/result`。

## 第三阶段：持续动态区间检测

### 时间

2026-08-07 至 2026-08-10

### 问题背景

旧算法只比较前后帧像素变化和上一张已保存图片。它可以识别正常 PPT 换页，但在 P 视频播放影片、连续滚动或持续变化画面时会生成大量无业务价值的切片。

本阶段在保留稳定 PPT 像素去重逻辑的基础上，增加跨时间窗口的持续动态检测。

### 当前技术方案

1. 使用 PyAV 直接从 URL 流式解码，不下载或落盘源 MP4。
2. 按配置的时间间隔采样参考帧，默认间隔为 1000ms。
3. 使用 `cv2.absdiff` 计算相邻采样帧像素绝对差。
4. 统计全局变化像素比例和 `4 x 4` 网格活动比例，过滤鼠标等局部小变化。
5. 使用 `STABLE`、`DYNAMIC_CANDIDATE`、`DYNAMIC`、`STABILIZING` 四态状态机区分短暂换页和持续动态。
6. 使用短间隙合并和重复动态簇处理视频中的短暂停顿与部分长静止镜头。
7. 使用 Farneback 稠密光流延续由强像素/网格活动创建的候选或已确认区间。光流不能从稳定状态独立创建区间。
8. 候选 PPT 图片先在内存中延迟发布。如果候选所在范围随后被确认为动态区间，则直接丢弃，不产生最终 JPEG 或残留 `.part`。
9. 动态结束且页面重新稳定后，恢复原有 PPT 去重和切片发布。

### 动态结果契约

`manifest.json` 和终态回调增加 `dynamic_segments`：

```json
{
  "dynamic_segments": [
    {
      "type": "SUSPECTED_VIDEO_PLAYBACK",
      "start_ms": 2372747,
      "end_ms": 2639249,
      "confidence": 0.91,
      "reason": "sustained_visual_change"
    }
  ]
}
```

- `type` 当前只有 `SUSPECTED_VIDEO_PLAYBACK`，统一表示疑似视频播放或持续滚动画面。
- `reason` 当前实现值为 `sustained_visual_change` 或 `repeated_dynamic_cluster`。
- 区间采用视频时间轴上的半开形式 `[start_ms,end_ms)`。
- 区间按开始时间排序、互不重叠。
- 没有检测到持续动态内容时返回空列表。
- `dynamic_segments` 不改变 `count` 的图片计数语义。

### 黑屏过滤

启动或转场黑屏如果持续超过稳定确认时间，可能被旧发布路径当作首张 PPT。本阶段在新旧切片发布路径增加统一的候选过滤：

- 灰度均值不超过 `5`；
- 灰度大于 `20` 的像素比例不超过 `0.1%`。

同时满足两个条件时才判定为空黑屏。过滤只影响切片发布，不改变动态状态机观测。黑底但存在可见文字或图形的正常课件仍然允许发布。

## 当前项目结构

```text
ppt_slice/
├── app/
│   ├── api/v1/video.py
│   ├── core/
│   │   ├── config.py
│   │   └── logger.py
│   ├── docs/
│   ├── models/task.py
│   ├── schemas/__init__.py
│   ├── services/
│   │   ├── dynamic_detection.py
│   │   ├── image_compare.py
│   │   ├── shared_result.py
│   │   ├── slice_pipeline.py
│   │   ├── task_manager.py
│   │   └── video_processor.py
│   ├── utils/
│   │   ├── helpers.py
│   │   └── uri.py
│   └── main.py
├── harness/
│   ├── reports/
│   ├── scenarios/
│   ├── tools/
│   ├── architecture-review.md
│   ├── change-ledger.md
│   └── verification.md
├── openspec/changes/detect-ppt-video-playback-segments/
├── tests/
├── AGENTS.md
├── README.md
├── config.toml
└── requirements.txt
```

## 配置管理

当前配置优先级为：

1. 显式环境变量
2. `config.toml`
3. 代码默认值

`config.toml` 是唯一的文件配置源，服务不读取 `.env`。默认配置文件位于项目根目录，可用 `CONFIG_PATH` 指定其他 TOML 文件。共享结果根目录可用 `RESULT_ROOT` 覆盖。

配置分为：

- `[app]`：应用名称和版本；监听地址与端口由 Uvicorn 启动参数控制。
- `[task]`：最大并发任务数、帧队列容量等。
- `[similarity]`：稳定页面和已保存页面的像素相似度阈值。
- `[dynamic_detection]`：采样、像素活动、网格活动、状态机、区间合并、动态簇、光流和候选稳定参数。
- `[paths]`：共享结果根目录。
- `[logging]`：日志级别、路径、格式、轮转大小和备份数量。

所有字段及环境变量映射以 `README.md` 和 `config.toml` 注释为准。

## Harness 与 OpenSpec

### OpenSpec

变更 `detect-ppt-video-playback-segments` 记录持续动态检测和全量语料 Harness 的需求、设计、规范及任务。

当前任务进度为 `40/46`。尚未完成的工作主要包括完整校准集、保留验证集、最终全量端到端复跑和已知低运动漏报的处理，因此不能归档该变更或声明完整语料验收通过。

### Harness

`harness/` 负责：

- 递归发现和冻结课程 P 视频清单。
- 直接从远程 URL 流式运行检测。
- 生成静态证据帧和联系表。
- 分离保存算法原始结果和人工/AI 复核结论。
- 统计候选、疑似漏报、边界误差、处理覆盖率和性能。
- 保存成功与失败轮次，禁止覆盖失败证据。

源 MP4、视频副本、MP4/GIF 预览、静态证据图片、联系表和本机 `test/` 结果不提交 Git。仓库只保留代码、OpenSpec、Markdown 记录及允许提交的 JSON/CSV 摘要。

## 当前验证状态

### 自动验证

2026-08-10 的最新完整本地验证结果：

- `python -m compileall -q app harness tests` 通过。
- `from app.main import app` 导入成功。
- 完整 `unittest` 共 99 项通过。
- `/health` 返回健康状态。
- `/LocalVideoPPTSliceTasks/v1.0.0/getVersion` 返回版本信息。
- OpenSpec 严格校验通过。
- 动态检测关闭时的旧流程兼容测试通过。
- 内存真实编解码测试确认不落盘 MP4。

### 真实课程定向验证

- `0912空中交通管理与签派`：黑屏过滤前后均生成 31 张切片，图片时间戳、JPEG 内容和 3 个动态区间一致，未发现黑屏切片。
- `计算思维与程序实践II`：过滤前 15 张切片，其中首张为纯黑启动画面；过滤后保留 14 张有效切片。
- 两次验证都直接从 URL 流式解码，没有保存源 MP4、视频副本或视频预览。

上述结果证明本地回归和两个真实课程定向场景符合当前预期，不代表全部课程已经无误报或漏报。

## 已知限制

- 低运动、低帧率或长时间重复同一帧的视频仍可能被拆断或漏报。
- 单纯扩大动态簇间隔会把正常 PPT 错误并入动态区间，已有真实失败证据，因此不能采用无限扩大时间窗口的方式处理。
- 音轨包含课堂声音，简单音量变化不能作为播放视频真值。
- 当前像素差、网格活动和光流不具备完整画面语义识别能力。
- 后续要解决已知漏报，需要可靠的上游播放区间元数据，或引入 `PPT_SLIDE`、`VIDEO_PLAYER`、`DESKTOP_OTHER` 画面语义分类。

## 当前职责边界

- `ppt_slice` 负责视频解码、稳定 PPT 切片、动态区间检测、共享结果落盘和一次终态回调。
- 调度、任务持久化、DAG 推进、重试策略和跨实例路由由算法调度平台负责。
- 不在 PPT 算子内部引入 Redis、Kafka 或课程级数据库任务队列。
- 算子保持单 Uvicorn Worker，通过内部任务容量支持并发处理。

## 后续工作

1. 选择低运动和重复帧视频的架构方案：上游元数据或画面语义分类。
2. 完成校准集候选及漏报复核。
3. 冻结算法和配置后揭示保留验证集。
4. 对最终冻结语料执行不依赖历史缓存的全量端到端复跑。
5. 在满足门槛后完成 OpenSpec 任务并归档变更。
