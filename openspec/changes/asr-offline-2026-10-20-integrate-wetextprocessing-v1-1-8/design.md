## Context

`asr_offline` 的唯一离线接口是 `POST /v1.1.8/seacraft_asr`。Paraformer 和 Whisper 的推理结果目前在 `asr_common.py` 中组装为 `segments`、`text`、速度信息和时间字段。课程验证表明，ASR 能正确给出不少语义内容，但中文数字、百分比、日期、分数和口头填充词的展示形式不稳定。

`WeTextProcessing==1.2.0` 使用 Pynini 编译有限状态规则。PyPI 包本身不携带 Pynini 二进制，在 Linux GPU 镜像中需要通过 Conda 安装 `pynini` 和 OpenFST；Mac 的 `asr` 环境也必须使用可用的 Conda-forge Pynini 构建。ITN 必须在 ASR 分段完成后运行，不能改变 VAD、时间轴、说话人、情绪和语速计算逻辑。

## Goals / Non-Goals

**Goals:**

- 在 `v1.1.8` 返回前对 Paraformer 文本执行可配置 ITN。
- 对 `auto`/`zh` 使用中文 `InverseNormalizer`，对显式 `en` 使用英文 `InverseNormalizer`。
- 同时规范化每个 `segment_text` 和顶层 `text`，保持原分段数量、`bg`、`ed`、`segment_words`、角色和情绪字段不变。
- 让默认配置与已验证的中文课程效果一致，并提供关闭、参数调整和异常回退能力。
- 让 192.168.29.12 的 `asr` 环境、GPU Docker 镜像和 Mac `asr` 环境采用可重复的依赖安装方式。

**Non-Goals:**

- 不训练或替换 Paraformer、VAD、Punc、Whisper 模型。
- 不通过 ITN 修复 ASR 错听，不对化学名词、数学公式进行语义推理或自动纠错。
- 不改变 `/v1.1.8/seacraft_asr` 的 HTTP 方法、路径、请求字段、响应字段名称和时间语义。
- 不把完整 ASR 文本写入日志，不修改 `asr_online` 或其他算子。

## Decisions

### 1. 在结果组装的最后阶段做分段级 ITN

在 `run_paraformer_asr` 和 `run_whisper_asr` 形成最终 `output_segments` 后统一调用 ITN 适配器。适配器接收语言、分段文本和配置，返回同一分段结构中的规范化文本；顶层 `text` 由规范化后的分段重新拼接。分段级处理能避免跨 VAD 边界合并数字，也能保留原始时间轴。

备选方案是对整段课程文本一次处理，但会跨越分段边界误合并日期、时间和公式，且无法直接映射回字幕时间，因此不采用。

### 2. 语言选择与配置

新增 `[itn]` 配置段，至少包含 `enabled`、`enable_standalone_number`、`enable_0_to_9`、`remove_interjections` 和 `failure_mode`。默认启用；`auto` 和 `zh` 使用中文规则，`en` 使用 WeTextProcessing 1.2.0 的英文规则。`failure_mode=passthrough` 时，构图或单段处理异常只返回 ASR 原文，并记录不含文本内容的错误摘要。

适配器在进程内按语言和参数缓存 normalizer，避免每个请求重复编译 FST；启动时不强制构图，首次使用或显式预热时构图，防止健康检查因依赖初始化失败而不可用。

### 3. 依赖和镜像安装

在项目依赖清单中固定 `WeTextProcessing==1.2.0` 和 `importlib_resources`。Docker 的 `deps` 阶段通过 Conda-forge 安装与 Python 3.11 匹配的 `pynini`/OpenFST，再用 pip 安装 WeTextProcessing；不让 pip 在 CentOS 7 中从源码编译 Pynini。Mac 验证使用同一 Conda-forge Pynini 版本，并在 README/部署说明中记录安装和检查命令。

### 4. API 兼容和可观测性

不新增必填请求字段，不删除任何响应字段。ITN 只替换已有文本字段的值；当 ITN 关闭或回退时，响应结构与当前 ASR 结果相同。日志只记录 ITN 是否启用、语言、处理分段数、改写分段数、耗时和失败原因类别，不记录完整文本。

### 5. 验证顺序和发布

先在 `192.168.29.12:/root/workspace/` 使用 `asr` 环境完成编译、依赖检查、服务健康检查、v1.1.8 短音频真实转写和 WeTextProcessing 数字/术语回归；再在 Mac 同步相同代码和依赖完成同等级验证。两端都通过后，检查工作区只包含本变更相关文件，创建中文规范提交并推送当前分支。

## Risks / Trade-offs

- **规则误判** → 保留 `passthrough` 回退；增加“零点五十”、连续数字、数学表达式、一氧化碳/二氧化碳、一阶导数、二维/三维等回归用例，并保留原始 ASR 结果用于人工复核。
- **Pynini 构建失败** → Docker 使用 Conda-forge 二进制包，Mac/Linux 验证在独立 `asr` 环境执行 `import pynini` 和版本检查；依赖不可用时服务启动检查应明确报错。
- **处理耗时增加** → normalizer/FST 按语言和参数缓存，按分段处理但避免重复构图；记录 ITN 耗时并在两台环境用完整课程音频测量。
- **英文规则与中文规则效果不同** → 显式按 `language` 选择规则；为 `auto` 固定中文规则并在文档中说明，英文效果以独立测试样例验收，不把中文术语保护假设扩展到英文。
- **部署配置遗漏** → 同步项目 `config.toml`、GPU operator 配置、Dockerfile、依赖清单、启动/部署文档和测试；在发布前做配置 diff 和镜像内依赖导入检查。
