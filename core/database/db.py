"""
Асинхронный слой для работы с PostgreSQL.
Управление сессиями, connection pooling, инициализация БД.
Обновлено для совместимости с SQLAlchemy 2.0.
"""
from typing import AsyncGenerator

from sqlalchemy import text  # <--- Добавлено для фикса ошибки "SELECT 1"
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)

from config.settings import settings
from config.logging import get_logger
from core.database.models import Base

logger = get_logger(__name__)

# Глобальные переменные для работы с БД
_engine: AsyncEngine = None
_session_factory: async_sessionmaker = None


async def init_db() -> None:
    """
    Инициализировать подключение к БД и создать таблицы.
    Должна быть вызвана при запуске приложения.
    """
    global _engine, _session_factory
    
    logger.info("🔧 Инициализация БД", url=settings.database_url)
    
    # Создать асинхронный engine
    _engine = create_async_engine(
        settings.database_url,
        echo=settings.is_development,  # Выводить SQL в логи (только для dev)
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # Проверять соединение перед использованием
        pool_recycle=3600,  # Перезагружать соединения каждый час
    )
    
    # Создать session factory
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    
    # Создать все таблицы (если их еще нет)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("✅ БД инициализирована успешно")


async def close_db() -> None:
    """
    Закрыть подключение к БД.
    Должна быть вызвана при остановке приложения.
    """
    global _engine
    
    if _engine:
        logger.info("🛑 Закрытие подключения к БД")
        await _engine.dispose()
        logger.info("✅ Подключение закрыто")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Получить асинхронную сессию БД для FastAPI (Depends).
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database не инициализирована. Вызовите init_db() при запуске."
        )
    
    async with _session_factory() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            logger.error("💥 Ошибка в БД сессии", error=str(e))
            raise
        finally:
            await session.close()


async def get_db_session() -> AsyncSession:
    """
    Получить сессию БД напрямую (например, для воркеров).
    ВАЖНО: Нужно вызвать close() вручную!
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database не инициализирована. Вызовите init_db() при запуске."
        )
    
    return _session_factory()


async def health_check() -> bool:
    """
    Проверить доступность БД.
    Фикс SQLAlchemy 2.0: строковые запросы должны быть обернуты в text().
    """
    if _session_factory is None:
        return False
        
    try:
        async with _session_factory() as session:
            # Исправлено: добавлена обертка text()
            await session.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.error("💥 БД недоступна", error=str(e))
        return False