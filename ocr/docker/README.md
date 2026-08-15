# OCR v6 Linux Docker 部署手册

本文面向拿到离线镜像包的 Linux 部署和运维人员。正式交付物为：

- `ocr_v6_amd.tar`：`linux/amd64`、Cython 保护版镜像；
- `ocr_v6_amd.tar.sha256`：离线包摘要；
- `config.toml.example`：宿主机配置示例；
- `smoke_test.py`、`load_test.py` 和测试图片：验收工具。

镜像标签固定为 `ocr:v6_amd`。正式配置不在镜像内，必须从宿主机只读挂载。该平台镜像
还要求每个 OCR 容器传入 `REQUIRE_GPU=true`，用于在启动阶段阻止未映射 GPU 的误部署。

## 1. 加载镜像

在交付目录执行的第一条命令是：

```bash
docker load -i ocr_v6_amd.tar
```

随后校验离线包和镜像。交付摘要应为
`8201d9234eeac95cc993f76d74890f0dbbce4910a018e2db6ba0472790822cd9`：

```bash
sha256sum -c ocr_v6_amd.tar.sha256
docker image inspect ocr:v6_amd --format '{{.Id}} {{.Architecture}} {{.Os}}'
```

正确结果：

```text
ocr_v6_amd.tar: OK
sha256:bba69f2ab3f9521c3d5dde8d3f3803a52f673925d3204552738347c8ff3d5abe amd64 linux
```

摘要、架构或镜像 ID 不一致时停止部署并重新取得交付包，不要继续启动容器。

## 2. 检查 GPU 环境

```bash
uname -m
docker version --format '{{.Server.Version}}'
nvidia-smi -L
docker run --rm --gpus '"device=0"' nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi -L
```

必须满足：主机为 `x86_64`，Docker、NVIDIA 驱动和 NVIDIA Container Toolkit 均可用。
记录准备使用的物理 GPU 编号，确认显存没有被未知进程占满：

```bash
nvidia-smi --query-gpu=index,name,memory.total,memory.used,utilization.gpu --format=csv
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv
```

## 3. 创建配置

创建部署目录并从交付示例生成正式配置：

```bash
mkdir -p /opt/ocr-v6
cp config.toml.example /opt/ocr-v6/config.toml
chmod 640 /opt/ocr-v6/config.toml
```

建议先使用以下完整配置。每个容器只映射一张物理 GPU，因此容器内统一使用
`device = "cuda:0"`，与宿主机物理 GPU 编号无关。

```toml
[application]
name = "ocr"
version = "OCR_V3.0_PP-OCRv6"

[server]
host = "0.0.0.0"
port = 8866
workers = 1

[ocr]
device = "cuda:0"
cpu_threads = 8
enable_mkldnn = false
detection_model_dir = "models/PP-OCRv6_medium_det"
recognition_model_dir = "models/PP-OCRv6_medium_rec"
recognition_batch_size = 4
enable_hpi = false
max_concurrency = 1
image_max_bytes = 20971520

[ocr.detection]
limit_side_len = 960
threshold = 0.3
box_threshold = 0.5
unclip_ratio = 1.5

[formula]
enabled = false
layout_model_dir = "models/PP-DocLayout_plus-L"
recognition_model_dir = "models/PP-FormulaNet_plus-M"
recognition_batch_size = 1
layout_threshold = 0.5

[logging]
level = "INFO"
directory = "logs"
max_size_mb = 100
backup_count = 3
```

### 参数决策表

| 参数 | 部署决策 | 推荐值与说明 |
| --- | --- | --- |
| `application.name` | 按需修改 | 用于服务标识；没有平台命名要求时保持默认 |
| `application.version` | 保持默认 | 当前镜像为 `PP-OCRv6` |
| `server.host` | 保持默认 | 容器内必须监听 `0.0.0.0` |
| `server.port` | 保持默认 | 容器内固定 `8866`，宿主机端口由 `docker run` 决定 |
| `server.workers` | 保持默认 | 固定 `1`，多实例通过多个容器实现 |
| `ocr.device` | 必须修改/确认 | GPU 容器只映射一张卡时必须为 `cuda:0` |
| `cpu_threads`、`enable_mkldnn` | 保持默认 | 只在 `device = "cpu"` 时生效，GPU 部署忽略 |
| 两个 OCR 模型目录 | 保持默认 | 模型已在镜像内，禁止改成宿主机随意路径 |
| OCR `recognition_batch_size` | 保持默认 | RTX 3090 已验证推荐 `4`；其他显卡先用 `4` 再压测 |
| `enable_hpi` | 保持默认 | 必须为 `false`，当前镜像没有 UltraInfer |
| `max_concurrency` | 保持默认 | 必须为 `1`；单引擎串行推理，入口并发会排队 |
| `image_max_bytes` | 按需修改 | 默认 20 MiB；增大时同步调整 Nginx 请求体上限 |
| `limit_side_len` | 按需修改 | 默认 `960`；增大可能提高小字召回，也会增加耗时和显存 |
| `threshold` | 按需修改 | 默认 `0.3`；降低会增加召回和噪声 |
| `box_threshold` | 保持默认 | 默认 `0.5`，过滤低置信度文本框 |
| `unclip_ratio` | 按需修改 | 默认 `1.5`，控制文本框向外扩张比例 |
| `formula.enabled` | 按需修改 | 默认关闭；只有业务需要公式识别时改为 `true` |
| 两个公式模型目录 | 保持默认 | 模型已在镜像内 |
| 公式 `recognition_batch_size` | 保持默认 | 固定 `1`，不套用 OCR batch 值 |
| `layout_threshold` | 按需修改 | 默认 `0.5`，公式布局区域阈值 |
| `logging.level` | 按需修改 | 生产推荐 `INFO`，排障时临时改为 `DEBUG` |
| `logging.directory` | 保持默认 | 容器内为 `/app/logs` |
| `max_size_mb`、`backup_count` | 保持默认 | 应用日志 100 MiB、保留 3 个；Docker 日志单独轮转 |

## 4. 选择部署拓扑

### 4.1 单卡单实例

单卡单实例不需要 Nginx，直接把宿主机 `8866` 映射到容器。以下示例使用宿主机物理
GPU 2；该卡在容器内变成逻辑 GPU 0，因此配置仍为 `device = "cuda:0"`。

```bash
docker run -d \
  --name ocr-v6-amd \
  --restart unless-stopped \
  --gpus '"device=2"' \
  -e REQUIRE_GPU=true \
  -p 8866:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  ocr:v6_amd
```

### 4.2 单卡多实例

同一张物理 GPU 可以启动多个 OCR 容器。每个容器只映射一张物理 GPU，配置均使用
`device = "cuda:0"`。后端只绑定宿主机回环地址，不能直接暴露到外部网络。

以下示例在物理 GPU 0 上启动两个实例：

```bash
docker run -d \
  --name ocr-v6-gpu0-1 \
  --restart unless-stopped \
  --gpus '"device=0"' \
  -e REQUIRE_GPU=true \
  -p 127.0.0.1:8867:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file --log-opt max-size=100m --log-opt max-file=3 \
  ocr:v6_amd

docker run -d \
  --name ocr-v6-gpu0-2 \
  --restart unless-stopped \
  --gpus '"device=0"' \
  -e REQUIRE_GPU=true \
  -p 127.0.0.1:8868:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file --log-opt max-size=100m --log-opt max-file=3 \
  ocr:v6_amd
```

单卡多实例必须通过第 5 节的 Docker Nginx 提供统一入口。Nginx upstream 中只保留
`8867` 和 `8868` 两个后端。

### 4.3 多卡多实例

下面以两张物理 GPU、每卡两个实例为例。前两个容器与单卡示例相同，再启动：

```bash
docker run -d \
  --name ocr-v6-gpu1-1 \
  --restart unless-stopped \
  --gpus '"device=1"' \
  -e REQUIRE_GPU=true \
  -p 127.0.0.1:8869:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file --log-opt max-size=100m --log-opt max-file=3 \
  ocr:v6_amd

docker run -d \
  --name ocr-v6-gpu1-2 \
  --restart unless-stopped \
  --gpus '"device=1"' \
  -e REQUIRE_GPU=true \
  -p 127.0.0.1:8870:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file --log-opt max-size=100m --log-opt max-file=3 \
  ocr:v6_amd
```

确认四个后端均为 `Up`，并且每个容器内只看到一张逻辑 GPU 0：

```bash
docker ps --filter name=ocr-v6-gpu
docker exec ocr-v6-gpu0-1 nvidia-smi -L
docker exec ocr-v6-gpu0-2 nvidia-smi -L
docker exec ocr-v6-gpu1-1 nvidia-smi -L
docker exec ocr-v6-gpu1-2 nvidia-smi -L
curl --fail http://127.0.0.1:8867/ocr/getVersion
curl --fail http://127.0.0.1:8868/ocr/getVersion
curl --fail http://127.0.0.1:8869/ocr/getVersion
curl --fail http://127.0.0.1:8870/ocr/getVersion
```

## 5. 使用 Docker Nginx 提供统一入口

多实例需要 Nginx；单卡单实例不需要。本文验证的 Nginx 镜像为
`nginx:1.27-alpine`。联网服务器可拉取，离线服务器应由交付方同时提供 Nginx tar 包。

```bash
docker pull nginx:1.27-alpine
docker image inspect nginx:1.27-alpine --format '{{.Id}} {{.Architecture}}'
```

创建 `/opt/ocr-v6/nginx.conf`：

```nginx
events {
    worker_connections 1024;
}

http {
    log_format upstream_json escape=json
        '{"time":"$time_iso8601","remote_addr":"$remote_addr",'
        '"request":"$request","status":$status,'
        '"upstream_addr":"$upstream_addr","upstream_status":"$upstream_status",'
        '"request_time":$request_time,'
        '"upstream_response_time":"$upstream_response_time"}';

    access_log /dev/stdout upstream_json;
    error_log /dev/stderr warn;

    upstream ocr_backend {
        least_conn;
        server 127.0.0.1:8867 max_fails=3 fail_timeout=30s;
        server 127.0.0.1:8868 max_fails=3 fail_timeout=30s;
        server 127.0.0.1:8869 max_fails=3 fail_timeout=30s;
        server 127.0.0.1:8870 max_fails=3 fail_timeout=30s;
        keepalive 32;
    }

    server {
        listen 8866;
        client_max_body_size 25m;

        location = /nginx-health {
            access_log off;
            return 200 "ok\n";
        }

        location / {
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_connect_timeout 5s;
            proxy_read_timeout 180s;
            proxy_send_timeout 180s;
            proxy_next_upstream error timeout http_502 http_503 http_504;
            proxy_next_upstream_tries 4;
            proxy_pass http://ocr_backend;
        }
    }
}
```

单卡双实例时删除 `8869`、`8870` 两行。先检查配置，再启动 Nginx 容器：

```bash
docker run --rm \
  --network host \
  -v "/opt/ocr-v6/nginx.conf:/etc/nginx/nginx.conf:ro" \
  nginx:1.27-alpine nginx -t

docker run -d \
  --name ocr-nginx \
  --restart unless-stopped \
  --network host \
  -v "/opt/ocr-v6/nginx.conf:/etc/nginx/nginx.conf:ro" \
  --log-driver json-file \
  --log-opt max-size=100m \
  --log-opt max-file=3 \
  nginx:1.27-alpine
```

`--network host` 让 Nginx 容器访问宿主机回环后端；Nginx 对外监听 `8866`，OCR 后端端口
仍仅绑定 `127.0.0.1`。生产防火墙只需放行统一入口 `8866`。

检查统一入口与分流日志：

```bash
curl --fail http://127.0.0.1:8866/nginx-health
curl --fail http://127.0.0.1:8866/ocr/getVersion
docker logs --tail 200 ocr-nginx
```

访问日志中的 `$upstream_addr` 应出现多个后端地址。修改 upstream 后执行无中断重载：

```bash
docker exec ocr-nginx nginx -t
docker exec ocr-nginx nginx -s reload
```

## 6. 验收服务

### 6.1 启动成功判定

每个 OCR 容器必须保持 `Up`，日志包含：

```text
Creating model: ('PP-OCRv6_medium_det', '/app/models/PP-OCRv6_medium_det', None)
Creating model: ('PP-OCRv6_medium_rec', '/app/models/PP-OCRv6_medium_rec', None)
Application startup complete.
Uvicorn running on http://0.0.0.0:8866
```

```bash
docker logs --tail 200 ocr-v6-amd
curl --fail --show-error http://127.0.0.1:8866/ocr/getVersion
python3 /opt/ocr-v6/smoke_test.py \
  --base-url http://127.0.0.1:8866 \
  --image /opt/ocr-v6/ocr-test.jpg
```

多实例时只测试 Nginx 的统一入口，不把回环后端地址交给业务调用方。

### 6.2 公式开关

默认 `formula.enabled = false`。请求传 `enable_formula=true` 时，`formula_results` 返回能力
未启用信息，但原有 OCR 响应结构不变。确需公式识别时修改为 `true`，逐个重启 OCR 容器，
再用公式图片验收：

```bash
python3 /opt/ocr-v6/smoke_test.py \
  --base-url http://127.0.0.1:8866 \
  --image /opt/ocr-v6/formula-document.png \
  --enable-formula
```

公式模型会额外占用显存，启用后必须重新压测并评估每卡实例数。

## 7. 常见失败日志与判断

以下清单覆盖配置文件不存在、GPU 不可用、模型异常、端口占用和 Nginx 后端故障。

| 现象或日志 | 判定 | 处理 |
| --- | --- | --- |
| `配置文件不存在：/app/config.toml` | 配置未挂载或路径错误 | 检查宿主机文件和 `docker inspect <容器> --format '{{json .Mounts}}'` |
| `GPU 设备 cuda:0 不可用` | GPU 映射、驱动或 Toolkit 异常 | 对比宿主机和容器内 `nvidia-smi -L`，检查 `DeviceRequests` |
| `检测模型目录不存在`、`模型文件摘要不一致` | 镜像或模型损坏 | 停止该镜像，重新加载摘要正确的 tar，不在容器内替换单个模型 |
| `port is already allocated`、`address already in use` | 宿主机端口冲突 | 用 `ss -lntp` 和 `docker ps` 找到占用方，不能直接停止未知业务 |
| Nginx `connect() failed (111: Connection refused)` | 某 OCR 后端不可用 | 检查对应容器；其他健康后端会重试，访问日志可见 `502, 200` |
| Nginx `no live upstreams` 或统一入口返回 `502` | 全部后端不可用 | 恢复至少一个 OCR 容器后再验收 |
| `request body is buffered to a temporary file` | 大请求体写入 Nginx 临时文件的告警 | 请求返回 200 时不是失败；检查容器磁盘空间和请求体大小 |
| `FatalError: Termination signal` 后再次启动成功 | 人工重启产生的 SIGTERM | 以随后出现启动成功日志且接口返回 200 为准 |

常用排查命令：

```bash
docker ps -a --filter name=ocr
docker logs --tail 200 ocr-v6-amd
docker logs --tail 200 ocr-nginx
docker inspect ocr-v6-amd --format '{{json .HostConfig.DeviceRequests}}'
ss -lntp | grep ':8866'
nvidia-smi
```

## 8. 性能与每卡实例数

RTX 3090 单容器固定图片压测的已验证推荐值为：

- `recognition_batch_size = 4`；
- `max_concurrency = 1`；
- 客户端并发 `2`；
- `13.468 QPS`，P95 `152.716 ms`，100% 成功且无 HTTP 5xx。

Docker Nginx 真机验证使用两张 RTX 4090 D、每卡两个 OCR 容器、客户端并发 8、40 个
计量请求，得到 `39.259 QPS`、P95 `249.816 ms`、100% 成功。停止一个后端后再次执行
40 个请求仍为 100% 成功、HTTP 5xx 为 0，并观察到 `502, 200` 的重试链。该短测用于
证明容器拓扑、分流和故障转移可行，不是生产容量承诺，也不能直接与 RTX 3090 报告对比。

每张卡可以启动多个 OCR 容器，但同卡多实例会争用计算和显存，不保证比单实例更快。
生产必须从每卡 1 个实例开始，使用真实业务图片记录 QPS、P95、错误率和显存，再逐个增加
实例。出现 OOM、5xx、结果异常或 P95 明显恶化时立即回退上一个实例数。

压测示例：

```bash
python3 /opt/ocr-v6/load_test.py \
  --ip 127.0.0.1 \
  --port 8866 \
  --image /opt/ocr-v6/ocr-test.jpg \
  --concurrency 8 \
  --warmup 10 \
  --requests 100 \
  --output /opt/ocr-v6/load-test-result.json
```

## 9. 日常运维

```bash
docker ps --filter name=ocr
docker stats --no-stream
docker logs --tail 200 ocr-nginx
nvidia-smi
curl --fail http://127.0.0.1:8866/ocr/getVersion
```

多实例维护时一次只重启一个后端，并在操作后检查统一入口：

```bash
docker restart ocr-v6-gpu0-1
docker logs --tail 100 ocr-v6-gpu0-1
curl --fail http://127.0.0.1:8866/ocr/getVersion
```

验证 Nginx 故障转移时可停止一个后端，确认统一入口仍正常后再恢复：

```bash
docker stop ocr-v6-gpu0-1
curl --fail http://127.0.0.1:8866/ocr/getVersion
docker start ocr-v6-gpu0-1
```

## 10. 升级与回滚

升级前保留当前 tar、摘要、配置和镜像 ID，不要覆盖唯一可用的回滚包。多实例采用逐容器
升级：一次停止一个后端，加载新镜像并以原 GPU、端口、配置重新启动，验收通过后再处理
下一个。最后重载 Nginx 并执行真实 OCR 压测。

回滚时加载保留的旧 tar，按第 4 节原 GPU 和端口逐个恢复容器。单实例回滚命令为：

```bash
docker stop ocr-v6-amd
docker rm ocr-v6-amd
docker load -i /opt/ocr-v6/ocr_v6_amd.tar
docker run -d \
  --name ocr-v6-amd \
  --restart unless-stopped \
  --gpus '"device=2"' \
  -e REQUIRE_GPU=true \
  -p 8866:8866 \
  -v "/opt/ocr-v6/config.toml:/app/config.toml:ro" \
  --log-driver json-file --log-opt max-size=100m --log-opt max-file=3 \
  ocr:v6_amd
```

## 11. 停止与清理

单实例：

```bash
docker stop ocr-v6-amd
docker rm ocr-v6-amd
```

多实例与 Nginx：

```bash
docker rm -f ocr-nginx
docker rm -f ocr-v6-gpu0-1 ocr-v6-gpu0-2 ocr-v6-gpu1-1 ocr-v6-gpu1-2
```

停止或删除容器不会删除 tar、配置和镜像。不得执行 `docker system prune`，不得删除
`ocr_v6_amd.tar`，也不得删除服务器上任何 `algorithm*` 镜像。
