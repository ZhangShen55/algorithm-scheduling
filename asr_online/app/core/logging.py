import logging
from pathlib import Path

from app.core.config import PROJECT_ROOT, settings as _settings
from packages.operator_registry_client import FileLoggingSettings, configure_logging


class _HotwordFilter(logging.Filter):
    def filter(self, record):
        return ('Attempting to parse hotwords' not in record.getMessage() and
                'Hotword list:' not in record.getMessage() and 'rtf_avg:' not in record.getMessage())

def setup_logging(log_path: str | None = None) -> None:
    configured = dict(getattr(_settings, "logging_config", {}))
    if log_path:
        legacy_path = Path(log_path)
        configured.update({"directory": str(legacy_path.parent), "file_name": legacy_path.name})
    settings = FileLoggingSettings.from_mapping(
        configured,
        service_name="asr_online",
        project_root=PROJECT_ROOT,
    )
    configure_logging(settings)
    for handler in logging.getLogger().handlers:
        handler.addFilter(_HotwordFilter())

    # 日志降噪
    logging.getLogger("ai-voice-analysis-service").setLevel(logging.WARNING)
    logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
