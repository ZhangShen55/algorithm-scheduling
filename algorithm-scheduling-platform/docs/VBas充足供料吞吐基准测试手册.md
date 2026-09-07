# VBas 充足供料吞吐基准测试手册

## 1. 目的

本手册用于在 `192.168.29.11` 三张 NVIDIA GPU 上分别测量：

- `C_vbas`：预生成真实帧直连 VBas 时的裸吞吐；
- `C_feed`：真实 FFprobe/FFmpeg 抽帧到零延迟 sink 的供料能力；
- `C_dispatch`：预抽帧通过 Control 离线租约、实例路由和真实 VBas HTTP 的分发能力；
- D：从课程任务到视觉终态的完整链路效率。

只有完整稳态窗口、原始时序和终态证据齐全的 attempt 才能用于推荐参数。

## 2. 稳定合同

- 不改变 `/ImageDetect/teacher/v1.0.0` 和 `/ImageDetect/student/v1.0.0` 的路径、字段和响应。
- 裸能力测试直连 `127.0.0.1:18981/28981/38981`，不经过 Kafka、Control 租约或外部网络。
- 教师、学生分别执行，然后使用确定性 `50%/50%` 顺序执行混合负载。
- 默认单实例阶梯为 `8 -> 6 -> 4 -> 3 -> 2 -> 1`；媒体活动流阶梯为 `16 -> 12 -> 8 -> 6 -> 4 -> 2 -> 1`。
- 每档重启三个 VBas，确认模型就绪后再起测，不继承上一档的显存平台和在途请求。
- 预热默认 60 秒；稳态必须同时满足不少于 600 秒和 100 个成功 batch。
- 教师单实例 `c8` 首轮 10,008 batch 长稳耗时约 32.4 分钟，证明 10,000 batch
  不适合作为所有负载档的硬门槛；学生低档以 600 秒长稳窗口评估显存趋势。
- 错误率上限为 1% 时至少观察 100 个响应后判定；所有错误仍写入原始记录。

权威默认参数位于
[`harness/config/vbas-throughput-benchmark.toml`](../harness/config/vbas-throughput-benchmark.toml)。

## 3. Fixture 准备

fixture 目录位于 `/data/course/vbas-benchmark/fixtures`，不进入 Git。语料必须来自真实课程视频，并包含教师常见画面、学生多人画面和无人画面。

```bash
cd /root/workspace/algorithm-scheduling/algorithm-scheduling-platform

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python \
  scripts/run_vbas_throughput_benchmark.py manifest \
  --fixture-root /data/course/vbas-benchmark/fixtures \
  --teacher /data/course/vbas-benchmark/fixtures/teacher/teacher-01.jpg \
  --student /data/course/vbas-benchmark/fixtures/student/student-01.jpg \
  --multi-person /data/course/vbas-benchmark/fixtures/student/multi-person-01.jpg \
  --empty /data/course/vbas-benchmark/fixtures/shared/empty-01.jpg \
  --source-video 'teacher-video-url' \
  --source-video 'student-video-url' \
  --output /data/course/vbas-benchmark/fixtures/manifest.json

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python \
  scripts/run_vbas_throughput_benchmark.py validate-fixtures \
  --fixture-root /data/course/vbas-benchmark/fixtures \
  --manifest /data/course/vbas-benchmark/fixtures/manifest.json
```

manifest 只记录相对路径、字节数、分辨率、类别和 SHA-256；源 URL 只记录 SHA-256，不记录原文。

## 4. 预检

```bash
export CAMPAIGN_ID="vbas-throughput-$(date +%Y%m%d%H%M%S)"
export REPORT_ROOT="$PWD/deploy/reports/vbas-throughput"

PYTHONPATH="$PWD:$PWD/.." .venv/bin/python \
  scripts/run_vbas_throughput_benchmark.py preflight \
  --campaign-id "$CAMPAIGN_ID" --attempt 1 \
  --report-root "$REPORT_ROOT" --case-id preflight \
  --config-path "$(docker inspect algorithm-operators-vbas-gpu0-1 --format \
    '{{range .Mounts}}{{if eq .Destination "/workspace/config.toml"}}{{.Source}}{{end}}{{end}}')" \
  --fixture-root /data/course/vbas-benchmark/fixtures \
  --fixture-root-in-container /data/course/vbas-benchmark/fixtures \
  --manifest /data/course/vbas-benchmark/fixtures/manifest.json \
  --control-url http://127.0.0.1:18100 \
  --target 'vbas-gpu0,http://127.0.0.1:18981,0,algorithm-operators-vbas-gpu0-1' \
  --target 'vbas-gpu1,http://127.0.0.1:28981,1,algorithm-operators-vbas-gpu1-1' \
  --target 'vbas-gpu2,http://127.0.0.1:38981,2,algorithm-operators-vbas-gpu2-1'
```

预检必须确认三张卡的型号/显存/驱动、三个互异容器和 GPU 归属、镜像 ID、模型就绪、fixture 可读、日志可写和离线租约归零。

## 5. VBas 阶梯

```bash
CAMPAIGN_ID="$CAMPAIGN_ID" MODE=teacher SCOPE=single \
  deploy/scripts/run-vbas-throughput-staircase
CAMPAIGN_ID="$CAMPAIGN_ID" MODE=student SCOPE=single \
  deploy/scripts/run-vbas-throughput-staircase
CAMPAIGN_ID="$CAMPAIGN_ID" MODE=teacher SCOPE=triple \
  deploy/scripts/run-vbas-throughput-staircase
CAMPAIGN_ID="$CAMPAIGN_ID" MODE=student SCOPE=triple \
  deploy/scripts/run-vbas-throughput-staircase
CAMPAIGN_ID="$CAMPAIGN_ID" MODE=mixed SCOPE=triple \
  deploy/scripts/run-vbas-throughput-staircase
```

阶梯脚本会暂时修改当前 VBas 容器实际挂载的 `config.toml`，每档重启三实例，在退出、中断或失败时恢复原配置。高位档触发护栏时证据保留为失败，脚本继续执行下一低档。
当代码以不含 `.git` 和 `.venv` 的 release 快照部署时，显式传入
`GIT_SHA` 与 `PYTHON_BIN`；证据仍记录被发布的准确提交，且不要求服务器持有 GitHub 私钥。
使用 `TIERS='8 6' ATTEMPT=2` 可只重跑指定档位，并将证据写入新的 attempt；
不得使用相同 attempt 覆盖已有失败或通过结果。

## 6. 纯媒体和预抽帧分发

纯媒体使用固定本地视频，将 `--active-streams` 依次设为 `16/12/8/6/4/2/1`，且 `--max-concurrent-processes` 不小于活动流数。
每个媒体 attempt 会记录输入视频 SHA-256，并在成功或失败后清理当前 campaign
生成的临时帧目录；固定输入视频和其他 campaign 目录不会被删除。

```bash
CAMPAIGN_ID="$CAMPAIGN_ID" STREAM=student \
  VIDEO=/data/course/vbas-benchmark/videos/student.mp4 \
  deploy/scripts/run-vbas-media-feed-staircase
```

阶梯脚本默认逐档执行 `16 12 8 6 4 2 1`；可用 `TIERS` 和 `ATTEMPT`
精确重跑失败档，已有 attempt 仍禁止覆盖。

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python \
  scripts/run_vbas_throughput_benchmark.py media \
  --campaign-id "$CAMPAIGN_ID" --attempt 1 \
  --report-root "$REPORT_ROOT" --case-id media-student-16 \
  --video /data/course/vbas-benchmark/videos/student.mp4 \
  --course-root /data/course --stream student \
  --active-streams 16 --max-concurrent-processes 16
```

预抽帧分发保留 Control 离线租约和真实 VBas HTTP：

```bash
PYTHONPATH="$PWD:$PWD/.." .venv/bin/python \
  scripts/run_vbas_throughput_benchmark.py dispatch \
  --campaign-id "$CAMPAIGN_ID" --attempt 1 \
  --report-root "$REPORT_ROOT" --case-id dispatch-student \
  --fixture-root /data/course/vbas-benchmark/fixtures \
  --fixture-root-in-container /data/course/vbas-benchmark/fixtures \
  --manifest /data/course/vbas-benchmark/fixtures/manifest.json \
  --control-url http://127.0.0.1:18100 --stream student \
  --batch-size 8 --batch-count 1000 --target-slots 6
```

## 7. 结论规则

- 最高稳定吞吐档和达到该吞吐 95% 的最低并发档必须同时报告。
- 候选拐点至少重复两轮；吞吐相对偏差超过 5% 时保持“未收敛”。
- 显存从 `M_ready` 升到 `M_warm` 后形成平台可以通过；稳态后半段继续增长、越过硬护栏、OOM 或容器重启必须失败。
- ready queue 不足时的空档归因于供料；ready 足够但槽位空闲时才归因于租约/分发。
- 完整链路只应用隔离测试选出的候选参数，每轮只改一个变量或直接依赖的一组变量。

## 8. 证据目录

```text
deploy/reports/vbas-throughput/{campaign_id}/{case_id}/attempt-{NNN}/
├── identity.json
├── load.json | media.json | dispatch.json | preflight.json
├── gpu.json
└── summary.json
```

证据是 write-once，默认权限 `0600`。中断轮次必须使用新 attempt，禁止覆盖或在事后补写为通过。
