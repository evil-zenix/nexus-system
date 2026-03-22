"""
OSINT/Scraper Worker для Nexus системы.
Логирует сообщения в БД и сохраняет медиа в приватный Storage Channel.
"""
import json
import time
from typing import Optional, List, Dict

from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.types import InputFile

from config.settings import settings
from config.logging import get_logger
from core.workers.base_worker import BaseWorker
from core.queue.message_schema import NexusMessage, ProcessingResult, MediaStorageResult
from core.database import queries

logger = get_logger(__name__)


class OSINTWorker(BaseWorker):
    """
    OSINT/Scraper Worker для Nexus системы.
    
    Функциональность:
    1. Логирует ВСЕ сообщения в messages_log таблицу
    2. Сохраняет текст сообщения
    3. Для медиа: использует copyMessage API для сохранения в Storage Channel
    4. Записывает file_id из Storage Channel в БД (НЕ скачивает на диск!)
    5. Ведет полный лог активности пользователя
    
    Storage Channel паттерн:
    - Бот копирует сообщение с медиа в приватный системный канал
    - Получает file_id из скопированного сообщения
    - Сохраняет file_id в БД (вечная ссылка на файл в Telegram)
    - Файл остается на Telegram серверах, не занимая место на диске
    """
    
    def __init__(self, concurrency: int = 3):
        """Инициализировать OSINT Worker"""
        super().__init__(
            worker_name="OSINTWorker",
            queue_name="tasks:osint",
            concurrency=concurrency,
        )
        
        # ID приватного канала для хранения медиа
        self.storage_channel_id = settings.storage_channel_id
        
        if not self.storage_channel_id:
            logger.warning(
                "⚠️ STORAGE_CHANNEL_ID не установлен! "
                "Медиа не будут сохраняться в Storage Channel."
            )
        
        logger.info(
            "🔍 OSINT Worker инициализирован",
            storage_channel_id=self.storage_channel_id,
        )
    
    # ========================================================================
    # MAIN PROCESSING METHOD
    # ========================================================================
    
    async def process(
        self,
        message: NexusMessage,
        session: AsyncSession,
    ) -> ProcessingResult:
        """
        Обработать сообщение (основной метод воркера).
        
        Логирует сообщение и сохраняет медиа в Storage Channel.
        
        Args:
            message: NexusMessage из Redis
            session: AsyncSession для БД
        
        Returns:
            ProcessingResult с результатом обработки
        """
        start_time = time.time()
        
        try:
            logger.debug(
                "📥 OSINT обработка сообщения",
                message_id=message.message_id,
                user_id=message.user_id,
                has_media=message.has_media,
            )
            
            # ================================================================
            # STEP 1: Создать/получить записи в БД
            # ================================================================
            
            # Создать или получить группу
            group = await queries.get_or_create_group(
                session=session,
                telegram_chat_id=message.chat_id,
                bot_id=1,  # TODO: получить real bot_id из БД
                chat_type=message.chat_type,
                title=message.chat_title,
                username=message.chat_username,
            )
            
            # Создать или получить пользователя
            user = await queries.get_or_create_user(
                session=session,
                telegram_user_id=message.user_id,
                group_id=group.id,
                username=message.username,
                first_name=message.first_name,
                last_name=message.last_name,
            )
            
            logger.debug(
                "✅ Записи в БД созданы",
                group_id=group.id,
                user_id=user.id,
            )
            
            # ================================================================
            # STEP 2: Обработать медиа (если есть)
            # ================================================================
            
            nexus_file_id = "{}"
            media_storage_results = []
            
            if message.has_media and self.storage_channel_id:
                logger.info(
                    "📸 Обработка медиа",
                    message_id=message.message_id,
                    media_type=message.media_type,
                )
                
                # Получить экземпляр бота
                bot = await self.get_bot_instance(message.bot_token)
                
                # Обработать медиа в зависимости от типа
                if message.media_type == "photo" and message.photo_file_ids:
                    nexus_file_id, results = await self._process_photo(
                        bot=bot,
                        message=message,
                    )
                    media_storage_results.extend(results)
                
                elif message.media_type == "video" and message.video_file_id:
                    nexus_file_id, results = await self._process_video(
                        bot=bot,
                        message=message,
                    )
                    media_storage_results.extend(results)
                
                elif message.media_type == "document" and message.document_file_id:
                    nexus_file_id, results = await self._process_document(
                        bot=bot,
                        message=message,
                    )
                    media_storage_results.extend(results)
                
                logger.info(
                    "✅ Медиа обработано",
                    message_id=message.message_id,
                    storage_count=len(media_storage_results),
                )
            
            # ================================================================
            # STEP 3: Логировать сообщение в БД
            # ================================================================
            
            message_log = await queries.create_message_log(
                session=session,
                user_id=user.id,
                group_id=group.id,
                telegram_message_id=message.message_id,
                message_text=message.text or message.caption,
                has_media=message.has_media,
                media_type=message.media_type,
                nexus_file_id=nexus_file_id,  # JSON с file_id'ами из Storage Channel
                moderation_status="clean",  # Moderator воркер обновит это значение
            )
            
            logger.info(
                "✅ Сообщение залогировано",
                message_log_id=message_log.id,
                message_id=message.message_id,
            )
            
            # ================================================================
            # STEP 4: Вернуть результат
            # ================================================================
            
            return ProcessingResult(
                success=True,
                message=f"Message logged successfully. Media items: {len(media_storage_results)}",
                media_storage=media_storage_results if media_storage_results else None,
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при обработке в OSINT Worker",
                error=str(e),
                exc_type=type(e).__name__,
                message_id=message.message_id,
            )
            
            return ProcessingResult(
                success=False,
                message="Error during OSINT processing",
                error=str(e),
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
    
    # ========================================================================
    # MEDIA PROCESSING
    # ========================================================================
    
    async def _process_photo(
        self,
        bot: Bot,
        message: NexusMessage,
    ) -> tuple[str, List[MediaStorageResult]]:
        """
        Обработать фотографию через Storage Channel.
        
        Процесс:
        1. Скопировать сообщение с фото в Storage Channel через copyMessage
        2. Получить file_id из скопированного сообщения
        3. Вернуть JSON с file_id'ами
        
        Args:
            bot: Bot экземпляр
            message: NexusMessage с фотографией
        
        Returns:
            (nexus_file_id JSON, список MediaStorageResult)
        """
        results = []
        file_ids = {}
        
        try:
            # Использовать file_id с наибольшим разрешением (первый в списке)
            if message.photo_file_ids:
                best_photo_file_id = message.photo_file_ids[0]
                
                logger.debug(
                    "📸 Копирование фото в Storage Channel",
                    message_id=message.message_id,
                    file_id=best_photo_file_id,
                )
                
                # Скопировать сообщение в Storage Channel
                # (это получит постоянный file_id)
                try:
                    copied_message = await bot.copy_message(
                        chat_id=self.storage_channel_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                        caption=(
                            f"[STORAGE]\n"
                            f"Original: @{message.username or 'unknown'} "
                            f"({message.user_id})\n"
                            f"Chat: {message.chat_id}\n"
                            f"Message ID: {message.message_id}"
                        ),
                    )
                    
                    # Получить file_id из скопированного сообщения
                    if copied_message.photo:
                        storage_file_id = copied_message.photo[-1].file_id
                        file_ids["photo"] = [storage_file_id]
                        
                        results.append(
                            MediaStorageResult(
                                media_type="photo",
                                file_id=storage_file_id,
                                file_size=copied_message.photo[-1].file_size,
                                storage_message_id=copied_message.message_id,
                            )
                        )
                        
                        logger.info(
                            "✅ Фото скопировано в Storage Channel",
                            original_message_id=message.message_id,
                            storage_message_id=copied_message.message_id,
                            file_id=storage_file_id,
                        )
                
                except Exception as e:
                    logger.error(
                        "💥 Ошибка при копировании фото в Storage Channel",
                        error=str(e),
                        message_id=message.message_id,
                    )
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при обработке фото",
                error=str(e),
                message_id=message.message_id,
            )
        
        nexus_file_id = json.dumps(file_ids, ensure_ascii=False)
        return nexus_file_id, results
    
    async def _process_video(
        self,
        bot: Bot,
        message: NexusMessage,
    ) -> tuple[str, List[MediaStorageResult]]:
        """
        Обработать видео через Storage Channel.
        
        Args:
            bot: Bot экземпляр
            message: NexusMessage с видео
        
        Returns:
            (nexus_file_id JSON, список MediaStorageResult)
        """
        results = []
        file_ids = {}
        
        try:
            if message.video_file_id:
                logger.debug(
                    "🎥 Копирование видео в Storage Channel",
                    message_id=message.message_id,
                    file_id=message.video_file_id,
                )
                
                try:
                    copied_message = await bot.copy_message(
                        chat_id=self.storage_channel_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                        caption=(
                            f"[STORAGE VIDEO]\n"
                            f"Original: @{message.username or 'unknown'} "
                            f"({message.user_id})\n"
                            f"Chat: {message.chat_id}\n"
                            f"Duration: {message.video_duration}s"
                        ),
                    )
                    
                    if copied_message.video:
                        storage_file_id = copied_message.video.file_id
                        file_ids["video"] = [storage_file_id]
                        
                        results.append(
                            MediaStorageResult(
                                media_type="video",
                                file_id=storage_file_id,
                                file_size=copied_message.video.file_size,
                                storage_message_id=copied_message.message_id,
                            )
                        )
                        
                        logger.info(
                            "✅ Видео скопировано в Storage Channel",
                            original_message_id=message.message_id,
                            storage_message_id=copied_message.message_id,
                            file_id=storage_file_id,
                        )
                
                except Exception as e:
                    logger.error(
                        "💥 Ошибка при копировании видео в Storage Channel",
                        error=str(e),
                        message_id=message.message_id,
                    )
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при обработке видео",
                error=str(e),
                message_id=message.message_id,
            )
        
        nexus_file_id = json.dumps(file_ids, ensure_ascii=False)
        return nexus_file_id, results
    
    async def _process_document(
        self,
        bot: Bot,
        message: NexusMessage,
    ) -> tuple[str, List[MediaStorageResult]]:
        """
        Обработать документ через Storage Channel.
        
        Args:
            bot: Bot экземпляр
            message: NexusMessage с документом
        
        Returns:
            (nexus_file_id JSON, список MediaStorageResult)
        """
        results = []
        file_ids = {}
        
        try:
            if message.document_file_id:
                logger.debug(
                    "📄 Копирование документа в Storage Channel",
                    message_id=message.message_id,
                    file_id=message.document_file_id,
                    filename=message.document_name,
                )
                
                try:
                    copied_message = await bot.copy_message(
                        chat_id=self.storage_channel_id,
                        from_chat_id=message.chat_id,
                        message_id=message.message_id,
                        caption=(
                            f"[STORAGE DOCUMENT]\n"
                            f"Original: @{message.username or 'unknown'} "
                            f"({message.user_id})\n"
                            f"File: {message.document_name}\n"
                            f"Chat: {message.chat_id}"
                        ),
                    )
                    
                    if copied_message.document:
                        storage_file_id = copied_message.document.file_id
                        file_ids["document"] = [storage_file_id]
                        
                        results.append(
                            MediaStorageResult(
                                media_type="document",
                                file_id=storage_file_id,
                                file_size=copied_message.document.file_size,
                                storage_message_id=copied_message.message_id,
                            )
                        )
                        
                        logger.info(
                            "✅ Документ скопирован в Storage Channel",
                            original_message_id=message.message_id,
                            storage_message_id=copied_message.message_id,
                            file_id=storage_file_id,
                            filename=message.document_name,
                        )
                
                except Exception as e:
                    logger.error(
                        "💥 Ошибка при копировании документа в Storage Channel",
                        error=str(e),
                        message_id=message.message_id,
                    )
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при обработке документа",
                error=str(e),
                message_id=message.message_id,
            )
        
        nexus_file_id = json.dumps(file_ids, ensure_ascii=False)
        return nexus_file_id, results