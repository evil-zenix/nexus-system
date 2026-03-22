"""
Database query функции (CRUD операции).
Работает с SQLAlchemy асинхронно через AsyncSession.
"""
from typing import Optional, List

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import get_logger
from core.database.models import SystemBot, Group, User, MessageLog

logger = get_logger(__name__)


# ============================================================================
# SYSTEM BOTS (система ботов-воркеров)
# ============================================================================

async def get_or_create_bot(
    session: AsyncSession,
    bot_id: int,
    bot_name: str,
    bot_token: str,
    bot_username: Optional[str] = None,
) -> SystemBot:
    """
    Получить или создать бота в системе.
    
    Args:
        session: AsyncSession для БД
        bot_id: Telegram Bot ID
        bot_name: Имя бота в системе (moderator_bot, osint_bot)
        bot_token: Telegram токен
        bot_username: Username бота в Telegram
    
    Returns:
        Объект SystemBot
    """
    # Попытаться получить существующего бота
    stmt = select(SystemBot).where(SystemBot.bot_id == bot_id)
    result = await session.execute(stmt)
    bot = result.scalar_one_or_none()
    
    if bot:
        logger.info("✅ Бот найден в БД", bot_id=bot_id, bot_name=bot_name)
        return bot
    
    # Создать нового бота
    bot = SystemBot(
        bot_id=bot_id,
        bot_name=bot_name,
        bot_token=bot_token,
        bot_username=bot_username,
        is_active=True,
    )
    
    session.add(bot)
    await session.commit()
    
    logger.info("🆕 Новый бот создан", bot_id=bot_id, bot_name=bot_name)
    return bot


async def get_bot_by_name(session: AsyncSession, bot_name: str) -> Optional[SystemBot]:
    """Получить бота по имени"""
    stmt = select(SystemBot).where(
        (SystemBot.bot_name == bot_name) & (SystemBot.is_active == True)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_all_active_bots(session: AsyncSession) -> List[SystemBot]:
    """Получить список всех активных ботов"""
    stmt = select(SystemBot).where(SystemBot.is_active == True)
    result = await session.execute(stmt)
    return result.scalars().all()


# ============================================================================
# GROUPS (целевые чаты/группы)
# ============================================================================

async def get_or_create_group(
    session: AsyncSession,
    telegram_chat_id: int,
    bot_id: int,
    chat_type: str,
    title: Optional[str] = None,
    username: Optional[str] = None,
) -> Group:
    """
    Получить или создать группу.
    
    Args:
        session: AsyncSession для БД
        telegram_chat_id: Telegram Chat ID
        bot_id: ID бота в БД
        chat_type: Тип чата (private, group, supergroup, channel)
        title: Название чата
        username: Username чата
    
    Returns:
        Объект Group
    """
    # Попытаться получить существующую группу
    stmt = select(Group).where(
        (Group.telegram_chat_id == telegram_chat_id) & (Group.bot_id == bot_id)
    )
    result = await session.execute(stmt)
    group = result.scalar_one_or_none()
    
    if group:
        # Обновить информацию если нужно
        if title and group.title != title:
            group.title = title
        if username and group.username != username:
            group.username = username
        await session.commit()
        logger.info("✅ Группа найдена", chat_id=telegram_chat_id)
        return group
    
    # Создать новую группу
    group = Group(
        telegram_chat_id=telegram_chat_id,
        bot_id=bot_id,
        chat_type=chat_type,
        title=title,
        username=username,
        is_active=True,
        is_subscribed=True,
    )
    
    session.add(group)
    await session.commit()
    
    logger.info("🆕 Новая группа создана", chat_id=telegram_chat_id, title=title)
    return group


async def get_group_by_chat_id(
    session: AsyncSession,
    telegram_chat_id: int,
) -> Optional[Group]:
    """Получить группу по Telegram Chat ID"""
    stmt = select(Group).where(Group.telegram_chat_id == telegram_chat_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ============================================================================
# USERS (пользователи в группах)
# ============================================================================

async def get_or_create_user(
    session: AsyncSession,
    telegram_user_id: int,
    group_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> User:
    """
    Получить или создать пользователя в группе.
    
    Args:
        session: AsyncSession для БД
        telegram_user_id: Telegram User ID
        group_id: ID группы в БД
        username: Username пользователя
        first_name: Имя пользователя
        last_name: Фамилия пользователя
    
    Returns:
        Объект User
    """
    # Попытаться получить существующего пользователя
    stmt = select(User).where(
        (User.telegram_user_id == telegram_user_id) & (User.group_id == group_id)
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    if user:
        # Обновить информацию если изменилась
        if username and user.username != username:
            user.username = username
        if first_name and user.first_name != first_name:
            user.first_name = first_name
        if last_name and user.last_name != last_name:
            user.last_name = last_name
        if first_name or last_name:
            user.full_name = f"{first_name or ''} {last_name or ''}".strip()
        await session.commit()
        return user
    
    # Создать нового пользователя
    full_name = f"{first_name or ''} {last_name or ''}".strip() or None
    
    user = User(
        telegram_user_id=telegram_user_id,
        group_id=group_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        status="member",
        message_count=0,
    )
    
    session.add(user)
    await session.commit()
    
    logger.info(
        "🆕 Новый пользователь создан",
        user_id=telegram_user_id,
        username=username,
        group_id=group_id,
    )
    return user


async def get_user(
    session: AsyncSession,
    telegram_user_id: int,
    group_id: int,
) -> Optional[User]:
    """Получить пользователя по ID в конкретной группе"""
    stmt = select(User).where(
        (User.telegram_user_id == telegram_user_id) & (User.group_id == group_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ============================================================================
# MESSAGES_LOG (логирование сообщений)
# ============================================================================

async def create_message_log(
    session: AsyncSession,
    user_id: int,
    group_id: int,
    telegram_message_id: int,
    message_text: Optional[str] = None,
    has_media: bool = False,
    media_type: Optional[str] = None,
    nexus_file_id: str = "{}",
    moderation_status: str = "clean",
) -> MessageLog:
    """
    Создать лог сообщения.
    
    Args:
        session: AsyncSession для БД
        user_id: ID пользователя в БД
        group_id: ID группы в БД
        telegram_message_id: Telegram Message ID
        message_text: Текст сообщения
        has_media: Есть ли медиа
        media_type: Тип медиа (photo, video, document)
        nexus_file_id: JSON с file_id из Storage Channel
        moderation_status: Результат модерации (clean, spam, flagged)
    
    Returns:
        Объект MessageLog
    """
    # Также обновить последнее сообщение пользователя
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalar_one()
    
    from datetime import datetime
    
    user.last_message_date = datetime.utcnow()
    if user.first_message_date is None:
        user.first_message_date = datetime.utcnow()
    user.message_count += 1
    
    # Создать лог сообщения
    message_log = MessageLog(
        user_id=user_id,
        group_id=group_id,
        telegram_message_id=telegram_message_id,
        message_text=message_text,
        has_media=has_media,
        media_type=media_type,
        nexus_file_id=nexus_file_id,
        moderation_status=moderation_status,
    )
    
    session.add(message_log)
    await session.commit()
    
    logger.info(
        "📝 Сообщение залогировано",
        msg_id=telegram_message_id,
        user_id=user_id,
        has_media=has_media,
    )
    return message_log


async def get_message_log(
    session: AsyncSession,
    telegram_message_id: int,
    group_id: int,
) -> Optional[MessageLog]:
    """Получить лог сообщения"""
    stmt = select(MessageLog).where(
        (MessageLog.telegram_message_id == telegram_message_id)
        & (MessageLog.group_id == group_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_message_moderation(
    session: AsyncSession,
    message_log_id: int,
    moderation_status: str,
    moderation_reason: Optional[str] = None,
) -> MessageLog:
    """Обновить статус модерации сообщения"""
    stmt = (
        update(MessageLog)
        .where(MessageLog.id == message_log_id)
        .values(
            moderation_status=moderation_status,
            moderation_reason=moderation_reason,
        )
        .returning(MessageLog)
    )
    result = await session.execute(stmt)
    await session.commit()
    return result.scalar_one()


async def get_recent_messages_by_user(
    session: AsyncSession,
    user_id: int,
    limit: int = 10,
) -> List[MessageLog]:
    """Получить последние сообщения от пользователя"""
    stmt = (
        select(MessageLog)
        .where(MessageLog.user_id == user_id)
        .order_by(MessageLog.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_spam_messages(
    session: AsyncSession,
    group_id: int,
    limit: int = 100,
) -> List[MessageLog]:
    """Получить сообщения, отмеченные как спам"""
    stmt = (
        select(MessageLog)
        .where(
            (MessageLog.group_id == group_id)
            & (MessageLog.moderation_status == "spam")
        )
        .order_by(MessageLog.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()