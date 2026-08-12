# 场景：算子本机真实运行与平台接入前提

## 目标

在 MacBook CPU 环境逐个验证算子能够从真实 `app.main:app` 启动、完成业务推理，并确认轻量
`algorithm-operator-registry-client==0.1.0` wheel 不依赖平台 PostgreSQL、Redis、Kafka 或仓储源码。
本场景证明算子可供后续 orchestrator/online gateway 联调，不代表课程 DAG 已经端到端完成。

## 环境与结果

| 算子 | Conda/Python | 真实调用证据 | 结论 |
| --- | --- | --- | --- |
| `asr_online` | `asr` / 3.11.13 | WebSocket 连续发送 10 块音频，9 块返回非空文本 | 通过 |
| `asr_offline` | `asr` / 3.11.13 | `v1.1.8` 处理 442.85 秒真实法语 MP3，返回 140 个 segment 和 1063 个真实词时间 | 通过，本机 CPU 耗时约 536.8 秒 |
| `facerec` | `facerecapi` / 3.10.19 | `/persons` 写入 512×float32 embedding；同图 `/recognize` 100% 命中；未保存人物图片 | 通过，Python 版本有约束 |
| `ocr` | `ocr-v6` / 3.11 | `/ocr/prediction` 识别出“土地整治与土壤修复研究中心”等文本；149 项测试通过 | 通过 |
| `screen_det` | `screen_det` / 3.11 | `/detect_all` 的四个检测模块均返回成功；68 项测试和 17 个子测试通过 | 通过 |
| `vbas` | `vbas` / 3.11 | 学生接口识别 24 人；教师接口识别站立、讲授；55 项测试通过 | 通过 |
| `text_analysis` | `openai` / 3.11 | 真实 Qwen 调用返回关键词及完整课程 overview、key_points、mindmap；24 项测试通过 | 通过，依赖外部 LLM |
| `ppt_slice` | `ppt_slice` / 3.11 | 合成 MP4 经 HTTP 处理，落盘 1 张切片并回调一次；manifest 检出 `6000-15000ms` 动态区间；99 项测试通过 | 机制通过，真实课程 P 视频待验收 |

## ASR Offline 多语言合同

- 外部样本 `/Volumes/Data55/asr测试文件/法语音频.mp3` 时长 `442.853878` 秒，只用于本机验证，不复制进仓库。
- 唯一离线转写路由为 `POST /v1.1.8/seacraft_asr`；`POST /v1.1.7/seacraft_asr`、`POST /audio/detect_mandarin` 和 ASR Offline `POST /text/question` 均返回 HTTP 404，且不再出现于 OpenAPI。独立 `text_analysis` 算子不受影响。
- `language=auto/zh/en` 保持 Paraformer 路由，`language=fr` 在 `open_mul_lang=true` 且 Whisper 就绪时执行小语种转写。功能关闭或模型未就绪时返回 HTTP 200 / 业务码 `4003`，空语言和未支持语言返回 HTTP 200 / 业务码 `4009`。
- 法语成功响应顶层仅有 `language`、`segments`、`text`、`speed_info`、`load_audio_time_ms` 和 `gpu_time_ms`。实测得到 140 个 segment、1063 个真实词时间和 139 个正数 `speed`；`speed_info` 的 1/5/10 分钟窗口数分别为 8/2/1。
- 小语种不调用说话人、角色或情绪增强模型。请求相应能力时 `role`/`emotion` 为 `null`；`wordTimestamps=false` 时 `segment_words=[]`，为 `true` 时返回 Whisper 真实词时间。Whisper 始终启用词对齐，仅在序列化时按请求隐藏词数组，保证 `wordTimestamps` 不改变 `speed` 和 `speed_info`。
- `rate_factor=0.4` 只用于单段 `speed`，`speed_info` 不乘该系数。Pyannote 与 FiveWh/BERT 运行时、配置和部署资源均已移除；本地 BERT 两目录约 1.5 GB，只从 Docker context 排除而不物理删除。Paraformer、VAD、标点、CAM++、emotion2vec 和 Whisper 保留。
- FiveWh 退役后使用从 `test_wav/chinEng-16k.wav` 派生的 12 秒临时 WAV 复验真实 HTTP 路径：冷启动 `/ops/health` 为 200，三个退役路由均为 404；`language=zh` 返回 6 个 segment、71 字符非空文本、原有 6 个顶层字段和 1/5/10 分钟 `speed_info`。临时配置和音频未进入仓库，服务停止后移至废纸篓。
- 证据层级仅为算子静态/单元合同、本机冷启动和真实 CPU 推理，不代表已经通过真实租约调用，也不证明 Kafka、课程 DAG、GPU 容器或三卡部署已验收。

## FaceRec Python 约束

`facerecapi` 最终环境名保持不变，但本机不能升级到 Python 3.11。根因是
`fastdeploy-python==1.0.7` 的 macOS 包只提供 `cpython-310-darwin.so`；在 Python 3.11 中不是可导入
扩展。当前保留 Python 3.10，不以未做数值一致性验证的 ONNX Runtime 替换 FastDeploy。若后续更换
推理后端，必须先对同一批人脸验证 embedding 维度、归一化、相似度分布和阈值一致性。

## 人脸库边界

- `facerec + MongoDB` 是人物 embedding 的领域权威。
- A 服务通过 `online-gateway-service` 的独立管理路由访问人物入库、更新、删除；不直接连接 MongoDB。
- 人脸管理不进入课程 Kafka/DAG，不写平台 PostgreSQL，也不新增第五个平台服务。
- `save_person_photo=false` 只阻止图片写盘；embedding、bbox、MongoDB 记录和识别保持有效。

## 注册客户端

wheel 的 `Requires-Python` 为 `>=3.10`，运行依赖只有 FastAPI、HTTPX 和 Pydantic。它向算子提供
注册、心跳、排空和 `/ops/*` 运行面，不包含实例选择、PostgreSQL、Redis、Kafka 或任务状态机。
默认关闭主动注册；设置 `PLATFORM_REGISTRATION_ENABLED=true` 后才连接 `control-service`。

八个算子的运行依赖文件统一固定 `algorithm-operator-registry-client==0.1.0`。内部 PyPI 尚未建立时，
由 `scripts/build_and_stage_operator_registry_wheel.py` 将 Git 已跟踪源码复制到 clean source tree，
再在临时 wheelhouse 离线构建并严格校验固定成员、依赖元数据与 RECORD，
再通过跨进程文件锁和耐久事务 journal 发布到 `dist`，并将同一 SHA-256 的字节暂存到
各算子的 Git 忽略目录；中断事务由下一次运行优先回滚，避免九个路径残留混合版本。旧的
`scripts/stage_operator_registry_wheel.py` 委托同一流程，不再复制可能陈旧的现存 wheel。
镜像先安装该 wheel 再解析其余 requirements，避免从公网误解析私有包。

## 未完成项

1. 尚未使用真实课堂 P 视频验证 PPT 黑屏、滚动内容和疑似播放视频排除效果。
2. `orchestrator-service` factory 尚未注入 PPT handler；提交、续租、终态持久化和 OCR 释放还没有组成常驻运行闭环。
3. ScreenDet 与 Text Analysis 的通用 `/ops/status` 尚未反映真实模型/下游 LLM 就绪，应分别以 `/health`、`/api/models` 和真实请求作为就绪证据。
4. 本场景没有证明多 GPU 部署、Kafka 消费、课程 DAG 或在线网关实例路由。
5. 仓库外的旧报告流水线 `/Users/zhangshen/Documents/workspace/ai报告分析课程数据/scripts/pipeline.py` 仍调用已退役的 `/audio/detect_mandarin` 和 ASR Offline `/text/question`；发布前必须迁移这些步骤或确认整条流水线已停用。`text_analysis` 的同路径实现语义并非逐字段等价，迁移后必须做真实报告回归。
