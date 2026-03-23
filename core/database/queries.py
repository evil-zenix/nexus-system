"""
Database query функции (CRUD операции).
Работает с SQLAlchemy асинхронно через AsyncSession.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import get_logger
from core.database.models import SystemBot, Group, User, MessageLog, GlobalUser, PasswordSearch

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


# ============================================================================
# GLOBAL USERS (единый кошелёк для всей сети ботов — экономика)
# ============================================================================

async def get_or_create_global_user(
    session: AsyncSession,
    telegram_user_id: int,
    username: Optional[str] = None,
    full_name: Optional[str] = None,
) -> GlobalUser:
    """
    Получить или создать глобальный профиль пользователя.
    
    Используется при каждом сообщении для обновления кэша имени
    и инициализации экономического профиля.
    """
    stmt = select(GlobalUser).where(GlobalUser.telegram_user_id == telegram_user_id)
    result = await session.execute(stmt)
    global_user = result.scalar_one_or_none()
    
    if global_user:
        # Обновить кэшированные данные если изменились
        changed = False
        if username and global_user.username != username:
            global_user.username = username
            changed = True
        if full_name and global_user.full_name != full_name:
            global_user.full_name = full_name
            changed = True
        if changed:
            global_user.updated_at = datetime.utcnow()
            await session.commit()
        return global_user
    
    # Создать новый глобальный профиль
    global_user = GlobalUser(
        telegram_user_id=telegram_user_id,
        username=username,
        full_name=full_name,
        diamonds=0,
        xp=0,
        balance=0.0,
    )
    session.add(global_user)
    await session.commit()
    
    logger.info(
        "🆕 Создан глобальный профиль",
        telegram_user_id=telegram_user_id,
        username=username,
    )
    return global_user


async def add_xp(
    session: AsyncSession,
    telegram_user_id: int,
    amount: int = 10,
) -> GlobalUser:
    """
    Начислить XP пользователю.
    
    Автоматически конвертирует XP в diamonds:
        каждые 100 XP = 1 diamond.
    
    Args:
        session: AsyncSession
        telegram_user_id: Telegram User ID
        amount: Количество XP для начисления (default: 10 за сообщение)
    
    Returns:
        Обновлённый объект GlobalUser
    """
    stmt = select(GlobalUser).where(GlobalUser.telegram_user_id == telegram_user_id)
    result = await session.execute(stmt)
    global_user = result.scalar_one_or_none()
    
    if not global_user:
        logger.warning(
            "⚠️ Пользователь не найден в global_users при начислении XP",
            telegram_user_id=telegram_user_id,
        )
        return None
    
    old_xp = global_user.xp
    global_user.xp += amount
    
    # Конвертация XP → diamonds (каждые 100 XP = 1 diamond)
    old_diamonds_from_xp = old_xp // 100
    new_diamonds_from_xp = global_user.xp // 100
    earned_diamonds = new_diamonds_from_xp - old_diamonds_from_xp
    
    if earned_diamonds > 0:
        global_user.diamonds += earned_diamonds
        logger.info(
            "💎 Начислены diamonds за XP",
            telegram_user_id=telegram_user_id,
            diamonds_earned=earned_diamonds,
            total_diamonds=global_user.diamonds,
            total_xp=global_user.xp,
        )
    
    global_user.updated_at = datetime.utcnow()
    await session.commit()
    return global_user


# ============================================================================
# OSINT LOOKUP (кросс-ботный пробив пользователя)
# ============================================================================

async def osint_lookup_user(
    session: AsyncSession,
    telegram_user_id: int,
) -> Dict[str, Any]:
    """
    OSINT-пробив: собрать всю информацию о пользователе по всем ботам сети.
    
    Возвращает словарь с:
    - global_user: глобальный профиль (XP, diamonds, balance)
    - appearances: список чатов/ботов, где видели пользователя
    - total_messages: суммарное количество сообщений
    - first_seen: первое появление в сети
    - last_seen: последнее появление в сети
    - is_banned_anywhere: забанен ли хоть в одном месте
    """
    report: Dict[str, Any] = {
        "telegram_user_id": telegram_user_id,
        "global_user": None,
        "appearances": [],
        "password_searches": [],
        "total_messages": 0,
        "first_seen": None,
        "last_seen": None,
        "is_banned_anywhere": False,
        "is_spam_anywhere": False,
    }
    
    # 1. Глобальный профиль (экономика)
    stmt = select(GlobalUser).where(GlobalUser.telegram_user_id == telegram_user_id)
    result = await session.execute(stmt)
    global_user = result.scalar_one_or_none()
    
    if global_user:
        report["global_user"] = {
            "username": global_user.username,
            "full_name": global_user.full_name,
            "xp": global_user.xp,
            "diamonds": global_user.diamonds,
            "balance": global_user.balance,
            "registered_at": global_user.created_at,
        }
    
    # 2. Все записи пользователя по всем группам/ботам
    stmt = (
        select(User, Group, SystemBot)
        .join(Group, User.group_id == Group.id)
        .join(SystemBot, Group.bot_id == SystemBot.id)
        .where(User.telegram_user_id == telegram_user_id)
    )
    result = await session.execute(stmt)
    rows = result.all()
    
    total_messages = 0
    first_seen = None
    last_seen = None
    
    for user_row, group_row, bot_row in rows:
        total_messages += user_row.message_count
        
        if user_row.is_banned:
            report["is_banned_anywhere"] = True
        if user_row.is_spam:
            report["is_spam_anywhere"] = True
        
        # Трекинг дат
        if user_row.first_message_date:
            if first_seen is None or user_row.first_message_date < first_seen:
                first_seen = user_row.first_message_date
        if user_row.last_message_date:
            if last_seen is None or user_row.last_message_date > last_seen:
                last_seen = user_row.last_message_date
        
        report["appearances"].append({
            "bot_name": bot_row.bot_name,
            "bot_username": bot_row.bot_username,
            "chat_id": group_row.telegram_chat_id,
            "chat_title": group_row.title,
            "chat_type": group_row.chat_type,
            "message_count": user_row.message_count,
            "status": user_row.status,
            "is_banned": user_row.is_banned,
            "first_seen": user_row.first_message_date,
            "last_seen": user_row.last_message_date,
        })
    
    # 3. Список паролей, которые искал пользователь
    stmt_pass = (
        select(PasswordSearch.password)
        .where(PasswordSearch.telegram_user_id == telegram_user_id)
        .order_by(PasswordSearch.created_at.desc())
        .limit(20)
    )
    res_pass = await session.execute(stmt_pass)
    report["password_searches"] = list(res_pass.scalars().all())
    
    report["total_messages"] = total_messages
    report["first_seen"] = first_seen
    report["last_seen"] = last_seen
    
    logger.info(
        "🔍 OSINT lookup завершён",
        telegram_user_id=telegram_user_id,
        appearances=len(report["appearances"]),
        total_messages=total_messages,
    )
    return report


async def get_global_user_by_username(
    session: AsyncSession,
    username: str,
) -> Optional[GlobalUser]:
    """
    Найти глобальный профиль пользователя по @username.
    
    Args:
        session: AsyncSession
        username: Username без @ (например: "johndoe")
    
    Returns:
        GlobalUser или None если не найден
    """
    # Нормализация: убрать @ если передали с ним
    clean_username = username.lstrip("@").lower()
    
    stmt = select(GlobalUser).where(
        func.lower(GlobalUser.username) == clean_username
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def save_password_search(
    session: AsyncSession,
    telegram_user_id: int,
    password: str,
) -> None:
    """Сохранить пароль, который искал пользователь."""
    # Убедимся что юзер есть в глобальной таблице
    stmt = select(GlobalUser).where(GlobalUser.telegram_user_id == telegram_user_id)
    gu = (await session.execute(stmt)).scalar_one_or_none()
    if not gu:
        return
        
    pw_search = PasswordSearch(
        telegram_user_id=telegram_user_id,
        password=password,
    )
    session.add(pw_search)
    await session.commit()

