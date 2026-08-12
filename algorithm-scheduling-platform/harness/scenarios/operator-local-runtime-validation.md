# 场景：算子本机真实运行与平台接入前提

## 目标

在 MacBook CPU 环境逐个验证算子能够从真实 `app.main:app` 启动、完成业务推理，并确认轻量
`algorithm-operator-registry-client==0.1.0` wheel 不依赖平台 PostgreSQL、Redis、Kafka 或仓储源码。
本场景证明算子可供后续 orchestrator/online gateway 联调，不代表课程 DAG 已经端到端完成。

## 环境与结果

| 算子 | Conda/Python | 真实调用证据 | 结论 |
| --- | --- | --- | --- |
| `asr_online` | `asr` / 3.11.13 | WebSocket 连续发送 10 块音频，9 块返回非空文本 | 通过 |
| `asr_offline` | `asr` / 3.11.13 | `v1.1.8` 处理真实 20 秒 WAV，返回 8 个 segment，含说话人和情绪 | 通过 |
| `facerec` | `facerecapi` / 3.10.19 | `/persons` 写入 512×float32 embedding；同图 `/recognize` 100% 命中；未保存人物图片 | 通过，Python 版本有约束 |
| `ocr` | `ocr-v6` / 3.11 | `/ocr/prediction` 识别出“土地整治与土壤修复研究中心”等文本；149 项测试通过 | 通过 |
| `screen_det` | `screen_det` / 3.11 | `/detect_all` 的四个检测模块均返回成功；68 项测试和 17 个子测试通过 | 通过 |
| `vbas` | `vbas` / 3.11 | 学生接口识别 24 人；教师接口识别站立、讲授；55 项测试通过 | 通过 |
| `text_analysis` | `openai` / 3.11 | 真实 Qwen 调用返回关键词及完整课程 overview、key_points、mindmap；24 项测试通过 | 通过，依赖外部 LLM |
| `ppt_slice` | `ppt_slice` / 3.11 | 合成 MP4 经 HTTP 处理，落盘 1 张切片并回调一次；manifest 检出 `6000-15000ms` 动态区间；99 项测试通过 | 机制通过，真实课程 P 视频待验收 |

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
再原子发布到 `dist` 并将同一 SHA-256 的字节暂存到各算子的 Git 忽略目录。旧的
`scripts/stage_operator_registry_wheel.py` 委托同一流程，不再复制可能陈旧的现存 wheel。
镜像先安装该 wheel 再解析其余 requirements，避免从公网误解析私有包。

## 未完成项

1. 尚未使用真实课堂 P 视频验证 PPT 黑屏、滚动内容和疑似播放视频排除效果。
2. `orchestrator-service` factory 尚未注入 PPT handler；提交、续租、终态持久化和 OCR 释放还没有组成常驻运行闭环。
3. ScreenDet 与 Text Analysis 的通用 `/ops/status` 尚未反映真实模型/下游 LLM 就绪，应分别以 `/health`、`/api/models` 和真实请求作为就绪证据。
4. 本场景没有证明多 GPU 部署、Kafka 消费、课程 DAG 或在线网关实例路由。
