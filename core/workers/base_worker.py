"""
Базовый класс для воркеров (Workers).
Все воркеры наследуются от BaseWorker и реализуют метод process().
"""
import asyncio
import time
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from config.logging import get_logger
from core.queue.redis_client import dequeue_task, get_queue_length
from core.queue.message_schema import NexusMessage, ProcessingResult
from core.database.db import get_db_session

logger = get_logger(__name__)


class BaseWorker(ABC):
    """
    Базовый класс для всех воркеров.
    
    Воркер — это бесконечный цикл, который:
    1. Тянет задачи из Redis очереди
    2. Обрабатывает задачу через метод process()
    3. Сохраняет результаты в БД
    4. Повторяет цикл
    
    Подклассы должны реализовать:
    - process(message: NexusMessage) -> ProcessingResult
    
    Примеры:
    - ModeratorWorker: проверяет спам
    - OSINTWorker: логирует сообщения и медиа
    """
    
    def __init__(
        self,
        worker_name: str,
        queue_name: str,
        concurrency: int = 5,
    ):
        """
        Инициализировать воркер.
        
        Args:
            worker_name: Имя воркера (для логирования)
            queue_name: Имя Redis очереди (tasks:moderator)
            concurrency: Количество одновременных задач
        """
        self.worker_name = worker_name
        self.queue_name = queue_name
        self.concurrency = concurrency
        self.running = False
        self.processed_count = 0
        self.error_count = 0
        self.start_time = None
        
        logger.info(
            f"🔧 Инициализация {worker_name}",
            queue=queue_name,
            concurrency=concurrency,
        )
    
    # ========================================================================
    # ABSTRACT METHOD (должен быть реализован в подклассе)
    # ========================================================================
    
    @abstractmethod
    async def process(
        self,
        message: NexusMessage,
        session: AsyncSession,
    ) -> ProcessingResult:
        """
        Обработать сообщение из очереди.
        
        Этот метод должен быть переопределен в подклассе.
        Здесь происходит основная логика воркера.
        
        Args:
            message: NexusMessage из Redis очереди
            session: AsyncSession для работы с БД
        
        Returns:
            ProcessingResult с результатами обработки
        """
        pass
    
    # ========================================================================
    # MAIN WORKER LOOP
    # ========================================================================
    
    async def run(self) -> None:
        """
        Запустить воркер.
        Бесконечный цикл, который тянет задачи из очереди и обрабатывает их.
        """
        self.running = True
        self.start_time = time.time()
        
        logger.info(
            f"✅ {self.worker_name} запущен",
            queue=self.queue_name,
            concurrency=self.concurrency,
        )
        
        # Создать список для отслеживания активных задач
        active_tasks = set()
        
        try:
            while self.running:
                # Проверить очередь
                queue_length = await get_queue_length(self.queue_name)
                
                # Если есть свободные слоты для concurrency
                if len(active_tasks) < self.concurrency and queue_length > 0:
                    # Получить задачу из очереди
                    queued_task = await dequeue_task(self.queue_name)
                    
                    if queued_task:
                        # Создать корутину для обработки
                        task_coro = self._handle_task(queued_task)
                        
                        # Запустить как асинхронную задачу
                        async_task = asyncio.create_task(task_coro)
                        active_tasks.add(async_task)
                        
                        # Добавить callback для удаления из active_tasks
                        async_task.add_done_callback(active_tasks.discard)
                
                # Если есть активные задачи, дождаться одну
                if active_tasks:
                    done, active_tasks = await asyncio.wait(
                        active_tasks,
                        timeout=1.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    # Если нет задач, спать немного
                    await asyncio.sleep(1)
        
        except KeyboardInterrupt:
            logger.info(f"⚠️ {self.worker_name} остановлен (Ctrl+C)")
        
        except Exception as e:
            logger.error(
                f"💥 Критическая ошибка в {self.worker_name}",
                error=str(e),
                exc_type=type(e).__name__,
            )
        
        finally:
            # Дождаться завершения всех оставшихся задач
            self.running = False
            
            if active_tasks:
                logger.info(
                    f"⏳ Ожидание завершения {len(active_tasks)} активных задач..."
                )
                await asyncio.gather(*active_tasks, return_exceptions=True)
            
            # Логировать статистику
            self._log_statistics()
    
    async def _handle_task(self, queued_task: Dict[str, Any]) -> None:
        """
        Обработать одну задачу из очереди.
        
        Args:
            queued_task: Словарь с данными задачи (из Redis)
        """
        task_start_time = time.time()
        
        try:
            # Парсить NexusMessage из очереди
            message_data = queued_task.get("data", {})
            message = NexusMessage(**message_data)
            
            logger.debug(
                f"📥 Получена задача",
                worker=self.worker_name,
                message_id=message.message_id,
                user_id=message.user_id,
            )
            
            # Получить сессию БД
            session = await get_db_session()
            
            try:
                # Обработать сообщение через метод подкласса
                result = await self.process(message, session)
                
                # Проверить результат
                if result.success:
                    self.processed_count += 1
                    logger.info(
                        f"✅ Задача обработана успешно",
                        worker=self.worker_name,
                        message_id=message.message_id,
                        processing_time_ms=result.processing_time_ms,
                    )
                else:
                    self.error_count += 1
                    logger.warning(
                        f"⚠️ Задача обработана с ошибкой",
                        worker=self.worker_name,
                        message_id=message.message_id,
                        error=result.error,
                    )
            
            finally:
                await session.close()
        
        except ValueError as e:
            self.error_count += 1
            logger.error(
                f"💥 Ошибка валидации NexusMessage",
                worker=self.worker_name,
                error=str(e),
            )
        
        except Exception as e:
            self.error_count += 1
            logger.error(
                f"💥 Неожиданная ошибка при обработке задачи",
                worker=self.worker_name,
                error=str(e),
                exc_type=type(e).__name__,
            )
        
        finally:
            # Логировать время обработки
            elapsed_time = (time.time() - task_start_time) * 1000
            if elapsed_time > settings.task_timeout * 1000:
                logger.warning(
                    f"⏱️ Задача обработана дольше таймаута",
                    worker=self.worker_name,
                    time_ms=int(elapsed_time),
                    timeout_ms=int(settings.task_timeout * 1000),
                )
    
    async def stop(self) -> None:
        """Остановить воркер"""
        logger.info(f"🛑 Остановка {self.worker_name}...")
        self.running = False
    
    def _log_statistics(self) -> None:
        """Логировать статистику работы воркера"""
        uptime = time.time() - self.start_time
        
        logger.info(
            f"📊 Статистика {self.worker_name}",
            processed=self.processed_count,
            errors=self.error_count,
            uptime_seconds=int(uptime),
            avg_per_second=round(self.processed_count / uptime, 2) if uptime > 0 else 0,
        )
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    async def get_bot_instance(self, bot_token: str) -> Bot:
        """
        Получить экземпляр aiogram Bot.
        
        Args:
            bot_token: Telegram Bot Token
        
        Returns:
            Bot экземпляр
        """
        return Bot(token=bot_token)
    
    async def send_telegram_message(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
    ) -> bool:
        """
        Отправить сообщение в Telegram.
        
        Args:
            bot: Bot экземпляр
            chat_id: Telegram Chat ID
            text: Текст сообщения
            reply_to_message_id: ID сообщения для reply (опционально)
        
        Returns:
            True если успешно отправлено, False иначе
        """
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
            )
            return True
        except Exception as e:
            logger.error(
                "💥 Ошибка при отправке сообщения в Telegram",
                error=str(e),
                chat_id=chat_id,
            )
            return False
    
    async def delete_message(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
    ) -> bool:
        """
        Удалить сообщение из Telegram.
        
        Args:
            bot: Bot экземпляр
            chat_id: Telegram Chat ID
            message_id: Telegram Message ID
        
        Returns:
            True если успешно удалено, False иначе
        """
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            return True
        except Exception as e:
            logger.error(
                "💥 Ошибка при удалении сообщения",
                error=str(e),
                chat_id=chat_id,
                message_id=message_id,
            )
            return False
    
    async def restrict_user(
        self,
        bot: Bot,
        chat_id: int,
        user_id: int,
        can_send_messages: bool = False,
    ) -> bool:
        """
        Ограничить пермиссии пользователя (забан).
        
        Args:
            bot: Bot экземпляр
            chat_id: Telegram Chat ID
            user_id: Telegram User ID
            can_send_messages: Может ли отправлять сообщения
        
        Returns:
            True если успешно ограничено, False иначе
        """
        try:
            from aiogram.types import ChatPermissions
            
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=can_send_messages),
            )
            return True
        except Exception as e:
            logger.error(
                "💥 Ошибка при ограничении пользователя",
                error=str(e),
                chat_id=chat_id,
                user_id=user_id,
            )
            return False