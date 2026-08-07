import logging
from logging.handlers import RotatingFileHandler

from app.core.settings import LoggingSettings


def configure_logging(settings: LoggingSettings) -> None:
    settings.directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(settings.level)
    if any(getattr(handler, "_ocr_service_handler", False) for handler in root.handlers):
        return

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream._ocr_service_handler = True
    root.addHandler(stream)

    file_handler = RotatingFileHandler(
        settings.directory / "ocr-service.log",
        maxBytes=settings.max_size_mb * 1024 * 1024,
        backupCount=settings.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._ocr_service_handler = True
    root.addHandler(file_handler)
