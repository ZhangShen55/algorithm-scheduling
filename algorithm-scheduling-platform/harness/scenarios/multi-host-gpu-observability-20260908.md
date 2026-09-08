# 多主机 GPU 观测发布与验收（2026-09-08）

## 范围与结论

- 本记录对应 OpenSpec `support-multi-host-gpu-observability`，初始基线为 `12aea48`；在
  benchmark 提交 `9927df6` 后继续完成真实远程算子验收，目标主机为 `192.168.29.11` 和
  `192.168.29.12`。
- 两台主机各运行一个只读 NVML GPU Exporter，均显式返回自身固定内网 IP；控制台并行展示
  两台主机的 6 张 GPU，并仅以 `host_id + gpu_index` 关联算子实例。
- `192.168.29.11` 的 21 个现役算子保持 ONLINE；`192.168.29.12` 的 GPU 0 新增 VBas、
  GPU 1 新增 ScreenDet，最终 Control Service 返回 23/23 ONLINE，GPU 2 继续由既有 Qwen 独占。
- Control Service 仅增加两个精确 `trusted_service_urls`，没有修改业务源码。Online Gateway、
  A 服务接口、算子 HTTP/WebSocket 契约、Kafka envelope、数据库结构、Redis Key 和容量租约
  语义均未改变。
- 生产 NFS、远程注册、真实租约、跨机调用、输入读取、结果回写与页面 GPU 关联均已实测，
  OpenSpec 任务 7.4 已补齐。

## 本地门禁

执行并通过：

```bash
cd algorithm-scheduling-ops-console
npm test -- --run
npm run build

cd ../algorithm-scheduling-platform
GPU_EXPORTER_HOST_ID=192.168.29.11 .venv/bin/pytest -q \
  ../gpu_metrics_exporter/test_app.py \
  tests/test_operator_registry_client.py \
  tests/test_milestone_2b_compose.py \
  -k 'not compose_declares_exact_three_gpu_and_three_cpu_operator_matrix'
.venv/bin/ruff check ../gpu_metrics_exporter/app.py \
  packages/operator_registry_client/runtime.py \
  tests/test_operator_registry_client.py tests/test_milestone_2b_compose.py \
  deploy/scripts/multi_host_preflight.py
.venv/bin/python -m compileall -q ../gpu_metrics_exporter \
  packages/operator_registry_client deploy/scripts/multi_host_preflight.py
openspec validate support-multi-host-gpu-observability --strict
git diff --check
```

结果为前端 `18 passed`、生产构建通过；Exporter、注册客户端和 Compose 专项
`34 passed, 1 deselected`；Ruff、compileall、OpenSpec strict 和 diff 检查通过。

被排除的旧矩阵用例仍断言 VBas 配置存在 `TIAS.MaxConcurrentBatches=1024`，而 benchmark
已将权威配置改为 `MaxConcurrentOfflineBatches=2`。其 `KeyError` 在本变更前已存在，不回退
benchmark 配置；本次新增的 21 实例标签专项测试独立通过。

初始临时目录预检只证明工具行为。后续已在真实 NFS 挂载上再次通过创建、写入、读取、fsync、
原子 rename、删除、跨机可见性与 `/data/result` 清理保护检查。

真实远程算子阶段追加执行并通过：Control Service 全量 `25 passed`；注册客户端与 Compose 专项
`34 passed`；多机 Compose 渲染、OpenSpec strict 与 `git diff --check` 通过。

## 远端发布身份

### 192.168.29.11

| 组件 | 镜像 | 镜像 ID | 容器 ID | 状态 |
| --- | --- | --- | --- | --- |
| Ops Console | `algorithm-scheduling/ops-console:v0.9_260908_multi_host` | `sha256:e3d8bdbc390ffa58466d1f2f4ac1f837515a7de404ef42a99d648f048fd04a76` | `1583e859b9cf5479e25b67628d363dc093e347b10a3c5d556b99160ffebefe8d` | healthy |
| GPU Exporter | `algorithm-scheduling/gpu-metrics-exporter:v0.2_260907_multi_host` | `sha256:1eb7b5432e536680597658169e78b893ba9ee09a09f89b5fdc9b1d3a812833dc` | `599c3429f6c4a85a8a64af400f2ba59c13aa79c104fe7acbfcecd24d7e4b1e05` | running |

算子统一发布目录为
`/root/workspace/releases/multi-host-gpu-20260907/algorithm-scheduling-platform/deploy`。
按 `gpu0 -> gpu1 -> gpu2 -> cpu` 分组强制重建，每组全部 healthy 后才进入下一组。镜像保持为：

- ASR Offline/Online、FaceRec、OCR、ScreenDet、PPT Slice：各自 `v1.0_260827`；
- VBas：`algorithm-vbas:v1.0_260831`；其 benchmark 后心跳 `1s`、离线批并发 `2` 配置从
  运行 release 原样复制并校验摘要，没有被本地旧配置覆盖。

三个历史 Text Analysis orphan 只产生 Compose 提示，未启动、删除或纳入当前平台。
注册令牌未输出或写入仓库/Harness；真实远程算子阶段只在 `.12` 的 root 专用配置目录保存
`0600` 环境文件供 Compose 使用。

### 192.168.29.12

GPU Exporter 容器为 `algorithm-gpu-metrics-exporter-29-12`，镜像
`algorithm-gpu-metrics-exporter:multi-host-20260907`，镜像 ID
`sha256:70e975193c426dbe2b7e1397309df2aa2e80d8dfa9b59f6be054a82235def1e6`，容器 ID
`5a6e299cb8125d71e78f20cdf68267b91b445449552cd715cc4c5e53b7ed7e98`。

该主机 Docker NAT 的 `DOCKER` iptables 链缺失，普通端口发布失败。为避免重启 Docker 影响
既有 `fileserver:5555` 和 `campaign-slow-media:5556`，Exporter 使用 host 网络监听 `9400`；
两个媒体容器在发布、故障注入和恢复后始终为 running。Exporter 未挂载 Docker Socket。

真实算子补充阶段的最终运行身份如下，两个算子同样使用 host 网络以绕开异常 NAT 链：

| 实例 | GPU | 监听地址 | 镜像 ID | 容器 ID | 状态 |
| --- | --- | --- | --- | --- | --- |
| `vbas-29-12-gpu0` | 0 | `192.168.29.12:28981` | `sha256:c3261f111088249c387e5cc2ed47ac781c136fbac5dd139aae8339cfe1062c68` | `ecce3747c0b21820336a5fe15b495893990eb0544143db5798a8810434de086b` | healthy |
| `screen-det-29-12-gpu1` | 1 | `192.168.29.12:28880` | `sha256:268c849235a2d101c967b968e41c394915ef8dcece68da6929e728943b65ac10` | `ebc75218daeb1ae35001be10b8fbdec28a9957c6917aeb938c31512647a2b128` | healthy |

## 接口与注册证据

- `GET 192.168.29.11:9400/health` 返回 `host_id=192.168.29.11`，`/gpu` 返回两张
  RTX 4090 D 和一张 RTX 3090。
- `GET 192.168.29.12:9400/health` 返回 `host_id=192.168.29.12`，`/gpu` 返回三张
  RTX 4090；两台主机均存在 GPU 0/1/2，但身份不冲突。
- 从局域网工作站运行 `multi_host_preflight.py endpoints` 同时检查两个 `/gpu`，结果
  `status=ok` 且响应身份分别与配置 IP 一致。
- 两个 `/gpu` 对控制台 Origin 的 OPTIONS 均返回 HTTP 204、
  `Access-Control-Allow-Origin: *` 和 `GET, OPTIONS`。
- Control Service `/ops/operator-instances` 最终返回 23 个 ONLINE 实例；`.11` 的 21 个实例
  保持原标签，新增实例分别上报 `192.168.29.12+0` 和 `192.168.29.12+1`。两个注册地址与
  `trusted_service_urls` 精确一致。
- Vision Orchestrator 容器成功访问远端 VBas 的 `/ops/health` 与 `/ops/metadata`；Online
  Gateway 容器成功访问远端 VBas 和 ScreenDet，证明跨机地址不依赖单机 Docker DNS。
- Online Gateway 对 `detect_all` 的真实请求取得 `screen-det-29-12-gpu1` 租约并在约 350 ms
  完成，租约 acquired/released 和请求延迟指标均带该实例 ID。
- Online Gateway 对 `student_behavior` 的四次轮转请求中两次取得 `vbas-29-12-gpu0` 租约，
  两次均成功调用并释放，实例指标计数为 2。

## NFS 与真实推理

- `.11` 将本地 ext4 的 `/data/course`、`/data/result` 仅导出给 `.12`；`.12` 以 NFS 4.2
  持久挂载到相同路径。防火墙只允许来源 `192.168.29.12` 的 `nfs`、`mountd`、`rpc-bind`。
- 两端真实预检均通过 UID/GID、读写、fsync、原子 rename、删除和结果目录保护；`.11` 创建的
  course 探针可由 `.12` 校验删除，`.12` 创建的 result 探针可由 `.11` 校验删除。
- VBas 容器通过 NFS 绝对路径读取 fixture；文件 SHA-256 在 `.11`、`.12` 和容器内均为
  `3c9c37ee3a1d8b82cd3a2310871867aa41e6099bfdd7fd46d5221ddcbb61edf6`。
- 学生和教师推理均返回状态码 0 与 1 项结果，两个稳定业务路径均未变化。
- VBas 容器经写入、fsync 和原子替换生成
  `/data/result/_harness/multi-host-gpu-observability/vbas-29-12-gpu0.json`；`.11` 读取 SHA-256
  `30652949309a42bd2d3c2718c95b26d44e9b9b028177d861a19d7da080c346be`，证明结果回写可见。

## 浏览器验收

真实入口为 `http://192.168.29.11:5174/`。浏览器验证结果：

- 总览最终显示 `23/23` 在线实例和 `6 张显卡`；两台主机各三张卡独立分组。
- `.11` 每张 GPU 继续关联原有六类 GPU 算子；`.12` 的 GPU 0 关联
  `vbas-29-12-gpu0`，GPU 1 关联 `screen-det-29-12-gpu1`，GPU 2 显示“未部署算子”。
- 实例清单包含服务器和 GPU 列，新增两个实例均按显式标签显示在 `.12` 对应显卡，不解析
  `instance_id`、容器名或 URL。
- GPU 刷新设为 1 秒后连续采样 5 次，主机数始终为 2、GPU 卡数始终为 6，GPU 区域
  `top=1170`、`height=511` 均不变化；最近成功时间持续推进，没有刷新提示引起的跳动。
- 截图见 `../evidence/multi-host-gpu-observability/overview-two-hosts.png`。桌面
  `1440x2200` 画面中两主机、6 张卡、实例关联和空主机状态均可见，无文本重叠。
- 真实算子部署后的截图见
  `../evidence/multi-host-gpu-observability/overview-remote-operators.png`，SHA-256 为
  `2c1d9352e66638afa1efc3b63d3ec5ceb6acbbbe57d2a92f00883f9bbe7595d6`。

## 局部故障与恢复

受控停止 `.12` Exporter，等待超过一个总览刷新周期后：

- `.11` 仍在线并持续更新，21 个实例和网关数据保持可读；
- `.12` 显示“读取失败 · 显示最近成功数据”，三张卡的最后成功快照仍保留；
- `fileserver` 与 `campaign-slow-media` 始终 running。

重新启动 Exporter 后 `/health` 恢复，控制台约 3 秒内自动清除错误并更新最近成功时间。
该过程同时验证运行回滚/恢复；没有执行 Docker daemon 重启、prune、卷或数据清理。

## 兼容与完成边界

Control Service 只修改部署可信地址配置和对应配置测试，继续使用既有 `labels` 字段；
Online Gateway、数据库迁移目录及 A 服务契约没有改动。调度服务继续使用租约中的
`service_url`，显式 `PLATFORM_INSTANCE_LABELS` 仍优先。

OpenSpec 任务 7.4 已用真实远程算子证据完成。`.12` 的 Qwen 容器 ID
`93a844e1b2a308f588eab6fa5859f745ccf85feab3890ce0239e0cc2ebdeb9fa`、GPU 2 绑定、启动时间和
重启次数在部署前后完全一致；没有重启 Docker daemon、执行 prune、删除卷、旧数据、模型或
历史 release。
