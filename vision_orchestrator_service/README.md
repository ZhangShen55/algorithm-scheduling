# Vision Orchestrator Service

本服务负责离线视觉编排策略、VBas 调用、自适应抽帧、行为聚合和证据筛选。

在本服务目录安装公共平台包和服务依赖：

```bash
python -m pip install -e ../algorithm-scheduling-platform
python -m pip install -r requirements.txt
```

The canonical entrypoint is:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --workers 1
```

配置按内置默认值、根目录 `config.toml`、`VISION_` 环境变量的顺序加载。
嵌套字段使用双下划线，`CONFIG_PATH` 可以指定其他 TOML 文件。

`/health` 和 `/ready` 当前只表示进程健康与就绪，不表示 Kafka Consumer 和长期
Worker 循环已经完成。

从工作区根目录构建镜像：

```bash
docker build -f vision_orchestrator_service/docker/Dockerfile \
  -t vision-orchestrator-service .
```
