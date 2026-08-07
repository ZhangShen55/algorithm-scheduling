from packages.platform_common.config import PlatformSettings
from services.orchestrator_service.app.core.config import OrchestratorSettings


def to_platform_settings(settings: OrchestratorSettings) -> PlatformSettings:
    """Adapt service-owned settings to shared components during migration."""

    return PlatformSettings(
        service_name=settings.service.name,
        environment=settings.service.environment,
        log_level=settings.service.log_level,
        trace_header=settings.service.trace_header,
        postgres_dsn=settings.postgres.dsn,
        kafka_bootstrap_servers=",".join(settings.kafka.bootstrap_servers),
        control_service_url=settings.control.base_url,
        course_root=settings.storage.course_root,
        result_root=settings.storage.result_root,
    )
