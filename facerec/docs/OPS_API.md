# 运维接口

## GET /ops/health

该接口始终以 HTTP 200 返回进程健康信息，顶层 `status` 为 `healthy` 或 `degraded`。

`components` 包含：

- `database`：MongoDB ping 和读取延迟。
- `arcface`：embedding 模型 readiness 与配置设备。
- `dlib_workers`：兼容字段，表示当前所选 detector worker 是否全部 ready。
- `detector`：实际 detector 类型、配置设备和各 worker provider 状态。
- `storage`：容器文件系统容量。

只有 MongoDB、ArcFace 和全部所选 detector worker ready 时，平台注册状态才会报告
`model_ready=true`。Redis 不属于 FaceRec readiness。

## GET /ops/metadata

由共享 operator registry client 提供，固定包含：

```json
{
  "instance_id": "facerec-gpu0",
  "operator_code": "facerec",
  "capabilities": ["recognize"]
}
```

模型/API 版本取自对应平台环境变量。

## GET /ops/status

返回生命周期、`model_ready`、在途请求数和平台注册声明容量。检测 worker 数量由
`threading.max_workers` 控制，不等同于 `declared_capacity`。

## POST /ops/drain

把算子生命周期切换为 draining，拒绝新的非 `/ops/*` 请求，用于受控停止。关闭时会先
撤销 detector readiness，再停止注册心跳并等待 worker 退出。

## GET /ops/metrics

返回 CPU、内存、磁盘和 MongoDB 应用统计。

## GET /ops/stats/api-calls

支持 `start_date`、`end_date`、`endpoint`、`method`、`limit` 和 `offset` 查询 API 调用
统计记录。

## GET /ops/stats/hourly

支持按日期、路径和方法查询小时聚合统计。

## GET /ops/stats/summary

返回请求总数、成功/失败数、成功率、平均耗时、热门路径和小时分布。统计集合的 TTL 由
根配置 `[stats]` 控制。
