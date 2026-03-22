"""
Webhook обработчики для Core Router.
Принимают JSON от Telegram, валидируют, кидают в Redis.
"""
import hashlib
import hmac
from typing import Optional, List

from fastapi import HTTPException, Depends, Path, Body
from aiogram import Bot

from config.settings import settings
from config.logging import get_logger
from core.database.db import get_db_session
from core.database import queries
from core.queue.redis_client import enqueue_task
from core.queue.message_schema import (
    WebhookRequest,
    WebhookResponse,
    NexusMessage,
    TelegramMessage,
    TelegramUser,
)
from core.router.app import app

logger = get_logger(__name__)


# ============================================================================
# UTILITIES
# ============================================================================

async def validate_bot_token(bot_name: str) -> Optional[str]:
    """
    Валидировать что бот существует в конфиге.
    
    Args:
        bot_name: Имя бота (из URL пути)
    
    Returns:
        Bot token если найден, None иначе
    """
    token = settings.get_bot_token(bot_name)
    if not token:
        logger.warning("⚠️ Неизвестный бот", bot_name=bot_name)
        return None
    return token


async def validate_webhook_secret(
    x_telegram_bot_api_secret_token: Optional[str] = None
) -> bool:
    """
    Валидировать webhook secret (опционально).
    Если передан WEBHOOK_SECRET в конфиге, проверить его.
    
    Args:
        x_telegram_bot_api_secret_token: Header с токеном от Telegram
    
    Returns:
        True если валиден или не используется, False если невалиден
    """
    if not settings.webhook_secret or settings.webhook_secret == "change_me_in_production":
        # Secret не установлен или default value
        return True
    
    if not x_telegram_bot_api_secret_token:
        logger.warning("⚠️ Secret token не передан в header")
        return False
    
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        logger.warning("⚠️ Invalid webhook secret token")
        return False
    
    return True


async def extract_telegram_update_info(
    update: WebhookRequest,
) -> Optional[TelegramMessage]:
    """
    Извлечь TelegramMessage из различных типов update'ов.
    Может быть: message, edited_message, channel_post, edited_channel_post
    
    Args:
        update: WebhookRequest от Telegram
    
    Returns:
        TelegramMessage или None если не найдено
    """
    # Порядок приоритета
    message = (
        update.message
        or update.edited_message
        or update.channel_post
        or update.edited_channel_post
    )
    
    return message


async def build_nexus_message(
    telegram_message: TelegramMessage,
    bot_name: str,
    bot_token: str,
) -> NexusMessage:
    """
    Построить NexusMessage из Telegram message.
    Извлекает все необходимые данные для воркеров.
    
    Args:
        telegram_message: Message от Telegram API
        bot_name: Имя бота
        bot_token: Bot token
    
    Returns:
        NexusMessage для очереди
    """
    # Получить Bot ID из токена
    bot_id = int(bot_token.split(":")[0])
    
    # Извлечь информацию о пользователе
    user = telegram_message.from_user or TelegramUser(id=0, is_bot=False, first_name="Unknown")
    
    # Извлечь информацию о чате
    chat = telegram_message.chat
    
    # Определить тип медиа и file_id'ы
    media_type = None
    photo_file_ids = None
    video_file_id = None
    document_file_id = None
    document_name = None
    has_media = False
    
    if telegram_message.photo:
        has_media = True
        media_type = "photo"
        # Фото приходят отсортированными от большего к меньшему
        photo_file_ids = [p.file_id for p in telegram_message.photo]
    
    elif telegram_message.video:
        has_media = True
        media_type = "video"
        video_file_id = telegram_message.video.file_id
    
    elif telegram_message.document:
        has_media = True
        media_type = "document"
        document_file_id = telegram_message.document.file_id
        document_name = telegram_message.document.file_name
    
    # Построить сообщение для очереди
    nexus_message = NexusMessage(
        # Bot информация
        bot_name=bot_name,
        bot_id=bot_id,
        bot_token=bot_token,
        
        # Message информация
        message_id=telegram_message.message_id,
        chat_id=chat.id,
        chat_type=chat.type,
        chat_title=chat.title,
        chat_username=chat.username,
        
        # User информация
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        
        # Содержание
        text=telegram_message.text,
        caption=telegram_message.caption,
        
        # Медиа
        has_media=has_media,
        media_type=media_type,
        photo_file_ids=photo_file_ids,
        video_file_id=video_file_id,
        document_file_id=document_file_id,
        document_name=document_name,
        
        # Timestamps
        message_date=telegram_message.date,
        is_edited=telegram_message.edit_date is not None,
        
        # Raw update для расширенной обработки
        raw_update=telegram_message.dict(),
    )
    
    return nexus_message


# ============================================================================
# WEBHOOK ENDPOINTS
# ============================================================================

@app.post("/webhook/{bot_name}", response_model=WebhookResponse)
async def webhook_handler(
    bot_name: str = Path(..., description="Имя бота (moderator_bot, osint_bot и т.д.)"),
    update: WebhookRequest = Body(..., description="Update от Telegram"),
    x_telegram_bot_api_secret_token: Optional[str] = None,
):
    """
    Основной webhook обработчик для получения обновлений от Telegram.
    
    URL: POST /webhook/{bot_name}
    
    Этот эндпоинт:
    1. Валидирует что бот существует в конфиге
    2. Проверяет webhook secret (опционально)
    3. Парсит telegram update
    4. Строит NexusMessage
    5. Кидает в Redis очередь для воркеров
    6. Мгновенно возвращает 200 OK Telegram
    
    Args:
        bot_name: Имя бота из URL
        update: JSON payload от Telegram
        x_telegram_bot_api_secret_token: Optional header с secret
    
    Returns:
        WebhookResponse (ok=True)
    
    Raises:
        HTTPException(404) если бот не найден
        HTTPException(403) если secret невалиден
    """
    
    logger.info(
        "📨 Получен Webhook",
        bot_name=bot_name,
        update_id=update.update_id,
    )
    
    # ========================================================================
    # STEP 1: Валидировать бот
    # ========================================================================
    
    bot_token = await validate_bot_token(bot_name)
    if not bot_token:
        logger.error(
            "❌ Бот не найден",
            bot_name=bot_name,
            available_bots=settings.get_all_bot_names(),
        )
        raise HTTPException(
            status_code=404,
            detail=f"Bot '{bot_name}' not found. Available: {settings.get_all_bot_names()}",
        )
    
    # ========================================================================
    # STEP 2: Валидировать webhook secret
    # ========================================================================
    
    if not await validate_webhook_secret(x_telegram_bot_api_secret_token):
        logger.error(
            "❌ Невалидный webhook secret",
            bot_name=bot_name,
            update_id=update.update_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Invalid webhook secret token",
        )
    
    # ========================================================================
    # STEP 3: Парсить update (может быть несколько типов сообщений)
    # ========================================================================
    
    telegram_message = await extract_telegram_update_info(update)
    
    if not telegram_message:
        logger.debug(
            "ℹ️ Update без сообщения (может быть callback, reaction и т.д.)",
            bot_name=bot_name,
            update_id=update.update_id,
        )
        # Все равно вернуть 200 OK для Telegram
        return WebhookResponse(
            ok=True,
            message="Update received but no message to process",
        )
    
    # ========================================================================
    # STEP 4: Синхронизировать бота и чат в БД
    # ========================================================================
    
    session = await get_db_session()
    
    try:
        # Создать или получить бота в БД
        bot_record = await queries.get_or_create_bot(
            session=session,
            bot_id=int(bot_token.split(":")[0]),
            bot_name=bot_name,
            bot_token=bot_token,
        )
        
        # Создать или получить чат в БД
        group_record = await queries.get_or_create_group(
            session=session,
            telegram_chat_id=telegram_message.chat.id,
            bot_id=bot_record.id,
            chat_type=telegram_message.chat.type,
            title=telegram_message.chat.title,
            username=telegram_message.chat.username,
        )
        
        logger.info(
            "✅ БД синхронизирована",
            bot_id=bot_record.id,
            group_id=group_record.id,
        )
    
    except Exception as e:
        logger.error(
            "💥 Ошибка при синхронизации БД",
            error=str(e),
            bot_name=bot_name,
        )
        # Все равно продолжить обработку (кинуть в очередь)
    
    finally:
        await session.close()
    
    # ========================================================================
    # STEP 5: Построить NexusMessage для очереди
    # ========================================================================
    
    try:
        nexus_message = await build_nexus_message(
            telegram_message=telegram_message,
            bot_name=bot_name,
            bot_token=bot_token,
        )
        
        logger.debug(
            "🔨 NexusMessage построено",
            bot_name=bot_name,
            user_id=nexus_message.user_id,
            has_media=nexus_message.has_media,
        )
    
    except Exception as e:
        logger.error(
            "💥 Ошибка при построении NexusMessage",
            error=str(e),
            bot_name=bot_name,
        )
        # Все равно вернуть 200 OK (не блокировать Telegram)
        return WebhookResponse(
            ok=True,
            message="Update received but failed to process",
        )
    
    # ========================================================================
    # STEP 6: Определить целевые очереди и кинуть задачи
    # ========================================================================
    
    # В зависимости от bot_name кинуть в нужную очередь
    queue_mapping = {
        "moderator": "tasks:moderator",
        "osint": "tasks:osint",
    }
    
    queued_count = 0
    
    for bot_key, queue_name in queue_mapping.items():
        if bot_key in bot_name.lower():
            success = await enqueue_task(
                queue_name=queue_name,
                task_data=nexus_message.dict(),
                ttl=settings.task_ttl,
            )
            
            if success:
                queued_count += 1
                logger.info(
                    "✅ Задача добавлена в очередь",
                    queue=queue_name,
                    bot_name=bot_name,
                    message_id=telegram_message.message_id,
                )
            else:
                logger.error(
                    "❌ Ошибка при добавлении в очередь",
                    queue=queue_name,
                    bot_name=bot_name,
                )
    
    if queued_count == 0:
        # Если не поддерживается этот тип бота, просто залогировать
        logger.warning(
            "⚠️ Бот не поддерживает автоматическую маршрутизацию в очередь",
            bot_name=bot_name,
        )
    
    # ========================================================================
    # STEP 7: Вернуть 200 OK для Telegram (ВАЖНО!)
    # ========================================================================
    
    return WebhookResponse(
        ok=True,
        message=f"Update received and queued ({queued_count} workers)",
    )


# ============================================================================
# BULK WEBHOOK (для тестирования)
# ============================================================================

@app.post("/webhook/test/{bot_name}", response_model=WebhookResponse)
async def test_webhook(
    bot_name: str = Path(..., description="Имя бота"),
    update: WebhookRequest = Body(...),
):
    """
    Тестовый эндпоинт webhook'а (без проверки secret).
    Используется для локальной разработки и тестирования.
    
    В production эту строку следует удалить.
    """
    if settings.is_production:
        raise HTTPException(
            status_code=403,
            detail="Test webhook not available in production",
        )
    
    logger.warning(
        "⚠️ Используется TEST webhook (только для разработки)",
        bot_name=bot_name,
    )
    
    return await webhook_handler(
        bot_name=bot_name,
        update=update,
        x_telegram_bot_api_secret_token=None,
    )