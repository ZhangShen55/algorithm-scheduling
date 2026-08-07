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
