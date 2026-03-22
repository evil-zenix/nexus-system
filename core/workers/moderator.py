"""
Moderator Worker для Nexus системы.
Проверяет сообщения на спам и применяет модерацию.
"""
import re
import time
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot

from config.logging import get_logger
from core.workers.base_worker import BaseWorker
from core.queue.message_schema import NexusMessage, ProcessingResult, ModerationResult
from core.database import queries

logger = get_logger(__name__)


class ModeratorWorker(BaseWorker):
    """
    Воркер модератора.
    
    Функциональность:
    1. Проверяет сообщения на спам-паттерны
    2. Проверяет на запрещенные слова
    3. Проверяет на flood (много ссылок, много капса)
    4. Применяет действия (warn, delete, ban)
    5. Логирует результаты в БД
    
    Конфигурация через переменные окружения:
    - MODERATOR_DELETE_SPAM: удалять ли спам-сообщения
    - MODERATOR_BAN_THRESHOLD: количество warnings перед баном
    - MODERATOR_AUTO_DELETE_URLS: удалять ли ссылки
    """
    
    def __init__(self, concurrency: int = 5):
        """Инициализировать Moderator Worker"""
        super().__init__(
            worker_name="ModeratorWorker",
            queue_name="tasks:moderator",
            concurrency=concurrency,
        )
        
        # Конфигурация модерации
        self.delete_spam = True
        self.ban_threshold = 3
        self.auto_delete_urls = False
        
        # Паттерны спама
        self.spam_patterns = self._init_spam_patterns()
        self.forbidden_words = self._init_forbidden_words()
        
        logger.info("🚔 Moderator Worker инициализирован")
    
    # ========================================================================
    # SPAM DETECTION PATTERNS
    # ========================================================================
    
    def _init_spam_patterns(self) -> List[Tuple[str, str]]:
        """
        Инициализировать паттерны спама (regex).
        
        Returns:
            Список (паттерн, описание)
        """
        return [
            # Много ссылок
            (r"(https?://|www\.)\S+", "multiple_links"),
            # Много (@) упоминаний
            (r"@\w+", "mentions_spam"),
            # Много повторяющихся символов
            (r"(.)\1{4,}", "repeated_chars"),
            # Много капса (> 50% букв в капсе)
            (r"[A-Z]{5,}", "excessive_caps"),
            # Много восклицательных знаков
            (r"!{3,}", "excessive_exclamation"),
            # Много вопросительных знаков
            (r"\?{3,}", "excessive_question"),
            # Emoji flood (много эмодзи подряд)
            (r"[\U0001F300-\U0001F9FF]{5,}", "emoji_flood"),
        ]
    
    def _init_forbidden_words(self) -> List[str]:
        """
        Инициализировать список запрещенных слов.
        В production это должна быть БД таблица.
        
        Returns:
            Список запрещенных слов
        """
        return [
            "xxx",  # Пример
            "spam",  # Пример
            "bot",  # Пример
        ]
    
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
        
        Args:
            message: NexusMessage из Redis
            session: AsyncSession для БД
        
        Returns:
            ProcessingResult с результатом модерации
        """
        start_time = time.time()
        
        try:
            # Пропустить пустые сообщения
            if not message.text and not message.caption:
                logger.debug(
                    "ℹ️ Пустое сообщение (только медиа)",
                    message_id=message.message_id,
                )
                return ProcessingResult(
                    success=True,
                    message="Empty message (no text), skipped",
                    processing_time_ms=int((time.time() - start_time) * 1000),
                )
            
            # Получить текст сообщения
            text = (message.text or message.caption or "").strip()
            
            logger.debug(
                "🔍 Проверка на спам",
                message_id=message.message_id,
                user_id=message.user_id,
                text_length=len(text),
            )
            
            # ================================================================
            # STEP 1: Проверить на спам
            # ================================================================
            
            moderation_result = await self._check_spam(text)
            
            if moderation_result.is_spam:
                logger.warning(
                    "⚠️ Обнаружен спам",
                    message_id=message.message_id,
                    reason=moderation_result.reason,
                    confidence=moderation_result.confidence,
                )
            else:
                logger.debug(
                    "✅ Сообщение чистое",
                    message_id=message.message_id,
                )
            
            # ================================================================
            # STEP 2: Обновить статус модерации в БД
            # ================================================================
            
            # Получить или создать пользователя
            user = await queries.get_or_create_user(
                session=session,
                telegram_user_id=message.user_id,
                group_id=1,  # Будет обновлено после создания group
                username=message.username,
                first_name=message.first_name,
                last_name=message.last_name,
            )
            
            # Получить или создать группу
            group = await queries.get_or_create_group(
                session=session,
                telegram_chat_id=message.chat_id,
                bot_id=1,  # Будет обновлено после поиска бота
                chat_type=message.chat_type,
                title=message.chat_title,
                username=message.chat_username,
            )
            
            # Обновить group_id в user если не заполнен
            if user.group_id != group.id:
                user.group_id = group.id
                await session.commit()
            
            # Создать/обновить лог сообщения
            moderation_status = "spam" if moderation_result.is_spam else "clean"
            
            message_log = await queries.create_message_log(
                session=session,
                user_id=user.id,
                group_id=group.id,
                telegram_message_id=message.message_id,
                message_text=text,
                has_media=message.has_media,
                media_type=message.media_type,
                nexus_file_id="{}",  # Пока пусто (заполнится в OSINT воркере)
                moderation_status=moderation_status,
            )
            
            logger.info(
                "📝 Статус модерации сохранен",
                message_log_id=message_log.id,
                moderation_status=moderation_status,
            )
            
            # ================================================================
            # STEP 3: Применить действия если нужно
            # ================================================================
            
            action_taken = False
            
            if moderation_result.is_spam and self.delete_spam:
                # Получить экземпляр бота
                bot = await self.get_bot_instance(message.bot_token)
                
                # Удалить сообщение
                delete_success = await self.delete_message(
                    bot=bot,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                )
                
                if delete_success:
                    logger.info(
                        "🗑️ Спам-сообщение удалено",
                        message_id=message.message_id,
                        chat_id=message.chat_id,
                    )
                    action_taken = True
                    
                    # Отправить предупреждение пользователю
                    warning_text = (
                        f"⚠️ Ваше сообщение было удалено за нарушение правил.\n"
                        f"Причина: {moderation_result.reason}"
                    )
                    
                    await self.send_telegram_message(
                        bot=bot,
                        chat_id=message.chat_id,
                        text=warning_text,
                        reply_to_message_id=message.message_id,
                    )
                    
                    # Увеличить счетчик warnings
                    user.warnings_count += 1
                    
                    # Проверить порог бана
                    if user.warnings_count >= self.ban_threshold:
                        logger.warning(
                            "🚫 Пользователь достиг порога бана",
                            user_id=message.user_id,
                            warnings=user.warnings_count,
                            threshold=self.ban_threshold,
                        )
                        
                        # Забанить пользователя
                        ban_success = await self.restrict_user(
                            bot=bot,
                            chat_id=message.chat_id,
                            user_id=message.user_id,
                            can_send_messages=False,
                        )
                        
                        if ban_success:
                            user.is_banned = True
                            logger.info(
                                "🚷 Пользователь забанен",
                                user_id=message.user_id,
                                chat_id=message.chat_id,
                            )
                    
                    await session.commit()
            
            # ================================================================
            # STEP 4: Вернуть результат
            # ================================================================
            
            return ProcessingResult(
                success=True,
                message=f"Spam check completed. Is spam: {moderation_result.is_spam}",
                moderation=moderation_result,
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
        
        except Exception as e:
            logger.error(
                "💥 Ошибка при обработке в Moderator Worker",
                error=str(e),
                exc_type=type(e).__name__,
                message_id=message.message_id,
            )
            
            return ProcessingResult(
                success=False,
                message="Error during moderation processing",
                error=str(e),
                processing_time_ms=int((time.time() - start_time) * 1000),
            )
    
    # ========================================================================
    # SPAM DETECTION LOGIC
    # ========================================================================
    
    async def _check_spam(self, text: str) -> ModerationResult:
        """
        Проверить сообщение на спам.
        
        Args:
            text: Текст сообщения
        
        Returns:
            ModerationResult с результатом проверки
        """
        if not text:
            return ModerationResult(
                is_spam=False,
                confidence=0.0,
            )
        
        # Счетчик подозрений
        suspicion_score = 0.0
        reason = None
        
        # ================================================================
        # CHECK 1: Запрещенные слова
        # ================================================================
        
        text_lower = text.lower()
        for word in self.forbidden_words:
            if word.lower() in text_lower:
                suspicion_score += 0.3
                reason = f"forbidden_word: {word}"
                logger.debug(
                    "🚩 Обнаружено запрещенное слово",
                    word=word,
                )
                break
        
        # ================================================================
        # CHECK 2: Спам-паттерны (regex)
        # ================================================================
        
        for pattern, pattern_name in self.spam_patterns:
            if re.search(pattern, text):
                suspicion_score += 0.2
                if not reason:
                    reason = pattern_name
                logger.debug(
                    "🚩 Обнаружен спам-паттерн",
                    pattern=pattern_name,
                )
                
                # Если найдено несколько паттернов, это явно спам
                if suspicion_score > 0.4:
                    break
        
        # ================================================================
        # CHECK 3: Статистический анализ
        # ================================================================
        
        # Процент капсовых букв
        capital_letters = sum(1 for c in text if c.isupper())
        capital_percentage = (capital_letters / len(text)) * 100 if text else 0
        
        if capital_percentage > 50 and len(text) > 10:
            suspicion_score += 0.15
            if not reason:
                reason = "excessive_caps"
            logger.debug(
                "🚩 Слишком много заглавных букв",
                percentage=capital_percentage,
            )
        
        # Количество специальных символов
        special_chars = sum(1 for c in text if not c.isalnum() and not c.isspace())
        special_percentage = (special_chars / len(text)) * 100 if text else 0
        
        if special_percentage > 30:
            suspicion_score += 0.15
            if not reason:
                reason = "excessive_special_chars"
            logger.debug(
                "🚩 Слишком много специальных символов",
                percentage=special_percentage,
            )
        
        # ================================================================
        # DECISION
        # ================================================================
        
        is_spam = suspicion_score > 0.5
        
        return ModerationResult(
            is_spam=is_spam,
            confidence=min(suspicion_score, 1.0),
            reason=reason,
            action="delete" if is_spam else None,
        )