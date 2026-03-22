"""
Entry point для запуска Core Router.
Запускает FastAPI server через Uvicorn.
"""
import sys
import uvicorn

from config.settings import settings
from config.logging import get_logger

logger = get_logger(__name__)


def run_router():
    """
    Запустить FastAPI Router через Uvicorn.
    
    Конфигурация:
    - Host: 0.0.0.0 (доступен из Docker сети)
    - Port: 8000
    - Workers: 1 (достаточно для слабого железа)
    - Reload: только для разработки
    """
    
    logger.info("🚀 Инициализация Core Router")
    logger.info(
        "📋 Параметры",
        host="0.0.0.0",
        port=8000,
        environment=settings.environment,
        debug=settings.is_development,
    )
    
    try:
        uvicorn.run(
            "core.router.app:app",
            host="0.0.0.0",
            port=8000,
            workers=1,  # Один воркер достаточно для слабого железа
            reload=settings.is_development,  # Reload только в разработке
            log_level=settings.log_level.lower(),
            access_log=True,
            use_colors=not settings.is_production,  # Цветной вывод в dev
        )
    
    except KeyboardInterrupt:
        logger.info("⚠️ Router остановлен (Ctrl+C)")
        sys.exit(0)
    
    except Exception as e:
        logger.error(
            "💥 Критическая ошибка при запуске Router",
            error=str(e),
            exc_type=type(e).__name__,
        )
        sys.exit(1)


if __name__ == "__main__":
    run_router()