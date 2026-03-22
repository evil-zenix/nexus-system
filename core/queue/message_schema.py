"""
Pydantic схемы (DTO) для сообщений в очереди Redis.
Используются для валидации и сериализации данных.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime

from pydantic import BaseModel, Field


# ============================================================================
# TELEGRAM UPDATE & MESSAGE SCHEMAS
# ============================================================================

class TelegramUser(BaseModel):
    """Telegram User из API"""
    id: int
    is_bot: bool = False
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None


class TelegramChat(BaseModel):
    """Telegram Chat из API"""
    id: int
    type: str  # private, group, supergroup, channel
    title: Optional[str] = None
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    description: Optional[str] = None


class TelegramPhotoSize(BaseModel):
    """Telegram PhotoSize"""
    file_id: str
    file_unique_id: str
    width: int
    height: int
    file_size: Optional[int] = None


class TelegramDocument(BaseModel):
    """Telegram Document"""
    file_id: str
    file_unique_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class TelegramVideo(BaseModel):
    """Telegram Video"""
    file_id: str
    file_unique_id: str
    width: int
    height: int
    duration: int
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class TelegramMessage(BaseModel):
    """Telegram Message из API"""
    message_id: int
    date: int
    chat: TelegramChat
    from_user: Optional[TelegramUser] = Field(None, alias="from")
    text: Optional[str] = None
    photo: Optional[List[TelegramPhotoSize]] = None
    document: Optional[TelegramDocument] = None
    video: Optional[TelegramVideo] = None
    caption: Optional[str] = None
    edit_date: Optional[int] = None
    is_topic_message: Optional[bool] = None
    
    class Config:
        populate_by_name = True  # Разрешить population by alias


class TelegramUpdate(BaseModel):
    """Telegram Update (webhook payload)"""
    update_id: int
    message: Optional[TelegramMessage] = None
    edited_message: Optional[TelegramMessage] = None
    channel_post: Optional[TelegramMessage] = None
    edited_channel_post: Optional[TelegramMessage] = None
    callback_query: Optional[Dict[str, Any]] = None
    
    class Config:
        extra = "allow"  # Разрешить дополнительные поля


# ============================================================================
# NEXUS INTERNAL SCHEMAS (схемы для очереди Nexus)
# ============================================================================

class NexusMessage(BaseModel):
    """
    Сообщение Nexus для очереди.
    Минимальный набор данных для обработки воркерами.
    """
    # Telegram информация
    bot_name: str = Field(description="Имя бота (moderator_bot, osint_bot)")
    bot_id: int = Field(description="Telegram Bot ID")
    bot_token: str = Field(description="Telegram Bot Token")
    
    message_id: int = Field(description="Telegram Message ID")
    chat_id: int = Field(description="Telegram Chat ID")
    chat_type: str = Field(description="Тип чата (private, group, supergroup, channel)")
    chat_title: Optional[str] = Field(None, description="Название чата/группы")
    chat_username: Optional[str] = Field(None, description="Username чата")
    
    # User информация
    user_id: int = Field(description="Telegram User ID")
    username: Optional[str] = Field(None, description="Username пользователя")
    first_name: Optional[str] = Field(None, description="Имя пользователя")
    last_name: Optional[str] = Field(None, description="Фамилия пользователя")
    
    # Содержание сообщения
    text: Optional[str] = Field(None, description="Текст сообщения")
    caption: Optional[str] = Field(None, description="Подпись к медиа")
    
    # Медиа информация
    has_media: bool = Field(False, description="Содержит ли медиа")
    media_type: Optional[str] = Field(None, description="Тип медиа (photo, video, document, audio)")
    
    # Для фото
    photo_file_ids: Optional[List[str]] = Field(None, description="File ID фотографий (от большей к меньшей)")
    
    # Для видео
    video_file_id: Optional[str] = Field(None, description="File ID видео")
    video_duration: Optional[int] = Field(None, description="Длительность видео (сек)")
    
    # Для документов
    document_file_id: Optional[str] = Field(None, description="File ID документа")
    document_name: Optional[str] = Field(None, description="Имя файла документа")
    
    # Timestamp
    message_date: int = Field(description="Unix timestamp сообщения")
    is_edited: bool = Field(False, description="Отредактировано ли сообщение")
    
    # Raw Telegram Update (для расширенной обработки)
    raw_update: Optional[Dict[str, Any]] = Field(None, description="Полный JSON update от Telegram")


class QueuedTask(BaseModel):
    """
    Обертка над сообщением в очереди.
    Добавляет метаинформацию о времени добавления и TTL.
    """
    data: NexusMessage = Field(description="Основные данные сообщения")
    enqueued_at: str = Field(description="ISO timestamp когда задача была добавлена")
    ttl: int = Field(description="Time To Live задачи (сек)")


# ============================================================================
# WORKER RESULT SCHEMAS (результаты работы воркеров)
# ============================================================================

class ModerationResult(BaseModel):
    """Результат модерации от воркера"""
    is_spam: bool = Field(description="Определено ли как спам")
    confidence: float = Field(description="Уверенность 0.0-1.0")
    reason: Optional[str] = Field(None, description="Причина флага")
    action: Optional[str] = Field(None, description="Рекомендуемое действие (delete, warn, ban)")


class MediaStorageResult(BaseModel):
    """Результат сохранения медиа в Storage Channel"""
    media_type: str = Field(description="Тип медиа")
    file_id: str = Field(description="File ID из Storage Channel")
    file_size: Optional[int] = Field(None, description="Размер файла")
    storage_message_id: int = Field(description="Message ID в Storage Channel")


class ProcessingResult(BaseModel):
    """Общий результат обработки задачи"""
    success: bool = Field(description="Успешно ли обработана задача")
    message: Optional[str] = Field(None, description="Описание результата")
    moderation: Optional[ModerationResult] = Field(None, description="Результат модерации")
    media_storage: Optional[List[MediaStorageResult]] = Field(None, description="Результаты сохранения медиа")
    error: Optional[str] = Field(None, description="Ошибка если есть")
    processing_time_ms: int = Field(0, description="Время обработки в миллисекундах")


# ============================================================================
# HEALTH CHECK SCHEMAS
# ============================================================================

class HealthCheckResponse(BaseModel):
    """Ответ на health check запрос"""
    status: str = Field("ok", description="Статус (ok, degraded, error)")
    timestamp: str = Field(description="ISO timestamp")
    services: Dict[str, bool] = Field(description="Статус сервисов (redis, database, telegram)")
    version: str = Field("0.1.0", description="Версия приложения")


# ============================================================================
# WEBHOOK VALIDATION SCHEMAS
# ============================================================================

class WebhookRequest(BaseModel):
    """
    Валидационная схема для webhook запроса от Telegram.
    Используется для парсинга и валидации JSON от Telegram.
    """
    update_id: int
    message: Optional[TelegramMessage] = None
    edited_message: Optional[TelegramMessage] = None
    channel_post: Optional[TelegramMessage] = None
    edited_channel_post: Optional[TelegramMessage] = None
    callback_query: Optional[Dict[str, Any]] = None
    
    class Config:
        extra = "allow"  # Разрешить дополнительные неизвестные поля


class WebhookResponse(BaseModel):
    """Ответ на webhook запрос"""
    ok: bool = True
    message: str = "Update received and queued"