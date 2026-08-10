# PPT 动态区间检测 Harness

本目录保存动态区间检测的可审计验证资料。需求和设计以 OpenSpec 为准；长期项目规则位于 `AGENTS.md`；每轮算法版本、配置、运行结果和复核结论记录在 Harness。

## 目录所有权

- `architecture-review.md`：稳定的架构边界和风险复审。
- `change-ledger.md`：每轮算法或配置调整及关联报告。
- `verification.md`：可复现命令、环境和执行结果。
- `scenarios/dynamic-video-corpus.md`：语料发现、真值和复核场景。
- `reports/<run_id>/`：可提交的小型 JSON、CSV 和中文 Markdown 摘要。
- `.cache/`：探测元数据和压缩特征，禁止提交。
- `artifacts/<run_id>/`：证据帧与联系表，禁止提交。

## 数据边界

课程 MP4 只允许从 URL 流式探测和解码，不得下载或落盘原始视频、完整副本、MP4/GIF 预览或其他可还原完整视频的内容。允许落盘的视觉证据仅限少量 JPEG/PNG 静态帧和联系表。

## 运行标识

每轮使用不可复用的 `run_id`，并在报告中绑定：冻结 inventory 指纹、代码提交、算法版本、有效配置摘要、开始时间和完成状态。失败与不确定结果不得被后续运行覆盖。

已有真值的 P 视频使用重复参数 `--known-calibration-url URL` 固定进入校准集；其余课程按稳定哈希 70/30 划分。inventory 必须记录 `split_reason`，并把最终 split 纳入指纹。

## 核心命令

```bash
conda run -n ppt_slice python -m harness.tools.detect \
  --inventory harness/reports/INVENTORY_RUN/inventory.json \
  --split CALIBRATION \
  --run-id DETECTION_RUN \
  --max-workers 2 \
  --max-retries 1 \
  --timeout-seconds 420 \
  --output harness/reports/DETECTION_RUN/detections.json

conda run -n ppt_slice python -m harness.tools.prepare_review \
  --inventory harness/reports/INVENTORY_RUN/inventory.json \
  --detections harness/reports/DETECTION_RUN/detections.json \
  --output harness/reports/DETECTION_RUN/review-queue.json

conda run -n ppt_slice python -m harness.tools.evidence \
  --review-queue harness/reports/DETECTION_RUN/review-queue.json \
  --artifact-root harness/artifacts/DETECTION_RUN/review \
  --output harness/reports/DETECTION_RUN/review-queue-with-evidence.json \
  --max-workers 2

conda run -n ppt_slice python -m harness.tools.overview \
  --review-queue harness/reports/DETECTION_RUN/review-queue-with-evidence.json \
  --output-dir harness/artifacts/DETECTION_RUN/detection-overview \
  --kind DETECTION

conda run -n ppt_slice python -m harness.tools.dense_evidence \
  --review-queue harness/reports/DETECTION_RUN/review-queue-with-evidence.json \
  --artifact-root harness/artifacts/DETECTION_RUN/dense-review \
  --candidate-number 1 \
  --step-ms 1000

conda run -n ppt_slice python -m harness.tools.review_assist \
  --review-queue harness/reports/DETECTION_RUN/review-queue-with-evidence.json \
  --output harness/reports/DETECTION_RUN/review-queue-assisted.json
```

真实扫描默认在隔离子进程中执行。`--timeout-seconds` 是每次尝试的硬超时；超时子进程必须被终止后才允许重试。检查点只允许在资源指纹、配置指纹、inventory 和检测 `run_id` 一致时恢复。

分页总览和密集复核工具只读取、生成 JPEG/PNG 静态证据。静态辅助复核只会自动完成相邻证据帧均未达到大范围活动阈值的 `AUDIT` 项；任何出现活动、缺帧或证据不足的项目继续保持待人工/视觉复核。
