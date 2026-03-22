"""
SQLAlchemy ORM модели для Nexus системы.
Асинхронные модели таблиц: system_bots, groups, users, messages_log, global_users.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship, Mapped, mapped_column

# Базовый класс для всех моделей
Base = declarative_base()


class GlobalUser(Base):
    """
    Глобальный профиль пользователя — единый кошелёк для всей сети ботов.
    
    Один пользователь (по telegram_user_id) имеет один набор экономических
    атрибутов независимо от того, в скольких ботах/чатах был замечен.
    """
    
    __tablename__ = "global_users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_global_users_uid"),
        Index("ix_global_users_telegram_user_id", "telegram_user_id"),
        Index("ix_global_users_username", "username"),
    )
    
    # Первичный ключ
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Telegram User ID (уникальный в рамках всей сети)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # Кэшированный username (обновляется при каждом сообщении)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Кэшированное полное имя
    full_name: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    
    # ===================== ЭКОНОМИКА =====================
    
    # Алмазы (премиальная валюта)
    diamonds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Опыт (накапливается за каждое сообщение)
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Баланс (основная игровая валюта)
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    
    # =====================================================
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    
    def __repr__(self) -> str:
        return (
            f"<GlobalUser(tg_id={self.telegram_user_id}, "
            f"xp={self.xp}, diamonds={self.diamonds}, balance={self.balance})>"
        )


class SystemBot(Base):
    """
    Модель бота-воркера в системе Nexus.
    
    Хранит информацию о каждом боте, который управляется системой.
    Может быть несколько ботов (moderator_bot, osint_bot и т.д.).
    """
    
    __tablename__ = "system_bots"
    __table_args__ = (
        UniqueConstraint("bot_id", name="uq_system_bots_bot_id"),
        UniqueConstraint("bot_name", name="uq_system_bots_bot_name"),
        Index("ix_system_bots_is_active", "is_active"),
    )
    
    # Первичный ключ
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Telegram Bot ID (уникальный для каждого бота)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # Имя бота в системе (например: "moderator_bot", "osint_bot")
    bot_name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Username бота в Telegram (например: @my_moderator_bot)
    bot_username: Mapped[str] = mapped_column(String(255), nullable=True)
    
    # Токен бота (хранится для переиспользования)
    bot_token: Mapped[str] = mapped_column(String(512), nullable=False)
    
    # Статус (активен/неактивен)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Описание назначения бота
    description: Mapped[str] = mapped_column(Text, nullable=True)
    
    # Последний раз видели бота в онлайне
    last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    
    # Метаинформация (JSON)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    
    # Relationships
    groups = relationship("Group", back_populates="bot", cascade="all, delete-orphan")
    
    def __repr__(self) -> str:
        return f"<SystemBot(id={self.id}, name={self.bot_name}, active={self.is_active})>"


class Group(Base):
    """
    Модель целевого чата (группы/канала).
    
    Каждая группа связана с одним ботом.
    Может содержать множество пользователей и сообщений.
    """
    
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "bot_id", name="uq_groups_chat_bot"),
        Index("ix_groups_bot_id", "bot_id"),
        Index("ix_groups_is_active", "is_active"),
        Index("ix_groups_telegram_chat_id", "telegram_chat_id"),
    )
    
    # Первичный ключ
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # ID чата/группы в Telegram
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # Ссылка на бота
    bot_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("system_bots.id"), nullable=False
    )
    
    # Тип чата (private, group, supergroup, channel)
    chat_type: Mapped[str] = mapped_column(String(50), nullable=False)
    
    # Название чата/группы
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Username чата (если есть)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Описание чата
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Активен ли чат в системе мониторинга
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Количество членов (синхронизируется периодически)
    members_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Статус подписки на события (может быть приостановлена)
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    
    # Relationships
    bot = relationship("SystemBot", back_populates="groups")
    users = relationship("User", back_populates="group", cascade="all, delete-orphan")
    messages = relationship(
        "MessageLog", back_populates="group", cascade="all, delete-orphan"
    )
    
    def __repr__(self) -> str:
        return f"<Group(id={self.id}, title={self.title}, chat_id={self.telegram_chat_id})>"


class User(Base):
    """
    Модель пользователя в группе.
    
    Досье пользователя с основной информацией.
    """
    
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "group_id", name="uq_users_uid_gid"),
        Index("ix_users_telegram_user_id", "telegram_user_id"),
        Index("ix_users_group_id", "group_id"),
        Index("ix_users_username", "username"),
    )
    
    # Первичный ключ
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # ID пользователя в Telegram
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # ID группы
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=False
    )
    
    # Username пользователя (может быть пусто)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Имя пользователя (First Name)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Фамилия пользователя (Last Name)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Полное имя (для удобства)
    full_name: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    
    # Статус в группе (может быть: member, administrator, creator и т.д.)
    status: Mapped[str] = mapped_column(String(50), default="member", nullable=False)
    
    # Количество отправленных сообщений
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Флаг: забанен ли пользователь системой
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Флаг: помечена ли учетка как спамер
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Количество предупреждений (для модерации)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Временная метка первого сообщения
    first_message_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    
    # Временная метка последнего сообщения
    last_message_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    
    # Relationships
    group = relationship("Group", back_populates="users")
    messages = relationship(
        "MessageLog", back_populates="user", cascade="all, delete-orphan"
    )
    
    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, name={self.first_name})>"


class MessageLog(Base):
    """
    Модель логирования сообщений.
    
    Каждое сообщение логируется с текстом и ссылками на медиа в Storage Channel.
    **КЛЮЧЕВОЕ ПОЛЕ**: nexus_file_id — постоянная ссылка на файл в Storage Channel.
    """
    
    __tablename__ = "messages_log"
    __table_args__ = (
        Index("ix_messages_log_user_id", "user_id"),
        Index("ix_messages_log_group_id", "group_id"),
        Index("ix_messages_log_telegram_message_id", "telegram_message_id"),
        Index("ix_messages_log_created_at", "created_at"),
        Index("ix_messages_log_has_media", "has_media"),
    )
    
    # Первичный ключ
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # ID пользователя, отправившего сообщение
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False
    )
    
    # ID группы, в которую отправлено сообщение
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("groups.id"), nullable=False
    )
    
    # ID сообщения в Telegram (для удаления/редактирования)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    
    # Текст сообщения (может быть пусто, если только медиа)
    message_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Содержит ли сообщение медиа-файлы
    has_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # ========================= КЛЮЧЕВОЕ ПОЛЕ =========================
    # Список file_id файлов из Storage Channel (JSON массив)
    # Формат: {"photo": ["file_id_1"], "video": ["file_id_2"], "document": []}
    # Это позволяет НЕ скачивать файлы на диск, а хранить их на Telegram
    nexus_file_id: Mapped[str] = mapped_column(
        Text, default="{}", nullable=False
    )
    # ==================================================================
    
    # Тип медиа (если есть): photo, video, document, audio, etc.
    media_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Результат модерации (spam/clean/flagged)
    moderation_status: Mapped[str] = mapped_column(
        String(50), default="clean", nullable=False
    )
    
    # Причина флага модерации (если применима)
    moderation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Флаг: удалено ли сообщение
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Timestamp удаления
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Отредактировано ли сообщение
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Timestamp редактирования
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Текущее количество реакций на сообщение (для аналитики)
    reaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    
    # Relationships
    user = relationship("User", back_populates="messages")
    group = relationship("Group", back_populates="messages")
    
    def __repr__(self) -> str:
        return (
            f"<MessageLog(id={self.id}, user_id={self.user_id}, "
            f"msg_id={self.telegram_message_id}, media={self.has_media})>"
        )