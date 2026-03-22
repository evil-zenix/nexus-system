"""
Логирование Nexus системы через structlog
JSON логи для easy парсинга в ELK/Datadog
"""
import logging
import sys
from typing import Any

import structlog

from config.settings import settings


def configure_logging() -> None:
    """
    Конфигурировать structlog для всей системы.
    Использует JSON логи для production и красивый вывод для dev.
    """
    
    # Определить процессор логов в зависимости от окружения
    if settings.is_production:
        # Production: JSON логи для парсинга
        processors = [
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),  # JSON вывод
        ]
    else:
        # Development: красивый вывод с цветом
        processors = [
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(),  # Красивый вывод
        ]
    
    # Конфигурация structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # Конфигурация стандартного logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )
    
    # Установить уровень логирования для основных библиотек
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(
        logging.DEBUG if settings.is_development else logging.INFO
    )
    logging.getLogger("sqlalchemy").setLevel(
        logging.DEBUG if settings.is_development else logging.WARNING
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """
    Получить логгер с именем модуля.
    
    Args:
        name: Имя модуля (__name__)
    
    Returns:
        BoundLogger для логирования
    """
    return structlog.get_logger(name)


class LoggerContext:
    """
    Контекстный менеджер для добавления информации в логи.
    
    Пример:
        with LoggerContext(request_id="123"):
            logger.info("event")  # будет включен request_id
    """
    
    def __init__(self, logger: structlog.BoundLogger, **context):
        self.logger = logger
        self.context = context
    
    def __enter__(self):
        for key, value in self.context.items():
            self.logger = self.logger.bind(**{key: value})
        return self.logger
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


# ============================================================================
# Вспомогательные функции логирования
# ============================================================================

def log_event(
    logger: structlog.BoundLogger,
    event: str,
    level: str = "info",
    **kwargs: Any
) -> None:
    """
    Логировать событие с дополнительной информацией.
    
    Args:
        logger: BoundLogger экземпляр
        event: Название события
        level: Уровень логирования (info, warning, error, debug)
        **kwargs: Дополнительные поля
    """
    log_method = getattr(logger, level, logger.info)
    log_method(event, **kwargs)


def log_error(
    logger: structlog.BoundLogger,
    error_msg: str,
    exception: Exception = None,
    **kwargs: Any
) -> None:
    """
    Логировать ошибку с traceback.
    
    Args:
        logger: BoundLogger экземпляр
        error_msg: Описание ошибки
        exception: Исключение для traceback
        **kwargs: Дополнительные поля
    """
    if exception:
        kwargs["exception"] = str(exception)
        kwargs["exc_type"] = type(exception).__name__
    
    logger.error(error_msg, **kwargs)


# Инициализировать логирование при импорте
configure_logging()