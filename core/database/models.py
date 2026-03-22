"""
SQLAlchemy ORM модели для Nexus системы.
Исправлено: использование JSONB для корректной работы с PostgreSQL.
"""
from datetime import datetime
from typing import Optional, Dict, Any

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
from sqlalchemy.dialects.postgresql import JSONB # Добавлено для работы с JSON в Postgres

# Базовый класс для всех моделей
Base = declarative_base()


class GlobalUser(Base):
    """
    Глобальный профиль пользователя — единый кошелёк для всей сети ботов.
    """
    
    __tablename__ = "global_users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_global_users_uid"),
        Index("ix_global_users_telegram_user_id", "telegram_user_id"),
        Index("ix_global_users_username", "username"),
    )
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    
    # Экономика
    diamonds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    def __repr__(self) -> str:
        return f"<GlobalUser(tg_id={self.telegram_user_id}, xp={self.xp}, diamonds={self.diamonds})>"


class SystemBot(Base):
    """
    Модель бота-воркера. Исправлено поле metadata_json.
    """
    
    __tablename__ = "system_bots"
    __table_args__ = (
        UniqueConstraint("bot_id", name="uq_system_bots_bot_id"),
        UniqueConstraint("bot_name", name="uq_system_bots_bot_name"),
        Index("ix_system_bots_is_active", "is_active"),
    )
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bot_username: Mapped[str] = mapped_column(String(255), nullable=True)
    bot_token: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # ИСПРАВЛЕНО: Используем JSONB и дефолтный словарь Python
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, server_default='{}', nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    groups = relationship("Group", back_populates="bot", cascade="all, delete-orphan")


class Group(Base):
    """
    Модель целевого чата.
    """
    
    __tablename__ = "groups"
    __table_args__ = (
        UniqueConstraint("telegram_chat_id", "bot_id", name="uq_groups_chat_bot"),
        Index("ix_groups_bot_id", "bot_id"),
        Index("ix_groups_telegram_chat_id", "telegram_chat_id"),
    )
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_id: Mapped[int] = mapped_column(Integer, ForeignKey("system_bots.id"), nullable=False)
    chat_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    members_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    bot = relationship("SystemBot", back_populates="groups")
    users = relationship("User", back_populates="group", cascade="all, delete-orphan")
    messages = relationship("MessageLog", back_populates="group", cascade="all, delete-orphan")


class User(Base):
    """
    Модель пользователя внутри конкретной группы.
    """
    
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", "group_id", name="uq_users_uid_gid"),
        Index("ix_users_telegram_user_id", "telegram_user_id"),
    )
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id"), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="member", nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    first_message_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_message_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    group = relationship("Group", back_populates="users")
    messages = relationship("MessageLog", back_populates="user", cascade="all, delete-orphan")


class MessageLog(Base):
    """
    Модель логирования сообщений. Исправлено поле nexus_file_id.
    """
    
    __tablename__ = "messages_log"
    __table_args__ = (
        Index("ix_messages_log_user_id", "user_id"),
        Index("ix_messages_log_group_id", "group_id"),
    )
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id"), nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # ИСПРАВЛЕНО: nexus_file_id теперь тоже JSONB для хранения структуры файлов
    nexus_file_id: Mapped[Dict[str, Any]] = mapped_column(JSONB, default=dict, server_default='{}', nullable=False)
    
    media_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    moderation_status: Mapped[str] = mapped_column(String(50), default="clean", nullable=False)
    moderation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    user = relationship("User", back_populates="messages")
    group = relationship("Group", back_populates="messages")