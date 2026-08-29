# PPT Slice Docker 部署

所有命令都从 `ppt_slice/` 项目根目录执行，Docker 构建上下文必须为项目根目录 `.`。PPT
Slice 是 CPU 算子，容器不需要 GPU 参数。配置文件和共享结果目录必须在运行时挂载。

## 构建镜像

```bash
docker build \
  -f docker/Dockerfile \
  -t algorithm-ppt-slice:local \
  .
```

## 准备运行目录

```text
/opt/algorithm-operators/ppt_slice/
├── config.toml
└── result/
```

部署配置的 `[paths].result_root` 应设置为 `/data/result`。如果任务使用绝对本地视频路径，
还必须把对应宿主机课程目录以相同容器路径挂载；远程 HTTP/HTTPS 视频不需要课程目录挂载。

## 启动容器

```bash
docker run -d \
  --name ppt-slice-cpu0 \
  --restart unless-stopped \
  -p 9001:9001 \
  -v /opt/algorithm-operators/ppt_slice/config.toml:/workspace/config.toml:ro \
  -v /opt/algorithm-operators/ppt_slice/result:/data/result \
  -e CONFIG_PATH=/workspace/config.toml \
  -e RESULT_ROOT=/data/result \
  algorithm-ppt-slice:local
```

接入调度平台时，还要加入平台网络并提供实例注册信息：

```bash
docker run -d \
  --name ppt-slice-cpu0 \
  --restart unless-stopped \
  --network algorithm-platform \
  -p 127.0.0.1:19001:9001 \
  -v /opt/algorithm-operators/ppt_slice/config.toml:/workspace/config.toml:ro \
  -v /data/course:/data/course \
  -v /data/result:/data/result \
  -e CONFIG_PATH=/workspace/config.toml \
  -e RESULT_ROOT=/data/result \
  -e UVICORN_WORKERS=1 \
  -e PLATFORM_INSTANCE_ID=ppt-slice-cpu0 \
  -e PLATFORM_SERVICE_URL=http://ppt-slice-cpu0:9001 \
  -e PLATFORM_OPERATOR_REGISTRY_TOKEN=REPLACE_WITH_TOKEN \
  algorithm-ppt-slice:local
```

平台模式使用的 TOML 需要启用注册，并把 `platform.control_service_url` 指向同一 Docker
网络中的 Control Service。一个容器固定运行一个 Uvicorn worker；
`platform.max_concurrent_requests` 同时控制该实例接受的后台任务数。
