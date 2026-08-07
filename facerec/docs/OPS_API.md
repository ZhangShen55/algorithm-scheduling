# 运维接口说明（/ops）

## 📌 接口概览

| 方法 | 路径 | 说明 | 备注 |
|------|------|------|------|
| GET | `/ops/health` | 健康检查 | DB + 存储空间 |
| GET | `/ops/metrics` | 系统指标 | CPU/内存/磁盘 + 应用指标 |
| GET | `/ops/stats/api-calls` | API 调用日志 | 支持筛选与分页 |
| GET | `/ops/stats/hourly` | 按小时统计 | 支持筛选 |
| GET | `/ops/stats/summary` | 汇总统计 | 默认近 30 天 |

---

## ✅ GET `/ops/health`

健康检查接口，返回各组件状态。

**响应示例**:
```json
{
  "status": "healthy",
  "timestamp": "2026-01-09T10:12:30.123456",
  "components": {
    "database": {
      "status": "up",
      "latency_ms": 12.3
    },
    "storage": {
      "status": "up",
      "disk_usage_percent": 62.45,
      "disk_free_gb": 120.37
    }
  }
}
```

**说明**:
- `status`: `healthy` 或 `degraded`
- `database` 通过 `db.command("ping")` + 读操作检测
- `storage` 使用 `/` 根目录磁盘统计

---

## ✅ GET `/ops/metrics`

系统指标接口，包含系统资源与应用指标。

**响应示例**:
```json
{
  "system": {
    "cpu_percent": 12.4,
    "memory_percent": 43.1,
    "memory_used_gb": 6.5,
    "memory_total_gb": 15.6,
    "disk_usage_percent": 62.4,
    "disk_free_gb": 120.3
  },
  "application": {
    "total_persons": 1250,
    "total_requests_today": 3480
  }
}
```

**说明**:
- `application.total_persons`: persons 集合文档数
- `application.total_requests_today`: 今日 API 调用量（来自 hourly 聚合）

---

## ✅ GET `/ops/stats/api-calls`

获取 API 调用详细日志。

**查询参数**:
- `start_date`: 开始日期（YYYY-MM-DD）
- `end_date`: 结束日期（YYYY-MM-DD）
- `endpoint`: 精确路径（如 `/persons`）
- `method`: HTTP 方法（GET/POST/DELETE...）
- `limit`: 返回数量，1-1000
- `offset`: 跳过数量

**响应示例**:
```json
[
  {
    "request_id": "b8d5d0c2-0c0c-4c3a-9c4c-5a4b2a0b0e66",
    "timestamp": "2026-01-09T10:12:30.123456",
    "method": "POST",
    "path": "/persons",
    "status_code": 200,
    "duration_ms": 52.31,
    "client_ip": "10.0.0.8",
    "success": true,
    "error_message": null
  }
]
```

---

## ✅ GET `/ops/stats/hourly`

按小时聚合统计数据。

**查询参数**:
- `start_date`: 开始日期（YYYY-MM-DD）
- `end_date`: 结束日期（YYYY-MM-DD）
- `endpoint`: 精确路径（如 `/persons/search`）
- `method`: HTTP 方法（GET/POST/DELETE...）
- `limit`: 返回数量，1-1000

**响应示例**:
```json
[
  {
    "date": "2026-01-09",
    "hour": 10,
    "endpoint": "/persons",
    "method": "POST",
    "total_requests": 120,
    "success_count": 118,
    "error_count": 2,
    "success_rate": 98.33,
    "avg_response_time_ms": 45.6,
    "min_response_time_ms": 12.4,
    "max_response_time_ms": 210.3
  }
]
```

---

## ✅ GET `/ops/stats/summary`

统计汇总接口。

**查询参数**:
- `start_date`: 开始日期（YYYY-MM-DD，默认近 30 天）
- `end_date`: 结束日期（YYYY-MM-DD，默认今天）

**响应示例**:
```json
{
  "total_requests": 25300,
  "total_success": 24780,
  "total_errors": 520,
  "success_rate": 97.95,
  "avg_response_time_ms": 48.2,
  "top_endpoints": [
    {
      "endpoint": "/persons",
      "method": "POST",
      "total_requests": 5200,
      "total_errors": 12
    }
  ],
  "hourly_distribution": [
    {"hour": 9, "total_requests": 1800},
    {"hour": 10, "total_requests": 2100}
  ]
}
```

---

## 📝 注意事项

1. 统计数据依赖 `APIStatsMiddleware`，请确保中间件已启用。
2. 统计数据有 TTL 清理机制，保留天数由 `config.toml` 的 `[stats]` 控制：
   - `retention_days`: 详细日志保留天数
   - `hourly_retention_days`: 按小时聚合保留天数
3. `endpoint` 为精确匹配路径，不支持模糊匹配。

---

## 🔄 更新日志

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-01-09 | 初始版本（与当前代码对齐） |
