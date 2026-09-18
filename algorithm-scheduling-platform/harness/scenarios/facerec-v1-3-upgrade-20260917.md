# FaceRec v1.3 多人脸升级与正式替换验证（2026-09-17 至 2026-09-18）

## 目标与边界

- 以上游 `FaceRec.git` 的 `v1.3` 固定提交
  `980e81b7a0bf9bffc07cd5c338a3f91eef1a019d` 为算法基线，引入 InsightFace
  `buffalo_l` 和单图多人脸能力。
- 保持 `app.main:app`、既有 HTTP 字段、MongoDB 人员事实源、平台注册、容量、日志和
  `save_person_photo=false` 合同；不引入算子直连 Redis、ROI 请求字段或视频拉流。
- 本地完成源码与 CPU 门禁；平台、三卡 GPU、真实租约和最终清理只在 `192.168.29.11` 验收。
- 最终目录、repository、Compose service 和实例统一使用 `facerec` 正式名称；版本只进入 tag、
  revision 和历史证据。

## 代码、模型与本地门禁

- 隔离候选提交为 `9a455a1707d4cab1b19179c5a31142b84872f83a`；最终目录替换提交为
  `37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36`，均已推送到远端分支。
- `facerec_v1.3/` 已改名为唯一正式 `facerec/`；不存在嵌套 `.git`、`.msc` 或 gitlink。
- 最终目录七个模型与 `/Volumes/Data55/算子功能部署/facerec/ai_models` 逐文件一致，模型继续
  由 Git 忽略。真实 fixture SHA-256 为
  `8cdb30362c5d257af50c190d17fee61ab179cd85dc8723a2d474d1542904b1d5`。
- 最终路径 `compileall`、导入与 `pip check` 通过；全量测试为
  `81 passed, 2 skipped, 1 warning in 39.49s`，真实单人/多人推理为
  `1 passed in 13.48s`；实现阶段平台聚焦回归为 `64 passed`，收口按当前 Harness 命令复跑为
  `72 passed in 3.19s`。
- Uvicorn 启动、关闭和 OpenAPI 对比通过；`POST /recognize` 请求字段仍为
  `photo/targets/threshold`，响应字段仍为 `data/message/status_code`。

## 远端候选门禁

- 候选镜像 `algorithm-facerec:candidate-9a455a1` 的原镜像 ID 为
  `sha256:5d95e67096b1811cabba6a2393a96420e3d4b3b94cc63f53b0d5c71429cbf355`。
- 隔离端口 `18013/28013/38013` 的三实例均为 healthy，ArcFace 使用 FastDeploy GPU，
  InsightFace 五个 session 的首选 provider 均为 `CUDAExecutionProvider`。
- 三实例以 `facerec-candidate-gpu0/1/2` 注册为 `ONLINE`，`model_ready=true`，声明容量均为
  `128`；真实租约、隔离 Online Gateway、共享 MongoDB 新增/更新/删除和停止恢复通过。
- 最终 SHA 镜像在替换正式实例前重新部署到隔离环境；Gateway 录入返回空 `photo_path`，三卡
  真实识别命中，删除后三实例均不可再查询该测试人员。候选 12,778 行日志为有效 JSON Lines，
  敏感模式命中为 0，媒体文件为 0。

## 最终缓存构建

服务器使用独立 checkout
`/root/workspace/algorithm-scheduling-facerec-37d8e1e` 构建最终提交，没有使用服务器既有脏
checkout。构建未传 `--no-cache`，未执行 builder、buildx 或 system prune。

| 项目 | 结果 |
| --- | --- |
| 镜像 tag | `algorithm-facerec:v1.3_37d8e1e` |
| 镜像 ID | `sha256:68e3f880d7ec340095f899ad41e7dd8e9c53827d42b8a5417b3f7e355a7cc88c` |
| OCI revision | `37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36` |
| build 退出码 | `0` |
| `pip check` | `No broken requirements found` |
| BuildKit cache | 构建前 `96.72GB`，清理后 `109.2GB` |
| 清理后根盘可用 | 约 `155GB` |

七个镜像内模型均非空并逐文件记录 SHA-256。首次容器内依赖检查误用了镜像不存在的
`python` 命令，得到 OCI exec 失败；改用镜像实际提供的 `python3 -m pip check` 后通过，
该命令错误不作为产品失败。

## 正式滚动替换

替换前 dry-run 确认旧容器、Compose 身份、端口、镜像和 revision 均未漂移。gpu1、gpu2
先通过算子 `/ops/drain` 与带管理令牌的 Control Service drain，确认活动租约和 inflight 为 0，
再按完整 ID 停止、删除和启动新实例；新注册保留 `DRAINING` 时显式恢复为 `ONLINE`。

| 实例 | 被替换旧容器完整 ID | 新容器完整 ID | 物理 GPU | 结果 |
| --- | --- | --- | --- | --- |
| `facerec-gpu0` | `bcafafb42cc07f50e6c11ff4b00992de733c4a161129a81464ccab04af56d84e` | `005213f722702b0ebca12699dea895af58866da41ea4efc272a20fd111edd842` | `0` | healthy / ONLINE |
| `facerec-gpu1` | `047d4ce7edb8bdad04cc94022f13974b3cef879b1d55e10402c5540d7371a685` | `a50d6874f9c20cd13c1d2a97d989949f5bd3671f272f7f8444ca1842f4079bf1` | `1` | healthy / ONLINE |
| `facerec-gpu2` | `ad57a8176305be66ffadf05b3b4301eabb4f4b8431f549a5ea9b6652505ff860` | `865ead3a520ec448e83a9aa8bb09d2064430c709b6bec686db493e7f137d2539` | `2` | healthy / ONLINE |

gpu0 首次切换时，Control drain 因漏传管理令牌返回 HTTP 401，随后 Compose 启动命令又因漏传
`-f docker-compose.operators.yml` 没有找到配置文件。旧 gpu0 已按完整 ID 正常停止并删除，
但新容器未在该命令中创建；紧接着使用明确 Compose 文件恢复，新容器 healthy、注册和真实推理
均通过，gpu1/gpu2 在此期间持续服务。后续两实例使用修正后的鉴权和 Compose 命令完成滚动。

## 最终平台验收

- 三实例均使用最终镜像 ID，Compose project 为 `algorithm-operators`，service 为
  `facerec-gpu0/1/2`，端口保持 `18003/28003/38003`。
- Control Service 中三实例均为 `ONLINE`、`model_ready=true`、inflight 0、声明容量 128，
  capability 保持 `recognize`。
- 三实例 `/ops/health` 均为 healthy，MongoDB、ArcFace 和 InsightFace detector 均为 `up`；
  InsightFace 五个 session 首选 provider 全部为 `CUDAExecutionProvider`，无 CPU 回退。
- 正式 Online Gateway 完成人员录入、查询、真实租约识别和删除。录入返回空 `photo_path`；
  三实例均真实命中共享 MongoDB 记录，删除后三实例均不可再查询该人员。
- 清理后再次对三实例和 Gateway 执行真实图片推理，三个直连请求约为
  `100-115ms`，Gateway 租约请求约为 `203ms`，HTTP 和算子业务状态均为 200。
- 三实例容器内媒体文件数均为 0，日志敏感模式命中为 0；宿主机 `jq` 对全部日志执行
  JSON Lines 校验均通过。最初尝试在镜像内调用 `jq` 因镜像未安装该工具返回 1，随后改由
  宿主机解析相同日志，不把工具缺失记为日志格式失败。

## 精确清理与保留项

- dry-run 中唯一被当前正式容器引用的旧镜像为
  `sha256:49352c58c2a3cc39b3c37a52d7d6dbb8951de3db45c5661835a9a2dbc689d5cd`，revision 为
  `d19e5e46b9cb0c78d775727e1cf33a75a4321df8`；三个旧容器删除后该镜像按完整 ID 删除。
- 无引用的隔离候选镜像 `sha256:5d95e67096b1811cabba6a2393a96420e3d4b3b94cc63f53b0d5c71429cbf355`
  与候选 tag 已删除；隔离候选三个算子容器、Control 和 Gateway 共五个容器均按完整 ID删除。
- 候选目录、临时 Git bundle 和专用 wheel HTTP 服务已删除；最终 release checkout 因承载正式
  Compose 配置和受控证据而保留，`facerec-wheel-cache` 与 BuildKit cache 保留。
- 清理前后 Docker volume 清单摘要均为
  `2ae7a8fed0274b8efc0f28c657eb7cc530f6809ea9dcd14e70d09e9d52ca8c56`；排除本次 FaceRec
  和候选容器后的无关容器清单摘要均为
  `c7964304c90f8f7c5537f9f20326969d8146ca2fee9b219d56dc6d716219ea98`。
- 历史未引用的 `candidate-b52a680`、`v1.0_260826`、`v1.0_260825` 镜像不在本次 dry-run，
  作为既有回滚资产保留；未执行宽泛旧镜像清理。

## 受控原始证据

权威远端证据根：

```text
/root/workspace/algorithm-scheduling-facerec-37d8e1e/algorithm-scheduling-platform/deploy/reports/facerec-v1-3-upgrade-20260917/releases/37d8e1ecb0972e0e7c803c4a3277a0738b3f2a36
```

所有文件均为 `0600`、硬链接数 1；`manifest.sha256` 与
`manifest-supplemental.sha256` 校验通过，凭据、Base64、测试人员字段和长编码串脱敏扫描通过。
首份 `40-real-inference.json` 的 fixture SHA 使用了错误的手工转录值。为遵守 write-once，原文件
未删除、未覆盖；`41-real-inference-correction.json` 明确记录差异，权威结果为重新执行的
`42-real-inference-rerun.json`，其 fixture SHA 与实际文件一致且四条推理均为 PASS。

## 结论与限制

FaceRec v1.3 已在最终目录和 `192.168.29.11` 正式三卡平台完成升级、真实推理、平台注册、
Gateway 租约、共享 MongoDB、无落图、日志和清理后 Smoke。HTTP 合同与正式命名保持兼容，
旧正式资产已精确删除，缓存、卷、中间件和无关业务资产未变。

本次没有执行新的峰值压测；`max_concurrent_requests=128` 沿用隔离候选的真实租约、drain 和
重启恢复证据，不将该声明扩大解释为新增吞吐基准。历史未引用镜像也未纳入本 change 的删除范围。

## 2026-09-18 GPU 进程名增量收口

升级后宿主机 `nvidia-smi` 将 FaceRec ArcFace 主进程显示为 `facerec`，但把同一容器内的
InsightFace spawn worker 显示为 `/run/operator-python/facerec`。PID `3551110` 与 `3551304`
的 PPID、完整命令行、cgroup 和容器完整 ID 证明二者均属于 gpu0 容器
`005213f722702b0ebca12699dea895af58866da41ea4efc272a20fd111edd842`；这不是重复部署，而是
子进程继承了解析后的绝对 `sys.executable`。

实现提交 `48070e6a12d84f1f716a1d8b24fa9af22e844ee1` 移除 `/run/operator-python` 软链接，
让同一入口以 `exec -a facerec` 启动 Uvicorn 和 `multiprocessing` spawn 子进程；测试修正提交
`cc3c78dbcd608f77461d7d39bc5c5c6d156d6276` 将 Linux `/proc` 动态断言改为有界轮询，避免在
shebang 的 `/usr/bin/env` 过渡阶段误判。macOS FaceRec 全量仍为 `81 passed, 2 skipped,
1 warning`，平台入口与 Harness consistency 为 `14 passed, 1 skipped`；目标 Linux 入口测试
为 `10 passed`，真实 spawn 的最终 `argv[0]` 为 `facerec`。

`192.168.29.11` 以 `--network host` 和原 wheel cache 参数复用 BuildKit cache 构建
`algorithm-facerec:v1.3_cc3c78d`，镜像 ID 为
`sha256:482f4c48fb3b511b165f17609df5d3199f95c1e127048d772b80ffb59daca119`，revision 为
`cc3c78dbcd608f77461d7d39bc5c5c6d156d6276`。第一次构建遗漏 `--network host`，依赖层因
本机 wheel 服务不可达失败且未产出镜像；修正后依赖层显示 `CACHED`。未使用 `--no-cache`，
未执行任何 prune；BuildKit private cache 由 `109.2GB` 增至 `109.9GB`。

| 实例 | 新容器完整 ID | 主进程 PID | 检测 worker PID | GPU UUID |
| --- | --- | ---: | ---: | --- |
| `facerec-gpu0` | `f5dd3e37646c753474848b1b8bd9deaf850c75729d11f28f572d4fc079111fd1` | `2886129` | `2886313` | `GPU-812c493d-596f-a8f1-cebf-675b05a311e6` |
| `facerec-gpu1` | `426fa6ecf744cd0db1a78421312cab1d91d1bece662c5e2b0d7af209b2d31d3b` | `2894314` | `2894619` | `GPU-a9286375-8b8a-0e46-e81c-80a27257368b` |
| `facerec-gpu2` | `fe96dee6773182042b1bf96876547cdf56febbdf90ff6f0e99e34a6060d10aa4` | `2902643` | `2902899` | `GPU-8c6b7efd-2b4f-04f3-db71-b50d2482fc49` |

六个 CUDA PID 在 `nvidia-smi` 中均精确显示 `facerec`，各自 cgroup 完整 ID 与上表容器一致。
三实例均为 healthy、`ONLINE`、`model_ready=true`、容量 128；真实三实例识别和 Online
Gateway 租约识别均为 HTTP/算子业务 200，`has_face=true`。Harness `INF-FACEREC` 再次完成
新增、跨实例识别、共享 MongoDB 查询和删除，`save_person_photo=false` 保持生效。

滚动替换自动删除的旧容器完整 ID 为 `005213...edd842`、`a50d68...079bf1`、
`865ead...7d2539`；零引用旧镜像
`sha256:68e3f880d7ec340095f899ad41e7dd8e9c53827d42b8a5417b3f7e355a7cc88c` 随后按完整 ID
删除。卷摘要仍为 `2ae7a8fed0274b8efc0f28c657eb7cc530f6809ea9dcd14e70d09e9d52ca8c56`；模型、
中间件、其他算子、无关镜像和 cache 均保留，临时 wheel 服务、bundle 和日志已精确删除。

本次受控证据根为：

```text
/root/workspace/algorithm-scheduling-facerec-48070e6/algorithm-scheduling-platform/deploy/reports/facerec-process-name-20260918/milestone-2b/releases/v1.3_cc3c78d/cc3c78dbcd608f77461d7d39bc5c5c6d156d6276
```

`manifest.sha256` 校验全部通过；8 个证据文件均为 `0600`、硬链接数 1，凭据、私钥和长编码串
扫描为 0。证据仅保存镜像/容器/PID、状态与推理摘要，不保存 Base64、人员字段或完整响应。
