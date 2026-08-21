import logging

from packages.operator_registry_client import FileLoggingSettings, configure_logging

from app.core.config import PROJECT_ROOT, settings

LOG_DIR = PROJECT_ROOT / "logs"

class _HotwordFilter(logging.Filter):
    def filter(self, record):
        return ('Attempting to parse hotwords' not in record.getMessage() and
                'Hotword list:' not in record.getMessage() and 'rtf_avg:' not in record.getMessage())

def setup_logging() -> None:
    configured = dict(settings.logging_config)
    project_root = PROJECT_ROOT
    if LOG_DIR != PROJECT_ROOT / "logs":
        # 测试或临时运行可替换整个日志根，但生产配置仍从项目根解析。
        project_root = LOG_DIR.parent
        configured["directory"] = LOG_DIR.name
    configure_logging(
        FileLoggingSettings.from_mapping(
            configured,
            service_name="asr_offline",
            project_root=project_root,
        )
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_HotwordFilter())

    # 日志降噪
    logging.getLogger("ai-voice-analysis-service").setLevel(logging.WARNING)
    logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
