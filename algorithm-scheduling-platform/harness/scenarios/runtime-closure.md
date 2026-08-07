# Scenario: Platform Runtime Closure

该场景是完整第一版运行闭环。当前方案 C 的较小基础门槛见
`harness/scenarios/foundation-scheduling-closure.md`；基础闭环不依赖真实 PPT、ASR、视觉或在线算子。

## Preconditions

- Real PostgreSQL, Redis and Kafka containers are healthy.
- Four platform service processes start with their own annotated config.
- Contract operator stubs or real operators register through control-service.

## Required flows

Run PPT-only, ASR-only, teacher-only, student-only and combined submissions. Capture API responses, Kafka offsets, node transitions, selected instances, metrics and retained/removed paths. No test may directly complete a node through the repository.

## Current verdict

Not complete. Worker entrypoints and broker loops require runtime evidence.
