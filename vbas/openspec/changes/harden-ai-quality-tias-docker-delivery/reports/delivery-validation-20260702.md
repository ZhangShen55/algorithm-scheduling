# harden-ai-quality-tias-docker-delivery 交付验证报告

## 验证结论

本次验证通过。`ai_quality` 和 `tias` 完成 Docker 交付加固、Cython 保护构建、模型静态加密能力、部署文档补充，并完成 4 节课端到端回归。

## 验证环境

- 验证日期：2026-07-02
- 分支：`dev_6.0_ai_quality`
- Python 环境：`conda run -n jy-tias`
- Kafka：`10.67.65.8:9092`
- Kafka topic：`classroom_cv_task`
- Kafka group：`cv-analysis-service-harden-20260702`
- Redis：本地 Docker Redis，`redis://127.0.0.1:6379/0`
- ai_quality API：`http://127.0.0.1:9100`
- TIAS 实例：`http://127.0.0.1:8981`、`8982`、`8983`、`8984`
- 视频文件服务：本地 Nginx，`http://127.0.0.1:18080`
- 快照挂载目录：`10.80.5.131:/image` 挂载到项目 `mnt`
- 快照配置：`SnapshotMountRoot=/Users/zhangshen/Documents/workspace/jy-algorithm-tias-server/mnt`，入库保存相对路径

## Docker 构建与保护验证

已构建镜像：

| 镜像 | 镜像 ID | 架构 |
| --- | --- | --- |
| `ai-quality:6.0-test` | `67ffa1f436aa` | `arm64/linux` |
| `ai-quality:6.0-protected-test` | `ddd5a3aa4000` | `arm64/linux` |
| `tias:6.0-test` | `6370749df847` | `amd64/linux` |
| `tias:6.0-protected-test` | `b87eb0550392` | `amd64/linux` |

保护镜像检查：

- `ai-quality:6.0-protected-test`：`/workspace/ai_quality` 下存在 22 个 `.so`，保留薄入口 `ai_quality/app.py`，核心业务源码示例 `ai_quality/application/worker.py` 已移除。
- `tias:6.0-protected-test`：`tias/api`、`tias/core`、`tias/services`、`tias/schemas` 下存在 30 个 `.so`，保留薄入口 `tias/main.py`，核心源码示例 `tias/services/teacher_head_pose_service.py`、`tias/core/model_protection.py` 已移除。
- TIAS 保护镜像在 Mac arm64 本机运行时有 `linux/amd64` 平台提示，这是基础镜像架构差异，不影响本次功能验证；生产部署建议使用匹配服务器架构的镜像构建环境。

## 模型保护验证

- 单元测试覆盖模型加密解密一致性、错误密钥失败、临时目录权限和清理逻辑。
- `tias.core.model_protection` 支持 AES-GCM；缺少 `cryptography` 时提供标准库 fallback。
- `ModelProtection.Enabled=false` 时保持明文模型加载路径不变。
- `ModelProtection.Enabled=true` 时从加密模型目录读取 `.enc`，从运行时密钥文件读取密钥，临时解密到配置目录，加载后按配置清理。
- 文档已明确：模型加密保护静态文件，不承诺防止具备宿主机 root、容器调试或进程内存读取权限的运行时逆向。

## 4 节课 Kafka 回归

正式回归任务如下：

| task_id | course_id | partition | offset | 结果 |
| --- | --- | ---: | ---: | --- |
| `harden-ai-quality-rerun-20260702-01` | `harden-rerun-01` | 0 | 30 | 成功 |
| `harden-ai-quality-rerun-20260702-02` | `harden-rerun-02` | 0 | 31 | 成功 |
| `harden-ai-quality-rerun-20260702-03` | `harden-rerun-03` | 0 | 32 | 成功 |
| `harden-ai-quality-rerun-20260702-04` | `harden-rerun-04` | 0 | 33 | 成功 |

`classroom_cv_task` 当前只有 partition `0`。本次回归按单 Worker 逐条消费，重点验证 Docker 交付、远程 TIAS 调度、落库和快照写入链路。

## 数据库结果

`lesson_ai_workflow`：

| task_id | status | progress | note | duration_sec |
| --- | ---: | ---: | --- | ---: |
| `harden-ai-quality-rerun-20260702-01` | 3 | 100 | 视觉分析完成 | 564 |
| `harden-ai-quality-rerun-20260702-02` | 3 | 100 | 视觉分析完成 | 523 |
| `harden-ai-quality-rerun-20260702-03` | 3 | 100 | 视觉分析完成 | 510 |
| `harden-ai-quality-rerun-20260702-04` | 3 | 100 | 视觉分析完成 | 530 |

五张表计数：

| task_id | behavior_timeline | snapshot_event | student_behavior_stat | indicator_score_result |
| --- | ---: | ---: | ---: | ---: |
| `harden-ai-quality-rerun-20260702-01` | 55 | 19 | 1 | 5 |
| `harden-ai-quality-rerun-20260702-02` | 55 | 19 | 1 | 5 |
| `harden-ai-quality-rerun-20260702-03` | 55 | 19 | 1 | 5 |
| `harden-ai-quality-rerun-20260702-04` | 55 | 19 | 1 | 5 |

## 快照验证

- NFS 挂载：`10.80.5.131:/image on /Users/zhangshen/Documents/workspace/jy-algorithm-tias-server/mnt`
- 文件数量：76 张，和数据库 `lesson_snapshot_event` 的 4 * 19 条记录一致。
- 入库路径示例：`cv/harden-ai-quality-rerun-20260702-01/student-5-0107.png`
- 绝对 URL 或绝对路径计数：0

## 命令验证

已执行并通过：

```bash
conda run -n jy-tias env PYTHONPATH=. pytest -q
```

结果：`128 passed, 2 skipped, 222 warnings`

```bash
openspec validate harden-ai-quality-tias-docker-delivery --strict
```

结果：`Change 'harden-ai-quality-tias-docker-delivery' is valid`

```bash
docker compose -f ai_quality/docker/docker-compose.yml config
docker compose -f tias/docker/docker-compose.yml config
docker compose -f tias/docker/docker-compose.gpu.yml config
```

结果：三个 compose 静态校验均通过。

```bash
git diff --check
```

结果：无空白错误。

## 已知说明

- 第一次 4 节课尝试中，曾因测试环境手工注册 TIAS 后没有持续心跳导致注册表过期，后续正式 rerun 使用持续心跳同步后通过；这不是本次代码交付失败点。
- 本地 Mac 执行 TIAS Docker 保护镜像时有 amd64/arm64 平台提示，生产构建应使用目标架构环境或显式指定 `--platform`。
- 模型加密只提升静态模型文件被复制后的保护门槛，不替代私有镜像仓库、宿主机权限控制、只读挂载、密钥管理和最小权限运行。
