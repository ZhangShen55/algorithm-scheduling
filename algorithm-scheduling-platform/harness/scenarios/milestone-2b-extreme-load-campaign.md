# 里程碑 2B A 服务极限负载 Campaign

## 当前范围

- OpenSpec 变更：`run-milestone-2b-extreme-load-campaign`。
- 当前权威拓扑：七类算子、21 个算子实例、18 个 GPU 实例、3 个 CPU PPT 实例、四个平台服务和四个中间件。
- A 服务模拟器只允许访问 `control-service:18100` 和 `online-gateway-service:18103`。
- Text Analysis、`PPT_KEYWORDS` 和 `COURSE_OVERVIEW` 不属于本 Campaign。
- 本场景是现有 217 条反例、26 条压力/恢复用例和 6 项 B 级人工复核之外的附加真实负载验证，不能替代原门禁。

## 初始保护基线

- 开始分支：`codex/milestone-2b-three-gpu-deployment`。
- 开始 SHA：`3cefc915317428cf17db037ba16023b48cd59783`。
- `text_analysis/README.md`、`5{n++}`、三个算子 Docker README、运维可视化设计草稿、`ppt_slice/docker/` 和 `text_analysis/docker/` 是开始前已有的用户 dirty/untracked 内容；本变更不得覆盖、删除或提交。
- `standardize-service-file-logging` 当前为 `54/72`，`retire-text-analysis-from-scheduling-platform` 当前为 `50/62`。剩余项主要是同一新 SHA 的远端构建、真实推理、日志、七算子 release 与最终复审，因此开始 SHA 不是最终 Campaign SHA。
- 最终 Campaign SHA 只能在上述两项的当前必需实现、七算子基线和本 Campaign 实现都纳入同一 clean commit 后冻结。

机器可读基线见 `harness/baselines/milestone-2b-extreme-load-campaign-initial.json`。

## 2026-08-23 目标服务器只读盘点

- 目标：`192.168.29.11`，x86_64、80 逻辑 CPU、125 GiB 内存、Docker 26.1.4。
- Docker 当前共 50 个容器：8 个运行、42 个停止；共 475 个镜像。没有执行删除、停止、重启、重标或 prune。
- 根文件系统约 1.5 TB，剩余约 103 GB、7%；`/data/course` 与 `/data/result` 位于同一文件系统且均存在。
- 由于剩余比例低于 10%，当前已经触发 Campaign 磁盘红线。允许继续本地实现、只读盘点和精确清理 dry-run；禁止直接启动远端负载阶梯。
- GPU0/GPU1 为 RTX 4090 D，GPU2 为 RTX 3090；三卡均约 24 GiB，盘点时没有计算进程。
- 服务器 checkout 停留在 `5f973adae6a81580ecd285ee81e203275fa14ba1`，不是本地开始 SHA。
- `18100`、`18103` 对外监听；PostgreSQL、Redis、Kafka、MongoDB、`18101`、`18102` 只在回环监听，符合当前端口边界。
- 旧 21 个七算子容器当前均已停止；四个平台服务与四个中间件构成当前 8 个运行容器。

## 负载主机基线

- 主机：`zhangshendeMacBook-Pro.local`，Mac17,2、arm64、10 逻辑 CPU、32 GiB 内存。
- 主地址：`192.168.28.144`；目标服务器为另一台 x86_64 主机，二者不共享 CPU、内存或 GPU。
- 系统 Python 为 3.9.6，不作为 Campaign Python 权威；实际运行必须使用平台 `.venv` 的 Python 3.11 依赖并记录版本。
- 打开文件上限为 1,048,575；正式加压仍必须实时记录 CPU、内存、socket、文件句柄、网络和事件循环漂移，避免把负载机上限误归因于平台。

## 当前证据结论

- 已达到：工作区保护、目标机只读库存、GPU/磁盘/端口和负载主机基线。
- 尚未达到：最终 clean Git SHA、Campaign 本地代码和测试、媒体源下载基线、常驻部署、镜像清理 dry-run、远端七算子新 release、阶段 0–6、4 小时长稳和最终清理。
- 已知质量阻断：`ASR-013` 仍为 24 个中英混合术语片段中 9 个严重错误。性能测试可以继续，但最终结论必须保持质量阻断，除非同一最终 SHA 的新证据解除它。

## 安全门禁

1. 目标机磁盘恢复到警戒线以上前，不进入负载阶梯。
2. 远端生命周期和故障注入始终只有一个写入控制者。
3. 清理只能依据经审核的完整容器/镜像 ID dry-run 计划执行。
4. 禁止 `docker system prune -a`、`docker compose down -v`、删除卷、删除 `/data/result`、删除模型和改写历史 release。
5. 每一阶段必须原子发布原始证据；未执行、证据缺失或重复 ID 不得聚合为通过。
