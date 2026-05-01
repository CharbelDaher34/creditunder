import logging
import logging.handlers
import sys
from pathlib import Path

import structlog

_LOG_DIR = Path("/logs")


def configure_logging(level: str = "INFO") -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # stdlib root logger — two handlers: stdout + rotating file
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not root.handlers:
        stdout_handler = logging.StreamHandler(sys.stdout)
        file_handler = logging.handlers.RotatingFileHandler(
            _LOG_DIR / "processor.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        for h in (stdout_handler, file_handler):
            h.setLevel(getattr(logging, level.upper(), logging.INFO))
            root.addHandler(h)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
