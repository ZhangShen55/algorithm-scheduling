# Video PPT Slice Extraction Service

从视频流中检测稳定 PPT 页面和疑似视频播放/持续滚动区间，将稳定截图写入平台共享结果目录，并在任务结束时发送一次终态元数据回调。远程课程 MP4 直接通过 URL 流式解码，不下载或落盘视频文件。

## 启动

```bash
conda activate ppt_slice
python -m uvicorn app.main:app --host 0.0.0.0 --port 9001 --workers 1
```

配置文件默认为项目根目录 `config.toml`，可用 `CONFIG_PATH` 指定其他文件。共享输出根目录由 `[paths].result_root` 或环境变量 `RESULT_ROOT` 配置，请保证算子和平台回调接收方挂载的是同一个目录。

## 算法实现

### 旧版与当前版的区别

| 项目 | 旧版 | 当前版（开启 `dynamic_detection.enabled`） |
| --- | --- | --- |
| 解码帧 | 只解码关键帧 | 解码参考帧，再按默认 `1000ms` 时间间隔采样 |
| PPT 切片依据 | 相邻帧与上一张已保存图片的像素绝对差相似度 | 保留旧像素相似度用于稳定 PPT 发布，并增加持续动态检测 |
| 动态特征 | 无 | 前后采样帧像素绝对差、全局变化比例、`4×4` 网格活动比例 |
| 低运动延续 | 无 | Farneback 稠密光流，只能延续候选或已确认动态段 |
| 时间判断 | 单次帧比较 | `STABLE`、`DYNAMIC_CANDIDATE`、`DYNAMIC`、`STABILIZING` 四态状态机 |
| 跨静止片段 | 无 | `20s` 短间隙合并；至少 3 段强动态证据时允许以 `90s` 间隙形成连续动态簇 |
| 切片发布 | 满足相似度后立即写 JPEG | 候选先在内存延迟，确认属于动态区间后丢弃；恢复稳定后再发布 PPT |
| 动态结果 | 无 | 返回 `dynamic_segments`，并抑制区间内无业务价值的切片 |

当前版本仍然使用前后帧像素变化，但它不再是唯一依据。默认检测过程如下：

1. PyAV 直接从 URL 流式解码，不保存源 MP4；开启动态检测时保留参考帧，并按 `sample_interval_ms=1000` 限频。
2. 对相邻采样帧执行 `cv2.absdiff`，转灰度后统计绝对差不小于 `30` 的像素比例。
3. 将变化掩码划分为 `4×4` 网格；全局变化像素比例至少 `0.04` 且活动网格比例至少 `0.18` 时，形成可创建候选的强活动信号。网格限制用于过滤鼠标指针等局部小变化。
4. 可选 Farneback 稠密光流在宽度 `320px` 的缩小图上计算运动；运动幅度至少 `0.5` 的像素覆盖比例达到 `0.05` 时形成弱运动信号。光流不能从稳定状态单独创建区间，只能确认由强活动启动的候选或延续已确认区间。
5. 四态状态机在默认 `8s` 窗口内要求活动观测比例达到 `0.70`，并持续至少 `5s` 后确认动态区间。普通换页或短动画若在确认前恢复稳定，不会生成动态区间。
6. 动态内容连续稳定 `3s` 后退出；若最后只由弱光流保活，则最多使用 `15s` 运动宽限。相邻区间间隔不超过 `20s` 时合并。
7. 至少 3 段重复强动态证据、相邻间隔均不超过 `90s` 时形成连续动态簇，并抑制簇内静止镜头产生的错误 PPT 切片。
8. 稳定 PPT 仍使用旧版像素绝对差相似度：连续画面达到稳定阈值，且与上一张保存页差异足够大，再持续稳定默认 `2s` 后发布一张代表图。
9. 发布候选前过滤纯黑或近纯黑空画面：灰度均值不超过 `5`，且灰度大于 `20` 的像素比例不超过 `0.1%` 时不生成切片。该保守规则允许黑底但存在可见文字或图形的正常课件继续发布，并且不改变动态状态机的观测。

### 动态区间类型

对外 `dynamic_segments[].type` 目前只有一种：

```text
SUSPECTED_VIDEO_PLAYBACK
```

该类型统一表示“疑似视频播放或持续滚动画面”，当前算法不对二者做语义分类。Harness 中的 `CONFIRMED_VIDEO`、`CONFIRMED_SCROLL`、`FALSE_POSITIVE` 和 `UNCERTAIN` 只是离线人工/AI 复核标签，不属于算子响应字段。

`reason` 不是新的类型，目前可能为：

- `sustained_visual_change`：由持续像素/网格活动及其光流延续确认；
- `repeated_dynamic_cluster`：由多段重复动态证据合并形成连续动态簇。

### 已知限制

像素差、网格活动和光流仍无法可靠识别长时间重复同一帧或运动极弱的视频。继续无条件放大时间聚簇会吞掉正常 PPT，因此当前版本不这样处理。要解决该类漏报，需要增加 `PPT_SLIDE / VIDEO_PLAYER / DESKTOP_OTHER` 画面语义分类，或者由上游提供可靠播放区间。在完整校准集、保留集和最终全量复跑完成前，不能声明对全部课程无已知误报或漏报。

## 提交任务

接口路径保持不变，内部协议统一使用 snake_case：

```http
POST /LocalVideoPPTSliceTasks/v1.0.0
Content-Type: application/json
```

```json
{
  "uri": "/data/course/course-001/media/slides.mp4",
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "result_callback_uri": "http://orchestrator-service:18101/internal/ppt-slice/callback/101",
  "threshold": 0.98
}
```

受理成功立即返回：

```json
{
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 50,
  "reason": ""
}
```

当单 worker 中已有 `max_concurrent_tasks` 个任务时返回 `status=70`，不会启动后台处理。

## 共享结果

请求不能指定输出目录。任务输出固定为：

```text
{result_root}/{task_id}/ppt/
├── manifest.json
└── slices/
    ├── ppt-0001-f1-t0s.jpg
    └── ppt-0002-f18-t17s.jpg
```

文件名中的 `f` 表示采样帧序号，`t...s` 表示视频时间秒数；用于视频定位时以 `t` 字段为准。

每张 JPEG 先写同名 `.part` 文件，再通过原子替换发布。所有图片完成后，`manifest.json` 同样先写 `manifest.json.part`，再原子替换为终态文件。

成功 manifest 示例：

```json
{
  "schema_version": 1,
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 60,
  "path": "/data/result/course-001/ppt/slices",
  "manifest_path": "/data/result/course-001/ppt/manifest.json",
  "count": 2,
  "reason": "",
  "images": [
    {
      "frame_seq": 1,
      "snap_time": 0,
      "path": "/data/result/course-001/ppt/slices/ppt-0001-f1-t0s.jpg"
    }
  ],
  "dynamic_segments": [
    {
      "type": "SUSPECTED_VIDEO_PLAYBACK",
      "start_ms": 2368000,
      "end_ms": 2627000,
      "confidence": 0.91,
      "reason": "sustained_visual_change"
    }
  ]
}
```

`dynamic_segments` 使用视频时间轴上的半开毫秒区间 `[start_ms,end_ms)`。已确认区间内的候选图片不会发布；`count` 仍只统计 `images` 中的 PPT 图片。没有检测到持续动态内容时返回空列表。

`task_id` 和 `operator_task_id` 只允许字母、数字、点、下划线和连字符，且不能为 `.` 或 `..`。算子也会拒绝将已有符号链接用作任务输出目录。

## 终态回调

所有图片和 manifest 发布后，算子只向 `result_callback_uri` POST 一次终态元数据。回调不含 Base64，不逐图回调，也不重试；失败会记录错误日志。

```json
{
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 60,
  "path": "/data/result/course-001/ppt/slices",
  "manifest_path": "/data/result/course-001/ppt/manifest.json",
  "count": 2,
  "reason": "",
  "dynamic_segments": []
}
```

终态状态：`60` 表示完成，`70` 表示失败。

本地回调夹具：

```bash
python callback.py
```

## 配置

| TOML | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `task.max_concurrent_tasks` | `MAX_CONCURRENT_TASKS` | `15` | 单 Uvicorn worker 最大并发任务数 |
| `task.max_queue_size` | `MAX_QUEUE_SIZE` | `25` | 单任务帧队列容量 |
| `paths.result_root` | `RESULT_ROOT` | `./shared_results` | 共享结果根目录 |
| `dynamic_detection.enabled` | `DYNAMIC_DETECTION_ENABLED` | `true` | 启用持续动态区间检测；关闭后恢复旧切片判断 |
| `dynamic_detection.sample_interval_ms` | `DYNAMIC_SAMPLE_INTERVAL_MS` | `1000` | 动态检测参考帧进入流水线的最小时间间隔（毫秒） |
| `dynamic_detection.pixel_difference_threshold` | `DYNAMIC_PIXEL_DIFFERENCE_THRESHOLD` | `30` | 灰度像素绝对差达到该值时计为变化像素 |
| `dynamic_detection.changed_pixel_ratio` | `DYNAMIC_CHANGED_PIXEL_RATIO` | `0.04` | 全局变化像素比例阈值，也用于判断单个网格是否活动 |
| `dynamic_detection.grid_rows` | `DYNAMIC_GRID_ROWS` | `4` | 活动网格行数 |
| `dynamic_detection.grid_columns` | `DYNAMIC_GRID_COLUMNS` | `4` | 活动网格列数 |
| `dynamic_detection.active_grid_ratio` | `DYNAMIC_ACTIVE_GRID_RATIO` | `0.18` | 活动网格占全部网格的比例阈值 |
| `dynamic_detection.window_ms` | `DYNAMIC_WINDOW_MS` | `8000` | 动态活动观测滚动窗口（毫秒） |
| `dynamic_detection.confirmation_ms` | `DYNAMIC_CONFIRMATION_MS` | `5000` | 动态候选确认时间（毫秒） |
| `dynamic_detection.required_active_ratio` | `DYNAMIC_REQUIRED_ACTIVE_RATIO` | `0.70` | 滚动窗口内活动观测比例阈值 |
| `dynamic_detection.exit_stable_ms` | `DYNAMIC_EXIT_STABLE_MS` | `3000` | 连续稳定退出时间（毫秒） |
| `dynamic_detection.merge_gap_ms` | `DYNAMIC_MERGE_GAP_MS` | `20000` | 相邻动态区间合并间隙（毫秒） |
| `dynamic_detection.cluster_gap_ms` | `DYNAMIC_CLUSTER_GAP_MS` | `90000` | 重复动态段形成连续动态簇时允许跨越的长静止间隔（毫秒） |
| `dynamic_detection.cluster_min_segments` | `DYNAMIC_CLUSTER_MIN_SEGMENTS` | `3` | 连续动态簇所需最少动态段数，不能小于 3 |
| `dynamic_detection.optical_flow_enabled` | `DYNAMIC_OPTICAL_FLOW_ENABLED` | `true` | 光流可延续强活动已创建的候选并保活已确认动态段；不能从稳定状态单独起段 |
| `dynamic_detection.optical_flow_width` | `DYNAMIC_OPTICAL_FLOW_WIDTH` | `320` | 光流计算的缩放宽度（像素） |
| `dynamic_detection.optical_flow_magnitude_threshold` | `DYNAMIC_OPTICAL_FLOW_MAGNITUDE_THRESHOLD` | `0.5` | 认定像素运动的光流位移幅度阈值 |
| `dynamic_detection.optical_flow_active_ratio` | `DYNAMIC_OPTICAL_FLOW_ACTIVE_RATIO` | `0.05` | 认定弱光流活动的运动像素覆盖比例 |
| `dynamic_detection.motion_grace_ms` | `DYNAMIC_MOTION_GRACE_MS` | `15000` | 最后由弱光流保活时的无运动宽限；最后为强活动时仍按 `exit_stable_ms` 退出 |
| `dynamic_detection.candidate_stable_ms` | `DYNAMIC_CANDIDATE_STABLE_MS` | `2000` | 普通 PPT 候选页面需要保持稳定的时间（毫秒） |

保持 `--workers 1`。容量锁只负责单 worker 内的 `N` 个并发任务，不设计跨进程协调、Kafka 或数据库依赖。

## Harness

`harness/` 用于递归发现并冻结课程 P 视频清单、直接流式运行全量检测、生成静态证据、维护独立复核结论和输出 JSON/CSV/中文 Markdown 报告。课程 MP4、完整视频副本、MP4/GIF 预览均不得落盘；只允许在 Git 忽略目录保存少量静态证据帧、联系表和压缩特征。`test/` 下的真实课程切片与 manifest 只供本机人工复核，同样由 Git 忽略。连续动态簇尚未决议时，候选切片只以 JPEG 字节保留在内存，不产生临时视频文件。

```bash
conda run -n ppt_slice python -m harness.tools.corpus \
  --root-url 'http://192.168.29.12:5555/course/' \
  --run-id 'RUN_ID' \
  --known-calibration-url 'KNOWN_TRUTH_PPT_URL' \
  --output 'harness/reports/RUN_ID/inventory.json'
```

## 验证

```bash
conda run -n ppt_slice python -m compileall -q app
conda run -n ppt_slice python -c "from app.main import app; print(app.title)"
conda run -n ppt_slice python -m unittest discover -s tests -v
```

健康与版本路由：

```bash
curl http://127.0.0.1:9001/health
curl http://127.0.0.1:9001/LocalVideoPPTSliceTasks/v1.0.0/getVersion
```

## Docker

```bash
docker build -t ppt-slice .
docker run --rm -p 9001:9001 \
  -v /host/result:/data/result \
  ppt-slice
```
