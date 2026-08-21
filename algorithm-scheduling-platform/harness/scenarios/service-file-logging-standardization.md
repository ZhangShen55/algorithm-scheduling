# 服务文件日志标准化场景

## 基线

- 变更开始 SHA：`778515596b42123a3061daeb9a1c3bb446f1de1b`
- 目标范围：7 个当前算子（`asr_offline`、`asr_online`、`facerec`、`ocr`、`screen_det`、`ppt_slice`、`vbas`）和 4 个平台服务。
- 明确排除：`text_analysis/`。开始时该目录包含用户已有 dirty 修改；非缓存文件数为 101，快照摘要为 `4ed6f26dcd3bd657b970133ea26d5e330da43b5fc7a1693216f4e613a05e9baf`。实现期间不得修改或删除它。
- 当前差异：`asr_online` 使用普通 `FileHandler`；`asr_offline` 使用按日轮转；图像算子和 PPT 各自实现大小轮转；平台共享 logger 仅输出结构化 stdout。

## 目标合同

- 项目根默认创建 `logs/{instance_id}/application.log`。
- 单个文件默认最大 `100 MiB`，归档默认保留 `7` 天。
- 文件日志与 stdout 同时输出，JSON Lines 至少包含服务、实例、级别、事件和链路标识。
- 不挂载日志卷时，容器内日志仍可查看；启用宿主机挂载时才提供跨容器重建持久化。
- Base64、媒体字节、Token、密码、完整请求体、完整 ASR/OCR 文本和 embedding 不得进入日志。
- 仅对轮转、清理、脱敏、handler 去重和实例目录边界补充必要中文原因注释。

## 验证命令

```bash
python -m pytest -q algorithm-scheduling-platform/tests/test_operator_logging.py
python -m pytest -q \
  algorithm-scheduling-platform/tests/integration/test_container_logging_modes.py
python -m compileall -q algorithm-scheduling-platform/packages
PYTHONPATH=algorithm-scheduling-platform python -m pytest -q \
  algorithm-scheduling-platform/tests/test_logging_config_contract.py \
  algorithm-scheduling-platform/tests/test_platform_logging.py \
  algorithm-scheduling-platform/tests/test_sensitive_logging.py
cd algorithm-scheduling-platform && .venv/bin/ruff check packages scripts tests \
  ../control_service/app ../orchestrator_service/app \
  ../vision_orchestrator_service/app ../online_gateway_service/app
cd algorithm-scheduling-platform && MYPYPATH=. .venv/bin/mypy packages scripts \
  ../control_service/app ../orchestrator_service/app \
  ../vision_orchestrator_service/app ../online_gateway_service/app
openspec validate standardize-service-file-logging --strict
```

后续接入 11 个项目后，再补充各项目环境中的导入、健康、真实推理、容器内无挂载模式和代表性宿主机挂载模式证据。远端构建必须等待 `retire-text-analysis-from-scheduling-platform` 本地阶段完成，并使用两个变更同一最终 SHA。

## 证据边界

本场景只证明日志行为，不替代算子推理、任务状态机、Kafka、租约、视觉聚合或在线网关业务验收。旧 release 的日志不作为新范围通过证据。

## 注释复审清单

- [x] 共享轮转、过期清理、实例目录边界和单条事件截断处说明约束原因。
- [x] Uvicorn handler 去重、stdout/file 初始化失败顺序和上下文字段允许列表处说明约束原因。
- [x] 本次改动未向 `vendor`、模型、推理、接口或 `text_analysis/` 批量增加注释。
- [x] `check_sensitive_logging.py` 只做日志参数 AST 检查，不通过修改业务控制流规避敏感字段。

## 2026-08-21 本地实施记录

- 共享 `algorithm-operator-registry-client==0.2.0` 已加入 `logging.py`；日志实现使用
  `timezone.utc`，兼容 Python 3.10 和 3.11。
- 提交前重新执行日志专项、平台基础/日志、敏感日志、11 项配置和 Compose/Docker 聚焦回归，
  结果为 `107 passed`；算子侧 ASR Offline 运行配置 `4/4`、OCR 完整套件 `175 passed`、
  FaceRec 日志配置 `1 passed`。平台 `.venv` 的 Ruff 和 strict Mypy 分别通过
  （141 个源文件无 Mypy 错误）。
- 共享 wheel 已按七算子范围重新构建，SHA-256 为
  `ff489dc4cd207cb4903dd1679a55e202349cb908fffdeb7ced12069b9ee869c8`；构建和分发未包含
  `text_analysis`。wheel 在 Python 3.11.13（`asr`）和 Python 3.10.19（`facerecapi`）
  隔离目录安装导入通过，两个环境 `pip check` 通过。
- `docker compose config --quiet` 已在临时非敏感 `OPERATOR_REGISTRY_TOKEN` 下分别验证平台、算子及日志
  override 展开；默认 Compose 不挂载日志，显式 override 才挂载每实例宿主机目录。
- `deploy/scripts/preflight host` 已增加可选 `LOG_ROOT` 分支；未设置时跳过宿主机日志目录，设置后
  才创建并校验绝对路径、非符号链接、当前身份归属、可写性和最小剩余空间。部署合同与配置测试
  `35 passed`，覆盖目录创建和符号链接拒绝。
- 静态敏感日志检查已从源码字符串匹配收敛为日志参数 AST 检查，允许 `len(audio_bytes)` 等只记录大小的
  表达式，拒绝直接传入请求、媒体、Base64、PCM、凭据或 embedding 对象；当前结果为 `PASS`。
- 本阶段没有修改 `text_analysis/`，也没有执行其镜像构建、注册或部署；其历史 dirty 状态属于独立退役变更。
- 11 个项目的完整模型推理、真实进程轮转/重建和 `192.168.29.11` 远端构建尚未在本记录中宣称通过；
  必须等 `retire-text-analysis-from-scheduling-platform` 本地完成后，使用两个变更相同的最终 Git SHA 一次执行。
- 使用本机已缓存的 `alpine:3.20` 执行两种真实容器日志模式测试：无挂载时容器可在
  `logs/{instance_id}` 创建并读取日志；绑定临时宿主机实例目录后，删除首个容器并启动替代
  容器，旧活动日志和归档均保留，新事件由容器内 shell 与宿主机路径读取一致。该专项及现有
  日志配置合同共 `6 passed`，没有构建业务镜像、访问 `/data` 或遗留测试容器。
- 使用 11 个独立 Python 进程和隔离的临时实例目录完成小阈值轮转专项，逐项目验证写入前轮转、
  一日过期清理、未过期归档保留和目录隔离，结果为
  `{"processes": 11, "status": "PASS"}`。该结果只覆盖日志进程行为；七算子真实模型推理及
  HTTP/WebSocket 全链路敏感哨兵仍须在远端同一最终 SHA 下完成。

## 2026-08-21 远端 clean-clone 根配置缺口

- 七算子 retirement Attempt 3 在镜像构建前的 clean-clone 全量测试中发现
  `facerec/config.toml` 不存在；本机此前因 `.gitignore` 下的实际文件存在而错误通过。
- 同一审计确认 `ocr/config.toml` 也处于相同的 ignore 边界。修复把两份非敏感根默认配置纳入
  Git，继续排除 `text_analysis/config.toml`，并增加 11 份目标根配置必须被 Git 跟踪的门禁。
- 失败结果为 `1 failed, 2733 passed, 8 skipped`，Canonical 随即输出 `restore: complete`；
  尚未构建镜像、启动算子或提交课程任务。该 SHA 只作为 clean-clone 缺口证据。
