# control-service

课程任务接入、状态查询、算子注册和容量管理服务。本服务直接连接 PostgreSQL 和 Redis，不直接连接 Kafka。

## 本地启动

```bash
python -m uvicorn services.control_service.app.main:app \
  --host 0.0.0.0 --port 18100 --workers 1
```

默认读取服务根目录的 `config.toml`。`CONFIG_PATH` 可指定其他文件，`CONTROL_`
前缀的环境变量可覆盖 TOML，嵌套字段使用双下划线，例如
`CONTROL_POSTGRES__DSN` 和 `CONTROL_SERVICE__PORT`。

Docker 构建上下文必须是 `algorithm-scheduling-platform/` 根目录：

```bash
docker build -f services/control_service/docker/Dockerfile -t control-service .
```

