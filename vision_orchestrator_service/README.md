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

每个学生或教师图片批次申请一个共享 VBas 租约，并写入课程、批次、流类型和追踪上下文；
教师请求中的可选头部姿态仍属于同一批次，不额外占用容量。同步批次调用设置有限硬超时，
跨越单次 TTL 时续租同一个租约，并在成功、失败、超时或取消后释放。容量不足属于离线等待，
不会把课程节点标记为最终失败。VBas 的学生、教师和头部姿态能力共享同一实例容量池。

从工作区根目录构建镜像：

```bash
docker build -f vision_orchestrator_service/docker/Dockerfile \
  -t vision-orchestrator-service .
```
