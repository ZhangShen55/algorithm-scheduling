# 里程碑 2B 部署证据目录

本目录只提交目录结构和本说明。运行时证据由根 `.gitignore` 排除，固定归档到：

```text
deploy/reports/milestone-2b/releases/{release_tag}/{git_sha}/
├── preflight/
├── container-maintenance/
├── image-build/
├── gpu-instances/
├── registration/
├── smoke/
├── negative/
├── load/
├── recovery/
└── summary/
```

`deploy/scripts/prepare-report-directory` 创建权限 `0700` 的 release 和分类目录。JSON、
日志及账本按运行脚本的合同以 `0600` 发布；受限模型 manifest 只复制到 Git 工作树外、
权限为 `0700` 的受限 release 目录，并保持文件权限 `0600`。证据文件采用 write-once
语义：相同字节的重跑是幂等成功，已有路径内容不同时拒绝覆盖。不得人工拼接不同
`release_tag` 或 `git_sha` 的证据。

## Canonical 输入文件

注册权威输入固定为以下文件：

```text
registration/operator-registration.json
registration/operator-registration-profile-gpu0.json
registration/operator-registration-profile-gpu1.json
registration/operator-registration-profile-gpu2.json
registration/operator-registration-profile-cpu.json
registration/operator-registration-instances-784a68323268.json
```

其中 `operator-registration-instances-784a68323268.json` 是
`facerec-gpu0`、`facerec-gpu1`、`facerec-gpu2` 同时就绪的 FaceRec instances 文件。
18 个 GPU 实例恢复后的独立注册文件必须全部存在：

```text
registration/operator-registration-instance-asr-offline-gpu0.json
registration/operator-registration-instance-asr-offline-gpu1.json
registration/operator-registration-instance-asr-offline-gpu2.json
registration/operator-registration-instance-asr-online-gpu0.json
registration/operator-registration-instance-asr-online-gpu1.json
registration/operator-registration-instance-asr-online-gpu2.json
registration/operator-registration-instance-facerec-gpu0.json
registration/operator-registration-instance-facerec-gpu1.json
registration/operator-registration-instance-facerec-gpu2.json
registration/operator-registration-instance-ocr-gpu0.json
registration/operator-registration-instance-ocr-gpu1.json
registration/operator-registration-instance-ocr-gpu2.json
registration/operator-registration-instance-screen-det-gpu0.json
registration/operator-registration-instance-screen-det-gpu1.json
registration/operator-registration-instance-screen-det-gpu2.json
registration/operator-registration-instance-vbas-gpu0.json
registration/operator-registration-instance-vbas-gpu1.json
registration/operator-registration-instance-vbas-gpu2.json
```

每个 GPU 实例还必须同时提供运行中推理和停止后残留检查证据：

| 实例 | 运行中证据 | 停止后证据 |
|---|---|---|
| `asr-offline-gpu0` | `gpu-instances/asr-offline-gpu0.json` | `recovery/asr-offline-gpu0-stopped.json` |
| `asr-offline-gpu1` | `gpu-instances/asr-offline-gpu1.json` | `recovery/asr-offline-gpu1-stopped.json` |
| `asr-offline-gpu2` | `gpu-instances/asr-offline-gpu2.json` | `recovery/asr-offline-gpu2-stopped.json` |
| `asr-online-gpu0` | `gpu-instances/asr-online-gpu0.json` | `recovery/asr-online-gpu0-stopped.json` |
| `asr-online-gpu1` | `gpu-instances/asr-online-gpu1.json` | `recovery/asr-online-gpu1-stopped.json` |
| `asr-online-gpu2` | `gpu-instances/asr-online-gpu2.json` | `recovery/asr-online-gpu2-stopped.json` |
| `facerec-gpu0` | `gpu-instances/facerec-gpu0.json` | `recovery/facerec-gpu0-stopped.json` |
| `facerec-gpu1` | `gpu-instances/facerec-gpu1.json` | `recovery/facerec-gpu1-stopped.json` |
| `facerec-gpu2` | `gpu-instances/facerec-gpu2.json` | `recovery/facerec-gpu2-stopped.json` |
| `ocr-gpu0` | `gpu-instances/ocr-gpu0.json` | `recovery/ocr-gpu0-stopped.json` |
| `ocr-gpu1` | `gpu-instances/ocr-gpu1.json` | `recovery/ocr-gpu1-stopped.json` |
| `ocr-gpu2` | `gpu-instances/ocr-gpu2.json` | `recovery/ocr-gpu2-stopped.json` |
| `screen-det-gpu0` | `gpu-instances/screen-det-gpu0.json` | `recovery/screen-det-gpu0-stopped.json` |
| `screen-det-gpu1` | `gpu-instances/screen-det-gpu1.json` | `recovery/screen-det-gpu1-stopped.json` |
| `screen-det-gpu2` | `gpu-instances/screen-det-gpu2.json` | `recovery/screen-det-gpu2-stopped.json` |
| `vbas-gpu0` | `gpu-instances/vbas-gpu0.json` | `recovery/vbas-gpu0-stopped.json` |
| `vbas-gpu1` | `gpu-instances/vbas-gpu1.json` | `recovery/vbas-gpu1-stopped.json` |
| `vbas-gpu2` | `gpu-instances/vbas-gpu2.json` | `recovery/vbas-gpu2-stopped.json` |

八类 full Smoke 输入由一个逻辑用例文件和八个算子证据文件组成：

```text
smoke/cases.json
smoke/asr_offline.json
smoke/asr_online.json
smoke/facerec.json
smoke/ocr.json
smoke/ppt_slice.json
smoke/screen_det.json
smoke/text_analysis.json
smoke/vbas.json
```

24 个实例都必须有独立运行目录。canonical 路径为
`smoke/instances/{instance_id}/runs/{run_id}/cases.json`，同目录还必须有该实例对应的
`{operator_code}.json`。18 个 GPU 实例运行用于关联 `gpu-instances/*.json` 的真实推理，
6 个 CPU 实例为 `ppt-slice-cpu0/1/2` 和 `text-analysis-cpu0/1/2`。不得用 full Smoke
替代每实例 runs，也不得在同一路径覆盖重跑结果。

反例和压力声明固定写入：

```text
negative/cases.json
load/cases.json
```

两个文件使用版本化 `execution_declaration` envelope，包含 `schema_version`、类别、
状态、`mock`、release/SHA、中文原因和逐条声明。报告计划必须展开为 217 条 negative
声明和 26 条 load 声明，共 243 条；没有现场证据时保持“未执行及原因”，不能据此标为通过。

## 聚合与报告门禁

`scripts/aggregate_milestone_2b_cases.py` 校验上述 canonical 输入、版本化 envelope、
release/SHA 一致性和覆盖计数，然后 write-once 发布：

```text
summary/cases.json
```

完整 envelope 有 335 条用例：60 条注册/GPU 证据、32 条 Smoke 和 243 条执行声明。
每条用例保留 `mock` 字段；聚合覆盖率分别统计期望、观察和通过数量。renderer 只把
`mock=false` 计入真实验证的通过、失败和未执行计数，同时在明细中明确标识“真实”或
`Mock`；Mock 结果不能替代真实验收。

`scripts/render_milestone_2b_report.py` 只读取 `summary/cases.json`，并以一次事务发布：

```text
summary/report.json
summary/report.md
```

`report.json` 是版本化报告 envelope，包含 `overall_status`、覆盖率、真实用例计数、
输入摘要和证据索引；Markdown 展示同一结论。证据索引只记录类型、字节数和摘要，
报告不包含证据原文。生成文件不等于验收通过：只有 renderer 返回码 `0` 且
`overall_status` 为“通过”才通过门禁；返回码 `3` 表示报告已生成但验收未通过，其他
非零返回码表示输入校验或发布错误。

## 受限信息边界

外部 manifest 的直接父目录必须由当前执行用户拥有且精确为 `0700`，文件必须是同一
用户拥有的普通文件且精确为 `0600`；源路径的任一父级不得是软链接，源文件不得位于
任何 Git worktree。`generate-model-asset-manifest` 只用于交付方准备外部基线，部署过程
不得重新生成或覆盖可信 manifest。

`summary` 只能记录模型根名称、文件总数和总字节数，不得包含逐文件路径、逐文件哈希、
密钥内容、密钥大小、密钥摘要、人脸原图或服务器认证信息。真实 JSON、日志、快照、
暂停账本、模型 manifest 和任何 secret metadata 都是运行证据，不得强制加入 Git。
