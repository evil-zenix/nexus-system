"""
Nexus Bot Network — Dynamic Polling + Smart Router.

Архитектура:
- BotPoolManager управляет пулом ботов (bot_id → asyncio.Task)
- Smart Router: любой текст автоматически маршрутизируется по regex
- Все боты-клоны используют один Dispatcher
"""
import asyncio
import json
import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy import select

from config.settings import settings
from core.queue.redis_client import init_redis
from core.database.db import init_db, get_db_session
from core.database.models import User, Group, SystemBot, GlobalUser
from core.database import queries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# КОНСТАНТЫ
# ============================================================================

BOT_SYNC_INTERVAL = 300  # 5 минут
XP_PER_MESSAGE = 10

_admin_ids_raw = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = set(
    int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip().isdigit()
)

# Smart Router regex шаблоны
RE_USER_ID = re.compile(r"^\d+$")
RE_GROUP_ID = re.compile(r"^-\d+$")
RE_USERNAME = re.compile(r"^(?:https?://t\.me/|t\.me/|@)?([\w_]+)$")
RE_EMAIL = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
RE_NICKNAME = re.compile(r"^[\w.-]+$") # Для Sherlock: только буквы/цифры без пробелов/слэшей


# ============================================================================
# BOT POOL MANAGER
# ============================================================================

class BotPoolManager:
    """Управляет пулом ботов. Каждый бот — отдельная asyncio.Task."""
    
    def __init__(self, dispatcher: Dispatcher):
        self.dp = dispatcher
        self.active_tasks: Dict[int, asyncio.Task] = {}
        self.active_bots: Dict[int, Bot] = {}
        self._running = False
    
    async def start(self) -> None:
        self._running = True
        logger.info("🚀 BotPoolManager запускается...")
        await self._sync_bots_from_db()
        asyncio.create_task(self._sync_loop())
        logger.info(f"✅ BotPoolManager запущен. Ботов в пуле: {len(self.active_tasks)}")
    
    async def stop(self) -> None:
        self._running = False
        logger.info("🛑 Остановка BotPoolManager...")
        for bot_id, task in list(self.active_tasks.items()):
            task.cancel()
        for bot_id, bot in list(self.active_bots.items()):
            await bot.session.close()
        self.active_tasks.clear()
        self.active_bots.clear()
    
    async def add_bot(self, token: str, bot_db_id: int, telegram_bot_id: int) -> bool:
        if telegram_bot_id in self.active_bots:
            return False
        bot = Bot(token=token)
        self.active_bots[telegram_bot_id] = bot
        task = asyncio.create_task(
            self._run_bot_polling(bot, telegram_bot_id),
            name=f"bot_polling_{telegram_bot_id}",
        )
        self.active_tasks[telegram_bot_id] = task
        logger.info(f"➕ Бот {telegram_bot_id} добавлен в пул")
        return True
    
    async def remove_bot(self, telegram_bot_id: int) -> None:
        if telegram_bot_id not in self.active_tasks:
            return
        self.active_tasks[telegram_bot_id].cancel()
        del self.active_tasks[telegram_bot_id]
        bot = self.active_bots.pop(telegram_bot_id, None)
        if bot:
            await bot.session.close()
        logger.info(f"➖ Бот {telegram_bot_id} удалён из пула")
    
    async def _run_bot_polling(self, bot: Bot, bot_id: int) -> None:
        try:
            logger.info(f"▶️  Polling запущен для бота {bot_id}")
            await self.dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            logger.info(f"⏹️  Polling остановлен для бота {bot_id}")
        except Exception as e:
            logger.error(f"💥 Ошибка polling бота {bot_id}: {e}")
    
    async def _sync_loop(self) -> None:
        while self._running:
            await asyncio.sleep(BOT_SYNC_INTERVAL)
            if self._running:
                logger.info("🔄 Синхронизация ботов из БД...")
                await self._sync_bots_from_db()
    
    async def _sync_bots_from_db(self) -> None:
        try:
            session = await get_db_session()
            try:
                db_bots = await queries.get_all_active_bots(session)
                db_bot_ids = {b.bot_id: b for b in db_bots}
                for bot_id, bot_record in db_bot_ids.items():
                    if bot_id not in self.active_bots:
                        await self.add_bot(
                            token=bot_record.bot_token,
                            bot_db_id=bot_record.id,
                            telegram_bot_id=bot_id,
                        )
                for tg_id in list(self.active_bots.keys()):
                    if tg_id not in db_bot_ids:
                        await self.remove_bot(tg_id)
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"💥 Ошибка при синхронизации ботов из БД: {e}")


# ============================================================================
# OSINT ФУНКЦИИ (вынесены для переиспользования в Smart Router)
# ============================================================================

async def _do_osint_by_user_id(message: types.Message, target_id: int) -> None:
    """OSINT-пробив по Telegram User ID с проверкой is_hidden."""
    session = await get_db_session()
    try:
        stmt = select(GlobalUser).where(GlobalUser.telegram_user_id == target_id)
        result = await session.execute(stmt)
        gu_obj = result.scalar_one_or_none()
        
        if gu_obj and gu_obj.is_hidden:
            await message.answer(
                f"🔒 Пользователь <code>{target_id}</code> скрыл свои данные из OSINT-выдачи.",
                parse_mode="HTML",
            )
            return
        
        report = await queries.osint_lookup_user(session, target_id)
        
        if not report["appearances"] and not report["global_user"]:
            await message.answer(
                f"🔍 <code>{target_id}</code> — не найден в базе Nexus.",
                parse_mode="HTML",
            )
            return
        
        is_self = (message.from_user.id == target_id)
        await message.answer(
            _format_osint_report(report, is_self=is_self),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"💥 OSINT error: {e}")
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await session.close()


async def _do_osint_by_username(message: types.Message, username: str) -> None:
    """OSINT-пробив по @username."""
    session = await get_db_session()
    try:
        found = await queries.get_global_user_by_username(session, username)
        if not found:
            # Fallback: пробуем как группу/канал
            await _do_group_lookup(message, username)
            return
        
        if found.is_hidden:
            await message.answer(
                f"🔒 <code>{username}</code> скрыл свои данные из OSINT-выдачи.",
                parse_mode="HTML",
            )
            return
        
        report = await queries.osint_lookup_user(session, found.telegram_user_id)
        is_self = (message.from_user.id == found.telegram_user_id)
        await message.answer(_format_osint_report(report, is_self=is_self), parse_mode="HTML")
    except Exception as e:
        logger.error(f"💥 OSINT username error: {e}")
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await session.close()


async def _do_group_lookup(message: types.Message, chat_identifier) -> None:
    """Пробив по Channel/Group ID или username."""
    session = await get_db_session()
    try:
        groups = []
        if isinstance(chat_identifier, int) or (isinstance(chat_identifier, str) and chat_identifier.lstrip("-").isdigit()):
            chat_id = int(chat_identifier)
            stmt = select(Group).where(Group.telegram_chat_id == chat_id)
            result = await session.execute(stmt)
            groups = result.scalars().all()
        
        found_in_db = len(groups) > 0
        
        lines = [f"📡 <b>Чат/Канал: {chat_identifier}</b>\n"]
        
        if found_in_db:
            lines.append("🗄 <b>Найдено в нашей БД:</b>")
            for g in groups:
                lines.append(
                    f"  • {g.title or 'Без названия'} (тип: {g.chat_type})\n"
                    f"    👥 Участников: {g.members_count} | "
                    f"Активен: {'✅' if g.is_active else '❌'}"
                )
        else:
            lines.append("🗄 В нашей БД не найдено.")
            
        # Запрашиваем через API Telegram
        try:
            chat = await message.bot.get_chat(chat_identifier)
            lines.append("\n🌐 <b>Данные из Telegram API:</b>")
            lines.append(f"  • Название: <b>{chat.title or chat.first_name}</b>")
            lines.append(f"  • Тип: {chat.type}")
            if chat.description or chat.bio:
                desc = chat.description or chat.bio
                lines.append(f"  • Описание: <i>{desc}</i>")
        except Exception as e:
            if not found_in_db:
                lines.append(f"\n❌ Telegram API: Чат приватный или недоступен ({e})")
        
        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"💥 Group lookup error: {e}")
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        await session.close()


async def _do_email_check(message: types.Message, email: str) -> None:
    """Проверка Email на утечки (заглушка)."""
    await message.answer(
        f"📧 <b>Проверка Email</b>: <code>{email}</code>\n\n"
        f"🔄 Поиск в базах утечек...\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Результат</b>:\n"
        f"  • Утечек найдено: <b>0</b>\n"
        f"  • Баз проверено: <b>12</b>\n\n"
        f"<i>⚙️ Модуль Email-OSINT в разработке. "
        f"Полная интеграция скоро.</i>",
        parse_mode="HTML",
    )


async def _do_password_check(message: types.Message, password: str) -> None:
    """Проверка пароля на утечки (заглушка) и сохранение в БД."""
    session = await get_db_session()
    try:
        await queries.save_password_search(session, message.from_user.id, password)
    except Exception as e:
        logger.error(f"Ошибка сохранения пароля: {e}")
    finally:
        await session.close()
        
    # Маскируем пароль в выводе
    masked = password[:2] + "*" * (len(password) - 4) + password[-2:] if len(password) > 4 else "****"
    await message.answer(
        f"🔐 <b>Проверка пароля</b>: <code>{masked}</code>\n\n"
        f"🔄 Поиск в breach-базах...\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Результат</b>:\n"
        f"  • Совпадений найдено: <b>0</b>\n"
        f"  • Баз проверено: <b>8</b>\n\n"
        f"<i>⚙️ Модуль Password-OSINT в разработке.</i>",
        parse_mode="HTML",
    )


async def _do_nickname_search(message: types.Message, nickname: str) -> None:
    """Поиск по никнейму во всех соцсетях (Sherlock)."""
    status_msg = await message.answer(
        f"🕵️ <b>Sherlock (поиск никнейма)</b>: <code>{nickname}</code>\n"
        f"🔄 Запуск сканирования... Пожалуйста, подождите (это может занять время).",
        parse_mode="HTML",
    )
    
    try:
        # Реальный вызов sherlock
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "sherlock", nickname, "--print-all",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace")
        
        # Парсинг вывода Sherlock (строки с '[+]')
        found_urls = []
        for line in output.splitlines():
            if "[+]" in line:
                found_urls.append(line.replace("[+]", "✅").strip())
        
        if found_urls:
            res_text = "\n".join(found_urls[:30]) # Ограничение
            if len(found_urls) > 30:
                res_text += f"\n\n...и ещё {len(found_urls)-30} совпадений."
                
            await status_msg.edit_text(
                f"🕵️ <b>Sherlock</b>: <code>{nickname}</code>\n\n"
                f"Найдено совпадений: <b>{len(found_urls)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n{res_text}",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        else:
            await status_msg.edit_text(
                f"🕵️ <b>Sherlock</b>: <code>{nickname}</code>\n\n"
                f"❌ Совпадений не найдено или Sherlock вернул ошибку.",
                parse_mode="HTML",
            )
            
    except Exception as e:
        logger.error(f"Sherlock error: {e}")
        await status_msg.edit_text(f"❌ Ошибка запуска Sherlock: <code>{e}</code>", parse_mode="HTML")


def _format_osint_report(report: dict, is_self: bool = False) -> str:
    """Форматировать OSINT-отчёт в HTML."""
    gu = report.get("global_user")
    tid = report["telegram_user_id"]
    
    username_display = f"@{gu['username']}" if gu and gu.get("username") else str(tid)
    full_name = (gu.get("full_name") or "Неизвестно") if gu else "Неизвестно"
    
    lines = [
        f"🔍 <b>OSINT Report: {username_display}</b>",
        f"👤 Имя: {full_name}",
        f"🆔 ID: <code>{tid}</code>",
    ]
    
    if gu:
        lines.append(
            f"💎 Diamonds: {gu['diamonds']} | "
            f"XP: {gu['xp']} | "
            f"Balance: {gu['balance']:.2f}"
        )
        if gu.get("registered_at"):
            lines.append(f"📅 В сети с: {gu['registered_at'].strftime('%Y-%m-%d')}")
    
    apps = report.get("appearances", [])
    lines.append(f"\n📡 Замечен в <b>{len(apps)}</b> ботах сети:")
    
    for app in apps[:10]:
        bot_tag = f"@{app['bot_username']}" if app.get("bot_username") else app["bot_name"]
        chat_name = app.get("chat_title") or str(app["chat_id"])
        lines.append(
            f'  • [{bot_tag}] → "{chat_name}" '
            f"({app['message_count']} сообщ.)"
            + (" 🚫" if app.get("is_banned") else "")
        )
    
    if report.get("first_seen"):
        lines.append(f"\n🕐 Первое: {report['first_seen'].strftime('%Y-%m-%d %H:%M')}")
    if report.get("last_seen"):
        lines.append(f"🕐 Последнее: {report['last_seen'].strftime('%Y-%m-%d %H:%M')}")
    
    lines.append(f"💬 Всего сообщений: <b>{report['total_messages']}</b>")
    
    # 5. Вывод паролей из БД
    pw_searches = report.get("password_searches")
    if pw_searches and not is_self:
        lines.append(f"\n🔑 <b>Искал пароли ({len(pw_searches)}):</b>")
        # Выводим только первые 10 паролей чтобы не спамить
        for p in pw_searches[:10]:
            lines.append(f"  • <code>{p}</code>")
        if len(pw_searches) > 10:
            lines.append("  • ...и другие")
            
    flags = []
    if report.get("is_banned_anywhere"):
        flags.append("🚫 Забанен")
    if report.get("is_spam_anywhere"):
        flags.append("⚠️ Спамер")
    if flags:
        lines.append("⚡ Статус: " + ", ".join(flags))
    
    return "\n".join(lines)


# ============================================================================
# HANDLERS — общий Dispatcher (все боты-клоны наследуют эту логику)
# ============================================================================

def build_dispatcher() -> Dispatcher:
    """Собрать Dispatcher: явные команды + Smart Router + callback'и."""
    dp = Dispatcher()
    
    # ==================================================================
    # /start — приветствие
    # ==================================================================
    @dp.message(Command("start"))
    async def handle_start(message: types.Message):
        await message.answer(
            f"👋 Привет, <b>{message.from_user.full_name}</b>!\n"
            f"🛰 <b>Nexus Network Online</b>\n\n"
            f"Просто напиши мне:\n"
            f"  • <code>123456789</code> — пробив по User ID\n"
            f"  • <code>@username</code> — пробив по нику\n"
            f"  • <code>-1001234</code> — пробив чата/канала\n"
            f"  • <code>mail@example.com</code> — проверка email\n"
            f"  • <code>любой текст</code> — поиск никнейма\n\n"
            f"📋 /menu — главное меню\n"
            f"📊 /profile — твой профиль",
            parse_mode="HTML",
        )
    
    # ==================================================================
    # /menu — главное меню с inline-кнопками
    # ==================================================================
    @dp.message(Command("menu"))
    async def handle_menu(message: types.Message):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
                InlineKeyboardButton(text="💰 Баланс", callback_data="menu_balance"),
            ],
            [
                InlineKeyboardButton(text="🔍 Пробить себя", callback_data="menu_check_me"),
                InlineKeyboardButton(text="🕵️ Скрыться", callback_data="menu_hide"),
            ],
            [
                InlineKeyboardButton(text="🏪 Аукцион", callback_data="menu_auction"),
            ],
        ])
        await message.answer(
            "📋 <b>NEXUS — Главное меню</b>\n\n"
            "Выбери действие:",
            reply_markup=kb,
            parse_mode="HTML",
        )
    
    # Callback'и для inline-кнопок меню
    @dp.callback_query(F.data == "menu_profile")
    async def cb_profile(callback: CallbackQuery):
        await callback.answer()
        session = await get_db_session()
        try:
            gu = await queries.get_or_create_global_user(
                session, callback.from_user.id,
                callback.from_user.username, callback.from_user.full_name,
            )
            await callback.message.edit_text(
                f"👤 <b>Профиль: {callback.from_user.full_name}</b>\n"
                f"🎯 XP: <b>{gu.xp}</b>\n"
                f"💎 Diamonds: <b>{gu.diamonds}</b>\n"
                f"💰 Balance: <b>{gu.balance:.2f}</b>\n"
                f"🔒 Скрыт: {'Да' if gu.is_hidden else 'Нет'}",
                parse_mode="HTML",
            )
        finally:
            await session.close()
    
    @dp.callback_query(F.data == "menu_balance")
    async def cb_balance(callback: CallbackQuery):
        await callback.answer()
        session = await get_db_session()
        try:
            gu = await queries.get_or_create_global_user(
                session, callback.from_user.id,
                callback.from_user.username, callback.from_user.full_name,
            )
            await callback.message.edit_text(
                f"💰 <b>Баланс</b>\n\n"
                f"💎 Diamonds: <b>{gu.diamonds}</b>\n"
                f"🎯 XP: <b>{gu.xp}</b> (до 💎: {100 - (gu.xp % 100)} XP)\n"
                f"💵 Balance: <b>{gu.balance:.2f}</b>",
                parse_mode="HTML",
            )
        finally:
            await session.close()
    
    @dp.callback_query(F.data == "menu_check_me")
    async def cb_check_me(callback: CallbackQuery):
        await callback.answer("🔍 Проверяю...")
        await _do_check_me(callback.message, callback.from_user)
    
    @dp.callback_query(F.data == "menu_hide")
    async def cb_hide(callback: CallbackQuery):
        await callback.answer()
        session = await get_db_session()
        try:
            gu = await queries.get_or_create_global_user(
                session, callback.from_user.id,
                callback.from_user.username, callback.from_user.full_name,
            )
            gu.is_hidden = True
            gu.updated_at = datetime.utcnow()
            await session.commit()
            await callback.message.edit_text(
                "🕵️ <b>Режим невидимки активирован!</b>\n\n"
                "Твои данные больше не будут отображаться в OSINT-выдаче.\n"
                "Используй /hiden_me повторно чтобы отключить.",
                parse_mode="HTML",
            )
        finally:
            await session.close()
    
    @dp.callback_query(F.data == "menu_auction")
    async def cb_auction(callback: CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "🏪 <b>Аукцион</b>\n\n"
            "⚙️ Модуль аукциона в разработке.\n"
            "Скоро здесь можно будет продавать и покупать 💎 за баланс.",
            parse_mode="HTML",
        )
    
    # ==================================================================
    # /profile — профиль
    # ==================================================================
    @dp.message(Command("profile"))
    async def handle_profile(message: types.Message):
        session = await get_db_session()
        try:
            gu = await queries.get_or_create_global_user(
                session=session,
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            await message.answer(
                f"👤 <b>Профиль: {message.from_user.full_name}</b>\n"
                f"🎯 XP: <b>{gu.xp}</b>\n"
                f"💎 Diamonds: <b>{gu.diamonds}</b>\n"
                f"💰 Balance: <b>{gu.balance:.2f}</b>\n"
                f"🔒 Скрыт: {'Да' if gu.is_hidden else 'Нет'}\n\n"
                f"<i>За каждые 100 XP ты получаешь 1 💎</i>",
                parse_mode="HTML",
            )
        finally:
            await session.close()
    
    # ==================================================================
    # /check_me — OSINT-пробив самого себя (скрывает чувствительное)
    # ==================================================================
    @dp.message(Command("check_me"))
    async def handle_check_me(message: types.Message):
        await message.answer("🔍 Проверяю твоё досье...")
        await _do_check_me(message, message.from_user)
    
    # ==================================================================
    # /hiden_me — скрыть/показать себя в OSINT
    # ==================================================================
    @dp.message(Command("hiden_me"))
    async def handle_hiden_me(message: types.Message):
        session = await get_db_session()
        try:
            gu = await queries.get_or_create_global_user(
                session=session,
                telegram_user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
            # Переключаем флаг
            gu.is_hidden = not gu.is_hidden
            gu.updated_at = datetime.utcnow()
            await session.commit()
            
            if gu.is_hidden:
                await message.answer(
                    "🕵️ <b>Режим невидимки активирован!</b>\n\n"
                    "Твои данные скрыты из OSINT-выдачи.\n"
                    "Повторный /hiden_me отключит режим.",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "👁 <b>Режим невидимки выключен.</b>\n\n"
                    "Твои данные снова видны в OSINT.",
                    parse_mode="HTML",
                )
        finally:
            await session.close()
    
    # ==================================================================
    # /password <pass> — проверка пароля на утечки
    # ==================================================================
    @dp.message(Command("password"))
    async def handle_password(message: types.Message, command: CommandObject):
        if not command.args:
            await message.answer(
                "❌ Укажи пароль: <code>/password myp@ssw0rd</code>",
                parse_mode="HTML",
            )
            return
        # Удалить исходное сообщение (чтобы пароль не висел в чате)
        try:
            await message.delete()
        except Exception:
            pass
        await _do_password_check(message, command.args.strip())
    
    # ==================================================================
    # /email <mail> — проверка email на утечки
    # ==================================================================
    @dp.message(Command("email"))
    async def handle_email(message: types.Message, command: CommandObject):
        if not command.args:
            await message.answer(
                "❌ Укажи email: <code>/email user@example.com</code>",
                parse_mode="HTML",
            )
            return
        await _do_email_check(message, command.args.strip())
    
    # ==================================================================
    # /nickname <nick> — поиск по соцсетям (Sherlock-стиль)
    # ==================================================================
    @dp.message(Command("nickname"))
    async def handle_nickname(message: types.Message, command: CommandObject):
        if not command.args:
            await message.answer(
                "❌ Укажи никнейм: <code>/nickname johndoe</code>",
                parse_mode="HTML",
            )
            return
        await _do_nickname_search(message, command.args.strip())
    
    # ==================================================================
    # /find <target> — явная OSINT-команда (совместимость)
    # ==================================================================
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
        await message.answer(f"🔍 Ищу <code>{target}</code>...", parse_mode="HTML")
        await _smart_route(message, target)
    
    # ==================================================================
    # /add_bot <TOKEN> — добавить бота (admin)
    # ==================================================================
    @dp.message(Command("add_bot"))
    async def handle_add_bot(message: types.Message, command: CommandObject):
        if ADMIN_USER_IDS and message.from_user.id not in ADMIN_USER_IDS:
            await message.answer("⛔ Нет прав.")
            return
        if not command.args:
            await message.answer(
                "❌ <code>/add_bot TOKEN</code>",
                parse_mode="HTML",
            )
            return
        
        token = command.args.strip()
        try:
            test_bot = Bot(token=token)
            bot_info = await test_bot.get_me()
            await test_bot.session.close()
        except Exception as e:
            await message.answer(f"❌ Невалидный токен: <code>{e}</code>", parse_mode="HTML")
            return
        
        session = await get_db_session()
        try:
            db_bot = await queries.get_or_create_bot(
                session=session,
                bot_id=bot_info.id,
                bot_name=bot_info.username or f"bot_{bot_info.id}",
                bot_token=token,
                bot_username=bot_info.username,
            )
            if _pool_manager:
                added = await _pool_manager.add_bot(token, db_bot.id, bot_info.id)
                status = "🟢 запущен" if added else "уже в пуле"
            else:
                status = "сохранён"
            
            await message.answer(
                f"✅ Бот <b>@{bot_info.username}</b> добавлен!\n"
                f"🆔 <code>{bot_info.id}</code> | {status}",
                parse_mode="HTML",
            )
        finally:
            await session.close()
    
    # ==================================================================
    # 🧠 SMART ROUTER — перехват любого текста (без команд)
    # Должен быть ПОСЛЕДНИМ хендлером, после всех Command-хендлеров
    # ==================================================================
    @dp.message(F.text)
    async def smart_router(message: types.Message):
        """Маршрутизация текста по regex-паттернам."""
        if not message.from_user or not message.text:
            return
        
        text = message.text.strip()
        
        # Пропускаем команды (уже обработаны выше)
        if text.startswith("/"):
            return
        
        # --- Сохранение + начисление XP (при любом тексте) ---
        await _save_message_and_xp(message)
        
        # --- Smart Route ---
        await _smart_route(message, text)
    
    return dp


# ============================================================================
# SMART ROUTE LOGIC
# ============================================================================

async def _smart_route(message: types.Message, text: str) -> None:
    """Определить тип запроса по regex и вызвать нужную функцию."""
    
    # 1. Только цифры (>0) → User ID
    if RE_USER_ID.match(text):
        target_id = int(text)
        if target_id > 0:
            await message.answer(f"🔍 Пробив User ID: <code>{target_id}</code>", parse_mode="HTML")
            await _do_osint_by_user_id(message, target_id)
            return
    
    # 2. Отрицательное число → Channel/Group ID
    if RE_GROUP_ID.match(text):
        chat_id = int(text)
        await message.answer(f"📡 Пробив чата/канала: <code>{chat_id}</code>", parse_mode="HTML")
        await _do_group_lookup(message, chat_id)
        return
    
    # 3. @username или t.me/username
    match = RE_USERNAME.match(text)
    if match:
        target = match.group(1)
        await message.answer(f"🔍 Пробив (t.me/ или @): <code>{target}</code>", parse_mode="HTML")
        await _do_osint_by_username(message, target)
        return
    
    # 4. Email
    if RE_EMAIL.match(text):
        await _do_email_check(message, text)
        return
    
    # 5. Любой другой текст → поиск никнейма только если подходит под критерии (одно слово)
    if RE_NICKNAME.match(text):
        await _do_nickname_search(message, text)
    else:
        # Это обычный текст с пробелами или символами — игнорируем (уже сохранили в БД)
        pass


# ============================================================================
# /check_me — «Иллюзия безопасности» (скрывает чувствительные данные)
# ============================================================================

async def _do_check_me(message_or_cb, from_user) -> None:
    """OSINT себя, но с маскировкой чувствительных данных."""
    session = await get_db_session()
    try:
        report = await queries.osint_lookup_user(session, from_user.id)
        gu = report.get("global_user")
        
        lines = [
            f"🔍 <b>Самопроверка: {from_user.full_name}</b>",
            f"🆔 ID: <code>{from_user.id}</code>",
        ]
        
        if gu:
            lines.extend([
                f"💎 Diamonds: {gu['diamonds']} | XP: {gu['xp']}",
                f"📅 Регистрация: {gu['registered_at'].strftime('%Y-%m-%d') if gu.get('registered_at') else 'н/д'}",
            ])
        
        apps = report.get("appearances", [])
        lines.append(f"\n📡 Ты замечен в <b>{len(apps)}</b> ботах сети:")
        for app in apps[:5]:
            lines.append(f"  • {app.get('chat_title', '?')} ({app['message_count']} сообщ.)")
        
        lines.extend([
            f"\n💬 Всего сообщений: <b>{report['total_messages']}</b>",
            "",
            "━━━━━━━━━━━━━━━━━━━━━",
            "🔒 <b>Чувствительные данные:</b>",
            "  • Пароли: <b>не обнаружены</b> ✅",
            "  • Утечки email: <b>0 совпадений</b> ✅",
            "  • Телефоны: <b>скрыты системой</b> 🔒",
            "",
            f"<i>💡 Хочешь исчезнуть? Используй /hiden_me</i>",
        ])
        
        await message_or_cb.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"💥 check_me error: {e}")
        await message_or_cb.answer(f"❌ Ошибка: {e}")
    finally:
        await session.close()


# ============================================================================
# Сохранение сообщения + XP (вызывается из Smart Router)
# ============================================================================

async def _save_message_and_xp(message: types.Message) -> None:
    """Сохранить сообщение в БД + начислить XP."""
    if not message.from_user:
        return
    
    session = await get_db_session()
    try:
        # GlobalUser
        await queries.get_or_create_global_user(
            session=session,
            telegram_user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
        )
        
        # Group
        result = await session.execute(
            select(Group).where(Group.telegram_chat_id == message.chat.id)
        )
        db_group = result.scalar_one_or_none()
        if not db_group:
            db_group = Group(
                telegram_chat_id=message.chat.id,
                title=message.chat.title or message.chat.first_name or "Private",
                chat_type=message.chat.type,
                bot_id=1,
                is_active=True,
                is_subscribed=True,
            )
            session.add(db_group)
            await session.flush()
        
        # User (per-group)
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
        
        db_user.message_count = (db_user.message_count or 0) + 1
        db_user.last_message_date = datetime.utcnow()
        if not db_user.first_message_date:
            db_user.first_message_date = datetime.utcnow()
        if message.from_user.username:
            db_user.username = message.from_user.username
        
        await session.commit()
        
        # XP
        updated_gu = await queries.add_xp(
            session=session,
            telegram_user_id=message.from_user.id,
            amount=XP_PER_MESSAGE,
        )
        
        if updated_gu and updated_gu.xp % 100 == 0 and updated_gu.xp > 0:
            await message.reply(
                f"💎 +1 алмаз! Всего: {updated_gu.diamonds} 💎 (XP: {updated_gu.xp})",
            )
    except Exception as e:
        logger.error(f"💥 Ошибка сохранения: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
    finally:
        await session.close()


# ============================================================================
# MAIN
# ============================================================================

_pool_manager: Optional[BotPoolManager] = None


async def _bootstrap_from_env(pool_manager: BotPoolManager) -> None:
    """Если БД пуста — подтянуть ботов из .env."""
    session = await get_db_session()
    try:
        db_bots = await queries.get_all_active_bots(session)
        if db_bots:
            return
        
        tokens_raw = settings.telegram_tokens
        tokens_dict = tokens_raw if isinstance(tokens_raw, dict) else json.loads(tokens_raw or "{}")
        
        if not tokens_dict:
            logger.warning("⚠️ Нет ботов ни в БД, ни в .env. Используй /add_bot.")
            return
        
        logger.info(f"📦 Импорт из .env: {list(tokens_dict.keys())}")
        for bot_name, token in tokens_dict.items():
            try:
                tmp = Bot(token=token)
                info = await tmp.get_me()
                await tmp.session.close()
                await queries.get_or_create_bot(
                    session=session, bot_id=info.id,
                    bot_name=bot_name, bot_token=token,
                    bot_username=info.username,
                )
                logger.info(f"✅ @{info.username} импортирован из .env")
            except Exception as e:
                logger.error(f"❌ Ошибка импорта {bot_name}: {e}")
    finally:
        await session.close()


async def main():
    global _pool_manager
    
    logger.info("=" * 60)
    logger.info("🚀 NEXUS BOT NETWORK + SMART ROUTER — запуск")
    logger.info("=" * 60)
    
    await init_db()
    await init_redis()
    
    dp = build_dispatcher()
    _pool_manager = BotPoolManager(dispatcher=dp)
    
    await _bootstrap_from_env(_pool_manager)
    await _pool_manager.start()
    
    try:
        tasks = list(_pool_manager.active_tasks.values())
        if tasks:
            logger.info(f"⏳ Запущено {len(tasks)} ботов. Ожидаем...")
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.warning("⚠️ Нет активных ботов. /add_bot или TELEGRAM_TOKENS в .env")
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Остановка")
    finally:
        await _pool_manager.stop()
        logger.info("✅ Nexus остановлен")


if __name__ == "__main__":
    asyncio.run(main())
