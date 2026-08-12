# 里程碑 2B GPU 实例真实性证据采集

## 范围

本场景验证 Task 8 的证据采集器行为，使用 fake `docker`、`nvidia-smi` 和
`/proc` 树，不连接远程服务器，不执行真实 CUDA 或算子推理。本场景通过只能证明
验收工具对规定输入能正确失败闭合，不代表 18 个 GPU 实例已经验收。

## 固定合同

- 运行模式必须提供 `--trigger-file` JSON argv 数组；直接调用进程，不经 shell。
- 触发命令存活期间持续采样；未同步采到目标 CUDA PID 必须失败。
- 容器环境中的 `PLATFORM_INSTANCE_ID`、`PLATFORM_GPU_ID`、`GPU_PROCESS_NAME`
  必须和参数一致。`NVIDIA_VISIBLE_DEVICES` 与 Docker device request 必须唯一绑定目标卡。
- 容器内 CUDA probe 必须返回可用、`device_count=1`、`current_device=0`，且 GPU UUID
  与宿主目标卡一致。
- 宿主 CUDA PID 必须同时出现在 `docker top`，`/proc/<pid>/cgroup` 必须包含
  完整 64 位容器 ID，`NSpid` 必须给出宿主/容器 PID 映射。短 ID 和前缀匹配无效。
- `nvidia-smi` 的 `process_name` 必须和预期算子名相等；显示 `python` 时不允许
  用环境变量伪造通过。
- 采样期间每轮复查容器完整 ID；容器重启或替换后本次证据失效。
- `--assert-stopped --evidence <prior.json>` 只检查先前已精确映射的 CUDA PID。
  若 PID 被其他容器复用，当前 cgroup 不同时不判为残留。
- JSON 输出只能位于 release 归档中的 `gpu-instances` 或 `recovery`，使用
  `0600` 临时文件、`fsync` 和无覆盖发布。触发命令只记录可执行文件名和参数数量。

## 本地验证

从 `algorithm-scheduling-platform` 执行：

```bash
.venv/bin/python -m pytest -q tests/test_milestone_2b_gpu_evidence.py
.venv/bin/ruff check deploy/scripts/verify-gpu-instance \
  tests/test_milestone_2b_gpu_evidence.py
.venv/bin/python -m mypy --strict deploy/scripts/verify-gpu-instance \
  tests/test_milestone_2b_gpu_evidence.py
.venv/bin/python -m py_compile deploy/scripts/verify-gpu-instance \
  tests/test_milestone_2b_gpu_evidence.py
```

## 证据与结论

- RED：首批 `13` 项测试均因验证器不存在而失败；增量 PID 复用测试曾暴露
  停止检查误判残留的问题，在实现 cgroup 复核后转为 GREEN。
- GREEN：`18` 项 fake 运行时行为测试通过，覆盖 cgroup v1/v2、外来 PID、
  容器 ID 前缀碰撞、错误进程名、双可见卡、触发过快、容器重启、输出软链接/冲突/
  并发和停止残留。
- 证据层级：达到 GPU 验收工具的单元/脚本行为层级；未达到服务器 Docker、
  NVIDIA Driver、真实 CUDA、真实推理或三卡部署验收层级。
- 剩余风险：目标服务器的 NVIDIA 驱动版本可能不支持 compute-apps 的 `gpu_uuid`
  字段，工具已提供按 UUID `--id` 查询的降级路径，但必须在 Task 12-14 真机预检中
  校准；MIG 模式不在当前三张物理卡设计范围内。
