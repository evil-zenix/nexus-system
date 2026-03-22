"""
Telegram API утилиты для Nexus системы.
"""
from typing import Optional

from aiogram import Bot
from aiogram.types import File

from config.logging import get_logger

logger = get_logger(__name__)


class TelegramClient:
    """
    Обертка над aiogram Bot для удобства.
    """
    
    def __init__(self, token: str):
        """
        Инициализировать Telegram клиент.
        
        Args:
            token: Telegram Bot Token
        """
        self.token = token
        self.bot = Bot(token=token)
    
    async def get_file_info(self, file_id: str) -> Optional[File]:
        """
        Получить информацию о файле.
        
        Args:
            file_id: Telegram File ID
        
        Returns:
            File информация или None если ошибка
        """
        try:
            file_info = await self.bot.get_file(file_id)
            return file_info
        except Exception as e:
            logger.error(
                "💥 Ошибка при получении информации о файле",
                file_id=file_id,
                error=str(e),
            )
            return None
    
    async def download_file(
        self,
        file_id: str,
        destination: str,
    ) -> bool:
        """
        Скачать файл (НЕ рекомендуется для Storage Channel паттерна!).
        
        Args:
            file_id: Telegram File ID
            destination: Путь для сохранения файла
        
        Returns:
            True если успешно скачан, False иначе
        """
        try:
            file_info = await self.get_file_info(file_id)
            
            if not file_info:
                return False
            
            await self.bot.download_file(file_info.file_path, destination)
            logger.info(
                "✅ Файл скачан",
                file_id=file_id,
                destination=destination,
            )
            return True
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при скачивании файла",
                file_id=file_id,
                error=str(e),
            )
            return False
    
    async def close(self) -> None:
        """Закрыть сессию бота"""
        await self.bot.session.close()