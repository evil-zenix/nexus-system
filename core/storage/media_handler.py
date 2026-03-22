"""
Storage Channel Media Handler.
Управление сохранением медиа в приватном Telegram канале.

Паттерн Storage Channel:
1. Вместо скачивания файлов на диск
2. Используется copyMessage API для копирования в приватный канал
3. Получается постоянный file_id из скопированного сообщения
4. file_id хранится в БД (может использоваться вечно)
5. Файл остается на Telegram серверах

Преимущества:
✓ Не занимает место на жестком диске ПК
✓ Не требует периодической очистки
✓ Надежное хранилище (Telegram серверы)
✓ Быстрый доступ через Telegram API
✓ file_id не истекает со временем
"""
import json
from typing import Optional, Dict, List

from config.settings import settings
from config.logging import get_logger

logger = get_logger(__name__)


class StorageChannelHandler:
    """
    Обработчик Storage Channel для Nexus системы.
    Управляет сохранением и организацией файлов в приватном канале.
    """
    
    def __init__(self, storage_channel_id: int):
        """
        Инициализировать обработчик Storage Channel.
        
        Args:
            storage_channel_id: ID приватного Telegram канала для хранения
        """
        self.storage_channel_id = storage_channel_id
        
        if not storage_channel_id:
            logger.warning(
                "⚠️ Storage Channel ID не установлен. "
                "Медиа не будут сохраняться в Storage Channel."
            )
        else:
            logger.info(
                "🔧 Инициализация Storage Channel Handler",
                storage_channel_id=storage_channel_id,
            )
    
    # ========================================================================
    # FILE_ID MANAGEMENT
    # ========================================================================
    
    def parse_nexus_file_id(self, nexus_file_id: str) -> Dict[str, List[str]]:
        """
        Парсить JSON nexus_file_id в словарь file_id'ов.
        
        Args:
            nexus_file_id: JSON строка с file_id'ами
                          Формат: {"photo": ["file_id_1"], "video": ["file_id_2"]}
        
        Returns:
            Словарь с file_id'ами по типам медиа
        
        Пример:
            nexus_file_id = '{"photo": ["file_id_1"], "video": []}'
            result = parse_nexus_file_id(nexus_file_id)
            # {"photo": ["file_id_1"], "video": []}
        """
        try:
            if not nexus_file_id or nexus_file_id == "{}":
                return {}
            
            file_ids = json.loads(nexus_file_id)
            return file_ids
        
        except json.JSONDecodeError:
            logger.error(
                "💥 Ошибка парсинга nexus_file_id",
                nexus_file_id=nexus_file_id,
            )
            return {}
    
    def create_nexus_file_id(self, file_ids: Dict[str, List[str]]) -> str:
        """
        Создать JSON nexus_file_id из словаря file_id'ов.
        
        Args:
            file_ids: Словарь с file_id'ами
                     Формат: {"photo": ["file_id_1"], "video": ["file_id_2"]}
        
        Returns:
            JSON строка
        
        Пример:
            file_ids = {"photo": ["file_id_1"], "video": []}
            result = create_nexus_file_id(file_ids)
            # '{"photo": ["file_id_1"], "video": []}'
        """
        try:
            return json.dumps(file_ids, ensure_ascii=False)
        
        except Exception as e:
            logger.error(
                "💥 Ошибка создания nexus_file_id",
                error=str(e),
            )
            return "{}"
    
    # ========================================================================
    # FILE_ID VALIDATION
    # ========================================================================
    
    def is_valid_file_id(self, file_id: str) -> bool:
        """
        Проверить что file_id валидный Telegram file_id.
        
        Args:
            file_id: Telegram file_id для проверки
        
        Returns:
            True если валидный, False иначе
        
        Примечание:
            Telegram file_id'ы - это строки формата:
            - 3d46a03ecc5c70bc7be8a58e15b8c7b6a
            - AgACAgIAAxkBAAI...
        """
        if not file_id or not isinstance(file_id, str):
            return False
        
        # Минимальная длина file_id
        if len(file_id) < 20:
            return False
        
        # Максимальная длина file_id
        if len(file_id) > 200:
            return False
        
        return True
    
    # ========================================================================
    # MEDIA STATS
    # ========================================================================
    
    def get_media_count(self, nexus_file_id: str) -> int:
        """
        Получить количество файлов в nexus_file_id.
        
        Args:
            nexus_file_id: JSON с file_id'ами
        
        Returns:
            Количество файлов
        
        Пример:
            nexus_file_id = '{"photo": ["file_id_1", "file_id_2"], "video": ["file_id_3"]}'
            count = get_media_count(nexus_file_id)
            # 3
        """
        file_ids = self.parse_nexus_file_id(nexus_file_id)
        
        total_count = 0
        for media_type, ids in file_ids.items():
            if isinstance(ids, list):
                total_count += len(ids)
        
        return total_count
    
    def get_media_by_type(self, nexus_file_id: str, media_type: str) -> List[str]:
        """
        Получить file_id'ы определенного типа.
        
        Args:
            nexus_file_id: JSON с file_id'ами
            media_type: Тип медиа (photo, video, document)
        
        Returns:
            Список file_id'ов для этого типа
        
        Пример:
            nexus_file_id = '{"photo": ["file_id_1", "file_id_2"], "video": ["file_id_3"]}'
            photos = get_media_by_type(nexus_file_id, "photo")
            # ["file_id_1", "file_id_2"]
        """
        file_ids = self.parse_nexus_file_id(nexus_file_id)
        
        if media_type in file_ids:
            return file_ids[media_type]
        
        return []
    
    # ========================================================================
    # LOGGING UTILITIES
    # ========================================================================
    
    def log_storage_info(
        self,
        message_id: int,
        user_id: int,
        nexus_file_id: str,
        media_type: Optional[str] = None,
    ) -> None:
        """
        Залогировать информацию о сохраненном медиа.
        
        Args:
            message_id: Telegram Message ID оригинального сообщения
            user_id: Telegram User ID
            nexus_file_id: JSON с file_id'ами из Storage Channel
            media_type: Тип медиа (опционально)
        """
        media_count = self.get_media_count(nexus_file_id)
        
        logger.info(
            "📦 Медиа сохранено в Storage Channel",
            message_id=message_id,
            user_id=user_id,
            media_count=media_count,
            media_type=media_type,
            nexus_file_id=nexus_file_id[:50] + "..." if len(nexus_file_id) > 50 else nexus_file_id,
        )
    
    # ========================================================================
    # EXAMPLE USAGE
    # ========================================================================
    
    @staticmethod
    def example_usage():
        """
        Пример использования Storage Channel Handler.
        
        Пример:
            handler = StorageChannelHandler(storage_channel_id=-1001234567890)
            
            # Создать nexus_file_id
            file_ids = {"photo": ["file_id_1", "file_id_2"], "video": ["file_id_3"]}
            nexus_file_id = handler.create_nexus_file_id(file_ids)
            
            # Парсить nexus_file_id
            parsed = handler.parse_nexus_file_id(nexus_file_id)
            
            # Получить количество файлов
            count = handler.get_media_count(nexus_file_id)
            
            # Получить фото
            photos = handler.get_media_by_type(nexus_file_id, "photo")
            
            # Логировать информацию
            handler.log_storage_info(
                message_id=999,
                user_id=123456789,
                nexus_file_id=nexus_file_id,
                media_type="photo",
            )
        """
        pass


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def create_storage_caption(
    user_id: int,
    username: Optional[str],
    chat_id: int,
    message_id: int,
) -> str:
    """
    Создать подпись для сообщения в Storage Channel.
    
    Args:
        user_id: Telegram User ID
        username: Username пользователя (опционально)
        chat_id: Telegram Chat ID
        message_id: Telegram Message ID
    
    Returns:
        Форматированная подпись
    
    Пример:
        caption = create_storage_caption(
            user_id=123456789,
            username="john_doe",
            chat_id=-1001234567890,
            message_id=999,
        )
        # "[NEXUS STORAGE]\nUser: @john_doe (123456789)\nChat: -1001234567890\nMessage: 999"
    """
    user_str = f"@{username}" if username else f"ID:{user_id}"
    
    caption = (
        f"[NEXUS STORAGE]\n"
        f"User: {user_str} ({user_id})\n"
        f"Chat: {chat_id}\n"
        f"Message: {message_id}"
    )
    
    return caption


# Инициализировать глобальный обработчик Storage Channel
storage_handler: Optional[StorageChannelHandler] = None

if settings.storage_channel_id:
    storage_handler = StorageChannelHandler(settings.storage_channel_id)