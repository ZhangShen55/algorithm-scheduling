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

当前新任务 DAG 固定为 `PPT_SLICE -> PPT_OCR` 和 `ASR_TRANSCRIPTION`。新任务不创建
`PPT_KEYWORDS`、`COURSE_OVERVIEW` 或占位结果；历史任务中的这些节点仍由查询层原样返回。

普通节点先申请实例租约，再领取节点并绑定任务、节点和追踪上下文；一次真实 HTTP 调用只占
一个租约。同步调用使用有限硬超时，超过单次租约 TTL 时续租同一个租约，完成、失败、超时或
取消后释放。容量暂时不足时节点进入等待状态，由后续调度重试，不会把内部 `503` 直接作为课程
终态返回 A 服务。

`PPT_OCR` 是工作项型节点：协调节点不占用外层算子租约，每个 `ppt_image_id` 独立申请 OCR
租约，持久化单图结果后释放。幂等单图调用仅对瞬时 `NetworkError`/`RemoteProtocolError`
执行 `[ppt]` 中配置的有限重试，默认总尝试 2 次、间隔 0.2 秒；OCR 业务错误、HTTP 超时和
未知错误不重试，最终节点原因至少保留异常类型。全部 OCR 工作项完成后 PPT 任务直接进入终态。

PPT Slice 是异步长任务，从算子受理到 manifest 终态持久化持续续租，终态事务完成后才释放。
确定性 `operator_task_id` 的提交仅对瞬时 `NetworkError`/`RemoteProtocolError` 执行 `[ppt]`
配置的有限重试，默认总尝试 2 次、间隔 0.2 秒；同一租约和实例不变。超时、HTTP、业务和
未知错误不重试。PPT 算子把相同在途请求作为幂等重复返回，避免响应丢失后启动第二个后台任务。

ASR 只调用离线转写算子并保存完整 v1.1.8 结果和 `effective_params`，转写完成后 ASR 任务
直接进入终态。Orchestrator 不注册、租赁或调用 Text Analysis。

Outbox Publisher、Kafka Consumer 和节点执行循环已经接入应用生命周期；`/ops/readiness`
同时报告这些后台组件的状态。健康接口只说明进程存活，不能替代 readiness、真实基础设施和
算子契约验证。
# 日志

运行日志默认写入 `logs/{instance_id}/application.log`，同时输出到 stdout；单文件上限
100 MiB，归档保留 7 日。日志只保留 Outbox、Kafka、节点和算子实例上下文，不记录媒体
URL 凭据或完整 ASR/OCR 结果。
