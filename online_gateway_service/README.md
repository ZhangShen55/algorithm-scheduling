# Online Gateway Service

本服务暴露在线 VBas、人脸识别、图像质量检测、单图 OCR 和实时 ASR 转发契约。

内部运维控制台读取 `/metrics` 时可直接跨域访问。本服务默认提供宽松的 CORS 响应并处理 `OPTIONS` 预检，不启用浏览器凭据；生产网络应限制服务端口的可达范围。

这些在线路由属于当前七算子拓扑，不依赖 Text Analysis。退役离线关键词和课程脑图不会
改变现有 HTTP/WebSocket 路径、请求响应、无队列语义或共享容量租约。

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

## 在线路由与容量

图片请求由上游直接提供 Base64，网关不拉流、不截图、不进入 Kafka 或离线任务队列。每个同步
请求申请一个带在线上下文的算子租约；在线 ASR 在整个 WebSocket 会话期间持有并续租同一个
实例。VBas 人数、教师和学生三条在线路由在 Gateway 内等待平台在线容量，不把瞬时容量不足直接
返回上游。`[http].hard_timeout_seconds` 默认 600 秒，是从请求进入 Gateway 到响应生成的单一总预算；
图片校验、容量等待、Control 恢复、VBas 调用、退避和实例重选共同消耗该预算。A 服务客户端和
反向代理超时建议设置为 630 至 660 秒。

VBas 纯图片推理默认最多调用三次（首次加两次重试）。建连失败、连接复位、HTTP
`429/502/503/504`、读取超时和响应协议错误会先释放原租约，再在剩余预算内重新申请实例；请求
正文、`TaskID` 和 `ImageID` 保持不变。HTTP `400/422` 和本地图片/坐标校验错误不重试。调用方
取消连接时会取消容量等待或下游调用并释放已取得租约。

`[leases]` 同时配置请求/WebSocket TTL 和续租重试、安全余量。瞬时网络读取失败会对原
lease_id、原实例有限重试，不申请第二个实例；安全窗口耗尽或确认租约丢失只终止对应请求或
实时 ASR 会话。释放 404 视为已释放，瞬时释放失败不逆转已经返回的业务终态。

FaceRec 人物管理由 Online Gateway 代理以下接口：

- `POST /api/online/face/persons`
- `POST /api/online/face/persons/batch`
- `GET /api/online/face/persons`
- `POST /api/online/face/persons/search`
- `DELETE /api/online/face/persons/delete`

这五个接口将请求原样转发到 `[face_persons].base_url` 指定的同一个固定 FaceRec
管理实例；里程碑 2B Compose 中该地址为 `facerec-gpu0`。FaceRec 的
`status_code/message/data` 响应对象原样放入 `BusinessResponse.data`。人物管理不申请推理容量
租约、不进入 Kafka、也不由网关直连 MongoDB。`POST /api/online/face/recognize`
则为每个请求申请 `recognize` 租约，在三个 FaceRec 识别实例间路由；三个实例共享
MongoDB。因此人物管理能力按单实例计算，识别能力按三实例计算。人脸原图是否保存继续由
FaceRec `image.save_person_photo` 控制，当前默认值为 `false`。

里程碑 2B 三卡部署使用 `[http].max_connections=2048`、
`max_keepalive_connections=512` 和有界正数 `pool_timeout_seconds`。启动时会拒绝非正数连接上限、
超过总连接数的保活上限以及非有限或非正数的池等待超时。连接池只保证网关能够承接
千级并发；请求是否进入算子仍由 Control Service 容量租约决定。单实例注册的 online pool 容量
等于 VBas `MaxConcurrentOnlineRequests`，不包含 `MaxQueueOnlineSize`；三个实例各 24 时最多同时
持有 72 个平台在线租约，其余请求留在 Gateway 等待。等待预算耗尽才返回 `50301`，不能把连接池
或 VBas 内部队列大小当成平台注册容量。

四个在线图片入口在申请租约前校验 Base64 语法、解码后大小和图片可解码性；
完整解码在有界线程池中执行，不阻塞网关的异步事件循环。非图片 Data URI、损坏图片和超过
`[base64].max_decoded_bytes` 的请求返回业务码 `40001`，不申请算子容量。

`GET /metrics` 通过 `algorithm_operator_request_*` 给出已经调用到各实例的请求计数与耗时，
并通过 `algorithm_capacity_lease_events_total` 累计 `requested`、`acquired`、`rejected`、
`released` 和 `release_failed`。极短请求的调度证据应同时使用实例请求增量、租约事件增量和
0.5–1 秒峰值采样，不能只依赖 5 秒时点快照。

容量恢复指标 `algorithm_capacity_recovery_events_total` 按 `capacity_pool`、`capability`、
`instance_id`、`stage`、`exception_type` 和 `outcome` 区分等待、重试、重选、超时、失败与释放。
结构化日志包含 trace、尝试次数、已耗时和剩余预算，但不记录 Base64、完整请求/响应或识别结果。

VBas 在线最终失败仍使用 HTTP `200`，业务码含义为：`50301` 容量等待超时、`50302` Control 在
恢复预算内持续不可用、`50201` VBas 建连或协议类错误重试耗尽、`50401` VBas 响应超时重试耗尽、
`50000` 确定性或未分类平台错误。成功响应保持 VBas 原响应，不增加外层包装。

单图 OCR 接口为 `POST /api/online/ocr/recognize`：

```json
{
  "image_id": "frame-001",
  "image": "data:image/png;base64,...",
  "enable_formula": false
}
```

`image_id` 可省略并由网关生成，`enable_formula` 是严格布尔且默认 `false`。网关转换为 OCR
现有 `/ocr/prediction` 的单元素 `key/value` 请求，响应对象原样放入
`BusinessResponse.data`。请求正文最大 `75497472` 字节，Base64 解码后单图最大
`52428800` 字节；两项都在申请租约前执行。

里程碑 2B 当前不部署 Nginx、Ingress 或其他反向代理，A 服务直接访问宿主机 `18103` 映射的
Online Gateway，因此当前链路不存在另一个代理请求体上限。以后增加反向代理时，其请求体
上限必须不小于 `75497472` 字节，并需要把超限和边界请求纳入发布 Smoke 后才能交付。

部署后使用一张无敏感信息的真实 OCR 图片执行网关 Smoke：

```bash
algorithm-scheduling-platform/deploy/scripts/run-online-gateway-smoke \
  --image /data/course/_harness/fixtures/ocr.png \
  --release-tag "$RELEASE_TAG" --git-sha "$EXPECTED_GIT_SHA" \
  --reports-root "$REPORT_ROOT"
```

该命令同时验证正常单图 OCR、解码后超过 50 MiB 和正文超过 72 MiB 三个场景，证据只记录
状态和大小边界，不保存图片、Base64 或 OCR 文本。

从工作区根目录构建镜像：

```bash
docker build -f online_gateway_service/docker/Dockerfile \
  -t online-gateway-service .
```
# 日志

运行日志默认写入 `logs/{instance_id}/application.log`，同时输出到 stdout；单文件上限
100 MiB，归档保留 7 日。日志只记录图片/音频大小、耗时、实例和状态，不记录图像、音频、
完整转写、Token 或 Cookie。
