# 实时负载路由远端验证记录

## 验证范围

- 变更：`balance-operator-routing-by-live-load`
- Git SHA：`d19e5e46b9cb0c78d775727e1cf33a75a4321df8`
- 服务器：`192.168.29.11`
- 发布标签：`v1.0_260827`
- 验证日期：2026-08-27
- 本记录只引用远端受限证据路径和脱敏统计，不包含 Token、媒体内容或完整请求/响应。

## 发布与 Stage45

Stage45 结果为 `CODEX_STAGE45_COMPLETE failures=0`。21 个算子实例均完成运行时检查；18 个
GPU 实例分别验证 GPU 0/1/2，3 个 PPT CPU 实例完成真实切片；容器恢复、注册恢复和运行时
包一致性均通过。

Stage45 后快照：29/29 容器健康，21/21 实例注册，18 个 GPU 实例、3 个 CPU 实例，三张卡
绑定正确，活跃租约为 0，`/data/course` 和 `/data/result` 存在。VBas 为
`1024/1024/0`，Vision 为 `max_batch_size=8/max_concurrency=16`。

## 离线 20 路学生行为

证据：
`deploy/reports/milestone-2b/releases/v1.0_260827/d19e5e46b9cb0c78d775727e1cf33a75a4321df8/scenarios/offline20.json`

证据 SHA-256：`d6fc2eefd27e905f773cd4d96022c4ad3febaeb3cb3bfffdb0852035260dd1ed`。

- 20/20 北向请求成功受理，20/20 任务最终状态 `60`，节点完成，失败/拒绝批次为 0。
- Vision 真实批次分布：`vbas-gpu0=821`、`vbas-gpu1=820`、`vbas-gpu2=519`。
- 三个实例均处理真实批次，整个窗口没有固定实例独占；最终 Kafka lag 为 0，活跃租约和
  上报在途归零。
- 场景总体标记为“失败”，唯一原因是验证器要求“最早 `first_seen_at` 的租约 cohort
  必须同时包含三个实例”。实际首个 cohort 在毫秒级时间差下先出现一个实例，后续
  1731 个采样点覆盖三个实例；该严格门槛未满足，不能重标为通过。

## 在线 1000 路学生单图

首次执行因负载机 `nofile` 软上限 1024 小于所需 1256，在预检阶段无效；该证据保留在
`scenarios/online1000.json`。

提高当前 SSH shell 的 `nofile` 到 8192 后执行两次：

- R2：`1000/1000` 同时在途，`1000/1000` 释放，三实例路由为 `358/348/252`；999 个成功，
  1 个 HTTP 200/业务码 `50000` 服务错误。证据 SHA-256：
  `aefce400af8a4c8ba2a8f597088185f5a7f5fcd31b8ac3d46e0ffe5837b1544a`。
- R3：`1000/1000` 同时在途，`1000/1000` 释放，三实例路由为 `359/351/266`；999 个成功，
  1 个业务码 `50301` 容量不足。证据 SHA-256：
  `1bbde530dbdbf70c50db6cf7c5aef300f2e043cbd33f8126cbae6b059f8e6dec`。

两次均证明 Online Gateway 经过公共租约将请求分散到三个实例并完成释放，但由于各有 1
个非成功响应，不能满足 1000/1000 全部成功门禁。日志未显示固定 GPU0 独占，VBas 三实例
均有真实调用，最终状态快照仍为 PASS、活跃租约为 0。

## ASR、OCR、PPT 只读调查基线

修复前的 16 路调查记录在
`operator-live-load-routing-readonly-baseline.md`，没有修改算子代码、配置、容器或数据：

- ASR Offline：16/16 受理，真实调用 `gpu0=16、gpu1=0、gpu2=0`。
- PPT Slice：16/16 受理窗口中，真实调用 `cpu0=8、cpu1=0、cpu2=0`。
- OCR：真实调用 `gpu0=72、gpu1=0、gpu2=0`。

该基线确认公共 Redis 租约选择器的首实例偏置同样影响 ASR、OCR、PPT；它不是当前修复后
通过证据。当前变更已在公共注册表层修复，后续应在在线千路问题处理后分别补做三类算子的
新 revision 均衡验证。

## 结论与阻断项

Stage45、发布配置和离线任务结果已形成证据；离线严格首 cohort 门禁和在线 1000/1000
稳定性门禁尚未通过。因此不执行混合负载、旧镜像删除或最终 Campaign 归档。旧镜像、构建
缓存、数据目录和历史证据继续保留，后续应先澄清首 cohort 门槛并定位 `50000/50301` 的
实际原因，再继续 8.x、9.x 和 10.x。
