"""
FastAPI приложение для Core Router.
Точка входа для всех Telegram Webhook'ов.
"""
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from config.settings import settings
from config.logging import get_logger, configure_logging
from core.database.db import init_db, close_db, health_check as db_health_check
from core.queue.redis_client import init_redis, close_redis, redis_health_check
from core.queue.message_schema import HealthCheckResponse

logger = get_logger(__name__)

# ============================================================================
# LIFESPAN MANAGEMENT (инициализация и очистка)
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Контекстный менеджер для управления жизненным циклом приложения.
    Инициализирует ресурсы при запуске и очищает при остановке.
    """
    # STARTUP
    logger.info("🚀 Запуск Core Router...")
    logger.info(
        "📋 Конфигурация",
        environment=settings.environment,
        log_level=settings.log_level,
        telegram_bots=len(settings.telegram_tokens),
    )
    
    try:
        # Инициализировать БД
        await init_db()
        
        # Инициализировать Redis
        await init_redis()
        
        logger.info("✅ Все сервисы инициализированы")
    
    except Exception as e:
        logger.error("💥 Ошибка при инициализации сервисов", error=str(e))
        raise
    
    yield  # Приложение работает здесь
    
    # SHUTDOWN
    logger.info("🛑 Остановка Core Router...")
    
    try:
        # Закрыть Redis
        await close_redis()
        
        # Закрыть БД
        await close_db()
        
        logger.info("✅ Все сервисы остановлены")
    
    except Exception as e:
        logger.error("💥 Ошибка при остановке сервисов", error=str(e))


# ============================================================================
# FASTAPI APP INITIALIZATION
# ============================================================================

def create_app() -> FastAPI:
    """
    Создать и конфигурировать FastAPI приложение.
    
    Returns:
        FastAPI приложение
    """
    # Конфигурировать логирование
    configure_logging()
    
    # Создать приложение с lifespan
    app = FastAPI(
        title="Nexus Core Router",
        description="Распределенная система Telegram-ботов",
        version="0.1.0",
        lifespan=lifespan,
    )
    
    # ========================================================================
    # EXCEPTION HANDLERS
    # ========================================================================
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Обработчик HTTP исключений"""
        logger.warning(
            "⚠️ HTTP ошибка",
            status_code=exc.status_code,
            detail=exc.detail,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": exc.detail,
                "status_code": exc.status_code,
            },
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Обработчик неожиданных исключений"""
        logger.error(
            "💥 Необработанное исключение",
            error=str(exc),
            exc_type=type(exc).__name__,
            path=request.url.path,
        )
        
        if settings.is_production:
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": "Internal Server Error",
                    "status_code": 500,
                },
            )
        else:
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": str(exc),
                    "exc_type": type(exc).__name__,
                    "status_code": 500,
                },
            )
    
    # ========================================================================
    # HEALTH CHECK ENDPOINTS
    # ========================================================================
    
    @app.get("/health", response_model=HealthCheckResponse)
    async def health_check():
        """
        Проверить здоровье Router и всех зависимых сервисов.
        
        Returns:
            HealthCheckResponse с статусом сервисов
        """
        redis_ok = await redis_health_check()
        db_ok = await db_health_check()
        
        services = {
            "redis": redis_ok,
            "database": db_ok,
            "telegram": True,  # Всегда доступен (не требует подключения)
        }
        
        overall_ok = all(services.values())
        
        return HealthCheckResponse(
            status="ok" if overall_ok else "degraded",
            timestamp=datetime.utcnow().isoformat(),
            services=services,
            version="0.1.0",
        )
    
    @app.get("/ping")
    async def ping():
        """Простой PING для проверки"""
        return {"ok": True, "message": "pong"}
    
    @app.get("/metrics/queue")
    async def get_queue_metrics():
        """Получить метрики очередей"""
        from core.queue.redis_client import get_all_queue_names, get_queue_length
        
        try:
            queues = await get_all_queue_names()
            
            metrics = {}
            for queue_name in queues:
                length = await get_queue_length(queue_name)
                metrics[queue_name] = length
            
            logger.info("📊 Метрики очередей", metrics=metrics)
            
            return {
                "ok": True,
                "timestamp": datetime.utcnow().isoformat(),
                "queues": metrics,
                "total_tasks": sum(metrics.values()),
            }
        except Exception as e:
            logger.error("💥 Ошибка при получении метрик", error=str(e))
            return {
                "ok": False,
                "error": str(e),
            }
    
    # ========================================================================
    # INFO ENDPOINTS
    # ========================================================================
    
    @app.get("/info")
    async def get_info():
        """Получить информацию о системе"""
        return {
            "ok": True,
            "system": "Nexus Core Router",
            "version": "0.1.0",
            "environment": settings.environment,
            "bots_configured": len(settings.telegram_tokens),
            "bot_names": settings.get_all_bot_names(),
            "storage_channel_id": settings.storage_channel_id,
        }
    
    return app


# Создать приложение при импорте
app = create_app()