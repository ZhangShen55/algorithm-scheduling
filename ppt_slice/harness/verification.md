# 验证记录

## 固定验证命令

```bash
conda run -n ppt_slice python -m compileall -q app harness tests
conda run -n ppt_slice python -c "from app.main import app; print(app.title)"
conda run -n ppt_slice python -m unittest discover -s tests -v
openspec validate detect-ppt-video-playback-segments --strict
```

## 2026-08-07 初始状态

- OpenSpec 严格校验：通过。
- 语料发现测试：6 项通过。
- 冻结运行：`20260807-baseline-v1`，40 个目录页、37 个 P 视频，探测失败 0。
- 集合划分：校准集 25，保留集 12；视频总时长约 32.99 小时。
- 动态检测实现与全量远程语料：尚未执行。
- 原始 MP4 落盘：禁止；后续 runner 测试必须检查工作目录不存在 `.mp4`。
- 失败和跳过项：必须在对应 `run_id` 报告中列出。

## 2026-08-07 本地回归 `20260807-local-regression-v1`

- 范围：持续动态检测、旧流程回退、共享结果契约、Harness 工具和内存真实编解码。
- 环境：Conda `ppt_slice`。
- MP4 边界：仅使用测试内存中的合成 MP4 字节；未写入原始或预览 MP4 文件。
- 执行固定验证命令中的 `compileall`、应用导入和完整测试命令。
- 结果：应用导入成功，`57` 项测试全部通过。
- 旧流程回退：`test_disabled_detection_keeps_legacy_stable_frame_flow` 通过。
- 无 MP4 落盘真实编解码：
  `test_real_decode_suppresses_dynamic_and_recovers_stable_slide_without_disk_mp4` 通过。
- 结论：本地回归通过；该结论不替代远程全量语料复核。

## 2026-08-07 全量旧算法基线 `20260807-baseline-v1`

- inventory：37 个 P 视频，总时长约 32.99 小时；探测失败 0。
- 处理结果：37/37 完成，失败 0，总切片 2816。
- 密度统计：按 60 秒窗口记录每分钟切片数，达到 5 张的密集窗口共 221 个。
- 关键帧间隔：各视频 p95 约 `839-1226ms`，观察到的单视频最大间隔最高 `2678ms`。
- 性能：旧关键帧基线累计处理 `4101.125s`。
- 数据边界：结果只含轻量 JSON；所有条目的 `mp4_persisted=false`。
- 证据：`harness/reports/20260807-baseline-v1/full-corpus-baseline.json`。

## 2026-08-07 已知样本阈值校准 `20260807-threshold-calibration-v3`

- 播放标注：`[2368000,2627000)`；检测：`[2372747,2622064)`。
- 时间覆盖约 `96.3%`；开始/结束误差约 `4.7s/4.9s`。
- 核心 `[2388000,2607000)` 切片数：0。
- 动态前后恢复：检测前 `2365s` 有稳定切片，检测后 `2627s` 恢复稳定切片。
- 末尾持续滚动：检测 `3291693-3299833ms`。
- 处理耗时：`141.870s`；`mp4_persisted=false`。
- 结论：已知样本强制回归通过；仍需完整校准集、保留集和最终全量复核。

## 2026-08-07 真实 HTTP 任务 `harness-live-20260807-v1`

- 服务：`app.main:app`，`127.0.0.1:9001`；健康和版本接口通过。
- 输入：已知 `0912` 远程 P 视频 URL，服务直接从 URL 解码，未下载或落盘源 MP4。
- 受理：`task_id=harness-live-20260807-v1`、`operator_task_id=ppt-live-20260807-v1`，受理状态 `50`。
- 终态：状态 `60`、切片 `33` 张，manifest 的 `count`、`images` 数量和实际 JPEG 文件数均为 `33`。
- 动态区间：`2372747-2622064ms`、`3291693-3299833ms`，与校准直接扫描结果一致。
- 原子与数据边界：结果目录无 `.part`、`.mp4` 或 `.gif`；只包含 PPT JPEG 和 `manifest.json`。
- 回调：服务日志仅出现 1 次该任务的“终态回调成功”，本地回调夹具返回 HTTP `200`；任务完成后容量计数恢复为 0。
- 性能：本机同时执行 Harness 远程静态证据提取，真实任务耗时 `679.329s`，该并发负载下处理速率约 `4.86x`。

## 2026-08-07 v5 证据恢复与动态簇本地回归

- `20260807-calibration-v5` 完整证据恢复：1201/1201 完成、失败 0，`mp4_persisted=false`。
- 合并既有复核结论后，算法候选 93/93 已复核；保守静态辅助完成 512 个审计窗口，596 个存在变化的审计窗口保留。
- v5 发现长静止镜头拆段和区间内错误切片，结论为失败，证据保留在 `harness/reports/20260807-calibration-v5/` 和 Git 忽略的对应 artifacts 目录。
- 动态簇 TDD：先验证缺少聚簇函数、配置和抑制流水线时测试失败，再实现 `cluster_gap_ms=90000`、`cluster_min_segments=3` 及尾部候选内存延迟发布。
- 完整单元、契约、Harness 和内存真实编解码回归：78 项全部通过。
- 测试过程未写入源 MP4、远程视频副本或视频预览；真实编解码夹具仅在内存中构造 MP4 字节。

## 2026-08-07 v7 光流保活本地回归

- 失败复现：`test_sustained_dynamic_frames_are_suppressed_and_reported`、`test_repeated_dynamic_cluster_suppresses_slices_from_long_static_gaps`、`test_real_decode_suppresses_dynamic_and_recovers_stable_slide_without_disk_mp4` 均因稳定页恢复过慢失败。
- TDD 红灯：`test_strong_activity_without_motion_uses_normal_stable_exit` 在修正前得到 `STABILIZING != STABLE`，证明测试覆盖了无条件宽限问题。
- 定向绿灯：强活动正常退出、弱光流保活以及原 3 个回归共 5 项全部通过。
- 完整回归：`conda run -n ppt_slice python -m unittest discover -s tests -v`，`87` 项全部通过。
- MP4 边界：内存真实解码测试通过 `mp4_persisted=false`；未保存远程视频、完整副本、MP4/GIF 预览或可还原视频的中间文件。

## 2026-08-07 v7d 长会话真实回归失败与撤回

- 真实扫描：`harness/reports/20260807-targeted-v7d/`，4/4 完成，所有结果 `mp4_persisted=false`。
- 强制样本：`0912` 主区间 `2372747-2639249ms`，起止误差约 `4.7s/12.2s`，仍满足 20 秒边界要求。
- 长会话失败：莎士比亚 `15:55` 误合并为 `12180-2680721ms`；`16:45` 仍漏报绝大部分影片。本轮明确标记失败，不执行保留集揭示。
- 撤回后完整回归：`conda run -n ppt_slice python -m unittest discover -s tests -v`，`89` 项全部通过。
- 静态语义证据：莎士比亚 `15:55` 的 `1308s、1400s、1640s、1945s` 与 `16:45` 的 `1759s、1765s、2100s` 联系表均确认为影片播放，而不是 PPT。
- 音轨检查：目标视频有 AAC 音轨，但音量在课件/桌面与影片阶段重叠，不能作为可验收判据。
- 数据边界：没有写入任何 MP4、完整副本、MP4/GIF 预览或可还原视频的中间文件。

## 2026-08-10 文件名与黑屏过滤回归

- 固定验证：`compileall` 通过，`from app.main import app` 导入成功，完整 `unittest` 共 `92` 项通过，OpenSpec 严格校验通过，`git diff --check` 通过。
- 文件名契约：共享结果测试确认正式名称使用 `ppt-0001-f{frame_seq}-t{snap_time}s.jpg`，其中视频定位以 `t` 秒数为准。
- 黑屏回归：纯黑启动帧不发布；黑底但带可见内容的正常课件仍可发布；过滤逻辑覆盖动态检测开启和关闭两条切片路径。
- 计算思维课程：直接从远程 URL 流式处理，原结果中的首张 `t3s` 图片为纯黑；过滤后切片数由 15 变为 14，结果目录无 MP4、GIF 或 `.part`。
- `0912` 强制样本：重新流式处理耗时 `151.247s`，生成 31 张切片、3 个动态区间、0 张黑屏。与过滤前结果逐项比较，图片时间戳、JPEG 内容和动态区间完全一致。
- 动态区间：`2372747-2639249ms`、`3058328-3072573ms`、`3291693-3299833ms`，均为 `sustained_visual_change`。
- 结论范围：本地回归、黑屏缺陷回归和两门真实课程定向验证通过；低运动/重复帧视频漏报仍未解决，因此校准集、保留集和最终全量验收继续保持未完成。
