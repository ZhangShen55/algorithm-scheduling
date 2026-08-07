# Online Gateway Service

本服务暴露现有在线 VBas、人脸识别、图像质量检测和实时 ASR 转发契约。

在本服务目录安装公共平台包和服务依赖：

```bash
python -m pip install -e ../algorithm-scheduling-platform
python -m pip install -r requirements.txt
```

The canonical entrypoint is:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1
```

配置按内置默认值、根目录 `config.toml`、`ONLINE_` 环境变量的顺序加载。
嵌套字段使用双下划线，`CONFIG_PATH` 可以指定其他 TOML 文件。

`/ready` 当前只报告进程就绪，尚未探测 control-service；本次迁移不改变 HTTP
请求转发和 WebSocket 粘性路由契约。

从工作区根目录构建镜像：

```bash
docker build -f online_gateway_service/docker/Dockerfile \
  -t online-gateway-service .
```
