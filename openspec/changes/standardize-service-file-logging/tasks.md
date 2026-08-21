## 1. 变更依赖与工作区保护

- [x] 1.1 记录当前分支、完整 Git SHA、dirty/untracked 文件和 11 个目标项目的现有日志配置、handler、日志目录及 `.gitignore` 基线。
- [x] 1.2 对 `text_analysis/` 生成只读文件清单与内容摘要，后续不得修改其源码、配置、日志、Docker、文档或测试。
- [x] 1.3 只读核对 `retire-text-analysis-from-scheduling-platform` 的目标七算子拓扑、Compose 和 Harness 合同，确认日志变更可以先行实施；不要求退役变更已经 apply。
- [x] 1.4 搜索并登记七算子与四服务中现有 `FileHandler`、大小/时间轮转、Uvicorn logging 配置和敏感字段日志调用，形成逐项目迁移表。
- [x] 1.5 增加变更保护测试，禁止日志改造路径包含 `text_analysis/`、vendor、模型目录、生成代码或与日志无关的业务模块。

## 2. 统一合同测试与算子共享实现

- [x] 2.1 先为 `algorithm-operator-registry-client` 增加日志配置模型测试，覆盖默认值、环境覆盖、项目根解析、实例标识、非法大小/天数和越界文件名。
- [x] 2.2 先增加共享 Handler 的小阈值测试，覆盖写入前轮转、UTC 归档命名、单文件上限、启动清理、轮转后清理、未过期保留和实例目录外文件保护。
- [x] 2.3 先增加 JSON Lines formatter 测试，覆盖基础字段、可选任务/算子上下文、异常堆栈、有界事件和不可序列化 extra 字段。
- [x] 2.4 先增加 stdout/file 双输出与幂等初始化测试，证明重复调用不会增加 root、`uvicorn.error` 或 `uvicorn.access` handler。
- [x] 2.5 先增加敏感哨兵测试，证明 Base64/Data URL、媒体字节、Token、Cookie、密码、完整 DSN、ASR 文本和 OCR 文本不进入文件或 stdout。
- [x] 2.6 在 `operator_registry_client` 实现日志配置、项目根安全解析、实例目录创建、大小加年龄轮转、启动清理、JSON formatter、上下文与脱敏器。
- [x] 2.7 在标准库大小/年龄组合、清理边界、Uvicorn 去重、允许列表和文件失败诊断处补充简洁中文原因注释，不给显然赋值逐行加注释。
- [x] 2.8 更新共享包 README、版本和导出 API，构建新 wheel 并运行 wheel clean-install、Python 3.10/3.11 导入、`pip check` 及现有注册/租约测试。

## 3. 平台共享日志实现

- [x] 3.1 为 `platform_common.logging` 增加与算子共享实现参数化的合同测试，复用同一轮转、保留、格式、脱敏、幂等和失败关闭用例。
- [x] 3.2 扩展 `platform_common.logging`，在保留现有 `JsonFormatter`、`get_trace_id` 和 `log_node_audit` 语义的前提下增加文件日志、实例字段、清理和 Uvicorn 接管。
- [x] 3.3 对节点审计允许字段进行白名单化，保留 `task_id`、`task_type`、`node`、`operator_code`、`attempt`、`elapsed_ms` 和 `outcome`，拒绝任意请求/响应对象透传。
- [x] 3.4 在平台共享实现的轮转补偿、日志初始化顺序、Uvicorn 去重和敏感字段允许列表处补充简洁中文原因注释。
- [x] 3.5 验证现有平台日志/trace/metrics 测试、strict Mypy、Ruff 和 `compileall`，确认节点审计事件保持向后可解析。

## 4. 七个算法算子接入

- [x] 4.1 接入 `asr_offline`：迁移现有按日轮转到统一 `[logging]`，保留项目根解析，更新测试、README、Docker 与部署权威配置。
- [x] 4.2 接入 `asr_online`：替换普通 `FileHandler` 与旧根目录日志文件，保证 WebSocket 事件不记录 PCM、完整识别文本或完整消息体。
- [x] 4.3 接入 `facerec`：统一应用日志而不改变 MongoDB API 统计 TTL，保证人脸图片、embedding、Token 和完整 DSN 不落文件日志。
- [x] 4.4 接入 `ocr`：迁移现有大小轮转，保证 Base64 图片、完整 OCR 文本和公式内容不落日志，保持 OCR 路由与结果模型稳定。
- [x] 4.5 接入 `screen_det`：迁移应用与访问日志，保留 `YOLO_CONFIG_DIR`/`MPLCONFIGDIR` 缓存目录语义并禁止图片内容落日志。
- [x] 4.6 接入 `ppt_slice`：统一 `application.log` 与错误事件，保持异步并发、共享路径、manifest 和一次终态回调合同不变，不记录视频/切片内容。
- [x] 4.7 接入 `vbas`：统一框架和业务 logger，跳过 `app/vendor` 注释改动，保证学生/教师图片与完整检测响应不落日志。
- [x] 4.8 将新 `algorithm-operator-registry-client` 精确版本加入七算子 `requirements.txt` 与受控 wheelhouse/镜像安装流程，更新依赖锁和构建门禁。
- [x] 4.9 为七个算子的根 `config.toml` 和部署权威 TOML 增加带中文注释的 `[logging]` 全量字段，删除或明确迁移不再消费的旧日志字段。
- [x] 4.10 逐项目复审本组 diff，只在日志集成点与非直观边界保留必要中文注释，确认没有注释性改动进入推理、模型、vendor 或接口模块。

## 5. 四个平台服务接入

- [x] 5.1 为四服务配置模型增加统一 `LoggingSettings` 和测试，保持配置优先级、项目根及服务包相对导入规则不变。
- [x] 5.2 接入 `control_service`，记录任务/注册/租约审计标识但不记录 A 服务完整请求、数据库凭据或算子管理 Token。
- [x] 5.3 接入 `orchestrator_service`，记录 Outbox、Kafka、节点和适配器上下文但不记录媒体 URL 查询凭据、完整 ASR/OCR 结果或 Kafka 消息大字段。
- [x] 5.4 接入 `vision_orchestrator_service`，记录扫描轮次、时间点、租约和聚合摘要但不记录帧图片、Base64 或完整 VBas 响应。
- [x] 5.5 接入 `online_gateway_service`，保持 HTTP/实时 ASR WebSocket 转发合同和会话粘性，日志只记录大小、耗时、实例和状态，不记录图像/音频/完整转写。
- [x] 5.6 为四服务根 `config.toml` 增加带中文注释的 `[logging]` 全量字段，收敛旧 `[service].log_level` 并更新 README 与启动说明。
- [x] 5.7 逐服务复审新增/修改代码注释，确认仅解释轮转、脱敏、handler 和上下文边界且不改变 lifespan、Kafka loop、租约续期或服务就绪控制流。

## 6. Docker、Compose 与宿主机持久化

- [x] 6.1 更新七算子与四服务 `.gitignore`，保留 `logs/` 目录约定但禁止提交活动日志、归档和实例运行文件。
- [x] 6.2 更新 Dockerfile 和启动脚本，创建项目根 `logs/{instance_id}`，保证进入容器可以查看日志，使用非越界路径并让应用运行用户具备最小写权限。
- [x] 6.3 更新 Canonical operator/platform Compose，提供可选的 `/data/logs/algorithm-scheduling/{service}/{instance_id}` 到容器 `logs/{instance_id}` 挂载；默认不挂载时仍使用容器内日志目录。
- [x] 6.4 扩展主机/部署预检，仅在启用宿主机挂载时安全创建并校验日志目录归属、权限、非符号链接、可写性和磁盘余量；未挂载时不得因缺少宿主机目录失败，也不得修改无关目录或 `/data/result`。
- [x] 6.5 更新配置权威、Compose 展开和 attestation 测试，确认日志字段/挂载完整且七算子 21 实例、GPU/CPU、端口、网络和模型挂载不变。
- [ ] 6.6 增加两种运行模式测试：未挂载时容器内 `logs/{instance_id}` 能创建并写入；启用挂载时旧日志在容器替换后保留，新容器继续写入同一宿主机实例目录且不覆盖归档，并通过容器内 shell 和宿主机路径读取同一日志事件。

## 7. 配置、文档与注释质量门禁

- [x] 7.1 更新根 `AGENTS.md` 和平台 `AGENTS.md` 的持久日志合同、配置位置、目标范围和验证要求，保持其为长期规则而非变更流水账。
- [x] 7.2 更新七算子、四服务 README 和部署文档，说明本地/容器日志位置、实例目录、100 MiB、七日保留、stdout 查询和敏感内容禁记规则。
- [x] 7.3 在 `docs/算法功能调度平台总体设计-v2.md` 和部署对接文档中补充日志存储边界，但不向 A 服务增加日志查询合同或暴露宿主机路径。
- [x] 7.4 增加静态注释复审脚本或检查清单，要求关键非直观日志逻辑有中文原因注释，并拒绝 comment-only 大范围业务/vendor/text_analysis 改动。
- [x] 7.5 增加静态敏感日志检查和代表性运行测试，禁止 `logger.*` 直接接收 request body、Base64、PCM、图片字节、完整 Settings、ASR/OCR 完整结果或 embedding。
- [x] 7.6 执行 `text_analysis/` 前后摘要对比，确认该非平台项目完全未被本变更修改。

## 8. 本地分层验证

- [ ] 8.1 在各项目规定 Conda/`.venv` 环境运行 11 项 `compileall` 和 `app.main:app` 导入，验证日志目录首次启动自动创建。
- [ ] 8.2 运行七算子完整相关测试、`pip check`、健康/就绪和真实推理，比较路由/OpenAPI/响应合同并记录日志未包含输入输出大字段。
- [ ] 8.3 运行四服务完整相关测试、strict Mypy、Ruff、健康/就绪和跨服务闭环测试，确认任务状态机、Outbox、Kafka、租约和在线转发无回归。
- [ ] 8.4 使用小型临时阈值执行 11 项真实进程轮转测试，验证单文件上限、七日清理模拟、未过期保留和越界保护。
- [ ] 8.5 使用代表性 HTTP、WebSocket、ASR、OCR、FaceRec、ScreenDet、VBas 和 PPT 请求执行敏感哨兵检查，文件/stdout 原值检出数必须为零。
- [ ] 8.6 展开 Compose 并运行配置权威、挂载、项目根、实例隔离、clean clone、镜像构建合同和 `git diff --check`。
- [x] 8.7 运行 `openspec validate standardize-service-file-logging --strict` 及受影响活动变更的全部严格校验，解决所有失败后才进入远端阶段。

## 9. Harness 与变更协同

- [x] 9.1 新增 `harness/scenarios/service-file-logging-standardization.md`，记录 11 项原状态、目标合同、注释边界、验证命令、证据层级和剩余风险。
- [x] 9.2 为轮转、七日清理、敏感哨兵、实例隔离、容器重建和 `text_analysis` 排除建立独立 baseline/schema，禁止用普通 Smoke 代替专项证据。
- [x] 9.3 在 `harness/change-ledger.md` 追加日志标准化记录，不回写旧 release 或已完成 Text Analysis 历史证据。
- [ ] 9.4 确认本变更本地验证完成后，再继续 `retire-text-analysis-from-scheduling-platform` 的本地任务 `1.1` 至 `8.6`；两个变更均完成本地验证后才进入一次性的远端构建阶段。
- [ ] 9.5 冻结同时包含 retirement 与 logging 的同一完整 Git SHA、七算子拓扑、四服务镜像和日志配置权威，拒绝混用旧八算子或不同 SHA 证据。

## 10. 远端部署与专项验收

- [ ] 10.1 在 `192.168.29.11` 先按既有 Canonical Controller 规则核对恢复 audit、维护锁、旧容器状态、磁盘和端口，不直接停止、删除或 prune 无关容器。
- [ ] 10.2 以冻结 SHA 构建七个算子与四个平台镜像，inspect 完整 revision、amd64、日志配置、新共享 wheel 和非 root/写权限合同。
- [ ] 10.3 在不挂载日志卷的默认模式启动四平台与七算子 21 实例，验证 25 个容器内 `logs/{instance_id}` 可读、文件/stdout 双输出、实例字段和目录归属；再在启用挂载的代表性实例上验证宿主机持久化。
- [ ] 10.4 执行 18/18 GPU、3/3 CPU、7/7 综合 Smoke、PPT/OCR、ASR-only、教师/学生视觉、在线图片和实时 ASR，同时检查业务结果正确且敏感内容未落日志。
- [ ] 10.5 使用受控小阈值或专项进程验证 25 个容器的大小轮转与过期清理，不通过写满生产 100 MiB 文件制造磁盘压力。
- [ ] 10.6 重建代表性的 GPU 算子、PPT CPU 算子和平台服务容器，验证宿主机日志连续性、实例隔离和旧归档保留。
- [ ] 10.7 执行 retirement 新 schema 的 217 条反例、26 条压力/恢复、6 项 B 级复核及日志专项用例，要求同一 SHA 下无失败或未执行项。
- [ ] 10.8 完成 Canonical 精确恢复和唯一 audit，只有最终报告通过后才按镜像 ID 清理无容器引用的旧镜像，保留全部历史日志与 release 证据。

## 11. 最终复审与交接

- [ ] 11.1 逐条对照 `service-file-logging` spec、实现、测试、注释和 Harness，解决所有不符合项或明确记录阻断。
- [ ] 11.2 确认 11 个目标项目的默认值均为 100 MiB/7日、文件加 stdout、独立实例目录，并确认 `text_analysis/` 摘要完全一致。
- [ ] 11.3 复审 Git diff，确认没有接口、DAG、推理、注册租约、数据库、进程拓扑或无关注释变更，执行完整 `git diff --check`。
- [ ] 11.4 更新 Harness 最终 SHA、测试数量、远端 release、日志目录证据、注释复审结论和剩余风险，使用中文 Conventional Commit 提交并推送。
- [ ] 11.5 在 retirement 与 logging 的规范、代码、本地/远端证据全部完整后，再决定同步主 specs并分别归档两个变更。
