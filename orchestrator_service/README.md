# orchestrator-service

离线课程 DAG、Outbox 发布、节点执行和视觉编排边界服务。本服务直接连接
PostgreSQL 和 Kafka，通过 control-service 申请算子容量，不直接访问 Redis。

## 本地启动

在本服务目录安装公共平台包和服务依赖：

```bash
python -m pip install -e ../algorithm-scheduling-platform
python -m pip install -r requirements.txt
```

```bash
python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 18101 --workers 1
```

默认读取服务根目录的 `config.toml`。`CONFIG_PATH` 可指定其他文件，
`ORCHESTRATOR_` 前缀的环境变量可覆盖 TOML，例如
`ORCHESTRATOR_KAFKA__BOOTSTRAP_SERVERS='["kafka:29092"]'`。

Docker 构建上下文必须是工作区根目录：

```bash
docker build -f orchestrator_service/docker/Dockerfile -t orchestrator-service .
```

当前 `app.main` 只完成 FastAPI 和配置装配，Kafka Consumer、Outbox Publisher 和节点执行循环
尚未接入 lifespan，不应将现有健康接口视为 Worker 已可运行的证据。
