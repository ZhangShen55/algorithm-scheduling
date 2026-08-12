# 里程碑 2B 部署证据目录

本目录只提交目录结构和本说明。运行时证据按以下固定层级归档，并由根 `.gitignore` 排除：

```text
deploy/reports/milestone-2b/releases/{release_tag}/{git_sha}/
├── preflight/
├── container-maintenance/
├── image-build/
├── gpu-instances/
├── registration/
├── smoke/
├── negative/
├── load/
├── recovery/
└── summary/
```

各目录含义如下：

| 目录 | 运行证据 |
|---|---|
| `preflight` | 服务器、目录、端口、基础设施和版本预检 |
| `container-maintenance` | 既有容器快照、暂停账本和恢复结果 |
| `image-build` | 八镜像构建与镜像检查结果 |
| `gpu-instances` | GPU UUID、PID、cgroup 和同步推理采样 |
| `registration` | 24 实例注册、心跳、容量和路由结果 |
| `smoke` | 八类算子真实推理结果摘要 |
| `negative` | 反例与故障边界验证 |
| `load` | 并发、容量与压力验证 |
| `recovery` | 停止、恢复和残留进程验证 |
| `summary` | 可共享字段范围内的脱敏汇总 |

使用 `deploy/scripts/prepare-report-directory` 初始化目录。它将运行证据目录设为 `0700`，
拒绝软链接、非目录和路径穿越，并保留所有既有证据。调用者写入的证据文件应使用 `0600`。

`deploy/model-assets.json` 是可提交的模型根结构定义，不含模型哈希。真实外部
`model-assets.manifest.json` 包含逐文件字节数和 SHA-256，只允许保存在 Git 工作树外的受限目录；
初始化器可将其复制到 `{restricted_root}/milestone-2b/releases/{release_tag}/{git_sha}/`，目录权限
为 `0700`、文件权限为 `0600`。受限根不得设置在本 Git 工作树内。

`summary` 只能记录模型根名称、文件总数和总字节数，不得包含逐文件路径、逐文件哈希、密钥内容、
密钥大小、密钥摘要、人脸原图或服务器认证信息。真实 JSON、日志、快照、暂停账本、模型 manifest
和任何 secret metadata 都是运行证据，不得强制加入 Git。
