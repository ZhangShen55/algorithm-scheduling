# Text Analysis API

这是基于你给的代码整理出的 **模块化 FastAPI 项目结构**。核心思路：
- `main.py` 只做应用装配（中间件、路由挂载、健康检查）。
- 每个接口独立成路由文件（`app/api/v1/routes/*`）。
- 大模型客户端、Prompt 加载、配置集中管理在 `services/` 与 `core/`。
- 你原本的 `models.py`、`schemas.py`、`utils.py` 已迁移到 `app/models/*` 与 `app/utils/*`，并统一成 `app.` 前缀导入。

## 清除缓存
- `find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null`
- `find . -name "*.pyc" -delete 2>/dev/null`
- `find . -name "._*" -delete 2>/dev/null`



## 快速启动
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 配置
默认读取根目录 `config.toml`，也可以用 `CONFIG_PATH=/path/to/config.toml` 指定配置文件。

### 平台注册与运行配置

本地根配置默认不注册；受控 CPU 部署使用
`algorithm-scheduling-platform/deploy/config/operators/text_analysis.cpu.toml`：

| 字段 | 本地根配置 | 受控部署 | 说明 |
| --- | --- | --- | --- |
| `platform.registration_enabled` | `false` | `true` | 是否主动注册到调度平台 |
| `platform.control_service_url` | `""` | `http://control-service:18100` | 注册与心跳地址 |
| `platform.heartbeat_interval_seconds` | `5` | `5` | 心跳间隔 |
| `platform.max_concurrent_requests` | `256` | `256` | 脑图与关键词接口共享容量 |
| `runtime.require_gpu` | `false` | `false` | 文本分析受控部署不要求 CUDA |

Compose 继续管理实例 ID、服务 URL、注册 Token、`CONFIG_PATH`、端口和
`UVICORN_WORKERS=1`；CPU 实例不设置 GPU 字段。平台只注册 `/v1/course_overviews` 和
`/v1/extract_keywords` 两项能力，二者共享 `256`。各接口内部的 LLM 分片、并发、重试和
`mt_max_concurrency` 仍是本地处理约束，不派生或拆分平台注册容量。

## 不走代理
```bash
NO_PROXY=10.80.5.130,localhost,127.0.0.1 \
no_proxy=10.80.5.130,localhost,127.0.0.1 \
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 学生互动分析

学生互动分析接口为 `POST /v1/student_interaction_analysis`。调用方应先请求 `/v1/course_time_analysis`，再把同一份 `textSegments`、`course_start.time`、`course_end.time` 和 `breaks` 传入学生互动分析接口。

请求示例：

```json
{
  "textSegments": [],
  "course_start": 159.8,
  "course_end": 8830.0,
  "breaks": [
    {"start": 3794.47, "end": 5867.0}
  ]
}
```

响应 `result.interactions` 是数组。无可确认师生互动或学生之间交流时返回：

```json
{"interactions": []}
```
