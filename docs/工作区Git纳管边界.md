# 工作区 Git 纳管边界

## 仓库范围

工作区根目录作为单一 Git 仓库，纳管调度平台四个服务、算法算子源码、安全默认配置、测试代码、数据库迁移、部署文件、设计文档、OpenSpec 与 Harness。

远端地址：`git@github.com:ZhangShen55/algorithm-scheduling.git`。

## 纳入内容

- Python 源码、脚本、Dockerfile、Compose 与依赖清单。
- 不包含真实凭据的 `config.toml` 和 `.env.example`。
- 单元测试、契约测试及体积适合普通 Git 的小型测试资源。
- PostgreSQL 迁移、README、设计文档、PDF、OpenSpec 与 Harness。

## 排除内容

- ASR、OCR、VBas、人脸和图像质量模型权重及加密模型。
- wheel、虚拟环境、构建产物、Python/pytest/Ruff/Mypy 缓存。
- 课程音视频、大型本地推理样例、识别输出和运行结果。
- 日志、PID、数据库文件、临时目录、`/data`、`/output`。
- `.env`、私钥、证书和 Docker secrets。
- 当前含本地秘密字段的 `facerec/config.toml`、`ocr/config.toml` 与 `text_analysis/config.toml`；这些项目后续应使用脱敏示例配置表达可复现默认值。

## 提交门禁

每次基线或结构迁移提交前必须检查：

1. `git diff --cached --stat` 与 `git diff --cached --name-only`。
2. 暂存文件中是否存在超过 GitHub 普通 Git 限制的大文件。
3. 是否误纳入模型、媒体、密钥、真实密码、日志或运行数据。
4. 安全默认配置是否仍可通过环境变量覆盖生产秘密。

目录迁移验证完成前只产生本地提交，不自动执行 `git push`。
