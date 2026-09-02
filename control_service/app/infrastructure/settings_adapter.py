from packages.platform_common.config import PlatformSettings

from ..core.config import SERVICE_ROOT, ControlSettings


def to_platform_settings(settings: ControlSettings) -> PlatformSettings:
    """Adapt service-owned settings to shared components during migration."""

    return PlatformSettings(
        service_name=settings.service.name,
        environment=settings.service.environment,
        log_level=settings.service.log_level,
        trace_header=settings.service.trace_header,
        logging=settings.logging,
        project_root=SERVICE_ROOT,
        postgres_dsn=settings.postgres.dsn,
        redis_url=settings.redis.url,
        orchestrator_metrics_url=settings.orchestrator.metrics_url,
        operator_registry_token=settings.operator_registry.management_token,
        trusted_operator_service_urls=settings.operator_registry.trusted_service_urls,
    )
