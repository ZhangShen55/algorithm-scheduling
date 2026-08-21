from pathlib import Path

from packages.operator_registry_client import (
    FileLoggingSettings,
)
from packages.operator_registry_client import (
    configure_logging as configure_shared_logging,
)

from app.core.settings import LoggingSettings


def configure_logging(settings: LoggingSettings) -> None:
    project_root = Path(__file__).resolve().parents[2]
    directory = settings.directory
    if directory.is_absolute():
        try:
            relative_directory = directory.resolve().relative_to(project_root)
        except ValueError:
            project_root = directory.resolve().parent
            relative_directory = Path(directory.name)
    else:
        relative_directory = directory
    shared_settings = FileLoggingSettings.from_mapping(
        {
            "level": settings.level,
            "directory": str(relative_directory),
            "file_name": settings.file_name,
            "max_file_size_mib": settings.max_file_size_mib,
            "retention_days": settings.retention_days,
            "stdout_enabled": settings.stdout_enabled,
            "file_enabled": settings.file_enabled,
        },
        service_name="ocr",
        project_root=project_root,
    )
    configure_shared_logging(shared_settings)
