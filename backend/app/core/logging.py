"""structlog 設定：structured logging 收口（NFR 硬化，Phase 9）。

- ``app_env == "development"``：彩色 ConsoleRenderer（人類可讀）
- 其他環境：JSONRenderer（一行一 JSON，可被 log 系統查詢）
- stdlib logging（uvicorn、sqlalchemy 等）經 ``ProcessorFormatter`` 統一輸出格式
- ``settings.log_level`` 在此第一次真正被接上
- ``structlog.contextvars``（由 RequestContextMiddleware 綁定 request_id）
  會自動出現在每一行 log，包含例外 log
"""

import logging

import structlog

from app.core.config import settings

_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
]


def configure_logging() -> None:
    """設定 structlog 與 stdlib root logger。冪等：重複呼叫只是重設同樣配置。"""
    dev = settings.app_env == "development"
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if dev:
        # ConsoleRenderer 自己會把 exc_info 渲染成彩色 traceback
        renderer: structlog.typing.Processor = structlog.dev.ConsoleRenderer()
        tail: list = []
    else:
        renderer = structlog.processors.JSONRenderer()
        tail = [structlog.processors.format_exc_info]

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *tail,
            renderer,
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
