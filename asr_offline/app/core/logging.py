import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import PROJECT_ROOT


LOG_DIR = PROJECT_ROOT / "logs"

class _HotwordFilter(logging.Filter):
    def filter(self, record):
        return ('Attempting to parse hotwords' not in record.getMessage() and
                'Hotword list:' not in record.getMessage() and 'rtf_avg:' not in record.getMessage())

def setup_logging() -> None:
    # 1. 清空旧 handler
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)

    # 2. 创建固定目录的按日日志归档，保留近7天
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_hdl = TimedRotatingFileHandler(
        LOG_DIR / "asr_service.log",
        when="midnight",
        interval=1,
        backupCount=7,
        encoding="utf-8",
    )
    console_hdl = logging.StreamHandler(sys.stdout)

    # 3. 将过滤器挂到 handler上
    file_hdl.addFilter(_HotwordFilter())
    console_hdl.addFilter(_HotwordFilter())

    # 4. 配置
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[file_hdl, console_hdl]
    )

    # 日志降噪
    logging.getLogger("ai-voice-analysis-service").setLevel(logging.WARNING)
    logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
