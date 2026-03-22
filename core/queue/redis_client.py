"""
Redis клиент для управления очередями сообщений (Message Broker).
Используется Router для добавления задач и Workers для получения задач.
"""
import json
from typing import Optional, Any, List
from datetime import datetime

import redis.asyncio as redis
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from config.settings import settings
from config.logging import get_logger

logger = get_logger(__name__)

# Глобальная переменная для Redis клиента
_redis_client: Optional[AsyncRedis] = None


async def init_redis() -> None:
    """
    Инициализировать подключение к Redis.
    Должна быть вызвана при запуске приложения.
    """
    global _redis_client
    
    logger.info("🔧 Инициализация Redis", url=settings.redis_url)
    
    try:
        # Создать асинхронный Redis клиент
        _redis_client = await redis.from_url(
            settings.redis_url,
            encoding="utf8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        
        # Проверить подключение
        await _redis_client.ping()
        logger.info("✅ Redis инициализирован успешно")
    
    except Exception as e:
        logger.error("💥 Ошибка подключения к Redis", error=str(e))
        raise


async def close_redis() -> None:
    """
    Закрыть подключение к Redis.
    Должна быть вызвана при остановке приложения.
    """
    global _redis_client
    
    if _redis_client:
        logger.info("🛑 Закрытие подключения к Redis")
        await _redis_client.close()
        logger.info("✅ Redis закрыт")


async def get_redis_client() -> AsyncRedis:
    """
    Получить Redis клиент (синглтон).
    
    Returns:
        AsyncRedis клиент
    
    Raises:
        RuntimeError если Redis не инициализирован
    """
    if _redis_client is None:
        raise RuntimeError(
            "Redis не инициализирован. Вызовите init_redis() при запуске."
        )
    return _redis_client


async def redis_health_check() -> bool:
    """
    Проверить здоровье Redis.
    
    Returns:
        True если Redis доступен, False иначе
    """
    try:
        client = await get_redis_client()
        await client.ping()
        return True
    except Exception as e:
        logger.error("💥 Redis недоступен", error=str(e))
        return False


# ============================================================================
# QUEUE OPERATIONS (операции с очередью сообщений)
# ============================================================================

async def enqueue_task(
    queue_name: str,
    task_data: dict,
    ttl: Optional[int] = None,
) -> bool:
    """
    Добавить задачу в очередь (RPUSH).
    Используется Router для добавления webhook'ов в очередь воркеров.
    
    Args:
        queue_name: Имя очереди (например: tasks:moderator, tasks:osint)
        task_data: Словарь с данными задачи (будет сериализован в JSON)
        ttl: Time To Live (сек). Если None, используется settings.task_ttl
    
    Returns:
        True если успешно добавлена, False иначе
    
    Пример:
        await enqueue_task(
            queue_name="tasks:moderator",
            task_data={"message": {...}, "chat_id": 123},
            ttl=3600
        )
    """
    if ttl is None:
        ttl = settings.task_ttl
    
    try:
        client = await get_redis_client()
        
        # Сериализовать в JSON и добавить метаинформацию
        task_with_meta = {
            "data": task_data,
            "enqueued_at": datetime.utcnow().isoformat(),
            "ttl": ttl,
        }
        
        task_json = json.dumps(task_with_meta, ensure_ascii=False)
        
        # Добавить в очередь
        queue_length = await client.rpush(queue_name, task_json)
        
        logger.info(
            "📤 Задача добавлена в очередь",
            queue=queue_name,
            queue_length=queue_length,
        )
        
        return True
    
    except Exception as e:
        logger.error(
            "💥 Ошибка при добавлении в очередь",
            queue=queue_name,
            error=str(e),
        )
        return False


async def dequeue_task(queue_name: str) -> Optional[dict]:
    """
    Получить задачу из очереди (LPOP).
    Используется Workers для получения задач.
    
    Args:
        queue_name: Имя очереди
    
    Returns:
        Словарь с данными задачи или None если очередь пуста
    
    Пример:
        task = await dequeue_task("tasks:moderator")
        if task:
            await process_task(task)
    """
    try:
        client = await get_redis_client()
        
        task_json = await client.lpop(queue_name)
        
        if not task_json:
            return None
        
        # Десериализовать JSON
        task = json.loads(task_json)
        
        logger.debug(
            "📥 Задача получена из очереди",
            queue=queue_name,
        )
        
        return task
    
    except json.JSONDecodeError:
        logger.error(
            "💥 Ошибка десериализации JSON из очереди",
            queue=queue_name,
        )
        return None
    except Exception as e:
        logger.error(
            "💥 Ошибка при получении из очереди",
            queue=queue_name,
            error=str(e),
        )
        return None


async def get_queue_length(queue_name: str) -> int:
    """
    Получить длину очереди.
    
    Args:
        queue_name: Имя очереди
    
    Returns:
        Количество задач в очереди
    """
    try:
        client = await get_redis_client()
        length = await client.llen(queue_name)
        return length
    except Exception as e:
        logger.error(
            "💥 Ошибка при получении длины очереди",
            queue=queue_name,
            error=str(e),
        )
        return 0


async def clear_queue(queue_name: str) -> bool:
    """
    Очистить очередь полностью (DELETE).
    ⚠️ Используется осторожно! Удалит все задачи в очереди.
    
    Args:
        queue_name: Имя очереди
    
    Returns:
        True если успешно очищена, False иначе
    """
    try:
        client = await get_redis_client()
        deleted = await client.delete(queue_name)
        logger.warning(
            "🗑️ Очередь очищена",
            queue=queue_name,
            deleted=deleted,
        )
        return True
    except Exception as e:
        logger.error(
            "💥 Ошибка при очистке очереди",
            queue=queue_name,
            error=str(e),
        )
        return False


async def get_all_queue_names(pattern: str = "tasks:*") -> List[str]:
    """
    Получить список всех очередей по паттерну.
    
    Args:
        pattern: Паттерн для поиска (по умолчанию все tasks:*)
    
    Returns:
        Список имен очередей
    """
    try:
        client = await get_redis_client()
        keys = await client.keys(pattern)
        return keys
    except Exception as e:
        logger.error(
            "💥 Ошибка при поиске очередей",
            pattern=pattern,
            error=str(e),
        )
        return []


# ============================================================================
# CACHE OPERATIONS (простой кэш в Redis)
# ============================================================================

async def cache_set(
    key: str,
    value: Any,
    ttl: int = 3600,
) -> bool:
    """
    Установить значение в кэш с TTL.
    
    Args:
        key: Ключ кэша
        value: Значение (будет сериализовано в JSON если нужно)
        ttl: Time To Live (сек)
    
    Returns:
        True если успешно установлено, False иначе
    """
    try:
        client = await get_redis_client()
        
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        
        await client.setex(key, ttl, value)
        return True
    except Exception as e:
        logger.error(
            "💥 Ошибка при установке кэша",
            key=key,
            error=str(e),
        )
        return False


async def cache_get(key: str) -> Optional[Any]:
    """
    Получить значение из кэша.
    
    Args:
        key: Ключ кэша
    
    Returns:
        Значение или None если ключ не найден
    """
    try:
        client = await get_redis_client()
        value = await client.get(key)
        
        if value is None:
            return None
        
        # Попытаться десериализовать из JSON
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    
    except Exception as e:
        logger.error(
            "💥 Ошибка при получении кэша",
            key=key,
            error=str(e),
        )
        return None


async def cache_delete(key: str) -> bool:
    """Удалить значение из кэша"""
    try:
        client = await get_redis_client()
        await client.delete(key)
        return True
    except Exception as e:
        logger.error(
            "💥 Ошибка при удалении кэша",
            key=key,
            error=str(e),
        )
        return False


# ============================================================================
# MONITORING & DEBUG
# ============================================================================

async def get_redis_info() -> dict:
    """
    Получить информацию о Redis для мониторинга.
    
    Returns:
        Словарь с информацией о Redis
    """
    try:
        client = await get_redis_client()
        info = await client.info()
        return {
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_human": info.get("used_memory_human", "unknown"),
            "used_memory_peak_human": info.get("used_memory_peak_human", "unknown"),
            "total_commands_processed": info.get("total_commands_processed", 0),
            "db0": info.get("db0", "{}"),
        }
    except Exception as e:
        logger.error("💥 Ошибка при получении информации Redis", error=str(e))
        return {}