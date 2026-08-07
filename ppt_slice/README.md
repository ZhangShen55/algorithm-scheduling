# Video PPT Slice Extraction Service

从视频流中检测 PPT 页面变化，将截图写入平台共享结果目录，并在任务结束时发送一次终态元数据回调。

## 启动

```bash
conda activate ppt_slice
python -m uvicorn app.main:app --host 0.0.0.0 --port 9001 --workers 1
```

配置文件默认为项目根目录 `config.toml`，可用 `CONFIG_PATH` 指定其他文件。共享输出根目录由 `[paths].result_root` 或环境变量 `RESULT_ROOT` 配置，请保证算子和平台回调接收方挂载的是同一个目录。

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
    ├── ppt-0001-1-0.jpg
    └── ppt-0002-18-17.jpg
```

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
      "path": "/data/result/course-001/ppt/slices/ppt-0001-1-0.jpg"
    }
  ]
}
```

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
  "reason": ""
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

保持 `--workers 1`。容量锁只负责单 worker 内的 `N` 个并发任务，不设计跨进程协调、Kafka 或数据库依赖。

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
