# 场景：ASR Offline tar 镜像单容器部署

## 目标与范围

验证已交付的 ASR Offline tar 镜像在单台 NVIDIA GPU Linux 主机上可以完成离线转写。本场景不启动平台调度服务、Kafka、Redis 或模型外部挂载。

鏡像必须已包含 `/app/model`，只读挂载宿主机 `config.toml` 到 `/config.toml`。日志由容器在 `/app/logs/asr_service.log` 中生成，不挂载宿主机日志目录。

## 前置条件

- x86_64 Linux 安装 Docker Engine、NVIDIA 驱动和 NVIDIA Container Toolkit。
- `nvidia-smi` 能查看需要分配给算子的物理 GPU。
- 交付 tar 包和 GPU 版 `asr_offline.gpu.toml` 与镜像一起发布。
- 配置使用 `device = "cuda:0"`。Docker 只透传一张物理 GPU 后，它在容器内会重编号为 `cuda:0`。

## 部署命令

```bash
sudo install -d -m 0755 /opt/seacraft/asr-offline/config
sudo install -m 0644 /path/to/asr_offline.gpu.toml \
  /opt/seacraft/asr-offline/config/config.toml

sudo docker load -i /srv/releases/seacraft-asr-offline.tar
export IMAGE_REF='<repository>:<tag>' # 以 docker load 的 Loaded image 输出为准

sudo docker run --rm --gpus '"device=0"' \
  --entrypoint /bin/bash "$IMAGE_REF" -lc 'nvidia-smi'

sudo docker rm -f asr-offline 2>/dev/null || true
sudo docker run -d \
  --name asr-offline \
  --restart unless-stopped \
  --gpus '"device=0"' \
  -p 8083:8083 \
  -v /opt/seacraft/asr-offline/config/config.toml:/config.toml:ro \
  "$IMAGE_REF"
```

启动命令不传递 `-e` 参数：进程名、端口、单 worker 和平台注册都使用镜像内置默认值。`-p 8083:8083` 会绑定宿主机全部网卡，必须在防火墙或安全组中仅放行所需的上游网段。

## 验收

```bash
until curl -fsS http://127.0.0.1:8083/ops/health; do sleep 2; done
curl -fsS http://127.0.0.1:8083/get_status | jq -e '.appVersion == "asr:latest"'
curl -fsS http://127.0.0.1:8083/openapi.json \
  | jq -e '.paths | has("/v1.1.8/seacraft_asr") and has("/audio/db_snr")'
sudo docker exec asr-offline ls -lh /app/logs/asr_service.log
sudo docker exec -it asr-offline sh -lc 'tail -n 200 -f /app/logs/asr_service.log'
```

再使用已获授权的短音频执行一次真实请求：

```bash
curl -fsS -X POST http://127.0.0.1:8083/v1.1.8/seacraft_asr \
  -F 'audioFile=@/srv/fixtures/asr-smoke.wav;type=audio/wav' \
  -F 'language=zh' \
  -F 'showSpk=false' \
  -F 'showEmotion=false' \
  -F 'showRoleIdentify=false' \
  -F 'wordTimestamps=false' \
  | jq -e '(.text | length) > 0 and (.segments | length) > 0'
```

预期是健康检查为 HTTP 200，状态版本为 `asr:latest`，OpenAPI 仍包含 v1.1.8 转写路由和音频质量路由，且转写结果的文本和 segments 非空。

## 日志与回滚边界

日志按本地时间每日轮转，保留当日活动文件和前 6 个归档。容器重建后文件日志会丢失；需要跨重建保留日志时，应使用 Docker 日志驱动或专用的日志采集系统，而不恢复宿主机目录挂载。
回滚前记录当前镜像引用：

```bash
export PREVIOUS_IMAGE="$(sudo docker inspect asr-offline --format '{{.Config.Image}}')"
```

导入新 tar 、以同一组 `docker run` 参数启动新镜像并验收。验收失败时，删除新容器后以相同参数启动 `$PREVIOUS_IMAGE`。不要删除宿主机上的配置目录或前一个镜像。

## 证据边界

本场景与 ASR 单元测试验证了当天加 6 个归档的日志配置和启动契约。它不代表已在目标服务器导入 tar 或完成 GPU 真实推理；每次发布必须执行本文的导入、GPU 透传和音频验收命令。
