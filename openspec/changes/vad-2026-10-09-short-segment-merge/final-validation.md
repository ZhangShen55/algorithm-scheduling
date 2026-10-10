# Final Validation

## Environment

- Remote host: `server12`, project `/root/workspace/asr_online`
- Runtime: Conda environment `asr`, GPU `cuda:0`, `ngpu = 1`
- Service: `127.0.0.1:8084`, one Uvicorn worker
- VAD: `vad_max_end_silence_ms = 1000`
- Short-segment merge: `vad_merge_max_gap_ms = 720`
- Uvicorn keepalive for long correction calls: `--ws-ping-timeout 120`
- Input: complete `test/教师1_16k.wav`, 2,820.032 seconds, 5,876 input chunks

## Full A/B/C Run

The test ran on `server12` itself over `ws://127.0.0.1:8084`, sequentially as A, B, C. B and C sent input at 0.4 seconds per 0.48-second chunk to model the upstream streaming cadence. The client retained a 250-chunk timestamp window and used a 120-second wall-clock watchdog.

Results are stored in `asr_online/test/full_abc_vad_merge_final_realtime`:

| Route | Audio chunks | Responses | Finished | Elapsed | Failure |
| --- | ---: | ---: | ---: | ---: | --- |
| A / v1.0.1 | 5,876 / 5,876 | 5,884 | 140 | 336.316 s | none |
| B / v1.0.2 | 5,876 / 5,876 | 6,107 | 223 | 2,404.804 s | none |
| C / v1.0.3 | 5,876 / 5,876 | 6,111 | 227 | 2,398.748 s | none |

All three routes had no stalled status, no `bg`/`ed` backwards movement, no finished-segment overlap, and no zero-duration finished result. The response key set remained exactly `key`, `text`, `finished`, `bg`, and `ed`.

The former fragmented region is merged in B into a finished segment from `217.45` to `237.60` seconds, rather than producing one finished result per short VAD event. The first full run also exposed and preserved a test-environment issue: Uvicorn's default 20-second WebSocket ping timeout can close a long correction call with code `1011`; the final run used a 120-second server keepalive timeout and completed successfully.

After the run, the remote service was stopped and port 8084 plus the ASR GPU allocation were confirmed released.

## VAD Three-Way Comparison

2026-10-10 在内网主机 `192.168.29.12` 的同一 GPU、同一代码和同一 v1.0.2 路由上补充了三组顺序对照。每组通过独立 `/tmp` 配置启动，模型就绪后仍等待满 300 秒再测试；每组结束后关闭服务并确认 8084 和 GPU 进程释放。正式 `config.toml` 始终保持 `1000/720`，没有被对照参数覆盖。

- 输入：`test/教师1_16k.wav` 的 98 至 152 秒，共 54 秒、113 个 0.48 秒音频块
- 尾部刷新：15 个静音块；发送间隔 0.4 秒
- 无合并基线：因配置要求正整数，使用 `vad_merge_max_gap_ms = 1` 近似关闭合并
- 产物：`asr_online/test/vad_three_way_comparison`

| 配置 | 响应 | finished / 纠正次数 | <=0.5s | 首个 finished | finished 平均到达时刻 | 平均纠正等待 | 重复 finished 文本 | 总耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| VAD 1000ms，近似无合并 | 178 | 50 | 48 | 4.727s | 53.122s | 1.606s | 1 | 216.706s |
| VAD 1200ms，近似无合并 | 178 | 50 | 48 | 4.796s | 49.271s | 1.485s | 0 | 198.679s |
| VAD 1000ms + merge 720ms | 132 | 4 | 0 | 5.116s | 28.635s | 0.733s | 0 | 63.965s |

三组均完整发送输入，无 stalled、失败、时间戳倒退、finished 重叠、零时长 finished 或响应字段异常。合并组相对 1000ms 无合并基线将 finished 和终端纠正调用从 50 次降到 4 次，0.5 秒以下短段从 48 次降到 0 次，总耗时降低约 70.5%；首个 finished 增加约 0.389 秒，符合 720ms 有界等待预期。仅将 VAD 尾静音从 1000ms 调到 1200ms 在该片段没有减少碎段。

合并组 finished 文本平均长度为 57.5 字，标点数为 17；两个无合并基线平均只有 3.66/4.06 字，标点数为 4/3，表明终端纠正获得了明显更完整的上下文。没有人工标注真值，因此这些指标只能说明上下文、断句和标点改善，不能单独证明字错率降低。

协议没有显式 EOS。切片结束时合并组仍有 `segment-000005` 的非空临时文本未收到 `finished=true`；把尾静音从 5 块增加到 15 块仍不能完成它，说明这是切片边界与 VAD start/end 状态的限制，不是缓存卡死。完整教师音频 A/B/C 验证已经全部跑完且没有 stalled；本次对照也保存了两轮 30 秒边界样本，分别位于 `vad_three_way_comparison_tail5_incomplete` 和 `vad_three_way_comparison_tail15_30s_boundary`，用于复现该限制。
