"""
Nexus Bot Network — Dynamic Polling с BotPoolManager.

Архитектура:
- BotPoolManager управляет пулом ботов (словарь bot_id → asyncio.Task)
- Каждые 5 минут синхронизирует активных ботов из БД
- Все боты используют один Dispatcher с общими хендлерами
- /add_bot — добавить нового бота в сеть  
- /find    — OSINT-пробив пользователя по всем ботам
- Начисление XP + diamonds за каждое сообщение
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from sqlalchemy import select

from config.settings import settings
from core.queue.redis_client import init_redis
from core.database.db import init_db, get_db_session
from core.database.models import User, Group, SystemBot
from core.database import queries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# КОНСТАНТЫ
# ============================================================================

# Интервал синхронизации ботов из БД (секунд)
BOT_SYNC_INTERVAL = 300  # 5 минут

# XP за каждое сообщение
XP_PER_MESSAGE = 10

# ID администраторов (из .env: ADMIN_USER_IDS=123456,789012)
_admin_ids_raw = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = set(
    int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip().isdigit()
)


# ============================================================================
# BOT POOL MANAGER — динамический пул ботов
# ============================================================================

class BotPoolManager:
    """
    Управляет пулом ботов в сети Nexus.
    
    Каждый бот запускается как отдельная asyncio.Task (dp.start_polling).
    При синхронизации из БД:
      - Новые боты добавляются в пул и запускаются
      - Деактивированные боты (is_active=False) останавливаются
    """
    
    def __init__(self, dispatcher: Dispatcher):
        self.dp = dispatcher
        # bot_id (Telegram numeric) → asyncio.Task
        self.active_tasks: Dict[int, asyncio.Task] = {}
        # bot_id → Bot instance (для корректного закрытия)
        self.active_bots: Dict[int, Bot] = {}
        self._running = False
    
    async def start(self) -> None:
        """Запустить менеджер: загрузить ботов из БД + начать цикл синхронизации."""
        self._running = True
        logger.info("🚀 BotPoolManager запускается...")
        
        # Первичная загрузка из БД
        await self._sync_bots_from_db()
        
        # Фоновая задача синхронизации
        asyncio.create_task(self._sync_loop())
        
        logger.info(
            f"✅ BotPoolManager запущен. Ботов в пуле: {len(self.active_tasks)}"
        )
    
    async def stop(self) -> None:
        """Остановить все боты и менеджер."""
        self._running = False
        logger.info("🛑 Остановка BotPoolManager...")
        
        for bot_id, task in list(self.active_tasks.items()):
            task.cancel()
        
        for bot_id, bot in list(self.active_bots.items()):
            await bot.session.close()
        
        self.active_tasks.clear()
        self.active_bots.clear()
    
    async def add_bot(self, token: str, bot_db_id: int, telegram_bot_id: int) -> bool:
        """
        Добавить нового бота в пул (вызывается после /add_bot или sync из БД).
        
        Args:
            token: Telegram Bot Token
            bot_db_id: ID бота в таблице system_bots (для логов)
            telegram_bot_id: Telegram numeric bot ID (ключ в пуле)
        
        Returns:
            True если бот успешно добавлен, False если уже был в пуле
        """
        if telegram_bot_id in self.active_bots:
            logger.info(f"ℹ️ Бот {telegram_bot_id} уже в пуле, пропускаем")
            return False
        
        bot = Bot(token=token)
        self.active_bots[telegram_bot_id] = bot
        
        # Запускаем polling в отдельной задаче
        task = asyncio.create_task(
            self._run_bot_polling(bot, telegram_bot_id),
            name=f"bot_polling_{telegram_bot_id}",
        )
        self.active_tasks[telegram_bot_id] = task
        
        logger.info(f"➕ Бот {telegram_bot_id} добавлен в пул (db_id={bot_db_id})")
        return True
    
    async def remove_bot(self, telegram_bot_id: int) -> None:
        """Убрать бота из пула (деактивирован в БД)."""
        if telegram_bot_id not in self.active_tasks:
            return
        
        self.active_tasks[telegram_bot_id].cancel()
        del self.active_tasks[telegram_bot_id]
        
        bot = self.active_bots.pop(telegram_bot_id, None)
        if bot:
            await bot.session.close()
        
        logger.info(f"➖ Бот {telegram_bot_id} удалён из пула")
    
    async def _run_bot_polling(self, bot: Bot, bot_id: int) -> None:
        """Запустить polling для одного бота с обработкой ошибок."""
        try:
            logger.info(f"▶️  Polling запущен для бота {bot_id}")
            await self.dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            logger.info(f"⏹️  Polling остановлен для бота {bot_id}")
        except Exception as e:
            logger.error(f"💥 Ошибка polling бота {bot_id}: {e}")
    
    async def _sync_loop(self) -> None:
        """Фоновая задача: каждые 5 минут синхронизировать ботов из БД."""
        while self._running:
            await asyncio.sleep(BOT_SYNC_INTERVAL)
            if self._running:
                logger.info("🔄 Синхронизация ботов из БД...")
                await self._sync_bots_from_db()
    
    async def _sync_bots_from_db(self) -> None:
        """Синхронизировать пул ботов с таблицей system_bots."""
        try:
            session = await get_db_session()
            try:
                db_bots = await queries.get_all_active_bots(session)
                db_bot_ids = {b.bot_id: b for b in db_bots}
                
                # Добавить новых ботов из БД
                for bot_id, bot_record in db_bot_ids.items():
                    if bot_id not in self.active_bots:
                        logger.info(
                            f"🆕 Новый бот из БД: {bot_record.bot_username} "
                            f"(id={bot_id})"
                        )
                        await self.add_bot(
                            token=bot_record.bot_token,
                            bot_db_id=bot_record.id,
                            telegram_bot_id=bot_id,
                        )
                
                # Остановить деактивированных ботов
                for tg_id in list(self.active_bots.keys()):
                    if tg_id not in db_bot_ids:
                        logger.info(f"🚫 Бот {tg_id} деактивирован — убираем из пула")
                        await self.remove_bot(tg_id)
                        
            finally:
                await session.close()
        
        except Exception as e:
            logger.error(f"💥 Ошибка при синхронизации ботов из БД: {e}")


# ============================================================================
# HANDLERS — общий Dispatcher для всех ботов в пуле
# ============================================================================

def build_dispatcher() -> Dispatcher:
    """Создать Dispatcher со всеми хендлерами."""
    dp = Dispatcher()
    
    # ------------------------------------------------------------------
    # /start — приветствие
    # ------------------------------------------------------------------
    @dp.message(Command("start"))
    async def handle_start(message: types.Message):
        user = message.from_user
        await message.answer(
            f"👋 Привет, {user.full_name}!\n"
            f"🛰 Nexus Network Online.\n"
            f"Твои сообщения сохраняются в общую базу.\n\n"
            f"📊 /profile — посмотреть XP и баланс\n"
            f"🔍 /find @username — OSINT-пробив\n"
        )
    
    # ------------------------------------------------------------------
    # /profile — посмотреть экономику
    # ------------------------------------------------------------------
    @dp.message(Command("profile"))
    async def handle_profile(message: types.Message):
        session = await get_db_session()
        try:
            global_user = await queries.get_or_create_global_user(
                session=session,
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            await message.answer(
                f"👤 <b>Профиль: {message.from_user.full_name}</b>\n"
                f"🎯 XP: <b>{global_user.xp}</b>\n"
                f"💎 Diamonds: <b>{global_user.diamonds}</b>\n"
                f"💰 Balance: <b>{global_user.balance:.2f}</b>\n\n"
                f"<i>За каждые 100 XP ты получаешь 1 💎</i>",
                parse_mode="HTML",
            )
        finally:
            await session.close()
    
    # ------------------------------------------------------------------
    # /add_bot <TOKEN> — добавить нового бота в сеть
    # ------------------------------------------------------------------
    @dp.message(Command("add_bot"))
    async def handle_add_bot(message: types.Message, command: CommandObject):
        # Проверка прав администратора
        if ADMIN_USER_IDS and message.from_user.id not in ADMIN_USER_IDS:
            await message.answer("⛔ У тебя нет прав для добавления ботов.")
            return
        
        if not command.args:
            await message.answer(
                "❌ Укажи токен бота:\n"
                "<code>/add_bot 1234567890:ABCdefGHIjklMNO...</code>",
                parse_mode="HTML",
            )
            return
        
        token = command.args.strip()
        
        # Валидация токена через Telegram API
        try:
            test_bot = Bot(token=token)
            bot_info = await test_bot.get_me()
            await test_bot.session.close()
        except Exception as e:
            await message.answer(f"❌ Невалидный токен: <code>{e}</code>", parse_mode="HTML")
            return
        
        # Сохранить в БД
        session = await get_db_session()
        try:
            db_bot = await queries.get_or_create_bot(
                session=session,
                bot_id=bot_info.id,
                bot_name=bot_info.username or f"bot_{bot_info.id}",
                bot_token=token,
                bot_username=bot_info.username,
            )
            
            # Немедленно добавить в пул (если не в пуле)
            # _pool_manager будет доступен через closure после инициализации
            if _pool_manager:
                added = await _pool_manager.add_bot(
                    token=token,
                    bot_db_id=db_bot.id,
                    telegram_bot_id=bot_info.id,
                )
                status = "запущен" if added else "уже был в пуле"
            else:
                status = "сохранён (пул не инициализирован)"
            
            await message.answer(
                f"✅ Бот <b>@{bot_info.username}</b> добавлен в сеть!\n"
                f"🆔 ID: <code>{bot_info.id}</code>\n"
                f"📡 Статус: {status}",
                parse_mode="HTML",
            )
            logger.info(
                f"➕ Новый бот добавлен через /add_bot: @{bot_info.username} "
                f"(id={bot_info.id})"
            )
        finally:
            await session.close()
    
    # ------------------------------------------------------------------
    # /find <@username или user_id> — OSINT-пробив
    # ------------------------------------------------------------------
    @dp.message(Command("find"))
    async def handle_find(message: types.Message, command: CommandObject):
        if not command.args:
            await message.answer(
                "❌ Укажи цель:\n"
                "<code>/find @username</code>\n"
                "<code>/find 123456789</code>",
                parse_mode="HTML",
            )
            return
        
        target = command.args.strip()
        await message.answer(f"🔍 Ищу информацию о <code>{target}</code>...", parse_mode="HTML")
        
        session = await get_db_session()
        try:
            telegram_user_id = None
            
            # Определить тип запроса: числовой ID или @username
            if target.lstrip("@").isdigit():
                telegram_user_id = int(target.lstrip("@"))
            else:
                # Поиск по username через global_users
                found = await queries.get_global_user_by_username(session, target)
                if found:
                    telegram_user_id = found.telegram_user_id
                else:
                    await message.answer(
                        f"❌ Пользователь <code>{target}</code> не найден в базе Nexus.",
                        parse_mode="HTML",
                    )
                    return
            
            # OSINT lookup
            report = await queries.osint_lookup_user(session, telegram_user_id)
            
            if not report["appearances"] and not report["global_user"]:
                await message.answer(
                    f"🔍 Пользователь <code>{telegram_user_id}</code> не найден ни в одном боте сети.",
                    parse_mode="HTML",
                )
                return
            
            # Форматируем отчёт
            gu = report["global_user"]
            username_display = f"@{gu['username']}" if gu and gu.get("username") else str(telegram_user_id)
            full_name_display = (gu.get("full_name") or "Неизвестно") if gu else "Неизвестно"
            
            lines = [
                f"🔍 <b>OSINT Report: {username_display}</b>",
                f"👤 Имя: {full_name_display}",
                f"🆔 ID: <code>{telegram_user_id}</code>",
            ]
            
            if gu:
                lines.append(
                    f"💎 Diamonds: {gu['diamonds']} | "
                    f"XP: {gu['xp']} | "
                    f"Balance: {gu['balance']:.2f}"
                )
                if gu.get("registered_at"):
                    lines.append(f"📅 В сети с: {gu['registered_at'].strftime('%Y-%m-%d')}")
            
            lines.append(f"\n📡 Замечен в <b>{len(report['appearances'])}</b> ботах сети:")
            
            for app in report["appearances"][:10]:  # Лимит 10 чатов
                bot_tag = f"@{app['bot_username']}" if app.get("bot_username") else app["bot_name"]
                chat_name = app.get("chat_title") or str(app["chat_id"])
                lines.append(
                    f"  • [{bot_tag}] → \"{chat_name}\" "
                    f"({app['message_count']} сообщ.)"
                    + (" 🚫" if app["is_banned"] else "")
                )
            
            if report["first_seen"]:
                lines.append(
                    f"\n🕐 Первое появление: {report['first_seen'].strftime('%Y-%m-%d %H:%M')}"
                )
            if report["last_seen"]:
                lines.append(
                    f"🕐 Последнее: {report['last_seen'].strftime('%Y-%m-%d %H:%M')}"
                )
            
            lines.append(f"💬 Всего сообщений: <b>{report['total_messages']}</b>")
            
            status_flags = []
            if report["is_banned_anywhere"]:
                status_flags.append("🚫 Забанен")
            if report["is_spam_anywhere"]:
                status_flags.append("⚠️ Спамер")
            if status_flags:
                lines.append("⚡ Статус: " + ", ".join(status_flags))
            
            await message.answer("\n".join(lines), parse_mode="HTML")
        
        except Exception as e:
            logger.error(f"💥 Ошибка OSINT lookup: {e}")
            await message.answer(f"❌ Ошибка при пробиве: {e}")
        finally:
            await session.close()
    
    # ------------------------------------------------------------------
    # Обработка всех сообщений: сохранение в БД + начисление XP
    # ------------------------------------------------------------------
    @dp.message()
    async def handle_and_save(message: types.Message):
        if not message.from_user:
            return
        
        session = await get_db_session()
        try:
            # 1. Upsert GlobalUser (экономический профиль)
            await queries.get_or_create_global_user(
                session=session,
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            
            # 2. Upsert Group
            result = await session.execute(
                select(Group).where(Group.telegram_chat_id == message.chat.id)
            )
            db_group = result.scalar_one_or_none()
            if not db_group:
                db_group = Group(
                    telegram_chat_id=message.chat.id,
                    title=message.chat.title or message.chat.first_name or "Private",
                    chat_type=message.chat.type,
                    bot_id=1,  # fallback; корректный bot_id подтягивается при sync
                    is_active=True,
                    is_subscribed=True,
                )
                session.add(db_group)
                await session.flush()
            
            # 3. Upsert User (per-group статистика)
            result = await session.execute(
                select(User).where(
                    (User.telegram_user_id == message.from_user.id)
                    & (User.group_id == db_group.id)
                )
            )
            db_user = result.scalar_one_or_none()
            if not db_user:
                db_user = User(
                    telegram_user_id=message.from_user.id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                    last_name=message.from_user.last_name,
                    full_name=message.from_user.full_name,
                    group_id=db_group.id,
                )
                session.add(db_user)
                await session.flush()
            
            # Обновить статистику пользователя
            db_user.message_count = (db_user.message_count or 0) + 1
            db_user.last_message_date = datetime.utcnow()
            if not db_user.first_message_date:
                db_user.first_message_date = datetime.utcnow()
            if message.from_user.username:
                db_user.username = message.from_user.username
            
            await session.commit()
            
            # 4. Начислить XP (и автоматически diamonds при пороге)
            updated_gu = await queries.add_xp(
                session=session,
                telegram_user_id=message.from_user.id,
                amount=XP_PER_MESSAGE,
            )
            
            # 5. Уведомить пользователя при достижении нового уровня diamonds
            if updated_gu and updated_gu.xp % 100 == 0 and updated_gu.xp > 0:
                await message.reply(
                    f"💎 Новый алмаз! У тебя теперь {updated_gu.diamonds} 💎 "
                    f"(XP: {updated_gu.xp})",
                )
            
            logger.info(
                f"✅ Сообщение сохранено: "
                f"@{message.from_user.username} | "
                f"chat={message.chat.id} | "
                f"xp={updated_gu.xp if updated_gu else '?'}"
            )
        
        except Exception as e:
            logger.error(f"💥 Ошибка сохранения сообщения: {e}")
            try:
                await session.rollback()
            except Exception:
                pass
        finally:
            await session.close()
    
    return dp


# ============================================================================
# MAIN — точка запуска
# ============================================================================

# Глобальная ссылка на менеджер (используется в хендлере /add_bot)
_pool_manager: Optional[BotPoolManager] = None


async def _bootstrap_from_env(pool_manager: BotPoolManager) -> None:
    """
    Если БД пуста — подтянуть ботов из .env (TELEGRAM_TOKENS).
    
    Это fallback для первого запуска, пока нет ни одного бота в system_bots.
    """
    session = await get_db_session()
    try:
        db_bots = await queries.get_all_active_bots(session)
        if db_bots:
            return  # В БД уже есть боты — ничего не делаем
        
        tokens_raw = settings.telegram_tokens
        tokens_dict = tokens_raw if isinstance(tokens_raw, dict) else json.loads(tokens_raw or "{}")
        
        if not tokens_dict:
            logger.warning(
                "⚠️ В БД нет ботов и TELEGRAM_TOKENS в .env пуст. "
                "Добавь бота через /add_bot."
            )
            return
        
        logger.info(f"📦 Импорт ботов из .env в БД: {list(tokens_dict.keys())}")
        
        for bot_name, token in tokens_dict.items():
            try:
                temp_bot = Bot(token=token)
                info = await temp_bot.get_me()
                await temp_bot.session.close()
                
                await queries.get_or_create_bot(
                    session=session,
                    bot_id=info.id,
                    bot_name=bot_name,
                    bot_token=token,
                    bot_username=info.username,
                )
                logger.info(f"✅ Бот из .env сохранён в БД: @{info.username}")
            except Exception as e:
                logger.error(f"❌ Ошибка импорта бота {bot_name}: {e}")
    finally:
        await session.close()


async def main():
    global _pool_manager
    
    logger.info("=" * 60)
    logger.info("🚀 NEXUS BOT NETWORK — запуск")
    logger.info("=" * 60)
    
    # Инициализация инфраструктуры
    await init_db()
    await init_redis()
    
    # Создать Dispatcher с хендлерами
    dp = build_dispatcher()
    
    # Создать менеджер пула
    _pool_manager = BotPoolManager(dispatcher=dp)
    
    # Если БД пустая — залить ботов из .env
    await _bootstrap_from_env(_pool_manager)
    
    # Запустить пул (загрузит ботов из БД + начнёт цикл синхронизации)
    await _pool_manager.start()
    
    # Ждём завершения всех задач
    try:
        tasks = list(_pool_manager.active_tasks.values())
        if tasks:
            logger.info(f"⏳ Запущено {len(tasks)} ботов. Ожидаем...")
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.warning(
                "⚠️ Нет активных ботов в пуле. "
                "Добавь бота в систему: /add_bot <TOKEN> "
                "или заполни TELEGRAM_TOKENS в .env"
            )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Получен сигнал остановки")
    finally:
        await _pool_manager.stop()
        logger.info("✅ Nexus Bot Network остановлен")


if __name__ == "__main__":
    asyncio.run(main())
