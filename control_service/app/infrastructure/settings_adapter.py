from packages.platform_common.config import PlatformSettings

from ..core.config import ControlSettings


def to_platform_settings(settings: ControlSettings) -> PlatformSettings:
    """Adapt service-owned settings to shared components during migration."""

    return PlatformSettings(
        service_name=settings.service.name,
        environment=settings.service.environment,
        log_level=settings.service.log_level,
        trace_header=settings.service.trace_header,
        postgres_dsn=settings.postgres.dsn,
        redis_url=settings.redis.url,
    )
