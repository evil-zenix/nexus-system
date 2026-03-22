import asyncio
import json
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from sqlalchemy import select
from config.settings import settings
from core.queue.redis_client import init_redis
from core.database.db import init_db, get_db_session
from core.database.models import User, Group, MessageLog

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("🚀 Запуск Nexus System с сохранением в БД...")
    await init_db()
    await init_redis()

    tokens = settings.telegram_tokens
    tokens_dict = tokens if isinstance(tokens, dict) else json.loads(tokens)
    dp = Dispatcher()

    @dp.message()
    async def handle_and_save(message: types.Message):
        # 1. Отвечаем пользователю
        await message.reply(f"🛰 Nexus Online. Сообщение сохранено в БД!")

        # 2. Логика сохранения в БД
        async with await get_db_session() as session:
            try:
                # Проверяем/создаем группу
                result = await session.execute(select(Group).where(Group.telegram_chat_id == message.chat.id))
                db_group = result.scalar_one_or_none()
                if not db_group:
                    db_group = Group(telegram_chat_id=message.chat.id, title=message.chat.title or "Private")
                    session.add(db_group)
                    await session.flush()

                # Проверяем/создаем пользователя
                result = await session.execute(select(User).where(User.telegram_user_id == message.from_user.id))
                db_user = result.scalar_one_or_none()
                if not db_user:
                    db_user = User(
                        telegram_user_id=message.from_user.id,
                        username=message.from_user.username,
                        full_name=message.from_user.full_name,
                        group_id=db_group.id
                    )
                    session.add(db_user)
                    await session.flush()

                # Сохраняем само сообщение
                new_msg = MessageLog(
                    user_id=db_user.id,
                    group_id=db_group.id,
                    telegram_message_id=message.message_id,
                    message_text=message.text,
                    created_at=datetime.utcnow()
                )
                session.add(new_msg)
                await session.commit()
                logger.info(f"✅ Сообщение от {message.from_user.username} сохранено в базу!")
            except Exception as e:
                await session.rollback()
                logger.error(f"💥 Ошибка сохранения: {e}")

    bots = [Bot(token=t) for t in tokens_dict.values()]
    logger.info(f"✅ Ботов в сети: {len(bots)}. Напиши мне!")
    try:
        await dp.start_polling(*bots)
    finally:
        for bot in bots:
            await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
