# Platform Harness

Harness stores reproducible evidence for architecture and runtime claims. `AGENTS.md` contains durable rules; Harness contains changing verdicts, commands, environments and remaining gaps.

Files:

- `architecture-review.md`: design-to-implementation evidence matrix.
- `change-ledger.md`: material change records and remaining risks.
- `verification.md`: repeatable commands and evidence tiers.
- `scenarios/`: scenario-specific inputs, expected outputs and evidence.

当前完整产品收口入口为
`scenarios/milestone-2b-business-lanes-closure.md`。它在历史三卡部署报告之上按
FaceRec、PPT/ASR、视觉、在线和 243 条完整验收的依赖顺序推进。

A 服务阶梯、突发、混合、过载、长稳和故障恢复的新增验证入口为
`scenarios/milestone-2b-extreme-load-campaign.md`。该场景是原 217 条反例、26 条压力/恢复和
6 项 B 级复核之外的附加容量验证；当前只完成只读基线，目标机磁盘仍处于红线，不能据此宣称
极限 Campaign 或里程碑 2B 已通过。

统一算子 TOML/Compose 配置归属、正整数容量、可归属租约、同步 HTTP 跨 TTL 续租、Online
Gateway 单图 OCR 和里程碑 2B 精确旧镜像清理的独立规划/验收入口为
`scenarios/unified-operator-capacity-leases-and-online-ocr.md`。该场景当前是待实施基线，不能替代
里程碑 2B 的真实业务泳道和最终验收证据。

Do not change a verdict to `符合` until the recorded command proves the required evidence tier. Health-only startup is not runtime closure.
