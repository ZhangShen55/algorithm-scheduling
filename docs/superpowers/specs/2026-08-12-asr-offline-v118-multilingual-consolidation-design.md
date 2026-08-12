# ASR Offline v1.1.8 小语种能力收敛设计

## 背景与目标

`asr_offline` 当前同时暴露 `/v1.1.7/seacraft_asr` 和
`/v1.1.8/seacraft_asr`。小语种 Whisper 逻辑仅位于 v1.1.7，并在默认
`showSpk=true` 时依赖 Pyannote；普通话检测接口也复用 Whisper。现有调度平台只调用
v1.1.8，因此旧路由和小语种 Pyannote 不再符合当前调度方式。

本变更将小语种转写并入唯一保留的 v1.1.8 路由，删除普通话检测和 v1.1.7，
并从运行依赖及容器中移除小语种 Pyannote。中文、英文和自动识别路径继续使用
Paraformer，保持原有增强能力。

## 已确认约束

- 保留 `POST /v1.1.8/seacraft_asr`。
- 删除 `POST /v1.1.7/seacraft_asr`。
- 删除 `POST /audio/detect_mandarin`，保留 `/audio/db_snr`。
- 本次不删除 `/text/question`。五何模型仍按请求懒加载，不在服务启动时占用显存。
- 不新增任何成功或错误响应字段，包括 `feature_status`、能力状态或成功
  `code=0`。
- 小语种仍使用现有 `[speech_rate].rate_factor = 0.4` 计算
  `segments[].speed`。
- `speed_info` 保持现有算法语义，不乘 `rate_factor`。
- 使用本机 `/Volumes/Data55/asr测试文件/法语音频.mp3` 做真实法语回归，
  不将该文件复制进仓库。

## 路由与语言选择

v1.1.8 在读取和转码音频前对 `language` 执行 `strip().lower()`，并使用规范化后的值
进行路由及响应：

| `language` | 执行路径 | `open_mul_lang` 的影响 |
| --- | --- | --- |
| `auto`、`zh`、`en` | Paraformer | 不受影响 |
| 当前小语种白名单 `fr` | Faster-Whisper | 必须为 `true` 且模型已就绪 |
| 空字符串或其他值 | 不执行模型，返回业务错误 | 不受影响 |

错误仍使用 HTTP 200：

- `fr` 能力关闭或 Whisper 未就绪：`{"msg": "未开启小语种识别或模型未就绪", "code": 4003}`。
- 未知或非法语言：`{"msg": "不支持的语言: <规范化值>", "code": 4009}`。

语言校验必须发生在 `prepare_asr_context()` 前，保证非法请求不会进入应用层音频读取、
预处理或模型调用。

## 小语种成功响应合同

顶层只返回现有成功字段：

```json
{
  "language": "fr",
  "segments": [],
  "text": "",
  "speed_info": [],
  "load_audio_time_ms": "0.00",
  "gpu_time_ms": "0.00"
}
```

每个 Whisper segment 始终包含：

- `segment_text`
- `bg`
- `ed`
- `speed`
- `segment_words`

增强参数按以下方式兼容，但小语种路径不调用 Pyannote、身份识别或 emotion2vec：

| 请求参数 | 小语种响应 |
| --- | --- |
| `showSpk=true` 或 `showRoleIdentify=true` | 每段包含 `"role": null` |
| `showSpk=false` 且 `showRoleIdentify=false` | 不返回 `role`，沿用未请求语义 |
| `showEmotion=true` | 每段包含 `"emotion": null` |
| `showEmotion=false` | 不返回 `emotion`，沿用未请求语义 |
| `wordTimestamps=false` | 每段 `"segment_words": []` |
| `wordTimestamps=true` | 返回 Faster-Whisper 的真实 `bg/ed/word_text`；个别无法对齐的段可为空数组 |

`showSpeed` 继续保持现状：不控制字段是否出现。`speed` 和 `speed_info` 始终计算。
`hotWords`、`openPanel` 和 `output` 继续被接口接受，但不为 Whisper 引入新行为。

当 Whisper 没有生成任何有效 segment 时，和 Paraformer 路径一致返回 HTTP 200 的业务
错误 `4008`，不返回空成功结果。

## 语速口径

小语种需要把现有仅识别 ASCII 的计数规则扩展为 Unicode 单词计数，否则
`élève`、`français`、`aujourd’hui` 会被拆分或漏计。计数器先做 NFC 规范化：

- 中文仍逐字计数；
- 非中文按 Unicode 字母或数字组成的单词计数；
- ASCII/弯引号和连字符仅在单词内部保留；
- 标点和空白不计数。

单段语速保留现有公式与已确认系数：

```text
segments[].speed = int(内容数量 × 60 / (ed - bg) × 0.4)
```

`speed_info` 保留 1、5、10 分钟窗口结构，使用同一内容计数口径，按 segment 与窗口的
重叠比例分配内容数量，并继续不乘 `0.4`。因此 `segments[].speed` 表示发声段内的修正
语速，`speed_info` 表示包含静音的墙钟时间内容密度，两者不能直接横向比较。

语速计算只基于 segment 文本和时间，不依赖 `wordTimestamps`，保证同一音频在
`wordTimestamps=true/false` 时得到相同的 `speed` 和 `speed_info`。

## 并发与推理生命周期

Faster-Whisper 的 `transcribe()` 返回惰性 generator。现有实现只在模型锁内取得
generator，真正解码发生在锁、GPU slot 和超时范围之外。

实现必须在同一个工作线程和模型锁内执行：

```python
segments, info = model.transcribe(...)
return list(segments), info
```

v1.1.8 小语种调用继续在 `acquire_gpu_slot()` 内等待上述完整调用结束，使 GPU slot、
模型锁和一小时超时覆盖实际 Whisper 解码全过程。

## 模型与部署资源

小语种路径完全移除：

- Pyannote import、singleton、getter 和启动加载；
- `open_mul_spk` 与 `pyannote_model_yml`；
- `app/utils/pynanote_speaker.py`；
- `pyannote.audio` 直接依赖；
- Dockerfile 中对 Pyannote 配置路径的改写；
- Compose 中仅为可信 Pyannote checkpoint 设置的
  `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`。

本地 `model/` 被 Git 忽略，本变更不执行不可恢复的本地模型目录删除。Docker 构建上下文
显式排除以下目录，确保新镜像不再携带这些资产：

- `model/speaker-diarization-3.1/`
- `model/segmentation-3.0/`
- `model/wespeaker-voxceleb-resnet34-LM/`

保留 Whisper、Paraformer、CAM++ 和 emotion2vec；后三者仍服务于 `auto/zh/en` 路径。
`open_mul_lang` 保留并继续控制 Whisper 是否在启动时常驻加载。

## 文档与平台同步

同步更新：

- `asr_offline/README.md` 与 `AGENTS.md`；
- 算子本地配置及平台 GPU 部署配置；
- 算子依赖、Dockerfile、Compose 和对应部署测试；
- 平台对 v1.1.8 成功响应及小语种降级行为的契约文档；
- 平台静态路由合同测试，只保留 v1.1.8。

调度平台专用 ASR 适配器已经调用 v1.1.8，不需要修改调用路径。

## 验证

自动测试至少覆盖：

1. v1.1.7 和普通话检测路由均为 404，且不出现在 OpenAPI；`/audio/db_snr` 和
   `/text/question` 仍存在。
2. `auto/zh/en`（含大小写和前后空格）只走 Paraformer。
3. `fr` 只走 Whisper；未知语言返回 HTTP 200 + `4009`，并且不读取音频。
4. `open_mul_lang=false` 或 Whisper 未就绪时，`fr` 返回 HTTP 200 + `4003`。
5. 小语种增强参数不加载或调用 Pyannote、身份识别或 emotion2vec，并按请求决定
   `role/emotion` 的 `null` 或字段缺失。
6. `wordTimestamps=false` 返回空数组；`true` 返回真实词级时间。
7. 小语种每段包含非负整数 `speed`，`speed_info` 结构保持不变，法语重音词按
   Unicode 单词正确计数，`rate_factor=0.4`。
8. Whisper generator 在模型锁和 GPU slot 释放前被完整消费。
9. Whisper 空结果返回业务 `4008`。
10. 两份配置键形状一致，Pyannote 依赖和容器专用兼容项消失。

真实验证使用 `/Volumes/Data55/asr测试文件/法语音频.mp3`：

- 文件约 442.85 秒、44.1 kHz 双声道；
- v1.1.8 `language=fr&wordTimestamps=true` 返回非空法语文本和 segments；
- 至少一个 segment 含真实词时间；所有 segment/word 时间单调且处于音频范围；
- 至少一个 segment 的 `speed` 为正数；
- `speed_info` 的 1/5/10 分钟窗口数分别为 8/2/1。

还需按算子指南运行 compileall、导入 `app.main:app`、完整测试、启动 Uvicorn 后检查
健康/路由，并完成真实推理。

## 已接受的兼容边界与范围外事项

- 按用户决定不增加能力状态字段，因此响应本身无法区分“不支持小语种角色/情绪”与
  “对应增强模型失败”。上游需按请求语言理解 `null`。
- 同级项目 `ai报告分析课程数据` 仍调用 `/audio/detect_mandarin`，并会把空
  `role/emotion` 误解释为业务值。该项目不在本次 `asr_offline` 实施授权范围内，
  发布删除路由前必须单独迁移或确认退役。
- 本次不改变报告项目的小语种语速阈值。法语的计数单位与中文不同，报告若跨语言比较
  必须另行定义口径。
- 不修改历史已完成计划；本设计取代其中“必须保留 v1.1.7”的旧合同。
