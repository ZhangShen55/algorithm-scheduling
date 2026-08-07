# 项目结构优化总结

## ✅ 当前状态概览

文档已根据最新代码结构更新，补充了接口定义与运维统计模块说明。

---

## 📊 优化成果

### **1. 目录结构调整**

```
app/
├── ai_models/        # AI模型文件
├── core/             # 核心模块
├── middleware/       # 统计中间件
├── router/           # API 路由
├── services/         # 业务逻辑
├── utils/            # 工具函数
├── tests/            # 测试文件
├── static/           # 前端静态资源
├── media/            # 媒体文件
├── logs/             # 日志
├── scripts/          # 脚本工具
└── tmp/              # 临时/遗留代码
```

### **2. 核心模块补充**

| 文件 | 作用 |
|------|------|
| `core/constants.py` | 统一管理常量 |
| `middleware/api_stats_middleware.py` | API 统计与 TTL 清理 |
| `router/ops.py` | 运维接口（健康检查、统计查询） |
| `services/ops_stats.py` | 统计聚合与查询 |
| `services/face_service.py` | 识别服务层（待接入路由） |
| `app/.gitignore` | 规范忽略日志/模型/媒体 |

---

## 🔌 接口概览（当前代码）

| 模块 | 方法 | 路径 | 说明 | 备注 |
|------|------|------|------|------|
| 识别 | POST | `/recognize` | 单张人脸识别 | `targets` 为编号列表 |
| 识别 | POST | `/recognize/batch` | 多帧识别聚合 | 返回 `frames` + `match` |
| 人物 | POST | `/persons` | 单个人物入库 | 存在则更新 |
| 人物 | POST | `/persons/batch` | 批量人物入库 | 返回逐条结果 |
| 人物 | GET | `/persons` | 获取人物列表 | 支持分页 |
| 人物 | POST | `/persons/search` | 搜索人物 | name 模糊、number 精确 |
| 人物 | DELETE | `/persons/delete` | 通用删除 | `name` 或 `id` |
| 人物 | DELETE | `/persons/by_name` | 模糊删除 | 仅建议开发环境 |
| 人物 | DELETE | `/persons/by_id` | 按 ID 删除 | 最安全 |
| 运维 | GET | `/ops/health` | 健康检查 | DB/存储 | 
| 运维 | GET | `/ops/metrics` | 系统指标 | CPU/内存/磁盘 | 
| 运维 | GET | `/ops/stats/api-calls` | 调用日志 | 支持筛选 |
| 运维 | GET | `/ops/stats/hourly` | 按小时统计 | 支持筛选 |
| 运维 | GET | `/ops/stats/summary` | 汇总统计 | 默认近 7 天 |
| Web | GET | `/` | 管理后台 | Basic Auth |

---

## 🧩 服务层说明

`services/face_service.py` 已封装识别流程，但当前 `/recognize` 仍由 `router/faces.py` 实现。若要切换到服务层，需要对齐 `RecognizeResp` 的返回结构（`match` 列表）。

---

## 🚨 重要提醒

- 模型文件应放在 `app/ai_models/`
- `/recognize` 的 `targets` 仅支持**编号列表**
- `/persons/search` 当前为 **POST** 请求
- `/ops` 接口依赖统计中间件与 `config.toml` 的 `[stats]` 配置

---

## 📋 验证清单

- [ ] 服务正常启动
- [ ] 模型文件正确加载
- [ ] `/recognize`、`/recognize/batch` 正常
- [ ] `/persons` CRUD 正常
- [ ] `/ops/health` 返回健康状态
- [ ] Web 页面正常访问

---

## 📚 相关文档

- [PERSONS_API.md](PERSONS_API.md) - 人物管理接口说明
- [RECOGNIZE_API_ERRORS.md](RECOGNIZE_API_ERRORS.md) - 识别接口错误码说明
- [OPS_API.md](OPS_API.md) - 运维接口说明

---

**更新日期**: 2026-01-09
**版本**: v2.2.0
