# Change Ledger

## 2026-08-12 - 里程碑 2B 远端部署执行记录（模型资产与镜像前置）

- 执行目标：在 `192.168.29.11` 上按里程碑 2B 计划推进三卡部署；本记录只收纳本次
  真实远端执行事实，不把本地 Harness 结果当作服务器通过。
- 已通过：服务器预检、x86_64/三卡/Docker/Compose 检查、代码提交
  `855109bf5e746f97a6caf4856b733eb9127c405e` 固定、运行容器快照、课程/结果目录准备、
  外部模型资产传输、模型 staging 和六个模型根的逐文件字节/哈希校验。
- 模型资产：受控源位于 Git 工作树外；清单生成前仅移除 staging 源中的两个明确污染文件
  `vbas/models/.DS_Store` 和 `ocr/models/manifest.sha256`，未触碰任何原始算子模型目录。
  服务器校验结果为：ASR Offline 76 个文件、ASR Online 10 个文件、OCR 13 个文件、VBas
  8 个文件、FaceRec 3 个文件、ScreenDet 4 个文件，全部 PASS。
- 未通过/未执行：八镜像尚未构建，平台/基础设施/24 个算子实例尚未启动，GPU 真实性、注册、
  Smoke、反例、压测、恢复和完整泳道均未执行。
- 阻塞根因：第一个镜像依赖的
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7` 在服务器下载最后一个大层时发生
  TLS/registry 长时间重试；两次有界预拉取均未形成完整本地镜像。`nvcr.io/v2/` API 可达，
  因此问题定位为大层传输/registry 稳定性，不是 Git、Dockerfile、模型清单或磁盘门禁失败。
- 操作边界：构建脚本在第一个镜像失败后按设计短路；未使用替代基础镜像、未执行
  `docker system prune`、未删除现有容器/数据卷、未启动不完整部署。
- 续接条件：恢复稳定的 `nvcr.io` 大层下载（或由用户提供同一 digest 的内部镜像缓存）后，
  从“预拉取基础镜像/八镜像构建”继续；构建成功后严格按
  `harness/scenarios/milestone-2b-deploy.md` 的阶段顺序执行。
- 证据位置：服务器上的运行日志按 release/SHA 归档；模型逐文件 manifest 保留在 Git 外受限
  目录，不写入仓库或报告。密码、私钥和模型原图未写入 Git、文档或命令参数。

## 2026-08-14 - 里程碑 2B 八镜像构建续接与 ASR Offline 基础环境阻塞

- 续接前置：用户已在目标服务器成功拉取
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-centos7`；远端检查确认镜像为
  `linux/amd64`，代码仍固定在 `855109bf5e746f97a6caf4856b733eb9127c405e`，工作树干净，
  六个模型根再次逐文件校验 PASS，根分区剩余约 212 GiB。
- 实际执行：重新运行八镜像构建入口；注册客户端 wheel、构建上下文门禁、模型门禁和
  ASR Offline 的 CUDA 基础镜像阶段均通过。CentOS/Conda 系统依赖安装完成后，构建在
  `requirements-pip.txt` 的 `torch==2.7.0` 解析处终止；后续七个镜像未开始。
- 表面错误：Dockerfile 将 pip 主索引固定为阿里云镜像，该镜像当前只列出到
  `torch 2.6.0`，因此报告 `No matching distribution found for torch==2.7.0`。
- 根因：官方 PyPI 的 `torch 2.7.0` Python 3.11 x86_64 wheel 为
  `manylinux_2_28`，要求 glibc 至少 2.28；当前 CentOS 7 CUDA 基础镜像只有 glibc 2.17。
  仅增加官方 PyPI 索引只能解决“找不到版本”，不能解决运行时 ABI 不兼容。
- 决策边界：未降级 Torch/Torchaudio、未强行升级容器 glibc、未切换未经确认的 CUDA/cuDNN
  组合、未继续构建后续镜像。已确认同 CUDA/cuDNN 版本的
  `nvcr.io/nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04` tag 存在；下一步需由用户准备该
  基础镜像，再以测试覆盖的最小 Dockerfile 变更将 ASR Offline 系统包管理从 yum 改为 apt。
- 当前结论：八镜像构建仍为失败；基础设施、平台、24 个算子实例及所有真实运行验收保持
  “未执行及原因”，不得宣称里程碑 2B 部署完成。

## 2026-08-12 - 里程碑 2B Task 10-11 文档与本地验收边界

- 先前状态：Task 7B-9 的构建上下文、模型资产事务、GPU 证据采集器、注册/Smoke
  和报告归档代码已经通过各自的本地行为门禁，但执行顺序、证据等级、服务器前置
  条件和真实部署未在一个场景中收敛；裸 `pytest` 可能被同名外部 `tests` 包遮蔽。
- 目标状态：新增 `harness/scenarios/milestone-2b-deploy.md`，固定从预检、容器
  快照/暂停、基础设施、模型 staging、八镜像构建、逐卡 Compose、GPU 证据、注册、
  八类 Smoke、反例/压力/恢复、恢复业务容器到报告渲染的顺序；所有命令使用当前
  CLI 和完整 Git SHA，并要求 `PYTHONPATH="$PWD:$PWD/.." .venv/bin/python -m pytest`。
- 变更文件：Task 10-11 Harness 场景、验证命令、部署 README、单机运维手册和本台账。
- 契约影响：不改变 A 面、算子 HTTP/WebSocket、Compose 端口、实例拓扑或服务边界；
  只收紧证据归档和部署操作的可复现性。
- 服务器前置：目标为 `root@192.168.29.11:22`，代码目录为
  `/root/workspace/algorithm-scheduling`，架构 x86_64、三张 NVIDIA GPU、Docker
  NVIDIA runtime、`/data/course` 与 `/data/result` 可写，并具备 PostgreSQL、Redis、
  Kafka、MongoDB。登录凭据和私钥只通过外部安全通道提供，禁止进入仓库、报告和命令历史。
- 当前证据等级：Task 7B-9 为静态/脚本行为/本地文件系统层级；里程碑 2A 为真实
  PostgreSQL/Redis/Kafka、服务运行和契约 Stub 层级。尚未取得目标服务器三卡、真实模型、
  24 实例注册、真实媒体推理或完整离线/在线泳道证据。
- OpenSpec 状态：只保留已有真实证据对应的 2.3-2.6、4.1-4.6、4.13-4.14、8.1-8.5
  勾选；7.4、7.5、4.7-4.12、视觉、在线、完整产品和真实部署任务不提前勾选。
- 剩余风险：ScreenDet 仅属于 `online-gateway-service`；PPT 真实课程视频仍需终态
  manifest/回调证据；ASR/VBas fixture 和六根模型 manifest 需要在服务器外部受控提供。

## 2026-08-12 - 里程碑 2B GPU 实例证据采集器（Task 8）

- 先前状态：设计要求为 18 个 GPU 实例留存 CUDA/PID/容器归属证据，但尚无可执行采集器，只看环境变量或空闲模型进程会产生假通过。
- 目标状态：`verify-gpu-instance` 将容器声明、容器内 `nvidia-smi` 唯一卡/UUID、按算子选择的 Torch/Paddle/FastDeploy 框架探针、真实触发存活期间的宿主 CUDA PID、进程名、`docker top`、完整 cgroup ID 和 `NSpid` 组合为单一证据链；停止模式只跟踪先前精确映射的 PID。
- 变更文件：GPU 采集 CLI、fake 运行时行为测试、部署说明、Harness 场景与验证入口。
- 契约影响：算子 HTTP/WebSocket、端口、Compose 实例数和平台调度契约不变；只新增部署验收工具。
- 验证命令与环境：macOS CPU 上用 fake `docker`、`nvidia-smi` 和 `/proc` 执行聚焦测试、Ruff、严格 Mypy 和 `py_compile`；不连接远程服务器。
- 证据层级与结论：验收工具单元/脚本行为层级符合；不计为真实 GPU、真实推理或三卡部署通过。
- 问题收纳：停止模式首版仅比对历史 PID，会把被其他容器复用的宿主 PID 误判为残留；已通过当前 cgroup 完整 ID 复核修复。规格复审又发现默认 Torch 会误杀 OCR/FaceRec、样本提交前没有二次检查 trigger、历史证据 SHA 和 UUID DeviceRequests 解析不完整；均以先 RED 后 GREEN 的回归关闭。
- 质量复审：所有 Docker/NVIDIA/框架辅助命令增加统一可配但有界的超时；触发器在独立进程组中运行，任意失败都用 SIGTERM/SIGKILL 有界回收整组。真实父进程派生长驻子进程的测试已证明超时后两者均消失。
- 剩余风险：目标驱动对 compute-apps `gpu_uuid` 查询的支持仍需真机预检；MIG 不在本阶段范围。PID 映射尚未记录 `/proc/<pid>/stat` starttime，极端快速 PID 复用仍有 TOCTOU 风险，已作为后续加强项记录。尚未勾选任何需要真实 GPU 证据的 OpenSpec 任务。

## 2026-08-12 - 离线 ASR 五何能力退役与路由模块收敛

- 先前状态：`asr_offline` 仍暴露 `POST /text/question`，请求首次触发时会加载 BERT FiveWh 模型并驻留；v1.1.7 已退役，但保留的 v1.1.8 路由源码仍命名为 `asr_v18.py`。
- 目标状态：只保留离线 ASR、音频质量和运行状态能力；删除本算子的 FiveWh HTTP 路由、请求实体、特征整理、BERT 推理与配置，将唯一 ASR 路由模块收敛为 `app/api/routes/asr.py`。独立 `text_analysis` 算子不在本次变更范围。
- 变更文件：`asr_offline` 路由装配、模型/配置/实体/工具、Docker ignore、单元合同、README/AGENTS；平台共享 ASR 配置、部署源文件合同及本 Harness。历史设计和上一条多语言账本不回写。
- 契约影响：显式删除 `POST /text/question`，HTTP 实测为 404；`POST /v1.1.8/seacraft_asr` 的路径、请求、响应、处理函数名和 OpenAPI operationId 保持不变，`speed`、`speed_info` 与 `rate_factor=0.4` 不变。
- 资源边界：删除所有 BERT/FiveWh 直接加载点，并将本机约 `393 MB` 的 `model/bert-base-chinese/` 与约 `1.1 GB` 的 `model/bert_output/` 排除出 Docker context；两个 Git 忽略的本地目录不物理删除。Paraformer、VAD、标点、CAM++、emotion2vec 和 Whisper 均保留。`transformers` 仍由 FunASR/ModelScope 音频依赖链使用，不因删除 FiveWh 而贸然移除。
- 验证命令与环境：`asr` Python 3.11.13 / macOS CPU 环境执行 `compileall`、`app.main:app` 导入、完整 `unittest`、`pip check`、平台 ASR 配置/GPU fail-fast/部署/适配器/Harness 聚焦合同，并用临时 CPU 配置冷启动真实服务。
- 真实证据：算子完整测试 `53/53`、平台聚焦合同测试 `22/22` 通过；`/ops/health` 为 HTTP 200，OpenAPI 只包含 v1.1.8 而不含三个退役路由，三个退役路由实际均为 HTTP 404。v1.1.8 对 12 秒真实中文音频返回 6 个 segment、71 字符非空文本、原有 6 个顶层字段和 1/5/10 分钟 `speed_info`，operationId 仍为 `api_asr_v18_v1_1_8_seacraft_asr_post`。
- 证据层级与结论：达到算子静态/单元合同、本机冷启动和真实 CPU HTTP 推理层级；FiveWh 退役及 v1.1.8 内部模块重命名符合。本轮未重复耗时约 9 分钟的法语推理，沿用上一条中对未改动 Whisper 响应链路的真实样本证据；未达到 GPU 容器、真实租约、Kafka 或课程 DAG 验收层级。
- 剩余风险：仓库外旧报告流水线 `/Users/zhangshen/Documents/workspace/ai报告分析课程数据/scripts/pipeline.py` 仍调用 `/audio/detect_mandarin` 和 ASR Offline `/text/question`。后者失败虽会被捕获，但报告会缺失五何结果；发布前必须迁移或确认停用。`text_analysis` 的同路径实现可作为候选，但依赖外部 LLM 且响应语义并非逐字段等价，不能只替换端口而不做报告回归。

## 2026-08-12 - 离线 ASR v1.1.8 多语言收敛与资源缩减

- 先前状态：离线 ASR 同时暴露 `v1.1.7`、`v1.1.8` 和普通话检测路由；小语种路径保留了当前调度不需要且额外占用模型资源的 Pyannote 说话人链路，v1.1.8 未承接法语 Whisper 响应合同。
- 目标状态：唯一离线转写接口收敛为 `POST /v1.1.8/seacraft_asr`；`auto/zh/en` 使用 Paraformer，只有白名单小语种 `fr` 使用 Faster-Whisper。`open_mul_lang=false` 或模型未就绪时返回 HTTP 200 / `4003`，空语言或未支持语言返回 HTTP 200 / `4009`。
- 变更文件：`asr_offline` 路由、请求实体、Whisper 并发/响应组装、模型与功能配置、requirements、Docker/Compose、合同测试和说明文档；同步更新本 Harness 账本、验证命令和算子场景。
- 契约影响：删除 `POST /v1.1.7/seacraft_asr` 和 `POST /audio/detect_mandarin`；保留 `/audio/db_snr` 和 `/text/question`。成功响应不新增顶层字段；法语请求的 `role`/`emotion` 按需返回 `null`，`segment_words` 在关闭词时间时为空数组、开启时为真实 Whisper 词时间。`rate_factor=0.4` 保留且只作用于单段 `speed`。
- 资源边界：移除 Pyannote 代码、直接依赖、配置、Docker 改写和 Compose 兼容环境变量；三个退役 Pyannote 模型目录只从镜像上下文排除，不删除本地文件。Paraformer、CAM++、emotion2vec、Whisper 和 FiveWh 均保留，FiveWh 按请求懒加载。
- 验证命令与环境：`asr` Python 3.11.13 / macOS CPU 环境执行 `compileall`、完整 `unittest`、`pip check`、平台 ASR 配置/部署/适配器聚焦合同测试、冷启动/OpenAPI/HTTP 路由验证，并对 `/Volumes/Data55/asr测试文件/法语音频.mp3` 执行真实 Faster-Whisper 推理。
- 真实证据：算子完整测试 `50/50`、平台聚焦合同测试 `20/20` 通过，两个退役路由均为 HTTP 404。`442.853878` 秒法语样本在 CPU 上约 `536.8` 秒完成，返回 140 个 segment、1063 个词时间、139 个正数 `speed` 和 8/2/1 个 1/5/10 分钟 `speed_info` 窗口；请求的 `role`/`emotion` 全为 `null`，顶层字段严格保持既有 6 项。
- 证据层级与结论：达到算子静态/单元合同、本机服务运行和真实 CPU 推理层级，未达到通过 `control-service` 真实租约调用的算子契约验证，不将其计为 Kafka、课程 DAG、GPU 容器或三卡部署完成证据。
- 剩余风险：仓库外旧报告流水线仍调用已退役的 `/audio/detect_mandarin`；发布前必须迁移该步骤或确认整条流水线已停用。

## 2026-08-12 - 里程碑 2B 模型资产与密钥边界（Task 7C）

- 先前状态：设计错误地列出七个模型目录并包含 VBas 加密目录；ScreenDet 运行读取 `model/screen.pt`、`model/occlusion.pt`，但 Dockerfile 没有复制模型且 `.dockerignore` 排除了模型；多个镜像仍可能复制本地配置或整个项目上下文。
- 目标状态：只交付六个实际明文模型根；仓库外源目录用精确 manifest 冻结全部普通文件，经过全量预校验后以锁、持久 journal、fsync、同文件系统 stage/backup 和原子重命名发布；八镜像统一使用 Compose 只读配置挂载。
- 变更文件：`deploy/model-assets.json`、模型生成/发布/验证与 runtime secret 脚本、`build-images`、八个 Dockerfile/`.dockerignore` 的必要边界、2B 设计/部署说明及行为测试。
- 契约影响：HTTP/WebSocket、算子端口、模型路径和 Compose 实例数不变。ScreenDet 明文模型现在明确进入镜像；VBas 当前镜像只含 `models`，不含 `models-encrypted` 或密钥。
- 故障证据：测试覆盖源在 worktree、符号链接、FIFO、缺失/额外、字节/hash 篡改、密钥/加密路径、缓存污染、复制阶段失败，以及 backup 后、replace 后、journal fsync 后中断恢复；目标不存在和已有旧目标两种切换均可重入。
- 构建输入补强：Git 输入门禁将矩阵中显式 `-f` Dockerfile 视为发布输入，即使 `.dockerignore` 排除该路径，未提交修改或删除仍会阻断构建。
- 配置与密钥：八个本地 `config*.toml` 不进入 context，服务器配置只由 Compose 只读挂载；runtime secret 校验只检查 ID、目标、普通文件、owner 和精确 `0600`，不读取内容、不记录 size/hash。当前明文模式不要求 secret。
- 已知风险：ASR Online 的 `.enc` 模型仍使用源码硬编码解密材料，本任务未扩大为业务模型加密改造，不能将其描述为安全密钥保护。未来 VBas/ScreenDet 加密模式必须使用独立只读 secret mount，且加密镜像不得同时内置明文权重。
- 完成边界：本任务提供资产和镜像输入门禁；尚未在目标 x86/NVIDIA 服务器完成八镜像真实构建、24 实例启动或推理，不勾选 OpenSpec 7.4。

## 2026-08-12 - 里程碑 2B 八镜像构建输入冻结（Task 7B）

- 先前状态：Compose 已引用八个版本化镜像，但没有单一的 context/Dockerfile/image 矩阵、统一构建入口或 Docker build context 机器门禁。
- 目标状态：用 `deploy/operator-images.tsv` 冻结八镜像矩阵；`build-images` 从任意目录分发 registry wheel，并按固定顺序构建、检查磁盘、附加 Git SHA label 和 inspect 终态校验；上下文门禁拒绝矩阵漂移、工作区根 context、越界 `COPY/ADD` 和常见污染输入。
- 变更文件：八镜像矩阵、`build-images`、`verify-operator-build-contexts`、八个 `.dockerignore`、行为测试和部署说明。
- 契约影响：算子 HTTP/WebSocket 契约、端口、Compose 实例数和模型目录名不变；只收紧镜像构建输入与发布标签。
- 验证命令与环境：Pytest 使用 fake Docker/df/Git 和临时工作区验证八镜像顺序、任意 cwd、失败短路、镜像引用/label inspect、磁盘门禁和上下文污染拒绝；真实工作区八个 context 门禁通过；本任务未构建大型镜像。
- 证据等级与结论：镜像构建管道单元/脚本行为及真实静态 context 门禁符合；尚未达到真实 Docker 镜像构建或容器运行证据。
- 已关闭风险：FaceRec 不再把 `media/`、本地 `config.toml`、Harness/OpenSpec/Codex 状态纳入 context；PPT 排除本地 Harness 大数据；ASR Offline 的模型 hotword WAV 用 `!model/**/*.wav` 从全局媒体排除规则中恢复。
- 规格复审补强：门禁只允许按算子精确声明的 negation，拒绝 `!*`、`!**`、`!**/*` 及其他宽泛重包含；拒绝 HTTP/HTTPS/Git 远程 `ADD`；遍历真实 context 文件并按 `.dockerignore` 顺序与 negation 计算最终 inclusion，阻止未忽略的媒体、测试、缓存与密钥制品。扫描实际发现并修复 VBas 模型 allowlist 重新纳入 `__pycache__` 和 ScreenDet 漏排 `tests/` 的问题。
- 质量复审补强：`build-images` 必须接收与 HEAD 精确一致的 40 位 `EXPECTED_GIT_SHA`，并在构建前拒绝会进入镜像 context 或 registry wheel 的 tracked dirty/untracked 源，但允许被 `.dockerignore` 排除的用户文档/测试变更和受 7C 管理的模型资产。Dockerfile 有限解析器支持 TAB、escape directive、续行、JSON 及 `--from` 两种形式，并对未知/解析失败 fail closed。镜像 inspect 校验 `RepoTags` 列表包含目标引用，不再依赖列表第一项。
- 质量二次复审：Git 输入门禁同时检查 HEAD 中原本会进入 context 但已被删除的 tracked 文件；删除已排除的测试文件仍允许。八个 `.dockerignore` 统一排除 `wheel/*.whl`，仅精确重包含 `algorithm_operator_registry_client-0.1.0-py3-none-any.whl`，ASR Offline 现有 PyArrow wheel 不再进入构建 context。
- 交付准备结论：Task 7C 已通过六个实际明文模型根的仓库外 manifest、事务暂存/校验与密钥边界门禁关闭交付准备；真实服务器资产传输和镜像构建仍待后续任务。

## 2026-08-11 - 方案 C 里程碑 2A 真实运行时闭环

- 先前状态：Kafka adapter、Outbox Publisher、Consumer、DAG、租约执行器和契约 Stub 只有组件或 Broker 级测试，没有真实服务进程贯通证据。
- 目标状态：用真实 PostgreSQL、Redis、Kafka、`control_service.app.main:app`、`orchestrator_service.app.main:app` 和独立 HTTP Stub 验证 ASR-only 调度、恢复和幂等。
- 变更文件：里程碑 2A 运行时集成测试、一键运行脚本、可延迟契约 Stub、orchestrator readiness 故障注入测试、gitignore 运行报告目录、Harness 证据文档和 OpenSpec 任务状态。
- 契约影响：A 面字段、任务类型、HTTP 路径、算子 `/execute` 请求/响应和默认端口均不变；延迟只由测试 Stub 环境变量控制。
- Kafka 客户端决策：平台选用 `aiokafka` 0.14.x，以原生 asyncio API 实现确认发送、手动提交、有界轮询和 lag；实装 0.14.0 元数据为 `Requires-Python >=3.10`，与平台 `requires-python>=3.11` 兼容，orchestrator 显式限定 `aiokafka>=0.14,<0.15`。该依赖属于平台，不进入算子模型环境。
- 验证环境：`postgres:17-alpine` 17.10、`redis:7.4-alpine` 7.4.10、`apache/kafka:4.0.0` 均为 healthy；每次运行使用唯一 `_test` 数据库、Redis DB 14 UUID 前缀、唯一 Topic/Group 和临时端口。
- 真实证据：NORMAL/URGENT 请求先到状态 30，再经首次心跳恢复到节点/任务 60；GET 观察到运行中 50；Kafka offset 从 2 恢复到 4；重复发布后 Outbox 尝试次数为 2，仍只有 2 个任务类型和 4 个节点；URGENT Stub 调用先于 NORMAL；终态租约为零。
- 实例选择证据：E2E 在节点执行轮询期间从本次唯一 Redis 前缀的 `lease:*` hash 采集 `lease_id`、`instance_id`、`capability`、`service_url` 到 `evidence.selected_instances`，而非从注册响应推断。断言实际观察到 `asr_offline` 与 `text_analysis`、实例 ID 是本次对应注册实例、URL 均为 Stub；采集后仍验证终态租约清零。
- 发布恢复证据边界：`tests/test_outbox_publisher.py` 通过组件故障注入验证发布失败时事件保持待发布；真实 Broker Harness 恢复待发布 Outbox 并重启 orchestrator，证明重投后 `published_at` 恢复、`publish_attempts>=2`。未停止真实 Broker，不将该证据表述为 Broker 停机演练。
- Kafka 不可用就绪证据：新增 `FakeConsumer.lag()` 故障注入用例，验证 `/ops/readiness` 返回 503、Kafka 检查为 `ready=false` 且中文诊断可见；不停止真实 Kafka 容器。该服务用例与真实 Broker 的发布/消费、手动提交、同 group 重启 offset、未提交重投和重复消息证据合并支撑 2.6。
- 规范复审：Stub 增加真实 `/health`，所有启动/readiness 只接受 HTTP 200；两次 orchestrator run 保存不同 PID、序号、探针响应、停止日志和退出码。teardown 只接受完整 `algorithm-test-milestone2a-<32 hex>` 名称，精确删除本次 Consumer Group 并验证消失后再删唯一 Topic。
- 历史清理：2026-08-11 复核时 Broker 实际存在 2 个而非先前报告的 3 个里程碑测试 group：`algorithm-test-milestone2a-c603501f7c894294a801bc6ec6c0237f`、`algorithm-test-milestone2a-cab7c092931149679a3796c687d3571b`。两者均按完整名称删除成功，随后 Consumer Group 列表为空；未删除其他 group。
- 证据等级与结论：达到消息代理集成、服务运行、算子 HTTP 契约和确定性重启恢复层级，里程碑 2A 符合。JSON 运行报告位于 gitignore 的 `harness/reports/milestone-2a/`。
- 状态同步复审：`compileall`、Ruff、严格 Mypy 均通过；平台 `276 passed`，orchestrator `17 passed`；两份真实集成文件与一键 Harness 各 `12 passed`且无 skipped；基础设施/平台 Compose 解析和严格 OpenSpec 校验通过。
- OpenSpec 状态：有证据的 2.3-2.6、4.1-4.6、4.13-4.14 和 8.1-8.5 标记完成；4.7-4.12、视觉、在线与真实算子任务保持未完成。
- 剩余风险：2A 只调用契约 Stub，没有接真实 PPT、OCR、离线 ASR 或 VBas；2B 的真实同步算子、异步 PPT 长租约和视觉编排仍需独立验收。ScreenDet 只属于在线网关，不属于离线 DAG。

## 2026-08-11 - 算子本机运行、注册 wheel 与 PPT 终态合同复核

- 先前状态：算子注册客户端依赖平台源码导入；FaceRec 无人物图片留存开关；ASR 环境名和 Python 版本不统一；PPT 平台回调拒绝真实 `dynamic_segments`，且没有失败终态路径。
- 目标状态：发布 Python 3.10+ 轻量注册 wheel；ASR 使用 `asr` Python 3.11；FaceRec 使用 `facerecapi` 并默认不保存人物图片；PPT 平台适配器完整接收最新终态合同。
- 变更文件：注册客户端包/构建测试、ASR/FaceRec 镜像与运行代码、PPT 平台适配器与测试、Compose、总体设计和 Harness。
- 契约影响：现有业务推理路径和字段不变；FaceRec 新增默认 false 的 `save_person_photo` 配置；PPT 内部终态增加 `dynamic_segments` 的平台接收与持久化。
- 证据：独立 wheel 构建与隔离导入、ASR/FaceRec/OCR/ScreenDet/VBas/Text Analysis/PPT 本机真实调用、PPT 回调和路径安全组件测试。
- 证据等级与结论：算子真实运行和 PPT 组件合同符合；课程 DAG、PPT 常驻运行时和真实课程 P 视频仍未验收。
- 剩余风险：FaceRec FastDeploy 阻塞 Python 3.11；online gateway 人脸管理路由尚未实现；ScreenDet/Text Analysis 通用就绪状态仍需增强。

## 2026-08-10 - PPT 视频输入字段规范化

- Previous state: PPT submission used the ambiguous `uri` field even though orchestrator supplied an already prepared absolute local file path.
- Target state: `video_path` is the canonical field, accepts remote URLs or absolute local paths, rejects relative paths, and keeps `uri` only as an operator-side compatibility input.
- Changed files: PPT request schema/API/tests/docs, orchestrator PPT adapter, platform contract tests, AGENTS and Harness scenario.
- Contract impact: orchestrator now emits `video_path`; the operator still accepts legacy `uri`, so staggered deployment remains compatible.
- Evidence: PPT unit/contract suite, real temporary local MP4 decode, platform adapter tests and operator HTTP smoke verification.
- Remaining risk: background orchestrator end-to-end execution remains outside this component contract change.

## 2026-08-06 - Runtime closure baseline

- Previous state: control and online have functional routes; orchestrator and vision entrypoints are health-only; Kafka adapters and real end-to-end evidence are absent.
- Target: four independently deployable FastAPI projects with annotated configuration, real lifespan resources and reproducible evidence.
- Contract impact: A and non-PPT operator contracts unchanged. PPT internal callback changes from Base64 per slide to shared files, atomic manifest and one terminal callback.
- Current evidence: component and PostgreSQL/Redis tests only. Broker-backed and complete service-runtime evidence remains pending.
- Remaining risk: long-running Worker loops, restart recovery, real operator images and full Compose have not yet been verified.

## 2026-08-06 - FastAPI delivery and PPT shared-result components

- Previous state: four service folders had uneven entrypoint/configuration layouts; no platform Compose existed; the platform PPT adapter still expected per-image Base64 callbacks.
- Target state: complete per-service FastAPI packages and annotated settings, a validated four-service single-machine Compose, and one platform-only PPT shared-path protocol.
- Changed files: `services/*/app`, four service `config.toml`/requirements/Dockerfiles, `deploy/docker-compose.platform.yml`, `services/orchestrator_service/ppt_slice.py`, `ppt_slice/app`, and related tests/docs.
- Contract impact: breaking internal PPT contract. Only snake_case submission, atomic `/data/result/{task_id}/ppt/manifest.json`, and one terminal metadata callback are accepted. A-facing and other operator contracts are unchanged.
- Verification: four-service structure/contract tests `33 passed`; PPT platform tests `9 passed`; PPT Conda tests `13 tests OK`; Compose config validation passed.
- Evidence tier and verdict: static/service component/operator smoke evidence is present. Broker-backed end-to-end evidence is not present.
- Remaining risks: orchestrator has not wired PPT submission/callback/reconciliation/lease components into its required runtime loop; vision and general DAG loops remain incomplete; platform images have not been built together in the final stack.

## 2026-08-07 - Root-level platform service relocation

- Previous state: four deployable services lived under `algorithm-scheduling-platform/services`, used `services.<service_name>` compatibility imports, and Docker builds copied the shared service tree.
- Target state: `control_service`, `orchestrator_service`, `vision_orchestrator_service` and `online_gateway_service` are independent workspace-root FastAPI projects; `algorithm-scheduling-platform` retains only shared packages, migrations, deployment definitions, cross-service tests and Harness.
- Changed files: four root service projects, platform packaging/tests/Compose/Makefile, root and platform AGENTS rules, design documents, Harness and the active relocation OpenSpec artifacts.
- Contract impact: HTTP/WebSocket paths, methods, fields, container ports, Kafka semantics and operator registration are unchanged. Only internal source paths, Python imports and Docker build contexts changed.
- Verification: four service suites `4/5/8/9 passed`; platform suite `192 passed`; Ruff and strict Mypy passed; three Compose files parsed; four images built with a root allowlist `.dockerignore` and returned `/health` HTTP 200; image inspection found no sibling service source; the expanded runtime/build/documentation old-path gate and strict OpenSpec validation passed.
- Evidence tier and verdict: static, unit, Compose, independent-image and service-runtime smoke evidence is complete for relocation. Broker-backed business end-to-end evidence remains outside this structural change.
- Remaining risks: the shared distribution still lives under `algorithm-scheduling-platform`; Orchestrator's FFmpeg image is large and slow to build; runtime closure work remains governed by the separate active change.

## 2026-08-07 - 方案 C 基础调度闭环与数据库说明基线

- 先前状态：开发顺序把真实 PPT 作为首条最小离线链路，但 PPT 正在独立优化；总体图没有清楚表达 control 只写 Outbox、orchestrator Publisher 从 PostgreSQL 读取后发布 Kafka 的方向；数据库迁移没有表和字段注释。
- 目标状态：一个基础阶段包含两个连续里程碑，先完成 `control-service` 的任务事实闭环，再完成 `orchestrator-service` 的通用运行时；使用契约 Stub 验证真实 PostgreSQL/Redis/Kafka，不依赖真实 PPT。10 张正式调度表及其全部字段具有中文说明。
- 变更文件：总体设计 V2、活动 OpenSpec、Harness 基础闭环场景、数据库逻辑模型、`0004_schema_comments.sql` 和迁移约束测试。
- 契约影响：A 面字段、HTTP/WebSocket 路径、算子协议和状态值不变；只调整实施顺序、完成口径和数据库元数据。
- 数据库审计：本机 `algorithm` 业务库当前无用户表；`algorithm_migration_test` 有 9 张调度测试表；`algorithm_repository_test` 有全部 10 张调度测试表；未删除、改名或修改任何现有表和数据。
- 当前证据：数据库注释迁移约束测试和迁移文件名检查已通过；在本轮新建并随后删除的临时验证库中顺序执行 `0001-0004`，得到 10 张表、92 个字段，缺失表注释和字段注释均为 0；基础 Broker 闭环尚未实现和验收。
- 证据等级与结论：DDL 静态契约符合；方案 C 的服务运行时仍为部分符合。
- 剩余风险：目标业务库尚未执行 `0001-0004`；Kafka adapter、Publisher、Consumer、Dispatcher 和契约 Stub 闭环待实现；PPT 最终内部契约仍由独立会话收口。

## 2026-08-07 - 方案 C 里程碑 1：control 事实闭环

- 先前状态：`control-service` 在应用构造期创建 Engine/Redis，真实入口未组合 PostgreSQL 算子审计，readiness 不区分存活与依赖就绪，Redis 注册、心跳和注销存在先读后写竞态。
- 目标状态：FastAPI lifespan 统一持有 PostgreSQL/Redis；课程事实与 Outbox 同事务；PostgreSQL 保存算子声明和运维事件；Redis 保存 TTL、实时生命周期和原子容量租约。
- 变更文件：`control_service/app/infrastructure/runtime.py`、`audited_operator_registry.py`、共享 Repository/Redis registry、`0005_operator_audit_and_status_comments.sql`、Control Compose/README 以及里程碑 1 测试。
- 契约影响：A 面字段、HTTP 路径、算子推理协议和状态值不变；A 面任务库故障明确为 HTTP 200 + 业务码 `50000`，注册/租约基础设施故障为 HTTP 503。新增运维历史查询 `GET /ops/operator-instances/{instance_id}/events`。注册激活规则调整为“`register` 返回 OFFLINE，首次成功心跳后 ONLINE”。
- 真实环境：`postgres:17-alpine`（PostgreSQL 17.10）与 `redis:7.4-alpine`（Redis 7.4.10）容器均为 healthy；集成测试每次创建唯一 `_test` PostgreSQL 数据库和 UUID Redis 前缀，结束后精确清理。
- 验证：里程碑 1 联合集成测试 `63 passed`；平台与 Control 完整回归 `255 passed`；其他三个服务回归分别 `5/8/9 passed`；Ruff、Mypy、compileall、迁移命名、Compose 解析和严格 OpenSpec 校验均通过，无 skipped。新增用例覆盖缺字段、未执行 `0005`、依赖故障响应、readiness 并行/总截止预算、DSN 原有 PostgreSQL options 保留、首次心跳与短暂心跳故障恢复。
- 证据等级与结论：里程碑 1 达到真实 PostgreSQL/Redis 集成和 FastAPI 运行时证据，结论为符合。方案 C 整体仍为部分符合，不得宣称完整调度闭环已完成。
- 剩余风险：里程碑 2 的 Kafka adapter、Outbox Publisher、Consumer、DAG 和契约 Stub 尚未实现；本机 `algorithm` 业务库未自动执行迁移；当前协议没有进程世代标识，同一 `instance_id` 只允许一个存活进程。同 ID 重注册会清理旧心跳和租约，未来若要支持新旧世代重叠，需单独设计世代令牌。

## 2026-08-10 - 架构图留存与里程碑证据边界

- 先前状态：总体设计 V5.4 用新的离线/在线/运维服务边界图替换了一体化组件全景图，无法在同一文档追溯此前讨论；“里程碑 1 闭环”也容易被误解为已经调用真实算子。
- 目标状态：历史总体组件图、当前服务边界图和方案 C 时序图同时保留并具有稳定编号；后续架构图只追加不覆盖。明确里程碑 1 只达到真实 FastAPI/PostgreSQL/Redis 的 control 事实闭环，里程碑 2A 使用真实 Kafka 和 HTTP 契约 Stub，2B 再接首个真实同步算子。
- 变更文件：`docs/算法功能调度平台总体设计-v2.md/.pdf`、Harness 变更记录与架构证据矩阵。
- 契约影响：A 面和现有算子业务接口不变；进一步明确平台任务状态由编排服务推进，算子不得直接写平台任务状态。
- 验证：Markdown Mermaid 代码块结构检查、PDF 渲染与逐页视觉检查。
- 证据等级与结论：文档与架构决策记录符合；里程碑 2A/2B 仍未实现。
- 剩余风险：真实 Kafka、运行中的 DAG、契约 Stub 调用及真实算子接入均待里程碑 2 验证。

## Record template

- Date and scope:
- Previous state:
- Target state:
- Changed files:
- Contract impact:
- Verification command and environment:
- Evidence tier and verdict:
- Remaining risks:
