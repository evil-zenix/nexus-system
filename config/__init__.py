"""Конфигурация Nexus системы"""

from config.settings import settings
from config.logging import get_logger, configure_logging

__all__ = ["settings", "get_logger", "configure_logging"]