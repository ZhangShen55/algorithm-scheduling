# 移除 API v1 路由前缀设计

## 背景

服务当前将同一个 FastAPI 路由同时挂载到根路径和 `/api/v1`，导致每个接口存在两套等价地址。业务侧只需要无前缀地址，因此移除 `/api/v1` 路由及其配置，减少调用约定和部署配置的歧义。

## 目标行为

- 所有业务接口只保留无前缀路径，例如 `/health`、`/detect_tilt`、`/detect_screen`、`/detect_inspect`、`/detect_quality_abnormal`、`/detect_occlusion`、`/detect_all`、`/config` 和 `/config/reload`。
- `/api/v1/*` 不提供重定向或兼容处理，统一由 FastAPI 返回 HTTP 404。
- 删除 `AppConfig.api_prefix` 和 `config.toml` 中的 `app.api_prefix`，不保留无效配置。
- 根路径 `/` 返回的接口地址全部使用无前缀路径。
- 各接口现有请求报文、响应报文、状态码和检测逻辑保持不变。

## 实现范围

1. `app/main.py` 只挂载一次公共路由，并更新根路径服务信息。
2. `app/core/config.py` 和 `config.toml` 删除 `api_prefix`。
3. 单元测试覆盖无前缀路由可用、旧前缀路由返回 404，以及配置输出不再包含 `api_prefix`。
4. README、API 接口文档、部署验证脚本和项目开发说明全部改用无前缀地址。

## 兼容性

这是有意的破坏性路由变更。使用 `/api/v1/*` 的调用方必须切换到对应的根路径接口。服务端不设置迁移期，也不返回 301、307 或 308 重定向。

## 验收标准

- 无前缀接口注册正常，现有接口测试继续通过。
- `/api/v1/health` 和 `/api/v1/detect_all` 等旧地址返回 HTTP 404。
- 应用运行配置、根路径响应、项目文档和验证脚本中不再使用 `api_prefix` 或 `/api/v1`。
- `screen_det` Conda 环境下完整单元测试通过，Python 编译检查通过，`git diff --check` 无错误。
