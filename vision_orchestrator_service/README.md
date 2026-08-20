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

`/health` 只表示进程存活；`/ready` 会检查视觉命令 Consumer 后台循环，以及按
`[readiness]` 开关启用的 PostgreSQL、Kafka 和 Control Service 依赖。后台循环
退出或必需依赖不可用时返回 HTTP `503`。

服务消费 `algorithm.visual.commands` 中的教师/学生课程级命令。命令只携带
`/data/course/{task_id}` 下的绝对本地视频路径和元数据，不携带媒体字节。服务使用
`ffprobe` 读取时长、`ffmpeg` 抽取 JPEG 帧，再通过 Control Service 租约调用 VBas；
不接入 RTSP，也不重新下载上游视频。抽帧缓存保留在课程临时目录，筛选出的少量证据图
复制到 `/data/result/{task_id}/vision`。`media.max_concurrent_processes` 限制单个服务容器
同时运行的 ffmpeg/ffprobe 进程数，默认 `2`；该字段只保护本地 CPU/内存，不改变扫描点、
VBas 批次或平台注册容量。

教师泳道先按可配置粗粒度扫描，命中板书或坐姿后按
`scan.refinement_intervals_seconds` 逐级加密；教师行为区间和学生人数结果写入现有
PostgreSQL 节点结果。节点完成后发布 `algorithm.visual.events` 终态事件，再提交 Kafka
offset。若数据库已完成但终态事件发布中断，重投命令只会补发终态事件，不重复执行推理。

每个学生或教师图片批次申请一个共享 VBas 租约，并写入课程、批次、流类型和追踪上下文；
教师请求中的可选头部姿态仍属于同一批次，不额外占用容量。同步批次调用设置有限硬超时，
跨越单次 TTL 时续租同一个租约，并在成功、失败、超时或取消后释放。容量不足属于离线等待，
不会把课程节点标记为最终失败。VBas 的学生、教师和头部姿态能力共享同一实例容量池。

容器镜像安装 `ffmpeg/ffprobe`。本地启动前也必须保证这两个命令可执行，并确保
PostgreSQL 迁移、Kafka 视觉主题、Control Service 和至少一个 VBas 实例已就绪。

从工作区根目录构建镜像：

```bash
docker build -f vision_orchestrator_service/docker/Dockerfile \
  -t vision-orchestrator-service .
```
