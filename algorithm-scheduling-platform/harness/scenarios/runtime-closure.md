# Scenario: Platform Runtime Closure

## Preconditions

- Real PostgreSQL, Redis and Kafka containers are healthy.
- Four platform service processes start with their own annotated config.
- Contract operator stubs or real operators register through control-service.

## Required flows

Run PPT-only, ASR-only, teacher-only, student-only and combined submissions. Capture API responses, Kafka offsets, node transitions, selected instances, metrics and retained/removed paths. No test may directly complete a node through the repository.

## Current verdict

Not complete. Worker entrypoints and broker loops require runtime evidence.
