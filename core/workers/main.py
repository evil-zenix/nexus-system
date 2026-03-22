"""
Entry point для запуска Workers.
Управляет инициализацией и запуском нужного воркера.
"""
import asyncio
import sys
import argparse
from typing import Optional

from config.settings import settings
from config.logging import get_logger, configure_logging
from core.database.db import init_db, close_db
from core.queue.redis_client import init_redis, close_redis
from core.workers.base_worker import BaseWorker
from core.workers.moderator import ModeratorWorker
from core.workers.osint_scraper import OSINTWorker

logger = get_logger(__name__)


# ============================================================================
# WORKER REGISTRY
# ============================================================================

WORKERS = {
    "moderator": ModeratorWorker,
    "osint": OSINTWorker,
}


# ============================================================================
# WORKER MANAGER
# ============================================================================

class WorkerManager:
    """
    Управляет инициализацией и запуском воркеров.
    """
    
    def __init__(self, worker_type: str, concurrency: Optional[int] = None):
        """
        Инициализировать WorkerManager.
        
        Args:
            worker_type: Тип воркера (moderator, osint)
            concurrency: Количество одновременных задач (опционально)
        """
        self.worker_type = worker_type
        self.concurrency = concurrency or settings.worker_concurrency
        self.worker: Optional[BaseWorker] = None
    
    async def initialize(self) -> None:
        """Инициализировать все зависимости"""
        logger.info(
            "🔧 Инициализация Worker Manager",
            worker_type=self.worker_type,
            concurrency=self.concurrency,
        )
        
        try:
            # Инициализировать БД
            await init_db()
            logger.info("✅ БД инициализирована")
            
            # Инициализировать Redis
            await init_redis()
            logger.info("✅ Redis инициализирован")
            
            # Создать воркер
            self._create_worker()
            logger.info("✅ Воркер создан")
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при инициализации",
                error=str(e),
                exc_type=type(e).__name__,
            )
            await self.cleanup()
            raise
    
    def _create_worker(self) -> None:
        """Создать воркер нужного типа"""
        worker_class = WORKERS.get(self.worker_type)
        
        if not worker_class:
            raise ValueError(
                f"Unknown worker type: {self.worker_type}. "
                f"Available: {list(WORKERS.keys())}"
            )
        
        self.worker = worker_class(concurrency=self.concurrency)
        logger.info(
            f"🆕 Создан воркер",
            worker_type=self.worker_type,
            worker_class=worker_class.__name__,
        )
    
    async def run(self) -> None:
        """Запустить воркер"""
        if not self.worker:
            raise RuntimeError("Worker not initialized. Call initialize() first.")
        
        logger.info(
            f"🚀 Запуск {self.worker.worker_name}",
            queue=self.worker.queue_name,
            concurrency=self.worker.concurrency,
        )
        
        try:
            await self.worker.run()
        except KeyboardInterrupt:
            logger.info("⚠️ Воркер остановлен (Ctrl+C)")
        except Exception as e:
            logger.error(
                f"💥 Критическая ошибка в воркере",
                error=str(e),
                exc_type=type(e).__name__,
            )
            raise
        finally:
            await self.cleanup()
    
    async def cleanup(self) -> None:
        """Очистить все ресурсы"""
        logger.info("🧹 Очистка ресурсов")
        
        try:
            # Остановить воркер
            if self.worker:
                await self.worker.stop()
            
            # Закрыть Redis
            await close_redis()
            
            # Закрыть БД
            await close_db()
            
            logger.info("✅ Ресурсы очищены")
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при очистке ресурсов",
                error=str(e),
            )


# ============================================================================
# MAIN
# ============================================================================

def parse_arguments() -> argparse.Namespace:
    """Парсить аргументы командной строки"""
    parser = argparse.ArgumentParser(
        description="Nexus Worker - обработчик задач из Redis очереди",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python -m core.workers.main --worker moderator
  python -m core.workers.main --worker osint --concurrency 5
  python -m core.workers.main --help

Доступные воркеры:
  moderator - проверка спама и модерация сообщений
  osint     - логирование сообщений и медиа в Storage Channel
        """,
    )
    
    parser.add_argument(
        "--worker",
        type=str,
        required=True,
        choices=list(WORKERS.keys()),
        help="Тип воркера для запуска",
    )
    
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Количество одновременных задач (по умолчанию из .env)",
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Уровень логирования",
    )
    
    return parser.parse_args()


async def main():
    """Главная функция"""
    args = parse_arguments()
    
    # Конфигурировать логирование
    configure_logging()
    
    logger.info(
        "═════════════════════════════════════════════════════════════════"
    )
    logger.info(
        "🚀 NEXUS WORKER STARTED"
    )
    logger.info(
        "═════════════════════════════════════════════════════════════════"
    )
    logger.info(
        "📋 Параметры запуска",
        worker=args.worker,
        concurrency=args.concurrency or settings.worker_concurrency,
        log_level=args.log_level,
        environment=settings.environment,
    )
    
    # Создать и запустить worker manager
    manager = WorkerManager(
        worker_type=args.worker,
        concurrency=args.concurrency,
    )
    
    try:
        await manager.initialize()
        await manager.run()
    
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал Ctrl+C")
        sys.exit(0)
    
    except Exception as e:
        logger.error(
            "💥 Критическая ошибка",
            error=str(e),
            exc_type=type(e).__name__,
        )
        sys.exit(1)
    
    finally:
        logger.info(
            "═════════════════════════════════════════════════════════════════"
        )
        logger.info(
            "🛑 NEXUS WORKER STOPPED"
        )
        logger.info(
            "═════════════════════════════════════════════════════════════════"
        )


if __name__ == "__main__":
    asyncio.run(main())