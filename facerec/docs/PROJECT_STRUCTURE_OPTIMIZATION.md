# 项目结构优化说明文档

## 📊 优化概述

本次文档更新基于**当前代码结构**，补充了运维统计模块与接口变化说明，确保目录结构与模块职责清晰。

---

## 🔄 优化前后对比

### **优化前的结构问题**

```
app/
├── ms1mv3_arcface_r100.onnx  ❌ 模型文件混在根目录
├── shape_predictor_68_face_landmarks.dat  ❌ 模型文件混在根目录
├── photo_8.jpg               ❌ 临时测试文件
├── test.png                  ❌ 临时测试文件
├── stress_test.py            ❌ 测试脚本混在根目录
├── test_recognize.py         ❌ 测试脚本混在根目录
├── tmp/                      ❌ 大量过时代码
└── static/js/app copy.js     ❌ 备份文件
```

### **优化后的结构（当前代码）**

```
app/
├── ai_models/                ✅ AI模型文件专用目录
│   ├── README.md
│   ├── ms1mv3_arcface_r100.onnx
│   └── shape_predictor_68_face_landmarks.dat
├── core/                     ✅ 核心模块
│   ├── ai_engine.py
│   ├── config.py
│   ├── constants.py
│   ├── database.py
│   ├── exceptions.py
│   └── logger.py
├── middleware/               ✅ 中间件
│   └── api_stats_middleware.py
├── models/                   ✅ 数据模型
│   ├── schemas.py
│   ├── request/
│   └── response/
├── router/                   ✅ 路由层
│   ├── faces.py
│   ├── persons.py
│   ├── web.py
│   └── ops.py
├── services/                 ✅ 业务逻辑层
│   ├── person.py
│   ├── face_service.py
│   └── ops_stats.py
├── utils/                    ✅ 工具函数
│   ├── image_loader.py
│   └── utils_mongo.py
├── static/                   ✅ 静态资源
├── media/                    ✅ 媒体文件
├── tests/                    ✅ 测试文件
│   ├── unit/
│   ├── integration/
│   ├── stress_test.py
│   └── test_recognize.py
├── scripts/                  ✅ 脚本工具
├── logs/                     ✅ 日志目录
├── tmp/                      ✅ 临时/遗留代码（待清理）
├── .gitignore
├── config.toml
├── main.py
└── requirements.txt
```

---

## ✨ 主要改进

### **1. 目录结构优化**

- ✅ 模型文件统一放入 `ai_models/`
- ✅ 测试脚本集中到 `tests/`
- ✅ 新增 `middleware/` 用于统计/监控中间件
- ✅ 新增 `router/ops.py` + `services/ops_stats.py` 进行运维统计

### **2. 常量与服务层**

- `core/constants.py`：统一管理业务常量
- `services/face_service.py`：封装识别逻辑（当前未接入路由层）

**注意**: 当前 `/recognize` 仍由 `router/faces.py` 实现；若接入 `face_service`，需先对齐 `RecognizeResp` 的返回结构（match 列表）。

### **3. 运维统计模块**

- `middleware/api_stats_middleware.py`：记录 API 调用日志 + TTL 清理
- `router/ops.py`：提供健康检查与统计查询
- `config.toml` 新增 `[stats]` 配置，控制统计数据保留天数

---

## 📐 架构分层

```
客户端
  ↓
FastAPI 应用
  ├── Middleware（APIStats）
  ↓
Router Layer (faces.py, persons.py, ops.py, web.py)
  ↓
Service Layer (person.py, ops_stats.py, face_service.py)
  ↓
Core Layer (ai_engine.py, database.py)
```

---

## 🛠️ 代码改动汇总（当前结构）

| 文件 | 作用 | 备注 |
|------|------|------|
| `core/ai_engine.py` | AI 推理与特征匹配 | 模型路径指向 `ai_models/` |
| `middleware/api_stats_middleware.py` | 统计中间件 | 记录 API 日志 + TTL |
| `router/ops.py` | 运维接口 | 健康检查、指标统计 |
| `services/ops_stats.py` | 统计聚合 | 访问量/响应时间统计 |
| `core/constants.py` | 常量管理 | 统一常量定义 |
| `services/face_service.py` | 识别服务层 | 未接入路由 |
| `app/.gitignore` | 规范忽略 | 过滤日志/模型/媒体 |
| `ai_models/README.md` | 模型说明 | 下载与放置说明 |

---

## 📝 后续优化建议

1. 将 `/recognize` 逻辑抽到 `face_service.py`，统一返回结构
2. 清理 `tmp/` 目录，迁移有价值的脚本到 `scripts/`
3. 补充单元测试（`tests/unit/`）
4. 视需求增加 API 版本前缀（如 `/api/v1`）

---

## ✅ 验证清单

- [ ] 服务正常启动 (`python main.py`)
- [ ] 模型文件正确加载
- [ ] `/recognize` 与 `/recognize/batch` 正常
- [ ] `/persons` 相关接口正常
- [ ] `/ops/health` 返回健康状态
- [ ] Web 界面可访问

---

## 📚 相关文档

- [PERSONS_API.md](PERSONS_API.md) - 人物管理接口说明
- [RECOGNIZE_API_ERRORS.md](RECOGNIZE_API_ERRORS.md) - 识别接口错误码说明
- [OPS_API.md](OPS_API.md) - 运维接口说明
- [PERSONS_DELETE_API_OPTIMIZATION.md](PERSONS_DELETE_API_OPTIMIZATION.md) - 删除接口优化

---

**更新日期**: 2026-01-09
**版本**: v2.2.0
