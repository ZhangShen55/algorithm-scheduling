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

## 租约和离线等待

普通节点先申请实例租约，再领取节点并绑定任务、节点和追踪上下文；一次真实 HTTP 调用只占
一个租约。同步调用使用有限硬超时，超过单次租约 TTL 时续租同一个租约，完成、失败、超时或
取消后释放。容量暂时不足时节点进入等待状态，由后续调度重试，不会把内部 `503` 直接作为课程
终态返回 A 服务。

`PPT_OCR` 和 `PPT_KEYWORDS` 是工作项型节点：协调节点不占用外层算子租约，每个
`ppt_image_id` 独立申请 OCR 或关键词租约，持久化单图结果后释放。PPT Slice 是异步长任务，
从算子受理到 manifest 终态持久化持续续租，终态事务完成后才释放。

Outbox Publisher、Kafka Consumer 和节点执行循环已经接入应用生命周期；`/ops/readiness`
同时报告这些后台组件的状态。健康接口只说明进程存活，不能替代 readiness、真实基础设施和
算子契约验证。
# 日志

运行日志默认写入 `logs/{instance_id}/application.log`，同时输出到 stdout；单文件上限
100 MiB，归档保留 7 日。日志只保留 Outbox、Kafka、节点和算子实例上下文，不记录媒体
URL 凭据或完整 ASR/OCR 结果。
