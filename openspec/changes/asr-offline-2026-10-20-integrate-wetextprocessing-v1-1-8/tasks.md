## 1. 基线和依赖准备

- [x] 1.1 记录当前工作区状态，确认 `asr_offline` 无既有中文规范改动，并隔离 `asr_online` 与其他未关联脏文件
- [x] 1.2 确认 `WeTextProcessing==1.2.0`、`pynini` 和 `OpenFST` 的 Linux GPU 与 Mac `asr` 环境安装组合
- [x] 1.3 更新 `asr_offline/requirements.txt`、Docker 依赖安装和相关锁定/检查文件

## 2. ITN 适配器和配置

- [x] 2.1 新增离线 ASR ITN 适配模块，按 `auto`/`zh`/`en` 选择对应 InverseNormalizer，并缓存构图结果
- [x] 2.2 在配置模型中增加 `[itn]` 字段、默认值和合法性校验
- [x] 2.3 实现分段级 ITN、顶层 `text` 重建、空文本跳过和 `passthrough` 异常回退
- [x] 2.4 将 ITN 接入 Paraformer 与 Whisper 的 v1.1.8 结果组装，不改变时间、说话人、情绪、语速和现有响应字段
- [x] 2.5 增加受控 ITN 日志，记录语言、耗时、分段统计和失败类别，不记录完整转写文本

## 3. 测试和文档

- [x] 3.1 增加中文数字、百分比、日期、分数、单位和数学表达式的适配器单元测试
- [x] 3.2 增加“一氧化碳”“二氧化碳”“一阶导数”“二维传热”“三维空间”等术语保护测试，以及连续数字和 ASR 错听边界测试
- [x] 3.3 增加 ITN 关闭、参数切换、初始化失败和单段失败回退测试
- [x] 3.4 增加 v1.1.8 路由响应兼容测试，确认分段数量、`bg`、`ed` 和其他业务字段不变
- [x] 3.5 更新 `asr_offline/README.md`、`docker/README.md`、`docker/部署说明.md` 和配置注释，记录安装、开关、回退和验证命令

## 4. 部署文件和镜像

- [x] 4.1 更新 `asr_offline/config.toml` 的 ITN 默认配置
- [x] 4.2 更新 `algorithm-scheduling-platform/deploy/config/operators/asr_offline.gpu.toml` 的 ITN 配置
- [x] 4.3 更新 `asr_offline/docker/Dockerfile`，在 Conda-forge 安装兼容 Python 3.11 的 Pynini/OpenFST 并安装 WeTextProcessing
- [x] 4.4 检查 `.dockerignore`、镜像构建上下文和启动脚本，确保 ITN 规则文件随依赖进入运行镜像

## 5. 192.168.29.12 远端验证

- [x] 5.1 将本变更相关文件同步到 `192.168.29.12:/root/workspace/`，不覆盖远端无关工作区改动
- [x] 5.2 在远端 `asr` 环境完成依赖安装、`pip check`、`pynini`/`itn` 导入和 FST 构图检查
- [x] 5.3 在远端执行 `compileall`、应用导入、项目测试并启动 v1.1.8 服务
- [x] 5.4 使用真实短音频调用 `/v1.1.8/seacraft_asr`，验证数字、术语、时间轴、回退和健康状态
- [x] 5.5 记录远端验证命令、依赖版本、服务日志摘要和结果证据

## 6. Mac 同步验证

- [x] 6.1 将同一代码状态同步到 Mac `asr` 环境，安装与远端一致的 ITN 依赖
- [x] 6.2 在 Mac 执行 `compileall`、应用导入、`pip check` 和项目测试
- [x] 6.3 启动本地服务并使用真实 `test_wav` 音频验证 v1.1.8 输出和 ITN 配置生效
- [x] 6.4 对比远端与 Mac 的规范化样例、响应字段和时间轴，记录任何平台差异

## 7. 中文提交和推送

- [x] 7.1 检查 diff，仅暂存本变更相关的 `asr_offline`、部署文件、测试、文档和 OpenSpec 工件
- [x] 7.2 创建中文规范提交，例如 `feat(asr_offline): 接入 WeTextProcessing 规范化离线转写`
- [x] 7.3 推送当前分支到 `origin`，记录提交 SHA 和远端验证结果
- [x] 7.4 最终复查工作区，确认未提交或推送用户既有的 `asr_online`、其他服务和无关 OpenSpec 改动
