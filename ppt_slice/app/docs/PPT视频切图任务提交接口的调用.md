# `/LocalVideoPPTSliceTasks/v1.0.0` 接口调用说明

## 1. 协议概述

该接口提交一个 PPT 视频切图任务。算子直接流式读取 `uri`，不落盘远程 MP4；稳定 PPT 图片写入平台共享结果目录，疑似视频播放或持续滚动区间内不发布切片。全部完成后原子发布 `manifest.json`，最后仅发送一次终态元数据回调。协议不包含 Base64 图片，也不逐图回调。

## 2. 请求

```http
POST /LocalVideoPPTSliceTasks/v1.0.0
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `uri` | string | 是 | 算子可访问的视频文件或视频流地址 |
| `task_id` | string | 是 | 平台任务 ID，同时决定共享结果目录 |
| `operator_task_id` | string | 是 | 本次算子执行 ID |
| `result_callback_uri` | string | 是 | 一次性终态回调地址 |
| `threshold` | number | 否 | 页面变化阈值，默认 `0.98`，范围 `0..1` |

请求示例：

```json
{
  "uri": "/data/course/course-001/media/slides.mp4",
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "result_callback_uri": "http://orchestrator-service:18101/internal/ppt-slice/callback/101",
  "threshold": 0.98
}
```

`task_id` 与 `operator_task_id` 只允许字母、数字、点、下划线和连字符，不能为 `.` 或 `..`。请求没有输出路径字段，不能覆盖服务端 `result_root`。

## 3. 受理响应

成功受理：

```json
{
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 50,
  "reason": ""
}
```

并发容量已满或算子执行 ID 重复：

```json
{
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 70,
  "reason": "当前任务数已达到最大值[15]，请稍后重试"
}
```

`50` 表示后台处理中；受理响应不是终态。

## 4. 共享目录与 manifest

图片目录固定为：

```text
{result_root}/{task_id}/ppt/slices
```

manifest 固定为：

```text
{result_root}/{task_id}/ppt/manifest.json
```

切片文件名格式为 `ppt-{序号}-f{frame_seq}-t{snap_time}s.jpg`。`f` 表示采样帧序号，`t...s` 表示视频时间秒数；定位原视频时应使用 `t` 字段。

图片与 manifest 均先写 `.part` 临时文件，再通过同目录原子替换发布。成功 manifest：

```json
{
  "schema_version": 1,
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 60,
  "path": "/data/result/course-001/ppt/slices",
  "manifest_path": "/data/result/course-001/ppt/manifest.json",
  "count": 1,
  "reason": "",
  "images": [
    {
      "frame_seq": 217,
      "snap_time": 216,
      "path": "/data/result/course-001/ppt/slices/ppt-0001-f217-t216s.jpg"
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

`dynamic_segments` 按 `start_ms` 升序且互不重叠，时间范围采用半开区间 `[start_ms,end_ms)`。该字段为空列表表示没有确认的持续动态内容。`count` 仅为图片数，不包含动态区间数。

失败时仍尝试发布 `status=70` 的 manifest，`reason` 记录失败原因，`images` 可包含失败前已原子发布的图片。

## 5. 一次性终态回调

manifest 原子发布后，算子向 `result_callback_uri` POST 一次：

```json
{
  "task_id": "course-001",
  "operator_task_id": "ppt-run-001",
  "status": 60,
  "path": "/data/result/course-001/ppt/slices",
  "manifest_path": "/data/result/course-001/ppt/manifest.json",
  "count": 1,
  "reason": "",
  "dynamic_segments": []
}
```

| 状态 | 含义 |
| --- | --- |
| `60` | 完成 |
| `70` | 失败或取消 |

回调不携带 `snapImage` 或其他 Base64 字段。回调失败只记录日志，不重试，不引入 Kafka 或数据库。

## 6. 并发

`config.toml` 的 `task.max_concurrent_tasks=N` 表示单个 Uvicorn worker 同时保留的任务容量。容量检查与任务登记在同一把进程内锁中完成，所以恰好允许 `N` 个任务，第 `N+1` 个被拒绝。部署命令必须保持 `--workers 1`。

## 7. 配置

```toml
[task]
max_concurrent_tasks = 15

[paths]
result_root = "./shared_results"

[dynamic_detection]
enabled = true
sample_interval_ms = 1000
confirmation_ms = 5000
exit_stable_ms = 3000
merge_gap_ms = 20000
cluster_gap_ms = 90000
cluster_min_segments = 3
```

生产容器通常设置 `RESULT_ROOT=/data/result` 并将平台共享卷挂载到 `/data/result`。`CONFIG_PATH` 可覆盖默认的项目根 `config.toml`。

`merge_gap_ms` 处理普通短暂停顿；只有至少 `cluster_min_segments` 个动态段的相邻间隔均不超过 `cluster_gap_ms` 时，才把长静止镜头合并进连续动态簇。簇尚未决议时，候选切片只在内存中延迟发布。服务直接从 URL 解码，不下载或落盘源 MP4、完整副本和视频预览。

关闭 `dynamic_detection.enabled` 后恢复旧切片判断流程。其他动态参数详见项目根 `config.toml` 注释。
