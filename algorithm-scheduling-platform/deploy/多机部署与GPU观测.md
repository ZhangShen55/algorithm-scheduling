# 多机部署与 GPU 观测

## 拓扑

平台主机 `192.168.29.11` 运行 Control Service、Orchestrator、Vision Orchestrator、Online Gateway、PostgreSQL、Redis、Kafka，并作为 NFS 服务端导出：

```text
/data/course
/data/result
```

GPU 主机 `192.168.29.12` 通过 `root@192.168.29.12:22` 执行部署。该主机现有媒体文件服务使用 `5555`，Qwen 服务独占 GPU 2 和端口 `8866`，GPU Exporter 使用 `9400`；远程算子不得停止、覆盖或占用这些既有资源。

## 身份与地址

每个算子实例必须同时具备：

```text
PLATFORM_INSTANCE_ID=vbas-29-12-gpu0
PLATFORM_HOST_ID=192.168.29.12
PLATFORM_GPU_ID=0
PLATFORM_SERVICE_URL=http://192.168.29.12:28981
PLATFORM_INSTANCE_LABELS={"host_id":"192.168.29.12","gpu":"0"}
```

`PLATFORM_INSTANCE_LABELS` 显式配置时优先级最高，必须同时包含 `host_id` 和 `gpu`。如果没有显式 labels，注册客户端使用 `PLATFORM_HOST_ID` 与 `PLATFORM_GPU_ID` 生成默认 labels。

Control Service 的 `[operator_registry.trusted_service_urls]` 必须为每个远程实例配置同一个跨机可达 URL，不能使用 Docker 单机网络内的服务名。

当前远程部署在 GPU 0 运行 VBas（`28981`），在 GPU 1 运行 ScreenDet（`28880`）。由于该主机 Docker NAT 链不可用，示例使用 host 网络直接监听宿主机端口；GPU 设备预留仍分别固定为 `0`、`1`，GPU 2 保留给既有 Qwen 服务。

## GPU Exporter

在每台 GPU 主机独立启动：

```bash
export GPU_EXPORTER_HOST_ID=192.168.29.12
docker compose -f algorithm-scheduling-platform/deploy/docker-compose.gpu-metrics.yml up -d --build
```

检查：

```bash
curl -fsS http://192.168.29.12:9400/health
curl -fsS http://192.168.29.12:9400/gpu
```

`/gpu` 的 `host_id` 必须是 `192.168.29.12`。控制台的 GPU 节点配置填写该 IP、显示名称和 `http://192.168.29.12:9400`，刷新周期可设置为 1 秒或更高。

远程算子与 GPU Exporter 的完整示例见 `docker-compose.multi-host.example.yml`。平台主机上的
Control Service 同步加载 `templates/multi-host-trusted-service-urls.toml.example` 中的可信地址。

## NFS 预检

平台主机使用 `templates/multi-host-nfs.exports.example` 作为受控导出配置，远程 GPU 主机将
`templates/multi-host-nfs.fstab.example` 中的两项合并到 `/etc/fstab` 后执行 `mount -a`。当前
算子容器以 root 运行，因此导出使用 `no_root_squash`；若后续改为固定非 root UID/GID，应同步
收紧目录属主和导出权限。

远程主机把 NFS 挂载到宿主机 `/data/course`、`/data/result`，再以相同路径映射进容器。两台主机分别执行：

```bash
python3 deploy/scripts/multi_host_preflight.py shared-path /data/course
python3 deploy/scripts/multi_host_preflight.py shared-path /data/result --result
```

这会检查目录非符号链接、UID/GID、权限、写入、fsync、同目录原子 rename 和删除。`/data/result --result` 的输出必须纳入清理策略，常规临时目录清理不得删除该目录。

跨机可见性检查由平台主机创建探针、远程主机读取并删除：

```bash
python3 deploy/scripts/multi_host_preflight.py create-peer-probe /data/course
python3 deploy/scripts/multi_host_preflight.py verify-peer-probe /data/course/.multi-host-peer-<token> <sha256>
```

## 回滚边界

GPU Exporter 只读 NVML，不挂载 Docker Socket，不控制容器。多机观测回滚只需恢复控制台节点列表和 Exporter 镜像；不得删除 NFS 数据，不得修改 A 服务、Kafka envelope、数据库结构或任务路径。
